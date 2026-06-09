"""Smart context management to minimize token usage.

Instead of dumping the entire codebase into context, we:
1. Fingerprint files (hash-based change detection)
2. Selectively read only relevant files (by path allowlist + grep)
3. Compress large files via AST/structure extraction
4. Cache summaries across runs
5. Use t0 models for fast file operations

Design rule: never read what you don't need. Never re-read what hasn't changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# File fingerprinting
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git", ".agentops", ".agent-worktrees", "node_modules", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", ".output", "coverage", ".nyc_output", "htmlcov",
    ".eggs", "*.egg-info",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib", ".dll", ".exe",
    ".wasm", ".class", ".jar", ".war",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".bmp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".pptx",
    ".db", ".sqlite", ".sqlite3",
    ".lock",  # package locks are huge and rarely useful for agents
}

# Max bytes to read from a single file for context
MAX_FILE_BYTES = 32_000
# Max bytes for the compressed summary of a large file
MAX_SUMMARY_BYTES = 4_000


@dataclass
class FileFingerprint:
    """Lightweight file metadata for change detection."""
    path: str
    size: int
    mtime: float
    content_hash: str  # first-8 of sha256
    language: str
    line_count: int


@dataclass
class ContextManifest:
    """Tracks what files are in scope and their fingerprints."""
    root: Path
    fingerprints: dict[str, FileFingerprint] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    generated_at: str = ""

    def save(self, path: Path) -> None:
        data = {
            "root": str(self.root),
            "generated_at": self.generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fingerprints": {
                k: {
                    "path": v.path, "size": v.size, "mtime": v.mtime,
                    "content_hash": v.content_hash, "language": v.language,
                    "line_count": v.line_count,
                }
                for k, v in self.fingerprints.items()
            },
            "summaries": self.summaries,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ContextManifest:
        if not path.exists():
            return cls(root=Path.cwd())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls(root=Path(data.get("root", ".")))
            manifest.generated_at = data.get("generated_at", "")
            for k, v in data.get("fingerprints", {}).items():
                manifest.fingerprints[k] = FileFingerprint(**v)
            manifest.summaries = data.get("summaries", {})
            return manifest
        except Exception:
            return cls(root=Path.cwd())


def _should_skip(path: Path, root: Path) -> bool:
    """Check if a file should be skipped for context."""
    rel = path.relative_to(root)
    parts = rel.parts
    for part in parts:
        if part in SKIP_DIRS or part.endswith(".egg-info"):
            return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    if path.name.startswith(".") and path.name not in (".env.example", ".gitignore", ".dockerignore"):
        return True
    return False


def _detect_language(path: Path) -> str:
    """Simple language detection by extension."""
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "jsx",
        ".tsx": "tsx", ".vue": "vue", ".html": "html", ".css": "css", ".scss": "scss",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".md": "markdown", ".rst": "rst", ".txt": "text",
        ".sh": "bash", ".bash": "bash", ".zsh": "zsh",
        ".sql": "sql", ".graphql": "graphql",
        ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".h": "c-header",
        ".dockerfile": "dockerfile",
    }
    if path.name.lower() in ("dockerfile", "makefile", "justfile", "procfile"):
        return path.name.lower()
    if path.name.lower().startswith("docker-compose"):
        return "docker-compose"
    return ext_map.get(path.suffix.lower(), "unknown")


def _content_hash(path: Path) -> str:
    """Fast hash of file content (first 8 chars of sha256)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            # Read in chunks to handle large files
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()[:8]
    except Exception:
        return "00000000"


def fingerprint_file(path: Path, root: Path) -> FileFingerprint:
    """Create a fingerprint for a single file."""
    try:
        stat = path.stat()
        line_count = 0
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            pass
        return FileFingerprint(
            path=str(path.relative_to(root)),
            size=stat.st_size,
            mtime=stat.st_mtime,
            content_hash=_content_hash(path),
            language=_detect_language(path),
            line_count=line_count,
        )
    except Exception:
        return FileFingerprint(str(path), 0, 0.0, "00000000", "unknown", 0)


def scan_project(root: Path, max_files: int = 5000) -> ContextManifest:
    """Scan a project directory and create fingerprints for all relevant files."""
    manifest = ContextManifest(root=root)
    manifest.generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip(path, root):
            continue
        if count >= max_files:
            break
        fp = fingerprint_file(path, root)
        manifest.fingerprints[fp.path] = fp
        count += 1
    return manifest


def changed_files(old: ContextManifest, new: ContextManifest) -> list[str]:
    """Find files that changed between two manifests."""
    changed = []
    for path, new_fp in new.fingerprints.items():
        old_fp = old.fingerprints.get(path)
        if not old_fp:
            changed.append(path)  # new file
        elif old_fp.content_hash != new_fp.content_hash:
            changed.append(path)  # modified
    return changed


def deleted_files(old: ContextManifest, new: ContextManifest) -> list[str]:
    """Find files that were deleted between two manifests."""
    return [p for p in old.fingerprints if p not in new.fingerprints]


# ---------------------------------------------------------------------------
# Selective file reading
# ---------------------------------------------------------------------------

