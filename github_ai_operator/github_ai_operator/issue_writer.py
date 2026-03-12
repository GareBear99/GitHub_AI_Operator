from __future__ import annotations

from typing import Dict, List

from .models import RepoProfile, ReviewResult


def default_review(repo: RepoProfile, similarity_score: float, heuristics: List[str]) -> ReviewResult:
    praise = [
        f'The project concept looks relevant to the target niche and scored {similarity_score:.2f} for similarity.',
        f'The repository already has visible community traction with {repo.stars} stars.',
    ]
    concerns = heuristics[:4] if heuristics else ['No obvious high-confidence issues were found in the lightweight pass.']
    improvements = [
        'Strengthen setup, usage, and contribution guidance in the root documentation.',
        'Add or surface tests and CI signals more clearly if they already exist.',
        'Break down fragile or oversized modules where practical to improve maintainability.',
    ]
    title = f'Constructive review: a few possible improvements for {repo.full_name.split("/")[-1]}'
    body = build_issue_body(repo, praise, concerns, improvements)
    confidence = 0.65 + min(0.2, 0.03 * len(concerns))
    return ReviewResult(
        summary='A lightweight automated review found a few potentially useful improvement areas.',
        praise=praise,
        concerns=concerns,
        improvements=improvements,
        issue_title=title,
        issue_body=body,
        confidence=min(confidence, 0.88),
    )


def build_issue_body(repo: RepoProfile, praise: List[str], concerns: List[str], improvements: List[str]) -> str:
    to_md = lambda items: '\n'.join(f'- {x}' for x in items)
    return (
        'Hi — I took a respectful automated review pass over this repository because it looks genuinely interesting.\n\n'
        f'**Repository:** {repo.full_name}\n\n'
        '**What already looks promising**\n'
        f'{to_md(praise)}\n\n'
        '**Possible issues or things worth checking**\n'
        f'{to_md(concerns)}\n\n'
        '**Potential improvements**\n'
        f'{to_md(improvements)}\n\n'
        'Notes:\n'
        '- This was a lightweight automated review pass. Any weakly-evidenced point should be verified manually.\n'
        '- Shared in the spirit of helpful contribution rather than criticism.\n'
    )


def review_from_ai_result(repo: RepoProfile, ai_result: Dict) -> ReviewResult:
    return ReviewResult(
        summary=ai_result.get('summary', ''),
        praise=ai_result.get('praise', []) if isinstance(ai_result.get('praise'), list) else [str(ai_result.get('praise', ''))],
        concerns=ai_result.get('concerns', []) if isinstance(ai_result.get('concerns'), list) else [str(ai_result.get('concerns', ''))],
        improvements=ai_result.get('improvements', []) if isinstance(ai_result.get('improvements'), list) else [str(ai_result.get('improvements', ''))],
        issue_title=ai_result.get('issue_title', f'Constructive review for {repo.full_name.split("/")[-1]}'),
        issue_body=ai_result.get('issue_body', ''),
        confidence=float(ai_result.get('confidence', 0.7)),
    )
