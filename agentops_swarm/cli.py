"""AgentOps Swarm v4 – multi-tier agentic orchestrator.

Key v4 improvements over v3:
  • 4-tier model routing (t0-local → t3-heavy). Heavy models plan; cheap models execute.
  • Smart context: file fingerprinting, selective reads, compressed summaries.
  • Course generation: every completed task produces a reveal.js course module.
  • Token budget tracking with automatic tier downgrades.
  • Smooth fallback cascade across all tiers.
  • Intervention hooks: pause, inspect, redirect at any point.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from agentops_swarm import __version__
    except Exception:
        __version__ = "4.0.0"

    from agentops_swarm.models import (
        PROVIDERS, TIERS, DEFAULT_PROFILES_V4,
        ModelProvider, ModelTier,
        probe_all, probe_ollama, probe_ollama_model, probe_claude, probe_codex, probe_antigravity, probe_gemini,
        select_provider, tier_for_role, resolve_profile,
        invoke_model, invoke_with_fallback, invoke_ollama, invoke_gemini,
        ensure_ollama_models, pull_ollama_model,
        estimate_tokens, estimate_cost,
        FallbackResult,
    )
    from agentops_swarm.context import (
        scan_project, changed_files, deleted_files,
        read_file_smart, grep_files, find_relevant_files,
        build_task_context, git_diff_context, project_tree,
        ContextManifest,
    )
    from agentops_swarm.course import (
        generate_task_course, generate_tranche_course,
        ensure_course_dirs, course_root,
    )
    from agentops_swarm.budget import (
        BudgetState, UsageRecord,
        load_budget, save_budget, track_usage, format_budget_report,
    )
else:
    try:
        from . import __version__
    except Exception:
        __version__ = "4.0.0"

    from .models import (
        PROVIDERS, TIERS, DEFAULT_PROFILES_V4,
        ModelProvider, ModelTier,
        probe_all, probe_ollama, probe_ollama_model, probe_claude, probe_codex, probe_antigravity, probe_gemini,
        select_provider, tier_for_role, resolve_profile,
        invoke_model, invoke_with_fallback, invoke_ollama, invoke_gemini,
        ensure_ollama_models, pull_ollama_model,
        estimate_tokens, estimate_cost,
        FallbackResult,
    )
    from .context import (
        scan_project, changed_files, deleted_files,
        read_file_smart, grep_files, find_relevant_files,
        build_task_context, git_diff_context, project_tree,
        ContextManifest,
    )
    from .course import (
        generate_task_course, generate_tranche_course,
        ensure_course_dirs, course_root,
    )
    from .budget import (
        BudgetState, UsageRecord,
        load_budget, save_budget, track_usage, format_budget_report,
    )

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path.cwd()
AGENTOPS_DIR = ROOT / ".agentops"
WORKTREE_DIR = ROOT / ".agent-worktrees"
ACTIVE_PATH = AGENTOPS_DIR / "active.json"
CONFIG_PATH = AGENTOPS_DIR / "config.json"
EVENTS_PATH = AGENTOPS_DIR / "events.jsonl"
BUDGET_PATH = AGENTOPS_DIR / "budget.json"
CONTEXT_PATH = AGENTOPS_DIR / "context-manifest.json"
TASKS_DIR = AGENTOPS_DIR / "tasks"
REPORTS_DIR = AGENTOPS_DIR / "reports"
RUNTIME_DIR = AGENTOPS_DIR / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
SCOUTS_DIR = AGENTOPS_DIR / "scouts"
ROLLBACKS_DIR = AGENTOPS_DIR / "rollbacks"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PACKAGE_ROOT / "templates"


def set_root(root: Path) -> None:
    """Rebase runtime paths after a guided flow selects a target folder."""
    global ROOT, AGENTOPS_DIR, WORKTREE_DIR, ACTIVE_PATH, CONFIG_PATH, EVENTS_PATH
    global BUDGET_PATH, CONTEXT_PATH, TASKS_DIR, REPORTS_DIR, RUNTIME_DIR
    global LOG_DIR, SCOUTS_DIR, ROLLBACKS_DIR

    ROOT = root.expanduser().resolve()
    AGENTOPS_DIR = ROOT / ".agentops"
    WORKTREE_DIR = ROOT / ".agent-worktrees"
    ACTIVE_PATH = AGENTOPS_DIR / "active.json"
    CONFIG_PATH = AGENTOPS_DIR / "config.json"
    EVENTS_PATH = AGENTOPS_DIR / "events.jsonl"
    BUDGET_PATH = AGENTOPS_DIR / "budget.json"
    CONTEXT_PATH = AGENTOPS_DIR / "context-manifest.json"
    TASKS_DIR = AGENTOPS_DIR / "tasks"
    REPORTS_DIR = AGENTOPS_DIR / "reports"
    RUNTIME_DIR = AGENTOPS_DIR / "runtime"
    LOG_DIR = RUNTIME_DIR / "logs"
    SCOUTS_DIR = AGENTOPS_DIR / "scouts"
    ROLLBACKS_DIR = AGENTOPS_DIR / "rollbacks"


USAGE_LIMIT_RE = re.compile(
    r"usage limit|session limit|rate limit|too many requests|\b429\b|quota|limit reached|try again|reset|5.?hour|five.?hour|overloaded|temporarily unavailable",
    re.IGNORECASE,
)

DEFAULT_CHECKS = [
    "test -x scripts/agents/run-pytest.sh && scripts/agents/run-pytest.sh tests -q || python3 -m pytest tests -q",
    "test -x ./scripts/check-secrets.sh && ./scripts/check-secrets.sh || true",
]

THEMES = ["nebula", "matrix", "reactor", "satellite", "deepsea", "arcade", "aurora", "oracle", "noir", "solar"]
SPINNERS = ["⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "◐◓◑◒", "▖▘▝▗", "←↖↑↗→↘↓↙", "⟡✦✧◆◇", "🌑🌒🌓🌔🌕🌖🌗🌘"]
QUOTES = [
    "compiling intent into motion",
    "synchronizing the swarm lattice",
    "polishing the command deck",
    "negotiating with entropy",
    "mapping diffs across hyperspace",
    "braiding context into code",
    "stabilizing the local-first continuum",
    "turning scattered scaffolds into systems",
    "checking invariants before the jump",
    "building quietly, refusing chaos",
    "fusing small patches into leverage",
    "routing cognition through worktrees",
    "cheap models carry heavy loads",
    "heavy models think; light models do",
]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def color(text: str, code: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg: str) -> None:
    print(color("→", "34") + " " + msg)


def ok(msg: str) -> None:
    print(color("✓", "32") + " " + msg)


def warn(msg: str) -> None:
    print(color("⚠", "33") + " " + msg)


def fail(msg: str, code: int = 1) -> None:
    print(color("✗", "31") + " " + msg, file=sys.stderr)
    raise SystemExit(code)


def run(cmd: str | list[str], cwd: Path | None = None, check: bool = False, capture: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if isinstance(cmd, list):
        args = cmd
        shell = False
        display = " ".join(shlex.quote(x) for x in cmd)
    else:
        args = cmd
        shell = True
        display = cmd
    event(None, "command", display, {"cwd": str(cwd or ROOT)})
    return subprocess.run(args, cwd=str(cwd or ROOT), text=True, shell=shell, capture_output=capture, check=check, env=env)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Init / state management
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    for p in [AGENTOPS_DIR, TASKS_DIR, REPORTS_DIR, RUNTIME_DIR, LOG_DIR, SCOUTS_DIR, WORKTREE_DIR, ROLLBACKS_DIR, AGENTOPS_DIR / "examples"]:
        p.mkdir(parents=True, exist_ok=True)


def event(task: str | None, typ: str, message: str, data: dict[str, Any] | None = None) -> None:
    try:
        ensure_dirs()
        rec = {"ts": now_iso(), "task": task, "type": typ, "message": message, "data": data or {}}
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid JSON at {path}: {exc}")


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def default_config(name: str = "project") -> dict[str, Any]:
    return {
        "version": 4,
        "project": name,
        "defaults": {
            "mode": "headless",
            "permission": "workspace",
            "pretty": True,
            "fallback": "cascade",
            "fallback_requires_confirmation": True,
            "auto_repair_requires_confirmation": True,
            "terminal": "auto",
            "max_parallel": 3,
            "prefer_local": False,
            "course_generation": True,
            "smart_context": True,
        },
        "budget": {
            "daily_limit_usd": 5.0,
            "session_limit_usd": 2.0,
        },
        "tiers": {
            "t0_models": ["ollama-qwen3-1.7b", "ollama-qwen3-4b"],
            "t1_models": ["ollama-qwen3-8b", "ollama-qwen3-14b", "gemini-flash"],
            "t2_models": ["claude-sonnet", "claude-haiku", "antigravity", "codex"],
            "t3_models": ["claude-opus"],
        },
        "profiles": DEFAULT_PROFILES_V4,
        "checks": DEFAULT_CHECKS,
    }


def ensure_init(name: str | None = None, force: bool = False) -> None:
    ensure_dirs()
    if force or not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, default_config(name or ROOT.name))
    if force or not ACTIVE_PATH.exists():
        save_json(ACTIVE_PATH, {"version": 4, "project": {"name": name or ROOT.name}, "tasks": []})
    readme_path = AGENTOPS_DIR / "examples" / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# AgentOps Examples\n\nDrop screenshots, markdown briefs, HTML prototypes, sketches, and fake data here.\n",
            encoding="utf-8",
        )


def require_init() -> None:
    if not ACTIVE_PATH.exists() or not CONFIG_PATH.exists():
        fail("AgentOps is not initialized here. Run: agentops init --name <project>")


def active() -> dict[str, Any]:
    require_init()
    return load_json(ACTIVE_PATH, {"version": 4, "tasks": []})


def config() -> dict[str, Any]:
    require_init()
    cfg = load_json(CONFIG_PATH, default_config(ROOT.name))
    cfg["profiles"] = {**DEFAULT_PROFILES_V4, **cfg.get("profiles", {})}
    defaults = cfg.setdefault("defaults", {})
    defaults.setdefault("fallback", "cascade")
    defaults.setdefault("fallback_requires_confirmation", True)
    return cfg


def save_active(a: dict[str, Any]) -> None:
    save_json(ACTIVE_PATH, a)


def tasks() -> list[dict[str, Any]]:
    return active().get("tasks", [])


def task_by_id(task_id: str) -> dict[str, Any]:
    for t in tasks():
        if t.get("id") == task_id:
            return t
    fail(f"Unknown task: {task_id}")


def profile(name: str | None) -> dict[str, str]:
    cfg = config()
    profiles = cfg.get("profiles", {})
    if not name:
        return profiles.get("sonnet-executor", DEFAULT_PROFILES_V4["sonnet-executor"])
    if name not in profiles:
        # Try v4 defaults
        if name in DEFAULT_PROFILES_V4:
            return DEFAULT_PROFILES_V4[name]
        fail(f"Unknown profile: {name}")
    return profiles[name]


# ---------------------------------------------------------------------------
# Budget integration
# ---------------------------------------------------------------------------

_budget_state: BudgetState | None = None


def get_budget() -> BudgetState:
    global _budget_state
    if _budget_state is None:
        _budget_state = load_budget(BUDGET_PATH)
        # Apply config limits
        try:
            cfg = config()
            budget_cfg = cfg.get("budget", {})
            _budget_state.daily_limit_usd = budget_cfg.get("daily_limit_usd", 5.0)
            _budget_state.session_limit_usd = budget_cfg.get("session_limit_usd", 2.0)
        except SystemExit:
            pass
    return _budget_state


def should_prefer_local() -> bool:
    """Check if we should prefer local models due to budget pressure."""
    budget = get_budget()
    try:
        cfg = config()
        if cfg.get("defaults", {}).get("prefer_local", False):
            return True
    except SystemExit:
        pass
    return budget.should_downgrade()


# ---------------------------------------------------------------------------
# Task & worktree management
# ---------------------------------------------------------------------------

def branch_for(task_id: str) -> str:
    t = task_by_id(task_id)
    return t.get("branch") or f"agent/{task_id}"


def wt_for(task_id: str) -> Path:
    return WORKTREE_DIR / task_id


def prompt_path(task_id: str, root: Path | None = None) -> Path:
    return (root or ROOT) / ".agentops" / "tasks" / f"{task_id}.prompt.md"


def report_path(task_id: str, root: Path | None = None) -> Path:
    return (root or ROOT) / ".agentops" / "reports" / task_id / "report.md"


def task_prompt(task_id: str, root: Path | None = None) -> str:
    p = prompt_path(task_id, root)
    if p.exists():
        return p.read_text(encoding="utf-8")
    t = task_by_id(task_id)
    rendered = generate_prompt_from_task(t)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(rendered, encoding="utf-8")
    return rendered


def read_template(kind: str, name: str) -> str:
    p = TEMPLATES_DIR / kind / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def generate_prompt_from_task(t: dict[str, Any]) -> str:
    framework = t.get("framework", "generic")
    profile_name = t.get("profile", "sonnet-executor")
    framework_text = read_template("frameworks", framework)
    role = profile(profile_name).get("role", "executor") if CONFIG_PATH.exists() else "executor"
    role_text = read_template("roles", profile_name) or read_template("roles", f"sonnet-{role}")
    allowed = "\n".join(f"- {p}" for p in t.get("allowed_paths", [])) or "- Use judgment; keep scope narrow."
    locked = "\n".join(f"- {p}" for p in t.get("locked_paths", [])) or "- None declared."
    acceptance = "\n".join(f"- {x}" for x in t.get("acceptance", [])) or "- Implement the task correctly and add tests."
    checks = "\n".join(f"- `{x}`" for x in t.get("checks", [])) or "- Run relevant tests."

    # Smart context section
    context_section = ""
    cfg = config() if CONFIG_PATH.exists() else {}
    if cfg.get("defaults", {}).get("smart_context", True):
        task_paths = t.get("allowed_paths", [])
        if task_paths:
            try:
                ctx = build_task_context(ROOT, task_paths, max_total_tokens=15_000)
                if ctx and len(ctx) > 100:
                    context_section = f"\n## Relevant code context\n\n{ctx}\n"
            except Exception:
                pass

    return f"""# AgentOps task: {t.get('id')}