def read_file_smart(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Read a file, truncating if too large with a clear indicator."""
    if not path.exists():
        return f"[file not found: {path}]"
    try:
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        # Read head and tail for large files
        head_bytes = max_bytes * 2 // 3
        tail_bytes = max_bytes // 3
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(head_bytes)
            f.seek(max(0, size - tail_bytes))
            tail = f.read(tail_bytes)
        return f"{head}\n\n[... {size - head_bytes - tail_bytes} bytes truncated ...]\n\n{tail}"
    except Exception as exc:
        return f"[read error: {exc}]"


def grep_files(root: Path, pattern: str, file_extensions: list[str] | None = None, max_results: int = 50) -> list[dict[str, Any]]:
    """Fast grep across project files. Returns matches with context."""
    results = []
    try:
        cmd = ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
               "--include=*.vue", "--include=*.html", "--include=*.md",
               "--include=*.yaml", "--include=*.yml", "--include=*.json",
               "--include=*.toml", "--include=*.sql", "--include=*.sh",
               "-l", pattern, str(root)]
        if file_extensions:
            cmd = ["grep", "-rn"]
            for ext in file_extensions:
                cmd.extend([f"--include=*{ext}"])
            cmd.extend(["-l", pattern, str(root)])

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            for line in p.stdout.strip().splitlines()[:max_results]:
                file_path = line.strip()
                if file_path:
                    try:
                        rel = str(Path(file_path).relative_to(root))
                    except ValueError:
                        rel = file_path
                    results.append({"file": rel, "match": True})
    except Exception:
        pass
    return results


def find_relevant_files(
    root: Path,
    task_paths: list[str],
    task_keywords: list[str] | None = None,
    manifest: ContextManifest | None = None,
    max_files: int = 30,
) -> list[str]:
    """Determine which files are relevant for a task.
    
    Strategy:
    1. Start with explicitly listed paths
    2. Add files matching keywords via grep
    3. Add test files corresponding to source files
    4. Limit total count
    """
    relevant: set[str] = set()

    # 1. Explicit paths
    for p in task_paths:
        if "*" in p:
            # Glob pattern
            for match in root.glob(p):
                if match.is_file() and not _should_skip(match, root):
                    relevant.add(str(match.relative_to(root)))
        elif (root / p).is_file():
            relevant.add(p)
        elif (root / p).is_dir():
            for f in (root / p).rglob("*"):
                if f.is_file() and not _should_skip(f, root):
                    relevant.add(str(f.relative_to(root)))
                    if len(relevant) >= max_files:
                        break

    # 2. Keyword grep
    if task_keywords:
        for kw in task_keywords[:5]:  # limit grep calls
            matches = grep_files(root, kw, max_results=10)
            for m in matches:
                relevant.add(m["file"])

    # 3. Test file inference
    test_files = set()
    for f in list(relevant):
        p = Path(f)
        if p.suffix == ".py" and not p.name.startswith("test_"):
            test_candidate = p.parent / f"test_{p.name}"
            if (root / test_candidate).exists():
                test_files.add(str(test_candidate))
            # Also check tests/ directory
            test_dir = root / "tests" / f"test_{p.name}"
            if test_dir.exists():
                test_files.add(str(test_dir.relative_to(root)))
    relevant.update(test_files)

    return sorted(relevant)[:max_files]


# ---------------------------------------------------------------------------
# Context compression
# ---------------------------------------------------------------------------

def compress_file_for_context(path: Path, root: Path) -> str:
    """Create a compressed representation of a file for context.
    
    For code files: extract imports, class/function signatures, docstrings.
    For config files: extract key structure.
    For markdown: extract headers and first paragraph.
    """
    content = read_file_smart(path, MAX_FILE_BYTES)
    language = _detect_language(path)
    rel = str(path.relative_to(root)) if root else str(path)

    if language == "python":
        return _compress_python(rel, content)
    elif language in ("javascript", "typescript", "jsx", "tsx"):
        return _compress_js(rel, content)
    elif language in ("json", "yaml", "toml"):
        return _compress_config(rel, content)
    elif language == "markdown":
        return _compress_markdown(rel, content)
    elif language == "vue":
        return _compress_vue(rel, content)
    else:
        # Generic: first N lines
        lines = content.splitlines()[:60]
        return f"### {rel}\n```\n" + "\n".join(lines) + "\n```\n"


def _compress_python(rel: str, content: str) -> str:
    """Extract Python file structure: imports, classes, functions, docstrings."""
    lines = content.splitlines()
    output = []
    output.append(f"### {rel}")

    # Imports
    imports = [l for l in lines if l.startswith(("import ", "from "))]
    if imports:
        output.append("Imports: " + ", ".join(i.strip() for i in imports[:15]))

    # Function/class signatures with docstrings
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("def ", "class ", "async def ")):
            output.append(f"  {line.rstrip()}")
            # Check for docstring
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line.startswith(('"""', "'''")):
                    # Single line docstring
                    if next_line.endswith(('"""', "'''")):
                        output.append(f"    {next_line}")
                    else:
                        output.append(f"    {next_line}...")

    return "\n".join(output) + "\n"


def _compress_js(rel: str, content: str) -> str:
    """Extract JS/TS structure: imports, exports, function signatures."""
    lines = content.splitlines()
    output = [f"### {rel}"]

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("import ", "export ", "const ", "function ", "class ", "interface ", "type ", "enum ")):
            if len(stripped) < 200:
                output.append(f"  {stripped[:120]}")

    return "\n".join(output) + "\n"


