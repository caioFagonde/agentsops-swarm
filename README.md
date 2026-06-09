# AgentOps Swarm v4

SOTA multi-agent worktree orchestration with multi-tier model routing, smart context management, automatic course generation, and token budget tracking.

Designed for senior developers who want controlled high-throughput implementation without losing safety, traceability, or merge discipline — and without burning through API limits in 10 minutes.

## What's new in v4

**Multi-tier model routing** — Four tiers from free local models to Opus, each used only where it matters. Heavy models plan; lightweight models execute, scout, summarize, and generate courses.

**Smart context** — File fingerprinting, compressed signatures, and targeted grep replace full-codebase reads. Executors get exactly the context they need, nothing more.

**Auto course generation** — Every completed task produces a reveal.js slide + companion guide explaining what was done, improvement opportunities, and failure modes. Serve the course locally or share it.

**Cascade fallback** — When a model hits limits or fails, the system automatically downgrades through agentic coding CLIs: Sonnet → Haiku → Antigravity → Codex. Direct API scouts are not allowed to falsely mark code execution as complete.

**Token budget tracking** — Real-time cost tracking per tier, per role, per task. Automatic tier downgrades when approaching daily limits.

## Model tiers

| Tier | Models | Cost | Used for |
|------|--------|------|----------|
| t0-local | Ollama qwen3 1.7b/4b | Free | Summaries, file classification, simple scouts |
| t1-fast | Ollama qwen3 8b+ with optional legacy Gemini | Free/low | Scouts, course generation, context prep |
| t2-mid | Claude Sonnet/Haiku, Antigravity, Codex | $$ / CLI-managed | Task execution, repair, verification |
| t3-heavy | Claude Opus | $$$ | Strategic planning ONLY — never execution |

## Install

**Linux/macOS:**

```bash
./install.sh
export PATH="$HOME/.local/bin:$PATH"
agentops doctor
```

**With local model setup:**

```bash
AGENTOPS_SETUP_LOCAL=true ./install.sh
```

**Windows PowerShell:**

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
agentops doctor
```

**Prerequisites:**
- Python 3.10+
- git
- npm (for Claude Code and Codex CLI)
- Ollama (optional, for free local models) — https://ollama.ai
- Antigravity CLI (`agy` or `antigravity`) optional but preferred for fallback execution. For headless use, set `AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE`.
- Gemini API key is supported only as a legacy opt-in direct API provider, not as a default route.

## Quick start

```bash
cd /path/to/project
agentops init --name my-project
agentops tui
```


## Complete guided flow

The fastest UX path is now a single guided command:

```bash
agentops complete-flow /path/to/project --name my-project --overwrite
# alias:
agentops flow /path/to/project --overwrite
```

Flow behavior:

1. Initializes `.agentops` in the selected folder, overwriting only when `--overwrite` is passed.
2. Captures a pasted prompt/specification and saves it under `.agentops/flow/`.
3. Generates tranches with the planner when available, or with `--offline-plan`/automatic offline bootstrap when no planner CLI is present.
4. Asks whether to start execution.
5. Runs tranche tasks one by one with animated terminal panels.
6. Stops at a manual merge gate after every tranche before continuing.

Useful options:

```bash
agentops complete-flow . --prompt-file spec.md --tranches 4 --start
agentops complete-flow . --plan-file .agentops/planner/flow-plan.json --start
agentops complete-flow . --offline-plan --no-start
```

Antigravity is a first-class execution profile. For headless execution, set a template matching your local CLI contract:

```bash
export AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE='agy run --workdir {worktree} --prompt-file {prompt}'
```

The harness does not guess Antigravity CLI flags. Without a template it opens/uses Antigravity only in interactive mode and fails headless with a clear continuation prompt path.

## Planning (t3-heavy → Opus)

```bash
agentops plan --overview overview.md --tranches 4 --run --profile opus-planner --overwrite
agentops list
```

## Scouting (t1-fast → local/Ollama by default)

```bash
# Smart scout using lightweight models (no CLI claude needed)
agentops scout --tranche 1

# Traditional CLI-based scout
agentops scout --tranche 1 --profile haiku-scout
```

## Execution (t2-mid → Sonnet/Antigravity/Codex)

```bash
# Launch with automatic cascade fallback
agentops launch --tranche 1 --spawn --monitor --fallback cascade

