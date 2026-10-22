# Katkı rehberi / Contributing

## Ground rules (from the build specification)

1. Raw logs are evidence: never modify, reorder, re-encode or delete raw data outside the
   retention job.
2. No secrets in the repository, no telemetry, no external CDNs or fonts; everything is bundled.
3. Never claim "legally compliant". Use "5651 yükümlülüklerini desteklemek için tasarlanmıştır".
4. Do not commit third-party binaries (Zamane jar, Java, installers) — they are downloaded and
   checksum-verified during setup.
5. Do not invent facts about Kamu SM, the law or device formats: make the behaviour configurable
   and write a test that fails loudly if the assumption is wrong.

## Branches and commits

- Work on a feature branch, open a pull request, keep `main` green.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat(core): …`, `fix(web): …`,
  `docs: …`, `test(ingest): …`, `chore(ci): …`. Scopes: `core`, `collector`, `ingest`, `api`,
  `worker`, `web`, `deploy`, `docs`, `ci`.

## Code

| Area | Rules |
|---|---|
| Python | 3.12, typed everywhere, `mypy --strict`, ruff (lint + format, line length 100), no naive datetimes (ruff `DTZ`), `pathlib` only, binary file I/O for log data |
| TypeScript | strict mode, ESLint (typescript-eslint strict), Prettier, no `any` except the documented TanStack column type |
| UI | Colours only through tokens in `web/src/styles/globals.css`; every string in `web/src/locales/{tr,en}.json`; status never by colour alone (icon + text); WCAG 2.1 AA |
| Docs | Turkish first, English second; every module has a docstring explaining its job |

## Checks before pushing

```bash
uv run poe check                 # Python: ruff, mypy, pytest
cd web && npm run check          # web: eslint, prettier, tsc, vitest, build
npm run e2e                      # web: Playwright + axe (after npm run build)
```

Optional: `uv tool install pre-commit && pre-commit install` runs the fast checks on every commit.

## Line endings

`.gitattributes` forces LF (CRLF only for `.ps1`/`.cmd`). Log fixtures are byte-exact and pinned by
`tests/fixtures/format/SHA256SUMS`; a Windows Git client that converts them makes the tests fail.
