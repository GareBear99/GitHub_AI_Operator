from __future__ import annotations

import json
import os
from typing import Dict, Optional

import requests

from .config import AIConfig


class AIReviewer:
    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg

    def review(self, payload: Dict) -> Optional[Dict]:
        if not self.cfg.enabled or not self.cfg.api_url:
            return None
        api_key = os.getenv(self.cfg.api_key_env)
        if not api_key:
            return None
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        body = {
            'model': self.cfg.model,
            'messages': [
                {'role': 'system', 'content': 'You are a precise open-source code review assistant. Return JSON only.'},
                {'role': 'user', 'content': json.dumps(payload)},
            ],
            'temperature': 0.2,
        }
        resp = requests.post(self.cfg.api_url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        msg = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
        return json.loads(msg)
