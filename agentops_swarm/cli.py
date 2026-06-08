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

try:
    from . import __version__
except Exception:
    __version__ = "3.0.0"

ROOT = Path.cwd()
AGENTOPS_DIR = ROOT / ".agentops"
WORKTREE_DIR = ROOT / ".agent-worktrees"
ACTIVE_PATH = AGENTOPS_DIR / "active.json"
CONFIG_PATH = AGENTOPS_DIR / "config.json"
EVENTS_PATH = AGENTOPS_DIR / "events.jsonl"
BUDGET_PATH = AGENTOPS_DIR / "budget.json"
TASKS_DIR = AGENTOPS_DIR / "tasks"
REPORTS_DIR = AGENTOPS_DIR / "reports"
RUNTIME_DIR = AGENTOPS_DIR / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
SCOUTS_DIR = AGENTOPS_DIR / "scouts"
ROLLBACKS_DIR = AGENTOPS_DIR / "rollbacks"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PACKAGE_ROOT / "templates"

USAGE_LIMIT_RE = re.compile(
    r"usage limit|session limit|rate limit|too many requests|\b429\b|quota|limit reached|try again|reset|5.?hour|five.?hour|overloaded|temporarily unavailable",
    re.IGNORECASE,
)

DEFAULT_PROFILES: dict[str, dict[str, str]] = {
    "opus-planner": {"engine": "claude", "model": "opus", "role": "planner"},
    "haiku-scout": {"engine": "claude", "model": "haiku", "role": "scout"},
    "sonnet-executor": {"engine": "claude", "model": "sonnet", "role": "executor"},
    "sonnet-repair": {"engine": "claude", "model": "sonnet", "role": "repair"},
    "codex-verifier": {"engine": "codex", "model": "", "role": "verifier"},
    "gpt-codex": {"engine": "codex", "model": "", "role": "executor"},
    "antigravity-executor": {"engine": "antigravity", "model": "", "role": "executor"},
}

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
]


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
        "version": 3,
        "project": name,
        "defaults": {
            "mode": "headless",
            "permission": "workspace",
            "pretty": True,
            "fallback": "ask",
            "fallback_requires_confirmation": True,
            "auto_repair_requires_confirmation": True,
            "terminal": "auto",
            "max_parallel": 3,
        },
        "profiles": DEFAULT_PROFILES,
        "checks": DEFAULT_CHECKS,
    }


def ensure_init(name: str | None = None, force: bool = False) -> None:
    ensure_dirs()
    if force or not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, default_config(name or ROOT.name))
    if force or not ACTIVE_PATH.exists():
        save_json(ACTIVE_PATH, {"version": 3, "project": {"name": name or ROOT.name}, "tasks": []})
    (AGENTOPS_DIR / "examples" / "README.md").write_text(
        "# AgentOps Examples\n\nDrop screenshots, markdown briefs, HTML prototypes, sketches, and fake data here. Do not store secrets or private data. Run `agentops examples-index`.\n",
        encoding="utf-8",
    ) if not (AGENTOPS_DIR / "examples" / "README.md").exists() else None


def require_init() -> None:
    if not ACTIVE_PATH.exists() or not CONFIG_PATH.exists():
        fail("AgentOps is not initialized here. Run: agentops init --name <project>")


def active() -> dict[str, Any]:
    require_init()
    return load_json(ACTIVE_PATH, {"version": 3, "tasks": []})


