from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

from .config import Limits

SRC_EXTS = {'.py', '.js', '.ts', '.tsx', '.jsx', '.cpp', '.c', '.h', '.hpp', '.rs', '.go', '.java', '.kt', '.swift'}


def run_cmd(cmd: List[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False)


def clone_repo(clone_url: str, dest: Path, depth: int = 1) -> bool:
    result = run_cmd(['git', 'clone', '--depth', str(depth), clone_url, str(dest)])
    if result.returncode != 0:
        print(f'[clone-failed] {result.stderr.strip()}')
        return False
    return True


def safe_delete(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def read_text(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='ignore')[:max_chars]
    except Exception:
        return ''


def collect_snapshot(repo_dir: Path, limits: Limits) -> Dict:
    files = []
    total_size = 0
    ext_counts: Dict[str, int] = {}
    root_files = []
    source_samples: Dict[str, str] = {}
    scanned = 0

    for p in repo_dir.rglob('*'):
        if '.git' in p.parts:
            continue
        if p.is_file():
            rel = str(p.relative_to(repo_dir))
            files.append(rel)
            total_size += p.stat().st_size
            ext = p.suffix.lower() or '[no_ext]'
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if len(Path(rel).parts) == 1:
                root_files.append(rel)
            if ext in SRC_EXTS and scanned < limits.max_files_scanned:
                source_samples[rel] = read_text(p, limits.max_source_chars_per_file)
                scanned += 1

    return {
        'file_count': len(files),
        'total_size_bytes': total_size,
        'root_files': sorted(root_files)[:40],
        'top_extensions': dict(sorted(ext_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
        'source_samples': source_samples,
    }


def heuristic_findings(repo_dir: Path, snapshot: Dict) -> List[str]:
    findings: List[str] = []
    roots = set(x.lower() for x in snapshot.get('root_files', []))
    if not any(x in roots for x in {'readme.md', 'readme.txt'}):
        findings.append('No README found in the repository root.')
    if not any('license' in x for x in roots):
        findings.append('No obvious license file found in the repository root.')
    if not any(x.startswith('.github') or x.startswith('tests') or x.startswith('test') for x in snapshot.get('root_files', [])):
        findings.append('No obvious root-level tests or GitHub automation files were found.')

    line_len_hits = 0
    todo_hits = 0
    for rel, text in snapshot.get('source_samples', {}).items():
        for line in text.splitlines():
            if len(line) > 180:
                line_len_hits += 1
            if re.search(r'\bTODO\b|\bFIXME\b|\bHACK\b', line):
                todo_hits += 1
    if line_len_hits >= 10:
        findings.append('Multiple long source lines suggest readability or maintainability cleanup may be worthwhile.')
    if todo_hits >= 5:
        findings.append('Several TODO/FIXME/HACK markers were found; some may point to unfinished or fragile areas.')

    package_json = repo_dir / 'package.json'
    if package_json.exists():
        try:
            obj = json.loads(package_json.read_text(encoding='utf-8'))
            scripts = obj.get('scripts', {})
            if 'test' not in scripts:
                findings.append('package.json exists but no test script was found.')
        except Exception:
            findings.append('package.json exists but could not be parsed cleanly.')

    return findings
