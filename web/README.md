# ARCILOGGER web arayüzü / web interface

> Kullanıcılar yalnızca tarayıcı kullanır; arayüz sunucunun kendisinden (Caddy) sunulur, dış
> kaynak yüklemez. Bu dosya geliştiriciler içindir.

React 18 + TypeScript (strict), Vite 7, Tailwind 4, Radix, TanStack Query/Table/Virtual,
react-i18next (Turkish first, English second). Tooling decisions: `docs/adr/0004-web-tooling.md`,
tokens: `docs/DESIGN-TOKENS.md`.

## Talking to the API

- `src/lib/api.ts` — the only `fetch` wrapper: same origin; `X-CSRF-Token` from the `arci_csrf`
  cookie on every POST/PUT/PATCH/DELETE; `X-Arci-Background: 1` for automatic requests; errors
  as `ApiError` (`.localized` = the server's text in the UI language); global reactions unless
  `silent` (401 → sign-in with `?next=`, 403 `password_change_required`, 403
  `privacy_notice_required`).
- `src/lib/api-types.ts` — TypeScript twins of the Pydantic request/response models.
- `src/lib/api-hooks.ts` — one TanStack Query hook per endpoint; `usePollingQuery` for widgets
  that refresh themselves (background header after the first load).
- `src/lib/auth.tsx` — `RequireAuth` (route guard), `useAuth()` (user and permission checks),
  `RequirePermission`, `ApiEventsBridge`, idle watch, `useSignOut`.
- `src/lib/reauth.tsx` — `useReauth().withReauth(action)`: on 403 `reauth_required` it asks for
  the password (+ TOTP), calls `/auth/reauth` and repeats the action. Use it for every sensitive
  action.
- `src/nav.ts` — menu entries with the permissions that show them (any of).

### Generated contract (`src/generated/`, never edit by hand)

`uv run python web/scripts/export_api_contract.py` (from the repository root) writes:

- `api-schemas.json` — OpenAPI schemas + paths; `src/lib/api-types.test.ts` compares every type in
  `api-types.ts` and every `api.get/post/…('/path')` call with it;
- `roles.json` — roles, labels and permissions from `arcilogger_api.security` (menu filter, role
  matrix);
- `audit-vectors.json` — golden audit entries hashed by the server's own code; the browser's
  canonical JSON / SHA-256 (`src/lib/audit-chain.ts`) is tested against them.

After any API change run it again; `--check` exits 1 when the files are out of date (meant for CI).

## Pages of this package

`/login` (password → TOTP or recovery code → first TOTP enrolment with recovery codes shown once
→ forced password change), `/setup` (first administrator with the console token), the KVKK
notice (dialog until accepted), the user menu (theme, language, "Parolayı değiştir" — the own
password with the current one, `POST /auth/password`, which ends the other sessions —, new
recovery codes, sign-out), `/settings` → `/settings/users` (users and read-only role matrix),
`/audit` (audit log, chain badge, detail drawer), and these pages of the main menu:

- `/` — **Panel** (`dashboard.view` or `dashboard.usage`) on `GET /dashboard/summary` and today's
  `GET /usage/company`: log intake, yesterday's seal, credits, clock offset, today's usage, open
  alerts, top sites, categories and 30 days of sealing, each block only for the permission it
  needs (a block the API leaves out is not shown; `dashboard.usage` alone gives only the usage
  blocks and never asks the summary). Live blocks refresh every 30 s, usage every 5 minutes, as
  background requests and not while the tab is hidden (`src/pages/dashboard/`).
- `/lookup`, `/lookup/:id` — **5651 Sorgu** (`lookup.run`): request details + query (day and
  24-hour time typed as text in the Turkish format, `src/lib/dates.ts`; the browser's own date
  inputs follow its UI language), results with the attribution chain, IP-conflict alternatives
  side by side, the engine's explanations as sent, every cited raw line in a side sheet
  (`GET /raw`), the official PDF (hash re-checked in the browser) and earlier lookups.
- `/evidence` — **Kanıt Paketleri** (`evidence.read`): list (refreshes while a package is built),
  detail sheet, create wizard (`evidence.create`; a generated password is shown once and never
  stored), download with re-authentication and a `Repr-Digest` check.
- `/sealing` — **Mühürleme** (`seal.read`): month calendar, day detail (files, manifest, token,
  verification, replica), "Doğrula" (`seal.verify`), manifest/token downloads, the Kamu SM credit
  card; a mock or test time-stamp authority is always shown in red.
- `/usage` — **Kullanım** (`usage.person` or `usage.anonymous`): tabs Şirket, Departman, Kişi,
  Cihaz, Site, Kategori with the period (site days), the hours filter and the selected detail in
  the address; drill-down company → department → person → site → host names / URLs, site →
  users, category → sites → users; the person's day timeline, hourly charts and 30-day heat map;
  every list read in full and searched (like the API's `q`) and sorted in the browser; export
  (`/usage/export.*`: CSV of a view's main table, Excel of every section); "Yeniden hesapla" for
  `settings.manage`. Every duration carries the "tahmini" note (`src/pages/usage/`). The E2E mock
  (`e2e/mock-usage.ts`) serves the captured usage day (`e2e/samples/usage.json`) and a generated
  dataset for other periods.
- `/security` — **Güvenlik** (`alerts.read`; state changes and notes `alerts.manage`): the summary
  (open alerts per severity, mean time to close), the alert list filtered, sorted and paged by the
  server (filters and the open alert in the address, the search text never), the detail drawer
  with the detector's details, the runbook in the UI language, the history and the e-mails
  (recipients masked); only the changes the API allows from the current state are offered
  (`src/pages/security/`). No rule editor yet.
- `/devices` — **Cihazlar ve Kişiler** (`devices.read`, changes `devices.manage`): tabs Cihazlar,
  Kişiler, Departmanlar ve VLAN'lar, İçe/Dışa aktar (`?tab=`); the device list is searched,
  filtered, sorted and paged by the server, the person list searched, filtered and paged;
  assignments with their history, persons
  (create, edit, deactivate), the VLANs and subnets (add, edit, move to another network, delete;
  `vlans-model.ts` checks the network like the API) with their departments, CSV import with a dry
  run and CSV export; person-level lists wait for the KVKK notice (`src/pages/devices/`).
- `/reports` — **Raporlar** (`reports.manage`): the scheduled usage reports (create, edit, switch
  on/off, delete), "Şimdi çalıştır" and "Önizle" (PDF in a new tab, the e-mail HTML in a sandboxed
  frame), the run history with deliveries and the files (checked against their SHA-256);
  `/reports?run=<id>` opens a run (the report e-mail links there). Run now, preview and the file
  downloads return person data and sit behind the KVKK notice (`require_privacy_notice`)
  (`src/pages/reports/`).

Device-provided text (host names, raw lines) goes through `visibleText` (`src/lib/display.ts`):
bidi overrides and other invisible characters appear as `\uXXXX`. Other menu entries are
placeholders ("Bu sayfa sonraki bir sürümde eklenecek.", no milestone code), already
permission-filtered (`BUILT_PAGES` in `src/nav.ts`).
Two top-bar controls of spec 12.2 come with the pages that use them: the global search box
appears once "Arama" is built (M2; its term goes to the page as router state, so not into the URL,
`?next=` or the web server's access log — but router state lives in the tab's session history
(`history.state`), which the browser may keep for session restore: if the term must not reach the
browser history at all, M2 hands it over in memory, e.g. a context or the query cache), and the
global time range ("[Bugün ▾]")
with the first page that reads it (Panel / Arama, M2) — until then a picker that filters nothing
would make pages such as Denetim Kaydı look limited to "today". "Kullanım" keeps its own period in
its address (`?from=&to=`).

## Licence (offline licensing)

The server decides every licence question; the web app only follows `GET /license`
(`useLicense()`: one shared query, fresh for 60 s, refreshed every 5 minutes in the background).
Paid modules: **Kullanım analizi** (`usage_analytics`: Kullanım, the Panel's usage widgets),
**Zamanlanmış kullanım raporları** (`usage_reports`: Raporlar) and **Toplu içe aktarma (CSV)**
(`bulk_import`: the import half of "İçe/Dışa aktar"). Nothing of 5651 Temel (collection,
sealing, lookups, evidence packages, devices and persons, alerts, audit, users, the Panel's
health boxes) depends on the licence in the browser.

- `src/lib/license.ts` (pure, unit-tested): `moduleEnabled(status, key)` (true while the licence
  is loading or unreadable: for marks and texts only; the API refuses a locked module's routes with
  403 `module_not_licensed` anyway), `bannersFor(status, { isAdmin })` + `bannerText`,
  `formatRequestCode`.
- `ModuleGate` (`src/components/layout/route-frames.tsx`) around Kullanım and Raporlar: the
  page's skeleton until the licence is known, `ModuleLockedPage` while the module is off (the
  page and its requests never run), the page itself when the licence cannot be read. The Panel
  asks `/usage/company` only once the licence is known and enables the module; otherwise a small
  locked card takes the usage widgets' place.
- The shell: at most two banners above the page (`LicenseBanner`, contract 8.3; administrators
  also see limits and maintenance and get the licence page link), lock marks on locked menu
  entries, "Lisans: …" under the version in the sidebar footer.
- `/settings/license` — **Lisans** (`settings.manage`): state, module table and limits; the
  request code (copy, `arcilogger-istek-kodu.txt`) to e-mail to the licensor (Arman Khadangan,
  arman@khadangan.com); `.lic` upload and removal (re-authentication first, refusals with the
  API's texts, files over 64 KiB refused before upload); history; "Hakkında" (version, release
  date, rights holder, the time source of the licence checks). The Users page shows
  "Kullanıcı: used / max" and turns "Kullanıcı ekle" off at the limit.
- A 403 `module_not_licensed` of any request (a tab opened before the licence changed) makes the
  shell read the licence again (`ApiEventsBridge`).

The unit tests and the E2E mock (`e2e/mock-license.ts`, default state `trial`) serve the real
API's licence answers, `e2e/samples/license.json`: `capture_api_samples.py` captures every state,
the uploads and every refusal, the locked modules' 403 and the users limit's 409 on licence sites
of its own (fresh database and data folder each, the installation's seed and the worker's
`license.check` at chosen moments, licence files signed with the core package's test key, the
release day pinned to 01.10.2026), so the file is the same on every capture. The sites' stories
are in `src/test/license-samples.ts`; a user without `settings.manage` gets the same document
with `details: null` (checked in `api-samples.test.ts`, so the tests derive that form). The
licence types are named like the API's schemas (`LicenseOut`, `LicenseEventOut`, …) and compared
with `api-schemas.json` like every other type.

## Tests

```bash
npm run check      # eslint, prettier, tsc, vitest, build
npm run e2e        # Playwright against a production build (dist-e2e), API mocked (e2e/mock-api.ts)
```

**What ships.** `npm run build` writes `dist`, the bundle `Build-ArciBundle.ps1` copies into the
installer: no source maps and no developer UI kit. The UI kit (`/ui-kit`, every component in both
themes, `src/pages/ui-kit`) and its menu entry exist only where `__UI_KIT__` is true
(`vite.config.ts`): the dev server, the unit tests and `npm run build:e2e` (mode `e2e`, into
`dist-e2e`), which Playwright builds and serves itself. A build check in `vite.config.ts`
(`shippedBundleCheck`) fails a build that would ship a source map, or, without the UI kit, any
module of `src/pages/ui-kit` or a `ui-kit` path; `src/components/layout/shipped-bundle.test.tsx`
renders the app without it.

`e2e/mock-api.ts` answers like the real endpoints (shapes from `api-types.ts`, texts from the
API's message tables, audit entries from the golden vectors). Like the API (every request model
is `extra="forbid"`), it refuses a JSON body with a field its OpenAPI schema does not have, or
without a required one, with 422 `validation_error` (`REQUEST_BODIES`, schemas from
`src/generated/api-schemas.json`), so a field the app sends but the API lacks fails the tests.

Options that need a request field the API does not have yet stay hidden: `src/lib/api-features.ts`
reads them from the generated contract at build time (`define` in `vite.config.ts`). The evidence
wizard's admin-only "ARCILOGGER denetim kaydını ekle" (`include_audit_log`, off by default)
appears with the API release whose `EvidenceCreate` accepts the field.

The 5651, evidence package,
sealing and credit answers are **captured from the real API**: `e2e/samples/*.json`, written by

```bash
export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres
uv run python web/scripts/capture_api_samples.py           # rewrite the samples
uv run python web/scripts/capture_api_samples.py --check   # exit 1 if the API's answers no longer match
```

`--check` compares keys, JSON types and the values of enum-like fields (states, statuses, modes,
tones, error codes — `ENUM_KEYS` in the script); messages, times and hashes are not compared. CI
runs it in the Linux Python job.

It runs the production code against a throwaway database and the mock time-stamp authority
(a site day with an IP conflict, a sealed October with a late, a failed and a waiting day,
packages in every state, the API's refusals, including time-outs and unreadable files). The
capture runs on a fixed clock (13.10.2026 07:00 site time, `CLOCK` in the script), not the
machine's date, so a rewrite on any day gives the same days, states and expiry times that the
specs expect (their browser clock is set to the same day). Three things are substituted before writing:
temporary paths, the one-time package password and the `token_sha256` values (`000…001`, …;
the secret scanner reads a random 64-digit value under a "token" key as a credential). The files
are not prettier-formatted (`.prettierignore`). `--check` compares the shape of every answer with
the stored samples; `src/lib/api-samples.test.ts` checks the samples field by field against the
TypeScript interfaces, so a changed API breaks a test instead of silently diverging from the
mock (`e2e/mock-records.ts`).

**Full-stack smoke test (local only, not in CI)** — real API, real PostgreSQL:

```bash
export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres
uv run python web/scripts/live_stack.py --dir /tmp/arci-live --drop --seed   # terminal 1
cd web && ARCI_LIVE_DIR=/tmp/arci-live npm run e2e:live                      # terminal 2
```

`live_stack.py` creates a throwaway database, configuration and secret store, runs
`arcilogger-db upgrade` and `arcilogger-api setup-token`, and serves the API on :8000;
`e2e-live/smoke.spec.ts` sets up the first administrator, enrols TOTP with a code computed from the
displayed key, checks the audit chain and entry hashes, creates a user and signs in as that user.
With `--seed`, yesterday's raw spool files are written in the collector's format and go through
`arcilogger-ingest run --once`, `arcilogger-worker rebuild` and `arcilogger-core seal` (mock
TSA); the test then runs a 5651 lookup in the browser (exact chain down to the person), opens a
raw line from the vault, downloads and hash-checks the PDF, builds and downloads an evidence
package and verifies the day on the sealing page (details in the script's docstring).
