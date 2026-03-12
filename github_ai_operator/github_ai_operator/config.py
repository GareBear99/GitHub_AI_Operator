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
    avoid_existing_issue_titles_like: List[str] = field(default_factory=lambda: ['review', 'bug', 'issue', 'feedback'])


@dataclass
class AIConfig:
    enabled: bool = False
    api_url: Optional[str] = None
    api_key_env: str = 'AI_API_KEY'
    model: str = 'gpt-4o-mini'


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

        return AppConfig(
            output_dir=raw.get('output_dir', 'output'),
            workspace_dir=raw.get('workspace_dir', '.tmp_workspace'),
            seed_repos=[seed_repo(x) for x in raw.get('seed_repos', [])],
            delay_profile=DelayProfile(**raw.get('delay_profile', {})),
            limits=Limits(**raw.get('limits', {})),
            search=SearchConfig(**raw.get('search', {})),
            posting=PostingConfig(**raw.get('posting', {})),
            ai=AIConfig(**raw.get('ai', {})),
        )
