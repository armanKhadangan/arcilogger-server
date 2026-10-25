# ADR-0004: Web interface tooling

- Status: accepted (2026-10-12)

## Decision

- React **18.3** (as specified), TypeScript strict, Vite 7, Tailwind CSS 4 with CSS-variable
  design tokens (light and dark), Radix UI primitives styled in-house (shadcn/ui pattern, no CLI),
  TanStack Query / Table / Virtual, Recharts, react-i18next (Turkish default, English).
- Fonts (Inter, JetBrains Mono) are bundled from Fontsource; no CDN or external request — an E2E
  test fails if the page requests anything outside the server.
- **Component library page:** a `/ui-kit` route inside the app instead of Storybook — no second
  toolchain, the same build, and Playwright runs axe (WCAG 2.1 AA) on it in both themes.
- Tests: Vitest 4 (jsdom) and Playwright + @axe-core/playwright.
- **npm ≥ 11** is required: npm 10's resolver crashes on Vitest 4's optional peer dependencies.
  Vitest 3 was rejected because its worker pool (`tinypool`) has critical advisories.
- Chart series 1 uses teal-600 `#0D9488` instead of the brand teal-700: teal-700 is too grey to work
  as a categorical colour. Brand surfaces keep teal-700. See [DESIGN-TOKENS.md](../DESIGN-TOKENS.md).
- CSV exports use `;`, a UTF-8 BOM and CRLF so they open correctly in Excel with Turkish regional
  settings, and neutralise spreadsheet formulas in text cells (log data is attacker-controlled).

## Amendment 2026-10-25: what the shipped bundle contains

- Status: accepted.
- Why: the bundle installed at every organisation showed a developer page ("Bileşen Kiti") in
  the administrators' menu and shipped source maps (the app's readable source).
- The `/ui-kit` route stays inside the app, but only where the build-time flag `__UI_KIT__`
  (`web/vite.config.ts`) is true: the dev server, the unit tests and the E2E build
  (`npm run build:e2e`, mode `e2e`, which Playwright builds and serves itself, so axe still runs
  on it in both themes). `npm run build` — the bundle `Build-ArciBundle.ps1` ships — has neither
  the page nor its menu entry, and no source maps (`build.sourcemap: false`).
- The build checks its own output (`shippedBundleCheck` in `web/vite.config.ts`): a source map,
  or without the UI kit any module of `src/pages/ui-kit` or a `ui-kit` path, fails the build, so
  a changed setting cannot ship them. The E2E tests therefore run against the production build
  plus the UI kit page, not byte for byte the shipped bundle.
