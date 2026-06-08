#!/usr/bin/env python3
"""AgentOps Swarm: reusable local multi-agent orchestration for Claude Code, Codex, and Antigravity.

Design constraints:
- Work is isolated in git worktrees.
- Agents may write branches; only merge gates integrate.
- Secrets and runtime data are excluded from prompts and path grants by policy.
- The CLI is intentionally filesystem-first; no daemon required.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VERSION = "2.1.0"
APP_DIR = ".agentops"
WT_DIR = ".agent-worktrees"
FORBIDDEN_FRAGMENTS = (
    ".env", "secrets", "backup", "backups", "logs", "token", "credentials", "client_secret",
    "private_key", "id_rsa", "id_ed25519", ".pem", ".p12", ".pfx", "oauth", "refresh"
)
DEFAULT_CHECKS = [
    "python3 -m pytest tests -q",
    "./scripts/check-secrets.sh",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=start, text=True).strip()
        return Path(out).resolve()
    except Exception:
        return start


def app_root() -> Path:
    return repo_root() / APP_DIR


def worktree_root() -> Path:
    return repo_root() / WT_DIR


def ensure_dirs() -> None:
    root = app_root()
    for sub in ["tasks", "reports", "scouts", "events", "planner", "examples", "budgets", "logs"]:
        (root / sub).mkdir(parents=True, exist_ok=True)
    worktree_root().mkdir(parents=True, exist_ok=True)


def run(cmd: list[str] | str, cwd: Path | None = None, check: bool = True, capture: bool = False, shell: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or repo_root()), check=check, text=True, capture_output=capture, shell=shell)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def active_path() -> Path:
    return app_root() / "active.json"


def config_path() -> Path:
    return app_root() / "config.json"


def load_active() -> dict[str, Any]:
    return read_json(active_path(), {"version": 1, "project": repo_root().name, "tasks": []})


def save_active(data: dict[str, Any]) -> None:
    write_json(active_path(), data)


def load_config() -> dict[str, Any]:
    return read_json(config_path(), default_config())


def save_config(data: dict[str, Any]) -> None:
    write_json(config_path(), data)


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "project_root": str(repo_root()),
        "terminal": "auto",
        "default_permission": "workspace",
        "model_profiles": {
            "opus-planner": {"engine": "claude", "model": os.environ.get("AGENTOPS_OPUS_MODEL", "opus"), "role": "planner"},
            "haiku-scout": {"engine": "claude", "model": os.environ.get("AGENTOPS_HAIKU_MODEL", "haiku"), "role": "scout"},
            "sonnet-executor": {"engine": "claude", "model": os.environ.get("AGENTOPS_SONNET_MODEL", "sonnet"), "role": "executor"},
            "sonnet-repair": {"engine": "claude", "model": os.environ.get("AGENTOPS_SONNET_MODEL", "sonnet"), "role": "repair"},
            "codex-verifier": {"engine": "codex", "model": os.environ.get("AGENTOPS_CODEX_MODEL", ""), "role": "verifier"},
            "antigravity-executor": {"engine": "antigravity", "model": os.environ.get("AGENTOPS_AGY_MODEL", ""), "role": "executor"},
        },
        "checks": [],
        "forbidden_fragments": list(FORBIDDEN_FRAGMENTS),
        "budget": {"currency": "token-minutes", "max_parallel_workers": 4, "sprint_minutes": 60},
    }


def event_path() -> Path:
    return app_root() / "events.jsonl"


def log_event(kind: str, **payload: Any) -> None:
    ensure_dirs()
    record = {"ts": utc_now(), "kind": kind, **payload}
    with event_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def budget_path() -> Path:
    return app_root() / "budget.json"


def budget_load() -> dict[str, Any]:
    return read_json(budget_path(), {"runs": [], "total_seconds": 0, "by_task": {}})


def budget_save(data: dict[str, Any]) -> None:
    write_json(budget_path(), data)


def budget_start(task_id: str, engine: str, mode: str) -> str:
    rid = f"run-{uuid.uuid4().hex[:12]}"
    data = budget_load()
    data["runs"].append({"id": rid, "task_id": task_id, "engine": engine, "mode": mode, "started_at": utc_now(), "ended_at": None, "seconds": None})
    budget_save(data)
    return rid


def budget_end(run_id: str) -> None:
    data = budget_load()
    now = time.time()
    for r in data.get("runs", []):
        if r.get("id") == run_id and r.get("ended_at") is None:
            started = datetime.fromisoformat(r["started_at"]).timestamp()
            seconds = max(0, int(now - started))
            r["ended_at"] = utc_now()
            r["seconds"] = seconds
            data["total_seconds"] = int(data.get("total_seconds", 0)) + seconds
            by = data.setdefault("by_task", {})
            by[r["task_id"]] = int(by.get(r["task_id"], 0)) + seconds
            break
    budget_save(data)


def branch_name(task_id: str) -> str:
    return f"agent/{task_id}"


def task_by_id(task_id: str) -> dict[str, Any]:
    active = load_active()
    for task in active.get("tasks", []):
        if task.get("id") == task_id:
            return task
    raise SystemExit(f"Unknown task: {task_id}")


def task_worktree(task_id: str) -> Path:
    return worktree_root() / task_id


def path_forbidden(p: str) -> bool:
    low = p.lower().replace("\\", "/")
    # allow design tokens, tokenization, and tokenizer source paths; block secret-like token files.
    allowed_words = ["design/tokens", "tokenizer", "tokenization", "tokens.ts", "tokens.scss"]
    if any(a in low for a in allowed_words):
        return False
    return any(x in low for x in FORBIDDEN_FRAGMENTS)


def check_task_safety(task: dict[str, Any]) -> list[str]:
    issues = []
    for field in ("allowed_paths", "locked_paths"):
        for p in task.get(field, []) or []:
            if path_forbidden(str(p)):
                issues.append(f"{task.get('id')} {field} contains forbidden path fragment: {p}")
    return issues


def template_dir() -> Path:
    # When installed, templates live beside this package in the repo distribution.
    candidates = [
        Path(__file__).resolve().parents[1] / "templates",
        Path(__file__).resolve().parent.parent / "templates",
        Path.cwd() / "agentops-swarm" / "templates",
        Path.home() / ".local" / "share" / "agentops-swarm" / "templates",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def role_template(profile: str) -> str:
    path = template_dir() / "roles" / f"{profile}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def framework_template(name: str | None) -> str:
    if not name:
        name = "generic"
    path = template_dir() / "frameworks" / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def write_task_prompt(task: dict[str, Any]) -> Path:
    ensure_dirs()
    tid = task["id"]
    profile = task.get("profile") or task.get("model_profile") or "sonnet-executor"
    framework = task.get("framework", "generic")
    prompt = f"""