Title: {t.get('title', t.get('id'))}
Priority: {t.get('priority', 'p1')}
Tranche: {t.get('tranche', 'unassigned')}
Executor profile: {profile_name}
Framework: {framework}
Tier: {profile(profile_name).get('tier', 'unknown')}

## Role guidance

{role_text.strip() or 'You are a bounded implementation worker.'}

## Framework guidance

{framework_text.strip() or 'Use the project conventions.'}

## Hard safety rules

- Do not read, print, modify, or commit `.env`, `.env.*`, secrets, private keys, tokens, credentials, backups, logs, or private data.
- Do not weaken authentication, authorization, or approval gates.
- Do not run destructive commands unless explicitly required and approved.
- Do not push to remote.
- Keep changes bounded to the task.
- Add or preserve tests. Do not delete meaningful tests to pass CI.
- Do NOT read the entire codebase. Only inspect files in the allowed paths.

## Allowed paths

{allowed}

## Locked/high-risk paths

{locked}
{context_section}
## Acceptance criteria

{acceptance}

## Checks to run

{checks}

## Required report

Write a report to:

`.agentops/reports/{t.get('id')}/report.md`

Include:
- root cause / implementation summary
- files changed
- tests/checks run
- failures or risks
- follow-up tasks
- estimated token usage category (low/medium/high)
"""


def normalize_task(raw: dict[str, Any]) -> dict[str, Any]:
    task_id = raw.get("id") or re.sub(r"[^a-z0-9-]+", "-", raw.get("title", "task").lower()).strip("-")
    return {
        "id": task_id,
        "title": raw.get("title", task_id),
        "tranche": raw.get("tranche", 1),
        "priority": raw.get("priority", "p1"),
        "executor": raw.get("executor", raw.get("engine", "claude")),
        "profile": raw.get("profile", profile_for_executor(raw.get("executor", raw.get("engine", "claude")))),
        "framework": raw.get("framework", "generic"),
        "branch": raw.get("branch", f"agent/{task_id}"),
        "allowed_paths": raw.get("allowed_paths", []),
        "locked_paths": raw.get("locked_paths", []),
        "acceptance": raw.get("acceptance", []),
        "checks": raw.get("checks", []),
    }


def profile_for_executor(executor: str) -> str:
    if executor == "codex":
        return "gpt-codex"
    if executor == "antigravity":
        return "antigravity-executor"
    if executor == "gemini":
        return "flash-scout"
    if executor == "ollama":
        return "local-scout"
    return "sonnet-executor"


def tasks_for_selector(task_ids: list[str] | None = None, tranche: str | None = None, all_tasks: bool = False) -> list[dict[str, Any]]:
    ts = tasks()
    if all_tasks:
        return ts
    if task_ids:
        return [task_by_id(x) for x in task_ids]
    if tranche is not None:
        return [t for t in ts if str(t.get("tranche")) == str(tranche)]
    fail("Specify task id(s), --tranche, or --all")


def update_task_fields(task_id: str, **updates: Any) -> dict[str, Any]:
    """Persist temporary task field changes and return original values.

    Fallback execution previously mutated only an in-memory task dict, while
    create_worktree()/worker_command() reloaded task data from disk. Persisting
    the runtime profile makes fallback routes actually take effect.
    """
    a = active()
    originals: dict[str, Any] = {}
    changed = False
    for task in a.get("tasks", []):
        if task.get("id") == task_id:
            for key, value in updates.items():
                originals[key] = task.get(key)
                task[key] = value
            changed = True
            break
    if not changed:
        fail(f"Unknown task: {task_id}")
    save_active(a)
    return originals


def restore_task_fields(task_id: str, originals: dict[str, Any]) -> None:
    if originals:
        update_task_fields(task_id, **originals)


# ---------------------------------------------------------------------------
# Worktree management
# ---------------------------------------------------------------------------

def sync_agentops_to_worktree(task_id: str) -> None:
    wt = wt_for(task_id)
    if not wt.exists():
        return
    dst = wt / ".agentops"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ["active.json", "config.json"]:
        src = AGENTOPS_DIR / name
        if src.exists():
            shutil.copy2(src, dst / name)
    for name in ["tasks", "examples", "scouts"]:
        src = AGENTOPS_DIR / name
        dd = dst / name
        if src.exists():
            if dd.exists():
                shutil.rmtree(dd)
            ignore = shutil.ignore_patterns("runtime", "logs", "reports", "*.log", "events.jsonl", "budget.json")
            shutil.copytree(src, dd, ignore=ignore)
    p = prompt_path(task_id)
    if not p.exists():
        task_prompt(task_id)
    (dst / "tasks").mkdir(exist_ok=True)
    if p.exists():
        shutil.copy2(p, dst / "tasks" / p.name)


def create_worktree(task_id: str, force: bool = False) -> Path:
    require_init()
    wt = wt_for(task_id)
    branch = branch_for(task_id)
    if wt.exists() and not force:
        sync_agentops_to_worktree(task_id)
        return wt
    if force and wt.exists():
        remove_worktree(task_id, remove_branch=False, yes=True)
    WORKTREE_DIR.mkdir(exist_ok=True)
    if run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0:
        run(["git", "worktree", "add", str(wt), branch], check=True)
    else:
        run(["git", "worktree", "add", str(wt), "-b", branch], check=True)
    sync_agentops_to_worktree(task_id)
    event(task_id, "worktree_created", str(wt), {"branch": branch})
    return wt


def remove_worktree(task_id: str, remove_branch: bool = True, yes: bool = False) -> None:
    wt = wt_for(task_id)
    branch = branch_for(task_id) if ACTIVE_PATH.exists() else f"agent/{task_id}"
    if not yes and not confirm(f"Remove worktree {wt} and branch {branch}?", default=False):
        return
    run(["git", "worktree", "remove", "--force", str(wt)], check=False)
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    if remove_branch:
        run(["git", "branch", "-D", branch], check=False)
    run(["git", "worktree", "prune"], check=False)
    shutil.rmtree(REPORTS_DIR / task_id, ignore_errors=True)
    for p in LOG_DIR.glob(f"{task_id}-*.log"):
        p.unlink(missing_ok=True)
    event(task_id, "cleaned", "worktree cleaned", {"branch_removed": remove_branch})


def confirm(msg: str, default: bool = False, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"{msg} {suffix} ").strip().lower()
    if not ans:
        return default
    return ans in {"y", "yes", "s", "sim"}


# ---------------------------------------------------------------------------
# Terminal spawning
# ---------------------------------------------------------------------------

def escape_cmd_for_terminal(cmd: str) -> str:
    return cmd.replace("'", "'\\''")


def spawn_terminal(title: str, cmd: str, terminal: str = "auto") -> None:
    event(None, "spawn", f"{title}: {cmd}")
    escaped = escape_cmd_for_terminal(cmd)
    if terminal == "tmux":
        run("tmux new-session -d -s agentops 2>/dev/null || true", check=False)
        run(f"tmux new-window -t agentops -n {shlex.quote(title[:24])} 'bash -lc '\''{escaped}'\'''", check=False)
        return
    if terminal == "current":
        subprocess.run(["bash", "-lc", cmd])
        return
    candidates: list[list[str]] = []
    if terminal in {"auto", "gnome"} and shutil.which("gnome-terminal"):
        candidates.append(["gnome-terminal", f"--title={title}", "--", "bash", "-lc", cmd])
    if terminal in {"auto", "x-terminal"} and shutil.which("x-terminal-emulator"):
        candidates.append(["x-terminal-emulator", "-T", title, "-e", "bash", "-lc", cmd])
    if terminal in {"auto", "kgx"} and shutil.which("kgx"):
        candidates.append(["kgx", f"--title={title}", "--", "bash", "-lc", cmd])
    if terminal in {"auto", "konsole"} and shutil.which("konsole"):
        candidates.append(["konsole", "--new-tab", "--title", title, "-e", "bash", "-lc", cmd])
    if terminal in {"auto", "xfce"} and shutil.which("xfce4-terminal"):
        candidates.append(["xfce4-terminal", f"--title={title}", f"--command=bash -lc {shlex.quote(cmd)}"])
    if terminal in {"auto", "kitty"} and shutil.which("kitty"):
        candidates.append(["kitty", "--title", title, "bash", "-lc", cmd])
    if terminal in {"auto", "alacritty"} and shutil.which("alacritty"):
        candidates.append(["alacritty", "--title", title, "-e", "bash", "-lc", cmd])
    if terminal in {"auto", "xterm"} and shutil.which("xterm"):
        candidates.append(["xterm", "-T", title, "-e", "bash", "-lc", cmd])
    for c in candidates:
        try:
            subprocess.Popen(c)
            return
        except Exception:
            continue
    if shutil.which("tmux"):
        warn("No GUI terminal worked; falling back to tmux. Attach with: tmux attach -t agentops")
        spawn_terminal(title, cmd, terminal="tmux")
        return
    warn("No supported terminal found. Run manually:")
    print(cmd)


# ---------------------------------------------------------------------------
# Worker execution
# ---------------------------------------------------------------------------

def write_budget_start(task_id: str, engine: str) -> str:
    data = load_json(BUDGET_PATH, {"runs": []})
    run_id = f"{task_id}-{int(time.time())}-{random.randint(1000,9999)}"
    data.setdefault("runs", []).append({"id": run_id, "task": task_id, "engine": engine, "started_at": now_iso(), "status": "running"})
    save_json(BUDGET_PATH, data)
    return run_id


def write_budget_end(run_id: str, status: str) -> None:
    data = load_json(BUDGET_PATH, {"runs": []})
    for r in data.get("runs", []):
        if r.get("id") == run_id:
            r["ended_at"] = now_iso()
            r["status"] = status
            break
    save_json(BUDGET_PATH, data)


def worker_command(task_id: str, mode: str, permission: str, profile_name: str | None = None, fallback: str = "cascade", yes: bool = False) -> tuple[list[str], str, str]:
    t = task_by_id(task_id)
    prof = profile(profile_name or t.get("profile"))
    engine = t.get("executor") or prof.get("engine", "claude")
    model = prof.get("model", "")
    wt = create_worktree(task_id)
    prompt = task_prompt(task_id)
    log_file = LOG_DIR / f"{task_id}-{engine}-{int(time.time())}.log"

    if engine == "claude":
        cmd = ["claude"]
        if model and shutil.which("claude"):
            cmd += ["--model", model]
        if mode == "headless":
            if permission == "full":
                cmd += ["--permission-mode", "bypassPermissions"]
            cmd += ["-p", prompt]
        else:
            if permission == "full":
                cmd += ["--permission-mode", "bypassPermissions"]
    elif engine in {"codex", "gpt"}:
        if mode == "headless":
            cmd = ["codex", "exec"]
            if permission == "full":
                cmd += ["--dangerously-bypass-approvals-and-sandbox"]
            else:
                cmd += ["--sandbox", "workspace-write"]
            cmd += ["--cd", str(wt), prompt]
        else:
            cmd = ["codex"]
            if permission == "full":
                cmd += ["--dangerously-bypass-approvals-and-sandbox"]
            else:
                cmd += ["--sandbox", "workspace-write"]
            cmd += ["--cd", str(wt)]
    elif engine in {"antigravity", "agy"}:
        executable = shutil.which("agy") or shutil.which("antigravity")
        template = os.environ.get("AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE")
        prompt_file = wt / ".agentops" / f"{task_id}-prompt.md"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(prompt, encoding="utf-8")
        if template and mode == "headless":
            shell_cmd = template.replace("{prompt}", shlex.quote(str(prompt_file))).replace("{worktree}", shlex.quote(str(wt)))
            return ["bash", "-lc", shell_cmd], "antigravity", str(log_file)
        if not executable:
            cmd = ["bash", "-lc", "echo 'Antigravity CLI not found. Install agy/antigravity or set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE.' >&2; exit 78"]
        elif mode == "interactive":
            cmd = [executable]
        else:
            cmd = ["bash", "-lc", f"echo 'Antigravity headless execution requires AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE.' >&2; echo 'Prompt: {shlex.quote(str(prompt_file))}' >&2; exit 78"]
    else:
        fail(f"Unsupported engine: {engine}")
    return cmd, engine, str(log_file)


def should_fallback(log_text: str, rc: int, on_any: bool) -> bool:
    return bool(rc != 0 and (on_any or USAGE_LIMIT_RE.search(log_text)))


def fallback_engine_choice(default: str, yes: bool) -> str:
    if default == "cascade":
        return "cascade"
    if default != "ask":
        return default
    if yes or not sys.stdin.isatty():
        return "pause"
    print("\nWorker failed or hit a model limit. Continue with fallback?")
    print("  1) Cascade (try cheaper models automatically)")
    print("  2) Antigravity")
    print("  3) Codex/GPT")
    print("  4) Retry original later")
    print("  5) Pause")
    choice = input("Selection [1-5]: ").strip()
    return {"1": "cascade", "2": "antigravity", "3": "codex", "4": "retry", "5": "pause", "": "cascade"}.get(choice, "pause")


# The cascade fallback order uses only agentic coding CLIs. Direct API scouts do not count as successful code fallbacks.
CASCADE_PROFILES = ["sonnet-executor", "claude-haiku", "antigravity-executor", "gpt-codex"]


def run_task_engine(task_id: str, mode: str = "headless", permission: str = "workspace", profile_name: str | None = None, pretty: bool = True, fallback: str = "cascade", yes: bool = False, fallback_on_any: bool = False) -> int:
    ensure_dirs()

    # Budget check before starting
    budget = get_budget()
    if budget.is_exhausted():
        warn("Budget exhausted. Only local models are available.")
        if not yes and not confirm("Continue with local-only execution?", default=False):
            return 76

    cmd, engine, log_file_s = worker_command(task_id, mode, permission, profile_name)
    log_file = Path(log_file_s)
    run_id = write_budget_start(task_id, engine)
    event(task_id, "worker_start", f"starting {engine}", {"cmd": cmd, "log": str(log_file), "tier": profile(profile_name or task_by_id(task_id).get("profile")).get("tier", "unknown")})

    start_time = time.time()
    if pretty:
        rc = run_pretty_subprocess(task_id, engine, cmd, log_file)
    else:
        with log_file.open("w", encoding="utf-8") as f:
            p = subprocess.Popen(cmd, cwd=str(wt_for(task_id)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            assert p.stdout
            for line in p.stdout:
                print(line, end="")
                f.write(line)
            rc = p.wait()

    duration = time.time() - start_time
    log_text = log_file.read_text(errors="ignore") if log_file.exists() else ""

    # Track usage
    track_usage(
        budget, BUDGET_PATH, task_id,
        f"cli-{engine}", profile(profile_name or task_by_id(task_id).get("profile")).get("tier", "t2-mid"),
        profile(profile_name or task_by_id(task_id).get("profile")).get("role", "executor"),
        task_prompt(task_id)[:1000], log_text[:1000], duration, rc == 0,
    )

    if should_fallback(log_text, rc, fallback_on_any):
        choice = fallback_engine_choice(fallback, yes)
        event(task_id, "fallback_choice", choice)
        if choice == "cascade":
            rc = run_cascade_fallback(task_id, permission, log_file, pretty=pretty, yes=yes, current_profile=profile_name)
        elif choice in {"codex", "gpt", "antigravity", "agy"}:
            rc = run_fallback(task_id, permission, choice, log_file, pretty=pretty, yes=yes)
        elif choice == "retry":
            warn("Retry requested later. Worktree preserved.")
            rc = 75
        else:
            warn("Task paused. Worktree preserved.")
            rc = 76

    if rc == 0:
        commit_dirty(task_id, fallback=False)
        event(task_id, "worker_completed", "completed")
        write_budget_end(run_id, "completed")
        # Generate course if enabled
        _maybe_generate_course(task_id)
    else:
        event(task_id, "worker_failed", f"exit code {rc}")
        write_budget_end(run_id, f"failed:{rc}")
    return rc


def run_cascade_fallback(task_id: str, permission: str, previous_log: Path, pretty: bool = True, yes: bool = False, current_profile: str | None = None) -> int:
    """Try progressively cheaper engines until one works."""
    t = task_by_id(task_id)
    original_executor = t.get("executor")
    original_profile = t.get("profile")

    # Determine which profiles to try
    profiles_to_try = []
    skip = True
    for p in CASCADE_PROFILES:
        if p == current_profile or p == original_profile:
            skip = False
            continue
        if not skip:
            profiles_to_try.append(p)

    # If we couldn't position in cascade, try all cheaper ones
    if not profiles_to_try:
        profiles_to_try = [p for p in CASCADE_PROFILES if p != original_profile]

    for fallback_profile in profiles_to_try:
        prof = profile(fallback_profile)
        engine = prof.get("engine", "claude")

        # Check if this engine is available
        if engine == "claude" and not probe_claude():
            continue
        if engine == "codex" and not probe_codex():
            continue
        if engine == "antigravity" and not probe_antigravity():
            continue

        info(f"Cascade fallback: trying {fallback_profile} ({engine})")

        if engine in ("claude", "codex", "antigravity"):
            # CLI-based execution
            originals = update_task_fields(task_id, executor=engine, profile=fallback_profile)
            t = task_by_id(task_id)
            wt = create_worktree(task_id)
            sync_agentops_to_worktree(task_id)

            # Write continuation prompt
            _write_continuation_prompt(task_id, previous_log, wt)

            try:
                rc = run_task_engine(
                    task_id, mode="headless", permission=permission,
                    profile_name=fallback_profile, pretty=pretty,
                    fallback="pause", yes=True, fallback_on_any=False,
                )
                if rc == 0:
                    return rc
            finally:
                restore_task_fields(task_id, originals)

    warn("All cascade fallbacks exhausted. Task paused.")
    return 76


def _write_continuation_prompt(task_id: str, previous_log: Path, wt: Path) -> None:
    """Write a continuation prompt for fallback workers."""
    prev_tail = previous_log.read_text(errors="ignore")[-8000:] if previous_log.exists() else ""
    continuation = wt / ".agentops" / "fallback-continuation.md"
    continuation.write_text(
        f"""# Fallback continuation for {task_id}

