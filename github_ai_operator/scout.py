#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random

from github_ai_operator.config import AppConfig
from github_ai_operator.delay import HumanPacer
from github_ai_operator.engine import OperatorEngine
from github_ai_operator.github_api import GitHubClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='GitHub AI operator: scout + contribute modes')
    p.add_argument('--config', required=True, help='Path to config JSON')
    p.add_argument('--print-queries', action='store_true', help='Only print generated queries')
    p.add_argument('--seed', type=int, default=1337, help='Random seed for pacing jitter')
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = AppConfig.from_json(args.config)
    gh = GitHubClient()
    pacer = HumanPacer(cfg.delay_profile, random.Random(args.seed))
    engine = OperatorEngine(cfg, gh, pacer)
    if args.print_queries:
        engine.print_queries()
    else:
        engine.run()


if __name__ == '__main__':
    main()