# AgentOps Task: {tid}

## Role
{role_template(profile)}

## Framework Guidance
{framework_template(framework)}

## Task
Title: {task.get('title', tid)}
Priority: {task.get('priority', 'p1')}
Executor: {task.get('executor', 'claude')}

## Allowed paths
{json.dumps(task.get('allowed_paths', []), indent=2)}

## Locked paths
{json.dumps(task.get('locked_paths', []), indent=2)}

## Acceptance criteria
{json.dumps(task.get('acceptance', []), indent=2, ensure_ascii=False)}

## Checks
{json.dumps(task.get('checks', []), indent=2, ensure_ascii=False)}

## Safety
- Do not read, print, modify, or commit .env, .env.*, secrets/, credentials, tokens, private keys, backups/, data/, logs/, or personal data.
- Do not perform destructive actions.
- Do not push to remote.
- Keep changes bounded to the task.
- Write a report to `.agentops/reports/{tid}/report.md` before finishing.

## Examples / reference material
If `.agentops/examples/GENERATED_INDEX.md` exists, read it and inspect only relevant example files. Use visual/material examples as inspiration/specification, not as copied assets unless the user owns them.

## Final report format
Write:
- status
- root cause / implementation summary
- files changed
- tests run
- results
- risks
- follow-up tasks
""".strip() + "\n"
    path = app_root() / "tasks" / f"{tid}.prompt.md"
    path.write_text(prompt, encoding="utf-8")
    return path


def cmd_init(args: argparse.Namespace) -> None:
    ensure_dirs()
    cfg = default_config()
    cfg["project_name"] = args.name or repo_root().name
    cfg["project_root"] = str(repo_root())
    if config_path().exists() and not args.force:
        print(f"{config_path()} already exists. Use --force to overwrite.")
    else:
        save_config(cfg)
    if not active_path().exists() or args.force:
        save_active({"version": 1, "project": cfg["project_name"], "created_at": utc_now(), "tasks": []})
    (app_root() / "examples" / "README.md").write_text(EXAMPLES_README, encoding="utf-8")
    log_event("init", project=cfg["project_name"], root=str(repo_root()))
    print(f"Initialized AgentOps in {app_root()}")


def cmd_doctor(args: argparse.Namespace) -> None:
    root = repo_root()
    print(f"AgentOps Swarm {VERSION}")
    print(f"Root: {root}")
    for name, cmd in [
        ("git", ["git", "--version"]),
        ("python", [sys.executable, "--version"]),
        ("claude", ["claude", "--version"]),
        ("codex", ["codex", "--version"]),
        ("agy", ["agy", "--version"]),
        ("antigravity", ["antigravity", "--version"]),
        ("tmux", ["tmux", "-V"]),
    ]:
        exe = shutil.which(cmd[0])
        if not exe:
            print(f"✗ {name}: not found")
            continue
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip().splitlines()[0]
            print(f"✓ {name}: {out}")
        except Exception as e:
            print(f"? {name}: found at {exe}, version check failed: {e}")
    if not app_root().exists():
        print("✗ .agentops not initialized. Run: agentops init")
    else:
        print("✓ .agentops initialized")
    issues = []
    for task in load_active().get("tasks", []):
        issues.extend(check_task_safety(task))
    if issues:
        print("Safety issues:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✓ task path grants look safe")


def cmd_add_task(args: argparse.Namespace) -> None:
    ensure_dirs()
    active = load_active()
    task = {
        "id": args.id,
        "title": args.title or args.id,
        "tranche": args.tranche,
        "priority": args.priority,
        "executor": args.executor,
        "profile": args.profile,
        "framework": args.framework,
        "allowed_paths": args.allowed_path or [],
        "locked_paths": args.locked_path or [],
        "acceptance": args.acceptance or [],
        "checks": args.check or [],
        "branch": branch_name(args.id),
    }
    issues = check_task_safety(task)
    if issues and not args.force:
        raise SystemExit("Refusing unsafe task:\n" + "\n".join(issues) + "\nUse --force only if you understand the risk.")
    active["tasks"] = [t for t in active.get("tasks", []) if t.get("id") != args.id] + [task]
    save_active(active)
    prompt = write_task_prompt(task)
    log_event("task.added", task_id=args.id, tranche=args.tranche, executor=args.executor)
    print(f"Added task {args.id}")
    print(f"Prompt: {prompt}")


def cmd_list(args: argparse.Namespace) -> None:
    active = load_active()
    for task in sorted(active.get("tasks", []), key=lambda t: (t.get("tranche", 999), t.get("priority", "p9"), t.get("id", ""))):
        wt = task_worktree(task["id"])
        wt_state = "worktree" if wt.exists() else "no-worktree"
        report = app_root() / "reports" / task["id"] / "report.md"
        print(f"{task['id']:<32} t{task.get('tranche','?'):<3} {task.get('priority',''):<3} {task.get('executor',''):<12} {wt_state:<12} {'report' if report.exists() else 'no-report'}")


def cmd_status(args: argparse.Namespace) -> None:
    active = load_active()
    for task in active.get("tasks", []):
        tid = task["id"]
        wt = task_worktree(tid)
        status = "no-worktree"
        dirty = ""
        if wt.exists():
            status = "worktree"
            try:
                out = subprocess.check_output(["git", "status", "--short"], cwd=wt, text=True)
                dirty = "dirty" if out.strip() else "clean"
            except Exception:
                dirty = "?"
        report_root = app_root() / "reports" / tid / "report.md"
        report_wt = wt / APP_DIR / "reports" / tid / "report.md"
        report = "report" if report_root.exists() or report_wt.exists() else "no-report"
        print(f"{tid:<32} {task.get('priority',''):<3} {status:<12} {dirty:<7} {report:<10} {branch_name(tid)}")


def cmd_create_worktree(args: argparse.Namespace) -> None:
    task = task_by_id(args.task_id)
    wt = task_worktree(task["id"])
    branch = branch_name(task["id"])
    if wt.exists() and not args.force:
        print(f"Worktree already exists: {wt}")
        return
    if wt.exists():
        run(["git", "worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(wt, ignore_errors=True)
    run(["git", "branch", "-D", branch], check=False)
    run(["git", "worktree", "add", str(wt), "-b", branch])
    write_task_prompt(task)
    log_event("worktree.created", task_id=task["id"], path=str(wt), branch=branch)
    print(f"Created {wt} on {branch}")


def cmd_clean(args: argparse.Namespace) -> None:
    ids = args.task_id or [t["id"] for t in load_active().get("tasks", []) if args.tranche is None or t.get("tranche") == args.tranche]
    for tid in ids:
        wt = task_worktree(tid)
        br = branch_name(tid)
        run(["git", "worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(wt, ignore_errors=True)
        if args.branches:
            run(["git", "branch", "-D", br], check=False)
        if args.reports:
            shutil.rmtree(app_root() / "reports" / tid, ignore_errors=True)
        log_event("task.cleaned", task_id=tid)
        print(f"Cleaned {tid}")
    run(["git", "worktree", "prune"], check=False)


def engine_for_task(task: dict[str, Any]) -> str:
    profile = task.get("profile")
    cfg = load_config()
    if profile and profile in cfg.get("model_profiles", {}):
        return cfg["model_profiles"][profile].get("engine") or task.get("executor", "claude")
    return task.get("executor", "claude")


def model_for_profile(profile: str | None) -> str:
    if not profile:
        return ""
    return str(load_config().get("model_profiles", {}).get(profile, {}).get("model", ""))


def prompt_file_for_task(task_id: str) -> Path:
    task = task_by_id(task_id)
    path = app_root() / "tasks" / f"{task_id}.prompt.md"
    if not path.exists():
        path = write_task_prompt(task)
    return path


def command_for_task(task: dict[str, Any], mode: str, permission: str) -> list[str]:
    engine = engine_for_task(task)
    tid = task["id"]
    wt = task_worktree(tid)
    prompt = prompt_file_for_task(tid)
    return [sys.executable, "-m", "agentops_swarm.cli", "_run-task", tid, "--engine", engine, "--mode", mode, "--permission", permission, "--worktree", str(wt), "--prompt", str(prompt)]


def agy_executable() -> str | None:
    return shutil.which("agy") or shutil.which("antigravity")


def cmd_run_task_internal(args: argparse.Namespace) -> None:
    tid = args.task_id
    engine = args.engine
    mode = args.mode
    permission = args.permission
    wt = Path(args.worktree)
    prompt = Path(args.prompt)
    wt.mkdir(parents=True, exist_ok=True)
    report_dir = wt / APP_DIR / "reports" / tid
    report_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(wt)
    log_event("task.run.start", task_id=tid, engine=engine, mode=mode, permission=permission)
    rid = budget_start(tid, engine, mode)
    rc = 0
    try:
        prompt_text = prompt.read_text(encoding="utf-8")
        if engine == "claude":
            profile = task_by_id(tid).get("profile", "sonnet-executor")
            model = model_for_profile(profile)
            cmd = ["claude"]
            if model:
                cmd += ["--model", model]
            if mode == "headless":
                if permission == "full":
                    cmd += ["--permission-mode", "bypassPermissions"]
                cmd += ["-p", prompt_text]
                rc = subprocess.call(cmd)
            else:
                print(f"Paste or ask Claude to read: {prompt}")
                if permission == "full":
                    cmd += ["--permission-mode", "bypassPermissions"]
                rc = subprocess.call(cmd)
        elif engine == "codex":
            cmd = ["codex"]
            if mode == "headless":
                cmd.append("exec")
                if permission == "full":
                    cmd.append("--dangerously-bypass-approvals-and-sandbox")
                else:
                    cmd += ["--sandbox", "workspace-write"]
                cmd += ["--cd", str(wt), prompt_text]
                rc = subprocess.call(cmd)
            else:
                print(f"Paste or ask Codex to read: {prompt}")
                if permission == "full":
                    cmd.append("--dangerously-bypass-approvals-and-sandbox")
                else:
                    cmd += ["--sandbox", "workspace-write"]
                cmd += ["--cd", str(wt)]
                rc = subprocess.call(cmd)
        elif engine == "antigravity":
            exe = agy_executable()
            if not exe:
                print("Antigravity CLI not found. Expected `agy` or `antigravity`.")
                rc = 127
            elif mode == "headless":
                # AGY is primarily a TUI. Try a practical stdin-driven /goal invocation.
                # Users can override with AGENTOPS_ANTIGRAVITY_COMMAND_TEMPLATE.
                tmpl = os.environ.get("AGENTOPS_ANTIGRAVITY_COMMAND_TEMPLATE")
                if tmpl:
                    command = tmpl.replace("{prompt}", prompt_text).replace("{worktree}", str(wt)).replace("{prompt_file}", str(prompt))
                    rc = subprocess.call(command, shell=True, cwd=wt)
                else:
                    print("Launching Antigravity TUI with /goal piped. If your AGY version requires interactive auth, use --mode interactive.")
                    p = subprocess.Popen([exe], cwd=wt, stdin=subprocess.PIPE, text=True)
                    assert p.stdin is not None
                    p.stdin.write("/goal " + prompt_text.replace("\n", " ") + "\n")
                    p.stdin.close()
                    rc = p.wait()
            else:
                print(f"Starting Antigravity. Paste or use /goal with: {prompt}")
                rc = subprocess.call([exe], cwd=wt)
        else:
            print(f"Unknown engine: {engine}")
            rc = 2
    finally:
        budget_end(rid)
    if rc != 0:
        report = report_dir / "report.md"
        report.write_text(f"# {tid} report\n\nStatus: FAILED\n\nAgent exited with code {rc}.\n\n", encoding="utf-8")
        log_event("task.run.failed", task_id=tid, rc=rc)
        raise SystemExit(rc)
    report = report_dir / "report.md"
    if not report.exists():
        report.write_text(f"# {tid} report\n\nStatus: COMPLETED_WITHOUT_EXPLICIT_REPORT\n\n", encoding="utf-8")
    if subprocess.call(["git", "status", "--short"], stdout=subprocess.PIPE, text=True) == 0:
        out = subprocess.check_output(["git", "status", "--short"], text=True)
        if out.strip():
            subprocess.call(["git", "add", "."])
            subprocess.call(["git", "commit", "-m", f"Agent task: {tid}"])
    log_event("task.run.complete", task_id=tid, engine=engine)


def spawn_terminal(title: str, cmd: list[str], terminal: str = "auto") -> None:
    command = " ".join(shlex_quote(c) for c in cmd)
    root = repo_root()
    wrapped = f"cd {shlex_quote(str(root))} && {command}; echo; read -r -p 'Press Enter to close...'"
    log_event("terminal.spawn", title=title, terminal=terminal, command=command)
    if terminal == "current":
        subprocess.call(wrapped, shell=True)
        return
    if terminal == "tmux" or (terminal == "auto" and not any(shutil.which(x) for x in ["gnome-terminal", "x-terminal-emulator", "kgx", "konsole", "xfce4-terminal", "kitty", "alacritty", "xterm"])):
        if not shutil.which("tmux"):
            print("No GUI terminal and tmux not found. Command:")
            print(wrapped)
            return
        subprocess.call(["tmux", "new-session", "-d", "-s", "agentops"], stderr=subprocess.DEVNULL)
        subprocess.call(["tmux", "new-window", "-t", "agentops", "-n", title[:18], "bash", "-lc", wrapped])
        print("Started tmux window. Attach: tmux attach -t agentops")
        return
    options = []
    if terminal in ("auto", "gnome") and shutil.which("gnome-terminal"):
        options = ["gnome-terminal", "--title", title, "--", "bash", "-lc", wrapped]
    elif terminal in ("auto", "x-terminal-emulator") and shutil.which("x-terminal-emulator"):
        options = ["x-terminal-emulator", "-T", title, "-e", "bash", "-lc", wrapped]
    elif terminal in ("auto", "kgx") and shutil.which("kgx"):
        options = ["kgx", "--title", title, "--", "bash", "-lc", wrapped]
    elif terminal in ("auto", "konsole") and shutil.which("konsole"):
        options = ["konsole", "--new-tab", "--title", title, "-e", "bash", "-lc", wrapped]
    elif terminal in ("auto", "xfce4-terminal") and shutil.which("xfce4-terminal"):
        options = ["xfce4-terminal", "--title", title, "--command", f"bash -lc {shlex_quote(wrapped)}"]
    elif terminal in ("auto", "kitty") and shutil.which("kitty"):
        options = ["kitty", "--title", title, "bash", "-lc", wrapped]
    elif terminal in ("auto", "alacritty") and shutil.which("alacritty"):
        options = ["alacritty", "--title", title, "-e", "bash", "-lc", wrapped]
    elif terminal in ("auto", "xterm") and shutil.which("xterm"):
        options = ["xterm", "-T", title, "-e", "bash", "-lc", wrapped]
    else:
        print("No supported terminal found. Command:")
        print(wrapped)
        return
    subprocess.Popen(options)


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def selected_tasks(tranche: int | None = None, task_ids: list[str] | None = None) -> list[dict[str, Any]]:
    tasks = load_active().get("tasks", [])
    if task_ids:
        wanted = set(task_ids)
        tasks = [t for t in tasks if t.get("id") in wanted]
    if tranche is not None:
        tasks = [t for t in tasks if int(t.get("tranche", 0)) == tranche]
    return tasks


def cmd_launch(args: argparse.Namespace) -> None:
    tasks = selected_tasks(args.tranche, args.task_id)
    for task in tasks:
        if not task_worktree(task["id"]).exists():
            cmd_create_worktree(argparse.Namespace(task_id=task["id"], force=False))
    for task in tasks:
        cmd = command_for_task(task, args.mode, args.permission)
        if args.spawn:
            spawn_terminal(f"agent:{task['id']}", cmd, args.terminal)
        else:
            print(" ".join(shlex_quote(c) for c in cmd))
    if args.monitor:
        monitor_cmd = [sys.executable, "-m", "agentops_swarm.cli", "tui"] if args.tui else [sys.executable, "-m", "agentops_swarm.cli", "status"]
        if args.spawn:
            spawn_terminal("agentops:monitor", monitor_cmd, args.terminal)


def cmd_run_task(args: argparse.Namespace) -> None:
    task = task_by_id(args.task_id)
    if not task_worktree(args.task_id).exists():
        cmd_create_worktree(argparse.Namespace(task_id=args.task_id, force=False))
    engine = args.engine or engine_for_task(task)
    cmd = [sys.executable, "-m", "agentops_swarm.cli", "_run-task", args.task_id, "--engine", engine, "--mode", args.mode, "--permission", args.permission, "--worktree", str(task_worktree(args.task_id)), "--prompt", str(prompt_file_for_task(args.task_id))]
    if args.spawn:
        spawn_terminal(f"agent:{args.task_id}", cmd, args.terminal)
    else:
        raise SystemExit(subprocess.call(cmd))


def cmd_scout(args: argparse.Namespace) -> None:
    tasks = selected_tasks(args.tranche, [args.task_id] if args.task_id else None)
    profile = args.profile or "haiku-scout"
    model = model_for_profile(profile)
    for task in tasks:
        tid = task["id"]
        scout_file = app_root() / "scouts" / f"{tid}.md"
        prompt = f"""
