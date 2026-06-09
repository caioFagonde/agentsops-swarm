# Python Framework Guidance

## Conventions
- Use type hints on all function signatures.
- Follow existing import style (absolute vs relative).
- Prefer `pathlib.Path` over `os.path`.
- Use `dataclasses` or `pydantic` for structured data — match project style.

## Token-efficient patterns
- `grep -rn "def function_name" --include="*.py"` to find definitions.
- `python -m py_compile file.py` for quick syntax check.
- `python -m pytest tests/test_specific.py -x -q` for targeted test runs.

## Checks
- `python -m pytest -x -q` — stop on first failure, quiet output.
- `python -m py_compile <changed files>` — syntax validation.
- `ruff check <changed files>` or `flake8 <changed files>` if configured.
- `mypy <changed files> --ignore-missing-imports` if the project uses mypy.

## Anti-patterns to avoid
- Do NOT add `print()` debugging — use `logging` or remove before commit.
- Do NOT use mutable default arguments.
- Do NOT catch bare `except:` — catch specific exceptions.
- Do NOT import `*` from any module.
