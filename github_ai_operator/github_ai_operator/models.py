from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


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
    has_wiki: bool = False
    license_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
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
    snapshot: Dict[str, Any]
    heuristics: List[str]
    review: ReviewResult
    contribution_rules: Dict[str, Any]


@dataclass
class PostDecision:
    """Structured record of why a repo was posted or skipped."""
    repo_full_name: str
    action: str
    similarity_score: float
    confidence: float
    reason: str = ''