You are a read-only scout for AgentOps.
Task: {tid} - {task.get('title','')}
Allowed paths: {task.get('allowed_paths', [])}
Acceptance criteria: {task.get('acceptance', [])}

Inspect the repository without editing. Produce:
1. relevant files
2. likely implementation points
3. risks/conflicts
4. tests to run
5. suggested executor instructions

Write the report to: {scout_file}
Do not read .env, secrets, data, backups, logs, credentials, or tokens.
""".strip()
        cmd = ["claude"]
        if model:
            cmd += ["--model", model]
        cmd += ["-p", prompt]
        log_event("scout.start", task_id=tid, profile=profile)
        rc = subprocess.call(cmd, cwd=repo_root())
        log_event("scout.complete" if rc == 0 else "scout.failed", task_id=tid, rc=rc)
        print(f"Scout requested for {tid}. Report should be: {scout_file}")


def run_project_checks(label: str, extra: list[str] | None = None) -> tuple[bool, Path]:
    ensure_dirs()
    log = app_root() / "reports" / "auto-repair" / f"{label}-{int(time.time())}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    checks = extra or cfg.get("checks") or DEFAULT_CHECKS
    success = True
    with log.open("w", encoding="utf-8") as f:
        for check in checks:
            f.write(f"$ {check}\n")
            f.flush()
            proc = subprocess.run(check, cwd=repo_root(), shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            f.write(proc.stdout)
            f.write(f"\n[exit {proc.returncode}]\n")
            f.flush()
            if proc.returncode != 0:
                success = False
                break
    return success, log


def create_repair_task(label: str, failed_log: Path, failed_command: str, profile: str) -> str:
    tid = f"auto-repair-{label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    task = {
        "id": tid,
        "title": f"Auto repair {label}",
        "tranche": 999,
        "priority": "p0",
        "executor": "claude",
        "profile": profile,
        "framework": "qa",
        "allowed_paths": ["."],
        "locked_paths": [],
        "acceptance": ["Fix concrete failing checks", "Do not broaden scope"],
        "checks": [],
        "branch": branch_name(tid),
    }
    active = load_active()
    active["tasks"].append(task)
    save_active(active)
    prompt = f"""
