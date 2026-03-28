from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import json


@dataclass
class DelayProfile:
    min_search_seconds: float = 2.0
    max_search_seconds: float = 8.0
    min_clone_seconds: float = 3.0
    max_clone_seconds: float = 12.0
    min_issue_seconds: float = 20.0
    max_issue_seconds: float = 90.0
    jitter_seconds: float = 1.25


@dataclass
class Limits:
    max_repos_per_run: int = 15
    max_issue_posts_per_run: int = 2
    max_issue_posts_per_day: int = 4
    max_clone_depth: int = 1
    max_files_scanned: int = 250
    max_source_chars_per_file: int = 12000
    min_similarity_score: float = 0.18
    min_issue_confidence: float = 0.72
    max_search_pages_per_query: int = 2
    repost_cooldown_days: int = 30
    duplicate_title_overlap_threshold: float = 0.72
    max_issue_scan_pages: int = 3


@dataclass
class SearchConfig:
    mode: str = 'related'
    custom_queries: List[str] = field(default_factory=list)
    include_forks: bool = False
    include_archived: bool = False
    min_stars: int = 0
    pushed_after: Optional[str] = None
    languages: List[str] = field(default_factory=list)
    required_topics: List[str] = field(default_factory=list)


@dataclass
class PostingConfig:
    enabled: bool = False
    draft_only: bool = True
    require_manual_approval: bool = False
    allowlist: List[str] = field(default_factory=list)
    denylist: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=lambda: ['ai-review'])
    skip_if_contributing_missing: bool = False
    avoid_existing_issue_titles_like: List[str] = field(
        default_factory=lambda: ['review', 'bug', 'issue', 'feedback']
    )


@dataclass
class AIConfig:
    enabled: bool = False
    api_url: Optional[str] = None
    api_key_env: str = 'AI_API_KEY'
    model: str = 'gpt-4o-mini'
    timeout_seconds: int = 120
    max_retries: int = 2


@dataclass
class SeedRepo:
    full_name: str
    weight: float = 1.0


@dataclass
class AppConfig:
    output_dir: str = 'output'
    workspace_dir: str = '.tmp_workspace'
    seed_repos: List[SeedRepo] = field(default_factory=list)
    delay_profile: DelayProfile = field(default_factory=DelayProfile)
    limits: Limits = field(default_factory=Limits)
    search: SearchConfig = field(default_factory=SearchConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)
    ai: AIConfig = field(default_factory=AIConfig)

    @staticmethod
    def from_json(path: str | Path) -> 'AppConfig':
        raw = json.loads(Path(path).read_text(encoding='utf-8'))

        def seed_repo(item: dict) -> SeedRepo:
            return SeedRepo(full_name=item['full_name'], weight=float(item.get('weight', 1.0)))

        limits_raw = raw.get('limits', {})
        known_limit_fields = {f.name for f in Limits.__dataclass_fields__.values()}
        limits_obj = Limits(**{k: v for k, v in limits_raw.items() if k in known_limit_fields})

        cfg = AppConfig(
            output_dir=raw.get('output_dir', 'output'),
            workspace_dir=raw.get('workspace_dir', '.tmp_workspace'),
            seed_repos=[seed_repo(x) for x in raw.get('seed_repos', [])],
            delay_profile=DelayProfile(**raw.get('delay_profile', {})),
            limits=limits_obj,
            search=SearchConfig(**raw.get('search', {})),
            posting=PostingConfig(**raw.get('posting', {})),
            ai=AIConfig(**raw.get('ai', {})),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.seed_repos:
            raise ValueError('At least one seed repo is required.')
        for seed in self.seed_repos:
            if '/' not in seed.full_name:
                raise ValueError(f'Invalid seed repo full_name: {seed.full_name}')
            if seed.weight <= 0:
                raise ValueError(f'Seed repo weight must be > 0: {seed.full_name}')
        self._validate_range('min_similarity_score', self.limits.min_similarity_score, 0.0, 1.0)
        self._validate_range('min_issue_confidence', self.limits.min_issue_confidence, 0.0, 1.0)
        self._validate_range(
            'duplicate_title_overlap_threshold',
            self.limits.duplicate_title_overlap_threshold,
            0.0, 1.0,
        )
        if self.limits.max_repos_per_run < 1:
            raise ValueError('max_repos_per_run must be >= 1')
        if self.limits.max_issue_posts_per_run < 0 or self.limits.max_issue_posts_per_day < 0:
            raise ValueError('Issue posting limits cannot be negative')
        if self.limits.max_search_pages_per_query < 1:
            raise ValueError('max_search_pages_per_query must be >= 1')
        if self.limits.max_issue_scan_pages < 1:
            raise ValueError('max_issue_scan_pages must be >= 1')
        if self.limits.repost_cooldown_days < 0:
            raise ValueError('repost_cooldown_days cannot be negative')
        self._validate_delay_order(
            self.delay_profile.min_search_seconds, self.delay_profile.max_search_seconds, 'search'
        )
        self._validate_delay_order(
            self.delay_profile.min_clone_seconds, self.delay_profile.max_clone_seconds, 'clone'
        )
        self._validate_delay_order(
            self.delay_profile.min_issue_seconds, self.delay_profile.max_issue_seconds, 'issue'
        )
        if self.ai.enabled and not self.ai.api_url:
            raise ValueError('AI is enabled but ai.api_url is missing.')
        if self.posting.enabled and self.posting.draft_only:
            raise ValueError(
                'posting.enabled=true conflicts with draft_only=true. '
                'Set draft_only=false to post live, or keep enabled=false to use draft mode.'
            )
        if self.search.mode not in {'related', 'custom', 'hybrid'}:
            raise ValueError("search.mode must be one of: related, custom, hybrid")
        if self.posting.enabled and not self.posting.draft_only:
            if self.limits.max_issue_posts_per_run > self.limits.max_issue_posts_per_day:
                raise ValueError(
                    'max_issue_posts_per_run cannot exceed max_issue_posts_per_day when posting is live.'
                )

    @staticmethod
    def _validate_range(name: str, value: float, low: float, high: float) -> None:
        if not (low <= value <= high):
            raise ValueError(f'{name} must be between {low} and {high}')

    @staticmethod
    def _validate_delay_order(low: float, high: float, name: str) -> None:
        if low < 0 or high < 0:
            raise ValueError(f'{name} delay values cannot be negative')
        if high < low:
            raise ValueError(f'{name} max delay must be >= min delay')
