from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, List, Optional

import requests

from .models import RepoProfile

GITHUB_API = 'https://api.github.com'
HEADERS = {
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}


class GitHubClient:
    def __init__(self, token: Optional[str] = None) -> None:
        token = token or os.getenv('GITHUB_TOKEN')
        if not token:
            raise RuntimeError('GITHUB_TOKEN is required')
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.headers['Authorization'] = f'Bearer {token}'
        self.session.headers['User-Agent'] = 'github-ai-operator/1.0'

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        resp = self.session.request(method, url, timeout=60, **kwargs)
        if resp.status_code in (403, 429):
            remaining = resp.headers.get('X-RateLimit-Remaining')
            reset = resp.headers.get('X-RateLimit-Reset')
            if remaining == '0' and reset:
                sleep_for = max(0, int(reset) - int(time.time())) + 2
                print(f'[rate-limit] sleeping {sleep_for}s')
                time.sleep(min(sleep_for, 300))
                resp = self.session.request(method, url, timeout=60, **kwargs)
        resp.raise_for_status()
        return resp

    def get_repo(self, full_name: str) -> RepoProfile:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}')
        data = r.json()
        return self._to_repo(data)

    def get_readme(self, full_name: str) -> str:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}/readme')
        data = r.json()
        encoded = data.get('content', '')
        if encoded:
            return base64.b64decode(encoded).decode('utf-8', errors='ignore')
        return ''

    def get_contributing_rules(self, full_name: str) -> Dict[str, Any]:
        owner, repo = full_name.split('/', 1)
        candidates = [
            'CONTRIBUTING.md', '.github/CONTRIBUTING.md',
            '.github/ISSUE_TEMPLATE/bug_report.md',
            '.github/ISSUE_TEMPLATE/config.yml',
        ]
        found: Dict[str, Any] = {'files': [], 'content': {}}
        for path in candidates:
            url = f'{GITHUB_API}/repos/{owner}/{repo}/contents/{path}'
            resp = self.session.get(url, timeout=60)
            if resp.status_code == 200:
                body = resp.json()
                content = base64.b64decode(body.get('content', '')).decode('utf-8', errors='ignore')
                found['files'].append(path)
                found['content'][path] = content[:20000]
        return found

    def search_repositories(self, query: str, per_page: int = 10, page: int = 1, sort: str = 'updated', order: str = 'desc') -> List[RepoProfile]:
        r = self._request('GET', f'{GITHUB_API}/search/repositories', params={
            'q': query,
            'per_page': per_page,
            'page': page,
            'sort': sort,
            'order': order,
        })
        return [self._to_repo(item) for item in r.json().get('items', [])]

    def list_issues(self, full_name: str, state: str = 'open', per_page: int = 20) -> List[Dict[str, Any]]:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}/issues', params={'state': state, 'per_page': per_page})
        return r.json()

    def create_issue(self, full_name: str, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {'title': title, 'body': body}
        if labels:
            payload['labels'] = labels
        r = self._request('POST', f'{GITHUB_API}/repos/{full_name}/issues', json=payload)
        return r.json()

    @staticmethod
    def _to_repo(item: Dict[str, Any]) -> RepoProfile:
        return RepoProfile(
            full_name=item['full_name'],
            html_url=item['html_url'],
            clone_url=item['clone_url'],
            description=item.get('description') or '',
            language=item.get('language'),
            stars=item.get('stargazers_count', 0),
            topics=item.get('topics', []),
            default_branch=item.get('default_branch'),
            archived=bool(item.get('archived')),
            disabled=bool(item.get('disabled')),
            fork=bool(item.get('fork')),
            open_issues_count=int(item.get('open_issues_count', 0)),
        )
