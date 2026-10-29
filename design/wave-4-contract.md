# Wave 4 contract: Uyum Durumu, Sistem and the first-run wizard

- **Status:** final, 2026-10-28 (owner questions Q1 and Q10 answered; the others ship their
  defaults). Scope: roadmap 1.8 (the Uyum Durumu and Sistem pages) and roadmap 1.9 (the remaining
  first-run wizard steps).
- **Base:** `wave-3` at `e7971db`; every `path:line` below was read there (the branch `wave-4`
  starts from `wave-3` at `110096a`, whose two later commits touch none of the cited files). Wave 3
  is still being implemented; §4 says which files its packages own
  (`design/wave-3-contract.md:5098-5462`).
- **Card 7's expected days** were owner question Q10, answered 2026-10-28: content-filter records
  are expected on every day of the week, except the days listed in `usage.holidays` (§2.4).
- **Language:** the contract is in English; every user-facing text is Turkish first with English
  beside it. The only wording about the law is "5651 yükümlülüklerini desteklemek için
  tasarlanmıştır" / "designed to support 5651 obligations". No card, page, PDF or document says a
  site "conforms" or is "uyumlu".
- **Example world only:** Örnek A.Ş., `172.20.x` addresses, MACs `00:00:5E:00:53:xx`, public
  addresses from RFC 5737 (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`). Shipped texts carry no
  organisation name at all (`web/src/locales/locales.test.ts:107-122`).
- **"Sahada doğrula" (verify on site):** anything about Kamu SM, FortiGate, MikroTik or the law that
  the repository does not confirm is configurable or reported as recorded, and is marked
  "sahada doğrula" in the text that shows it.
- **How to read:** §0 rules and decisions, §1 facts, §2 the design (names, JSON, rules, texts), §3
  tests, §4 packages, file ownership and merge order, then "Implementation notes", "Deviations"
  and "Owner questions". Your GitHub issue names the sections of your package.

---

## 0. Rules and decisions

### 0.1 Rules that bind every package

1. Product rules of `CLAUDE.md:29-48` and `CONTRIBUTING.md:3-12`: no legal-compliance claim, raw
   logs untouched, no invented device/Kamu SM/law facts, no secrets, no telemetry, fictional
   example data only, KVKK, no licence state blocks a 5651 function, TR first with EN, every API
   error with `code`, `message_tr`, `message_en`, bound SQL parameters only.
2. Both pages are **5651 Temel**: no `require_module`, no `ModuleGate`
   (`core/src/arcilogger_core/license/modules.py:1-6`, `CLAUDE.md:43`).
3. The pages **only read**: files under `state/` and `admin/`, the vault, the database and active
   alerts. They never ask a device or Kamu SM, never download or install anything. A value such as
   the credit is the newest *recorded* one, shown with its time.
4. **No data about log subjects** on either page or in the PDF: no client IP or MAC, no name of a
   person attributed to a device, no device host name, no sender address, no replica target path.
   Allowed: configured source names, counts, times, drive letters, table names, filter profile
   names, configured time-server addresses. One exception: the PDF names the signed-in ARCILOGGER
   account that exported it (display name and username, as
   `api/src/arcilogger_api/reports/render.py:344-345`). Hence no KVKK notice gate, and **viewing
   is not audited** (as the Panel, `api/src/arcilogger_api/routers/dashboard.py:34-35`). The PDF
   export and the configuration backup confirmation **are** audited.
5. Status is never shown by colour alone: icon + text everywhere (`CONTRIBUTING.md:27`), WCAG 2.1 AA,
   light and dark theme.
6. Automatic refreshes send `X-Arci-Background: 1` (every 30 s, like the Panel:
   `api/src/arcilogger_api/security.py:10-13`, `web/src/lib/api-hooks.ts:886`).
7. Settings-meta texts never contain `uyumlu|uyumu|garanti|complian|guarantee`
   (`db/tests/test_settings_meta.py:31`, `:63`); locale texts never contain a milestone code
   (`M6`) or any phrase of the claim lists (`web/src/locales/locales.test.ts:62-97`,
   `tests/test_docs_consistency.py:852-866`, `:1919-1926`).
8. Owner questions are implemented with the default this contract states and listed in the pull
   request under "Questions" (`CLAUDE.md:26-27`); a package never decides one.

### 0.2 Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Two phases.** Five packages (W4-A…W4-E) start at once and merge into `wave-4` while Wave 3 is still running; they never edit a file a Wave 3 package owns and never cross a coupling point (§4.2). Two **late** packages (W4-F switch-on, W4-G docs) do every edit of a file Wave 3 owns today, after Wave 3 has merged into `wave-4`. | Wave 3 is not finished (`design/wave-3-contract.md:5463-5500`); a new API area, worker job, built page or alert code fails a test whose other half is a Wave 3 file (§4.2). |
| D2 | API code of A, B and D lives in **new packages that `routers/` does not discover**; W4-F mounts them with three two-line modules under `api/src/arcilogger_api/routers/`. Until then their tests mount the router themselves. | `routers/` mounts every module it finds (`api/src/arcilogger_api/routers/__init__.py:1-23`), and a mounted area needs its row in LISANS/LICENSING (`tests/test_docs_consistency.py:1559-1572`). |
| D3 | One server function computes the ten cards; the page and the PDF use it. Cards come back fully worded (TR and EN), so the web shows server text and the PDF says the same. | SPEC 3.4; no drift between page and PDF. |
| D4 | Four tones `ok`, `warning`, `error`, `neutral` — the sealing page's vocabulary (`api/src/arcilogger_api/sealinfo.py:84-99`). `neutral` = "no data yet / does not apply", always with a reason. | One colour/icon map in the web app. |
| D5 | **No new table, no migration** in Wave 4. Disk samples go to `state/system-history.json`; the configuration-backup confirmation is an audit entry. | A migration moves `db/tests/test_migrations.py` `HEAD` (WP-A's file) and needs DATA-MODEL rows (WP-A). The worker may write `state\` (`deploy/windows/installer/ArciInstall.psm1:2089-2095`). |
| D6 | Card 7's settings are three `alerts.*` keys in the existing group "Uyarılar ve e-posta". No new settings group. | The group list is pinned by `web/e2e/settings-application.spec.ts:22-36` (WP-E) and by `db/tests/test_settings_meta.py:20-29`; the keys are also the thresholds of the new alert. |
| D7 | New alert `R20-filter` (dedup key `R20-filter:none`, severity `high`), added only by W4-F. Before that, no Python string constant in `core`, `api`, `worker`, `db`, `ingest`, `collector` has the whole value `R20-filter` (or `R20-lease`): dedup-key prefixes are `"R20-filter:"`, `"R20-lease:"`; a longer text that mentions the rule (the `alerts.filter_days` help text) is fine. | `tests/test_docs_consistency.py:300-323` collects only constants that fully match `R\d{2}-[a-z]+(?:-[a-z]+)*` and turns each into a required ILK-ADIMLAR §9 row (W3-owned). |
| D8 | The **Uyum Durumu Raporu** is a PDF made on request (`GET /compliance/report.pdf`): not stored, not time-stamped, audited with its SHA-256. No schedule in Wave 4 (owner question Q14). | Raporlar is licence module `usage_reports` (`api/src/arcilogger_api/routers/reports.py:70-74`, `worker/src/arcilogger_worker/service.py:410`); a 5651 report must not depend on it, and a new schedule kind needs a migration (D5). |
| D9 | System health comes from files the API may already read: status files, the agent heartbeat, ApplySystem's status, the evidence files of the scheduled tasks, `job_runs`, clock samples. No Service Control Manager or Task Scheduler query (owner question Q19). | No installer, ACL or PowerShell change; works on Linux CI. |
| D10 | CPU and memory through the standard library (`ctypes` on Windows, `/proc` on Linux) in `core/src/arcilogger_core/hostinfo.py`; CPU % is computed between two readings kept in the API process. No `psutil` (owner question Q16). | No new third-party dependency in the offline bundle. |
| D11 | The first-run wizard is a **guided checklist** at `/settings/setup` that opens the existing Ayarlar pages; each step's state is derived from the server's configuration and history; nothing is stored, a skipped step simply stays open (owner question Q18). | Wizard and Ayarlar cannot drift (they are the same forms); `/setup/*` cannot be a sign-in target (`web/src/lib/redirect.ts:8-16`). |
| D12 | Cards name their documentation as text ("UYUM-DURUMU.md bölüm 2.7"); no docs route in the web app (owner question Q15). | Docs are bundled, not served (`deploy/windows/installer/Build-ArciBundle.ps1:314-326`). |
| D13 | Card links are **nav keys**; the web hides a link the user may not open with `linkTarget` (`web/src/pages/dashboard/dashboard-model.ts:101-105`). The card is still shown. | The answer is the same for every user and can be cached. |
| D14 | Once both pages exist, the placeholder mechanism is deleted (`web/src/pages/placeholder-page.tsx`, the `placeholder()` route factory `web/src/routes.tsx:20-28`, `:50`, the `placeholder.*` keys). | No menu entry is a placeholder any more; dead code. |
| D15 | **Permissions (owner questions Q1, Q2).** `compliance.read` is held by `admin` and `auditor` (Q1, answered 2026-10-28): the auditor opens Uyum Durumu with its cards and downloads the Uyum Durumu Raporu (`GET /compliance/report.pdf` needs `compliance.read` only, 2.2). Everything that changes state stays admin-only: the backup confirmation (`settings.manage`), every setting and the wizard. `system.read` (Sistem, the Panel disk card) stays admin-only (Q2). The cards' alert references stay in the auditor's answer although `alerts.read` stays admin-only; the web hides their link (D13). W4-F adds `compliance.read` to `ROLE_PERMISSIONS["auditor"]` (`api/src/arcilogger_api/security.py:107-120`; the permission exists, `:100`). | The cards and the PDF carry no data about log subjects (0.1 item 4; card 8 lists lease-alert titles, which name causes, counts and configured networks only, `design/wave-3-contract.md:2021-2026`, `:2072-2074`): no KVKK notice gate, viewing stays unaudited, the auditor's PDF export is audited like the administrator's. The new holder re-pins the auditor's menu (`web/e2e/permissions.spec.ts:30-43`, a Wave 3 file): a coupling point (§4.2). |

### 0.3 Non-goals of Wave 4

Re-stamping, CRL and the "no legal validity" banner on every page (card 5 only reports the
certificate's remaining days); the rule engine R01-R19 (only `R20-filter` is added); retention of
database tables (card 4 judges sealed days only); automatic off-server copies of dumps and
configuration; vault SACL events; content-filter evidence of vendors other than FortiGate; asking
devices or Kamu SM; downloading updates; any wording that presents the checklist or the PDF as
proof of legal conformity; a scheduled Uyum Durumu Raporu; a `compliance` kind in Raporlar; Task
Scheduler or service-manager queries; a `job_runs` purge; pre-filling sources from any device list;
the web app for role users on their own PCs; changing the verification tasks' schedule (card 3 is
judged on the tasks as they are, owner question Q6).

---

## 1. Facts this contract rests on

| Fact | Where |
|---|---|
| Menu entries `compliance` (`/compliance`, `compliance.read`) and `system` (`/system`, `system.read`) exist; they are missing from `BUILT_PAGES`, so they render the placeholder | `web/src/nav.ts:93-111`, `:119-133`; `web/src/routes.tsx:20-28`, `:50` |
| Tests that pin the two placeholders | `web/src/nav.test.ts:53-60`, `web/src/components/layout/app-shell.test.tsx:115-124`, `web/src/components/layout/shipped-bundle.test.tsx:47-55`, `web/e2e/shell.spec.ts:144-149`, `web/e2e/permissions.spec.ts:60-86` |
| README's placeholder line is tied to `BUILT_PAGES` by a test | `README.md:103`, `tests/test_docs_consistency.py:627-638` |
| Routers are discovered from `routers/`; the prefix→tag table and the licence route table list every route | `api/src/arcilogger_api/routers/__init__.py:13-23`; `api/tests/test_errors_and_app.py:20-54`; `api/tests/test_license_routes.py:71-72`, `:255-259` |
| Every API area and every worker job must be classified in LISANS/LICENSING; every job named in CORRELATION | `tests/test_docs_consistency.py:1470-1492`, `:1559-1595`, `:715-723` |
| Every `R\d{2}-…` string in the Python packages must be in ILK-ADIMLAR/FIRST-STEPS §9 | `tests/test_docs_consistency.py:299-334` |
| The web types test wants an interface for every API schema, in the listed files, and every literal endpoint the app calls must exist | `web/src/lib/api-types.test.ts:22-28`, `:203-210`, `:227-295` |
| Captured samples come from area modules listed in `SAMPLE_MODULES`; the samples test lists its type files | `web/scripts/capture_api_samples.py:203`, `:3067-3072`; `web/src/lib/api-samples.test.ts:30-38` |
| Panel helpers the cards reuse: ingest box, seal box, credit estimate, clock box, `CLOCK_STALE` = 3 h | `api/src/arcilogger_api/routers/dashboard.py:75`, `:243-264`, `:364-385`, `:387-408`, `:430-455` |
| Day states and tones; "verified" = a passing verification within 35 days; deletions; replica per day | `api/src/arcilogger_api/sealinfo.py:80`, `:85-99`, `:260`, `:307-333`, `:338-350`, `:370-428`; credit history `:751` |
| R15 watched sources, newest message, dedup key `R15:<name>`, grace setting | `worker/src/arcilogger_worker/alerts/r15.py:40-47`, `:73-88` |
| R20 checks: keys, `CHECKS`, `drives()`, `backup_state_path()`, `DISK_CRITICAL_PERCENT` = 95 | `worker/src/arcilogger_worker/alerts/system.py:1-62`, `:112`, `:238-256`, `:665-671`, `:860-871` |
| Active alert states `open`, `acknowledged`; `active_alerts` locks rows (`FOR UPDATE SKIP LOCKED`) | `worker/src/arcilogger_worker/alerts/store.py:45`, `:79-87` |
| Alert catalogue: `RuleText`, `RULES`, `_STEPS`, `DESCRIBERS` | `worker/src/arcilogger_worker/alerts/texts.py:52-59`, `:250`, `:997` |
| Thresholds: retention floor 730 and `[retention] enabled` false; credit threshold 500; `legal_grade` = Zamane on the production host; replica off by default; clock limit 1000 ms; backup on | `core/src/arcilogger_core/config.py:36`, `:135`, `:165-175`, `:193-194`, `:219-237`, `:300-304` |
| Alert settings (`alerts.disk_used_percent` 80 … `alerts.backup_missing_hours` 48); work days and holidays exist only as usage settings | `db/src/arcilogger_db/settings.py:161-168`, `:205`, `:209` |
| `setup_problems` (site name, time servers, sources); its `sources` message does not split into TR/EN in the API because its English part contains `[[sources]]` | `core/src/arcilogger_core/config.py:449-511`; `api/src/arcilogger_api/routers/admin_config.py:85`, `:455-492` |
| `verify` writes `state/verify/last-{kind}.json` with `kind` = `full` (`--all`), `day` (`--day`) or `last-<days verified>` (`--last N`): the files are `last-full.json`, `last-day.json`, `last-last-<N>.json`; the existing readers glob `last-*.json` | `core/src/arcilogger_core/cli.py:353-355`; `api/src/arcilogger_api/sealinfo.py:279`; `worker/src/arcilogger_worker/alerts/system.py:400` |
| The seal state of a day keeps `tsa_cert_not_after` | `core/src/arcilogger_core/seal.py:1218-1220` |
| `search_counts`: exact hourly counts per `(hour, source, raw_day, kind, tbl, action, net)`, count column `n`, index `(source, hour)` | `db/src/arcilogger_db/models.py:1947-1990` |
| Web filter rows keep `profile`; DNS filter rows have no profile column | `db/src/arcilogger_db/models.py:295-351` |
| Sources: `type` ∈ fortigate/mikrotik/generic, `enabled`, `expected`, `unknown`, `heartbeat_minutes` | `db/src/arcilogger_db/models.py:73`, `:649-667` |
| Worker jobs, `JobState` in `state/worker-status.json`, `job_runs` with index `(job, started_at)` | `worker/src/arcilogger_worker/service.py:165-184`, `:375-415`, `:421-452`, `:734-746`; `db/src/arcilogger_db/models.py:931-946` |
| Collector and ingest status files (shapes) | `collector/src/arcilogger_collector/service.py:150-175`; `ingest/src/arcilogger_ingest/status.py:1-37` |
| Service freshness 2 min; agent heartbeat; ApplySystem status with `time.peers` | `core/src/arcilogger_core/admin/contract.py:290`, `:1100-1123`; `api/src/arcilogger_api/admin_channel.py:554`, `:613-645`, `:659`, `:1465-1470` |
| Versions and release date; `GET /license` gives `maintenance{until, covers_version}` to any signed-in user | `core/src/arcilogger_core/__init__.py:21-26`; `api/src/arcilogger_api/routers/license.py:84-91`, `:225-235` |
| Neutral report notes to reuse | `api/src/arcilogger_api/reports/texts.py:15-31` |
| Shared PDF fonts and styles | `worker/src/arcilogger_worker/pdf/__init__.py:1-11` |
| Audit writer; details may not hold floats | `api/src/arcilogger_api/audit.py:150-164` |
| Locale areas are found by glob; an area may add leaves to existing groups | `web/src/lib/i18n.ts:49-70`; precedent `web/src/locales/areas/search/tr.json` (`auditActions`) |
| The setup page's "other steps follow in M6" comment | `web/src/pages/setup/setup-page.tsx:50-55` |
| Every Ayarlar page uses `SettingsFrame` with its "way back" link | `web/src/components/admin/settings-frame.tsx:1-30` |

---

## 2. Design

### 2.1 What goes where

| Piece | Files (new unless marked) | Package |
|---|---|---|
| Ten cards, PDF, backup confirmation, API module (unmounted) | `api/src/arcilogger_api/compliance/{__init__,models,texts,cards,pdf,routes}.py` | A |
| Content-filter evidence (shared by card 7 and `R20-filter`) | `worker/src/arcilogger_worker/compliance_filter.py` | A |
| Three settings `alerts.filter_*` | `db/src/arcilogger_db/settings.py`, `settings_meta.py` (changed) | A |
| TS mirror of 2.3.1 | `web/src/lib/compliance-types.ts` | A |
| CPU, memory, uptime | `core/src/arcilogger_core/hostinfo.py`; `core/src/arcilogger_core/layout.py` (changed: `system_history`) | B |
| `/system`, `/system/disk` API module (unmounted) | `api/src/arcilogger_api/system/{__init__,models,resources,forecast,routes}.py` | B |
| Daily disk sample job function | `worker/src/arcilogger_worker/system_jobs.py` | B |
| TS mirror of 2.5.1 | `web/src/lib/system-types.ts` | B |
| `/system/health` API module (unmounted) | `api/src/arcilogger_api/system_health/{__init__,models,tasks,jobs,routes}.py` | D |
| `setup_problems` TR/EN split | `api/src/arcilogger_api/routers/admin_config.py` (changed: `_BILINGUAL`) | D |
| TS mirror of 2.6.1 | `web/src/lib/system-health-types.ts` | D |
| Page views, Panel disk card view, wizard | §2.8 | C |
| Mounting, job registration, `R20-filter`, the auditor's `compliance.read` (D15), hooks and routes, samples, E2E | §4.4 W4-F | F |
| Docs | §2.9 | E, F, G |

### 2.2 Permissions, licence, audit, caching

| Endpoint | Permission | Licence | Audit | Cache |
|---|---|---|---|---|
| `GET /api/v1/compliance` | `compliance.read` (admin, auditor) | none (5651 Temel) | no | 30 s, process-wide (`TtlCache`, `api/src/arcilogger_api/deps.py:64`); cleared by the confirmation below |
| `GET /api/v1/compliance/report.pdf` | `compliance.read` (admin, auditor) | none | `compliance.report_export` | none (fresh computation) |
| `POST /api/v1/compliance/config-backup` | `settings.manage` (admin) | none | `compliance.config_backup_confirm` | — |
| `GET /api/v1/system` | `system.read` (admin) | none | no | database sizes 5 min; the rest per request |
| `GET /api/v1/system/disk` | `system.read` (admin) | none | no | none |
| `GET /api/v1/system/health` | `system.read` (admin) | none | no | none |

Holders as D15 decides them (the auditor's `compliance.read` arrives with W4-F); hr and viewer
hold none of the three permissions.

Routers: A `APIRouter(prefix="/compliance", tags=["compliance"])`, B
`APIRouter(prefix="/system", tags=["system"])`, D `APIRouter(prefix="/system/health",
tags=["system"])`. Dependencies use `require(...)` (`api/src/arcilogger_api/security.py:359`).
Refusals: 401/403 as every route; 503 `unavailable` when the database is down (existing handler).

### 2.3 Uyum Durumu (W4-A)

#### 2.3.1 JSON — `web/src/lib/compliance-types.ts` (verbatim; the Pydantic models in `compliance/models.py` have the same names, fields and optionality, every field required)

```ts
/** Mirror of api/src/arcilogger_api/compliance/models.py (Wave 4 contract 2.3.1). */
import type { AlertSeverity, DateTimeString } from './api-types';

export type ComplianceTone = 'ok' | 'warning' | 'error' | 'neutral';
export type ComplianceCardId =
  | 'sources'
  | 'seal_yesterday'
  | 'chain'
  | 'retention'
  | 'tsa'
  | 'clock'
  | 'content_filter'
  | 'lease_evidence'
  | 'replica'
  | 'backup';
/** Nav key of the page with the details (web/src/nav.ts). */
export type ComplianceLinkPage = 'sources' | 'sealing' | 'search' | 'settings' | 'system';

export interface ComplianceFact {
  key: string;
  label_tr: string;
  label_en: string;
  value_tr: string;
  value_en: string;
}

export interface ComplianceAlertRef {
  id: number;
  rule: string;
  severity: AlertSeverity;
  opened_at: DateTimeString;
}

export interface ComplianceCard {
  id: ComplianceCardId;
  number: number;
  tone: ComplianceTone;
  title_tr: string;
  title_en: string;
  reason_tr: string;
  reason_en: string;
  facts: ComplianceFact[];
  alerts: ComplianceAlertRef[];
  page: ComplianceLinkPage;
  doc_tr: string;
  doc_en: string;
  verify_on_site_tr: string | null;
  verify_on_site_en: string | null;
  checked_at: DateTimeString;
}

export interface ComplianceCounts {
  ok: number;
  warning: number;
  error: number;
  neutral: number;
}

export interface ComplianceStatus {
  generated_at: DateTimeString;
  site_name: string | null;
  counts: ComplianceCounts;
  cards: ComplianceCard[];
  notice_tr: string;
  notice_en: string;
}

export interface ConfigBackupOut {
  confirmed_at: DateTimeString;
  config_sha256: string;
}
```

`cards` always holds the ten cards in the order of 2.3.3 (`number` 1-10). `alerts`: the card's
active alerts (states `open`, `acknowledged`), newest first, at most 5. `site_name`: `null` when
not set. Times are site time with offset (`to_site`).

**Types (also 2.5.1 and 2.6.1):** a `DateTimeString` field is a timezone-aware `datetime` in the
Pydantic model, a `DateString` field a `date`, and every TS union of literals a `Literal[...]`;
`web/src/lib/api-types.test.ts:127`, `:162-170` compare them so once W4-F adds the file to
`TYPES_FILES`.

`notice_tr` = "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Bu kartlar kayıt
toplama, mühürleme ve yedeklemenin teknik denetimlerinin sonucudur; hukuki bir değerlendirme ya da
uygunluk beyanı değildir. Sonuçları hukuk müşavirinizle teyit edin."
`notice_en` = "ARCILOGGER is designed to support 5651 obligations. These cards are the results of
technical checks of log collection, sealing and backups; they are neither a legal assessment nor a
statement of conformity. Confirm the results with your legal counsel."

#### 2.3.2 Rules common to all cards

- `cards.compute_status(db, layout, config, channel, now) -> ComplianceStatus`; one function per
  card, `card_<id>(ctx) -> ComplianceCard`. A card whose computation raises is returned as
  `neutral` with reason "Bu denetim şu anda yapılamadı: {error_code}" / "This check could not be
  run now: {error_code}" (the exception class name, never its message) and the error is logged; the
  other cards are unaffected.
- **Evaluation:** the conditions of a card are tried in the order of its table; the first that holds
  gives `tone` and the reason. A card with no matching condition is `ok` with its `ok` reason.
- **Alerts of a card** are read with a plain `SELECT` (no lock; never `active_alerts`, which locks:
  `worker/src/arcilogger_worker/alerts/store.py:79-87`) over `Alert.state IN ('open',
  'acknowledged')` and the card's dedup-key patterns: `R15:` prefix (1); `R20-seal:<yesterday>`
  exact (2); `R20-verify` exact (3); `R20-retention` prefix (4); `R20-credit` exact (5);
  `R20-clock` prefix (6); `R20-filter:` prefix (7); `R20-lease:` prefix (8); `R20-replica` prefix (9);
  `R20-backup` prefix (10).
- **Formatting:** dates/times with `arcilogger_core.fmt.format_date`/`format_datetime` (site time) in
  both languages; sizes "1,2 GB" (TR) / "1.2 GB" (EN); lists of names cut after 5 with "+{k}".
- **Doc reference:** `doc_tr` = `UYUM-DURUMU.md bölüm 2.{number}`, `doc_en` =
  `COMPLIANCE-STATUS.md section 2.{number}` (W4-E writes those sections).
- No card text names a person, a client address, a device host name or the replica target.

#### 2.3.3 The ten cards

Placeholders in braces are filled by the card. "watched sources" = `watched_sources(db)`
(`worker/src/arcilogger_worker/alerts/r15.py:73-82`). Every reason exists in TR and EN exactly as
given.

**Card 1 `sources` — "Log kaynakları veri gönderiyor" / "Log sources are sending"** · page `sources`

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | an active alert `R15:<name>` | "{n} kaynak kalp atışı süresinden uzun süredir veri göndermiyor: {names}." / "{n} source(s) have sent nothing for longer than their heartbeat: {names}." |
| warning | a watched source is silent beyond its `heartbeat_minutes` (as `ingest_box`, `dashboard.py:243-264`) without an R15 alert | "{names} kalp atışı süresinden uzun süredir sessiz; uyarı başlangıç bekleme süresi içinde." / "{names} silent for longer than the heartbeat; the alert is still in its start-up grace period." |
| warning | no watched source | "İzlenen log kaynağı yok. Kaynakları Ayarlar → Kaynaklar'da tanımlayın." / "No log source is watched. Define the sources in Settings → Sources." |
| ok | — | "İzlenen {n} kaynağın tümü kalp atışı süresi içinde veri gönderdi." / "All {n} watched sources sent data within their heartbeat." |

Facts: `watched` İzlenen kaynak / Watched sources; `active` Veri gönderen / Sending; `silent`
Sessiz / Silent (names or "—"); `heartbeat` Kalp atışı süresi / Heartbeat = "kaynağa göre,
varsayılan 15 dk" / "per source, default 15 min".

**Card 2 `seal_yesterday` — "Dünün kayıtları mühürlendi ve doğrulandı" / "Yesterday's records sealed and verified"** · page `sealing`

Data: `VaultView(layout, config, now=now).summary(yesterday, on_demand)` as `seal_box` does
(`dashboard.py:364-385`); overdue hour `alerts.seal_overdue_hour` (09:00).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | state `failed` | "Dünün ({day}) mühürlenmesi ya da doğrulaması başarısız: {message}" / "Sealing or verification of yesterday ({day}) failed: {message}" (`message`: the summary's `message_tr` in the TR text, `message_en` in the EN text, else "—"; `sealinfo.py:374-375`, `:415`; never `error_detail`) |
| error | state `pending` and now ≥ today at the overdue hour | "Dün ({day}) saat {hour}:00'a kadar mühürlenmedi." / "Yesterday ({day}) was not sealed by {hour}:00." |
| error | state `no_data` and at least one watched source | "Dün ({day}) için ham kayıt yok; kaynaklar veri göndermemiş olabilir." / "There are no raw records for yesterday ({day}); the sources may have sent nothing." |
| warning | state `pending` before the overdue hour | "Dünün ({day}) mühürlenmesi bekleniyor (Seal görevi 00:10'dan sonra çalışır)." / "Yesterday ({day}) is waiting to be sealed (the Seal task runs after 00:10)." |
| warning | state `sealed` | "Dün ({day}) mühürlendi, henüz doğrulanmadı (VerifyDaily 01:00'de çalışır)." / "Yesterday ({day}) is sealed but not verified yet (VerifyDaily runs at 01:00)." |
| warning | state `late` | "Dün ({day}) geç damgalandı." / "Yesterday ({day}) was time-stamped late." |
| warning | state `verified` and not `config.tsa.legal_grade` | "Dün mühürlendi ve doğrulandı, ancak damga Kamu SM üretim sunucusundan değil." / "Yesterday was sealed and verified, but the time stamp is not from the Kamu SM production server." |
| neutral | state `no_data` and no watched source | "Dün için kayıt yok; henüz izlenen kaynak tanımlanmadı." / "No records for yesterday; no watched source is defined yet." |
| ok | state `verified` | "Dün ({day}) mühürlendi ve doğrulandı." / "Yesterday ({day}) was sealed and verified." |

Facts: `day` Gün / Day; `state` Durum / State (the summary's `label_tr`/`label_en`); `sealed_at`
Mühürlendi / Sealed; `verification` Doğrulama / Verification ("{time}, başarılı|başarısız" /
"{time}, passed|failed" or "—"); `server` Damga sunucusu / Time-stamp server (2.3.3 card 5 labels).

**Card 3 `chain` — "Mühür zinciri bütün" / "Seal chain intact"** · page `sealing`

Data: the 30 day cells ending yesterday through `summary` (as `seal_box`); `state/verify/last-full.json`
(`at`, `ok`, `deep`; `cli.py:344-360`); the first sealed day (`arcilogger_core.chain.sealed_days`,
`core/src/arcilogger_core/chain.py:67-75`). The verification tasks stay as they are (daily 2 days,
weekly 90, monthly the whole chain deep: owner question Q6).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | a cell `failed` | "Son 30 günde {n} gün başarısız: {days}." / "{n} day(s) of the last 30 failed: {days}." |
| error | `last-full.json` has `ok: false`, or an active `R20-verify` | "Tam zincir denetimi ({at}) başarısız." / "The full chain check ({at}) failed." |
| warning | a cell `sealed` (no passing verification within 35 days) | "Son 30 günde {n} mühürlü gün son 35 günde doğrulanmadı." / "{n} sealed day(s) of the last 30 were not verified in the last 35 days." |
| warning | a cell `pending` older than yesterday | "Son 30 günde {n} gün henüz mühürlenmedi: {days}." / "{n} day(s) of the last 30 are not sealed yet: {days}." |
| warning | `last-full.json` older than 35 days, or missing while the first sealed day is ≥ 35 days ago | "Son tam zincir denetimi {at}; 35 günden eski." / "The last full chain check was on {at}, more than 35 days ago." — missing: "Tam zincir denetimi hiç çalışmadı." / "The full chain check has never run." |
| neutral | no sealed day among the 30 | "Son 30 günde mühürlü gün yok." / "No sealed day in the last 30 days." |
| ok | — | with a full check: "Son 30 günün mühürlü {n} günü doğrulandı; tam zincir denetimi {at} tarihinde başarılı." / "All {n} sealed days of the last 30 are verified; the full chain check of {at} passed." — before the first one: "Son 30 günün mühürlü {n} günü doğrulandı; tam zincir denetimi her ayın 1'inde çalışır, henüz çalışmadı." / "All {n} sealed days of the last 30 are verified; the full chain check runs on the 1st of each month and has not run yet." |

`late`, `no_data`, `deleted`, `open` cells do not change the tone (they are counted). Facts:
`verified`, `not_verified`, `failed`, `no_data` (counts; labels Doğrulanmış / Verified, Doğrulanmamış
/ Not verified, Başarısız / Failed, Veri yok ya da silinmiş / No data or deleted); `full_check` Son
tam zincir denetimi / Last full chain check; `tasks` Doğrulama görevleri / Verification tasks =
"günlük son 2 gün, haftalık son 90 gün, aylık tüm zincir" / "daily the last 2 days, weekly the last
90 days, monthly the whole chain".

**Card 4 `retention` — "Saklama süresi" / "Retention period"** · page `sealing`

Data: `config.retention.enabled`, `.days`, `MIN_RETENTION_DAYS` = 730 (`config.py:36`, `:173-175`);
oldest sealed day still in the vault (`sealed_days`); deleted days (`VaultView.deletions`,
`sealinfo.py:307-333`). "First sealed day" = the earliest of both (owner question Q7).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | an active `R20-retention…` alert | "Saklama görevinin açık bir uyarısı var." / "The retention task has an open alert." |
| error | first sealed day ≤ today − 730 and oldest sealed day in the vault > today − 730 | "Kurulum 730 günden eski, ancak en eski mühürlü gün {oldest} ({age} gün önce)." / "The installation is older than 730 days, but the oldest sealed day is {oldest} ({age} days ago)." |
| warning | retention off and oldest sealed day < today − `retention.days` | "Saklama kapalı ve {days} günden eski {n} mühürlü gün kasada duruyor (en eskisi {oldest}). Kişisel verilerin gereğinden uzun saklanmaması için saklamayı açın." / "Retention is off and {n} sealed day(s) older than {days} days are in the vault (the oldest {oldest}). Turn retention on so that personal data is not kept longer than needed." |
| neutral | no sealed day | "Henüz mühürlü gün yok." / "No sealed day yet." |
| ok | — | age ≥ 730: "En eski mühürlü gün {oldest} ({age} gün önce); 730 günlük süre kasada." / "The oldest sealed day is {oldest} ({age} days ago); the vault holds the 730 days." — younger: "En eski mühürlü gün {oldest} ({age} gün önce); kurulum 730 günden genç." / "The oldest sealed day is {oldest} ({age} days ago); the installation is younger than 730 days." |

Facts: `oldest` En eski mühürlü gün / Oldest sealed day; `retention` Saklama / Retention ("açık,
{days} gün" / "on, {days} days" or "kapalı" / "off"); `deleted` Saklama ile silinen gün / Days
deleted by retention.

**Card 5 `tsa` — "Kamu SM kontörü ve zaman damgası sertifikası" / "Kamu SM credit and time-stamp certificate"** · page `sealing`

Data: `config.tsa.provider`, `config.tsa.legal_grade` (`config.py:165-170`); `credit_box` and
`credit_history` (`dashboard.py:387-408`, `sealinfo.py:751`); `tsa_cert_not_after` of the newest
sealed day's state (`read_seal_state`, `sealinfo.py:151`; written at `seal.py:1218-1220`). The
180 days are the specification's number; "two days" is two missed runs of the daily Credit task.

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | not `legal_grade` | "Damgalar Kamu SM üretim sunucusundan alınmıyor (sağlayıcı: {provider}). Kamu SM bağlantısını Ayarlar → Kamu SM'de tamamlayın." / "Time stamps are not taken from the Kamu SM production server (provider: {provider}). Complete the Kamu SM connection in Settings → Kamu SM." |
| error | the newest known credit is 0 | "Kamu SM kontörü bitti." / "The Kamu SM credit is used up." |
| error | `tsa_cert_not_after` < now | "Son damganın TSA sertifikasının süresi {date} tarihinde doldu." / "The TSA certificate of the newest time stamp expired on {date}." |
| warning | credit ≤ `[tsa] credit_alert_threshold` | "Kalan kontör {credits}; eşik {threshold}. Tahmini {days} gün yeter." / "{credits} credits left; threshold {threshold}. Estimated to last {days} days." |
| warning | no credit reading, or the newest is older than 2 days | "Kontör bilinmiyor ya da son okuma ({at}) iki günden eski." / "The credit is unknown or the last reading ({at}) is more than two days old." |
| warning | certificate valid ≤ 180 more days | "Son damganın TSA sertifikası {date} tarihinde doluyor ({days} gün kaldı)." / "The TSA certificate of the newest time stamp expires on {date} ({days} days left)." |
| neutral | no sealed day yet | "Henüz Kamu SM ile damgalanmış gün yok." / "No day has been stamped with Kamu SM yet." |
| ok | — | "Kalan kontör {credits} (eşik {threshold}); son damganın TSA sertifikası {days} gün daha geçerli." / "{credits} credits left (threshold {threshold}); the TSA certificate of the newest time stamp is valid for {days} more days." |

Facts: `server` Damga sunucusu / Time-stamp server ("Kamu SM üretim" / "Kamu SM production", "Kamu
SM test" / "Kamu SM test", "Deneme (mock)" / "Trial (mock)", "RFC 3161" / "RFC 3161"); `credits`
Kalan kontör; `estimated_days` Tahmini gün; `threshold` Eşik; `credit_at` Son okuma; `cert_until`
TSA sertifikası bitişi; `cert_days` Kalan gün. `verify_on_site_tr` = "Kontör değeri Kamu SM
istemcisinin çıktısından okunur; okuma biçimi sahada doğrulanmalıdır." / `verify_on_site_en` =
"The credit is read from the Kamu SM client's output; verify the format on site."
(`config.py:133-134`).

**Card 6 `clock` — "Sunucu saati" / "Server clock"** · page `system`

Data: `clock_box(layout, config, now)` (`dashboard.py:430-455`); limit `[clock] alert_offset_ms`.
The card counts an offset **equal to** the limit as a problem ("< 1 s"; the Panel uses `<=`,
owner question Q9).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | sample within 3 h and abs(offset) ≥ limit | "Saat farkı {offset} ms; sınır {limit} ms." / "Clock offset {offset} ms; limit {limit} ms." |
| error | sample within 3 h without an answer, or an active `R20-clock…` alert | "Saat sunucusuna ulaşılamadı ya da saat uyarısı açık ({server})." / "The time server could not be reached or a clock alert is open ({server})." |
| warning | no sample, or the newest is older than 3 h (`CLOCK_STALE`) | "Son saat ölçümü {at}; 3 saatten eski (ClockCheck saatte bir çalışır)." / "The last clock measurement was at {at}, more than 3 hours ago (ClockCheck runs hourly)." — none: "Henüz saat ölçümü yok." / "No clock measurement yet." |
| warning | `[clock] ntp_server` empty | "Yerel saat kaynağı ayarlanmamış; saat yalnızca yedek sunucuyla ({fallback}) eşitleniyor." / "No local time source is set; the clock follows only the fallback server ({fallback})." |
| ok | — | "Saat farkı {offset} ms ({server}, {at}); sınır {limit} ms." / "Clock offset {offset} ms ({server}, {at}); limit {limit} ms." |

Facts: `checked_at` Son ölçüm; `server` Sunucu; `offset` Fark; `limit` Sınır; `local_source` Yerel
saat kaynağı ("ayarlı" / "set" or "yok" / "none").

**Card 7 `content_filter` — "İçerik filtresi kayıtları" / "Content filter records"** · page `search`

Data: `filter_evidence(...)` of 2.4.

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| neutral | no enabled, non-unknown source of type `fortigate` | "FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor." / "No FortiGate source is defined; no content filter records are expected." |
| error | `alert_due` (2.4) | "Beklenen günlerde son {hours} saattir web ya da DNS filtresi kaydı gelmedi (son kayıt: {last})." / "No web or DNS filter record in the last {hours} hours of expected days (last record: {last})." |
| warning | no filter record within the window, not `alert_due` | "Son {hours} saatte içerik filtresi kaydı yok; bu süre beklenmeyen bir gün içeriyor (son kayıt: {last})." / "No content filter record in the last {hours} hours; this period includes a day on which none is expected (last record: {last})." |
| warning | a kind in `silent_kinds` | "{kind} filtresi kaydı {hours} saattir gelmiyor; diğer tür geliyor." / "No {kind} filter record for {hours} hours; the other kind still arrives." (`kind`: "Web" / "Web", "DNS" / "DNS") |
| ok | — | "Son kayıt {last}; bugün {web} web, {dns} DNS filtresi kaydı." / "Last record {last}; today {web} web and {dns} DNS filter records." |

`{last}` is "—" when there is none in 8 days. Facts: `last` Son kayıt; `today_web` Bugün web
filtresi; `today_dns` Bugün DNS filtresi; `days` Son 7 gün / Last 7 days ("13.10: 1234 · 12.10: …");
`profiles` Web filtresi profilleri / Web filter profiles (at most 10 names or "—"); `dns_profile` DNS
filtresi profili / DNS filter profile = "FortiGate DNS filtresi kayıtlarından profil adı okunmuyor
(sahada doğrula)." / "No profile name is read from FortiGate DNS filter records (verify on site)."
(`db/src/arcilogger_db/models.py:328-351`); `expected_days` Beklenen günler (the default: "Pzt, Sal,
Çar, Per, Cum, Cmt, Paz" / "Mon, Tue, Wed, Thu, Fri, Sat, Sun"); `holidays` Resmî tatiller / Public
holidays = "beklenmez" / "not expected" (default) or "beklenen gün sayılır" / "count as expected
days".
`verify_on_site_tr` = "Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını kaydettiği ve
ARCILOGGER'a gönderdiği sahada doğrulanmalıdır." / `verify_on_site_en` = "Verify on site that the
firewall policies log web and DNS filter events and send them to ARCILOGGER."

**Card 8 `lease_evidence` — "DHCP ve ARP kayıtları (erişim kayıtları)" / "DHCP and ARP records (access records)"** · page `sources`

Data: active alerts with the dedup-key prefix `R20-lease:` (Wave 3 WP-B adds them,
`design/wave-3-contract.md:1982-2060`; none exist before Wave 3 merges); `search_counts` rows of
the last 24 h with `tbl IN ('mt_dhcp', 'mt_arp', 'fg_dhcp')` and `source IN (<watched source
names>)` (index `ix_search_counts_source_hour`). The card follows the configured evidence, not
"DHCP and ARP" literally: a FortiGate-only site has no ARP log (owner question Q11).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| neutral | no watched source | "İzlenen kaynak yok; erişim kaydı beklenmiyor." / "No watched source; no access records are expected." |
| error | an active `R20-lease:` alert of severity `high` or `critical` | "{n} kira kanıtı sorunu açık." / "{n} lease evidence problem(s) open." |
| warning | an active `R20-lease:` alert of another severity | the same text |
| warning | no row of the three tables in 24 h | "Son 24 saatte DHCP ya da ARP kaydı gelmedi." / "No DHCP or ARP record arrived in the last 24 hours." |
| ok | — | "Son 24 saatte {tables} kayıtları geldi; açık kira kanıtı uyarısı yok." / "{tables} records arrived in the last 24 hours; no lease evidence alert is open." |

Facts: `mt_dhcp` MikroTik DHCP, `mt_arp` MikroTik ARP, `fg_dhcp` FortiGate DHCP — each "{n} satır
(24 saat)" / "{n} rows (24 h)"; `problems` Açık kira kanıtı sorunları / Open lease evidence
problems = the open alerts' titles, Turkish `title`, English `details.title_en` (Wave 3 writes both,
`design/wave-3-contract.md:2072-2074`; without `title_en`: "Lease evidence problem"), at most 5 with
"+{k}", else "—". Never the dedup keys.

**Card 9 `replica` — "İkinci kopya (replika)" / "Second copy (replica)"** · page `sealing`

Data: `config.replica.enabled`; `VaultView.replica(day)` for the sealed days of the last 7 days
ending yesterday (`sealinfo.py:338-350`); `alerts.replica_stale_hours` (48).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | replica disabled (owner question Q12) | "Replika kapalı: mühürlü günlerin ikinci kopyası alınmıyor." / "The replica is off: no second copy of the sealed days is made." |
| error | an active `R20-replica…` alert | "Replika uyarısı açık: kopya gecikti ya da kopyalama başarısız." / "A replica alert is open: a copy is late or copying failed." |
| warning | a sealed day of the 7 without a verified copy | "{n} mühürlü gün henüz kopyalanmadı (en eskisi {day})." / "{n} sealed day(s) not copied yet (the oldest {day})." |
| neutral | no sealed day among the 7 | "Son 7 günde mühürlü gün yok." / "No sealed day in the last 7 days." |
| ok | — | "Son 7 günün mühürlü {n} günü replika hedefine kopyalandı ve denetlendi." / "All {n} sealed days of the last 7 were copied to the replica target and checked." |

Facts: `enabled` Replika; `last_copy` Son kopya; `pending` Bekleyen gün; `stale_hours` Gecikme
eşiği (saat). Never the target path. `verify_on_site_tr` = "Replika hedefinin değiştirilemez (WORM)
olduğu ARCILOGGER'dan denetlenemez; sahada doğrulayın." / `verify_on_site_en` = "Whether the
replica target is write-once (WORM) cannot be checked from ARCILOGGER; verify it on site."

**Card 10 `backup` — "Yapılandırma ve veritabanı yedekleri" / "Configuration and database backups"** · page `settings`

Data: `state/backup/last.json` (`backup_state_path`, `alerts/system.py:665-666`); `[backup] enabled`;
`alerts.backup_stale_hours` (36), `alerts.backup_missing_hours` (48); the installation's first
`job_runs.started_at`; the newest audit entry `compliance.config_backup_confirm` (its
`details.config_sha256`) against the current file's SHA-256 (`Channel.files`,
`admin_channel.py:659`; owner question Q13).

| Tone | Condition | reason_tr / reason_en |
|---|---|---|
| error | `[backup] enabled = false` | "Gece veritabanı yedeği kapalı." / "The nightly database backup is off." |
| error | an active `R20-backup…` alert, or `last.json` with `ok: false` | "Son veritabanı yedeği başarısız ya da yedek uyarısı açık." / "The last database backup failed or a backup alert is open." |
| neutral | no `last.json` and the first `job_runs` row is younger than `alerts.backup_missing_hours` | "İlk veritabanı yedeği henüz alınmadı (Backup görevi 01:40'ta çalışır)." / "The first database backup has not run yet (the Backup task runs at 01:40)." |
| warning | the last successful backup is older than `alerts.backup_stale_hours` | "Son başarılı veritabanı yedeği {at}; {hours} saatten eski." / "The last successful database backup was at {at}, more than {hours} hours ago." |
| warning | never confirmed | "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3)." / "The offline copy of the configuration files was never confirmed (BACKUP.md section 3)." |
| warning | confirmed for another SHA-256 | "Yapılandırma son onaydan ({at}) sonra değişti; yeni bir çevrimdışı kopya alın." / "The configuration changed after the last confirmation ({at}); make a new offline copy." |
| ok | — | "Son veritabanı yedeği {at} ({size}); yapılandırma kopyası {confirmed} tarihinde onaylandı." / "Last database backup {at} ({size}); configuration copy confirmed on {confirmed}." |

Facts: `last_backup` Son veritabanı yedeği; `size` Boyut; `enabled` Gece yedeği; `config_copy`
Yapılandırma kopyası onayı (time or "hiç" / "never"). Never who confirmed.

#### 2.3.4 `GET /compliance/report.pdf` — "Uyum Durumu Raporu" / "Compliance status report"

- Computes `compute_status` fresh, renders it with ReportLab and the shared fonts and styles of
  `arcilogger_worker.pdf` (`worker/src/arcilogger_worker/pdf/__init__.py:1-11`), A4.
- Content, in this order: title TR and EN; site name; "Oluşturulma / Generated: {time}
  (Europe/Istanbul)"; "Oluşturan / Exported by: {display name} ({username})" of the signed-in account (the one
  exception of 0.1 item 4); the summary line of
  2.8.5; `notice_tr` and `notice_en`; then per card: "{number}. {title_tr} / {title_en}", the tone
  as text in brackets ("[TAMAM] / [OK]", "[DİKKAT] / [ATTENTION]", "[SORUN] / [PROBLEM]", "[BİLGİ
  YOK] / [NO DATA]"), `reason_tr`, `reason_en`, the facts as a two-column table (TR label, TR value),
  the verify-on-site note when present, the active alerts as "rule, severity, opened" and
  `checked_at`. Every page's footer: `FOOTER_NOTE_TR`/`FOOTER_NOTE_EN`
  (`api/src/arcilogger_api/reports/texts.py:25-32`) and "Sayfa n / N".
- Response: `200 application/pdf`, `Content-Disposition: attachment;
  filename="ARCILOGGER-uyum-durumu-<YYYYMMDD-HHMM>.pdf"` (site time), `X-Arci-Sha256: <hex>`.
- Audit before the response: action `compliance.report_export`, target `null`, details
  `{"sha256": "<hex>", "ok": n, "warning": n, "error": n, "neutral": n}` (integers only).
- Not time-stamped (owner question Q14).

#### 2.3.5 `POST /compliance/config-backup`

No body. Records that the administrator has copied `arcilogger.toml`, `secret.key` and the Caddy CA
folder (with its private key) to encrypted offline media kept apart from the backup copies;
`secrets.json` is optional there (YEDEKLEME.md §3, `docs/YEDEKLEME.md:117-134`; the confirmation
text is `compliance.confirmBackupBody`, 2.8.5). Reads the current file SHA-256
(`Channel.files.sha256`); `None` → 409 `config_backup_unreadable`. Otherwise writes the audit entry
`compliance.config_backup_confirm` with details `{"config_sha256": "<hex>"}`, clears the 30 s
cache and answers `200 ConfigBackupOut`. A repeated confirmation is allowed (a new entry).

### 2.4 Content-filter evidence, its settings and `R20-filter`

**Settings (W4-A, group `alerts`, `db/src/arcilogger_db/settings.py` and `settings_meta.py`):**

| Key | Type | Default | Bounds | label_tr / label_en |
|---|---|---|---|---|
| `alerts.filter_days` | integer list | `[1, 2, 3, 4, 5, 6, 7]` | each 1-7; stored sorted without duplicates; may be empty | "İçerik filtresi kaydı beklenen günler" / "Days on which content filter records are expected" |
| `alerts.filter_skip_holidays` | boolean | `true` | — | "Resmî tatillerde içerik filtresi kaydı bekleme" / "Expect no content filter records on public holidays" |
| `alerts.filter_quiet_hours` | integer | `24` | 6-168 | "İçerik filtresi kaydı olmadan geçebilecek süre (saat)" / "Hours allowed without content filter records" |

Labels are at most 60 characters and help texts end with a full stop
(`db/tests/test_settings_meta.py:59-61`).

Help texts (TR / EN):
- `filter_days`: "ISO hafta günleri (1 = Pazartesi, 7 = Pazar). Bu günlerde aşağıdaki süre boyunca
  web ya da DNS filtresi kaydı gelmezse Uyum Durumu'nun 7. kartı kırmızı olur ve R20-filter uyarısı
  açılır. Varsayılan: haftanın her günü." / "ISO weekdays (1 = Monday, 7 = Sunday). If no web or DNS
  filter record arrives for the time below on these days, card 7 of the Uyum Durumu page turns red
  and the R20-filter alert opens. Default: every day of the week."
- `filter_skip_holidays`: "Açıkken (varsayılan) Kullanım analizi'nin tatil listesindeki
  (usage.holidays) günler beklenmez. Kapalıyken resmî tatiller de beklenen gün sayılır." / "On (the
  default): the days in the holiday list of usage analytics (usage.holidays) are not expected. Off:
  public holidays count as expected days."
- `filter_quiet_hours`: "Beklenen günlerde kayıtsız geçebilecek en uzun süre." / "The longest time
  without a record on expected days."

`alerts.filter_days` validation: a non-integer or out-of-range day is refused with the existing code
`setting_work_hours_days` (`db/src/arcilogger_db/settings.py:376-379`, its texts name the ISO
weekdays 1-7); duplicates are dropped and the list stored as `sorted(set(value))`, like
`usage.background_ports` (`:935-940`). No new `SETTING_ERRORS` code: `db/tests/test_settings_admin.py:270`
ties the catalogue to the refusals of a test file W4-A does not own.

**`worker/src/arcilogger_worker/compliance_filter.py` (W4-A):**

```python
@dataclass(frozen=True)
class FilterEvidence:
    applies: bool  # an enabled, non-unknown source of type "fortigate" exists
    last_at: datetime | None  # newest `time` of fg_web or fg_dns within 8 days
    today: Mapping[str, int]  # {"fg_web": n, "fg_dns": n}, the site day so far
    days: Sequence[tuple[date, int, int]]  # last 7 site days: (day, web, dns), newest first
    web_profiles: Sequence[str]  # distinct profiles of the newest 10 000 fg_web rows of 24 h
    window_hours: int  # alerts.filter_quiet_hours
    window_expected: bool  # every site day touched by [now - window, now] is expected
    alert_due: bool  # applies and no record in the window and window_expected
    silent_kinds: Sequence[str]  # "web"/"dns" seen in the 7 days but not in the window


def filter_evidence(session: Session, *, now: datetime) -> FilterEvidence: ...
```

- Counts come from `search_counts` (`tbl IN ('fg_web', 'fg_dns')`, `source IN (<FortiGate source
  names>)`, `hour >= now - 8 days`, sum of `n`); `last_at` from `SELECT max(time)` of each table
  within 8 days (time index); profiles from `SELECT DISTINCT profile FROM (SELECT
  profile FROM fg_web WHERE time >= :since ORDER BY time DESC LIMIT 10000)`; under 0.5 s on the
  perf data of §3.
- A site day is **expected** when its ISO weekday is in `alerts.filter_days` and not (
  `alerts.filter_skip_holidays` and the day is in `usage.holidays`; "MM-DD" every year or
  "YYYY-MM-DD" once, `db/src/arcilogger_db/settings.py:209`).
- **Default** (owner question Q10, answered 2026-10-28): every day of the week is expected
  (`alerts.filter_days` 1-7) except the listed holidays (`alerts.filter_skip_holidays` is `true`).
  `usage.holidays` ships the fixed-date national holidays; the administrator enters the others
  (`db/src/arcilogger_db/settings.py:206-209`).

**`R20-filter` (W4-F, `worker/src/arcilogger_worker/alerts/system.py`, `alerts/texts.py`):**
- `check_filter(ctx)` appended to `CHECKS` as `("filter", check_filter)`; it calls
  `filter_evidence(ctx.session, now=ctx.now)`; when `alert_due`: one `Finding(key="R20-filter:none",
  rule="R20-filter", severity="high", title="İçerik filtresi kaydı gelmiyor: son {hours} saat",
  details={"hours": n, "last_at": iso | None})`; it owns keys starting with `R20-filter`; an owned
  alert is resolved (`details.auto_resolved`) when `alert_due` no longer holds.
- `RULES["R20-filter"] = RuleText("İçerik filtresi kaydı gelmiyor", "No content filter records",
  "Güvenlik duvarının web ve DNS filtresi kayıtlarını gönderdiğini denetleyin (Uyum Durumu, kart 7).",
  "Check that the firewall sends its web and DNS filter records (Uyum Durumu, card 7).")`.
- `_STEPS["R20-filter"]`: TR "1. Uyum Durumu sayfasında 7. kartı açın: son kaydı ve beklenen günleri
  görün.\n2. Güvenlik duvarında web ve DNS filtresi kullanan politikalarda kayıt tutmanın açık olduğunu
  ve kayıtların ARCILOGGER'a gönderildiğini denetleyin (DEVICE-SETUP.md; sahada doğrulayın).\n3.
  Kurum bu günlerde çalışmıyorsa beklenen günleri ve tatil ayarını Ayarlar → Uygulama ayarları →
  Uyarılar ve e-posta'da düzeltin.\n4. Yeni bir içerik filtresi kaydı gelince uyarı kendiliğinden
  kapanır." / EN "1. Open card 7 on the Uyum Durumu page: see the last record and the expected
  days.\n2. Check on the firewall that the policies using web and DNS filtering log, and that the
  records are sent to ARCILOGGER (DEVICE-SETUP.md; verify on site).\n3. If the organisation does not
  work on these days, correct the expected days and the holiday setting in Settings → Application
  settings → Alerts and e-mail.\n4. The alert closes by itself when a new content filter record
  arrives."
- `DESCRIBERS["R20-filter"]`: "Son {hours} saatte içerik filtresi kaydı yok." / "No content filter
  record in the last {hours} hours." (only `hours`, an integer; no address, no name).

### 2.5 Sistem: resources (W4-B)

#### 2.5.1 JSON — `web/src/lib/system-types.ts` (verbatim; Pydantic models in `system/models.py`)

```ts
/** Mirror of api/src/arcilogger_api/system/models.py (Wave 4 contract 2.5.1). */
import type { DateString, DateTimeString } from './api-types';

export type DriveRole = 'data' | 'system';
export type StatusFileState = 'running' | 'stale' | 'missing';

export interface HostInfo {
  platform: 'windows' | 'linux' | 'other';
  cpu_percent: number | null;
  memory_total_bytes: number | null;
  memory_used_bytes: number | null;
  uptime_seconds: number | null;
}

export interface DiskForecast {
  state: 'estimated' | 'insufficient' | 'not_growing';
  samples: number;
  growth_bytes_per_day: number | null;
  warning_on: DateString | null;
  full_on: DateString | null;
}

export interface DriveInfo {
  role: DriveRole;
  path: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
  warning_percent: number;
  critical_percent: number;
  forecast: DiskForecast;
}

export interface TableSize {
  name: string;
  bytes: number;
}

export interface DatabaseInfo {
  storage: 'postgresql' | 'timescaledb';
  total_bytes: number | null;
  tables: TableSize[];
  measured_at: DateTimeString | null;
}

export interface CollectorSource {
  name: string;
  messages: number;
  bytes: number;
  last_received: DateTimeString | null;
}

/** Status-file entries that are not a configured source (2.5.2), never listed by name. */
export interface UnknownSenders {
  count: number;
  messages: number;
  bytes: number;
}

export interface CollectorInfo {
  state: StatusFileState;
  updated_at: DateTimeString | null;
  started_at: DateTimeString | null;
  sources: CollectorSource[];
  unknown_senders: UnknownSenders;
  dropped_unknown: number;
  dropped_transport: number;
  write_failures: number;
  write_error_since: DateTimeString | null;
  torn_lines: number;
  tcp_connections: number;
  tcp_framing_errors: number;
}

export interface IngestSource {
  name: string;
  lag_bytes: number;
  lag_seconds: number | null;
  lines_per_second: number | null;
  unfinished_days: number;
  has_error: boolean;
}

export interface IngestInfo {
  state: StatusFileState;
  service_state: string | null;
  updated_at: DateTimeString | null;
  database_ok: boolean | null;
  workers_configured: number | null;
  workers_active: number | null;
  lines_per_second: number | null;
  sources: IngestSource[];
  unknown_sources: number;
}

export interface VersionInfo {
  product: string;
  version: string;
  core_version: string;
  release_date: DateString;
}

export interface SystemSummary {
  checked_at: DateTimeString;
  host: HostInfo;
  drives: DriveInfo[];
  database: DatabaseInfo;
  collector: CollectorInfo;
  ingest: IngestInfo;
  version: VersionInfo;
}

export interface SystemDisk {
  checked_at: DateTimeString;
  drives: DriveInfo[];
}
```

#### 2.5.2 Rules

- **Host** (`core/src/arcilogger_core/hostinfo.py`): `cpu_times() -> CpuTimes | None`
  (`kernel32.GetSystemTimes`; `/proc/stat`), `cpu_percent(before, after) -> int | None`, `memory()
  -> Memory | None` (`GlobalMemoryStatusEx`; `/proc/meminfo` `MemTotal`/`MemAvailable`),
  `uptime_seconds() -> int | None` (`GetTickCount64`; `/proc/uptime`); any other platform or error →
  `None`. Typed for both mypy runs, in the style of `core/src/arcilogger_core/admin/winapi.py:61-69`.
  The API keeps the previous `CpuTimes` in its process (a lock-guarded module value); the first
  request answers `cpu_percent: null`, later ones the percentage since the previous reading (at
  least 1 s apart).
- **Drives:** `drives(config.data_dir)` (`worker/src/arcilogger_worker/alerts/system.py:238-256`):
  the first is `data`, the second (when another device) `system`; `path` is the drive label;
  `warning_percent` = `alerts.disk_used_percent`, `critical_percent` = 95.
- **Disk history** (job function of W4-B, registered by W4-F): `job_system_sample(ctx)` in
  `worker/src/arcilogger_worker/system_jobs.py`, job name **`system.sample`**, daily after 00:30. It
  appends `{"day": "YYYY-MM-DD", "drives": [{"path", "total", "used"}]}` (integers) to
  `state/system-history.json` = `{"schema": 1, "days": [...]}`, replacing an entry of the same day,
  keeping the newest 120 days, written with `atomic_write_text`; returns `{"day", "drives": n}`.
  `Layout.system_history` (new property, `core/src/arcilogger_core/layout.py`) names the file.
- **Forecast** (`system/forecast.py`): per drive path, the newest 30 entries; fewer than 7 →
  `insufficient`; least-squares slope of used bytes per day; slope ≤ 0 → `not_growing`; else
  `estimated` with `growth_bytes_per_day` = rounded slope, `warning_on` = first day the line reaches
  `warning_percent` (null when already above), `full_on` = first day it reaches the total (owner
  question Q17).
- **Database:** `pg_database_size(current_database())`; per top-level table of
  `arcilogger_db.models.Base.metadata` its size plus that of every descendant in `pg_inherits`
  (recursive); with TimescaleDB (`storage = "timescaledb"`, from the existing `db.timescale` row)
  `hypertable_size()` for hypertables; the 10 largest; cached 5 min with `measured_at`. A query error
  gives `total_bytes: null`, `tables: []` and is logged.
- **Collector / ingest:** read `layout.collector_status` / `layout.ingest_status`
  (`core/src/arcilogger_core/layout.py:84-91`); `state` = `missing` (no file), `stale` (its
  `updated_at` older than `SERVICE_FRESH`, 2 min), else `running`. **Sender addresses are never
  returned.** Both files key `sources` by spool name, which embeds the address for unknown senders
  (`unknown-<ip>`, written by default: `core/src/arcilogger_core/config.py:89`,
  `collector/src/arcilogger_collector/service.py:84-88`, `:263-269`, `:291`) and for members of a
  network source (`<name>-<ip>`, `config.py:436-445`). So `sources` lists configured sources only,
  under their configured name: an exact source's entry as is, a network source's members summed
  under its name (counts and lags summed, times and `lag_seconds` the largest, `has_error` any).
  Every other entry is only counted: `unknown_senders` (collector: entries, messages, bytes) and
  `unknown_sources` (ingest: entries); Kaynaklar lists them (`web/src/pages/sources/sources-page.tsx:51`).
  `dropped_unknown`, `dropped_transport` and `write_failures` are sums; `write_error_since` =
  `spool_write_error.since`; `torn_lines` = `torn_lines_total`. Ingest per source:
  `unfinished_days` = number of days, `has_error` = `last_error` is not null (the text is not
  returned).
- **Version:** `product`, the API `__version__`, `arcilogger_core.__version__`, `RELEASE_DATE`. The
  web reads maintenance from the existing `GET /license`.
- `GET /system` answers within 1 s on the perf data of §3; `GET /system/disk` = `drives` only.

### 2.6 Sistem: health (W4-D)

#### 2.6.1 JSON — `web/src/lib/system-health-types.ts` (verbatim; Pydantic models in `system_health/models.py`)

```ts
/** Mirror of api/src/arcilogger_api/system_health/models.py (Wave 4 contract 2.6.1). */
import type { DateTimeString } from './api-types';
import type { ServiceName } from './settings-types';

export type TaskName =
  | 'Seal'
  | 'VerifyDaily'
  | 'VerifyWeekly'
  | 'VerifyMonthly'
  | 'Replica'
  | 'Retention'
  | 'Credit'
  | 'ClockCheck'
  | 'Backup'
  | 'Requests'
  | 'ApplySystem';

export interface ServiceHealth {
  name: ServiceName;
  state: 'running' | 'stale' | 'missing';
  updated_at: DateTimeString | null;
}

export interface AgentHealth {
  state: 'ok' | 'stale' | 'missing' | 'disabled';
  heartbeat_at: DateTimeString | null;
}

export interface ApplySystemHealth {
  state: 'ok' | 'failed' | 'never' | 'disabled';
  at: DateTimeString | null;
  code: string | null;
}

export interface TaskHealth {
  task: TaskName;
  schedule_tr: string;
  schedule_en: string;
  state: 'ok' | 'late' | 'missing' | 'not_used' | 'not_observable';
  last_evidence_at: DateTimeString | null;
  evidence: string;
}

export interface JobHealth {
  job: string;
  schedule_tr: string;
  schedule_en: string;
  module: string | null;
  state: 'ok' | 'failing' | 'skipped' | 'running' | 'never';
  last_status: string | null;
  last_started: DateTimeString | null;
  last_finished: DateTimeString | null;
  duration_ms: number | null;
  failures: number;
  next_due: DateTimeString | null;
}

export interface ClockSampleOut {
  time: DateTimeString;
  server: string | null;
  ok: boolean;
  offset_ms: number | null;
  delay_ms: number | null;
  stratum: number | null;
}

export interface TimeHealth {
  ntp_server: string | null;
  fallback_server: string | null;
  alert_offset_ms: number;
  applied_peers: string | null;
  applied_at: DateTimeString | null;
  samples: ClockSampleOut[];
}

export interface SystemHealth {
  checked_at: DateTimeString;
  services: ServiceHealth[];
  agent: AgentHealth;
  apply_system: ApplySystemHealth;
  tasks: TaskHealth[];
  jobs: JobHealth[];
  time: TimeHealth;
}
```

#### 2.6.2 Rules

- **Services:** ArciCollector, ArciIngest, ArciWorker from their status files (`updated_at`;
  `SERVICE_FRESH` 2 min, `core/src/arcilogger_core/admin/contract.py:290`); ArciApi is always
  `running` (it answers). **Agent:** `Channel.agent_state` and `Channel.agent.heartbeat_at`
  (`admin_channel.py:613-632`). **ApplySystem:** `disabled` when `Channel.policy.enabled` is false,
  `never` when `Channel.system` is `None`, `failed` when its `ok` is false, else `ok`; `at`, `code`
  from it (`admin_channel.py:634-645`).
- **Tasks** (`system_health/tasks.py`, one row per file of `deploy/windows/tasks/`):

| task | schedule_tr / schedule_en | evidence (relative to the data folder) | late after | not_used when |
|---|---|---|---|---|
| Seal | "Her gün 00:10'dan sonra 5 dakikada bir" / "Every 5 minutes from 00:10" | newest modification time of `state\seal\<YYYY-MM-DD>.json` | 26 h | — |
| VerifyDaily | "Her gün 01:00" / "Daily at 01:00" | `at` of the newer of `state\verify\last-last-1.json`, `last-last-2.json` (Implementation note 7) | 26 h | — |
| VerifyWeekly | "Pazar 03:00" / "Sundays at 03:00" | newest `at` of `state\verify\last-last-<N>.json`, N ≥ 3 | 8 days | — |
| VerifyMonthly | "Ayın 1'i 04:00" / "1st of the month at 04:00" | `at` of `state\verify\last-full.json` | 32 days | — |
| Replica | "Saatte bir, 00:40'tan" / "Hourly from 00:40" | newest `verified_at` of `state\replica\<day>.json` (last 3 days) | 26 h | `[replica] enabled = false` |
| Retention | "Her gün 02:00" / "Daily at 02:00" | none readable → `not_observable` | — | `[retention] enabled = false` |
| Credit | "Her gün 06:00" / "Daily at 06:00" | newest `time` of `state\credit.jsonl` | 26 h | `[tsa] provider` ≠ `zamane` |
| ClockCheck | "Saatte bir, 00:05'ten" / "Hourly from 00:05" | newest `time` of `state\clock\<day>.jsonl` (today, yesterday) | 2 h | — |
| Backup | "Her gün 01:40" / "Daily at 01:40" | `finished` of `state\backup\last.json` | 26 h | `[backup] enabled = false` |
| Requests | "Olayla ve 5 dakikada bir" / "On event and every 5 minutes" | none readable by ArciApi (`state\requests`) → `not_observable` | — | — |
| ApplySystem | "Olayla ve 5 dakikada bir" / "On event and every 5 minutes" | `at` of `admin\system\status.json` | 15 min | browser administration off |

  `missing` = no evidence yet; `late` = evidence older than the limit. The "late after" values
  follow each task's cadence (`deploy/windows/tasks/*.xml`) plus a grace; they are not settings.
- **Jobs:** the list of `default_jobs(config.worker.interval_seconds)`
  (`worker/src/arcilogger_worker/service.py:375-415`, called as at
  `worker/src/arcilogger_worker/cli.py:194`), joined with `state/worker-status.json` `jobs` and, for
  `duration_ms`, per job of that list the newest finished `job_runs` row of the last 7 days (one
  `WHERE job = :job AND status <> 'running' AND started_at >= :since ORDER BY started_at DESC
  LIMIT 1` probe of index `ix_job_runs_job_started_at` each; 7 days as `check_jobs`,
  `worker/src/arcilogger_worker/alerts/system.py:116`, `:621`; no `DISTINCT ON` over the unpurged
  table, Q21); none → `null`. `state`: `never` (no state, no row), `running`, `failing`
  (`failures` ≥ 1), `skipped`, else `ok`. Schedule text from `every` ("Her {n} saniyede/dakikada/
  saatte bir" / "Every {n} seconds/minutes/hours") or `daily_after` ("Her gün {HH:MM}'den sonra" /
  "Daily after {HH:MM}"). `last_error` is never returned.
- **Time:** `[clock] ntp_server`, `fallback_server`, `alert_offset_ms`; `applied_peers` /
  `applied_at` = `Channel.system.time.peers` / `.changed_at` (null without ApplySystem); `samples` =
  the clock samples of the last 24 h, newest first (today's and yesterday's
  `state/clock/<day>.jsonl`), `offset_ms` = `offset_us` / 1000 when `ok`.
- `GET /system/health` answers within 1 s on the perf data of §3.

**`setup_problems` TR/EN split (W4-D):** `_BILINGUAL` (`admin_config.py:85`) also accepts
`[[name]]` inside the English part, so the `sources` problem splits into its Turkish and English
text (used by the wizard summary and Ayarlar).

### 2.7 Error codes and audit actions

| Code | HTTP | message_tr | message_en |
|---|---|---|---|
| `config_backup_unreadable` | 409 | "Yapılandırma dosyası okunamadı; yedek onayı kaydedilmedi." | "The configuration file could not be read; the backup confirmation was not recorded." |

Kept in `compliance/routes.py`'s own `MESSAGES` with an `_error()` helper, like
`api/src/arcilogger_api/routers/settings.py:50-69`. Audit actions: `compliance.report_export`,
`compliance.config_backup_confirm` (labels in 2.8.5).

### 2.8 Web

#### 2.8.1 Uyum Durumu page (`/compliance`)

- **W4-C (views, no data fetching):** `web/src/pages/compliance/compliance-view.tsx` exports
  `ComplianceView({ status, error, loading, updatedAt, onRefresh, onExport, exporting,
  onConfirmBackup, confirming })`; `compliance-card.tsx` (`ComplianceCardView`);
  `compliance-model.ts` (tone → `{badge tone, icon, label key}`: `ok` → success, `CircleCheck`;
  `warning` → warning, `TriangleAlert`; `error` → danger, `CircleX`; `neutral` → neutral,
  `CircleDashed`; the summary; link filtering with `linkTarget`). The view shows: `PageHeader` with
  `nav.compliance` and `compliance.description`; "Güncellendi {time}" (`aria-live="polite"`),
  "Yenile", "Uyum Durumu Raporu (PDF)"; the notice (`notice_tr`/`notice_en`) in an info alert; the
  summary line; the ten cards (number, title, badge with icon and text, reason, facts as `<dl>`,
  alerts count with a link to Güvenlik when allowed, the page link when allowed, the document
  reference as text, the verify-on-site note with its icon); on card 10 the button "Yapılandırma
  yedeğini aldım" (only with `settings.manage`) with a confirm dialog; when any card is not `ok` and
  the user has `settings.manage`, a link "İlk kurulum sihirbazını aç" to `/settings/setup`. Loading,
  error ("Tekrar dene") and stale-data rules as Kaynaklar (`web/src/pages/sources/sources-page.tsx:340-357`).
  The auditor (D15: `compliance.read` without `settings.manage`) gets the cards and the PDF button;
  the confirm button and the wizard link stay hidden, and `linkTarget` hides the links to Kaynaklar,
  Güvenlik, Ayarlar and Sistem (the links to Mühürleme and Arama stay).
- **W4-F (data and route):** `web/src/lib/compliance-hooks.ts` (`useCompliance()` polling 30 s in the
  background like `useDashboardSummary`; `useComplianceReport()` with `apiDownload('/compliance/
  report.pdf', …)`; `useConfirmConfigBackup()` posting and invalidating `useCompliance`),
  `web/src/pages/compliance/compliance-page.tsx` (`CompliancePage` = hooks → `ComplianceView`), the
  lazy route and `BUILT_PAGES`.

#### 2.8.2 Sistem page (`/system`)

- **W4-C:** `web/src/pages/system/system-view.tsx` (`SystemView({ summary, health, license, error,
  loading, updatedAt, onRefresh })`), `drive-meter.tsx` (meter with the value as text and the
  80 %/95 % marks), `system-model.ts` (byte and percent formatting through `web/src/lib/format.ts`,
  forecast text, state → badge maps). Blocks in this order: Kaynak kullanımı (CPU, bellek, çalışma
  süresi; "ölçülüyor" for a null CPU, "bu platformda okunamıyor" for null host values), Diskler (one
  meter per drive with the forecast text), Veritabanı (total and the largest tables), Toplayıcı,
  İçe aktarma, Hizmetler (services, agent, ApplySystem), Zamanlanmış görevler (table of
  `tasks`), Worker işleri (table of `jobs`), Saat (servers, applied peers, last samples), Sürüm ve
  güncelleme (versions, licence maintenance from `GET /license`, and the text "Yükseltme için yeni
  çevrimdışı paketi sunucuya kopyalayıp KURULUM.md bölüm 13'teki adımları izleyin. ARCILOGGER
  güncelleme indirmez." / "To upgrade, copy the new offline bundle to the server and follow
  INSTALL.md section 13. ARCILOGGER downloads no updates.").
- Forecast texts: `estimated` "Bu hızla %{warning} eşiğine {warning_on}, dolmaya {full_on}
  (~{days} gün)." / "At this rate the {warning}% mark is reached on {warning_on}, full on {full_on}
  (~{days} days)."; `insufficient` "Tahmin için en az 7 günlük ölçüm gerekiyor ({samples}/7)." / "A
  forecast needs at least 7 days of samples ({samples}/7)."; `not_growing` "Son 30 günde disk
  kullanımı artmadı." / "Disk use did not grow in the last 30 days."
- **W4-F:** `web/src/lib/system-hooks.ts` (`useSystemSummary()`, `useSystemHealth()` — 30 s;
  `useSystemDisk(enabled)` — 30 s), `web/src/pages/system/system-page.tsx` (`SystemPage`), route and
  `BUILT_PAGES`.

#### 2.8.3 Panel disk card

W4-C writes `web/src/pages/dashboard/disk-card.tsx` (`DiskCard({ disk })`: title "Disk", each drive's
used percent with icon and text, the forecast in one line, the link "Sistem sayfasını aç" through the
existing `DashCard` link rule). W4-F adds it to the Panel (`dashboard-page.tsx`, `dashboard-model.ts`:
block `disk`, requested only with `system.read`). The Panel's clock card link (`dashboard-page.tsx:106`)
starts to work when `/system` is built.

#### 2.8.4 First-run wizard (`/settings/setup`, W4-C)

- `/setup` (public) keeps the account step. Its stepper gets a third step "Kurulum sihirbazı" with
  the hint "Girişten sonra, Ayarlar'da" / "After sign-in, in Settings"; the comment at
  `web/src/pages/setup/setup-page.tsx:50-55` loses "the other wizard steps follow in M6".
- New route `/settings/setup` inside the app shell, `PermissionGate anyOf={['settings.manage']}`,
  page `web/src/pages/setup-wizard/wizard-page.tsx`. Step list as Ayarlar → Kamu SM does it: a `<nav>`
  of links `?step=<id>` with `aria-current="step"` and a badge (icon + text) per step; without
  `?step=`, the first step that is not done.
- Steps and their state (`wizard-model.ts`, from `GET /admin/config`, `GET /admin/status`, the request
  history, and for step 3 `GET /sources`):

| id | Title TR / EN | Done when | Opens |
|---|---|---|---|
| `admin` | "Yönetici ve iki adımlı doğrulama" / "Administrator and two-step verification" | always (the user is signed in) | — |
| `site` | "Site ayarları" / "Site settings" | no `error` problem with key `site_name` or `clock.*` | `/settings/general?wizard=1` |
| `sources` | "Log kaynakları" / "Log sources" | at least one enabled configured source | `/settings/sources?wizard=1` |
| `kamusm_test` | "Kamu SM: test sunucusu" / "Kamu SM: test server" | a succeeded "Damga testi" request in the history (as `useTested`, `web/src/pages/settings/kamusm-page.tsx:86-97`), or `tsa.server = production` | `/settings/kamusm?step=test&wizard=1` |
| `kamusm_production` | "Kamu SM: üretim" / "Kamu SM: production" | `tsa.server = production` and `seal_ready.ok` | `/settings/kamusm?step=production&wizard=1` |
| `replica` | "Replika" / "Replica" | replica enabled | `/settings/replica?wizard=1` |
| `smtp` | "E-posta" / "E-mail" | SMTP server and sender set | `/settings/email?wizard=1` |
| `summary` | "Özet" / "Summary" | — | — |

- Each step panel: what the step is for (2-3 sentences), its state ("Tamam" / "Eksik"), what is
  still missing, the button "Ayarlar'da aç" and "Bu adımı şimdilik atla" (goes to the next step; the
  step stays "Eksik"). The sources step adds a firewall note chosen by each enabled source's
  `firewall` state in `GET /admin/config` (`sections.sources[].firewall`,
  `api/src/arcilogger_api/routers/admin_config.py:227-230`): `open`/`pending` → "Etkin kaynakların
  adreslerine 514 numaralı bağlantı noktasını ARCILOGGER'ın güvenlik duvarı kuralı kendisi açar." /
  "ARCILOGGER's firewall rule opens port 514 to the addresses of the enabled sources by itself.";
  `not_managed` → "Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor; 514 numaralı bağlantı
  noktasını bu adreslere elle açın (KURULUM.md bölüm 12)." / "The firewall is not managed by
  ARCILOGGER; open port 514 to these addresses by hand (INSTALL.md section 12)." (`docs/AYARLAR.md:212`,
  `:281`); and the reference "Cihaz ayarları: DEVICE-SETUP.md bölüm 1 ve 4 (komutlar sahada
  doğrulanmalıdır)." / "Device settings: DEVICE-SETUP.md sections 1 and 4 (verify the commands on
  site)." — no device commands in the web app, no pre-filled device list; per configured source,
  whether data arrived (from `GET /sources`). The Kamu SM steps say "Test sunucusunun damgalarının
  yasal geçerliliği yoktur (Kamu SM); üretime geçmeden önce kök sertifika parmak izlerini Kamu SM'nin
  yayımladığı değerlerle karşılaştırın (sahada doğrula)." / "Stamps of the test server have no legal
  validity (Kamu SM); before switching to production compare the root fingerprints with the values
  Kamu SM publishes (verify on site)." (the wording of `docs/kamusm.md:12`, `:41`).
- Summary: every step with its state, the configuration problems (`problems` of `GET /admin/config`,
  all levels, TR/EN after W4-D's fix), "Kurulum sonrası denetim listesi: KURULUM.md bölüm 12" /
  "Checks after installation: INSTALL.md section 12" as text, and the link to Uyum Durumu when the
  user has `compliance.read`.
- `SettingsFrame` (`web/src/components/admin/settings-frame.tsx`): in wizard mode its "way back"
  link reads "Kurulum sihirbazına dön" and goes to `/settings/setup`. Wizard mode starts when the URL
  has `wizard=1` and is kept in `sessionStorage` (this tab only, every access in try/catch, as
  `web/src/pages/live/live-model.ts:234-260`), because the Kamu SM step links replace the whole query
  (`web/src/pages/settings/kamusm-page.tsx:342`, not W4-C's file); the wizard page and Ayarlar's
  overview end it. Without storage only the URL counts. Ayarlar's overview
  (`web/src/pages/settings/settings-page.tsx`) gets a card "İlk kurulum sihirbazı" linking to
  `/settings/setup`. No automatic redirect after the first sign-in.

#### 2.8.5 Fixed texts (locale areas)

W4-C writes `web/src/locales/areas/compliance/{tr,en}.json` (`compliance.*`),
`areas/system/{tr,en}.json` (`system.*`), `areas/setupWizard/{tr,en}.json` (`setupWizard.*`, and the
new leaves `setup.stepWizard`, `setup.stepWizardHint`, `auditActions.compliance_report_export`,
`auditActions.compliance_config_backup_confirm`). Fixed keys:

| Key | TR | EN |
|---|---|---|
| `compliance.description` | Kayıt toplama, mühürleme ve yedeklemenin teknik denetimleri. | Technical checks of log collection, sealing and backups. |
| `compliance.summary` | {{total}} denetimden {{ok}} tanesi tamam | {{ok}} of {{total}} checks OK |
| `compliance.tone.ok` / `.warning` / `.error` / `.neutral` | Tamam / Dikkat / Sorun / Bilgi yok | OK / Attention / Problem / No data |
| `compliance.export` | Uyum Durumu Raporu (PDF) | Compliance status report (PDF) |
| `compliance.document` | Belge: {{doc}} | Document: {{doc}} |
| `compliance.verifyOnSite` | Sahada doğrulayın | Verify on site |
| `compliance.openAlerts` | Açık uyarı: {{count}} | Open alerts: {{count}} |
| `compliance.confirmBackup` | Yapılandırma yedeğini aldım | I have copied the configuration |
| `compliance.confirmBackupBody` | arcilogger.toml, secret.key ve Caddy CA klasörünün (özel anahtarıyla) şifreli, çevrim dışı ve yedeklerden ayrı saklanan bir ortama kopyalandığını onaylıyorsunuz (secrets.json isteğe bağlı; YEDEKLEME.md bölüm 3). Onay, yapılandırmanın şimdiki SHA-256 değeriyle denetim kaydına yazılır. | You confirm that arcilogger.toml, secret.key and the Caddy CA folder (with its private key) were copied to encrypted offline media kept apart from the backups (secrets.json optional; BACKUP.md section 3). The confirmation is written to the audit log with the configuration's current SHA-256. |
| `compliance.openWizard` | İlk kurulum sihirbazını aç | Open the first-run wizard |
| `system.description` | Sunucunun kaynakları, hizmetleri, zamanlanmış görevleri ve saati. | The server's resources, services, scheduled tasks and clock. |
| `setupWizard.title` | İlk kurulum sihirbazı | First-run wizard |
| `setupWizard.skip` | Bu adımı şimdilik atla | Skip this step for now |
| `setupWizard.open` | Ayarlar'da aç | Open in Settings |
| `setupWizard.back` | Kurulum sihirbazına dön | Back to the first-run wizard |
| `setup.stepWizard` / `setup.stepWizardHint` | Kurulum sihirbazı / Girişten sonra, Ayarlar'da | First-run wizard / After sign-in, in Settings |
| `auditActions.compliance_report_export` | Uyum Durumu Raporu indirildi | Compliance status report exported |
| `auditActions.compliance_config_backup_confirm` | Yapılandırma yedeği onaylandı | Configuration backup confirmed |

Other texts of the pages are the implementer's, in the same areas, under the same rules (0.1 items
5, 7). A changed text gets a new key (`web/src/lib/i18n.ts:49-54`).

### 2.9 Docs

- **W4-E (any time):** new pair `docs/UYUM-DURUMU.md` / `docs/COMPLIANCE-STATUS.md`, numbered:
  §1 what the page shows and what it is not (the wording rule and the lawyer recommendation); §2.1-2.10
  one section per card (what it checks, from which file or table, every tone with its condition, what
  to do, the verify-on-site note); §3 the Uyum Durumu Raporu (PDF); §4 the Sistem page (each block, the
  task table of 2.6.2, the disk forecast); §5 the first-run wizard; §6 verify on site (DNS filter
  profile, firewall filter logging, the credit format, WORM, Windows rights). Plus
  `docs/YEDEKLEME.md`/`BACKUP.md` §3 (the confirmation button and card 10, in the words of
  `compliance.confirmBackupBody`), `docs/kamusm.md` §6
  (`docs/kamusm.md:174-176`: renewal "sonraki bir sürümde" / "in a later release" instead of "M6
  kapsamında"), `docs/SECURITY.md` (the two pages read only; Uyum Durumu and its PDF for the
  administrator and the auditor, the backup confirmation and Sistem for the administrator only
  (D15); no data about log subjects, the PDF names only the exporting account (0.1 item 4), the two
  audit actions), `docs/KABUL-TESTI-WINDOWS11.md` (manual steps for both pages and the wizard),
  `deploy/windows/README.md` (the new state file `state\system-history.json`).
- **W4-F (late, in the switch-on merge, because tests couple them to the code):**
  - `README.md`: "Yer tutucu sayfalar: **yok**." and Uyum Durumu, Sistem added to "Çalışan sayfalar";
    the placeholder sentence of `README.md:86-89` removed; the M6 row: "✅ … uyum sayfası (Uyum
    Durumu, Uyum Durumu Raporu), Sistem sayfası, ilk kurulum sihirbazı (Ayarlar → İlk kurulum
    sihirbazı) …" with the old "yer tutucu…; ilk kurulum sihirbazı yok" removed.
  - `docs/LISANS.md` §2.2: `| \`/compliance\` | Uyum Durumu: on denetim kartı, Uyum Durumu Raporu (PDF), yapılandırma yedeği onayı | 5651 Temel |` and `| \`/system\` | Sistem sayfası: kaynak kullanımı, disk tahmini, veritabanı boyutu, hizmetler, zamanlanmış görevler, saat | 5651 Temel |`; §2.3: `| \`system.sample\` | 5651 Temel | her zaman çalışır |`.
  - `docs/LICENSING.md` §2.2: `| \`/compliance\` | Uyum Durumu: the ten check cards, the status report (PDF), the configuration backup confirmation | 5651 Core |`, `| \`/system\` | System page: resource use, disk forecast, database size, services, scheduled tasks, clock | 5651 Core |`; §2.3: `| \`system.sample\` | 5651 Core | always runs |`.
  - `docs/CORRELATION.md` worker-job table: `| \`system.sample\` | daily after 00:30 — one sample of the data and system drive (total and used bytes) into \`state\system-history.json\` (the newest 120 days) for the disk forecast of the Sistem page and the Panel; details \`{"day", "drives"}\` |`.
  - `docs/ILK-ADIMLAR.md` §9 alert table: `| \`R20-filter\` | Beklenen bir günde 24 saattir web ya da DNS filtresi kaydı gelmiyor (\`alerts.filter_quiet_hours\`, \`alerts.filter_days\`; Uyum Durumu kart 7) |` and the daily check "Her sabah **Uyum Durumu** sayfasına bakın; her kart neyi denetlediğini ve ne yapılacağını söyler (UYUM-DURUMU.md)."; §7: the placeholder sentence of `docs/ILK-ADIMLAR.md:261` removed, both pages added to the working pages. `docs/FIRST-STEPS.md` the same in English (`| \`R20-filter\` | No web or DNS filter record for 24 hours on an expected day (… ; Uyum Durumu card 7) |`).
  - `docs/UYARILAR.md` / `docs/ALERTS.md`: the `R20-filter` row of the R20 table (condition as 2.4, severity Yüksek/High, settings `alerts.filter_days` (her gün / every day), `alerts.filter_skip_holidays` (açık/on), `alerts.filter_quiet_hours` (24), what to do: the runbook of 2.4) and the three rows of the settings table (`| \`alerts.filter_days\` | \`[1,2,3,4,5,6,7]\` | 1–7 | … |`, `| \`alerts.filter_skip_holidays\` | \`true\` | — | … |`, `| \`alerts.filter_quiet_hours\` | \`24\` | 6–168 | … |`).
- **W4-G (late, last):** `docs/KURULUM.md`/`INSTALL.md` (§9 first sign-in → the wizard;
  §12 the checklist → Uyum Durumu and Sistem beside the console commands; §13 version and maintenance
  on Sistem), `docs/AYARLAR.md`/`SETTINGS.md` (the wizard entry, the three `alerts.filter_*` keys in the
  "Uyarılar ve e-posta" group, the "back to the wizard" link), `docs/roles.md` (one file with its TR
  and EN parts, no English pair: `compliance.read` for admin and auditor in the permission table and
  under "Kararlar / Decisions", owner decision 2026-10-28; Sistem, the Panel disk card and the
  backup confirmation admin only; open question 5 (S5) answered for Uyum Durumu, kept open for the
  other pages), `docs/MVP-CONTRACTS.md` (the six endpoints, JSON of 2.3.1/2.5.1/2.6.1, the error
  code, the two audit actions, the `compliance.read` row of the §6 role table:
  `docs/MVP-CONTRACTS.md:260`), `docs/ARCHITECTURE.md` (the two pages, `hostinfo`,
  `state\system-history.json`), `docs/DENETCI-KILAVUZU.md` (how to read the Uyum Durumu Raporu:
  technical checks, not a legal assessment; the auditor opens the page and downloads it, D15),
  `docs/ASSUMPTIONS.md` (the Wave 4 defaults, the owner decisions Q1 and Q10 of 2026-10-28 and the
  verify-on-site list), `CHANGELOG.md` (Wave 4 under "Unreleased"), `docs/adr/0017-compliance-and-system-pages.md`
  (D1-D15, in ADR-0016's format) and its row in `docs/adr/README.md`.

---

## 3. Tests

Every package runs the gates of §4.1. Test names are fixed; add more where useful.

**Perf data** (the `perf` guards of A, B and D; `poe test-perf` on both CI jobs, as
`api/tests/test_search_perf.py:1`): loaded with `INSERT … SELECT generate_series`
(`api/tests/test_dashboard_api.py:421-441`), then `VACUUM ANALYZE`. Volumes: `fg_web` 300,000 and
`fg_dns` 500,000 rows on the newest site day and 20,000 / 35,000 on each of the 7 days before, two
FortiGate sources, `search_counts` rebuilt as `test_search_perf.py` does; 200 active alerts over
the ten cards' rules; `job_runs` 17,000 rows a day for 180 days (Q21); today's and yesterday's
`state/clock` files (24 samples each); 30 sealed days in the vault; status files with 20 sources.

**W4-A**
- `api/tests/test_compliance_cards.py`: `test_cards_come_in_order_with_their_ids`,
  `test_sources_card_states`, `test_seal_yesterday_card_states` (verified, sealed, late, pending before
  and after the overdue hour, failed, no_data with and without sources, not production),
  `test_chain_card_states`, `test_retention_card_states`, `test_tsa_card_states`,
  `test_clock_card_counts_the_limit_itself_as_a_problem`, `test_clock_card_states`,
  `test_content_filter_card_states`, `test_lease_evidence_card_follows_lease_alerts`,
  `test_lease_evidence_card_without_alerts_reads_the_tables`, `test_replica_card_states`,
  `test_backup_card_states`, `test_a_failing_card_is_neutral_and_the_others_answer`,
  `test_cards_carry_no_client_address_no_person_and_no_replica_target`,
  `test_every_card_text_has_both_languages_and_no_conformity_word` (no "uyumlu", no "conforms", and
  no phrase of `COMPLIANCE_CLAIMS`, `tests/test_docs_consistency.py:852-866`).
- `worker/tests/test_compliance_filter.py`: `test_counts_web_and_dns_per_day`,
  `test_every_day_of_the_week_is_expected_by_default` (Saturday and Sunday included),
  `test_a_day_removed_from_filter_days_is_not_expected`,
  `test_listed_holidays_are_not_expected_by_default`, `test_holidays_count_when_skipping_is_off`,
  `test_alert_due_only_when_the_whole_window_is_expected`, `test_a_silent_kind_is_reported`,
  `test_profiles_come_from_the_newest_rows_only`, `test_without_a_fortigate_source_it_does_not_apply`.
- `api/tests/test_compliance_api.py` (mounts the router itself, Implementation note 3):
  `test_compliance_answers_the_ten_cards`, `test_compliance_is_refused_without_compliance_read`
  (hr, viewer: 403 on the cards and the PDF, nothing computed),
  `test_auditor_reads_the_cards_and_the_report` (200 with the ten cards; the PDF with its
  `compliance.report_export` entry naming the auditor), `test_compliance_is_not_licence_gated`,
  `test_compliance_reads_are_not_audited`, `test_compliance_answer_is_cached_for_30_seconds`,
  `test_report_pdf_is_audited_with_its_sha256`, `test_config_backup_turns_card_10`,
  `test_config_backup_needs_settings_manage`, `test_config_backup_is_refused_for_the_auditor` (403,
  no audit entry, card 10 unchanged), `test_config_backup_refused_when_the_file_cannot_be_read`.
  The two auditor tests use the auditor of D15: until W4-F grants it, they add `compliance.read`
  with `monkeypatch.setitem(security.ROLE_PERMISSIONS, "auditor", … | {"compliance.read"})`, as
  `api/tests/test_evidence_api.py:341-345` does, so they pass before and after W4-F.
- `api/tests/test_compliance_report.py`: `test_report_lists_every_card_in_both_languages`,
  `test_report_shows_tones_as_text`, `test_report_claims_no_conformity`,
  `test_report_names_site_time_and_exporter`.
- `api/tests/test_compliance_perf.py` (marker `perf`): `test_compliance_within_one_second`.
- `db/tests/test_settings_filter.py`: `test_filter_settings_defaults_and_bounds`
  (`alerts.filter_days` `[1, 2, 3, 4, 5, 6, 7]`), `test_filter_days_refuse_values_outside_1_to_7`.

**W4-B**
- `core/tests/test_hostinfo.py`: `test_linux_readers_parse_proc`, `test_cpu_percent_from_two_readings`,
  `test_other_platform_answers_none`, `test_windows_readers` (runs on the Windows CI job).
- `worker/tests/test_system_jobs.py`: `test_sample_writes_one_entry_per_site_day`,
  `test_history_keeps_120_days`.
- `api/tests/test_system_forecast.py`: `test_forecast_needs_seven_samples`,
  `test_forecast_is_linear_over_the_last_30`, `test_forecast_not_growing`,
  `test_forecast_already_above_the_warning_mark`.
- `api/tests/test_system_api.py`: `test_system_summary_shape`, `test_system_disk_shape`,
  `test_system_is_refused_without_system_read`, `test_system_is_not_licence_gated`,
  `test_system_returns_no_sender_address` (status files with an `unknown-172.20.10.40` entry and a
  network member `ap-172.20.30.11`), `test_database_sizes_are_cached`,
  `test_database_sizes_on_timescaledb` (Linux TimescaleDB job), `test_first_cpu_reading_is_null`.
- `api/tests/test_system_perf.py` (`perf`): `test_system_within_one_second`.

**W4-C**
- `web/src/pages/compliance/compliance-model.test.ts`, `compliance-view.test.tsx` (ten cards with icon
  and text, links hidden without permission, notice shown, confirm button only with `settings.manage`,
  the auditor of D15: cards and PDF button without the confirm button and the wizard link, English
  switch, error keeps shown data).
- `web/src/pages/system/system-model.test.ts`, `system-view.test.tsx`;
  `web/src/pages/dashboard/disk-card.test.tsx`.
- `web/src/pages/setup-wizard/wizard-model.test.ts` (every step's state from configuration, first open
  step, skip keeps the step open), `wizard-page.test.tsx` (navigation by `?step=`, links carry
  `wizard=1`, summary lists problems in the interface language).
- `web/src/components/admin/settings-frame.test.tsx` (the back link with and without `wizard=1`,
  and after a link that drops the query).
- `web/e2e/setup-wizard.spec.ts`: a fresh installation walked step by step, axe light and dark on
  every step. The captured admin sample is not fresh (site name set, two enabled sources, replica
  on), so after `mockApi` the spec overrides `GET /api/v1/admin/config`, `/admin/status`,
  `/admin/requests` and `/sources` with `page.route` on modified copies of the captured samples
  (the layering of `web/e2e/mock-sources.ts:1-10`; `mock-api.ts`/`mock-admin.ts` stay untouched).
  It also follows a Kamu SM step link and expects the way back to the wizard.
  `web/e2e/setup.spec.ts` (the third step is shown).

**W4-D**
- `api/tests/test_system_health.py`: `test_services_from_status_file_age`,
  `test_agent_and_apply_system_states`, `test_task_states_from_evidence_files` (verify files named
  with the expression of `core/src/arcilogger_core/cli.py:353-355`, never by hand),
  `test_task_table_matches_the_task_files` (every file of `deploy/windows/tasks/` has exactly one row
  and every "HH:MM" of a row appears in its XML), `test_jobs_from_worker_status_and_job_runs`,
  `test_time_samples_of_the_last_24_hours`, `test_health_is_refused_without_system_read`,
  `test_health_returns_no_job_error_text`.
- `api/tests/test_setup_problem_texts.py`: `test_sources_problem_splits_into_turkish_and_english`.
- `api/tests/test_system_health_perf.py` (`perf`): `test_health_within_one_second`.

**W4-E, W4-G:** the existing docs tests stay green (`uv run --frozen pytest tests/test_docs_consistency.py
tests/test_search_docs.py`).

**W4-F**
- `api/tests/test_errors_and_app.py` (`EXPECTED_ROUTERS` += `/compliance` compliance, `/system` system,
  `/system/health` system), `api/tests/test_license_routes.py` (the six routes, never gated; counts).
- `api/tests/test_security.py`: `compliance.read` leaves `NOT_IN_SPEC_TABLE_ADMIN_ONLY` (`:59-69`)
  and is pinned for admin and auditor (owner decision 2026-10-28);
  `test_auditor_reads_compliance_but_not_system_or_settings` (the auditor holds `compliance.read`,
  not `system.read` nor `settings.manage`; hr and viewer hold none of the three).
- `worker/tests/test_alerts_filter.py`: `test_r20_filter_opens_on_a_silent_expected_day` (a Sunday,
  with the defaults), `test_r20_filter_resolves_when_a_record_arrives`,
  `test_r20_filter_has_text_steps_and_describer`, `test_r20_filter_mail_names_no_address`.
- Web unit: `web/src/nav.test.ts` ("no menu entry is a placeholder"; the auditor's menu gains
  `compliance` between `evidence` and `audit`, `:14-25`),
  `web/src/components/layout/app-shell.test.tsx` (`/compliance`, `/system` open pages of their own;
  the placeholder case removed), `shipped-bundle.test.tsx` (placeholder case removed),
  `web/src/pages/compliance/compliance-page.test.tsx` (the auditor gets the cards and the PDF, no
  confirm button), `web/src/pages/system/system-page.test.tsx`
  (captured samples; background header on refetch only; 403 page for auditor without a request),
  `web/src/pages/dashboard/dashboard-page.test.tsx` (admin's clock link to `/system`; disk card only
  with `system.read`).
- E2E on captured samples (`web/e2e/mock-w4.ts` layered like `web/e2e/mock-sources.ts`):
  `web/e2e/compliance.spec.ts`, `web/e2e/system.spec.ts`, `web/e2e/w4-a11y.spec.ts` (both pages and the
  wizard summary, light and dark), `web/e2e/dashboard.spec.ts` (clock link, disk card per role),
  `web/e2e/shell.spec.ts` (the placeholder scan replaced by both pages), `web/e2e/permissions.spec.ts`
  (placeholder test removed; the auditor's menu (`:30-43`) gains "Uyum Durumu" between "Kanıt
  Paketleri" and "Denetim Kaydı"; the auditor opens `/compliance` and downloads the PDF, without
  the confirm button; `/system` and `/settings/setup` closed by URL for auditor, hr, viewer and
  `/compliance` for hr, viewer, with no API call), `web/e2e-live/smoke.spec.ts` (both pages open
  on the real stack and show cards with reasons, not errors).
- Samples: `compliance.json`, `system.json`, `system-health.json`, `system-disk.json` captured; `--check`
  clean; `web/src/lib/api-samples.test.ts` and `api-types.test.ts` list the three type files.

---

## 4. Work packages

### 4.1 Branch, gate and ownership rules

- **Branches.** The manager creates `wave-4` from `wave-3` (`git push origin origin/wave-3:refs/heads/wave-4`)
  and, after every Wave 3 merge, merges `wave-3` into `wave-4`. Packages start with `git fetch origin
  wave-4 && git checkout -B <w4-x> origin/wave-4` and open their pull request **into `wave-4`**. Nobody
  pushes to `wave-3`, `wave-4` or `main`. `wave-4` goes to `main` after `wave-3` has.
- **Every merge keeps every gate green:** `uv run --frozen poe lint`; the touched pytest paths with `-n
  3 -m 'not perf' -p no:cacheprovider`; `poe test-perf` when a perf guard is touched;
  `export_api_contract.py` and `capture_api_samples.py` and both again with `--check`; `cd web && npm
  run check`; the touched Playwright specs. Never skip, xfail or weaken a test.
- **Generated files** (`web/src/generated/*.json`, `web/e2e/samples/*.json`) belong to no package: every
  merge whose input changes them regenerates them with the two scripts; a conflict (also when `wave-3` is
  merged into `wave-4`) is resolved by regenerating, never by hand (`design/wave-3-contract.md:5027-5030`).
- **One owner per file** (§4.3). A file another package owns is never edited; write the need under
  "Files outside my ownership" in the pull request and on the issue.
- **Fixed by this contract:** endpoints, JSON shapes and model names; card ids, order, conditions and
  texts; setting keys, defaults and bounds; the error code; audit actions; job name; alert rule, key,
  severity and texts; route paths; the fixed text keys of 2.8.5; test names of §3. A package that must
  deviate writes it in the pull request and under "Deviations" below.

### 4.2 Coupling points — why W4-F exists

These changes fail a test whose other half is a file a Wave 3 package owns today, so they are made
only in W4-F, after Wave 3 has merged into `wave-4`:

| Change | Test that couples it | Wave 3 owner of the other half |
|---|---|---|
| a module under `api/src/arcilogger_api/routers/` (new API area) | `tests/test_docs_consistency.py:1559-1572` (LISANS/LICENSING §2.2), `api/tests/test_license_routes.py:255-259`, `web/src/lib/api-types.test.ts:203-210` | WP-F2 (LISANS/LICENSING), WP-D (`test_license_routes.py` gains the observed route), WP-E (`api-types.test.ts`) |
| a `Job` in `default_jobs` | `tests/test_docs_consistency.py:715-723`, `:1576-1595`; `worker/tests/test_service.py:157-186` (the exact first-tick list) | WP-F2 (CORRELATION, LISANS); `test_service.py` has no Wave 3 owner and is W4-F's |
| a string `R20-filter` / `R20-lease` in Python code | `tests/test_docs_consistency.py:299-334` | WP-B / WP-F2 (ILK-ADIMLAR §9) |
| `compliance`, `system` in `BUILT_PAGES` | `tests/test_docs_consistency.py:627-638`, `web/e2e/permissions.spec.ts:60-86` | WP-F2 (README), integrator (`permissions.spec.ts`) |
| a web file calling `/compliance` or `/system…` | `web/src/lib/api-types.test.ts:227-295` (needs the mounted API) | — (follows the first row) |
| a new sample file | `web/scripts/capture_api_samples.py:203` | WP-D |
| a new `R20-…` check | `alerts/system.py`, `alerts/texts.py` | WP-B |
| `compliance.read` for the auditor in `ROLE_PERMISSIONS` (D15) | `web/e2e/permissions.spec.ts:30-43` (the auditor's menu, through the regenerated `web/src/generated/roles.json`), `web/src/nav.test.ts:14-25`, `api/tests/test_security.py:59-69` | integrator (`permissions.spec.ts`); `security.py`, `nav.test.ts` and `test_security.py` have no Wave 3 owner and are W4-F's |

Before W4-F, A, B and D test their routers by mounting them in their own test modules
(Implementation note 3), and C builds views that receive their data as props.

### 4.3 File ownership

"Owns" = may create or change. Shared, generated files: §4.1. Everything not listed is owned by
nobody in Wave 4 and must not be touched.

| Package | Owns |
|---|---|
| W4-A | `api/src/arcilogger_api/compliance/` (all files); `worker/src/arcilogger_worker/compliance_filter.py`; `db/src/arcilogger_db/settings.py`, `db/src/arcilogger_db/settings_meta.py` (the three keys only; frozen for Wave 3 since WP-C1, `design/wave-3-contract.md:5072`); `web/src/lib/compliance-types.ts`; `api/tests/test_compliance_cards.py`, `test_compliance_api.py`, `test_compliance_report.py`, `test_compliance_perf.py`; `worker/tests/test_compliance_filter.py`; `db/tests/test_settings_filter.py` |
| W4-B | `core/src/arcilogger_core/hostinfo.py`; `core/src/arcilogger_core/layout.py` (`system_history`); `api/src/arcilogger_api/system/` (all files); `worker/src/arcilogger_worker/system_jobs.py`; `web/src/lib/system-types.ts`; `core/tests/test_hostinfo.py`; `worker/tests/test_system_jobs.py`; `api/tests/test_system_forecast.py`, `test_system_api.py`, `test_system_perf.py` |
| W4-C | `web/src/pages/compliance/compliance-view.tsx`, `compliance-card.tsx`, `compliance-model.ts` and their tests; `web/src/pages/system/system-view.tsx`, `drive-meter.tsx`, `system-model.ts` and their tests; `web/src/pages/dashboard/disk-card.tsx` and test; `web/src/pages/setup-wizard/` (all files); `web/src/pages/setup/setup-page.tsx`; `web/src/pages/settings/settings-page.tsx`; `web/src/components/admin/settings-frame.tsx` and `settings-frame.test.tsx`; `web/src/pages/audit/audit-labels.ts`; `web/src/routes.tsx` **until C's last merge** (the wizard route; then W4-F); `web/src/locales/areas/compliance/*`, `areas/system/*`, `areas/setupWizard/*`; `web/src/test/w4-fixtures.ts`; `web/e2e/setup-wizard.spec.ts`, `web/e2e/setup.spec.ts` |
| W4-D | `api/src/arcilogger_api/system_health/` (all files); `api/src/arcilogger_api/routers/admin_config.py` (`_BILINGUAL` only); `web/src/lib/system-health-types.ts`; `api/tests/test_system_health.py`, `test_setup_problem_texts.py`, `test_system_health_perf.py` |
| W4-E | `docs/UYUM-DURUMU.md`, `docs/COMPLIANCE-STATUS.md` (new; **until E's merge**, then W4-G); `docs/YEDEKLEME.md`, `docs/BACKUP.md`; `docs/kamusm.md`; `docs/SECURITY.md`; `docs/KABUL-TESTI-WINDOWS11.md`; `deploy/windows/README.md` |
| W4-F (late) | `api/src/arcilogger_api/routers/compliance.py`, `routers/system.py`, `routers/system_health.py` (new); `api/src/arcilogger_api/security.py` (`ROLE_PERMISSIONS["auditor"]` += `compliance.read` only, D15); `api/tests/test_security.py`; `api/tests/test_errors_and_app.py`; `api/tests/test_license_routes.py`; `worker/src/arcilogger_worker/service.py`; `worker/src/arcilogger_worker/alerts/system.py`, `alerts/texts.py`; `worker/tests/test_alerts_filter.py` and any existing alert-text test that enumerates `RULES`; `worker/tests/test_service.py` (the first tick at 03:30 gains `("system.sample", "ok")` after `("reports.purge", "ok")`) and any other existing test that pins the `default_jobs` order or the `CHECKS` list; `web/src/lib/compliance-hooks.ts`, `system-hooks.ts`; `web/src/pages/compliance/compliance-page.tsx` (+ test); `web/src/pages/system/system-page.tsx` (+ test); `web/src/pages/dashboard/dashboard-page.tsx`, `dashboard-model.ts`, `dashboard-page.test.tsx`; `web/src/nav.ts`, `web/src/nav.test.ts`; `web/src/routes.tsx` (after C); `web/src/pages/placeholder-page.tsx` (deleted); `web/src/components/layout/app-shell.test.tsx`, `shipped-bundle.test.tsx`; `web/src/locales/tr.json`, `en.json` (`placeholder.*` removed only); `web/src/locales/ui-kit/tr.json`, `en.json` (`placeholder.*` removed only); `web/src/lib/api-types.test.ts`; `web/src/lib/api-samples.test.ts`; `web/scripts/capture_api_samples.py`, `web/scripts/samples_compliance.py`, `web/scripts/samples_system.py` (new); `web/e2e/mock-w4.ts`, `compliance.spec.ts`, `system.spec.ts`, `w4-a11y.spec.ts` (new), `web/e2e/dashboard.spec.ts`, `shell.spec.ts`, `permissions.spec.ts`, `web/e2e/samples.ts`; `web/e2e-live/smoke.spec.ts`; `README.md`; `docs/LISANS.md`, `docs/LICENSING.md`, `docs/CORRELATION.md`, `docs/ILK-ADIMLAR.md`, `docs/FIRST-STEPS.md`, `docs/UYARILAR.md`, `docs/ALERTS.md` |
| W4-G (late) | `docs/KURULUM.md`, `docs/INSTALL.md`, `docs/AYARLAR.md`, `docs/SETTINGS.md`, `docs/roles.md`, `docs/MVP-CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/DENETCI-KILAVUZU.md`, `docs/ASSUMPTIONS.md`, `CHANGELOG.md`, `docs/adr/0017-compliance-and-system-pages.md` (new), `docs/adr/README.md`; `docs/UYUM-DURUMU.md`, `docs/COMPLIANCE-STATUS.md` (after W4-E's merge) |

Files W4-F and W4-G own are owned by Wave 3 packages today (`design/wave-3-contract.md:5302-5402`)
or follow from them; that is why both are late packages.

### 4.4 The packages

#### W4-A — Uyum Durumu: cards, PDF and settings (backend; about 3 days; no dependency)
- **Reads:** §0, §2.2, §2.3, §2.4 (not the `R20-filter` part), §2.7, §3 W4-A, §4.1-4.3, Implementation notes.
- **First merge (day 1):** `web/src/lib/compliance-types.ts` verbatim from 2.3.1 (C imports it).
- **Done when:**
  - [ ] the ten cards follow 2.3.3 exactly, with every listed condition tested;
  - [ ] `filter_evidence` follows 2.4 (every day of the week expected by default, the listed
        holidays not);
  - [ ] the three settings exist with their texts, bounds and defaults; the settings sample is regenerated;
  - [ ] the PDF and the confirmation endpoint follow 2.3.4-2.3.5 with their audit entries;
  - [ ] no Python string constant whose whole value is `R20-filter` or `R20-lease` (D7);
  - [ ] all §3 W4-A tests pass, the perf guard included; every gate is green.
- **Commands:** `uv run --frozen poe lint`; `uv run --frozen pytest -n 3 -m 'not perf' -p no:cacheprovider api/tests/test_compliance_*.py worker/tests/test_compliance_filter.py db/tests/test_settings*.py tests/test_docs_consistency.py`; `uv run --frozen poe test-perf`; both scripts and `--check`; `cd web && npm run check`.

#### W4-B — Sistem: resources and disk forecast (backend; about 2.5 days; no dependency)
- **Reads:** §0, §2.2, §2.5, §3 W4-B, §4.1-4.3, Implementation notes.
- **First merge (day 1):** `web/src/lib/system-types.ts` verbatim from 2.5.1.
- **Done when:** `hostinfo` on Linux and Windows (Windows CI), the job function and history file,
  forecast, database sizes on both storage modes, collector and ingest blocks without sender addresses,
  `GET /system` and `/system/disk` as 2.5; §3 W4-B passes; gates green.
- **Commands:** as W4-A with `core/tests/test_hostinfo.py worker/tests/test_system_jobs.py api/tests/test_system_*.py`.

#### W4-C — Web: page views, Panel disk card view, first-run wizard (web; about 3.5 days; imports the mirrors of A, B, D)
- **Reads:** §0, §2.8, the JSON of 2.3.1/2.5.1/2.6.1, §3 W4-C, §4.1-4.3, Implementation notes.
- **Never** calls `/compliance` or `/system…` (no hook, no `api.get`): the views get their data as props;
  unit tests use `web/src/test/w4-fixtures.ts` (one value of each shape, every tone and state).
- **Done when:** the views of 2.8.1-2.8.3 and the wizard of 2.8.4 work; texts of 2.8.5; §3 W4-C passes
  including axe; `npm run check` and the touched Playwright specs green.
- **Commands:** `cd web && npm ci && npm run check`; `cd web && CI=1 npx playwright test e2e/setup.spec.ts e2e/setup-wizard.spec.ts --workers=2`.

#### W4-D — Sistem: health, tasks, jobs and clock (tests/infra; about 2 days; no dependency)
- **Reads:** §0, §2.2, §2.6, §3 W4-D, §4.1-4.3, Implementation notes.
- **First merge (day 1):** `web/src/lib/system-health-types.ts` verbatim from 2.6.1.
- **Done when:** `GET /system/health` as 2.6 (task table checked against `deploy/windows/tasks/*.xml`),
  the `_BILINGUAL` fix; §3 W4-D passes; gates green.
- **Commands:** as W4-A with `api/tests/test_system_health*.py api/tests/test_setup_problem_texts.py api/tests/test_admin_config.py`.

#### W4-E — Docs that Wave 3 does not own (docs; about 2 days; no dependency)
- **Reads:** §0, §2.3.3 (card texts), §2.6.2 (task table), §2.9, §4.1-4.3.
- **Done when:** the files of 2.9 (W4-E) are written TR/EN; the section numbers of 2.3.2's doc
  references exist; docs tests green; no example value outside the fictional world; the wording rule holds.
- **Commands:** `uv run --frozen pytest -n 3 -p no:cacheprovider tests/test_docs_consistency.py tests/test_search_docs.py`.

#### W4-F — Switch-on, samples and E2E (late; tests/infra; about 3 days)
- **Start:** Wave 3 has merged into `wave-4` **and** W4-A, W4-B, W4-C, W4-D have merged. The branch may be
  prepared earlier (samples modules, E2E specs on `w4-fixtures.ts`), merged only then.
- **Reads:** all of §2 and §3, §4.1-4.3.
- **Does:** the three router modules (`from arcilogger_api.compliance.routes import router as router`,
  …); `EXPECTED_ROUTERS` and `test_license_routes.py`; `Job("system.sample", job_system_sample,
  daily_after=time(0, 30))` after `reports.purge` in `default_jobs`; `check_filter` with `RULES`,
  `_STEPS`, `DESCRIBERS` of 2.4; `compliance.read` for the auditor (D15) with `test_security.py`;
  the hooks and page containers of 2.8.1-2.8.3; `BUILT_PAGES` +=
  `compliance`, `system`, lazy routes, deletion of the placeholder mechanism (D14) and rewrite of the five
  pinning tests; the Panel disk card; `TYPES_FILES` and the samples test list; `samples_compliance.py`
  and `samples_system.py` in `SAMPLE_MODULES`, samples captured; the E2E of §3; the docs rows of 2.9
  (W4-F) — **all in one pull request** so every coupled test turns green together.
- **Done when:** every coupled test of 4.2 is green; §3 W4-F passes; full Playwright and the live smoke
  pass; both `--check` scripts clean; the Windows CI jobs green.
- **Commands:** `uv run --frozen poe lint`; `uv run --frozen poe test`; `uv run --frozen poe test-perf`;
  both scripts and `--check`; `cd web && npm run check && CI=1 npx playwright test --workers=2`.

#### W4-G — Docs that Wave 3 owns today (late; docs; about 2 days)
- **Start:** Wave 3 has merged into `wave-4` and W4-E and W4-F have merged (so A-D too): G merges
  last, after every package's deviations are known. The branch may be prepared earlier.
- **Reads:** §0, §2 (for the facts it documents), §2.9 (W4-G), every "Deviations" section, Owner
  questions.
- **Done when:** the files of 2.9 (W4-G) are updated TR/EN; every `## Deviations — <package>` is
  folded into MVP-CONTRACTS/ASSUMPTIONS and, where a card text or condition changed, into
  UYUM-DURUMU/COMPLIANCE-STATUS; ADR-0017 is listed in the ADR index; docs tests green.
- **Commands:** `uv run --frozen pytest -n 3 -p no:cacheprovider tests/test_docs_consistency.py tests/test_search_docs.py`.

### 4.5 Merge order

```
            ┌─ W4-A ─┐
            ├─ W4-B ─┤
wave-4 ─────┼─ W4-C ─┼──(Wave 3 merged into wave-4)── W4-F ── W4-G ── manager: all gates, Windows jobs ─> main
(from       ├─ W4-D ─┤
 wave-3)    └─ W4-E ─┘
```

A, B, D merge their TypeScript mirror first (day 1), then the rest. C rebases once they are in. The
only serial steps are "Wave 3 merged" and "A-D merged" before F, and "E and F merged" before G
(G folds the deviations, so it merges last; it may prepare its branch while F runs).

---

## Implementation notes

1. **No legal claim anywhere.** Card, PDF, locale and doc texts never say "uyumlu" or "conforms"
   nor any phrase of the claim lists (0.1 item 7); settings meta also never "complian" or "uyumu"
   (0.1 item 7). "Uyum Durumu" is the page's name, not a verdict.
2. **Rule-code constants:** before W4-F no Python string constant has the whole value `R20-filter`
   or `R20-lease` (D7); use the `"R20-filter:"`, `"R20-lease:"` prefixes. Longer texts that mention
   the rule are fine. `R15`, `R20-seal`, `R20-verify`, `R20-retention`,
   `R20-credit`, `R20-clock`, `R20-replica`, `R20-backup` exist already and may be used.
3. **Testing an unmounted router** (A, B, D): override the `app` fixture of `api/tests/conftest.py` in
   the test module — request the parent `app` and `include_router(routes.router, prefix=API_PREFIX)`
   unless a route with that path already exists, so the test keeps passing after W4-F mounts it.
4. **Never lock alerts from the API:** read them with a plain `SELECT` (2.3.2).
5. **Audit details are integers and strings only** (`api/src/arcilogger_api/audit.py:162-164`).
6. **The API may import the worker, never the reverse** (`worker/src/arcilogger_worker/pdf/__init__.py:8-10`):
   `compliance_filter.py` therefore lives in the worker and imports nothing from `arcilogger_api`.
7. **Verify state names:** the CLI puts `last-` before a kind that already starts with `last-`
   (`core/src/arcilogger_core/cli.py:353-355`): `verify --last 2` (`deploy/windows/tasks/VerifyDaily.xml:45`)
   writes `last-last-2.json`, `--last 90` (`VerifyWeekly.xml:48`) `last-last-90.json`, and a young
   chain `last-last-<sealed days>`; only `--all` writes `last-full.json`. Tests build these names
   with the same expression; `worker/tests/test_alerts_system.py:255-257` writes `last-2.json`, a name
   the CLI never produces, which hides the mismatch.
8. **Panel helpers:** reuse `ingest_box`, `seal_box`'s use of `VaultView.summary`, `estimated_days`,
   `credit_box`, `clock_box` (import them; do not copy). `SEAL_STATUS` folds `verified` into `sealed`
   (`dashboard.py:87-97`): cards read `summary` directly.
9. **TimescaleDB sizes:** whether `hypertable_size()` counts compressed chunks is checked on the Linux
   TimescaleDB CI job; if not, list the shortfall under Deviations (the total stays authoritative).
10. **Core coverage gate** (≥ 90 %, both CI jobs): test `hostinfo`'s Linux part on Linux and its Windows
    part on the Windows job, as the existing platform modules do.
11. **Polling:** every automatic refresh goes through `usePollingQuery` (background header); "Yenile"
    counts as activity.
12. **Locale areas** add keys only; a key of an area must not exist in `tr.json`/`en.json` or another area
    (`web/src/locales/locales.test.ts:131-177`).
13. **Holiday parsing:** reuse the usage module's parser of `usage.holidays` if one exists; otherwise
    accept exactly "MM-DD" and "YYYY-MM-DD".

---

## Deviations

Each package appends `## Deviations — <package>` here in its pull request: what differs from this
contract, why, which tests show it, and what W4-G must fold into MVP-CONTRACTS, ASSUMPTIONS or
UYUM-DURUMU/COMPLIANCE-STATUS (W4-G merges last, §4.5).

## Deviations — contract (from the specification and roadmap, 2026-10-28)

- SPEC 12.3 "rsyslog stats": the product has its own collector (ADR-0002); Sistem shows its statistics.
- SPEC 3.4 card 3 asks for a daily 30-day and a weekly full-chain check; the tasks verify 2 days daily,
  90 days weekly and the whole chain monthly (`deploy/windows/tasks/VerifyDaily.xml:45`,
  `VerifyWeekly.xml:48`, `VerifyMonthly.xml:61`); card 3 judges the existing results (Q6).
- SPEC 3.4 card 8 "DHCP + ARP logs": the card follows the configured lease evidence (Q11).
- SPEC 3.1(a) "profile names seen" for DNS: no DNS profile is stored (`db/src/arcilogger_db/models.py:328-351`);
  the card says so, "sahada doğrula".
- SPEC 12.3 page 12 / roadmap 1.8 "compliance" schedule kind in Raporlar: not in Wave 4 (D8, Q14).
- SPEC 12.3 page 17 pre-fills sources from the first customer's device list: the product ships no
  customer data (`docs/ASSUMPTIONS.md:37-38`); the wizard pre-fills nothing.
- SPEC 13 asks for an encrypted nightly dump of database and configuration to the replica target; the
  repository dumps on the same disk and copies the configuration by hand (`docs/YEDEKLEME.md:117-137`);
  card 10 reports what exists.

## Deviations — W4-B (2026-10-28)

- **A forecast date more than 100 years away is `null`** (2.5.2 "Forecast"). A drive that grows by
  a few bytes a day has a positive least-squares slope, so it is `estimated`, but its marks lie
  thousands of years ahead, past the calendar (the first version answered 500 for such a drive).
  `forecast.HORIZON_DAYS` = 36,500: a mark further away gets `warning_on`/`full_on` = `null` while
  `state` stays `estimated` (with `growth_bytes_per_day` the rounded slope, 0 for less than half a
  byte a day). **W4-C:** the `estimated` text needs a variant for `full_on: null`, e.g. "Disk
  kullanımı çok yavaş artıyor; dolma tarihi öngörülemiyor." / "Disk use grows very slowly; no
  date can be given." Tests: `api/tests/test_system_forecast.py::
  test_a_drive_that_hardly_grows_gets_no_date`. Fold into 2.5.2 and W4-C's texts.
- **"Already above the warning mark" is decided by a measurement** (reading of 2.5.2): the drive's
  use at the moment of the request (`DriveInfo.used_bytes`), else the newest sample. A trend line
  that has passed the mark while no measurement has gives `warning_on` = today. `warning_on` is
  therefore `null` when the drive is at or above the mark, or when the mark lies beyond the
  horizon (deviation above); whether a drive is already above the mark is read from
  `used_bytes`/`used_percent` against `warning_percent`, never from the `null`. Tests:
  `test_forecast_already_above_the_warning_mark`. Fold into 2.5.2.
- **The collector and ingest blocks list the configured sources that have an entry in the status
  file** (reading of 2.5.2): a configured source that has not sent yet has no row (the Kaynaklar
  page lists every configured source). `used_percent` is the R20-disk check's whole percent
  (`used * 100 // total`); on Linux a drive's `path` is the label R20-disk uses (the data
  directory's path, `/` for the system drive). Nothing to fold.

## Deviations — W4-D

- **English interval texts in the singular for one:** "Every 1 minute" / "Every 1 hour", not
  "Every 1 minutes" (2.6.2 "Every {n} seconds/minutes/hours"); plural from two on. English grammar.
  Tests: `test_schedule_texts_of_every_unit`, `test_jobs_from_worker_status_and_job_runs`
  (`leases.incremental`). W4-G: MVP-CONTRACTS (Sistem job texts).
- **Turkish "from" suffix by how the time is read:** `daily_after` reads "Her gün 00:15'ten sonra",
  "03:30'dan sonra", "01:00'den sonra" (vowel harmony and consonant assimilation of the spoken
  minutes, or of the hour on the full hour), not a literal "{HH:MM}'den" for every time; the task
  table of 2.6.2 already writes "00:10'dan", "00:40'tan", "00:05'ten" this way. Tests:
  `test_schedule_texts_of_every_unit`, `test_task_times_carry_the_turkish_suffix_of_how_they_are_read`.
- **`failing` also when `last_status` is `failed` with `failures` 0:** a run whose `job_runs` row
  the database refused is recorded by the worker as `last_status = "failed"`, `last_error =
  "database unreachable"` without counting a failure (`worker/src/arcilogger_worker/service.py`,
  `run_job`); 2.6.2 says "`failing` (`failures` ≥ 1)". Showing it `ok` would hide that the job did
  not run. Test: `test_jobs_from_worker_status_and_job_runs` (`alerts.import`).
- **`running` from `job_runs`, a second bounded probe per job:** the worker writes
  `state/worker-status.json` only between its ticks and a tick runs each due job to its end, so the
  file never shows a job in progress. Per job, after the duration probe of 2.6.2, one more probe of
  `ix_job_runs_job_started_at`: `WHERE job = :job AND status = 'running' AND started_at > :after
  ORDER BY started_at DESC LIMIT 1`, `:after` = the start of the newest finished run (else the 7-day
  bound), so it reads only the rows past that run. A row older than the worker's `started_at` (left
  by a worker that stopped mid-run) or not newer than the run the status file names (its end could
  not be written) is not running. While running, `last_started` is the running row's start;
  `last_finished`, `last_status` and `duration_ms` stay those of the run before. A worker that
  stopped mid-run and was not started again shows that job `running` (ArciWorker itself shows
  `stale`). Tests: `test_jobs_from_worker_status_and_job_runs` (`leases.incremental`,
  `alerts.system`, `evidence.purge`), `test_health_reads_bounded_probes_per_job`,
  `test_health_within_one_second` (both probes explained). W4-G: MVP-CONTRACTS.
- **Seal evidence skips the states Retention wrote:** the newest modification time of a
  `state\seal\<YYYY-MM-DD>.json` whose `status` is not `deleted`, not of every file in the folder:
  Retention rewrites the state of each day it deletes (`status = "deleted"`,
  `core/src/arcilogger_core/retention.py` `_delete_day`), which on an installation older than its
  retention period would read as Seal evidence every day. The files are listed newest first (a stat
  each) and read until one is not deleted. A fixed window of recent days would miss a time-stamp
  outage of days: `seal_due` stops at the first failure, so Seal retries and rewrites only its
  oldest unsealed day. Test: `test_task_states_from_evidence_files` (an old day's `deleted` state
  written now leaves Seal `late`, then `missing`; a day six days back rewritten five minutes ago
  makes it `ok`). W4-G: MVP-CONTRACTS (Sistem task table).
- **Perf guard data:** `test_health_within_one_second` loads the part of the §3 perf data this
  endpoint reads: `job_runs` 17,000 rows a day for 180 days, today's and yesterday's `state/clock`
  files, status files with 20 sources, 30 days sealed in the vault (mock TSA), the verification
  results. `fg_web`, `fg_dns`, `search_counts` and the 200 active alerts are not loaded: the endpoint
  reads none of those tables.

## Deviations — W4-A

- **Card 4 reads its alerts by rule, not by dedup-key prefix (2.3.2).** The retention task's
  alerts are imported without a dedup key (`core/src/arcilogger_core/retention.py:214-225`,
  `:479-487` give no `key` fact; `worker/src/arcilogger_worker/alerts/importer.py` `import_key`
  has no `R20-retention` case and the rule is not in `ONE_ACTIVE`), so the prefix
  `R20-retention` never matched a real alert and the card's error row could not fire. Card 4
  therefore reads active alerts with `rule = 'R20-retention'` and `severity <> 'info'` (the
  `info` alert only notes days deleted as configured, as `worker/src/arcilogger_worker/evidence_jobs.py:424`
  reads it). Test: `test_retention_card_states` inserts the alerts as the importer stores them
  (no dedup key). The other cards' patterns match the keys the importer and the worker write.
  W4-G: fold into UYUM-DURUMU/COMPLIANCE-STATUS §2.4 if the section names the alert key.
- **Card 5 "the newest known credit is 0" is read as `≤ 0`.** The credit log may hold a negative
  reading (`api/src/arcilogger_api/routers/dashboard.py` `estimated_days` handles one); a
  negative credit is used up too. Test: `test_tsa_card_states`.
- **Card 10: a damaged `state/backup/last.json` takes the error row (manager decision on #41).** A
  file that does not parse or is not an object is a failed backup, as the worker's backup check
  opens `R20-backup` for it ("Yedek durum dosyası okunamıyor"); a file that cannot be read at all
  (access denied, a sharing violation) raises, so the card is `neutral` "could not be checked"
  (2.3.2), as every other unreadable file of every card (card 3's `last-full.json` included: an
  unreadable result is never a failed chain check). A status with a card from that error path is
  not kept in the 30 s cache (2.2). Tests: `test_backup_card_reads_a_damaged_state_file_as_a_failed_backup`,
  `test_chain_card_cannot_be_checked_with_an_unreadable_full_check`,
  `test_an_answer_with_a_card_that_could_not_be_computed_is_not_cached`. W4-G: UYUM-DURUMU /
  COMPLIANCE-STATUS §2.3 and §2.10.

## Deviations — W4-F (2026-10-29)

- **W4F-1: five fractional fields are `Float`, not `number` (manager decision on #27).**
  `IngestSource.lag_seconds`, `IngestSource.lines_per_second`, `IngestInfo.lines_per_second`
  (`web/src/lib/system-types.ts`) and `ClockSampleOut.offset_ms`, `ClockSampleOut.delay_ms`
  (`web/src/lib/system-health-types.ts`) are `Float` (`api-types.ts`). §2.5.1 and §2.6.1 mirror
  these as `number`; they carry fractional values, and `api-samples.test.ts` takes only `Float`
  for those (the Pydantic models are `float`; a `number` there is an integer). Test:
  `web/src/lib/api-samples.test.ts` "system.json" and "system-health.json" (the captured
  samples carry a 3.2 ms clock offset, a fractional ingest lag and fractional lines per second).
  Fold into §2.5.1 and §2.6.1.

---

## Owner questions

Each is implemented with the default shown until the owner decides; an answered question is
implemented as answered. The pull request lists the ones its package touches.

| # | Question | Default in Wave 4 |
|---|---|---|
| Q1 | Who sees Uyum Durumu? Should the auditor (`docs/roles.md:352-353`, open question S5)? | Answered 2026-10-28: admin and auditor |
| Q2 | Who sees Sistem and the Panel disk card? | `system.read` = admin only |
| Q3 | A fourth grey state "no data / does not apply"? | yes (`neutral`) |
| Q4 | Card 1 with no watched source: amber or red? | amber |
| Q5 | A stamp not from the Kamu SM production server (mock, RFC 3161, test server): which card, which colour? | card 5 red, card 2 amber |
| Q6 | Card 3: change the verification tasks to the specification (30 days daily, full chain weekly) or judge the existing 2/90/monthly results? | judge the existing results |
| Q7 | Card 4: "system age" = first sealed day (incl. deleted ones)? Retention off with days past the configured maximum: amber or red? | first sealed day; amber |
| Q8 | Card 5: extra "days of credit left" threshold? Certificate red only when expired? | no extra threshold; red only when expired |
| Q9 | Card 6: an offset equal to the limit counts as a problem (`<`, specification) while the Panel uses `<=`; align the Panel? | card `<`, Panel unchanged |
| Q10 | Card 7: expected days (are weekends working days?), holidays, rolling window length, web **or** DNS enough, `R20-filter` severity | Answered 2026-10-28: every day; listed holidays not expected; 24 h rolling; either kind; high |
| Q11 | Card 8: follow the configured lease evidence (Wave 3) instead of "DHCP and ARP" literally? | yes |
| Q12 | Card 9: replica switched off = red or amber? | red |
| Q13 | Card 10: is an administrator's confirmation of the offline configuration copy acceptable evidence? | yes, compared with the current file's SHA-256 |
| Q14 | Uyum Durumu Raporu: e-mail it on a schedule (to whom)? Time-stamp it (one credit per export)? | on request only; not time-stamped (SHA-256 in the audit log) |
| Q15 | Documentation links: name document and section as text, or serve the bundled docs in the web app? | text |
| Q16 | CPU/RAM: standard library (`ctypes`) or `psutil` (per-process figures, new third-party notice)? | standard library |
| Q17 | Disk forecast: linear over the last 30 daily samples, shown from 7 samples? | yes |
| Q18 | Wizard: remember skipped steps and redirect the first administrator to it after the first sign-in? | no: state derived from configuration, entry from Ayarlar and Uyum Durumu |
| Q19 | Read Windows service and Task Scheduler states (needs a SYSTEM-written status file)? | no; tasks judged by their evidence files |
| Q20 | Put the Uyum Durumu Raporu texts on the lawyer review list (like RAPOR.md)? | yes (W4-G notes it in ASSUMPTIONS) |
| Q21 | `job_runs` grows about 17 000 rows a day; add a purge (how many days)? | no purge in Wave 4 |
