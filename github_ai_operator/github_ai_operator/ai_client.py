from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

from .config import AIConfig

_REQUIRED_KEYS = {'summary', 'praise', 'concerns', 'improvements', 'issue_title', 'confidence'}


class AIReviewer:
    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg

    def review(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Call the AI model and return a validated dict, or None if disabled/unavailable.

        Retries up to cfg.max_retries times on network or parse failure.
        Raises only on the final failed attempt so callers can fallback.
        """
        if not self.cfg.enabled or not self.cfg.api_url:
            return None
        api_key = os.getenv(self.cfg.api_key_env)
        if not api_key:
            print(f'[ai-warn] env var {self.cfg.api_key_env!r} not set; skipping AI review')
            return None

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        system_prompt = (
            'You are a precise open-source code review assistant.\n'
            'Return ONLY a JSON object — no markdown fences, no extra text.\n'
            'Required keys: summary (str), praise (list[str]), concerns (list[str]), '
            'improvements (list[str]), issue_title (str), issue_body (str), confidence (float 0-1).\n'
            'Be evidence-first: only include concerns that are clearly supported by the data provided.\n'
            'confidence should reflect how certain you are that the concerns are real and actionable.'
        )
        body: Dict[str, Any] = {
            'model': self.cfg.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': json.dumps(payload, default=str)},
            ],
            'temperature': 0.2,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                resp = requests.post(
                    self.cfg.api_url,
                    headers=headers,
                    json=body,
                    timeout=self.cfg.timeout_seconds,
                )
                resp.raise_for_status()
                choices: List[Dict] = resp.json().get('choices', [])
                if not choices:
                    raise ValueError('AI response contained no choices.')
                raw_content: str = choices[0].get('message', {}).get('content', '')
                parsed = self._extract_json(raw_content)
                if parsed is None:
                    raise ValueError(f'Could not extract JSON from AI response: {raw_content[:200]!r}')
                validated = self._validate(parsed)
                return validated
            except Exception as exc:
                last_error = exc
                if attempt < self.cfg.max_retries:
                    wait = 1.5 * (attempt + 1)
                    print(f'[ai-retry] attempt {attempt + 1} failed ({exc}); retrying in {wait:.1f}s')
                    time.sleep(wait)
                    continue
                break

        if last_error:
            raise last_error
        return None

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """Try several strategies to extract a JSON dict from model output."""
        text = text.strip()
        if not text:
            return None

        candidates: List[str] = [text]

        # Fenced code block
        fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, flags=re.DOTALL)
        if fence:
            candidates.append(fence.group(1))

        # First top-level brace block
        brace = re.search(r'(\{.*\})', text, flags=re.DOTALL)
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
        """Fill in missing keys with safe defaults so downstream code never KeyErrors."""
        obj.setdefault('summary', '')
        obj.setdefault('praise', [])
        obj.setdefault('concerns', [])
        obj.setdefault('improvements', [])
        obj.setdefault('issue_title', '')
        obj.setdefault('issue_body', '')
        raw_conf = obj.get('confidence', 0.7)
        try:
            obj['confidence'] = max(0.0, min(1.0, float(raw_conf)))
        except (TypeError, ValueError):
            obj['confidence'] = 0.7
        # Coerce list fields to actual lists
        for key in ('praise', 'concerns', 'improvements'):
            if not isinstance(obj[key], list):
                obj[key] = [str(obj[key])] if obj[key] else []
        return obj