def config() -> dict[str, Any]:
    require_init()
    cfg = load_json(CONFIG_PATH, default_config(ROOT.name))
    if "profiles" not in cfg:
        cfg["profiles"] = DEFAULT_PROFILES
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
        return profiles.get("sonnet-executor", DEFAULT_PROFILES["sonnet-executor"])
    if name not in profiles:
        fail(f"Unknown profile: {name}")
    return profiles[name]


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
    return f"""# AgentOps task: {t.get('id')}

Title: {t.get('title', t.get('id'))}
Priority: {t.get('priority', 'p1')}
Tranche: {t.get('tranche', 'unassigned')}
Executor profile: {profile_name}
Framework: {framework}

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

## Allowed paths

{allowed}

## Locked/high-risk paths

{locked}

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


def sync_agentops_to_worktree(task_id: str) -> None:
    wt = wt_for(task_id)
    if not wt.exists():
        return
    dst = wt / ".agentops"
    dst.mkdir(parents=True, exist_ok=True)
    # Copy task metadata without noisy runtime files.
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
    # Ensure prompt exists in worktree too.
    p = prompt_path(task_id)
    if not p.exists():
        task_prompt(task_id)
    (dst / "tasks").mkdir(exist_ok=True)
    if p.exists():
        shutil.copy2(p, dst / "tasks" / p.name)


def create_worktree(task_id: str, force: bool = False) -> Path:
    require_init()
    t = task_by_id(task_id)
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


def worker_command(task_id: str, mode: str, permission: str, profile_name: str | None = None, fallback: str = "ask", yes: bool = False) -> tuple[list[str], str, str]:
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
            # Claude accepts --model in recent CLI builds. If not, failure will be visible and fallback can kick in.
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
        # Syntax varies; support template, otherwise interactive agy in worktree.
        template = os.environ.get("AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE")
        if template and mode == "headless":
            prompt_file = wt / ".agentops" / f"{task_id}-prompt.md"
            prompt_file.write_text(prompt, encoding="utf-8")
            shell_cmd = template.replace("{prompt}", str(prompt_file)).replace("{worktree}", str(wt))
            return ["bash", "-lc", shell_cmd], engine, str(log_file)
        cmd = ["agy"]
    else:
        fail(f"Unsupported engine: {engine}")
    return cmd, engine, str(log_file)


def should_fallback(log_text: str, rc: int, on_any: bool) -> bool:
    return bool(rc != 0 and (on_any or USAGE_LIMIT_RE.search(log_text)))


def fallback_engine_choice(default: str, yes: bool) -> str:
    if default != "ask":
        return default
    if yes or not sys.stdin.isatty():
        return "pause"
    print("\nWorker failed or hit a model limit. Continue with fallback?")
    print("  1) Codex/GPT")
    print("  2) Antigravity")
    print("  3) Retry original later")
    print("  4) Pause")
    choice = input("Selection [1-4]: ").strip()
    return {"1": "codex", "2": "antigravity", "3": "retry", "4": "pause", "": "pause"}.get(choice, "pause")


def run_task_engine(task_id: str, mode: str = "headless", permission: str = "workspace", profile_name: str | None = None, pretty: bool = True, fallback: str = "ask", yes: bool = False, fallback_on_any: bool = False) -> int:
    ensure_dirs()
    cmd, engine, log_file_s = worker_command(task_id, mode, permission, profile_name)
    log_file = Path(log_file_s)
    run_id = write_budget_start(task_id, engine)
    event(task_id, "worker_start", f"starting {engine}", {"cmd": cmd, "log": str(log_file)})
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
    log_text = log_file.read_text(errors="ignore") if log_file.exists() else ""
    if should_fallback(log_text, rc, fallback_on_any):
        choice = fallback_engine_choice(fallback, yes)
        event(task_id, "fallback_choice", choice)
        if choice in {"codex", "gpt", "antigravity", "agy"}:
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
    else:
        event(task_id, "worker_failed", f"exit code {rc}")
        write_budget_end(run_id, f"failed:{rc}")
    return rc


def run_fallback(task_id: str, permission: str, engine: str, previous_log: Path, pretty: bool = True, yes: bool = False) -> int:
    t = task_by_id(task_id)
    original_executor = t.get("executor")
    original_profile = t.get("profile")
    if engine in {"codex", "gpt"}:
        t["executor"] = "codex"
        t["profile"] = "gpt-codex"
    elif engine in {"antigravity", "agy"}:
        t["executor"] = "antigravity"
        t["profile"] = "antigravity-executor"
    # Write continuation prompt into worktree.
    wt = create_worktree(task_id)
    sync_agentops_to_worktree(task_id)
    continuation = wt / ".agentops" / "fallback-continuation.md"
    prev_tail = previous_log.read_text(errors="ignore")[-8000:] if previous_log.exists() else ""
    continuation.write_text(
        f"""# Fallback continuation for {task_id}

