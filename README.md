# GitHub AI Operator

Evidence-first GitHub scout and review operator for **discovering related repositories, generating review reports, and queueing high-confidence issue drafts**.

This build is designed to be **safer than a naive autonomous poster**:
- draft-first by default
- approval queue support
- cooldown history
- per-run and per-day caps
- duplicate checks against **open and closed** issues
- evidence gate before live posting

## What it does

1. Reads one or more seed repositories.
2. Builds related GitHub search queries from metadata, topics, README text, and sampled source signals.
3. Searches public repositories via the GitHub API.
4. Clones one candidate at a time into a temp workspace.
5. Collects a lightweight structural snapshot.
6. Runs one of these review layers:
   - Anthropic / Claude
   - Free / OpenAI-compatible providers
   - heuristic fallback
7. Saves JSON + Markdown reports locally.
8. Optionally queues or posts a high-confidence issue.
9. Deletes the clone and moves on.

## Safety posture

Default posture is safe:
- `posting.enabled = false`
- `posting.draft_only = true`

That means the operator will still scout, review, and write reports, but it will **not** mutate target repositories unless you explicitly enable live posting.

Live posting is additionally protected by:
- confidence threshold
- evidence gate
- allowlist / denylist
- duplicate-title and duplicate-body checks
- cooldown history
- issue caps

## Current strengths

- related repo discovery from your own repos
- report generation with Markdown + JSON output
- approval queue bundles for manual review
- contribution-file awareness
- duplicate checks across open and closed issues
- label-safe posting
- review-layer fallback stack

## Current limits

This is **not** a full semantic code auditor yet.
It currently works best as a:
- scout
- triage assistant
- issue-draft generator
- manual-review accelerator

It does **not** run full CI, execute tests inside target repos, or guarantee that an AI-generated review is correct.

## Project layout

```text
github_ai_operator/
├── github_ai_operator/
│   ├── __init__.py
│   ├── ai_client.py
│   ├── anthropic_client.py
│   ├── config.py
│   ├── delay.py
│   ├── engine.py
│   ├── free_llm_client.py
│   ├── github_api.py
│   ├── issue_writer.py
│   ├── models.py
│   ├── review.py
│   └── similarity.py
├── config.example.json
├── README.md
├── requirements.txt
└── scout.py
```

## Requirements

- Python 3.10+
- `git`
- a GitHub token in `GITHUB_TOKEN`

Optional:
- `ANTHROPIC_API_KEY`
- provider-specific AI keys if you want richer review generation

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
export GITHUB_TOKEN="YOUR_FINE_GRAINED_PAT"
```

## Quick start

Print generated queries:

```bash
python scout.py --config config.json --print-queries
```

Run safely in draft mode:

```bash
python scout.py --config config.json
```

Dry run only:

```bash
python scout.py --config config.json --dry-run
```

## Output

```text
output/
  owner__repo.json
  owner__repo.md
  index.json
  run_summary.json
  daily_state.json
  repo_history.json
  approval_queue/
    owner__repo.json
    owner__repo.md
```

## Recommended usage pattern

### Mode A — Scout only
Use this to discover related repositories and generate reports.

```json
"posting": {
  "enabled": false,
  "draft_only": true
}
```

### Mode B — Approval queue
Use this when you want strong drafts but still want a human gate.

```json
"posting": {
  "enabled": true,
  "draft_only": false,
  "require_manual_approval": true,
  "allowlist": ["owner/repo"]
}
```

### Mode C — Live posting
Only use this once you have tuned thresholds and are confident in the evidence quality.

```json
"posting": {
  "enabled": true,
  "draft_only": false,
  "allowlist": ["owner/repo"]
}
```

## Best practices

- keep volume low
- keep relevance high
- verify reports before posting live
- prefer allowlists over broad posting
- use the approval queue first
- treat heuristic-only reviews as drafts, not final judgment

## What changed in this hardened version

- fixed packaging layout
- added approval queue bundles
- improved duplicate detection
- checks open + closed issues
- evidence gate before live posting
- stronger snapshot extraction with symbol samples
- safer run/report flow

## License

MIT
