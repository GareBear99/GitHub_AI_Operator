from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .anthropic_client import AnthropicReviewer
from .free_llm_client import FreeLLMReviewer
from .ai_client import AIReviewer
from .config import AppConfig
from .delay import HumanPacer
from .github_api import GitHubClient
from .issue_writer import default_review, review_from_ai_result
from .models import RepoAssessment, RepoProfile, ReviewResult
from .review import clone_repo, collect_snapshot, heuristic_findings, safe_delete
from .similarity import build_queries, cosine_similarity, profile_keywords, source_keywords, weighted_bag


class OperatorEngine:
    def __init__(self, cfg: AppConfig, gh: GitHubClient, pacer: HumanPacer) -> None:
        self.cfg = cfg
        self.gh = gh
        self.pacer = pacer
        self.claude = AnthropicReviewer(api_key_env='ANTHROPIC_API_KEY', max_retries=cfg.ai.max_retries, timeout=cfg.ai.timeout_seconds)
        self.ai = AIReviewer(cfg.ai)
        self.free_llm = FreeLLMReviewer(max_retries=cfg.ai.max_retries, timeout=cfg.ai.timeout_seconds)

        self.output_dir = Path(cfg.output_dir)
        self.workspace_dir = Path(cfg.workspace_dir)
        self.approval_dir = self.output_dir / 'approval_queue'
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.approval_dir.mkdir(parents=True, exist_ok=True)

        self.daily_state_path = self.output_dir / 'daily_state.json'
        self.history_path = self.output_dir / 'repo_history.json'
        self.daily_state = self._load_daily_state()
        self.repo_history = self._load_repo_history()
        self.run_state: Dict[str, Any] = {'issues_posted': 0}

    def _load_daily_state(self) -> Dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.daily_state_path.exists():
            try:
                obj = json.loads(self.daily_state_path.read_text(encoding='utf-8'))
                if isinstance(obj, dict) and obj.get('date') == today:
                    return obj
            except Exception:
                pass
        return {'date': today, 'issues_posted': 0}

    def _load_repo_history(self) -> Dict[str, Any]:
        if self.history_path.exists():
            try:
                obj = json.loads(self.history_path.read_text(encoding='utf-8'))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
        return {}

    def _save_daily_state(self) -> None:
        self.daily_state_path.write_text(json.dumps(self.daily_state, indent=2), encoding='utf-8')

    def _save_repo_history(self) -> None:
        self.history_path.write_text(json.dumps(self.repo_history, indent=2), encoding='utf-8')

    def _seed_profiles(self) -> List[Tuple[RepoProfile, str, float]]:
        out: List[Tuple[RepoProfile, str, float]] = []
        for seed in self.cfg.seed_repos:
            repo = self.gh.get_repo(seed.full_name)
            readme = self.gh.get_readme(seed.full_name)
            out.append((repo, readme, seed.weight))
        return out

    def print_queries(self) -> List[str]:
        seeds = self._seed_profiles()
        seed_inputs = [(repo, readme) for repo, readme, _ in seeds]
        queries = build_queries(seed_inputs, self.cfg.search.custom_queries, self.cfg.search.min_stars, self.cfg.search.pushed_after, self.cfg.search.mode)
        for q in queries:
            print(q)
        return queries

    def run(self) -> None:
        seeds = self._seed_profiles()
        seed_inputs = [(repo, readme) for repo, readme, _ in seeds]
        queries = build_queries(seed_inputs, self.cfg.search.custom_queries, self.cfg.search.min_stars, self.cfg.search.pushed_after, self.cfg.search.mode)

        seed_keywords: List[str] = []
        for repo, readme, weight in seeds:
            seed_keywords.extend(weighted_bag(profile_keywords(repo, readme), weight))

        seen: Set[str] = {seed.full_name for seed in self.cfg.seed_repos}
        processed = 0
        posted = 0
        skip_counts: Dict[str, int] = {}
        report_index: List[Dict[str, Any]] = []

        for query in queries:
            if processed >= self.cfg.limits.max_repos_per_run:
                break
            self.pacer.before_search()
            for page in range(1, self.cfg.limits.max_search_pages_per_query + 1):
                if processed >= self.cfg.limits.max_repos_per_run:
                    break
                results = self.gh.search_repositories(query, per_page=min(10, self.cfg.limits.max_repos_per_run), page=page)
                if not results:
                    break
                for repo in results:
                    if processed >= self.cfg.limits.max_repos_per_run:
                        break

                    action = self._precheck_repo(repo, seen)
                    if action != 'ok':
                        skip_counts[action] = skip_counts.get(action, 0) + 1
                        continue

                    seen.add(repo.full_name)
                    try:
                        assessment = self._assess_repo(repo, seed_keywords)
                    except Exception as exc:
                        print(f'[assess-error] {repo.full_name}: {exc}')
                        skip_counts['assess_error'] = skip_counts.get('assess_error', 0) + 1
                        continue

                    if assessment.similarity_score < self.cfg.limits.min_similarity_score:
                        skip_counts['low_similarity'] = skip_counts.get('low_similarity', 0) + 1
                        continue

                    action = self._handle_posting(assessment)
                    skip_counts[action] = skip_counts.get(action, 0) + 1
                    report_index.append({
                        'repo': repo.full_name,
                        'score': round(assessment.similarity_score, 4),
                        'confidence': round(assessment.review.confidence, 4),
                        'action': action,
                        'review_engine': assessment.snapshot.get('review_engine', 'heuristic'),
                    })
                    processed += 1
                    self._record_repo_touch(repo.full_name, action)
                    if action == 'posted':
                        posted += 1

        self._save_daily_state()
        self._save_repo_history()

        run_summary = {
            'processed': processed,
            'posted': posted,
            'issues_posted_today': self.daily_state['issues_posted'],
            'issues_posted_this_run': self.run_state['issues_posted'],
            'skip_counts': skip_counts,
            'index': report_index,
        }
        (self.output_dir / 'index.json').write_text(json.dumps(report_index, indent=2), encoding='utf-8')
        (self.output_dir / 'run_summary.json').write_text(json.dumps(run_summary, indent=2), encoding='utf-8')
        self._print_run_summary(run_summary)

    def _precheck_repo(self, repo: RepoProfile, seen: Set[str]) -> str:
        if repo.full_name in seen:
            return 'already_seen'
        if repo.disabled:
            return 'disabled'
        if repo.archived and not self.cfg.search.include_archived:
            return 'archived'
        if repo.fork and not self.cfg.search.include_forks:
            return 'fork'
        if repo.stars < self.cfg.search.min_stars:
            return 'below_min_stars'
        if self.cfg.search.languages:
            lang = (repo.language or '').lower()
            if lang not in {x.lower() for x in self.cfg.search.languages}:
                return 'language_filtered'
        if self.cfg.search.required_topics:
            topics = {t.lower() for t in repo.topics}
            if not topics.intersection({x.lower() for x in self.cfg.search.required_topics}):
                return 'topic_filtered'
        history = self.repo_history.get(repo.full_name, {})
        last_touched = history.get('last_touched_at')
        if last_touched and self.cfg.limits.repost_cooldown_days > 0:
            try:
                touched_at = datetime.fromisoformat(last_touched)
                if datetime.now(timezone.utc) - touched_at < timedelta(days=self.cfg.limits.repost_cooldown_days):
                    return 'cooldown'
            except ValueError:
                pass
        return 'ok'

    def _record_repo_touch(self, full_name: str, action: str) -> None:
        self.repo_history[full_name] = {
            'last_touched_at': datetime.now(timezone.utc).isoformat(),
            'last_action': action,
        }

    def _assess_repo(self, repo: RepoProfile, seed_keywords: List[str]) -> RepoAssessment:
        self.pacer.before_clone()
        target_dir = self.workspace_dir / repo.full_name.replace('/', '__')
        try:
            if not clone_repo(repo.clone_url, target_dir, depth=self.cfg.limits.max_clone_depth):
                raise RuntimeError(f'Clone failed for {repo.full_name}')

            snapshot = collect_snapshot(target_dir, self.cfg.limits)
            heuristics = heuristic_findings(target_dir, snapshot)
            repo_readme = self.gh.get_readme(repo.full_name)
            repo_keywords = profile_keywords(repo, repo_readme)
            repo_keywords.extend(source_keywords(snapshot.get('source_samples', {})))
            score = cosine_similarity(seed_keywords, repo_keywords)
            contribution_rules = self.gh.get_contributing_rules(repo.full_name)
            review, engine_used = self._build_review(repo, score, snapshot, heuristics, contribution_rules)

            persisted = {k: v for k, v in snapshot.items() if k != 'source_samples'}
            persisted['review_engine'] = engine_used
            assessment = RepoAssessment(repo=repo, similarity_score=score, snapshot=persisted, heuristics=heuristics, review=review, contribution_rules=contribution_rules)
            self._save_assessment(assessment)
            return assessment
        finally:
            safe_delete(target_dir)

    def _build_review(self, repo: RepoProfile, score: float, snapshot: Dict[str, Any], heuristics: List[str], contribution_rules: Dict[str, Any]) -> Tuple[ReviewResult, str]:
        payload: Dict[str, Any] = {
            'repo': repo.to_dict(),
            'similarity_score': round(score, 4),
            'snapshot': {k: v for k, v in snapshot.items() if k != 'source_samples'},
            'source_samples': snapshot.get('source_samples', {}),
            'heuristics': heuristics,
            'contribution_rules': contribution_rules,
        }
        try:
            result = self.claude.review(payload)
            if result:
                print(f'[claude] reviewed {repo.full_name} — confidence={result.get("confidence", 0):.2f}')
                return review_from_ai_result(repo, result), 'claude'
        except Exception as exc:
            print(f'[claude-fallback] {repo.full_name}: {exc}')

        try:
            result = self.free_llm.review(payload)
            if result:
                print(f'[free-llm] reviewed {repo.full_name} — confidence={result.get("confidence", 0):.2f}')
                return review_from_ai_result(repo, result), 'free_llm'
        except Exception as exc:
            print(f'[free-llm-fallback] {repo.full_name}: {exc}')

        if self.cfg.ai.enabled:
            try:
                result = self.ai.review(payload)
                if result:
                    print(f'[ai] reviewed {repo.full_name}')
                    return review_from_ai_result(repo, result), 'openai'
            except Exception as exc:
                print(f'[ai-fallback] {repo.full_name}: {exc}')

        print(f'[heuristic] {repo.full_name} — no AI available')
        symbols_sample = []
        for names in (snapshot.get('symbol_samples') or {}).values():
            for name in names:
                if name not in symbols_sample:
                    symbols_sample.append(name)
        evidence = {
            'readme_present': any(x in (snapshot.get('root_files') or []) for x in ('README.md', 'README.rst', 'README.txt', 'README')),
            'has_license': any('license' in x.lower() for x in (snapshot.get('root_files') or [])),
            'source_file_sample_count': snapshot.get('source_file_sample_count', 0),
            'matched_files': list((snapshot.get('source_samples') or {}).keys())[:5],
            'symbol_files': [k for k, v in (snapshot.get('symbol_samples') or {}).items() if not v][:3],
            'symbols_sample': symbols_sample[:12],
            'contrib_files': contribution_rules.get('files', []),
        }
        return default_review(repo, score, heuristics, evidence=evidence), 'heuristic'

    def _save_assessment(self, assessment: RepoAssessment) -> None:
        slug = assessment.repo.full_name.replace('/', '__')
        report = {
            'repo': assessment.repo.to_dict(),
            'similarity_score': round(assessment.similarity_score, 4),
            'snapshot': assessment.snapshot,
            'heuristics': assessment.heuristics,
            'review': asdict(assessment.review),
            'contribution_rules': assessment.contribution_rules,
        }
        (self.output_dir / f'{slug}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        engine = assessment.snapshot.get('review_engine', 'unknown')
        md = [
            f'# {assessment.repo.full_name}',
            f'[View on GitHub]({assessment.repo.html_url})',
            '',
            f'**Stars:** {assessment.repo.stars} | **Language:** {assessment.repo.language or "unknown"} | **License:** {assessment.repo.license_name or "none"} | **Review engine:** {engine}',
            '',
            f'**Similarity score:** {assessment.similarity_score:.3f} | **Confidence:** {assessment.review.confidence:.3f}',
            '',
            '## Summary',
            assessment.review.summary,
            '',
            '## What looks good',
            *[f'- {x}' for x in assessment.review.praise],
            '',
            '## Concerns',
            *[f'- {x}' for x in assessment.review.concerns],
            '',
            '## Suggested improvements',
            *[f'- {x}' for x in assessment.review.improvements],
            '',
            '## Proposed issue title',
            f'`{assessment.review.issue_title}`',
            '',
            '## Proposed issue body',
            '',
            assessment.review.issue_body,
            '',
            '## Contribution files found',
            *([f'- {x}' for x in assessment.contribution_rules.get('files', [])] or ['- None']),
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

        engine = assessment.snapshot.get('review_engine', 'heuristic')
        if engine == 'heuristic' and posting.enabled and not posting.draft_only:
            print(f'[posting] skipping {repo_name} — heuristic-only review is not good enough to post live')
            return 'heuristic_only_skipped'
        if not self._has_strong_evidence(assessment):
            print(f'[posting] skipping {repo_name} — evidence gate failed')
            return 'evidence_gate_failed'

        duplicate_reason = self._find_duplicate_reason(repo_name, assessment.review.issue_title, assessment.review.issue_body)
        if duplicate_reason:
            return duplicate_reason

        if posting.draft_only or not posting.enabled:
            self._write_approval_bundle(assessment, 'draft_only')
            return 'draft_only'
        if posting.require_manual_approval:
            self._write_approval_bundle(assessment, 'manual_approval')
            return 'manual_approval_required'
        if posting.allowlist and repo_name not in posting.allowlist:
            self._write_approval_bundle(assessment, 'not_allowlisted')
            return 'not_allowlisted'
        if self.daily_state['issues_posted'] >= self.cfg.limits.max_issue_posts_per_day:
            return 'daily_cap_reached'
        if self.run_state['issues_posted'] >= self.cfg.limits.max_issue_posts_per_run:
            return 'run_cap_reached'

        self.pacer.before_issue()
        try:
            self.gh.create_issue(repo_name, assessment.review.issue_title, assessment.review.issue_body, posting.labels)
        except Exception as exc:
            print(f'[post-error] {repo_name}: {exc}')
            self._write_approval_bundle(assessment, 'post_error')
            return 'post_error'

        self.daily_state['issues_posted'] += 1
        self.run_state['issues_posted'] += 1
        return 'posted'

    def _write_approval_bundle(self, assessment: RepoAssessment, reason: str) -> None:
        slug = assessment.repo.full_name.replace('/', '__')
        base = self.approval_dir / slug
        payload = {
            'reason': reason,
            'repo': assessment.repo.to_dict(),
            'similarity_score': round(assessment.similarity_score, 4),
            'confidence': round(assessment.review.confidence, 4),
            'review_engine': assessment.snapshot.get('review_engine', 'unknown'),
            'issue_title': assessment.review.issue_title,
            'issue_body': assessment.review.issue_body,
            'concerns': assessment.review.concerns,
            'improvements': assessment.review.improvements,
            'contribution_files': assessment.contribution_rules.get('files', []),
            'matched_paths': assessment.snapshot.get('all_paths_sample', [])[:20],
            'symbol_samples': assessment.snapshot.get('symbol_samples', {}),
        }
        base.with_suffix('.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        md = [
            f'# Approval bundle: {assessment.repo.full_name}',
            '',
            f'**Reason queued:** {reason}',
            f'**Review engine:** {payload["review_engine"]}',
            f'**Similarity:** {payload["similarity_score"]}',
            f'**Confidence:** {payload["confidence"]}',
            '',
            '## Proposed issue title',
            f'`{assessment.review.issue_title}`',
            '',
            '## Proposed issue body',
            assessment.review.issue_body,
            '',
            '## Concerns',
            *[f'- {x}' for x in assessment.review.concerns],
            '',
            '## Evidence paths',
            *[f'- {x}' for x in payload['matched_paths']],
        ]
        base.with_suffix('.md').write_text('\n'.join(md), encoding='utf-8')

    def _find_duplicate_reason(self, repo_name: str, title: str, body: str) -> Optional[str]:
        try:
            issues = self.gh.list_issues(repo_name, state='all', max_pages=self.cfg.limits.max_issue_scan_pages)
        except Exception as exc:
            print(f'[issues-fetch-warn] {repo_name}: {exc}')
            return None

        title_key = self._normalize_text(title)
        body_key = self._normalize_text(body)
        avoid_phrases = [self._normalize_text(x) for x in self.cfg.posting.avoid_existing_issue_titles_like if len(self._normalize_text(x).split()) >= 2]

        for issue in issues:
            if 'pull_request' in issue:
                continue
            existing_title = self._normalize_text(str(issue.get('title', '')))
            existing_body = self._normalize_text(str(issue.get('body', '')))
            if not existing_title:
                continue
            if title_key == existing_title or title_key in existing_title or existing_title in title_key:
                return 'duplicate_title'
            if self._overlap_ratio(title_key, existing_title) >= self.cfg.limits.duplicate_title_overlap_threshold:
                return 'duplicate_similar_title'
            body_overlap = self._overlap_ratio(body_key, existing_body)
            if body_overlap >= 0.82 and body_key and existing_body:
                return 'duplicate_similar_body'
            if avoid_phrases and any(phrase in existing_title for phrase in avoid_phrases):
                return 'duplicate_config_phrase'
        return None

    def _has_strong_evidence(self, assessment: RepoAssessment) -> bool:
        concern_text = ' '.join(assessment.review.concerns).lower()
        path_sample = assessment.snapshot.get('all_paths_sample', [])[:20]
        has_path_evidence = bool(path_sample) and any('/' in p or '.' in p for p in path_sample)
        has_numeric_signal = bool(re.search(r'\d+', concern_text))
        has_explicit_path_in_concerns = any('/' in c or '.py' in c or '.js' in c or '.ts' in c or '.md' in c for c in assessment.review.concerns)
        sampled_sources = int(assessment.snapshot.get('source_file_sample_count', 0))
        return sampled_sources >= 3 and has_path_evidence and (has_numeric_signal or has_explicit_path_in_concerns)

    @staticmethod
    def _normalize_text(text: str) -> str:
        cleaned = ''.join(ch.lower() if ch.isalnum() or ch.isspace() else ' ' for ch in text)
        return ' '.join(cleaned.split())

    @classmethod
    def _overlap_ratio(cls, a: str, b: str) -> float:
        sa, sb = set(a.split()), set(b.split())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / max(len(sa), len(sb))

    @staticmethod
    def _print_run_summary(summary: Dict[str, Any]) -> None:
        print('\n' + '=' * 60)
        print('RUN SUMMARY')
        print('=' * 60)
        print(f'  Repos processed : {summary["processed"]}')
        print(f'  Issues posted   : {summary["posted"]}')
        print(f'  Posted today    : {summary["issues_posted_today"]}')
        print(f'  Posted this run : {summary["issues_posted_this_run"]}')
        if summary['skip_counts']:
            print('\n  Skip reasons:')
            for reason, count in sorted(summary['skip_counts'].items(), key=lambda kv: -kv[1]):
                print(f'    {reason:<35} {count}')
        print()
