from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Set

from .config import Limits

SRC_EXTS: Set[str] = {
    '.py', '.js', '.ts', '.tsx', '.jsx',
    '.cpp', '.c', '.h', '.hpp', '.cc', '.cxx',
    '.rs', '.go', '.java', '.kt', '.swift',
    '.rb', '.php', '.cs', '.m', '.mm',
}

IGNORED_DIRS: Set[str] = {
    '.git', '.hg', '.svn',
    '.venv', 'venv', 'env', '.env',
    '__pycache__', '.mypy_cache', '.pytest_cache', '.tox',
    'node_modules', '.pnp',
    'dist', 'build', 'out', 'output', 'coverage',
    '.next', '.nuxt', '.cache', '.parcel-cache',
    'vendor', 'third_party', 'deps', 'packages',
    'target', 'bin', 'obj', 'lib',
    '.idea', '.vscode', '.eclipse',
    '__MACOSX',
}

SYMBOL_PATTERNS = [
    re.compile(r'^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', re.MULTILINE),
    re.compile(r'^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE),
    re.compile(r'function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', re.MULTILINE),
    re.compile(r'(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(', re.MULTILINE),
]


def run_cmd(cmd: List[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, check=False)


def clone_repo(clone_url: str, dest: Path, depth: int = 1) -> bool:
    result = run_cmd(['git', 'clone', '--depth', str(depth), clone_url, str(dest)])
    if result.returncode != 0:
        print(f'[clone-failed] {result.stderr.strip()[:300]}')
        return False
    return True


def safe_delete(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def read_text(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='ignore')[:max_chars]
    except Exception:
        return ''


def _is_ignored(path: Path, repo_dir: Path) -> bool:
    try:
        parts = path.relative_to(repo_dir).parts
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in parts)


def _extract_symbols(text: str) -> List[str]:
    out: List[str] = []
    for pattern in SYMBOL_PATTERNS:
        out.extend(pattern.findall(text))
    seen = []
    for symbol in out:
        if symbol not in seen:
            seen.append(symbol)
    return seen[:12]


def collect_snapshot(repo_dir: Path, limits: Limits) -> Dict:
    files: List[str] = []
    total_size = 0
    ext_counts: Dict[str, int] = {}
    root_files: List[str] = []
    root_dirs: set[str] = set()
    source_samples: Dict[str, str] = {}
    symbol_samples: Dict[str, List[str]] = {}
    scanned = 0

    for p in repo_dir.rglob('*'):
        if _is_ignored(p, repo_dir):
            continue
        try:
            rel = str(p.relative_to(repo_dir))
        except ValueError:
            continue

        rel_parts = Path(rel).parts
        if p.is_dir():
            if len(rel_parts) == 1:
                root_dirs.add(rel)
            continue
        if not p.is_file():
            continue

        files.append(rel)
        try:
            total_size += p.stat().st_size
        except OSError:
            pass

        ext = p.suffix.lower() or '[no_ext]'
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        if len(rel_parts) == 1:
            root_files.append(rel)

        if ext in SRC_EXTS and scanned < limits.max_files_scanned:
            text = read_text(p, limits.max_source_chars_per_file)
            source_samples[rel] = text
            symbol_samples[rel] = _extract_symbols(text)
            scanned += 1

    return {
        'file_count': len(files),
        'total_size_bytes': total_size,
        'root_files': sorted(root_files)[:80],
        'root_dirs': sorted(root_dirs),
        'all_paths_sample': sorted(files)[:300],
        'top_extensions': dict(sorted(ext_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
        'source_samples': source_samples,
        'source_file_sample_count': len(source_samples),
        'symbol_samples': symbol_samples,
    }


def heuristic_findings(repo_dir: Path, snapshot: Dict) -> List[str]:
    findings: List[str] = []
    roots_lower: set[str] = {x.lower() for x in snapshot.get('root_files', [])}
    root_dirs_lower: set[str] = {x.lower() for x in snapshot.get('root_dirs', [])}
    all_paths_lower: List[str] = [x.lower() for x in snapshot.get('all_paths_sample', [])]

    readme_names = {'readme.md', 'readme.txt', 'readme.rst', 'readme'}
    if not roots_lower.intersection(readme_names):
        findings.append('No README found in the repository root (checked readme.md, readme.txt, readme.rst).')
    if not any('license' in x for x in roots_lower):
        findings.append('No license file detected in the repository root.')

    test_signal = (
        'tests' in root_dirs_lower or 'test' in root_dirs_lower or '.github' in root_dirs_lower
        or any(p.startswith('.github/') or '/tests/' in p or '/test/' in p or p.startswith('tests/') or p.startswith('test/') for p in all_paths_lower)
    )
    if not test_signal:
        findings.append('No tests directory or GitHub Actions/workflow files (.github/) were detected in the sampled paths.')

    long_line_hits = 0
    long_line_files: set[str] = set()
    todo_hits = 0
    todo_files: set[str] = set()
    empty_except_hits = 0
    empty_except_files: set[str] = set()

    symbol_samples = snapshot.get('symbol_samples', {})
    sparse_symbol_files = [rel for rel, symbols in symbol_samples.items() if not symbols][:3]

    for rel, text in snapshot.get('source_samples', {}).items():
        lines = text.splitlines()
        for line in lines:
            if len(line) > 180:
                long_line_hits += 1
                long_line_files.add(rel)
            if re.search(r'TODO|FIXME|HACK|XXX', line):
                todo_hits += 1
                todo_files.add(rel)
        if rel.endswith('.py') and re.search(r'except\s*:', text):
            empty_except_hits += 1
            empty_except_files.add(rel)

    if long_line_hits >= 10:
        sample = ', '.join(sorted(long_line_files)[:3])
        findings.append(f'{long_line_hits} source lines exceed 180 characters (e.g. {sample}), which may affect readability.')
    if todo_hits >= 5:
        sample = ', '.join(sorted(todo_files)[:4])
        findings.append(f'{todo_hits} TODO/FIXME/HACK/XXX markers were found across files including: {sample}.')
    if empty_except_hits >= 3:
        sample = ', '.join(sorted(empty_except_files)[:3])
        findings.append(f'Bare `except:` clauses found in {empty_except_hits} Python files (e.g. {sample}), which silently suppress all exceptions.')
    if sparse_symbol_files and snapshot.get('source_file_sample_count', 0) >= 5:
        findings.append(f'Several sampled source files expose few recognizable function/class symbols (e.g. {", ".join(sparse_symbol_files)}), which can make navigation and review harder.')

    package_json = repo_dir / 'package.json'
    if package_json.exists():
        try:
            obj = json.loads(package_json.read_text(encoding='utf-8', errors='ignore'))
            scripts = obj.get('scripts', {})
            if 'test' not in scripts:
                findings.append('package.json is present but no "test" script is defined under scripts.')
            if obj.get('version', '').startswith('0.0'):
                findings.append(f'package.json version is {obj["version"]!r}, suggesting early/pre-release status.')
        except Exception:
            findings.append('package.json is present but could not be parsed (likely malformed JSON).')

    pyproject = repo_dir / 'pyproject.toml'
    setup_py = repo_dir / 'setup.py'
    is_python_project = pyproject.exists() or setup_py.exists()
    if is_python_project and not any('/tests/' in p or p.startswith('tests/') or '/test/' in p or p.startswith('test/') for p in all_paths_lower):
        findings.append('Python project configuration exists (pyproject.toml or setup.py) but no tests/ directory was found.')

    if snapshot.get('file_count', 0) <= 3:
        findings.append('The repository contains very few files; verify that the default branch has the expected project content.')

    return findings
