# Documentation Framework Guidance

## Conventions
- Match existing doc style (MDX, plain Markdown, RST, etc).
- Use relative links for internal references.
- Include code examples that actually work — test them.
- Keep headings hierarchical: H1 for page title, H2 for sections, H3 for subsections.

## Token-efficient patterns
- `find docs/ -name "*.md" | head -20` to see doc structure.
- Check for a docs build system: `mkdocs.yml`, `docusaurus.config.js`, `conf.py`.
- `grep -rn "](/" docs/` to find internal links.

## Checks
- Verify all internal links resolve.
- Run docs build if a build system exists.
- Check for broken code examples.
- Verify new pages are added to navigation/sidebar config.

## Anti-patterns to avoid
- Do NOT duplicate information across multiple pages.
- Do NOT use absolute URLs for internal links.
- Do NOT leave TODO/placeholder sections in published docs.
- Do NOT write docs without concrete examples.
