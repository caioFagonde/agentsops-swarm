# Docker Compose Guidance

## Conventions
- Use named volumes for persistent data.
- Use `.env` files for configuration — never hardcode secrets.
- Pin image versions — no `latest` tags in production.
- Use health checks for service dependencies.

## Token-efficient patterns
- `docker compose config` — validate and view resolved config.
- `docker compose ps` — check service status.
- Read only the service you're modifying, not the entire compose file.

## Checks
- `docker compose config --quiet` — validate syntax.
- `docker compose build --dry-run` if supported, or just `build`.
- Verify `.env.example` is updated when adding new variables.
- Check that health checks exist for services others depend on.

## Anti-patterns to avoid
- Do NOT use `network_mode: host` unless absolutely necessary.
- Do NOT expose database ports to the host in production config.
- Do NOT mount the entire project directory as a volume in production.
- Do NOT skip `depends_on` with `condition: service_healthy`.
