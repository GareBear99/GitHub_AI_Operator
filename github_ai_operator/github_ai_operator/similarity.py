from __future__ import annotations

from collections import Counter
from typing import Iterable, List, Sequence, Tuple
import math
import re

from .models import RepoProfile

WORD_RE = re.compile(r'[a-zA-Z0-9_+#.-]{3,}')
STOP = {'the', 'and', 'with', 'from', 'that', 'this', 'your', 'for', 'into', 'using', 'tool', 'repo', 'repository'}


def tokenize(text: str) -> List[str]:
    return [w.lower() for w in WORD_RE.findall(text) if w.lower() not in STOP]


def profile_keywords(repo: RepoProfile, readme: str = '') -> List[str]:
    bag: List[str] = []
    bag.extend(tokenize(repo.full_name.replace('/', ' ')))
    bag.extend(tokenize(repo.description))
    bag.extend([t.lower() for t in repo.topics])
    if repo.language:
        bag.append(repo.language.lower())
    bag.extend(tokenize(readme[:5000]))
    return bag


def cosine_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    shared = set(ca) & set(cb)
    numerator = sum(ca[k] * cb[k] for k in shared)
    denom = math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values()))
    if not denom:
        return 0.0
    return numerator / denom


def build_queries(seed_repos: List[Tuple[RepoProfile, str]], custom_queries: Iterable[str], min_stars: int = 0, pushed_after: str | None = None) -> List[str]:
    queries: List[str] = []
    for repo, readme in seed_repos:
        kws = profile_keywords(repo, readme)
        top = [k for k, _ in Counter(kws).most_common(6)]
        parts = top[:4]
        if repo.language:
            parts.append(f'language:{repo.language}')
        if min_stars > 0:
            parts.append(f'stars:>={min_stars}')
        if pushed_after:
            parts.append(f'pushed:>={pushed_after}')
        queries.append(' '.join(parts).strip())
        topic_parts = [f'topic:{t}' for t in repo.topics[:3]]
        if topic_parts:
            queries.append(' '.join(topic_parts + ([f'language:{repo.language}'] if repo.language else [])))
    queries.extend([q.strip() for q in custom_queries if q.strip()])
    seen = set()
    out = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out
