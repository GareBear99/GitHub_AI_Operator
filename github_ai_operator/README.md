# GitHub AI Operator

GitHub AI Operator is a two-mode Python package for:

1. **Discovery / Scout Mode** — find repositories related to your own repos or custom topic searches.
2. **Contribution / Interaction Mode** — review established repos, generate useful feedback, and optionally post issues in a controlled, low-volume, allowlisted way.

It clones one repo at a time into a temporary workspace, reviews it, writes local reports, optionally posts a high-confidence issue, deletes the clone, and moves on.

## Main idea

This package is built around the split you described:

- **Related repo search aspect**: find projects similar to your repos, your niche, or your custom search themes.
- **Promotional/helpful aspect**: leave genuinely useful feedback on established repos so your work is visible through quality contributions rather than spam.

## What it does

- Reads one or more **seed repos** that represent your taste and project direction.
- Pulls repo metadata and README content from GitHub.
- Auto-generates **related search queries** from repo names, descriptions, topics, languages, and README keywords.
- Supports **custom queries** alongside related-search mode.
- Searches GitHub for candidate repositories.
- Applies filters for stars, language, topics, archived/fork state.
- Clones one repo at a time into a local temp workspace.
- Runs a lightweight code/repo review pass.
- Produces:
  - `.json` report
  - `.md` report
  - suggested issue title/body
- Checks for existing issues with similar titles.
- Respects:
  - daily posting caps
  - per-run posting caps
  - random human-style pacing
  - rate-limit handling
- Deletes the clone after processing.

## Safety defaults

By default this package is in **draft-only mode**.

That means it will:
- search repos
- review repos
- prepare issue drafts
- **not actually post issues** unless you intentionally enable posting

Recommended workflow:

1. Run in draft-only mode first.
2. Review generated reports.
3. Tune confidence thresholds.
4. Enable posting only for an allowlist or clearly welcomed repos.

## Package structure

```text
github_ai_operator/
├── github_ai_operator/
│   ├── __init__.py
│   ├── ai_client.py
│   ├── config.py
│   ├── delay.py
│   ├── engine.py
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
- `git` installed
- a GitHub token in `GITHUB_TOKEN`

Optional:
- an OpenAI-compatible API endpoint for stronger AI review generation

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
export GITHUB_TOKEN="YOUR_FINE_GRAINED_PAT"
```

If you want AI review generation:

```bash
export AI_API_KEY="YOUR_API_KEY"
```

Then edit `config.json`.

## Quick start

### 1. Print generated related queries

```bash
python scout.py --config config.json --print-queries
```

### 2. Run in discovery + draft mode

```bash
python scout.py --config config.json
```

## Config guide

### `seed_repos`
These are your anchor repos.

Example:

```json
"seed_repos": [
  {"full_name": "GareBear99/FreeEQ8", "weight": 1.0}
]
```

### `search.custom_queries`
Manual searches you want included.

Example:

```json
"custom_queries": [
  "audio plugin juce vst3 dsp stars:>=3",
  "creative coding synth plugin language:C++"
]
```

### `posting`
Key controls:

- `enabled`: allows actual posting logic to run
- `draft_only`: if `true`, no real issues are posted
- `allowlist`: repos allowed for actual issue posting
- `denylist`: repos never touched
- `require_manual_approval`: keeps the engine from posting even when enabled

### `delay_profile`
Human-style random pacing ranges.

This helps reduce robotic behavior and spaces out requests and issue creation.

### `limits`
Controls:

- max repos per run
- max issues per run/day
- clone depth
- scan limits
- similarity threshold
- issue confidence threshold

## Recommended modes

### Mode A — Scout only
Use this when you only want to find and review related repos.

Set:

```json
"posting": {
  "enabled": false,
  "draft_only": true
}
```

### Mode B — Contribute carefully
Use this when you want to actually leave helpful issue feedback.

Recommended settings:

```json
"posting": {
  "enabled": true,
  "draft_only": false,
  "allowlist": [
    "owner/repo"
  ]
}
```

## What the review currently checks

The lightweight local review currently looks for things like:

- missing README
- missing obvious license file
- missing obvious tests / GitHub automation
- very long source lines
- TODO / FIXME / HACK density
- missing `test` script in `package.json`

That is intentionally lightweight. It is meant to produce useful first-pass feedback, not pretend it performed a full formal audit.

## How posting works

Before issue creation, the engine checks:

- denylist
- contribution/issue template presence if configured
- confidence threshold
- existing issue titles to reduce duplicates
- daily cap
- per-run cap
- allowlist
- random human delay before mutation

## Notes on promotion

The visibility angle should come from **consistently useful feedback**, not self-advertising in issue bodies.

The healthiest usage pattern is:
- high relevance
- low volume
- good evidence
- respectful tone
- no self-promo inside issue content

That gives you a better chance of being noticed as a serious builder instead of a spammer.

## Practical next upgrades

Good v2 upgrades would be:

- Docker isolation per repo
- language-specific linters/test runners
- richer semantic similarity scoring
- better duplicate issue detection
- persisted cooldown history per repo
- schedule runner / cron integration
- star/fork/watch suggestion mode
- saved database of discovered repos
- GitHub App auth path instead of PAT-only
- local dashboard for report review and manual approval

## Example run flow

1. Read your seed repos.
2. Generate related searches.
3. Search GitHub.
4. Clone one matching repo into temp workspace.
5. Review it.
6. Save `.json` and `.md` reports.
7. Optionally post a high-confidence issue.
8. Delete the clone.
9. Move to next repo.

## Disclaimer

Use carefully.

Even when feedback is well-meaning, posting on public repos can still be unwelcome if it is too frequent, too generic, or too speculative. Draft-first review is strongly recommended.
