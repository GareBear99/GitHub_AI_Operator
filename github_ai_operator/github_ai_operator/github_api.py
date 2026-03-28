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
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5


class GitHubClient:
    def __init__(self, token: Optional[str] = None) -> None:
        token = token or os.getenv('GITHUB_TOKEN')
        if not token:
            raise RuntimeError('GITHUB_TOKEN is required (set via environment variable)')
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.headers['Authorization'] = f'Bearer {token}'
        self.session.headers['User-Agent'] = 'github-ai-operator/1.3'

    def _request(self, method: str, url: str, allow_404: bool = False, **kwargs: Any) -> requests.Response:
        last_error: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)

                if allow_404 and resp.status_code == 404:
                    return resp

                if resp.status_code in (403, 429):
                    remaining = resp.headers.get('X-RateLimit-Remaining', '1')
                    reset = resp.headers.get('X-RateLimit-Reset')
                    if remaining == '0' and reset:
                        sleep_for = max(0, int(reset) - int(time.time())) + 2
                        print(f'[rate-limit] sleeping {min(sleep_for, 300):.0f}s (reset={reset})')
                        time.sleep(min(sleep_for, 300))
                        continue
                    retry_after = resp.headers.get('retry-after')
                    if retry_after and attempt < _MAX_RETRIES - 1:
                        time.sleep(min(int(retry_after), 60))
                        continue
                    resp.raise_for_status()

                if resp.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF * (attempt + 1)
                    print(f'[server-error] {resp.status_code} for {url}, retrying in {wait:.1f}s')
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError('Unexpected request failure after retries')

    def get_repo(self, full_name: str) -> RepoProfile:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}')
        return self._to_repo(r.json())

    def get_readme(self, full_name: str) -> str:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}/readme', allow_404=True)
        if r.status_code == 404:
            return ''
        data = r.json()
        encoded = data.get('content', '')
        if encoded:
            return base64.b64decode(encoded.replace('\n', '')).decode('utf-8', errors='ignore')
        return ''

    def get_contributing_rules(self, full_name: str) -> Dict[str, Any]:
        owner, repo = full_name.split('/', 1)
        candidates = [
            'CONTRIBUTING.md',
            '.github/CONTRIBUTING.md',
            '.github/ISSUE_TEMPLATE/bug_report.md',
            '.github/ISSUE_TEMPLATE/config.yml',
            '.github/pull_request_template.md',
        ]
        found: Dict[str, Any] = {'files': [], 'content': {}}
        for path in candidates:
            url = f'{GITHUB_API}/repos/{owner}/{repo}/contents/{path}'
            try:
                resp = self._request('GET', url, allow_404=True)
                if resp.status_code == 200:
                    body = resp.json()
                    raw = base64.b64decode(body.get('content', '').replace('\n', '')).decode('utf-8', errors='ignore')
                    found['files'].append(path)
                    found['content'][path] = raw[:20000]
            except requests.RequestException as exc:
                print(f'[contrib-fetch-warn] {path}: {exc}')
        return found

    def search_repositories(self, query: str, per_page: int = 10, page: int = 1, sort: str = 'updated', order: str = 'desc') -> List[RepoProfile]:
        r = self._request(
            'GET',
            f'{GITHUB_API}/search/repositories',
            params={'q': query, 'per_page': per_page, 'page': page, 'sort': sort, 'order': order},
        )
        return [self._to_repo(item) for item in r.json().get('items', [])]

    def list_issues(self, full_name: str, state: str = 'all', per_page: int = 100, max_pages: int = 3) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            r = self._request(
                'GET',
                f'{GITHUB_API}/repos/{full_name}/issues',
                params={'state': state, 'per_page': per_page, 'page': page},
            )
            page_items = r.json()
            if not page_items:
                break
            items.extend(page_items)
            if len(page_items) < per_page:
                break
        return items

    def list_labels(self, full_name: str, per_page: int = 100) -> List[str]:
        r = self._request('GET', f'{GITHUB_API}/repos/{full_name}/labels', params={'per_page': per_page}, allow_404=True)
        if r.status_code == 404:
            return []
        return [str(item.get('name', '')) for item in r.json()]

    def create_issue(self, full_name: str, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {'title': title, 'body': body}
        use_labels = labels or []
        if use_labels:
            try:
                existing = set(self.list_labels(full_name))
                filtered = [lbl for lbl in use_labels if lbl in existing]
                if filtered:
                    payload['labels'] = filtered
                else:
                    print(f'[labels] none of {use_labels} exist on {full_name}; posting without labels')
            except requests.RequestException as exc:
                print(f'[labels-warn] could not fetch labels for {full_name}: {exc}; posting without labels')
        r = self._request('POST', f'{GITHUB_API}/repos/{full_name}/issues', json=payload)
        return r.json()

    @staticmethod
    def _to_repo(item: Dict[str, Any]) -> RepoProfile:
        license_name: Optional[str] = None
        if item.get('license') and isinstance(item['license'], dict):
            license_name = item['license'].get('name')
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
            has_wiki=bool(item.get('has_wiki')),
            license_name=license_name,
        )