The previous worker could not continue. Continue from the existing worktree and preserve all original scope/safety rules.

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
    # Temporarily override prompt file in root and worktree by setting a task prompt copy.
    p = prompt_path(task_id, wt)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(continuation.read_text(encoding="utf-8"), encoding="utf-8")
    # Override root prompt just for this invocation? Avoid; command builder reads root prompt. Pass temp by replacing root prompt backup.
    root_prompt = prompt_path(task_id)
    backup = root_prompt.read_text(encoding="utf-8") if root_prompt.exists() else None
    root_prompt.write_text(continuation.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        rc = run_task_engine(task_id, mode="headless", permission=permission, profile_name=t.get("profile"), pretty=pretty, fallback="pause", yes=True, fallback_on_any=False)
    finally:
        if backup is not None:
            root_prompt.write_text(backup, encoding="utf-8")
        t["executor"] = original_executor
        t["profile"] = original_profile
    return rc


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
    # Copy report to root.
    dest = report_path(task_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if r.exists():
        shutil.copy2(r, dest)


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
    # Nebula/default constellation
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
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(f"\033[1;{c}m╔══════════════════════════════════════════════════════════════════════════════╗\033[0m\n")
            sys.stdout.write(f"\033[1;{c}m║                          ✦ AGENTOPS SWARM ✦                                ║\033[0m\n")
            sys.stdout.write(f"\033[1;{c}m╠══════════════════════════════════════════════════════════════════════════════╣\033[0m\n")
            sys.stdout.write(f"║ Task       {task_id:<62}║\n")
            sys.stdout.write(f"║ Engine     {engine:<62}║\n")
            sys.stdout.write(f"║ Theme      {theme:<62}║\n")
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


def create_rollback_ref(label: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    ref = f"agentops/rollback/{label}-{ts}"
    run(["git", "branch", ref], check=False)
    event(None, "rollback_ref", ref)
    return ref


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

Fix the failing checks from the integrated branch. Do not add broad features. Do not weaken tests. Preserve security.

## Failure log

```text
{failure_log.read_text(errors='ignore')[-12000:]}
```

## Original task acceptance

{json.dumps(original.get('acceptance', []), indent=2)}

Write report to `.agentops/reports/{rid}/report.md`.
"""
        pp = prompt_path(rid)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(prompt, encoding="utf-8")
        rc = run_task_engine(rid, mode="headless", permission="workspace", profile_name=repair_profile, pretty=True, fallback="ask", yes=yes, fallback_on_any=True)
        if rc == 0:
            if merge_task(rid, auto_repair=False, run_task_checks=True, yes=yes):
                return True
    return False


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


# Commands

def cmd_init(args: argparse.Namespace) -> None:
    ensure_init(args.name, force=args.force)
    ok(f"AgentOps initialized for {args.name or ROOT.name}")


def cmd_doctor(args: argparse.Namespace) -> None:
    print(f"AgentOps {__version__}")
    print(f"Project: {ROOT}")
    for exe in ["git", "python3", "claude", "codex", "agy", "tmux", "gnome-terminal"]:
        path = shutil.which(exe)
        print(f"{exe:16} {path or 'missing'}")
    if ACTIVE_PATH.exists():
        print(f"tasks: {len(tasks())}")
    if not shutil.which("claude"):
        warn("Claude Code missing. Install/authenticate before using Claude profiles.")
    if not shutil.which("codex"):
        warn("Codex missing. Install/authenticate before using GPT/Codex fallback.")
    if not shutil.which("agy"):
        warn("Antigravity missing. Antigravity fallback will be unavailable.")


def cmd_profiles(args: argparse.Namespace) -> None:
    for k, v in config().get("profiles", {}).items():
        print(f"{k}: {json.dumps(v)}" if args.verbose else k)


def cmd_templates(args: argparse.Namespace) -> None:
    base = TEMPLATES_DIR
    if args.sub == "list":
        for p in sorted(base.rglob("*.md")):
            print(p.relative_to(base))


def cmd_list(args: argparse.Namespace) -> None:
    for t in tasks():
        wt = wt_for(t["id"])
        wt_state = "worktree" if wt.exists() else "no-worktree"
        dirty = "dirty" if wt.exists() and run(["git","status","--short"], cwd=wt, capture=True).stdout.strip() else ""
        rep = "report" if report_path(t["id"]).exists() or report_path(t["id"], wt).exists() else "no-report"
        print(f"{t['id']:<36} t{str(t.get('tranche','')):<4} {t.get('priority',''):<3} {t.get('executor',''):<12} {wt_state:<12} {dirty:<7} {rep}")


def cmd_status(args: argparse.Namespace) -> None:
    cmd_list(args)


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
    return f"""You are an Opus-level project planner for AgentOps Swarm.

Create a narrow, executable multi-agent DAG for this project. Output ONLY JSON with this schema:

{{
  "tasks": [
    {{
      "id": "kebab-case",
      "title": "short title",
      "tranche": 1,
      "priority": "p0|p1|p2",
      "executor": "claude|codex|antigravity",
      "profile": "sonnet-executor|gpt-codex|codex-verifier|antigravity-executor",
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
        cmd = ["claude", "--model", model, "-p", prompt_text]
        info("Running planner model")
        p = subprocess.run(cmd, text=True, capture_output=True)
        raw = p.stdout.strip() or p.stderr.strip()
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
        prof = profile(args.profile or "haiku-scout")
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
    data = load_json(BUDGET_PATH, {"runs": []})
    print(json.dumps(data, indent=2))


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


def tui_print_header() -> None:
    print(color("\n╔════════════════════════════════════════════════════╗", "96"))
    print(color("║              AGENTOPS SWARM CONTROL              ║", "96"))
    print(color("╚════════════════════════════════════════════════════╝", "96"))


def cmd_tui(args: argparse.Namespace) -> None:
    require_init()
    while True:
        tui_print_header()
        print("1) Status")
        print("2) Paste/edit task prompt")
        print("3) Generate DAG with planner model")
        print("4) Launch selected tasks")
        print("5) Merge selected tasks")
        print("6) Clean/retry tasks")
        print("7) Collect reports")
        print("8) Events")
        print("9) Budget")
        print("10) Examples index")
        print("q) Quit")
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
                    ns = argparse.Namespace(task=None, tranche=sel, all=False, spawn=spawn, monitor=True, mode="headless", permission="workspace", terminal="auto", fallback="codex" if yes else "ask", yes=yes, clean_first=False, max_parallel=None, fallback_on_any_failure=True)
                else:
                    ids = [x.strip() for x in sel.split(",") if x.strip()]
                    ns = argparse.Namespace(task=ids, tranche=None, all=False, spawn=spawn, monitor=True, mode="headless", permission="workspace", terminal="auto", fallback="codex" if yes else "ask", yes=yes, clean_first=False, max_parallel=None, fallback_on_any_failure=True)
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
                tid = input("Task id: ").strip()
                if confirm(f"Clean and retry {tid}?", default=False):
                    cmd_retry(argparse.Namespace(task=tid, mode="headless", permission="workspace", no_pretty=False, fallback="ask", yes=False, fallback_on_any_failure=True))
            elif choice == "7":
                cmd_collect(argparse.Namespace())
            elif choice == "8":
                cmd_events(argparse.Namespace(tail=60, json=False))
            elif choice == "9":
                cmd_budget(argparse.Namespace())
            elif choice == "10":
                examples_index()
            elif choice == "q":
                return
        except SystemExit as exc:
            warn(f"Command exited: {exc}")
        input("\nPress Enter...")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentops", description="AgentOps Swarm: multi-agent worktree orchestration")
    p.add_argument("--version", action="version", version=f"agentops {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init")
    sp.add_argument("--name")
    sp.add_argument("--force", action="store_true")
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
    sp.add_argument("--executor", default="claude", choices=["claude","codex","antigravity"])
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

    sp = sub.add_parser("import-plan")
    sp.add_argument("file")
    sp.add_argument("--overwrite", action="store_true")
    sp.set_defaults(func=cmd_import_plan)

    sp = sub.add_parser("scout")
    sp.add_argument("task", nargs="?")
    sp.add_argument("--tranche")
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--profile", default="haiku-scout")
    sp.set_defaults(func=cmd_scout)

    sp = sub.add_parser("run")
    sp.add_argument("task")
    sp.add_argument("--mode", default="headless", choices=["headless","interactive"])
    sp.add_argument("--permission", default="workspace", choices=["workspace","full"])
    sp.add_argument("--profile")
    sp.add_argument("--spawn", action="store_true")
    sp.add_argument("--terminal", default="auto")
    sp.add_argument("--no-pretty", action="store_true")
    sp.add_argument("--fallback", default="ask", choices=["ask","codex","gpt","antigravity","pause","retry"])
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
    sp.add_argument("--fallback", default="ask")
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
    sp.add_argument("--fallback", default="ask")
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

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