The previous worker could not continue. Continue from the existing worktree.

## Original prompt

```markdown
{task_prompt(task_id)}
```

## Previous log tail

```text
{prev_tail}
```

## Worktree state

```text
{run(['git','status','--short'], cwd=wt, capture=True).stdout}
{run(['git','diff','--stat'], cwd=wt, capture=True).stdout}
```

Write final report to `.agentops/reports/{task_id}/report.md`.
""",
        encoding="utf-8",
    )
    p = prompt_path(task_id, wt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(continuation.read_text(encoding="utf-8"), encoding="utf-8")
    root_prompt = prompt_path(task_id)
    root_prompt.write_text(continuation.read_text(encoding="utf-8"), encoding="utf-8")


def run_fallback(task_id: str, permission: str, engine: str, previous_log: Path, pretty: bool = True, yes: bool = False) -> int:
    t = task_by_id(task_id)
    original_executor = t.get("executor")
    original_profile = t.get("profile")
    originals: dict[str, Any] = {}
    fallback_profile = t.get("profile")
    if engine in {"codex", "gpt"}:
        fallback_profile = "gpt-codex"
        originals = update_task_fields(task_id, executor="codex", profile=fallback_profile)
    elif engine in {"antigravity", "agy"}:
        fallback_profile = "antigravity-executor"
        originals = update_task_fields(task_id, executor="antigravity", profile=fallback_profile)
    wt = create_worktree(task_id)
    sync_agentops_to_worktree(task_id)
    _write_continuation_prompt(task_id, previous_log, wt)
    backup = prompt_path(task_id).read_text(encoding="utf-8") if prompt_path(task_id).exists() else None
    try:
        rc = run_task_engine(task_id, mode="headless", permission=permission, profile_name=fallback_profile, pretty=pretty, fallback="pause", yes=True, fallback_on_any=False)
    finally:
        if backup is not None:
            prompt_path(task_id).write_text(backup, encoding="utf-8")
        restore_task_fields(task_id, originals)
    return rc


# ---------------------------------------------------------------------------
# Course generation integration
# ---------------------------------------------------------------------------

def _maybe_generate_course(task_id: str) -> None:
    """Generate course content for a completed task if enabled."""
    try:
        cfg = config()
        if not cfg.get("defaults", {}).get("course_generation", True):
            return

        t = task_by_id(task_id)
        report = ""
        rp = report_path(task_id)
        rp_wt = report_path(task_id, wt_for(task_id))
        for p in [rp, rp_wt]:
            if p.exists():
                report = p.read_text(encoding="utf-8", errors="ignore")
                break

        if not report:
            return

        # Get diff summary
        wt = wt_for(task_id)
        diff_summary = ""
        if wt.exists():
            diff_summary = git_diff_context(wt)

        budget = get_budget()
        info(f"Generating course for {task_id}")
        generate_task_course(
            task_data=t,
            report=report,
            diff_summary=diff_summary,
            agentops_dir=AGENTOPS_DIR,
            prefer_local=should_prefer_local(),
            budget_remaining=budget.remaining(),
        )
        ok(f"Course generated for {task_id}")
    except Exception as exc:
        warn(f"Course generation failed for {task_id}: {exc}")


# ---------------------------------------------------------------------------
# Commit, merge, repair
# ---------------------------------------------------------------------------

def commit_dirty(task_id: str, fallback: bool = False) -> None:
    wt = wt_for(task_id)
    if not wt.exists():
        return
    r = report_path(task_id, wt)
    if not r.exists():
        r.parent.mkdir(parents=True, exist_ok=True)
        status = run(["git", "status", "--short"], cwd=wt, capture=True).stdout
        stat = run(["git", "diff", "--stat"], cwd=wt, capture=True).stdout
        r.write_text(f"# {task_id} report\n\nStatus: completed without explicit report.\n\n## Git status\n\n```text\n{status}\n```\n\n## Diff stat\n\n```text\n{stat}\n```\n", encoding="utf-8")
    if run(["git", "status", "--short"], cwd=wt, capture=True).stdout.strip():
        run(["git", "add", "."], cwd=wt, check=True)
        msg = f"Agent task: {task_id}" if not fallback else f"Agent fallback task: {task_id}"
        run(["git", "commit", "-m", msg], cwd=wt, check=False)
    dest = report_path(task_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if r.exists():
        shutil.copy2(r, dest)


def create_rollback_ref(label: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    ref = f"agentops/rollback/{label}-{ts}"
    run(["git", "branch", ref], check=False)
    event(None, "rollback_ref", ref)
    return ref


def run_checks(checks: list[str] | None = None) -> tuple[int, str]:
    cfg = config()
    cmds = checks or cfg.get("checks") or DEFAULT_CHECKS
    output = []
    for cmd in cmds:
        info(f"check: {cmd}")
        p = run(cmd, capture=True, check=False)
        output.append(f"$ {cmd}\n{p.stdout}\n{p.stderr}")
        if p.returncode != 0:
            return p.returncode, "\n".join(output)
    return 0, "\n".join(output)


def merge_task(task_id: str, auto_repair: bool = False, repair_attempts: int = 1, repair_profile: str = "sonnet-repair", yes: bool = False, run_task_checks: bool = True) -> bool:
    branch = branch_for(task_id)
    commit_dirty(task_id)
    if run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode != 0:
        warn(f"Missing branch {branch}")
        return False
    if not run(["git", "diff", "--quiet", f"HEAD..{branch}"], check=False).returncode:
        ok(f"No changes to merge for {task_id}")
        return True
    create_rollback_ref(task_id)
    info(f"Merging {branch}")
    rc = run(["git", "merge", "--no-ff", branch, "-m", f"Merge agent task: {task_id}"], check=False).returncode
    if rc != 0:
        warn("Merge conflict. Resolve manually or run rollback.")
        return False
    if run_task_checks:
        checks = task_by_id(task_id).get("checks") or None
        rc, out = run_checks(checks)
        if rc != 0:
            log = REPORTS_DIR / "auto-repair" / f"{task_id}-check-failure-{int(time.time())}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(out, encoding="utf-8")
            warn(f"Checks failed after merge. Log: {log}")
            if auto_repair and repair_attempts > 0:
                if confirm(f"Spawn repair worker for {task_id}?", default=False, assume_yes=yes):
                    return repair_and_merge(task_id, log, repair_profile, repair_attempts, yes=yes)
            return False
    return True


def repair_and_merge(task_id: str, failure_log: Path, repair_profile: str, attempts: int, yes: bool = False) -> bool:
    for attempt in range(attempts):
        rid = f"auto-repair-{task_id}-{int(time.time())}-{attempt+1}"
        a = active()
        original = task_by_id(task_id)
        repair_task = normalize_task({
            "id": rid,
            "title": f"Auto repair for {task_id}",
            "tranche": "repair",
            "priority": "p0",
            "executor": profile(repair_profile).get("engine", "claude"),
            "profile": repair_profile,
            "framework": original.get("framework", "generic"),
            "allowed_paths": original.get("allowed_paths", []),
            "locked_paths": original.get("locked_paths", []),
            "acceptance": ["Fix the failing checks without broad refactors", "Preserve security boundaries"],
            "checks": original.get("checks", []),
        })
        a.setdefault("tasks", []).append(repair_task)
        save_active(a)
        prompt = f"""# Auto repair for {task_id}

