from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .ai_client import AIReviewer
from .config import AppConfig
from .delay import HumanPacer
from .github_api import GitHubClient
from .issue_writer import default_review, review_from_ai_result
from .models import RepoAssessment, RepoProfile
from .review import clone_repo, collect_snapshot, heuristic_findings, safe_delete
from .similarity import build_queries, cosine_similarity, profile_keywords


class OperatorEngine:
    def __init__(self, cfg: AppConfig, gh: GitHubClient, pacer: HumanPacer) -> None:
        self.cfg = cfg
        self.gh = gh
        self.pacer = pacer
        self.ai = AIReviewer(cfg.ai)
        self.output_dir = Path(cfg.output_dir)
        self.workspace_dir = Path(cfg.workspace_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.daily_state_path = self.output_dir / 'daily_state.json'
        self.daily_state = self._load_daily_state()

    def _load_daily_state(self) -> Dict:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.daily_state_path.exists():
            obj = json.loads(self.daily_state_path.read_text(encoding='utf-8'))
            if obj.get('date') == today:
                return obj
        return {'date': today, 'issues_posted': 0}

    def _save_daily_state(self) -> None:
        self.daily_state_path.write_text(json.dumps(self.daily_state, indent=2), encoding='utf-8')

    def _seed_profiles(self) -> List[tuple[RepoProfile, str]]:
        out = []
        for seed in self.cfg.seed_repos:
            repo = self.gh.get_repo(seed.full_name)
            readme = self.gh.get_readme(seed.full_name)
            out.append((repo, readme))
        return out

    def print_queries(self) -> List[str]:
        seeds = self._seed_profiles()
        queries = build_queries(seeds, self.cfg.search.custom_queries, self.cfg.search.min_stars, self.cfg.search.pushed_after)
        for q in queries:
            print(q)
        return queries

    def run(self) -> None:
        seeds = self._seed_profiles()
        queries = build_queries(seeds, self.cfg.search.custom_queries, self.cfg.search.min_stars, self.cfg.search.pushed_after)
        seed_keywords = []
        for repo, readme in seeds:
            seed_keywords.extend(profile_keywords(repo, readme))

        seen = set(seed.full_name for seed in self.cfg.seed_repos)
        processed = 0
        posted = 0
        report_index: List[Dict] = []

        for query in queries:
            if processed >= self.cfg.limits.max_repos_per_run:
                break
            self.pacer.before_search()
            for repo in self.gh.search_repositories(query, per_page=min(10, self.cfg.limits.max_repos_per_run)):
                if processed >= self.cfg.limits.max_repos_per_run:
                    break
                if repo.full_name in seen:
                    continue
                seen.add(repo.full_name)
                if repo.archived and not self.cfg.search.include_archived:
                    continue
                if repo.fork and not self.cfg.search.include_forks:
                    continue
                if repo.stars < self.cfg.search.min_stars:
                    continue
                if self.cfg.search.languages and (repo.language or '').lower() not in {x.lower() for x in self.cfg.search.languages}:
                    continue
                if self.cfg.search.required_topics:
                    topics = {t.lower() for t in repo.topics}
                    if not topics.intersection({x.lower() for x in self.cfg.search.required_topics}):
                        continue

                assessment = self._assess_repo(repo, seed_keywords)
                if assessment.similarity_score < self.cfg.limits.min_similarity_score:
                    continue
                action = self._handle_posting(assessment)
                report_index.append({
                    'repo': repo.full_name,
                    'score': assessment.similarity_score,
                    'action': action,
                })
                processed += 1
                if action == 'posted':
                    posted += 1

        self._save_daily_state()
        (self.output_dir / 'index.json').write_text(json.dumps(report_index, indent=2), encoding='utf-8')
        print(f'[done] processed={processed} posted={posted} output={self.output_dir}')

    def _assess_repo(self, repo: RepoProfile, seed_keywords: List[str]) -> RepoAssessment:
        self.pacer.before_clone()
        target_dir = self.workspace_dir / repo.full_name.replace('/', '__')
        try:
            if not clone_repo(repo.clone_url, target_dir, depth=self.cfg.limits.max_clone_depth):
                raise RuntimeError(f'clone failed for {repo.full_name}')
            snapshot = collect_snapshot(target_dir, self.cfg.limits)
            heuristics = heuristic_findings(target_dir, snapshot)
            repo_keywords = profile_keywords(repo, '')
            score = cosine_similarity(seed_keywords, repo_keywords)
            contribution_rules = self.gh.get_contributing_rules(repo.full_name)
            review = self._build_review(repo, score, snapshot, heuristics, contribution_rules)
            assessment = RepoAssessment(
                repo=repo,
                similarity_score=score,
                snapshot={k: v for k, v in snapshot.items() if k != 'source_samples'},
                heuristics=heuristics,
                review=review,
                contribution_rules=contribution_rules,
            )
            self._save_assessment(assessment)
            return assessment
        finally:
            safe_delete(target_dir)

    def _build_review(self, repo: RepoProfile, score: float, snapshot: Dict, heuristics: List[str], contribution_rules: Dict) -> any:
        payload = {
            'repo': repo.to_dict(),
            'similarity_score': score,
            'snapshot': {k: v for k, v in snapshot.items() if k != 'source_samples'},
            'heuristics': heuristics,
            'contribution_rules': contribution_rules,
            'task': 'Return JSON with keys summary, praise, concerns, improvements, issue_title, issue_body, confidence. Be constructive and avoid overstating uncertainty.',
        }
        ai_result = None
        try:
            ai_result = self.ai.review(payload)
        except Exception as e:
            print(f'[ai-fallback] {e}')
        if ai_result:
            return review_from_ai_result(repo, ai_result)
        return default_review(repo, score, heuristics)

    def _save_assessment(self, assessment: RepoAssessment) -> None:
        slug = assessment.repo.full_name.replace('/', '__')
        report = {
            'repo': assessment.repo.to_dict(),
            'similarity_score': assessment.similarity_score,
            'snapshot': assessment.snapshot,
            'heuristics': assessment.heuristics,
            'review': asdict(assessment.review),
            'contribution_rules': assessment.contribution_rules,
        }
        (self.output_dir / f'{slug}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        md = [
            f'# {assessment.repo.full_name}',
            '',
            f'URL: {assessment.repo.html_url}',
            '',
            f'Similarity score: {assessment.similarity_score:.3f}',
            '',
            '## Summary',
            assessment.review.summary,
            '',
            '## Praise',
            *[f'- {x}' for x in assessment.review.praise],
            '',
            '## Concerns',
            *[f'- {x}' for x in assessment.review.concerns],
            '',
            '## Improvements',
            *[f'- {x}' for x in assessment.review.improvements],
            '',
            '## Suggested Issue Title',
            assessment.review.issue_title,
            '',
            '## Suggested Issue Body',
            assessment.review.issue_body,
            '',
            '## Contribution Files Found',
            *[f'- {x}' for x in assessment.contribution_rules.get('files', [])],
        ]
        (self.output_dir / f'{slug}.md').write_text('\n'.join(md), encoding='utf-8')

    def _handle_posting(self, assessment: RepoAssessment) -> str:
        posting = self.cfg.posting
        repo_name = assessment.repo.full_name
        if repo_name in posting.denylist:
            return 'denied'
        if posting.skip_if_contributing_missing and not assessment.contribution_rules.get('files'):
            return 'skipped_no_contrib_rules'
        if assessment.review.confidence < self.cfg.limits.min_issue_confidence:
            return 'low_confidence'

        # duplicate title check
        title_key = assessment.review.issue_title.lower().strip()
        issues = self.gh.list_issues(repo_name)
        for issue in issues:
            if 'pull_request' in issue:
                continue
            existing_title = str(issue.get('title', '')).lower()
            if title_key == existing_title or title_key in existing_title or existing_title in title_key:
                return 'duplicate_title'

        if posting.draft_only or not posting.enabled:
            return 'draft_only'
        if posting.require_manual_approval:
            return 'manual_approval_required'
        if posting.allowlist and repo_name not in posting.allowlist:
            return 'not_allowlisted'
        if self.daily_state['issues_posted'] >= self.cfg.limits.max_issue_posts_per_day:
            return 'daily_limit_reached'
        if self.daily_state['issues_posted'] >= self.cfg.limits.max_issue_posts_per_run:
            return 'run_limit_reached'

        self.pacer.before_issue()
        self.gh.create_issue(repo_name, assessment.review.issue_title, assessment.review.issue_body, labels=posting.labels)
        self.daily_state['issues_posted'] += 1
        return 'posted'
