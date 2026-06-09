# FastAPI Framework Guidance

## Conventions
- Use Pydantic models for request/response schemas.
- Use dependency injection for shared services (db sessions, auth, config).
- Keep route handlers thin — business logic goes in service modules.
- Use `HTTPException` with specific status codes, not generic 500s.

## Token-efficient patterns
- `grep -rn "@app\.\|@router\." --include="*.py"` to find all endpoints.
- `grep -rn "class.*BaseModel" --include="*.py"` to find schemas.
- Check `main.py` or `app.py` for router includes — don't read all files.

## Checks
- `python -m pytest tests/ -x -q`
- `curl -s http://localhost:8000/docs` — verify OpenAPI schema loads.
- Check for missing error handlers on new routes.
- Verify all new Pydantic models have examples.

## Anti-patterns to avoid
- Do NOT put database queries directly in route handlers.
- Do NOT use `response_model=dict` — define proper schemas.
- Do NOT skip input validation — FastAPI does it via Pydantic, use it.
