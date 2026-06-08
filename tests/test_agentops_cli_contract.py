from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "agentops_swarm" / "cli.py"


def run_cmd(*args):
    return subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT, text=True, capture_output=True)


def test_help():
    p = run_cmd("--help")
    assert p.returncode == 0
    assert "AgentOps" in p.stdout


def test_doctor_imports():
    p = run_cmd("--version")
    assert p.returncode == 0
    assert "agentops" in p.stdout


def test_templates_exist():
    assert (ROOT / "templates" / "roles" / "opus-planner.md").exists()
    assert (ROOT / "templates" / "roles" / "haiku-scout.md").exists()
    assert (ROOT / "templates" / "frameworks" / "vue-quasar.md").exists()
