from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import RepoProfile, ReviewResult


def default_review(repo: RepoProfile, similarity_score: float, heuristics: List[str], evidence: Optional[Dict[str, Any]] = None) -> ReviewResult:
    evidence = evidence or {}

    praise: List[str] = []
    if repo.stars >= 5:
        praise.append(f'The project has {repo.stars} stars, suggesting real community interest.')
    if repo.topics:
        topic_str = ', '.join(repo.topics[:5])
        praise.append(f'Clearly-tagged topics ({topic_str}) aid discoverability.')
    if evidence.get('readme_present'):
        praise.append('A root README was found, giving contributors an immediate entry point.')
    if evidence.get('has_license'):
        praise.append('A license file is present, which is a basic requirement for open-source contribution.')
    if evidence.get('contrib_files'):
        contrib_str = ', '.join(evidence['contrib_files'][:3])
        praise.append(f'Contribution guidelines found ({contrib_str}), which lowers the barrier for new contributors.')
    if not praise:
        praise.append(f'The repository matched the scout criteria with a similarity score of {similarity_score:.2f}.')

    concerns = heuristics[:5] if heuristics else ['No specific high-confidence concerns were found in this lightweight pass.']

    improvements: List[str] = []
    if evidence.get('source_file_sample_count', 0) > 0:
        improvements.append(f'Reviewed {evidence["source_file_sample_count"]} source files; consider strengthening inline documentation where it is sparse.')
    symbol_files = evidence.get('symbol_files', [])
    if symbol_files:
        improvements.append(f'Expose a few more discoverable function/class names or comments in files like {", ".join(symbol_files[:3])}.')
    improvements.append('Ensure CI/CD configuration is visible and green-badged in the README.')
    improvements.append('Where applicable, add a CONTRIBUTING guide with clear PR and issue conventions.')

    title = f'Code-quality review: observed improvements for {repo.full_name.split("/")[-1]}'
    body = build_issue_body(repo, praise, concerns, improvements, evidence)
    confidence = 0.58 + min(0.22, 0.045 * len([c for c in concerns if c]))
    return ReviewResult(
        summary='A lightweight automated review pass identified a few potentially useful improvement areas based on repository metadata and sampled source files.',
        praise=praise,
        concerns=concerns,
        improvements=improvements,
        issue_title=title,
        issue_body=body,
        confidence=min(confidence, 0.80),
    )


def build_issue_body(repo: RepoProfile, praise: List[str], concerns: List[str], improvements: List[str], evidence: Optional[Dict[str, Any]] = None) -> str:
    evidence = evidence or {}

    def to_md(items: List[str]) -> str:
        return '\n'.join(f'- {x}' for x in items) if items else '- None noted.'

    evidence_lines: List[str] = []
    src_count = evidence.get('source_file_sample_count', 0)
    if src_count:
        evidence_lines.append(f'- Sampled {src_count} source file(s) during this pass.')
    matched = evidence.get('matched_files', [])
    if matched:
        evidence_lines.append(f'- Example files inspected: {", ".join(matched[:5])}')
    symbols = evidence.get('symbols_sample', [])
    if symbols:
        evidence_lines.append(f'- Example discovered symbols: {", ".join(symbols[:8])}')
    contrib = evidence.get('contrib_files', [])
    if contrib:
        evidence_lines.append(f'- Contribution-related files found: {", ".join(contrib[:5])}')
    if not evidence_lines:
        evidence_lines.append('- Metadata and structure scan only (no source files sampled).')

    evidence_block = '\n'.join(evidence_lines)

    return (
        'Hi — I ran an automated scout pass over this repository because it looked genuinely relevant to projects I track.\n\n'
        f'**Repository:** [{repo.full_name}]({repo.html_url})\n\n'
        '**What already looks promising**\n'
        f'{to_md(praise)}\n\n'
        '**Evidence basis for this review**\n'
        f'{evidence_block}\n\n'
        '**Observed concerns**\n'
        f'{to_md(concerns)}\n\n'
        '**Suggested improvements**\n'
        f'{to_md(improvements)}\n\n'
        '---\n'
        '*Notes: This is a lightweight automated review pass. All findings should be verified manually before acting on them. Shared in the spirit of constructive contribution, not criticism.*\n'
    )


def review_from_ai_result(repo: RepoProfile, ai_result: Dict[str, Any]) -> ReviewResult:
    def _ensure_list(val: Any) -> List[str]:
        if isinstance(val, list):
            return [str(x) for x in val]
        if val:
            return [str(val)]
        return []

    issue_body = ai_result.get('issue_body', '')
    if not issue_body:
        issue_body = build_issue_body(repo, _ensure_list(ai_result.get('praise')), _ensure_list(ai_result.get('concerns')), _ensure_list(ai_result.get('improvements')))

    raw_confidence = ai_result.get('confidence', 0.7)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))

    return ReviewResult(
        summary=ai_result.get('summary', ''),
        praise=_ensure_list(ai_result.get('praise')),
        concerns=_ensure_list(ai_result.get('concerns')),
        improvements=_ensure_list(ai_result.get('improvements')),
        issue_title=ai_result.get('issue_title', f'Code review for {repo.full_name.split("/")[-1]}'),
        issue_body=issue_body,
        confidence=confidence,
    )
