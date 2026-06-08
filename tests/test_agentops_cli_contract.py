from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_cli_compiles():
    ast.parse((ROOT / "agentops_swarm" / "cli.py").read_text())


def test_install_scripts_exist():
    assert (ROOT / "install.sh").exists()
    assert (ROOT / "install.ps1").exists()
    assert (ROOT / "bin" / "agentops").exists()


def test_docs_exist():
    assert (ROOT / "README.md").exists()
    assert (ROOT / "README.pt-BR.md").exists()
    assert "Claude" in (ROOT / "README.md").read_text()
    assert "Codex" in (ROOT / "README.md").read_text()
    assert "Antigravity" in (ROOT / "README.md").read_text()


def test_examples_exist():
    assert (ROOT / "examples" / "images").exists()
    assert (ROOT / "templates" / "prompts" / "personal-os-ui-big-picture.prompt.md").exists()