Fix the failing checks. Do not add features. Preserve security.

## Failure log

```text
{failure_log.read_text(errors='ignore')[-12000:]}
```

## Original acceptance

{json.dumps(original.get('acceptance', []), indent=2)}

Write report to `.agentops/reports/{rid}/report.md`.
"""
        pp = prompt_path(rid)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(prompt, encoding="utf-8")
        rc = run_task_engine(rid, mode="headless", permission="workspace", profile_name=repair_profile, pretty=True, fallback="cascade", yes=yes, fallback_on_any=True)
        if rc == 0:
            if merge_task(rid, auto_repair=False, run_task_checks=True, yes=yes):
                return True
    return False


# ---------------------------------------------------------------------------
# Pretty subprocess / TUI display
# ---------------------------------------------------------------------------

def theme_color(theme: str) -> str:
    return {"matrix":"32","reactor":"33","satellite":"36","deepsea":"34","arcade":"35","aurora":"95","oracle":"96","noir":"37","solar":"93"}.get(theme, "94")


def theme_art(theme: str, tick: int) -> list[str]:
    if theme == "matrix":
        glyphs = "01╱╲│╳•◦△◇"
        return ["".join(random.choice(glyphs) if (i + tick + row) % 5 == 0 else " " for i in range(66)) for row in range(3)]
    if theme == "reactor":
        return ["          ╭───────────────╮", "      ╭───┤  CORE ONLINE  ├───╮", "      │   ╰───────┬───────╯   │", "              ◉───◆───◉"]
    if theme == "arcade":
        return ["      ┌────────────────────────────┐", "      │  INSERT CONTEXT TO PLAY    │", f"      │  SCORE: {tick * 137 % 999999:06d}            │", "      └────────────────────────────┘"]
    if theme == "aurora":
        return ["      ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿", "        gradient fields aligning", "      ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿"]
    rows = []
    for row in range(4):
        line = ""
        for i in range(58):
            if (i + row + tick) % 17 == 0:
                line += "✦"
            elif (i * (row+1) + tick) % 23 == 0:
                line += "⋆"
            elif random.randrange(40) == 0:
                line += "·"
            else:
                line += " "
        rows.append(line)
    return rows


def run_pretty_subprocess(task_id: str, engine: str, cmd: list[str], log_file: Path) -> int:
    theme = os.environ.get("AGENTOPS_ANIMATION_THEME") or random.choice(THEMES)
    spinner = random.choice(SPINNERS)
    c = theme_color(theme)
    budget = get_budget()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=str(wt_for(task_id)), stdout=f, stderr=subprocess.STDOUT, text=True)
    start = time.time()
    tick = 0
    hide = "\033[?25l"
    show = "\033[?25h"
    try:
        sys.stdout.write(hide)
        while p.poll() is None:
            elapsed = int(time.time() - start)
            frame = spinner[tick % len(spinner)]
            tier_info = profile(task_by_id(task_id).get("profile")).get("tier", "?")
            budget_str = f"${budget.session_spent_usd:.3f}/${budget.session_limit_usd:.2f}"
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(f"\033[1;{c}m╔══════════════════════════════════════════════════════════════════════════════╗\033[0m\n")
            sys.stdout.write(f"\033[1;{c}m║                      ✦ AGENTOPS SWARM v4 ✦                                ║\033[0m\n")
            sys.stdout.write(f"\033[1;{c}m╠══════════════════════════════════════════════════════════════════════════════╣\033[0m\n")
            sys.stdout.write(f"║ Task       {task_id:<62}║\n")
            sys.stdout.write(f"║ Engine     {engine:<62}║\n")
            sys.stdout.write(f"║ Tier       {tier_info:<62}║\n")
            sys.stdout.write(f"║ Budget     {budget_str:<62}║\n")
            sys.stdout.write(f"║ Status     {frame} running{'':<52}║\n")
            sys.stdout.write(f"║ Elapsed    {elapsed//60:02d}:{elapsed%60:02d}{'':<57}║\n")
            sys.stdout.write(f"\033[1;{c}m╠══════════════════════════════════════════════════════════════════════════════╣\033[0m\n")
            for line in theme_art(theme, tick):
                sys.stdout.write(f"║  {line[:72]:<72}║\n")
            sys.stdout.write(f"\033[1;{c}m╠════════════════════════════════════ LOG TAIL ═══════════════════════════════╣\033[0m\n")
            tail = tail_file(log_file, 20)
            for line in tail:
                sys.stdout.write((line[:76] + "\n") if len(line) > 76 else line + "\n")
            sys.stdout.write(f"\033[1;{c}m╚══════════════════════════════════════════════════════════════════════════════╝\033[0m\n")
            sys.stdout.write(f"{random.choice(QUOTES)} | log: {log_file}\n")
            sys.stdout.flush()
            tick += 1
            time.sleep(2)
    finally:
        sys.stdout.write(show)
    rc = p.wait()
    sys.stdout.write("\nFinal output:\n" + "─" * 78 + "\n")
    for line in tail_file(log_file, 120):
        print(line)
    sys.stdout.write("─" * 78 + "\n")
    return rc


def tail_file(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(errors="ignore").splitlines()
        return lines[-n:]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Smart scout (uses t1 or t0 models instead of burning tokens)
# ---------------------------------------------------------------------------

def smart_scout(task_id: str, profile_name: str = "local-scout") -> str:
    """Run a scout using lightweight local models instead of coding CLIs.

    Falls back gracefully through the configured t1/t0 direct providers.
    """
    t = task_by_id(task_id)
    prompt_text = task_prompt(task_id)

    # Build compact context
    task_paths = t.get("allowed_paths", [])
    tree = project_tree(ROOT, max_depth=2, max_entries=100)
    context = ""
    if task_paths:
        context = build_task_context(ROOT, task_paths, max_total_tokens=8000)

    scout_prompt = f"""Inspect the repository for task `{task_id}`.

Task prompt:
```markdown
{prompt_text[:4000]}
```

Project tree:
```
{tree[:3000]}
```

{f"Relevant files:{chr(10)}{context[:5000]}" if context else ""}

Produce a concise report with:
- relevant files
- existing implementation
- gaps
- risks/conflicts
- suggested implementation steps
- tests to run

