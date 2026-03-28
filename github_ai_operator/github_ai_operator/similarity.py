from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple
import math

from .models import RepoProfile

WORD_RE = re.compile(r'[a-zA-Z0-9_+#.\-]{3,}')

# Expanded stop-word list that suppresses generic GitHub / tech vocabulary
STOP = {
    'the', 'and', 'with', 'from', 'that', 'this', 'your', 'for', 'into', 'using',
    'tool', 'repo', 'repository', 'project', 'code', 'file', 'files', 'github',
    'open', 'source', 'readme', 'license', 'contributing', 'docs', 'documentation',
    'example', 'sample', 'demo', 'app', 'lib', 'library', 'package', 'module',
    'simple', 'basic', 'easy', 'small', 'fast', 'based', 'written', 'made',
    'use', 'used', 'build', 'run', 'get', 'set', 'add', 'new', 'api', 'data',
    'list', 'test', 'tests', 'version', 'release', 'update', 'main', 'master',
    'src', 'include', 'util', 'utils', 'config', 'setup', 'install',
}


def normalize_token(token: str) -> str:
    t = token.lower().strip('._-')
    t = t.replace('c++', 'cpp').replace('c#', 'csharp')
    return t


def tokenize(text: str) -> List[str]:
    return [
        normalized
        for w in WORD_RE.findall(text)
        if (normalized := normalize_token(w)) and normalized not in STOP and len(normalized) >= 3
    ]


def profile_keywords(repo: RepoProfile, readme: str = '') -> List[str]:
    """Build a keyword bag from repo metadata + README."""
    bag: List[str] = []
    # Name and description get extra weight via repetition
    name_tokens = tokenize(repo.full_name.replace('/', ' '))
    bag.extend(name_tokens * 2)
    desc_tokens = tokenize(repo.description)
    bag.extend(desc_tokens * 2)
    # Topics are very high signal
    topic_tokens = [normalize_token(t) for t in repo.topics]
    bag.extend(topic_tokens * 3)
    if repo.language:
        bag.extend([normalize_token(repo.language)] * 2)
    # License presence is minor signal
    if repo.license_name:
        bag.extend(tokenize(repo.license_name))
    # README sampled text (lower weight, capped)
    bag.extend(tokenize(readme[:8000]))
    return bag


def source_keywords(source_samples: Dict[str, str]) -> List[str]:
    """Extract keyword signals from sampled source files."""
    bag: List[str] = []
    for rel, content in list(source_samples.items())[:40]:
        # File path tokens are medium signal
        bag.extend(tokenize(rel))
        # Source content first 2000 chars — lower signal
        bag.extend(tokenize(content[:2000]))
    return bag


def weighted_bag(items: Sequence[str], weight: float) -> List[str]:
    repeats = max(1, round(weight))
    out: List[str] = []
    for item in items:
        out.extend([item] * repeats)
    return out


def cosine_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    ca, cb = Counter(a), Counter(b)
    shared = set(ca) & set(cb)
    numerator = sum(ca[k] * cb[k] for k in shared)
    denom = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    if not denom:
        return 0.0
    return numerator / denom


def build_queries(
    seed_repos: List[Tuple[RepoProfile, str]],
    custom_queries: Iterable[str],
    min_stars: int = 0,
    pushed_after: str | None = None,
    mode: str = 'related',
) -> List[str]:
    """Generate de-duplicated search queries from seed profiles and optional custom queries."""
    queries: List[str] = []
    if mode in {'related', 'hybrid'}:
        for repo, readme in seed_repos:
            kws = profile_keywords(repo, readme)
            top_kws = [k for k, _ in Counter(kws).most_common(10)]
            # Primary query: top keyword terms + language
            primary_parts = top_kws[:4]
            if repo.language:
                primary_parts.append(f'language:{repo.language}')
            if min_stars > 0:
                primary_parts.append(f'stars:>={min_stars}')
            if pushed_after:
                primary_parts.append(f'pushed:>={pushed_after}')
            if primary_parts:
                queries.append(' '.join(primary_parts).strip())
            # Secondary query: topic-based (high precision)
            topic_parts = [f'topic:{t}' for t in repo.topics[:4]]
            if topic_parts:
                lang_suffix = [f'language:{repo.language}'] if repo.language else []
                star_suffix = [f'stars:>={min_stars}'] if min_stars > 0 else []
                queries.append(' '.join(topic_parts + lang_suffix + star_suffix))

    if mode in {'custom', 'hybrid', 'related'}:
        queries.extend([q.strip() for q in custom_queries if q.strip()])

    # De-duplicate while preserving order
    seen: set[str] = set()
    out: List[str] = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out