def _compress_config(rel: str, content: str) -> str:
    """Extract config structure: top-level keys."""
    lines = content.splitlines()[:40]
    return f"### {rel}\n```\n" + "\n".join(lines) + "\n```\n"


def _compress_markdown(rel: str, content: str) -> str:
    """Extract markdown structure: headers and first paragraphs."""
    lines = content.splitlines()
    output = [f"### {rel}"]
    for line in lines:
        if line.startswith("#"):
            output.append(f"  {line.strip()}")
    return "\n".join(output) + "\n"


def _compress_vue(rel: str, content: str) -> str:
    """Extract Vue SFC structure: template outline, script exports, style scoping."""
    output = [f"### {rel}"]
    # Extract section markers
    for section in re.findall(r"<(template|script|style)[^>]*>", content):
        output.append(f"  <{section}>")
    # Extract key bindings
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("export default", "defineComponent", "defineProps", "defineEmits", "const ")):
            output.append(f"  {stripped[:120]}")
    return "\n".join(output) + "\n"


# ---------------------------------------------------------------------------
# Build context bundle for a task
# ---------------------------------------------------------------------------

def build_task_context(
    root: Path,
    task_paths: list[str],
    task_keywords: list[str] | None = None,
    max_total_tokens: int = 30_000,
    include_full: bool = False,
) -> str:
    """Build a compact context string for a task.
    
    Returns a string with file contents/summaries that fits within token budget.
    """
    from .models import estimate_tokens

    manifest = scan_project(root)
    relevant = find_relevant_files(root, task_paths, task_keywords, manifest)

    parts = []
    total_tokens = 0

    # First pass: include small files in full
    for rel_path in relevant:
        full_path = root / rel_path
        if not full_path.exists():
            continue

        fp = manifest.fingerprints.get(rel_path)
        if not fp:
            continue

        if include_full or fp.size < 3000:
            content = read_file_smart(full_path, MAX_FILE_BYTES)
            entry = f"### {rel_path}\n```{fp.language}\n{content}\n```\n"
        else:
            entry = compress_file_for_context(full_path, root)

        entry_tokens = estimate_tokens(entry)
        if total_tokens + entry_tokens > max_total_tokens:
            # Budget exceeded — compress remaining files
            entry = compress_file_for_context(full_path, root)
            entry_tokens = estimate_tokens(entry)
            if total_tokens + entry_tokens > max_total_tokens:
                parts.append(f"### {rel_path} [skipped: token budget]\n")
                continue

        parts.append(entry)
        total_tokens += entry_tokens

    header = f"## Project context ({len(relevant)} files, ~{total_tokens} tokens)\n\n"
    return header + "\n".join(parts)


# ---------------------------------------------------------------------------
# Git-aware diff context
# ---------------------------------------------------------------------------

def git_diff_context(root: Path, base_branch: str = "main", max_bytes: int = 20_000) -> str:
    """Get a compact diff summary relative to base branch."""
    try:
        # Files changed
        p = subprocess.run(
            ["git", "diff", "--name-status", base_branch],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        if p.returncode != 0:
            return "[no git diff available]"

        file_changes = p.stdout.strip()

        # Stat summary
        p2 = subprocess.run(
            ["git", "diff", "--stat", base_branch],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        stat = p2.stdout.strip() if p2.returncode == 0 else ""

        # Actual diff (truncated)
        p3 = subprocess.run(
            ["git", "diff", base_branch],
            cwd=str(root), capture_output=True, text=True, timeout=10,
        )
        diff = p3.stdout[:max_bytes] if p3.returncode == 0 else ""

        return f"## Changed files\n```\n{file_changes}\n```\n\n## Stats\n```\n{stat}\n```\n\n## Diff\n```diff\n{diff}\n```\n"
    except Exception as exc:
        return f"[git diff error: {exc}]"


# ---------------------------------------------------------------------------
# Project tree (compact)
# ---------------------------------------------------------------------------

def project_tree(root: Path, max_depth: int = 3, max_entries: int = 200) -> str:
    """Generate a compact project tree for context."""
    lines = []
    count = 0

    def _walk(path: Path, depth: int, prefix: str = ""):
        nonlocal count
        if depth > max_depth or count > max_entries:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            if entry.is_dir():
                lines.append(f"{prefix}{entry.name}/")
                count += 1
                _walk(entry, depth + 1, prefix + "  ")
            elif entry.suffix.lower() not in SKIP_EXTENSIONS:
                size = entry.stat().st_size
                size_str = f"{size}" if size < 1024 else f"{size // 1024}K"
                lines.append(f"{prefix}{entry.name} ({size_str})")
                count += 1

    _walk(root, 0)
    return "\n".join(lines)
