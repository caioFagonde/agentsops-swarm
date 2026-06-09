# React Framework Guidance

## Conventions
- Use functional components with hooks — no class components.
- Use TypeScript interfaces for props and state shapes.
- Follow the project's state management pattern (Redux, Zustand, Context, etc).
- Keep components focused: one responsibility per component.

## Token-efficient patterns
- `grep -rn "export.*function\|export default" --include="*.tsx" --include="*.jsx"` to find components.
- `grep -rn "createBrowserRouter\|Route " src/` to find routing.
- Check `package.json` scripts for available commands.

## Checks
- `npm test -- --watchAll=false` or `npx vitest run`
- `npx tsc --noEmit` — type checking without build.
- `npm run build` — verify production build succeeds.
- `npx eslint src/ --ext .tsx,.ts,.jsx,.js` if ESLint is configured.

## Anti-patterns to avoid
- Do NOT use `useEffect` for derived state — use `useMemo`.
- Do NOT mutate state directly — use setter functions.
- Do NOT create components inside other components' render.
- Do NOT skip error boundaries for async data loading.