Do not edit files.
"""

    budget = get_budget()
    result = invoke_with_fallback(
        role="scout",
        prompt=scout_prompt,
        system="You are a read-only code scout. Inspect and report. Never edit files.",
        max_tokens=4096,
        budget_remaining=budget.remaining(),
        prefer_local=should_prefer_local(),
    )

    output = result.output if result.success else f"[Scout failed. Attempts: {result.attempts}]"

    # Track usage
    track_usage(
        budget, BUDGET_PATH, task_id,
        result.provider_used.name, tier_for_role("scout"),
        "scout", scout_prompt[:500], output[:500],
        0.0, result.success,
    )

    # Save report
    out_path = SCOUTS_DIR / f"{task_id}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def collect_reports() -> None:
    ensure_dirs()
    lines = ["# AgentOps Tranche Report", "", f"Generated: {now_iso()}", ""]
    for t in tasks():
        task_id = t.get("id")
        wt = wt_for(task_id)
        src = report_path(task_id, wt)
        dest = report_path(task_id)
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        status = "report" if dest.exists() else "no-report"
        dirty = "dirty" if wt.exists() and run(["git","status","--short"], cwd=wt, capture=True).stdout.strip() else "clean" if wt.exists() else "no-worktree"
        lines.append(f"- `{task_id}`: {dirty}, {status}")
    (REPORTS_DIR / "TRANCHE_REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(REPORTS_DIR / "TRANCHE_REPORT.md")


def examples_index() -> None:
    ex = AGENTOPS_DIR / "examples"
    ex.mkdir(parents=True, exist_ok=True)
    lines = ["# AgentOps Examples Index", "", f"Generated: {now_iso()}", ""]
    forbidden = re.compile(r"token|secret|credential|client_secret|private|\.env", re.I)
    for p in sorted(ex.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(ex)
        if forbidden.search(str(rel)):
            continue
        size = p.stat().st_size
        lines.append(f"## `{rel}`")
        lines.append(f"- size: {size} bytes")
        if p.suffix.lower() in {".md", ".txt", ".html", ".css", ".json", ".yaml", ".yml"} and size < 200_000:
            txt = p.read_text(errors="ignore")[:1600]
            lines.append("\n```text\n" + txt + "\n```\n")
        else:
            lines.append("")
    (ex / "GENERATED_INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(ex / "GENERATED_INDEX.md")


# ---------------------------------------------------------------------------
# Guided complete flow
# ---------------------------------------------------------------------------

def ui_rule(title: str = "") -> None:
    """Render a crisp terminal rule with optional Rich support."""
    try:
        from rich.console import Console  # type: ignore
        Console().rule(f"[bold cyan]{title}[/]" if title else "")
    except Exception:
        width = shutil.get_terminal_size((88, 20)).columns
        if title:
            label = f" {title} "
            side = max(2, (width - len(label)) // 2)
            print(color("─" * side + label + "─" * side, "36"))
        else:
            print(color("─" * width, "36"))


def ui_panel(title: str, lines: Iterable[str], accent: str = "96") -> None:
    """Render a rectangular operational panel; use Rich when present."""
    clean = [str(x) for x in lines]
    try:
        from rich.console import Console  # type: ignore
        from rich.panel import Panel  # type: ignore
        from rich.text import Text  # type: ignore
        body = Text("\n".join(clean))
        Console().print(Panel(body, title=title, border_style="cyan", expand=False))
    except Exception:
        width = min(shutil.get_terminal_size((96, 20)).columns, 100)
        border = "═" * max(10, width - 2)
        print(color(f"╔{border}╗", accent))
        print(color(f"║ {title[:width-4]:<{width-4}} ║", accent))
        print(color(f"╠{border}╣", accent))
        for line in clean:
            for chunk in textwrap.wrap(line, width=max(20, width - 4)) or [""]:
                print(f"║ {chunk:<{width-4}} ║")
        print(color(f"╚{border}╝", accent))


def ui_flow_header(stage: str, detail: str = "") -> None:
    art = [
        "AGENTOPS SWARM :: CONTROL FLOW",
        "init → prompt → plan → tranche → merge gate → next tranche",
    ]
    if detail:
        art.append(detail)
    ui_panel(stage, art, accent="96")


def ensure_git_repository(assume_yes: bool = False) -> None:
    """Ensure the target folder can support git worktrees."""
    r = run(["git", "rev-parse", "--is-inside-work-tree"], capture=True, check=False)
    if r.returncode == 0:
        return
    if not confirm("Target folder is not a git repository. Run git init here?", default=True, assume_yes=assume_yes):
        fail("AgentOps worktrees require a git repository.")
    run(["git", "init"], check=True)
    ok("Initialized git repository")


def read_flow_prompt(args: argparse.Namespace) -> str:
    if getattr(args, "prompt", None):
        return str(args.prompt).strip() + "\n"
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    print("Paste the complete project prompt/specification. End with a line containing only: EOF")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "EOF":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        fail("No prompt provided.")
    return text + "\n"


def write_flow_overview(prompt_text: str) -> Path:
    out = AGENTOPS_DIR / "flow" / f"overview-{int(time.time())}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt_text, encoding="utf-8")
    return out


def detect_flow_kind(prompt_text: str) -> str:
    low = prompt_text.lower()
    if any(x in low for x in ["agentops", "swarm", "tranche", "fallback", "worktree", "antigravity"]):
        return "agentops-harness"
    if any(x in low for x in ["vue", "quasar", "vite", "carousel", "steam big picture", "nexus core"]):
        return "vue-quasar"
    if any(x in low for x in ["fastapi", "pydantic", "sqlalchemy"]):
        return "fastapi"
    if any(x in low for x in ["python", "cli", "argparse", "pytest"]):
        return "python"
    return "generic"


def _task_executor(kind: str) -> tuple[str, str]:
    # Prefer Antigravity for bounded agentic coding when it is installed; otherwise use Claude Sonnet.
    if probe_antigravity():
        return "antigravity", "antigravity-executor"
    return "claude", "sonnet-executor"


def generate_offline_plan(prompt_text: str, tranches: int) -> dict[str, Any]:
    """Create a deterministic bootstrap plan when no planner model is available."""
    kind = detect_flow_kind(prompt_text)
    executor, exec_profile = _task_executor(kind)
    checks_vue = ["pnpm i", "pnpm run build"]
    checks_py = ["python3 -m pytest tests -q"]

    if kind == "vue-quasar":
        framework = "vue-quasar"
        base_paths = ["package.json", "vite.config.js", "index.html", "src", "README.md"]
        specs = [
            (1, "scaffold-vue-quasar-shell", "Scaffold the Vue 3 + Quasar + Vite project shell", ["Project installs with pnpm i", "Required files exist", "No TypeScript is introduced"], checks_vue),
            (2, "build-cinematic-carousel", "Implement the cinematic carousel and ambient background system", ["Focused active card uses rectangular geometry", "Side neighbors are cropped and animated", "Keyboard/wheel/click navigation works"], checks_vue),
            (2, "build-structured-workspace", "Implement the light operational canvas, topology map, metrics, and queue", ["Topology uses orthogonal SVG paths", "Canvas remains high contrast", "Module data drives all sections"], checks_vue),
            (3, "implement-controller-interactions", "Add command/search shortcuts, controller action strip, persistence, and responsive navigation", ["Arrow keys, Y, A, and Ctrl/Cmd+K work", "Active module persists", "Mobile drawer/dock behavior works"], checks_vue),
            (3, "polish-motion-accessibility", "Polish motion, reduced-motion behavior, focus states, and semantic landmarks", ["Reduced motion is honored", "Buttons have accessible labels/text", "No default Quasar dashboard styling leaks through"], checks_vue),
            (4, "qa-docs-build", "Run build QA and update README with usage instructions", ["pnpm run build succeeds", "README documents run/build", "No external API/backend required"], checks_vue),
        ]
    elif kind == "agentops-harness":
        framework = "python"
        base_paths = ["agentops_swarm", "templates", "tests", "README.md", "AGENTOPS.md", "install.sh", "install.ps1"]
        specs = [
            (1, "add-antigravity-first-routing", "Make Antigravity a first-class execution provider and remove Gemini from default routes", ["Antigravity profile is available", "Planner rules prefer Antigravity over Gemini", "Doctor reports Antigravity status"], checks_py),
            (2, "implement-complete-flow-command", "Add a complete guided flow from folder init through prompt, planning, tranche runs, and merge gates", ["Flow initializes target folders", "Prompt is saved", "Plan import/overwrite works", "Start and merge confirmations are explicit"], checks_py),
            (2, "tighten-fallback-continuation", "Make fallback persistence and continuation prompts reliable", ["Fallback profile changes persist for worker execution", "Direct API fallback cannot falsely succeed as code execution", "Previous log and worktree state are preserved"], checks_py),
            (3, "polish-terminal-ux", "Improve terminal panels, animations, and Rich-compatible presentation", ["Animated runner remains usable without Rich", "Headers show current tranche/task/budget", "No excessive Material/dashboard visual language"], checks_py),
            (4, "update-docs-tests", "Update documentation and tests for the new flow and provider policy", ["README explains complete flow", "Tests cover parser/profile contracts", "No stale Gemini-first wording remains in core docs"], checks_py),
        ]
    else:
        framework = kind if kind in {"fastapi", "python"} else "generic"
        base_paths = ["src", "tests", "README.md", "docs"]
        checks = checks_py if framework in {"python", "fastapi"} else ["test -f README.md"]
        specs = [
            (1, "map-architecture-and-contract", "Map the target architecture and create implementation contracts", ["Scope is bounded", "Risks and dependencies are explicit"], checks),
            (2, "implement-core-experience", "Implement the central product flow from the supplied prompt", ["Core flow works end to end", "Changes stay within allowed paths"], checks),
            (3, "polish-ux-and-edges", "Polish edge cases, terminal/UI affordances, accessibility, and failure paths", ["Fallback/empty/error states are handled", "Interaction quality is improved"], checks),
            (4, "qa-docs-release", "Run verification, update docs, and prepare merge notes", ["Checks pass", "README or docs explain usage"], checks),
        ]

    tasks_out: list[dict[str, Any]] = []
    for i, (tranche, tid, title, acceptance, checks) in enumerate(specs, start=1):
        tranche = min(int(tranche), max(1, tranches))
        tasks_out.append({
            "id": tid,
            "title": title,
            "tranche": tranche,
            "priority": "p0" if tranche == 1 else "p1",
            "executor": executor if "qa" not in tid else "codex",
            "profile": exec_profile if "qa" not in tid else "codex-verifier",
            "framework": framework,
            "allowed_paths": base_paths,
            "locked_paths": [".env", ".env.*", ".git", ".agentops/runtime", ".agentops/budget.json"],
            "acceptance": acceptance,
            "checks": checks,
        })
    return {"tasks": tasks_out, "source": "offline-bootstrap", "kind": kind}


def planner_available(profile_name: str) -> bool:
    prof = profile(profile_name)
    engine = prof.get("engine", "claude")
    if engine == "claude":
        return shutil.which("claude") is not None
    if engine == "antigravity":
        return probe_antigravity()
    if engine == "codex":
        return probe_codex()
    if engine == "ollama":
        return probe_ollama()
    if engine == "gemini":
        return probe_gemini()
    return False


def load_or_create_flow_plan(args: argparse.Namespace, prompt_text: str, overview_path: Path) -> Path:
    out_path = AGENTOPS_DIR / "planner" / f"flow-plan-{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if getattr(args, "plan_file", None):
        data = load_json(Path(args.plan_file), {})
        import_tasks(data, overwrite=args.overwrite)
        save_json(out_path, data)
        return out_path

    if args.offline_plan or not planner_available(args.planner_profile):
        if not args.offline_plan:
            warn(f"Planner profile {args.planner_profile} is unavailable; using deterministic offline bootstrap plan.")
        data = generate_offline_plan(prompt_text, args.tranches)
        import_tasks(data, overwrite=args.overwrite)
        save_json(out_path, data)
        return out_path

    # Use the existing model planner when available.
    cmd_plan(argparse.Namespace(
        overview=str(overview_path), tranches=args.tranches, run=True,
        profile=args.planner_profile, overwrite=args.overwrite,
    ))
    # Keep a copy in the flow namespace for discoverability.
    latest = sorted((AGENTOPS_DIR / "planner").glob("plan-*.json"), key=lambda x: x.stat().st_mtime)
    if latest:
        shutil.copy2(latest[-1], out_path)
    return out_path


def tranche_order() -> list[str]:
    vals = sorted({str(t.get("tranche", "1")) for t in tasks()}, key=lambda x: (int(x) if x.isdigit() else 9999, x))
    return vals


def summarize_flow_plan(plan_path: Path) -> None:
    ui_rule("Generated tranches")
    by_tranche: dict[str, list[dict[str, Any]]] = {}
    for t in tasks():
        by_tranche.setdefault(str(t.get("tranche", "1")), []).append(t)
    for tr in tranche_order():
        print(color(f"Tranche {tr}", "1;36"))
        for t in by_tranche.get(tr, []):
            print(f"  • {t.get('id'):<34} {t.get('executor'):<12} {t.get('title')}")
    print(f"\nPlan file: {plan_path}")


def run_flow_tranches(args: argparse.Namespace) -> None:
    order = tranche_order()
    if not order:
        fail("No tasks were generated/imported.")

    for index, tranche in enumerate(order, start=1):
        selected = tasks_for_selector(None, tranche, False)
        ui_flow_header(f"Tranche {tranche} / {order[-1]}", f"{len(selected)} task(s) queued")
        for t in selected:
            task_id = t["id"]
            if args.clean_first:
                remove_worktree(task_id, yes=True)
            ui_panel("Task execution", [
                f"task     {task_id}",
                f"title    {t.get('title')}",
                f"engine   {t.get('executor')} / {t.get('profile')}",
                f"mode     {args.mode} / {args.permission}",
            ], accent="94")
            rc = run_task_engine(
                task_id,
                mode=args.mode,
                permission=args.permission,
                profile_name=t.get("profile"),
                pretty=not args.no_pretty,
                fallback=args.fallback,
                yes=args.yes,
                fallback_on_any=args.fallback_on_any_failure,
            )
            if rc != 0:
                warn(f"Stopping flow at {task_id}; worker returned {rc}. Worktree is preserved for inspection.")
                return

        collect_reports()
        if not confirm(f"Merge tranche {tranche} now? This gate is intentionally manual.", default=False, assume_yes=args.yes):
            warn(f"Flow paused before merge for tranche {tranche}.")
            return
        cmd_merge(argparse.Namespace(
            task=None, tranche=tranche, all=False,
            auto_repair=args.auto_repair,
            repair_attempts=args.repair_attempts,
            repair_profile=args.repair_profile,
            yes=args.yes,
        ))

        if index < len(order):
            nxt = order[index]
            if not confirm(f"Continue to tranche {nxt}?", default=True, assume_yes=args.yes):
                warn("Flow paused after merge.")
                return

    ok("Complete flow finished all tranches.")


def cmd_complete_flow(args: argparse.Namespace) -> None:
    target = Path(args.folder or ROOT).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    os.chdir(target)
    set_root(target)

    ui_flow_header("Complete guided flow", str(ROOT))
    ensure_init(args.name or ROOT.name, force=args.overwrite)
    ensure_git_repository(assume_yes=args.yes)

    prompt_text = ""
    if args.plan_file and not args.prompt and not args.prompt_file:
        prompt_text = "# Existing planner file supplied; prompt capture skipped for resume.\n"
    else:
        prompt_text = read_flow_prompt(args)
    overview_path = write_flow_overview(prompt_text)
    ok(f"Prompt saved: {overview_path}")

    plan_path = load_or_create_flow_plan(args, prompt_text, overview_path)
    summarize_flow_plan(plan_path)

    start = args.start
    if start is None:
        start = confirm("Start tranche execution now?", default=False, assume_yes=args.yes)
    if not start:
        warn("Flow prepared but not started. Resume with: agentops complete-flow --plan-file " + str(plan_path))
        return

    run_flow_tranches(args)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    ensure_init(args.name, force=args.force)

    # Offer to set up local models
    if hasattr(args, "setup_local") and args.setup_local:
        info("Setting up local models...")
        if probe_ollama():
            ensure_ollama_models(["qwen3:4b", "qwen3:8b"], interactive=True)
        else:
            warn("Ollama not found. Install from https://ollama.com for local model support.")

    ok(f"AgentOps v4 initialized for {args.name or ROOT.name}")


def cmd_doctor(args: argparse.Namespace) -> None:
    print(f"AgentOps Swarm {__version__}")
    print(f"Project: {ROOT}")
    print()

    # Core tools
    for exe in ["git", "python3", "claude", "agy", "antigravity", "codex", "tmux"]:
        path = shutil.which(exe)
        status = color("✓", "32") if path else color("✗", "31")
        print(f"  {status} {exe:16} {path or 'missing'}")

    # Ollama
    print()
    ollama_path = shutil.which("ollama")
    if ollama_path:
        print(f"  {color('✓', '32')} ollama           {ollama_path}")
        if probe_ollama():
            r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines()[:10]:
                    print(f"      {line}")
        else:
            print(f"      {color('⚠', '33')} ollama not running. Start with: ollama serve")
    else:
        print(f"  {color('⚠', '33')} ollama           missing (local models unavailable)")
        print(f"      Install from https://ollama.com")

    # Agentic provider notes
    print()
    if probe_antigravity():
        print(f"  {color('✓', '32')} Antigravity      CLI found")
        if not os.environ.get("AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE"):
            print(f"      {color('⚠', '33')} set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE for headless runs")
    else:
        print(f"  {color('⚠', '33')} Antigravity      missing (install agy/antigravity for this fallback route)")
    if probe_gemini():
        print(f"  {color('·', '37')} Gemini API       legacy key found; not used by default")

    # Probe all
    print()
    results = probe_all()
    available_tiers = set()
    for prov_name, available in results.items():
        prov = PROVIDERS[prov_name]
        if available:
            for tier_name, tier in TIERS.items():
                if prov in tier.providers:
                    available_tiers.add(tier_name)

    print("Model tiers:")
    for tier_name in ["t0-local", "t1-fast", "t2-mid", "t3-heavy"]:
        status = color("✓", "32") if tier_name in available_tiers else color("✗", "31")
        tier = TIERS[tier_name]
        providers = [p.name for p in tier.providers if p.available]
        print(f"  {status} {tier_name:12} {', '.join(providers) if providers else 'no providers available'}")

    # Budget
    print()
    if ACTIVE_PATH.exists():
        print(f"Tasks: {len(tasks())}")
        budget = get_budget()
        print(budget.summary())


def cmd_profiles(args: argparse.Namespace) -> None:
    for k, v in config().get("profiles", {}).items():
        if args.verbose:
            print(f"{k}: {json.dumps(v)}")
        else:
            tier = v.get("tier", "?")
            role = v.get("role", "?")
            engine = v.get("engine", "?")
            print(f"  {k:<24} {tier:<12} {role:<12} {engine}")


def cmd_templates(args: argparse.Namespace) -> None:
    base = TEMPLATES_DIR
    if args.sub == "list":
        for p in sorted(base.rglob("*.md")):
            print(p.relative_to(base))


def cmd_list(args: argparse.Namespace) -> None:
    for t in tasks():
        wt = wt_for(t["id"])
        wt_state = "worktree" if wt.exists() else "no-wt"
        dirty = "dirty" if wt.exists() and run(["git","status","--short"], cwd=wt, capture=True).stdout.strip() else ""
        rep = "report" if report_path(t["id"]).exists() or report_path(t["id"], wt).exists() else "no-report"
        tier = profile(t.get("profile")).get("tier", "?")
        print(f"{t['id']:<36} t{str(t.get('tranche','')):<4} {t.get('priority',''):<3} {tier:<10} {t.get('executor',''):<10} {wt_state:<8} {dirty:<6} {rep}")


def cmd_status(args: argparse.Namespace) -> None:
    cmd_list(args)
    print()
    budget = get_budget()
    print(budget.summary())


def cmd_add_task(args: argparse.Namespace) -> None:
    require_init()
    a = active()
    raw = {
        "id": args.id,
        "title": args.title or args.id,
        "tranche": args.tranche,
        "priority": args.priority,
        "executor": args.executor,
        "profile": args.profile or profile_for_executor(args.executor),
        "framework": args.framework,
        "allowed_paths": args.allowed_path or [],
        "locked_paths": args.locked_path or [],
        "acceptance": args.acceptance or [],
        "checks": args.check or [],
    }
    nt = normalize_task(raw)
    a["tasks"] = [t for t in a.get("tasks", []) if t.get("id") != nt["id"]] + [nt]
    save_active(a)
    prompt_path(nt["id"]).write_text(generate_prompt_from_task(nt), encoding="utf-8")
    ok(f"Added task {nt['id']}")


def cmd_prompt(args: argparse.Namespace) -> None:
    task_by_id(args.task)
    p = prompt_path(args.task)
    p.parent.mkdir(parents=True, exist_ok=True)
    if args.file:
        p.write_text(Path(args.file).read_text(encoding="utf-8"), encoding="utf-8")
    elif args.stdin:
        p.write_text(sys.stdin.read(), encoding="utf-8")
    elif args.edit:
        if not p.exists():
            p.write_text(task_prompt(args.task), encoding="utf-8")
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(p)])
    else:
        print("Paste prompt. End with a line containing only: EOF")
        lines = []
        while True:
            line = input()
            if line == "EOF":
                break
            lines.append(line)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"Prompt saved: {p}")


def planner_prompt(overview: str, tranches: int) -> str:
    return f"""You are an Opus-level project planner for AgentOps Swarm v4.