# Auto repair task: {label}

Fix only the concrete failure shown below. Do not add broad features. Do not weaken tests unless the test is clearly wrong; if changed, preserve the intended invariant.

Failed command/check label: {failed_command}

Failure log excerpt:
```text
{failed_log.read_text(encoding='utf-8', errors='replace')[-12000:]}
```

Safety:
- Do not read or modify .env, secrets, data, backups, logs, credentials, tokens, or private keys.
- Do not push.
- Keep changes focused.
- Write report to `.agentops/reports/{tid}/report.md`.
""".strip() + "\n"
    p = app_root() / "tasks" / f"{tid}.prompt.md"
    p.write_text(prompt, encoding="utf-8")
    cmd_create_worktree(argparse.Namespace(task_id=tid, force=False))
    return tid


def cmd_merge(args: argparse.Namespace) -> None:
    tasks = selected_tasks(args.tranche, args.task_id)
    for task in tasks:
        tid = task["id"]
        wt = task_worktree(tid)
        branch = branch_name(tid)
        if wt.exists():
            out = subprocess.check_output(["git", "status", "--short"], cwd=wt, text=True)
            if out.strip():
                subprocess.call(["git", "add", "."], cwd=wt)
                subprocess.call(["git", "commit", "-m", f"Agent task: {tid}"], cwd=wt)
        current = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
        diff = subprocess.call(["git", "diff", "--quiet", f"{current}..{branch}"], cwd=repo_root())
        if diff == 0:
            print(f"No changes to merge for {tid}")
            continue
        print(f"Merging {branch}")
        subprocess.check_call(["git", "merge", "--no-ff", branch, "-m", f"Merge agent task: {tid}"], cwd=repo_root())
        if args.checks:
            ok, log = run_project_checks(tid)
            attempt = 0
            while not ok and args.auto_repair and attempt < args.repair_attempts:
                print(f"Checks failed after {tid}; spawning repair node. Log: {log}")
                repair_tid = create_repair_task(tid, log, "project checks", args.repair_profile)
                subprocess.check_call(command_for_task(task_by_id(repair_tid), "headless", args.permission), cwd=repo_root())
                subprocess.check_call(["git", "merge", "--no-ff", branch_name(repair_tid), "-m", f"Merge auto repair for {tid}"], cwd=repo_root())
                ok, log = run_project_checks(f"{tid}-repair-{attempt}")
                attempt += 1
            if not ok:
                raise SystemExit(f"Checks failed after {tid}. See {log}")
    collect_reports()


def collect_reports() -> None:
    ensure_dirs()
    report_root = app_root() / "reports"
    for wt in worktree_root().glob("*"):
        src = wt / APP_DIR / "reports"
        if not src.exists():
            continue
        for report in src.glob("*/report.md"):
            dest = report_root / report.parent.name / "report.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, dest)
    lines = ["# AgentOps tranche report", "", f"Generated: {utc_now()}", ""]
    for task in load_active().get("tasks", []):
        tid = task["id"]
        rp = report_root / tid / "report.md"
        status = "report" if rp.exists() else "no-report"
        lines.append(f"- `{tid}`: {status}")
    out = report_root / "TRANCHE_REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)
    log_event("reports.collected", path=str(out))


def cmd_collect(args: argparse.Namespace) -> None:
    collect_reports()


def cmd_events(args: argparse.Namespace) -> None:
    if not event_path().exists():
        return
    lines = event_path().read_text(encoding="utf-8").splitlines()[-args.tail:]
    for line in lines:
        if args.json:
            print(line)
        else:
            try:
                obj = json.loads(line)
                print(f"{obj.get('ts')} {obj.get('kind')} { {k:v for k,v in obj.items() if k not in ('ts','kind')} }")
            except Exception:
                print(line)


def cmd_budget(args: argparse.Namespace) -> None:
    data = budget_load()
    print(f"Total seconds: {data.get('total_seconds', 0)}")
    print("By task:")
    for tid, sec in sorted(data.get("by_task", {}).items(), key=lambda x: -x[1]):
        print(f"  {tid:<40} {sec:>8}s")
    print("Recent runs:")
    for r in data.get("runs", [])[-20:]:
        print(f"  {r.get('task_id'):<32} {r.get('engine',''):<12} {r.get('seconds')}s {r.get('started_at')}")


def cmd_profiles(args: argparse.Namespace) -> None:
    profiles = load_config().get("model_profiles", {})
    for name, prof in profiles.items():
        if args.verbose:
            print(f"{name}: {json.dumps(prof, ensure_ascii=False)}")
        else:
            print(f"{name:<22} {prof.get('engine',''):<12} {prof.get('model','')}")


def cmd_templates(args: argparse.Namespace) -> None:
    if args.sub == "list":
        td = template_dir()
        for p in sorted(td.glob("**/*.md")):
            print(p.relative_to(td))


def cmd_plan(args: argparse.Namespace) -> None:
    ensure_dirs()
    overview = Path(args.overview).read_text(encoding="utf-8")
    profile = args.profile or "opus-planner"
    model = model_for_profile(profile)
    planner_prompt = f"""
