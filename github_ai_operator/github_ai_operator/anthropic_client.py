from __future__ import annotations

"""
anthropic_client.py — Claude as the review brain via the Anthropic API.

Uses claude-sonnet-4-20250514. No external API key needed when running
inside the Claude environment — the Authorization header is handled by
the proxy layer. When running standalone, set ANTHROPIC_API_KEY.

The system prompt is the core engineering surface. It controls tone,
evidence requirements, and what makes an issue worth posting vs. skipping.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 2000

SYSTEM_PROMPT = """You are a senior open-source developer doing a genuine, careful code review.

Your only goal: write a GitHub issue that a maintainer reads and thinks
"this person actually looked at my project and has something useful to say."

STRICT RULES:
1. Evidence only. Every concern must reference a specific file, line pattern,
   count, or observable fact from the data given to you. No invented concerns.
2. If the repo is clean — say so. Ask a genuine technical question instead of
   manufacturing problems. That is more impressive than fake criticism.
3. One real issue is better than five weak ones. Do not pad.
4. Tone: peer reviewer, not auditor. "I noticed X — have you considered Y?"
   not "You should fix X."
5. Tight writing. The full issue body must be readable in under 2 minutes.
6. End with one genuine question or conversation opener. Something a human
   would actually want to answer.
7. The title must sound like a human wrote it. Not "Automated review findings."
   Something like "A few thoughts on the error handling in core.cpp" or
   "Quick question about your approach to X after reading through the code."
8. confidence reflects how certain you are that the concerns are real,
   specific, and worth a maintainer's time. Be honest — low confidence
   means don't post.

Return ONLY valid JSON. No markdown fences. No preamble. No postamble.
Schema:
{
  "summary": "one sentence — what you actually found",
  "praise": ["specific things that are genuinely good, with evidence"],
  "concerns": ["specific, evidence-backed concerns — only real ones"],
  "improvements": ["concrete actionable suggestions tied to the concerns"],
  "issue_title": "human-sounding title, not bot-sounding",
  "issue_body": "full markdown issue body ready to post",
  "confidence": 0.0,
  "conversation_hook": "the closing question or invitation"
}"""


class AnthropicReviewer:
    """
    Uses Claude directly as the review intelligence.
    
    This replaces the generic AIReviewer. Claude reads actual source code,
    understands what the project does, and writes something that sounds like
    a senior developer spent real time with the repo.
    """

    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        max_retries: int = 2,
        timeout: int = 120,
    ) -> None:
        self.api_key_env = api_key_env
        self.max_retries = max_retries
        self.timeout = timeout

    def review(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Send repo data to Claude and get back a structured review.
        Returns None if unavailable so engine falls back to heuristics.
        """
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            print(f"[claude] {self.api_key_env!r} not set — falling back to heuristics")
            return None

        user_content = self._build_prompt(payload)

        headers = {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    ANTHROPIC_API_URL,
                    headers=headers,
                    json=body,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                # Extract text from content blocks
                raw = "".join(
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                )

                parsed = self._extract_json(raw)
                if parsed:
                    return self._validate(parsed)
                raise ValueError(f"No valid JSON in response: {raw[:400]!r}")

            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait = 2.0 * (attempt + 1)
                    print(f"[claude-retry] attempt {attempt+1}: {exc}; retrying in {wait:.1f}s")
                    time.sleep(wait)

        if last_error:
            raise last_error
        return None

    @staticmethod
    def _build_prompt(payload: Dict[str, Any]) -> str:
        """
        Build a focused prompt that gives Claude only what it needs.
        Source samples are the most valuable signal — include them fully.
        """
        repo = payload.get("repo", {})
        snapshot = payload.get("snapshot", {})
        heuristics = payload.get("heuristics", [])
        contribution_rules = payload.get("contribution_rules", {})
        source_samples = payload.get("source_samples", {})
        similarity_score = payload.get("similarity_score", 0.0)

        parts: List[str] = []

        # --- Repo identity ---
        parts.append(f"# Repo: {repo.get('full_name', 'unknown')}")
        parts.append(f"Description: {repo.get('description') or 'none'}")
        parts.append(f"Language: {repo.get('language') or 'unknown'}")
        parts.append(f"Stars: {repo.get('stars', 0)}")
        parts.append(f"Topics: {', '.join(repo.get('topics', [])) or 'none'}")
        parts.append(f"License: {repo.get('license_name') or 'none detected'}")
        parts.append(f"Open issues: {repo.get('open_issues_count', 0)}")
        parts.append(f"Relevance to seed project: {similarity_score:.3f}")
        parts.append("")

        # --- Structure ---
        parts.append("# Repository structure")
        parts.append(f"Total files: {snapshot.get('file_count', 0)}")
        parts.append(f"Root files: {', '.join((snapshot.get('root_files') or [])[:25])}")
        parts.append(f"Root dirs: {', '.join(snapshot.get('root_dirs') or [])}")
        ext_pairs = list((snapshot.get("top_extensions") or {}).items())[:10]
        parts.append(f"Extensions: {', '.join(f'{k}({v})' for k,v in ext_pairs)}")
        parts.append("")

        # --- Static scan findings ---
        if heuristics:
            parts.append("# Static scan findings")
            for h in heuristics:
                parts.append(f"- {h}")
            parts.append("")

        # --- Contribution files ---
        contrib_files = (contribution_rules or {}).get("files", [])
        if contrib_files:
            parts.append(f"# Contribution files found: {', '.join(contrib_files)}")
            parts.append("")

        # --- Source code (the most important part) ---
        if source_samples:
            parts.append("# Source code samples")
            parts.append("(Use these to make specific, evidence-backed observations.)")
            parts.append("")
            shown = 0
            for rel, content in source_samples.items():
                if shown >= 8:
                    parts.append(f"... and {len(source_samples) - shown} more files")
                    break
                parts.append(f"## {rel}")
                parts.append(content[:3000])
                parts.append("")
                shown += 1

        parts.append("---")
        parts.append("Write the review. Be genuine. Be specific. Be brief.")
        return "\n".join(parts)

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        if not text:
            return None

        candidates = [text]

        # Try fenced block first
        fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if fence:
            candidates.append(fence.group(1))

        # Try first brace block
        brace = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if brace:
            candidates.append(brace.group(1))

        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _validate(obj: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure all expected keys exist with sane defaults."""
        obj.setdefault("summary", "")
        obj.setdefault("praise", [])
        obj.setdefault("concerns", [])
        obj.setdefault("improvements", [])
        obj.setdefault("issue_title", "")
        obj.setdefault("issue_body", "")
        obj.setdefault("conversation_hook", "")

        try:
            obj["confidence"] = max(0.0, min(1.0, float(obj.get("confidence", 0.7))))
        except (TypeError, ValueError):
            obj["confidence"] = 0.7

        for key in ("praise", "concerns", "improvements"):
            val = obj[key]
            if not isinstance(val, list):
                obj[key] = [str(val)] if val else []

        return obj
