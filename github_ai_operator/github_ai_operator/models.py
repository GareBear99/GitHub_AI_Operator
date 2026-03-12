from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class RepoProfile:
    full_name: str
    html_url: str
    clone_url: str
    description: str = ''
    language: Optional[str] = None
    stars: int = 0
    topics: List[str] = field(default_factory=list)
    default_branch: Optional[str] = None
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    open_issues_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ReviewResult:
    summary: str
    praise: List[str]
    concerns: List[str]
    improvements: List[str]
    issue_title: str
    issue_body: str
    confidence: float


@dataclass
class RepoAssessment:
    repo: RepoProfile
    similarity_score: float
    snapshot: Dict
    heuristics: List[str]
    review: ReviewResult
    contribution_rules: Dict
