# Vue 3 + Quasar Framework Guidance

## Conventions
- Use Composition API (`<script setup>`) for new components.
- Use Quasar components (QBtn, QCard, QPage) — don't reinvent with raw HTML.
- Keep component files under 200 lines — extract composables for logic.
- Use `defineProps`/`defineEmits` with TypeScript types.

## Token-efficient patterns
- `grep -rn "defineComponent\|<script setup>" --include="*.vue"` to find components.
- `grep -rn "path:" src/router/` to find routes.
- Check `quasar.config.js` for plugins and framework config.

## Checks
- `npm run lint` or `npx eslint src/ --ext .vue,.js,.ts`
- `npm run test:unit` if Vitest/Jest is configured.
- `npm run build` — verify no build errors.
- Visually check responsive behavior if UI changes.

## Anti-patterns to avoid
- Do NOT use Options API in new components (unless project convention).
- Do NOT use `any` type — define proper TypeScript interfaces.
- Do NOT skip loading/error/empty states in data-fetching components.
- Do NOT hardcode strings — use i18n if the project has it configured.
