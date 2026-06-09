# AgentOps v4 Operating Rules

## Security
- Never read or commit secrets, credentials, tokens, backups, logs, or private data.
- Never access files outside the assigned worktree.
- Never run destructive commands (rm -rf, DROP TABLE, etc) without explicit task scope.
- Never weaken security boundaries, validation, or error handling.

## Isolation
- Use one worktree per task.
- Respect path locks: only modify files assigned to your task.
- Do not read or modify other tasks' worktrees.

## Scope
- Keep every task bounded to its specification.
- Do not broaden scope, even if you see adjacent improvements needed.
- Note out-of-scope discoveries in your report for future tasks.
- Do not refactor unrelated code.

## Token discipline
- Do NOT read the entire codebase. Use smart context provided to you.
- Use `grep -n`, `head`, `tail`, `wc -l` for targeted reads.
- Prefer compressed context (signatures + imports) over full file reads.
- If a file is >300 lines, read only the specific functions you need.
- Never `cat` entire directories or large files.

## Model tier rules
- t3-heavy (Opus): Strategic planning ONLY. Never for execution or file reading.
- t2-mid (Sonnet/Antigravity/Codex): Bounded implementation and repair. Use smart context.
- t1-fast (Ollama/optional legacy APIs): Scouts, summaries, course generation. Cheap and fast.
- t0-local (Ollama qwen): File classification, simple summaries. Free.
- When budget is running low, cascade down to cheaper tiers automatically.

## Reports
- Write a structured report after every task.
- Include: changes made, tests run, checks passed/failed, notes.
- Be specific: file paths, line numbers, function names.
- Do not write prose novels — structured markdown with bullets.

## Checks
- Run all specified checks before marking a task complete.
- If checks fail, attempt repair within scope.
- If repair requires out-of-scope changes, report and stop.
- Never delete tests to make checks pass.

## Merge safety
- Create rollback refs before merging.
- Run checks after merge.
- If post-merge checks fail, report for repair (do not force).
- Human makes final push.

## Course generation
- Every completed task should produce a course module.
- Use t1-fast tier for course generation — never heavy models.
- Base course content on actual diffs, reports, and task data.
- Do not fabricate or embellish changes.

## Fallback cascade
- When a provider fails or hits limits, cascade to the next tier down.
- Default code-execution cascade: Sonnet → Haiku → Antigravity → Codex. Direct API scouts do not count as successful code fallbacks.
- Ask before fallback unless `--fallback cascade` or `--yes` is set.
- Log all fallbacks in the event log.
