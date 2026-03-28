#!/usr/bin/env python3
"""
scout.py — entry point for the GitHub AI operator.

Usage examples:
  python scout.py --config config.example.json
  python scout.py --config config.example.json --print-queries
  python scout.py --config config.example.json --dry-run
"""
from __future__ import annotations

import argparse
import random
import sys

from github_ai_operator.config import AppConfig
from github_ai_operator.delay import HumanPacer
from github_ai_operator.engine import OperatorEngine
from github_ai_operator.github_api import GitHubClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='GitHub AI operator: scout + review + optional posting',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('--config', required=True, help='Path to config JSON file')
    p.add_argument(
        '--print-queries',
        action='store_true',
        help='Print generated search queries and exit without running',
    )
    p.add_argument(
        '--dry-run',
        action='store_true',
        help='Run without cloning or posting (print queries, validate config)',
    )
    p.add_argument(
        '--seed',
        type=int,
        default=1337,
        help='Random seed for pacing jitter (use different values for unique timing per run)',
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg = AppConfig.from_json(args.config)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f'[config-error] {exc}', file=sys.stderr)
        return 2

    if args.dry_run or args.print_queries:
        print('[dry-run] Config loaded successfully.')
        print(f'[dry-run] Seed repos: {[s.full_name for s in cfg.seed_repos]}')
        print(f'[dry-run] Posting enabled: {cfg.posting.enabled} | draft_only: {cfg.posting.draft_only}')
        try:
            gh = GitHubClient()
            pacer = HumanPacer(cfg.delay_profile, random.Random(args.seed))
            engine = OperatorEngine(cfg, gh, pacer)
            queries = engine.print_queries()
            print(f'[dry-run] {len(queries)} queries generated.')
        except Exception as exc:
            print(f'[dry-run-warn] Could not fetch seed profiles: {exc}')
        return 0

    try:
        gh = GitHubClient()
        pacer = HumanPacer(cfg.delay_profile, random.Random(args.seed))
        engine = OperatorEngine(cfg, gh, pacer)
        engine.run()
        return 0
    except KeyboardInterrupt:
        print('\n[aborted] interrupted by user', file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f'[fatal] {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'[fatal] unexpected error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
