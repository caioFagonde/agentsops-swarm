"""AgentOps Swarm v4 — contract and unit tests."""
from pathlib import Path
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "agentops_swarm"
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}


def run_module(*args):
    """Run CLI as a module (python -m agentops_swarm.cli)."""
    return subprocess.run(
        [sys.executable, "-m", "agentops_swarm.cli", *args],
        cwd=ROOT, text=True, capture_output=True, env=ENV, timeout=30
    )


def run_py(code: str):
    """Run a snippet with PYTHONPATH set."""
    return subprocess.run(
        [sys.executable, "-c", code],
        env=ENV, text=True, capture_output=True, timeout=10
    )


# ── CLI contract tests ───────────────────────────────────────

def test_help():
    p = run_module("--help")
    assert p.returncode == 0, f"stderr: {p.stderr}"
    assert "AgentOps" in p.stdout or "agentops" in p.stdout.lower()


def test_version():
    p = run_module("--version")
    assert p.returncode == 0, f"stderr: {p.stderr}"
    assert "4." in p.stdout


# ── Template existence tests ─────────────────────────────────

def test_templates_roles_exist():
    roles_dir = ROOT / "templates" / "roles"
    assert roles_dir.exists(), "templates/roles/ directory missing"
    required = [
        "opus-planner.md", "sonnet-executor.md", "sonnet-repair.md",
        "haiku-scout.md", "flash-scout.md", "local-scout.md",
        "local-summarizer.md", "codex-verifier.md", "course-author.md",
        "antigravity-executor.md",
    ]
    for name in required:
        assert (roles_dir / name).exists(), f"Missing role template: {name}"
        content = (roles_dir / name).read_text()
        assert len(content) > 50, f"Role template {name} is too short"


def test_templates_frameworks_exist():
    fw_dir = ROOT / "templates" / "frameworks"
    assert fw_dir.exists(), "templates/frameworks/ directory missing"
    required = [
        "generic.md", "python.md", "fastapi.md", "vue-quasar.md",
        "react.md", "docker-compose.md", "ci.md", "docs.md", "qa.md",
    ]
    for name in required:
        assert (fw_dir / name).exists(), f"Missing framework template: {name}"
        content = (fw_dir / name).read_text()
        assert len(content) > 100, f"Framework template {name} is too short"


def test_templates_course_exist():
    course_dir = ROOT / "templates" / "course"
    assert course_dir.exists(), "templates/course/ directory missing"
    required = ["slide-template.md", "guide-template.md", "index-template.html"]
    for name in required:
        assert (course_dir / name).exists(), f"Missing course template: {name}"


# ── Module import tests ──────────────────────────────────────

def test_import_models():
    r = run_py("from agentops_swarm import models; print('ok')")
    assert r.returncode == 0, f"Import failed: {r.stderr}"
    assert "ok" in r.stdout


def test_import_context():
    r = run_py("from agentops_swarm import context; print('ok')")
    assert r.returncode == 0, f"Import failed: {r.stderr}"
    assert "ok" in r.stdout


def test_import_course():
    r = run_py("from agentops_swarm import course; print('ok')")
    assert r.returncode == 0, f"Import failed: {r.stderr}"
    assert "ok" in r.stdout


def test_import_budget():
    r = run_py("from agentops_swarm import budget; print('ok')")
    assert r.returncode == 0, f"Import failed: {r.stderr}"
    assert "ok" in r.stdout


# ── models.py unit tests ─────────────────────────────────────

def test_model_tiers():
    r = run_py("""
from agentops_swarm.models import TIERS
assert 't0-local' in TIERS
assert 't1-fast' in TIERS
assert 't2-mid' in TIERS
assert 't3-heavy' in TIERS
print('ok')
""")
    assert r.returncode == 0, f"Tier test failed: {r.stderr}"


def test_estimate_tokens():
    r = run_py("""
from agentops_swarm.models import estimate_tokens
n = estimate_tokens("hello world this is a test")
assert 4 < n < 20, f"Unexpected token count: {n}"
print('ok')
""")
    assert r.returncode == 0, f"Token estimation failed: {r.stderr}"


def test_default_profiles():
    r = run_py("""
from agentops_swarm.models import DEFAULT_PROFILES_V4
assert 'opus-planner' in DEFAULT_PROFILES_V4
assert 'sonnet-executor' in DEFAULT_PROFILES_V4
assert 'antigravity-executor' in DEFAULT_PROFILES_V4
assert DEFAULT_PROFILES_V4['antigravity-executor']['engine'] == 'antigravity'
assert DEFAULT_PROFILES_V4['opus-planner']['tier'] == 't3-heavy'
assert DEFAULT_PROFILES_V4['sonnet-executor']['tier'] == 't2-mid'
print('ok')
""")
    assert r.returncode == 0, f"Profile test failed: {r.stderr}"




def test_complete_flow_help():
    p = run_module("complete-flow", "--help")
    assert p.returncode == 0, f"stderr: {p.stderr}"
    assert "Guided" in p.stdout or "guided" in p.stdout.lower()
    assert "--offline-plan" in p.stdout


def test_antigravity_provider_contract():
    r = run_py("""
from agentops_swarm.models import PROVIDERS, TIERS, DEFAULT_PROFILES_V4
assert 'antigravity' in PROVIDERS
assert PROVIDERS['antigravity'].engine == 'antigravity'
assert any(p.engine == 'antigravity' for p in TIERS['t2-mid'].providers)
assert DEFAULT_PROFILES_V4['antigravity-executor']['tier'] == 't2-mid'
print('ok')
""")
    assert r.returncode == 0, f"Antigravity provider failed: {r.stderr}"