Create a narrow, executable multi-agent DAG. Output ONLY JSON with this schema:

{{
  "tasks": [
    {{
      "id": "kebab-case",
      "title": "short title",
      "tranche": 1,
      "priority": "p0|p1|p2",
      "executor": "claude|antigravity|codex|ollama|gemini",
      "profile": "sonnet-executor|antigravity-executor|gpt-codex|local-scout|flash-scout",
      "framework": "generic|vue-quasar|react|fastapi|python|docker-compose|ci|docs|qa",
      "allowed_paths": ["path"],
      "locked_paths": ["path"],
      "acceptance": ["criterion"],
      "checks": ["command"]
    }}
  ]
}}

Rules:
- Create {tranches} tranches.
- Keep each task bounded and mergeable.
- Include QA tasks after implementation tranches.
- Avoid secrets/runtime paths.
- Include checks.
- IMPORTANT: Use the cheapest executor that can handle the task:
  * "ollama" for grep, file listing, simple extraction
  * "ollama" for scouts, summaries, context compression, and simple extraction
  * "antigravity" for bounded agentic coding work when available
  * "claude" for complex implementation (sonnet) and planning (opus) ONLY
  * "codex" as fallback when Antigravity/Claude are unavailable
  * "gemini" only when the user explicitly opts into the legacy API path
- Set allowed_paths narrowly. Never allow the full repo.
- Each task should touch at most 5-10 files.

Project overview:

{overview}
"""


def cmd_plan(args: argparse.Namespace) -> None:
    require_init()
    overview = Path(args.overview).read_text(encoding="utf-8") if args.overview else sys.stdin.read()
    prompt_text = planner_prompt(overview, args.tranches)
    out_path = AGENTOPS_DIR / "planner" / f"plan-{int(time.time())}.json"
    out_path.parent.mkdir(exist_ok=True)
    if args.run:
        prof = profile(args.profile or "opus-planner")
        model = prof.get("model", "opus")
        engine = prof.get("engine", "claude")

        if engine == "claude" and shutil.which("claude"):
            cmd = ["claude", "--model", model, "-p", prompt_text]
            info(f"Running planner model ({model})")
            p = subprocess.run(cmd, text=True, capture_output=True)
            raw = p.stdout.strip() or p.stderr.strip()
        else:
            # Use direct invocation for non-claude planners
            info(f"Running planner via {engine}")
            provider = select_provider("t3-heavy")
            raw = invoke_model(provider, prompt_text, max_tokens=8192)

        (out_path.with_suffix(".raw.txt")).write_text(raw, encoding="utf-8")
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0) if m else raw)
        except Exception as exc:
            warn(f"Could not parse planner JSON: {exc}. Raw saved to {out_path.with_suffix('.raw.txt')}")
            return
    else:
        print(prompt_text)
        return
    import_tasks(data, overwrite=args.overwrite)
    save_json(out_path, data)
    ok(f"Imported planner tasks; saved {out_path}")


def import_tasks(data: dict[str, Any], overwrite: bool = False) -> None:
    a = active()
    existing = {t.get("id"): t for t in a.get("tasks", [])}
    for raw in data.get("tasks", []):
        nt = normalize_task(raw)
        if nt["id"] in existing and not overwrite:
            continue
        existing[nt["id"]] = nt
        prompt_path(nt["id"]).write_text(generate_prompt_from_task(nt), encoding="utf-8")
    a["tasks"] = list(existing.values())
    save_active(a)


def cmd_import_plan(args: argparse.Namespace) -> None:
    data = load_json(Path(args.file), {})
    import_tasks(data, overwrite=args.overwrite)
    ok("Plan imported")


def cmd_scout(args: argparse.Namespace) -> None:
    selected = tasks_for_selector([args.task] if args.task else None, str(args.tranche) if args.tranche else None, args.all)
    for t in selected:
        task_id = t["id"]
        prof_name = args.profile or "local-scout"
        prof = profile(prof_name)
        engine = prof.get("engine", "ollama")

        if engine in ("ollama", "gemini"):
            # Use smart scout (direct invocation, no CLI overhead)
            info(f"Smart scout {task_id} via {prof_name}")
            output = smart_scout(task_id, prof_name)
            print(f"Scout report: {SCOUTS_DIR / f'{task_id}.md'}")
        else:
            # Fall back to CLI-based scout
            prompt = f"""You are a read-only scout. Inspect the repository for task `{task_id}`.