You are an AgentOps planner. Create an implementation DAG for this project.

Overview:
```text
{overview}
```

Return ONLY valid JSON with this shape:
{{
  "version": 1,
  "project": "{repo_root().name}",
  "tasks": [
    {{
      "id": "short-kebab-id",
      "title": "task title",
      "tranche": 1,
      "priority": "p0|p1|p2",
      "executor": "claude|codex|antigravity",
      "profile": "sonnet-executor|codex-verifier|antigravity-executor",
      "framework": "generic|vue-quasar|react|fastapi|python|docker-compose|ci|docs|qa",
      "allowed_paths": ["..."],
      "locked_paths": ["..."],
      "acceptance": ["..."],
      "checks": ["..."]
    }}
  ]
}}

Constraints:
- Create up to {args.tranches} tranches.
- Keep tasks narrow and mergeable.
- Do not grant secret/runtime paths.
- Include at least one QA/verifier task per major tranche.
""".strip()
    out_path = app_root() / "planner" / f"plan-{int(time.time())}.json"
    cmd = ["claude"]
    if model:
        cmd += ["--model", model]
    cmd += ["-p", planner_prompt]
    if args.run:
        log_event("plan.start", profile=profile)
        proc = subprocess.run(cmd, cwd=repo_root(), text=True, capture_output=True)
        raw = proc.stdout.strip()
        (app_root() / "planner" / "last.raw.txt").write_text(raw, encoding="utf-8")
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            data = json.loads(raw[start:end])
            for task in data.get("tasks", []):
                task.setdefault("branch", branch_name(task["id"]))
            save_active(data)
            for task in data.get("tasks", []):
                write_task_prompt(task)
            write_json(out_path, data)
            print(f"Plan saved: {out_path}")
            log_event("plan.complete", path=str(out_path), task_count=len(data.get("tasks", [])))
        except Exception as e:
            print(raw)
            raise SystemExit(f"Planner output was not valid JSON: {e}")
    else:
        print(" ".join(shlex_quote(c) for c in cmd))


def cmd_tui(args: argparse.Namespace) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.live import Live
        from rich.text import Text
    except ImportError:
        print("Rich is not installed. Run: pip install rich")
        return
    console = Console()
    def render():
        table = Table(title="AgentOps Swarm")
        table.add_column("Task")
        table.add_column("Tranche")
        table.add_column("Executor")
        table.add_column("Worktree")
        table.add_column("Dirty")
        table.add_column("Report")
        for task in load_active().get("tasks", []):
            tid = task["id"]
            wt = task_worktree(tid)
            state = "yes" if wt.exists() else "no"
            dirty = ""
            if wt.exists():
                try:
                    dirty = "dirty" if subprocess.check_output(["git", "status", "--short"], cwd=wt, text=True).strip() else "clean"
                except Exception:
                    dirty = "?"
            report = "yes" if (app_root()/"reports"/tid/"report.md").exists() else "no"
            table.add_row(tid, str(task.get("tranche", "")), task.get("executor", ""), state, dirty, report)
        return Panel(table, title="r/Enter refresh · l list · c collect · b budget · q quit")
    console.clear()
    console.print(render())
    while True:
        try:
            ch = input("agentops> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if ch == "q":
            break
        if ch == "c":
            collect_reports()
        if ch == "b":
            cmd_budget(argparse.Namespace())
        console.clear(); console.print(render())


def cmd_examples_index(args: argparse.Namespace) -> None:
    base = app_root() / "examples"
    base.mkdir(parents=True, exist_ok=True)
    lines = ["# AgentOps examples index", "", f"Generated: {utc_now()}", ""]
    for p in sorted(base.rglob("*")):
        if p.is_dir() or p.name == "GENERATED_INDEX.md":
            continue
        rel = p.relative_to(base)
        if path_forbidden(str(rel)):
            continue
        size = p.stat().st_size
        lines.append(f"## `{rel}`")
        lines.append(f"- size: {size} bytes")
        if p.suffix.lower() in {".md", ".txt", ".html", ".css", ".json", ".yaml", ".yml"} and size < 100_000:
            text = p.read_text(encoding="utf-8", errors="replace")[:1200]
            lines.append("```text")
            lines.append(text)
            lines.append("```")
        lines.append("")
    out = base / "GENERATED_INDEX.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentops", description="Reusable local multi-agent swarm orchestrator")
    p.add_argument("--version", action="version", version=f"agentops {VERSION}")
    sub = p.add_subparsers(required=True)
    sp = sub.add_parser("init"); sp.add_argument("--name"); sp.add_argument("--force", action="store_true"); sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("doctor"); sp.set_defaults(func=cmd_doctor)
    sp = sub.add_parser("add-task"); sp.add_argument("id"); sp.add_argument("--title"); sp.add_argument("--tranche", type=int, default=1); sp.add_argument("--priority", default="p1"); sp.add_argument("--executor", choices=["claude","codex","antigravity"], default="claude"); sp.add_argument("--profile", default="sonnet-executor"); sp.add_argument("--framework", default="generic"); sp.add_argument("--allowed-path", action="append"); sp.add_argument("--locked-path", action="append"); sp.add_argument("--acceptance", action="append"); sp.add_argument("--check", action="append"); sp.add_argument("--force", action="store_true"); sp.set_defaults(func=cmd_add_task)
    sp = sub.add_parser("list"); sp.set_defaults(func=cmd_list)
    sp = sub.add_parser("status"); sp.set_defaults(func=cmd_status)
    sp = sub.add_parser("create-worktree"); sp.add_argument("task_id"); sp.add_argument("--force", action="store_true"); sp.set_defaults(func=cmd_create_worktree)
    sp = sub.add_parser("clean"); sp.add_argument("task_id", nargs="*"); sp.add_argument("--tranche", type=int); sp.add_argument("--branches", action="store_true"); sp.add_argument("--reports", action="store_true"); sp.set_defaults(func=cmd_clean)
    sp = sub.add_parser("launch"); sp.add_argument("--tranche", type=int); sp.add_argument("task_id", nargs="*"); sp.add_argument("--spawn", action="store_true"); sp.add_argument("--monitor", action="store_true"); sp.add_argument("--tui", action="store_true"); sp.add_argument("--mode", choices=["headless","interactive"], default="headless"); sp.add_argument("--permission", choices=["workspace","full"], default="workspace"); sp.add_argument("--terminal", default="auto"); sp.set_defaults(func=cmd_launch)
    sp = sub.add_parser("run"); sp.add_argument("task_id"); sp.add_argument("--engine", choices=["claude","codex","antigravity"]); sp.add_argument("--mode", choices=["headless","interactive"], default="headless"); sp.add_argument("--permission", choices=["workspace","full"], default="workspace"); sp.add_argument("--spawn", action="store_true"); sp.add_argument("--terminal", default="auto"); sp.set_defaults(func=cmd_run_task)
    sp = sub.add_parser("_run-task"); sp.add_argument("task_id"); sp.add_argument("--engine", required=True); sp.add_argument("--mode", required=True); sp.add_argument("--permission", required=True); sp.add_argument("--worktree", required=True); sp.add_argument("--prompt", required=True); sp.set_defaults(func=cmd_run_task_internal)
    sp = sub.add_parser("scout"); sp.add_argument("task_id", nargs="?"); sp.add_argument("--tranche", type=int); sp.add_argument("--profile", default="haiku-scout"); sp.set_defaults(func=cmd_scout)
    sp = sub.add_parser("merge"); sp.add_argument("task_id", nargs="*"); sp.add_argument("--tranche", type=int); sp.add_argument("--checks", action="store_true", default=True); sp.add_argument("--auto-repair", action="store_true"); sp.add_argument("--repair-attempts", type=int, default=2); sp.add_argument("--repair-profile", default="sonnet-repair"); sp.add_argument("--permission", choices=["workspace","full"], default="workspace"); sp.set_defaults(func=cmd_merge)
    sp = sub.add_parser("collect"); sp.set_defaults(func=cmd_collect)
    sp = sub.add_parser("events"); sp.add_argument("--tail", type=int, default=50); sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_events)
    sp = sub.add_parser("budget"); sp.set_defaults(func=cmd_budget)
    sp = sub.add_parser("profiles"); sp.add_argument("--verbose", action="store_true"); sp.set_defaults(func=cmd_profiles)
    sp = sub.add_parser("templates"); ss = sp.add_subparsers(dest="sub", required=True); ssp = ss.add_parser("list"); ssp.set_defaults(func=cmd_templates)
    sp = sub.add_parser("plan"); sp.add_argument("--overview", required=True); sp.add_argument("--tranches", type=int, default=4); sp.add_argument("--profile", default="opus-planner"); sp.add_argument("--run", action="store_true"); sp.set_defaults(func=cmd_plan)
    sp = sub.add_parser("tui"); sp.set_defaults(func=cmd_tui)
    sp = sub.add_parser("examples-index"); sp.set_defaults(func=cmd_examples_index)
    return p


EXAMPLES_README = """# AgentOps examples

Put reference material here:

- images/ screenshots and visual inspiration
- html/ exported prototypes
- markdown/ specs and notes
- sketches/ ASCII or Mermaid sketches
- flows/ user journeys
- data/ fake sample data only
- ui/ design-system notes

Do not put secrets, credentials, logs, backups, real private data, or token files here.
Run `agentops examples-index` to generate `GENERATED_INDEX.md`.
"""


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