# ── context.py unit tests ────────────────────────────────────

def test_context_scan_project():
    r = run_py("""
import tempfile, os
from pathlib import Path
from agentops_swarm.context import scan_project
with tempfile.TemporaryDirectory() as td:
    for name in ['a.py', 'b.js', 'c.md']:
        Path(td, name).write_text('content')
    manifest = scan_project(Path(td))
    assert len(manifest.fingerprints) == 3, f"Expected 3 files, got {len(manifest.fingerprints)}"
    print('ok')
""")
    assert r.returncode == 0, f"scan_project failed: {r.stderr}"


def test_context_compress_python():
    r = run_py("""
from agentops_swarm.context import _compress_python
code = '''
import os
from pathlib import Path

class MyClass:
    def method(self, x: int) -> str:
        # lots of implementation
        return str(x)

def standalone(a, b):
    # body
    return a + b
'''
compressed = _compress_python('test.py', code)
assert 'import os' in compressed
assert 'class MyClass' in compressed
assert 'def method' in compressed
assert 'def standalone' in compressed
print('ok')
""")
    assert r.returncode == 0, f"Compress python failed: {r.stderr}"


def test_context_read_file_smart():
    r = run_py("""
import tempfile, os
from pathlib import Path
from agentops_swarm.context import read_file_smart
with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
    for i in range(1000):
        f.write(f'line {i}\\n')
    f.flush()
    content = read_file_smart(Path(f.name))
    # Smart read should truncate or limit large files
    assert len(content) > 0
    os.unlink(f.name)
print('ok')
""")
    assert r.returncode == 0, f"read_file_smart failed: {r.stderr}"


# ── budget.py unit tests ─────────────────────────────────────

def test_budget_state_basics():
    r = run_py("""
from agentops_swarm.budget import BudgetState, UsageRecord
import time
state = BudgetState()
assert state.remaining() > 0
rec = UsageRecord(
    timestamp=time.strftime('%Y-%m-%dT%H:%M:%S'),
    provider='test', tier='t1-fast', role='scout',
    task_id='test-task',
    input_tokens=100, output_tokens=50,
    estimated_cost=0.001, duration_seconds=1.5, success=True
)
state.record(rec)
assert len(state.records) == 1
assert state.total_spent_usd > 0
print('ok')
""")
    assert r.returncode == 0, f"Budget state failed: {r.stderr}"


def test_budget_report():
    r = run_py("""
from agentops_swarm.budget import BudgetState, UsageRecord, format_budget_report
import time
state = BudgetState()
rec = UsageRecord(
    timestamp=time.strftime('%Y-%m-%dT%H:%M:%S'),
    provider='test', tier='t2-mid', role='executor',
    task_id='test-task',
    input_tokens=500, output_tokens=200,
    estimated_cost=0.01, duration_seconds=3.0, success=True
)
state.record(rec)
report = format_budget_report(state)
assert len(report) > 20
print('ok')
""")
    assert r.returncode == 0, f"Budget report failed: {r.stderr}"


# ── course.py unit tests ─────────────────────────────────────

def test_course_fallback_slide():
    r = run_py("""
from agentops_swarm.course import _fallback_slide
task_data = {'id': 'test-task', 'title': 'Fix authentication'}
slide = _fallback_slide(
    task_data,
    'Fixed the auth bug in login.py',
    '--- a/login.py\\n+++ b/login.py\\n@@ -10 +10 @@\\n-old\\n+new'
)
assert 'test-task' in slide or 'Fix authentication' in slide
assert len(slide) > 50
print('ok')
""")
    assert r.returncode == 0, f"Fallback slide failed: {r.stderr}"


def test_course_fallback_guide():
    r = run_py("""
from agentops_swarm.course import _fallback_guide
task_data = {'id': 'test-task', 'title': 'Fix authentication'}
guide = _fallback_guide(
    task_data,
    'Fixed the auth bug in login.py',
    '--- a/login.py\\n+++ b/login.py'
)
assert 'test-task' in guide or 'Fix authentication' in guide
assert len(guide) > 50
print('ok')
""")
    assert r.returncode == 0, f"Fallback guide failed: {r.stderr}"


# ── File structure tests ─────────────────────────────────────

def test_project_structure():
    expected = [
        "agentops_swarm/__init__.py",
        "agentops_swarm/cli.py",
        "agentops_swarm/models.py",
        "agentops_swarm/context.py",
        "agentops_swarm/course.py",
        "agentops_swarm/budget.py",
        "pyproject.toml",
        "install.sh",
        "install.ps1",
        "README.md",
        "AGENTOPS.md",
        "bin/agentops",
    ]
    for path in expected:
        assert (ROOT / path).exists(), f"Missing: {path}"


def test_pyproject_version():
    content = (ROOT / "pyproject.toml").read_text()
    assert 'version = "4.' in content, "pyproject.toml should be v4.x"
    assert "agentops-swarm" in content


def test_install_sh_v4():
    content = (ROOT / "install.sh").read_text()
    assert "v4" in content.lower() or "4" in content
    assert "ollama" in content.lower(), "install.sh should mention Ollama"


def test_readme_v4():
    content = (ROOT / "README.md").read_text()
    assert "v4" in content.lower() or "4.0" in content
    assert "course" in content.lower()
    assert "tier" in content.lower()