# Launch with animated dashboards
agentops launch --tranche 1 --spawn --monitor --mode headless --permission workspace
```

## Merge

```bash
agentops collect
agentops merge --tranche 1 --auto-repair --repair-attempts 2
```

Rollback refs are created before every merge:

```bash
agentops rollback --list
agentops rollback --ref agentops/rollback/<name>
```

## Course generation

Every completed task automatically generates a reveal.js course module. You can also trigger it manually:

```bash
# Generate course for a specific task
agentops course generate --task <task-id>

# Generate summary course for an entire tranche
agentops course generate --tranche 1

# Use local models only (free)
agentops course generate --task <task-id> --local

# View generated course files
agentops course view

# Serve the course locally
agentops course serve --port 8080
```

## New CLI commands

```bash
agentops setup-models           # Interactive local model setup
agentops setup-models --full    # Pull all recommended models
agentops inspect <task-id>      # Show task state, diff, report, logs
agentops context <task-id>      # Preview smart context for a task
agentops doctor                 # Enhanced: checks all tiers, models, Antigravity, legacy keys
agentops complete-flow .         # Guided init/prompt/plan/run/merge flow
```

## Smart context

Instead of reading the entire codebase, v4 builds targeted context bundles:

1. **File fingerprinting** — SHA256 hashes detect changes without reading content.
2. **Compressed signatures** — Python files become imports + function/class signatures. JS/Vue get equivalent treatment.
3. **Grep targeting** — Keywords from the task description find relevant files.
4. **Test inference** — Implementation files automatically include their test files.
5. **Token budgeting** — Context is truncated to fit the model's budget.

## Budget tracking

```bash
# View current budget status
agentops budget

# Budget is shown in TUI header
agentops tui
```

The budget system tracks cost per invocation across all tiers. When approaching limits, it automatically downgrades: t3 → t2 → t1 → t0. Daily limits reset at midnight; session limits reset after 6 hours of inactivity.

## TUI

```bash
agentops tui
```

The TUI provides: status overview, prompt paste/edit, planner DAG generation, launch selection, smart scouting, course generation, merge gates, inspect tasks, model setup, budget display, and more.

## Prompt input

```bash
agentops prompt task-id                # paste directly, end with EOF
agentops prompt task-id --file plan.md # from file
agentops prompt task-id --edit         # open in $EDITOR
```

## Examples folder

```bash
.agentops/examples/       # drop reference images, specs, data
agentops examples-index   # generate an index for agents to use
```

## Safety model

```
isolated worktree → smart context → report → tests/checks → sequential merge → rollback ref → optional repair → course
```

Path locks prevent merge conflicts. Rollback refs enable recovery. Reports and courses create an audit trail.

## Project structure

```
agentops_swarm/
  __init__.py        # version
  cli.py             # main CLI + TUI
  models.py          # multi-tier model routing + fallback
  context.py         # smart context management + fingerprinting
  course.py          # reveal.js course generation
  budget.py          # token budget tracking
templates/
  roles/             # role system prompts per profile
  frameworks/        # framework-specific guidance
  prompts/           # reusable prompt templates
  course/            # course generation templates
bin/
  agentops           # launcher script
```

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `AGENTOPS_ANTIGRAVITY_EXEC_TEMPLATE` | Headless Antigravity command template. Supports `{prompt}` and `{worktree}` placeholders. | — |
| `GEMINI_API_KEY` | Legacy Gemini Flash direct API access; not used by default | — |
| `GOOGLE_API_KEY` | Alternative legacy Gemini key | — |
| `ANTHROPIC_API_KEY` | Claude API (if using direct API) | — |
| `AGENTOPS_HOME` | Override install location | `~/.local/share/agentops-swarm` |
| `AGENTOPS_PYTHON` | Python interpreter | `python3` |
| `AGENTOPS_SETUP_LOCAL` | Auto-install Ollama on install | `false` |
| `AGENTOPS_INSTALL_TOOLS` | Auto-install npm tools on install | `true` |
| `AGENTOPS_DAILY_LIMIT` | Daily token budget (USD) | `5.00` |
| `AGENTOPS_SESSION_LIMIT` | Session token budget (USD) | `2.00` |