Task prompt:

```markdown
{task_prompt(task_id)}
```

Produce a concise report with:
- relevant files
- existing implementation
- gaps
- risks/conflicts
- suggested implementation steps
- tests to run

Do not edit files. Write report to `.agentops/scouts/{task_id}.md`.
"""
            out = SCOUTS_DIR / f"{task_id}.md"
            cmd = ["claude", "--model", prof.get("model", "haiku"), "-p", prompt]
            p = subprocess.run(cmd, text=True, capture_output=True)
            out.write_text(p.stdout or p.stderr, encoding="utf-8")
            print(f"Scout report: {out}")


def cmd_run(args: argparse.Namespace) -> None:
    task_by_id(args.task)
    if args.spawn:
        cmd = f"cd {shlex.quote(str(ROOT))} && agentops run {shlex.quote(args.task)} --mode {shlex.quote(args.mode)} --permission {shlex.quote(args.permission)} --fallback {shlex.quote(args.fallback)} {'--yes' if args.yes else ''} {'--fallback-on-any-failure' if args.fallback_on_any_failure else ''}; echo; read -r -p 'Press Enter to close...'"
        spawn_terminal(f"agent:{args.task}", cmd, args.terminal)
        return
    rc = run_task_engine(args.task, args.mode, args.permission, profile_name=args.profile, pretty=not args.no_pretty, fallback=args.fallback, yes=args.yes, fallback_on_any=args.fallback_on_any_failure)
    raise SystemExit(0 if rc in {0, 75, 76} else rc)


def cmd_launch(args: argparse.Namespace) -> None:
    selected = tasks_for_selector(args.task, str(args.tranche) if args.tranche is not None else None, args.all)
    if not selected:
        fail("No tasks selected")
    max_parallel = args.max_parallel or config().get("defaults", {}).get("max_parallel", 3)
    if len(selected) > max_parallel and not args.yes:
        if not confirm(f"Launch {len(selected)} tasks? Recommended max is {max_parallel}.", default=False):
            return

    # Show budget estimate
    budget = get_budget()
    info(f"Budget remaining: ${budget.remaining():.2f}")

    for t in selected:
        task_id = t["id"]
        if args.clean_first:
            remove_worktree(task_id, yes=True)
        create_worktree(task_id)
        cmd = f"cd {shlex.quote(str(ROOT))} && agentops run {shlex.quote(task_id)} --mode {shlex.quote(args.mode)} --permission {shlex.quote(args.permission)} --fallback {shlex.quote(args.fallback)} {'--yes' if args.yes else ''} {'--fallback-on-any-failure' if args.fallback_on_any_failure else ''}; echo; read -r -p 'Press Enter to close...'"
        if args.spawn:
            spawn_terminal(f"agent:{task_id}", cmd, args.terminal)
        else:
            print(cmd)
    if args.monitor:
        spawn_terminal("agentops:tui", f"cd {shlex.quote(str(ROOT))} && agentops tui", args.terminal)


def cmd_collect(args: argparse.Namespace) -> None:
    collect_reports()


def cmd_merge(args: argparse.Namespace) -> None:
    selected = tasks_for_selector(args.task, str(args.tranche) if args.tranche is not None else None, args.all)
    for t in selected:
        if not merge_task(t["id"], auto_repair=args.auto_repair, repair_attempts=args.repair_attempts, repair_profile=args.repair_profile, yes=args.yes):
            fail(f"Merge failed/stopped at {t['id']}")
    ok("Merge completed")

    # Generate tranche course if merging a whole tranche
    if args.tranche:
        try:
            cfg = config()
            if cfg.get("defaults", {}).get("course_generation", True):
                info(f"Generating tranche {args.tranche} course")
                generate_tranche_course(
                    args.tranche, selected, REPORTS_DIR, AGENTOPS_DIR,
                    prefer_local=should_prefer_local(),
                    budget_remaining=get_budget().remaining(),
                )
                ok(f"Tranche course generated at {course_root(AGENTOPS_DIR)}")
        except Exception as exc:
            warn(f"Tranche course generation failed: {exc}")


def cmd_clean(args: argparse.Namespace) -> None:
    selected = tasks_for_selector(args.task, str(args.tranche) if args.tranche is not None else None, args.all)
    for t in selected:
        remove_worktree(t["id"], remove_branch=not args.keep_branch, yes=args.yes or args.force)
    if args.prune:
        run(["git", "worktree", "prune"], check=False)


def cmd_retry(args: argparse.Namespace) -> None:
    selected = tasks_for_selector([args.task], None, False)
    for t in selected:
        remove_worktree(t["id"], remove_branch=True, yes=True)
        create_worktree(t["id"])
        rc = run_task_engine(t["id"], args.mode, args.permission, pretty=not args.no_pretty, fallback=args.fallback, yes=args.yes, fallback_on_any=args.fallback_on_any_failure)
        if rc not in {0, 75, 76}:
            raise SystemExit(rc)


def cmd_events(args: argparse.Namespace) -> None:
    if not EVENTS_PATH.exists():
        return
    lines = EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    tail = lines[-args.tail:] if args.tail else lines
    if args.json:
        print("\n".join(tail))
    else:
        for line in tail:
            try:
                r = json.loads(line)
                print(f"{r.get('ts')} {r.get('task') or '-':<32} {r.get('type'):<20} {r.get('message')}")
            except Exception:
                print(line)


def cmd_budget(args: argparse.Namespace) -> None:
    budget = get_budget()
    print(format_budget_report(budget))


def cmd_examples_index(args: argparse.Namespace) -> None:
    examples_index()


def cmd_rollback(args: argparse.Namespace) -> None:
    if args.list:
        out = run("git branch --list 'agentops/rollback/*' --sort=-committerdate", capture=True).stdout
        print(out)
        return
    if not args.ref:
        fail("Specify --ref or --list")
    if not confirm(f"Hard reset current branch to {args.ref}?", default=False, assume_yes=args.yes):
        return
    run(["git", "reset", "--hard", args.ref], check=True)
    ok(f"Rolled back to {args.ref}")


def cmd_course(args: argparse.Namespace) -> None:
    """Generate or view course content."""
    if args.sub == "generate":
        if args.task:
            for tid in args.task:
                t = task_by_id(tid)
                report = ""
                for p in [report_path(tid), report_path(tid, wt_for(tid))]:
                    if p.exists():
                        report = p.read_text(encoding="utf-8", errors="ignore")
                        break
                diff = git_diff_context(wt_for(tid)) if wt_for(tid).exists() else ""
                result = generate_task_course(t, report, diff, agentops_dir=AGENTOPS_DIR, prefer_local=args.local)
                ok(f"Course generated for {tid}")
        elif args.tranche:
            selected = tasks_for_selector(None, args.tranche, False)
            generate_tranche_course(args.tranche, selected, REPORTS_DIR, AGENTOPS_DIR, prefer_local=args.local)
            ok(f"Tranche course generated")
    elif args.sub == "view":
        root = course_root(AGENTOPS_DIR)
        if root.exists():
            info(f"Course directory: {root}")
            for f in sorted(root.glob("slides/*.md")):
                print(f"  slide: {f.name}")
            for f in sorted(root.glob("guides/*.md")):
                print(f"  guide: {f.name}")
        else:
            warn("No course generated yet.")
    elif args.sub == "serve":
        root = course_root(AGENTOPS_DIR)
        if not root.exists():
            fail("No course generated yet.")
        port = args.port or 8080
        info(f"Serving course at http://localhost:{port}")
        subprocess.run([sys.executable, "-m", "http.server", str(port)], cwd=str(root))


def cmd_setup_models(args: argparse.Namespace) -> None:
    """Interactive setup for local models."""
    info("Checking available model providers...")
    probe_all()

    # Ollama
    if probe_ollama():
        ok("Ollama is running")
        models = ["qwen3:1.7b", "qwen3:4b", "qwen3:8b"]
        if args.full:
            models.extend(["qwen3:14b", "qwen3:32b"])
        results = ensure_ollama_models(models, interactive=not args.yes)
        for model, success in results.items():
            if success:
                ok(f"  {model} ready")
            else:
                warn(f"  {model} not available")
    else:
        warn("Ollama not running. Install from https://ollama.com")

    # Antigravity / legacy providers
    if probe_antigravity():
        ok("Antigravity CLI found")
        if not os.environ.get("AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE"):
            warn("For headless Antigravity runs, set AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE.")
    else:
        warn("Antigravity CLI not found. Install agy/antigravity to enable that execution route.")
    if probe_gemini():
        info("Gemini API key found, but Gemini is a legacy opt-in provider, not a default route.")

    # Summary
    print()
    results = probe_all()
    available = sum(1 for v in results.values() if v)
    ok(f"{available}/{len(results)} model providers available")


# ---------------------------------------------------------------------------
# Intervention commands
# ---------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspect a task's current state: worktree, diff, report, prompt."""
    task_id = args.task
    t = task_by_id(task_id)
    wt = wt_for(task_id)

    print(f"\n{color('Task:', '1')} {task_id}")
    print(f"Title: {t.get('title')}")
    print(f"Profile: {t.get('profile')} | Tier: {profile(t.get('profile')).get('tier')}")
    print(f"Executor: {t.get('executor')}")

    if wt.exists():
        print(f"\nWorktree: {wt}")
        status = run(["git", "status", "--short"], cwd=wt, capture=True).stdout
        if status.strip():
            print(f"Modified files:\n{status}")
        stat = run(["git", "diff", "--stat"], cwd=wt, capture=True).stdout
        if stat.strip():
            print(f"Diff stat:\n{stat}")
    else:
        print("\nNo worktree created yet.")

    rp = report_path(task_id, wt if wt.exists() else ROOT)
    if rp.exists():
        print(f"\nReport (first 40 lines):")
        for line in rp.read_text(errors="ignore").splitlines()[:40]:
            print(f"  {line}")

    scout = SCOUTS_DIR / f"{task_id}.md"
    if scout.exists():
        print(f"\nScout report available: {scout}")

    # Show logs
    logs = sorted(LOG_DIR.glob(f"{task_id}-*.log"))
    if logs:
        latest = logs[-1]
        print(f"\nLatest log: {latest}")
        for line in tail_file(latest, 20):
            print(f"  {line}")


def cmd_context(args: argparse.Namespace) -> None:
    """Show the smart context that would be built for a task."""
    task_id = args.task
    t = task_by_id(task_id)
    task_paths = t.get("allowed_paths", [])

    if not task_paths:
        warn("No allowed_paths defined for this task. Context will be minimal.")

    ctx = build_task_context(ROOT, task_paths, max_total_tokens=args.max_tokens or 15000)
    print(ctx)
    tokens = estimate_tokens(ctx)
    print(f"\n--- {tokens} estimated tokens ---")


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def tui_print_header() -> None:
    print(color("\n╔════════════════════════════════════════════════════════╗", "96"))
    print(color("║             AGENTOPS SWARM v4 CONTROL                ║", "96"))
    print(color("╚════════════════════════════════════════════════════════╝", "96"))
    budget = get_budget()
    print(f"  Budget: ${budget.session_spent_usd:.3f}/${budget.session_limit_usd:.2f} | Remaining: ${budget.remaining():.2f}")
    if budget.should_downgrade():
        print(color("  ⚠ Budget pressure: preferring local models", "33"))
    print()


