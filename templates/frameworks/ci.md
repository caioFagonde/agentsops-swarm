# CI/CD Framework Guidance

## Conventions
- Keep pipeline steps fast — cache dependencies, parallelize where possible.
- Pin action/tool versions for reproducibility.
- Use matrix builds for multi-version testing.
- Fail fast: lint and type-check before running full test suite.

## Token-efficient patterns
- `find .github/workflows -name "*.yml"` or check `.gitlab-ci.yml`, `Jenkinsfile`.
- Read only the specific job/step you're modifying.
- `grep -n "runs-on:\|image:\|script:" .github/workflows/*.yml` for structure overview.

## Checks
- Validate YAML syntax: `python -c "import yaml; yaml.safe_load(open('file.yml'))"`
- For GitHub Actions: `actionlint` if available.
- Verify all referenced secrets exist in project settings (note them in report).

## Anti-patterns to avoid
- Do NOT store secrets in workflow files.
- Do NOT use `continue-on-error: true` to mask real failures.
- Do NOT skip the checkout step or assume working directory.
- Do NOT run unnecessary steps on non-default branches.
