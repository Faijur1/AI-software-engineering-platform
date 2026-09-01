# Frontend

Next.js (App Router) + React + TypeScript in strict mode.

Setup, prerequisites and the full development workflow are in the
[root README](../README.md). Design documents are in [`../docs/`](../docs/).

```bash
npm install
npm run dev        # http://localhost:3000 — needs the backend on :8000

npx tsc --noEmit   # types
npm run lint
npm run build
```

## Layout

```
src/
├── app/         App Router pages and layouts
├── features/    feature-scoped components (health, and later repos, chat, ...)
└── lib/         API client and configuration
```

## Conventions

- **Data fetching happens in Server Components.** Client components are used
  only where interactivity requires them (`RefreshButton` uses `useTransition`
  to drive `router.refresh()`).
- **Every request is bounded by a timeout** in `lib/api.ts`, so an unresponsive
  backend surfaces as an error state with a retry rather than an endless
  spinner.
- **Every view handles loading, empty and error states**, and errors offer a
  retry.
- **Nothing secret goes in a `NEXT_PUBLIC_` variable** — those reach the
  browser.
