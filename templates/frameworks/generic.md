# Generic Framework Guidance

## Principles
- Follow existing project conventions (naming, structure, patterns).
- Keep changes small, tested, and bounded to your assigned paths.
- Add structured error handling with clear user-facing messages.
- Preserve existing API contracts — do not change function signatures without updating callers.

## Before you start
- Check for existing patterns: `grep -r "similar_pattern" src/`
- Check for tests: `find . -name "test_*" -o -name "*_test.*"`
- Check for CI config: look for `.github/workflows/`, `Makefile`, `Justfile`, `tox.ini`

## Token discipline
- Do NOT read entire directories. Use `find`, `grep -l`, `wc -l` to locate what you need.
- Read only the functions you're modifying, not entire files.
- If a file is >300 lines, use `head`/`tail`/`sed -n` to read specific ranges.

## Checks
- Run the project's existing test suite.
- Run linting if configured.
- Verify no new warnings in build output.
