# AgentOps Swarm v3

AgentOps Swarm is a reusable local multi-agent orchestration toolkit for Claude Code, OpenAI Codex/GPT, and Google Antigravity. It is designed for senior developers who want controlled high-throughput implementation without losing safety, traceability, or merge discipline.

## Core capabilities

- Project-local `.agentops/` task graph.
- Git worktree isolation per task.
- Opus planner DAG generation.
- Haiku scout mode.
- Sonnet/Claude, Codex/GPT, and Antigravity worker profiles.
- Pretty animated worker dashboards by default.
- Automatic fallback when Claude/Codex/Antigravity fails or hits usage limits.
- Confirmation-first fallback and repair by default.
- Launch selected tasks or whole tranches from CLI/TUI.
- Prompt paste/edit/import from TUI or CLI.
- Auto-prune, clean, retry, rollback refs, merge gates, and repair workers.
- JSON event logs and budget/time tracking.

## Install

Linux/macOS:

```bash
./install.sh
export PATH="$HOME/.local/bin:$PATH"
agentops doctor
```

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
agentops doctor
```

The installer attempts to install Claude Code and Codex via npm when missing. Antigravity is best-effort because vendor install methods may change; `agentops doctor` verifies whether `agy` is available.

Provider login is still interactive:

```bash
claude doctor
codex
agy
```

## Quick start on any project

```bash
cd /path/to/project
agentops init --name my-project
agentops tui
```

Generate a full DAG automatically:

```bash
agentops plan --overview overview.md --tranches 4 --run --profile opus-planner --overwrite
agentops list
```

Run scouts:

```bash
agentops scout --tranche 1 --profile haiku-scout
```

Launch tasks with animated dashboards:

```bash
agentops launch --tranche 1 --spawn --monitor --mode headless --permission workspace
```

Launch aggressively with automatic fallback to Codex when workers fail:

```bash
agentops launch --tranche 1 --spawn --monitor --fallback codex --fallback-on-any-failure --yes
```

Merge safely:

```bash
agentops collect
agentops merge --tranche 1 --auto-repair --repair-attempts 2
```

Rollback refs are created before merges:

```bash
agentops rollback --list
agentops rollback --ref agentops/rollback/<name>
```

## Prompt input

Paste directly:

```bash
agentops prompt task-id
# paste prompt, end with EOF
```

From file:

```bash
agentops prompt task-id --file prompt.md
```

From editor:

```bash
agentops prompt task-id --edit
```

## TUI

```bash
agentops tui
```

The TUI supports status, prompt paste/edit, planner DAG generation, launch selection, merge, clean/retry, reports, events, budget, and examples indexing.

## Examples folder

Drop reference images, HTML, markdown specs, sketches, and fake data in:

```txt
.agentops/examples/
```

Generate an index:

```bash
agentops examples-index
```

## Safety model

AgentOps does not blindly merge worker output. The safety boundary is:

```txt
isolated worktree → report → tests/checks → sequential merge → rollback ref → optional repair worker
```

Avoid using `--permission full` unless the task is tightly scoped and the worktree is disposable.