def cmd_tui(args: argparse.Namespace) -> None:
    require_init()
    while True:
        tui_print_header()
        print("  1) Status")
        print("  2) Paste/edit task prompt")
        print("  3) Generate DAG with planner model")
        print("  4) Launch selected tasks")
        print("  5) Merge selected tasks")
        print("  6) Scout tasks (smart, uses cheap models)")
        print("  7) Clean/retry tasks")
        print("  8) Collect reports")
        print("  9) Generate course")
        print(" 10) Events")
        print(" 11) Budget report")
        print(" 12) Inspect task")
        print(" 13) Setup models")
        print(" 14) Examples index")
        print(" 15) Complete guided flow")
        print("  q) Quit")
        choice = input("\nChoice: ").strip().lower()
        try:
            if choice == "1":
                cmd_status(argparse.Namespace())
            elif choice == "2":
                tid = input("Task id: ").strip()
                cmd_prompt(argparse.Namespace(task=tid, file=None, stdin=False, edit=False))
            elif choice == "3":
                print("Paste overview. End with EOF.")
                lines = []
                while True:
                    line = input()
                    if line == "EOF":
                        break
                    lines.append(line)
                tmp = AGENTOPS_DIR / "planner" / "tui-overview.md"
                tmp.parent.mkdir(exist_ok=True)
                tmp.write_text("\n".join(lines), encoding="utf-8")
                tr = int(input("Number of tranches [4]: ").strip() or "4")
                cmd_plan(argparse.Namespace(overview=str(tmp), tranches=tr, run=True, profile="opus-planner", overwrite=True))
            elif choice == "4":
                sel = input("Task ids comma-separated, or tranche number: ").strip()
                spawn = confirm("Spawn terminals?", default=True)
                yes = confirm("Allow fallback without asking?", default=False)
                if sel.isdigit():
                    ns = argparse.Namespace(task=None, tranche=sel, all=False, spawn=spawn, monitor=True, mode="headless", permission="workspace", terminal="auto", fallback="cascade", yes=yes, clean_first=False, max_parallel=None, fallback_on_any_failure=True)
                else:
                    ids = [x.strip() for x in sel.split(",") if x.strip()]
                    ns = argparse.Namespace(task=ids, tranche=None, all=False, spawn=spawn, monitor=True, mode="headless", permission="workspace", terminal="auto", fallback="cascade", yes=yes, clean_first=False, max_parallel=None, fallback_on_any_failure=True)
                cmd_launch(ns)
            elif choice == "5":
                sel = input("Task ids comma-separated, or tranche number: ").strip()
                auto = confirm("Enable auto-repair if checks fail?", default=False)
                yes = confirm("Approve repair automatically?", default=False)
                if sel.isdigit():
                    ns = argparse.Namespace(task=None, tranche=sel, all=False, auto_repair=auto, repair_attempts=2, repair_profile="sonnet-repair", yes=yes)
                else:
                    ns = argparse.Namespace(task=[x.strip() for x in sel.split(",") if x.strip()], tranche=None, all=False, auto_repair=auto, repair_attempts=2, repair_profile="sonnet-repair", yes=yes)
                cmd_merge(ns)
            elif choice == "6":
                sel = input("Task ids comma-separated, or tranche number: ").strip()
                local = confirm("Prefer local models?", default=False)
                if sel.isdigit():
                    selected = tasks_for_selector(None, sel, False)
                else:
                    selected = tasks_for_selector([x.strip() for x in sel.split(",") if x.strip()])
                for t in selected:
                    smart_scout(t["id"])
                    ok(f"Scout: {t['id']}")
            elif choice == "7":
                tid = input("Task id: ").strip()
                if confirm(f"Clean and retry {tid}?", default=False):
                    cmd_retry(argparse.Namespace(task=tid, mode="headless", permission="workspace", no_pretty=False, fallback="cascade", yes=False, fallback_on_any_failure=True))
            elif choice == "8":
                cmd_collect(argparse.Namespace())
            elif choice == "9":
                sel = input("Task id or tranche number (or 'all'): ").strip()
                if sel.isdigit():
                    selected = tasks_for_selector(None, sel, False)
                    generate_tranche_course(sel, selected, REPORTS_DIR, AGENTOPS_DIR, prefer_local=should_prefer_local())
                    ok("Course generated")
                elif sel == "all":
                    for t in tasks():
                        _maybe_generate_course(t["id"])
                    ok("All courses generated")
                else:
                    _maybe_generate_course(sel)
                    ok(f"Course generated for {sel}")
            elif choice == "10":
                cmd_events(argparse.Namespace(tail=60, json=False))
            elif choice == "11":
                cmd_budget(argparse.Namespace())
            elif choice == "12":
                tid = input("Task id: ").strip()
                cmd_inspect(argparse.Namespace(task=tid))
            elif choice == "13":
                cmd_setup_models(argparse.Namespace(full=False, yes=False))
            elif choice == "14":
                examples_index()
            elif choice == "15":
                cmd_complete_flow(argparse.Namespace(folder=str(ROOT), name=None, overwrite=False, tranches=4, prompt_file=None, prompt=None, plan_file=None, planner_profile="opus-planner", offline_plan=False, start=None, mode="headless", permission="workspace", fallback="cascade", fallback_on_any_failure=True, auto_repair=False, repair_attempts=1, repair_profile="sonnet-repair", clean_first=False, no_pretty=False, yes=False))
            elif choice == "q":
                return
        except SystemExit as exc:
            warn(f"Command exited: {exc}")
        input("\nPress Enter...")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentops", description="AgentOps Swarm v4: multi-tier agentic orchestration")
    p.add_argument("--version", action="version", version=f"agentops {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init")
    sp.add_argument("--name")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--setup-local", action="store_true", help="Also set up local ollama models")
    sp.set_defaults(func=cmd_init)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    sp = sub.add_parser("profiles")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_profiles)

    sp = sub.add_parser("templates")
    s2 = sp.add_subparsers(dest="sub", required=True)
    s2.add_parser("list")
    sp.set_defaults(func=cmd_templates)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("status").set_defaults(func=cmd_status)

    sp = sub.add_parser("add-task")
    sp.add_argument("id")
    sp.add_argument("--title")
    sp.add_argument("--tranche", default=1)
    sp.add_argument("--priority", default="p1")
    sp.add_argument("--executor", default="claude", choices=["claude","antigravity","codex","gemini","ollama"])
    sp.add_argument("--profile")
    sp.add_argument("--framework", default="generic")
    sp.add_argument("--allowed-path", action="append")
    sp.add_argument("--locked-path", action="append")
    sp.add_argument("--acceptance", action="append")
    sp.add_argument("--check", action="append")
    sp.set_defaults(func=cmd_add_task)

    sp = sub.add_parser("prompt")
    sp.add_argument("task")
    sp.add_argument("--file")
    sp.add_argument("--stdin", action="store_true")
    sp.add_argument("--edit", action="store_true")
    sp.set_defaults(func=cmd_prompt)

    sp = sub.add_parser("plan")
    sp.add_argument("--overview")
    sp.add_argument("--tranches", type=int, default=4)
    sp.add_argument("--run", action="store_true")
    sp.add_argument("--profile", default="opus-planner")
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(func=cmd_plan)

    for flow_name in ("complete-flow", "flow"):
        sp = sub.add_parser(flow_name, help="Guided init → prompt → plan → tranche run → merge-gated flow", description="Guided init → prompt → plan → tranche run → merge-gated flow")
        sp.add_argument("folder", nargs="?", default=".", help="Target project folder to initialize and run in")
        sp.add_argument("--name")
        sp.add_argument("--overwrite", action="store_true", help="Overwrite .agentops config/tasks when initializing/importing")
        sp.add_argument("--tranches", type=int, default=4)
        sp.add_argument("--prompt-file")
        sp.add_argument("--prompt")
        sp.add_argument("--plan-file", help="Import an existing planner JSON instead of generating one")
        sp.add_argument("--planner-profile", default="opus-planner")
        sp.add_argument("--offline-plan", action="store_true", help="Use deterministic local tranche generation without a model")
        sp.add_argument("--start", dest="start", action="store_true", help="Start running tranches after planning")
        sp.add_argument("--no-start", dest="start", action="store_false", help="Stop after planning")
        sp.set_defaults(start=None)
        sp.add_argument("--mode", default="headless", choices=["headless", "interactive"])
        sp.add_argument("--permission", default="workspace", choices=["workspace", "full"])
        sp.add_argument("--fallback", default="cascade", choices=["ask", "cascade", "antigravity", "codex", "gpt", "pause", "retry"])
        sp.add_argument("--fallback-on-any-failure", action="store_true", default=True)
        sp.add_argument("--auto-repair", action="store_true")
        sp.add_argument("--repair-attempts", type=int, default=1)
        sp.add_argument("--repair-profile", default="sonnet-repair")
        sp.add_argument("--clean-first", action="store_true")
        sp.add_argument("--no-pretty", action="store_true")
        sp.add_argument("--yes", action="store_true", help="Assume yes for init/start/merge gates")
        sp.set_defaults(func=cmd_complete_flow)

    sp = sub.add_parser("import-plan")
    sp.add_argument("file")
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(func=cmd_import_plan)

    sp = sub.add_parser("scout")
    sp.add_argument("task", nargs="?")
    sp.add_argument("--tranche")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--profile", default="local-scout")
    sp.set_defaults(func=cmd_scout)

    sp = sub.add_parser("run")
    sp.add_argument("task")
    sp.add_argument("--mode", default="headless", choices=["headless","interactive"])
    sp.add_argument("--permission", default="workspace", choices=["workspace","full"])
    sp.add_argument("--profile")
    sp.add_argument("--spawn", action="store_true")
    sp.add_argument("--terminal", default="auto")
    sp.add_argument("--no-pretty", action="store_true")
    sp.add_argument("--fallback", default="cascade", choices=["ask","cascade","antigravity","codex","gpt","pause","retry"])
    sp.add_argument("--fallback-on-any-failure", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("launch")
    sp.add_argument("--task", action="append")
    sp.add_argument("--tranche")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--spawn", action="store_true")
    sp.add_argument("--monitor", action="store_true")
    sp.add_argument("--mode", default="headless")
    sp.add_argument("--permission", default="workspace")
    sp.add_argument("--terminal", default="auto")
    sp.add_argument("--fallback", default="cascade")
    sp.add_argument("--fallback-on-any-failure", action="store_true")
    sp.add_argument("--clean-first", action="store_true")
    sp.add_argument("--max-parallel", type=int)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_launch)

    sub.add_parser("collect").set_defaults(func=cmd_collect)

    sp = sub.add_parser("merge")
    sp.add_argument("task", nargs="*")
    sp.add_argument("--tranche")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--auto-repair", action="store_true")
    sp.add_argument("--repair-attempts", type=int, default=1)
    sp.add_argument("--repair-profile", default="sonnet-repair")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("clean")
    sp.add_argument("task", nargs="*")
    sp.add_argument("--tranche")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--keep-branch", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--prune", action="store_true")
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("retry")
    sp.add_argument("task")
    sp.add_argument("--mode", default="headless")
    sp.add_argument("--permission", default="workspace")
    sp.add_argument("--no-pretty", action="store_true")
    sp.add_argument("--fallback", default="cascade")
    sp.add_argument("--fallback-on-any-failure", action="store_true")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_retry)

    sp = sub.add_parser("events")
    sp.add_argument("--tail", type=int, default=80)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_events)

    sub.add_parser("budget").set_defaults(func=cmd_budget)
    sub.add_parser("examples-index").set_defaults(func=cmd_examples_index)
    sub.add_parser("tui").set_defaults(func=cmd_tui)

    sp = sub.add_parser("rollback")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--ref")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_rollback)

    # New v4 commands
    sp = sub.add_parser("course", help="Generate or view task courses")
    s2 = sp.add_subparsers(dest="sub", required=True)
    gen = s2.add_parser("generate")
    gen.add_argument("--task", action="append")
    gen.add_argument("--tranche")
    gen.add_argument("--local", action="store_true", help="Prefer local models")
    view = s2.add_parser("view")
    serve = s2.add_parser("serve")
    serve.add_argument("--port", type=int, default=8080)
    sp.set_defaults(func=cmd_course)

    sp = sub.add_parser("setup-models", help="Set up local/free model providers")
    sp.add_argument("--full", action="store_true", help="Install larger models too")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_setup_models)

    sp = sub.add_parser("inspect", help="Inspect a task's current state")
    sp.add_argument("task")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("context", help="Show smart context for a task")
    sp.add_argument("task")
    sp.add_argument("--max-tokens", type=int, default=15000)
    sp.set_defaults(func=cmd_context)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
