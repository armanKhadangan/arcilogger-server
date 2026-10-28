# ARCILOGGER — instructions for Claude Code sessions

ARCILOGGER is a Windows-native log and evidence server, used through a browser, that is designed
to support 5651 obligations. It is a Python uv workspace (`core`, `collector`, `ingest`, `db`,
`api`, `worker`), a React web app in `web/`, PostgreSQL 16 (TimescaleDB optional) and a PowerShell
installer in `deploy/windows/`. Read `CONTRIBUTING.md` (ground rules, team workflow) once.

## How the work is organised

- Work comes as **GitHub issues**, one per work package, each assigned to one person. The issue is
  your brief: read it completely before anything else. It names the branch to start from, the
  design contract sections to read, the files you own, the files you must not touch, the
  done-when list and the commands to run.
- **Design contracts** live in `design/` (Wave 3: `design/wave-3-contract.md`). They are binding:
  names, setting keys, defaults, error codes, JSON shapes, texts and tests are fixed there. Read
  §0, §4.1, your package in §4.3, every §2 and §3 section your package touches, and the
  "Implementation notes" sections.
- **Branches:** start from the wave's integration branch (Wave 3: `wave-3`), never from `main`:
  `git fetch origin wave-3 && git checkout -B <your branch> origin/wave-3`. Open your pull request
  **into that integration branch**. Never push to `wave-3` or `main`; the manager reviews and merges.
- **One owner per file.** Edit only the files your issue gives you (and the exceptions it lists).
  If you need a change in a file another package owns, write it in your pull request under
  "Files outside my ownership" and on the issue; do not edit it.
- **Deviations** from the contract are kept minimal; write each one in the pull request and append
  it under "## Deviations" at the end of the contract in the same pull request.
- **Open questions** of the contract: implement the contract's stated default and list the question
  in the pull request under "Questions". Do not decide owner questions yourself.

## Product rules (never break them)

- Never claim "legally compliant"; the wording is "5651 yükümlülüklerini desteklemek için
  tasarlanmıştır". Recommend legal review where it matters.
- Raw logs are evidence: never modify, reorder, re-encode or delete raw log data outside the
  retention job.
- Never invent facts about Kamu SM, the law, FortiGate or MikroTik formats. Unconfirmed behaviour is
  configurable, marked "sahada doğrula", with a test that fails loudly if the assumption is wrong.
- No secrets in the repository, logs or responses; no telemetry; no external CDNs or fonts.
- Example data only from the fictional world: Örnek A.Ş., 172.20.x addresses, MACs
  00:00:5E:00:53:xx, RFC 5737 addresses (192.0.2.x, 198.51.100.x, 203.0.113.x). No real customer,
  person or site name anywhere.
- KVKK: personal data is shown only to roles allowed to see it, audited before it is returned;
  alert e-mails carry no person names and no raw log lines.
- No licence state blocks a 5651 function.
- Turkish first with English beside it in the UI and the docs (TR/EN document pairs); every API
  error has `code`, `message_tr`, `message_en`.
- SQL from user input is always bound parameters, never built from strings.
- Write code that reads like the surrounding code: comment density, naming, idiom.
- Do not copy code from repositories without a licence.

## Environment

- **First thing in every new session** (after checking out your branch from `origin/wave-3`):
  `bash tools/dev/cloud-setup.sh`. It is safe to run again: it installs PostgreSQL 16 and the test
  role if they are missing and starts the server. If it fails (for example, no network access to
  the Ubuntu package mirrors), say so in the pull request and run the tests that need no database.
- PostgreSQL 16 on `127.0.0.1:5432` with the test role `arci_test` / `arci_test` (CREATEDB); if the
  server is down later: `service postgresql start`.
- `export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres ARCILOGGER_REQUIRE_DB=1`
- `uv sync --locked --all-packages`, and `cd web && npm ci` before web checks.
- TimescaleDB, PowerShell (`ARCILOGGER_PWSH`) and Caddy (`ARCILOGGER_CADDY`) are usually missing in a
  cloud session; those tests skip and CI runs them (Linux TimescaleDB job, Windows jobs). Say in
  your pull request which tests you could not run.
- Tests create databases named `arci_t_*`; drop leftovers of killed runs
  (`DROP DATABASE ... WITH (FORCE)`) when disk gets short.

## Gates before every push (read the exit codes)

- `uv run --frozen poe lint` (ruff, ruff format, mypy --strict for Linux and Windows).
- `uv run --frozen pytest -n 3 -m 'not perf' -p no:cacheprovider <the test paths you touched>`
  (GUI tests under `xvfb-run -a`); performance tests with `uv run --frozen poe test-perf`.
- After API or schema changes: `uv run --frozen python web/scripts/export_api_contract.py` and
  `uv run --frozen python web/scripts/capture_api_samples.py`, commit the result, then both again
  with `--check`. Never edit generated files by hand.
- Web: `cd web && npm run check`, and the affected Playwright specs
  (`cd web && CI=1 npx playwright test <specs> --workers=2`).
- Never skip, xfail, quarantine or weaken a test to get green. CI must be green on the pull request
  before review.

## Commits and pull requests

- English summary line with the area first (`feat(worker): …`, `fix(web): …`, `test(api): …`, see
  `CONTRIBUTING.md`) and a short body that says why. Small commits, one after each meaningful step:
  cloud sessions can restart and lose what is not committed.
- Fill in the pull request template. "Closes #N" only when the whole package is done; otherwise
  "Part of #N".
- Answer every review comment: fix it in a new commit, or reply why not. Do not rewrite history on
  a branch that is under review.
