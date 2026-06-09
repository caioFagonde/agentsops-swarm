# QA Framework Guidance

## Responsibilities
- Verify all acceptance criteria from the task specification.
- Run the full test suite and report results.
- Check for regressions in adjacent functionality.
- Validate error handling and edge cases.

## Token-efficient patterns
- `python -m pytest --tb=short -q` — compact output.
- `npm test -- --silent` — suppress noise.
- Focus on the specific files that changed: `git diff --name-only HEAD~1`.

## Checks
- All existing tests pass.
- New functionality has corresponding tests.
- Error cases return proper error codes/messages.
- No new warnings in lint/build output.
- UI changes (if any) render correctly in common viewports.

## Report format
```markdown
## QA Report: <task-id>
### Tests
- Total: <n>, Pass: <n>, Fail: <n>, Skip: <n>
### Regressions
- None | <list of new failures>
### Acceptance criteria
- [x/fail] <criterion>: <evidence>
### Edge cases tested
- <case>: <result>
```
