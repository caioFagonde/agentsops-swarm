# AgentOps Swarm

AgentOps Swarm is a reusable local orchestration layer for running several AI coding agents against any git project without losing control of merges, tests, or secrets.

It coordinates:

- Claude Code / CC for planning, implementation, and repair.
- OpenAI Codex for verification and narrow implementation.
- Google Antigravity CLI for additional agent execution.
- Git worktrees for isolation.
- Tranches/sprints for staged work.
- Rich TUI monitoring.
- JSON event logs and budget/time tracking.
- Examples/reference material folders for sketches, screenshots, HTML, markdown, data samples, and UI direction.

## Why this exists

One large prompt against one working tree creates chaos. AgentOps creates a controlled swarm:

```text
Overview prompt
  -> Opus planner creates tranches and tasks
  -> Haiku scout reads quickly and maps the repo
  -> Sonnet/Codex/Antigravity workers implement in isolated worktrees
  -> Verifiers and repair nodes handle failures
  -> You merge one branch at a time
```

## Install

### Linux/macOS

```bash
./install.sh
```

This installs AgentOps under `~/.local/share/agentops-swarm`, links `agentops` into `~/.local/bin`, and optionally installs Claude Code, Codex, and Antigravity CLI.

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
```

Restart PowerShell after the installer updates PATH.

## Authentication

AgentOps can install the CLIs, but authentication remains interactive:

```bash
claude doctor
codex
agy
```

## Quick start on any project

```bash
cd /path/to/project
agentops init --name my-project
agentops doctor
```

Create an overview prompt:

```bash
cat > overview.md <<'EOF'
Improve this project safely. Fix failing tests, improve UX, add documentation, and keep security boundaries intact. Divide work into narrow tranches.
EOF
```

Ask the Opus planner to create a DAG:

```bash
agentops plan --overview overview.md --tranches 4 --run --profile opus-planner
```

Scout tranche 1:

```bash
agentops scout --tranche 1 --profile haiku-scout
```

Launch workers:

```bash
agentops launch --tranche 1 --spawn --monitor --mode headless --permission workspace
```

Use tmux when GUI terminals do not open:

```bash
agentops launch --tranche 1 --spawn --terminal tmux --monitor --mode headless --permission workspace
tmux attach -t agentops
```

Merge with automatic repair:

```bash
agentops collect
agentops merge --tranche 1 --auto-repair --repair-attempts 2 --repair-profile sonnet-repair
```

Open TUI:

```bash
pip install rich
agentops tui
```

## Examples folder

Put implementation examples here:

```text
.agentops/examples/images/
.agentops/examples/html/
.agentops/examples/markdown/
.agentops/examples/sketches/
.agentops/examples/flows/
.agentops/examples/data/
.agentops/examples/ui/
```

Index them:

```bash
agentops examples-index
```

Agents are instructed to use examples as references/specs, not as copied assets unless you own them.

## Common commands

```bash
agentops init --name project
agentops doctor
agentops plan --overview overview.md --tranches 4 --run
agentops add-task ui-fix --title "Fix UI" --tranche 1 --executor claude --framework vue-quasar --allowed-path apps/web --acceptance "Build passes"
agentops list
agentops status
agentops scout --tranche 1
agentops launch --tranche 1 --spawn --monitor
agentops run ui-fix --mode interactive
agentops collect
agentops merge --tranche 1 --auto-repair
agentops events --tail 100
agentops budget
agentops tui
```

## Permission modes

`--permission workspace` keeps agents sandboxed where the underlying CLI supports it.

`--permission full` maps to bypass/full-access modes. Use it only because each agent runs inside a git worktree, and merges are gated by tests.

Never run full-permission agents directly in your main working tree.

## Engines

- `claude`: Claude Code CLI.
- `codex`: OpenAI Codex CLI.
- `antigravity`: Google Antigravity CLI (`agy` preferred, `antigravity` fallback).

Antigravity CLI is primarily a TUI. AgentOps supports it best in interactive mode; headless mode uses best-effort `/goal` piping or `AGENTOPS_ANTIGRAVITY_COMMAND_TEMPLATE` when you define a version-specific command.

## Security policy

AgentOps blocks obvious secret/runtime paths in task grants and tells agents not to read or modify secrets. This is not a substitute for reviewing diffs. Always inspect before pushing.

Forbidden by default:

```text
.env, secrets, backups, logs, tokens, credentials, private keys, OAuth files, local data
```

## GitHub private repo workflow

```bash
git init
git add .
git commit -m "Initial AgentOps Swarm"
gh repo create my-agentops-swarm --private --source=. --push
```

Or create the private repo in GitHub and push manually.
