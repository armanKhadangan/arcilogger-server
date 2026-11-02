# Wave 5 contract: re-stamping, revocation lists, legal-validity banners and TÜRKTRUST

- **Status:** final, 2026-11-02. The owner answered Q1–Q3 on 2026-11-02; Q4 and Q5 ship their
  defaults (see "Owner questions").
- **Scope:** roadmap Wave 5 ("re-stamping (renewal), CRL, legal-validity banners") and the
  TÜRKTRUST time-stamp provider (owner decision, 2026-11-02).
- **Base:** `wave-5` starts from `wave-4` at `6a27c95` (Wave 3 and Wave 4 complete). Every
  `path:line` below was read there.
  - The open Wave 4 follow-ups (#83, #88, #92, #94) still merge into `wave-4`. The manager merges
    `wave-4` into `wave-5` after each one, as was done for `wave-3` into `wave-4`.
- **Owner answers already given (2026-11-02, "hepsi için evet"):**
  - TÜRKTRUST is in Wave 5.
  - An outbound HTTP proxy is supported.
  - A frozen vault fixture is committed for format tests.
  - The legal-validity banner is shown to every signed-in role.
  - A token whose TSA certificate expired without a renewal is a **warning**, not a failure.
- **Owner answers on the Wave 5 questions (2026-11-02):**
  - Q1, "yasal": TÜRKTRUST production stamps count as legal grade (D12).
  - Q2, "TÜRKTRUST çalışması için ne gerekiyorsa": HTTPS by default, and plain HTTP as an
    explicit, warned fallback (D12, §2.4).
  - Q3, "ne daha iyi ve güvenliyse": the stored report tokens are renewed too (D3).
- **Language:** the contract is in English; every user-facing text is Turkish first with English
  beside it. The only wording about the law is "5651 yükümlülüklerini desteklemek için
  tasarlanmıştır" / "designed to support 5651 obligations".
- **Example world only:** Örnek A.Ş., `172.20.x`, `00:00:5E:00:53:xx`, RFC 5737. No TÜRKTRUST
  contact person, account name or password appears anywhere in the repository.
- **"Sahada doğrula" (verify on site):** every behaviour of Kamu SM or TÜRKTRUST that the
  repository does not confirm is configurable, marked "sahada doğrula" where it is shown, and has
  a test that fails loudly if the assumption breaks (§0.4 lists them).

---

## 0. Rules and decisions

### 0.1 Rules that bind every package

1. **Product rules** (`CLAUDE.md:29-48`) and the Wave 4 rules 0.1 items 1, 5, 6, 7 and 8
   (`design/wave-4-contract.md:30-58`) apply unchanged.
2. **Sealed data is never rewritten.**
   - Re-stamping adds new files under `vault/renewals/`. It never touches a day folder, a
     manifest or a token.
   - The CRL job writes only under `state/crl/`.
   - Nothing in Wave 5 deletes anything; only the retention job deletes (`CLAUDE.md:34`).
3. **Only hashes leave the server.** A renewal sends the SHA-256 of a renewal record, which
   lists hashes only. The CRL job downloads; it never uploads anything.
4. **Legal grade stays a three-part test** (`core/src/arcilogger_core/tsa/verify.py:65-75`):
   - the stamp claims a production provider;
   - the token verifies;
   - its chain ends at a root the site listed in `tsa.legal_root_fingerprints`.

   Wave 5 widens only the first part (TÜRKTRUST, D12). It never adds a fourth way to be legal.
5. **Two verifiers, one verdict** (ADR-0006).
   - The core verifier and the standalone verifier
     (`core/src/arcilogger_core/evidence/standalone/verify.py`) give the same code and level for
     every new check.
   - A shared table of cases in the tests feeds both.
6. **No licence state blocks a 5651 function.** Re-stamping, CRL fetch, the banner and the
   TÜRKTRUST provider are **5651 Temel**: no `require_module`, no `ModuleGate`.

### 0.2 Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Renewal record**: `vault/renewals/<YYYY-MM-DD>-<seq>.json` (canonical JSON, schema `arcilogger.renewal/1`) plus its token `<same>.json.zd`. It lists every token it protects by SHA-256 (§2.1.1). | The builder already ships `vault/renewals/*.json` whose raw text contains a packaged day's manifest or token hash (`core/src/arcilogger_core/evidence/builder.py:451-462`). The standalone verifier already checks a renewal's token and reports `renewal-covers` (`.../standalone/verify.py:3069-3094`). The format fits both without a builder change. |
| D2 | **A renewal of a renewal re-lists the original day hashes.** When a renewal token's own TSA certificate comes due, the next renewal covers that renewal's token hash **and** repeats every day hash it protected. | A package for one day then picks up the whole chain by the same raw-text match (D1). No graph walk in the builder. |
| D3 | **What is renewed:** day tokens (`vault/YYYY/MM/DD/manifest-<day>.json.zd`), deletion-record tokens (`vault/deletions/*.zd`), the stored official-report tokens (`exports/reports/*.pdf.zd`, `api/src/arcilogger_api/reports/store.py:1-8`, `:60`) and earlier renewal tokens. The builder also ships a renewal whose text contains a packaged report's token hash. | Owner answer Q3: "ne daha iyi ve güvenliyse". A report is evidence handed to an authority; its token deserves the same protection as a day's. |
| D4 | **When:** `arcilogger-core renew`, run daily by a new task `Renew`. A token is *due* when its TSA certificate's `notAfter` is less than `tsa.renew_before_days` (default 180) from now and no renewal that still counts covers it. One run writes at most one record, covering all due tokens. | One stamp a day at most: credit use stays predictable. |
| D5 | **A renewal counts** (status `covered`) only if its token verifies, it was stamped by a provider whose claim is legal grade (rule 4), and its own certificate is not due. A renewal stamped by `mock`, `rfc3161` or a test server is written but never counts. | A trial or test renewal must never hide a due production token. |
| D6 | **Long-term status per token:** `not_due`, `due`, `covered`, `late` (covered, but by a renewal stamped after the old certificate expired) and `uncovered` (expired with no renewal). `due` and `late` are warnings; `uncovered` is a warning too (owner answer). The day's verification result is unchanged: a valid token stays valid (ADR-0006). | Renewal is supporting evidence; it does not change whether a stamp was valid when it was made. |
| D7 | **CRL fetch:** `arcilogger-core crl fetch`, run daily by a new task `CrlFetch`. The URLs are `tsa.crl_urls` when set, otherwise the CRL distribution points (http/https only) of the certificates in the pinned roots folder and in the newest token of each provider. **No URL is built into the code** (Kamu SM's CRL URLs are unknown, K10). | `CLAUDE.md:35`; `docs/kamusm.md` K10. |
| D8 | **CRL storage:** each downloaded CRL whose signature verifies with a pinned certificate is stored byte for byte as `state/crl/<issuer-sha256-12>-<thisUpdate as YYYYMMDDTHHMMSSZ>.crl`, and described in `state/crl/index.json`. A CRL that does not verify is not stored; its failure is recorded in the index. Files are never deleted by the job (owner question Q4). | The builder copies `state/crl` into packages (`builder.py:517-527`, `cli.py:803`, `api/src/arcilogger_api/evidence_jobs.py:453`). A CA may drop an expired certificate from later lists, so older lists keep their worth (`standalone/verify.py:1548-1559`). |
| D9 | **Packages ship only the CRLs they need:** for each issuer, the newest stored CRL plus the first CRL with `thisUpdate` ≥ each packaged day's stamp time. | A long-running site would otherwise put years of lists into every package. |
| D10 | **The core verifier checks revocation** with the same rules and codes as `check_revocation` (`standalone/verify.py:1548-1640`): `crl-revoked` is a failure (level `fail`); `crl-revoked-later` and `crl-hold` are warnings (`warn`); `crl-none` and `crl-stale` are information (`info`); `crl-ok` has the level `ok`. A revocation dated at or before the stamp time outranks an earlier hold of the same certificate (`crl-revoked`), in both verifiers (C6). `verify_token` gains `crls=`; `details["crl"]` carries the code instead of `"not checked"` (`verify.py:408`). | Rule 5. |
| D11 | **Legal-validity status** is computed by one server function and served at `GET /legal-status` to **every signed-in user** (owner answer). A global banner shows it on every page. The Panel shows a blocking card when a closed day is unstamped for longer than `seal.late_after_hours` (48 h, `core/src/arcilogger_core/config.py:105`). | One source for the banner, the Panel, the Mühürleme page and the alerts. |
| D12 | **TÜRKTRUST** is the new provider `turktrust` (§2.4).<br>• RFC 3161 with HTTP Basic authentication; the username and password live in the secret store, never in the TOML file.<br>• HTTPS by default; plain HTTP only when an administrator sets `tsa.allow_plain_http` (re-authentication), which raises the warning `credentials_in_clear`.<br>• The site's pinned roots are used. There is no credit query.<br>• **Its production stamps claim legal grade** (owner answer Q1, 2026-11-02), through the product constant `TURKTRUST_LEGAL_GRADE = True` and the host `zd.turktrust.com.tr`. The three-part test of rule 4 is unchanged: a day is shown as legal grade only when its token verifies and chains to a root the site listed (the TÜRKTRUST root, uploaded with the "legal" confirmation). | Owner decision; recorded under "Deviations — contract" because SPEC §21 named only Kamu SM. The claim is a product constant, so a site cannot make any other provider "legal". The docs recommend legal review and keeping TÜRKTRUST's written statement with the site's records. |
| D13 | **Proxy:** `[network] https_proxy` (empty by default; `http://host:port`). The CRL fetch and the `rfc3161`/`turktrust` providers use it. The Zamane console client does not use it in Wave 5, because how it takes a proxy is unknown ("sahada doğrula"). The proxy takes no credentials in Wave 5. | Owner answer; no invented Zamane arguments. |
| D14 | **New alerts** `R20-renewal` (high), `R20-cert` (critical when revoked, high when expiring uncovered) and `R20-crl` (medium), each with dedup key `<code>:none`. Only the switch-on package W5-E adds the whole-string constants (Wave 4 D7 technique). | `tests/test_docs_consistency.py:299-334` turns each constant into a required ILK-ADIMLAR §9 row. |
| D15 | **Frozen vault fixture:** `tests/fixtures/vault-w5/`. It holds three sealed days stamped by the mock TSA, one deletion record, one renewal and one CRL, generated by `tools/dev/make_vault_fixture.py` and committed. A test fails if today's code reads it differently. It is regenerated only on purpose, with the reason in the commit. | Owner answer; pins the formats across releases. |
| D16 | **No new database table and no migration.** Renewal and CRL state live in files (`vault/renewals/`, `state/crl/`, `state/renewal.json`). | Files are the evidence convention; no `db/tests/test_migrations.py` head change. |

### 0.3 Non-goals of Wave 5

- Hash-algorithm renewal (re-hashing the raw files with a new algorithm, RFC 4998 hash trees).
- OCSP.
- Online revocation checks inside the standalone verifier.
- Renewing report tokens that exist only inside exported packages (the stored copies in
  `exports/reports/` are renewed, D3).
- A credit query for TÜRKTRUST (TÜRKTRUST offers none; its balance is in its own portal).
- The Spira library (a paid TÜRKTRUST product; plain RFC 3161 is enough).
- Proxy credentials.
- Proxy support for the Zamane console client.
- Changing which roots are legal.
- Any wording that says a stamp, a renewal or a CRL makes a site "compliant".

### 0.4 "Sahada doğrula" list (each has a test that fails loudly)

| Id | Assumption | Where it is configurable |
|---|---|---|
| T1 | TÜRKTRUST's TSA URL. The host `zd.turktrust.com.tr` is from TÜRKTRUST's e-mail; the **path** is unknown. Default `https://zd.turktrust.com.tr/`. | `tsa.url` |
| T2 | TÜRKTRUST answers HTTP 401 for wrong credentials. Its answer for an empty balance is unknown: a rejection whose status text contains a credit word is treated as "no credit" (`CREDIT_WORDS`, `core/src/arcilogger_core/tsa/client.py:18`). | `tsa.credit_regex` is reused for the status text. |
| T3 | Whether TÜRKTRUST's TSA certificate marks the extended key usage critical. | `tsa.require_critical_eku` (K6 mechanism, `config.py:144-147`) |
| T4 | TÜRKTRUST's CRL host (from its "kök sertifikaları ve iptal listeleri" page). | `tsa.crl_urls`; the firewall opens their hosts. |
| T5 | Port 80 (plain HTTP). TÜRKTRUST's e-mail names ports 80 and 443; HTTPS is the default and the probe stamp confirms it on site. | `tsa.allow_plain_http` (default false) |
| K10 | Kamu SM's CRL URLs. | `tsa.crl_urls`, else the certificates' CRL distribution points. |

---

## 1. Facts this contract rests on

| Fact | Where |
|---|---|
| Providers today: `Literal["mock","rfc3161","zamane"]`; `url`, `port`, `digest` sha256/sha512, `timeout_seconds`, `roots_dir`, `legal_root_fingerprints`, `require_critical_eku` | `core/src/arcilogger_core/config.py:121-147` |
| The site's claim: `legal_grade` is true only for `zamane` on `zd.kamusm.gov.tr` | `config.py:39-40`, `:164-170` |
| Legal grade = claim ∧ verified ∧ root in `legal_root_fingerprints` | `core/src/arcilogger_core/tsa/verify.py:65-75`; callers `cli.py:502`, `chain.py:128`, `:147`, `evidence/builder.py:247`, `seal.py:1224`, `api/src/arcilogger_api/sealinfo.py:715` |
| The manifest's `tsa` block records `provider`, `legal_grade` (the claim) and `digest` | `core/src/arcilogger_core/seal.py:1188-1199`; schema `arcilogger.manifest/1` `seal.py:91` |
| A day's state keeps `tsa_cert_not_after` | `seal.py:1218-1220` |
| `make_provider` builds `rfc3161` with no credentials; `Rfc3161HttpProvider` posts `application/timestamp-query` with `urllib`, no `Authorization` header | `core/src/arcilogger_core/tsa/__init__.py:48-91`; `core/src/arcilogger_core/tsa/rfc3161.py:21-43` |
| Shared RFC 3161 logic (`Rfc3161Client.stamp`, imprint/nonce checks, `credits()` → None, `CREDIT_WORDS`) | `core/src/arcilogger_core/tsa/client.py:18-59` |
| Error classes: unavailable, no credit, rejected, response, configuration (not retryable) | `core/src/arcilogger_core/tsa/base.py:11-36` |
| `trusted_roots`: pinned roots of `tsa.roots_dir`, plus the mock root only while the provider is `mock` | `tsa/__init__.py:94-110` |
| `verify_token(data, *, roots, file_path, digest, now, require_critical_eku)`; certificates checked at genTime (`_valid_at`); `details["crl"] = "not checked"` | `verify.py:220-222`, `:310-416`, `:408` |
| Secrets: `kamusm.customer` (64), `kamusm.password` (128), limits table | `core/src/arcilogger_core/secretstore.py:30-50` |
| Browser administration: TSA paths and secrets, the `test`/`production` server choice, the root upload with "legal" confirmation, `SECTION_SECRETS` | `core/src/arcilogger_core/admin/rules.py:108-160`, `:223-228`, `:1019-1020`, `:1410-1445`; `core/src/arcilogger_core/admin/contract.py:107`, `:122` |
| The builder ships renewals by raw-text hash match and copies the CRL folder | `core/src/arcilogger_core/evidence/builder.py:92`, `:451-462`, `:517-527`, `:829` (`NO_CRL_NOTE` `:104`) |
| The standalone verifier's renewal and revocation checks and codes; `VERIFIER_VERSION = "4"` | `core/src/arcilogger_core/evidence/standalone/verify.py:76`, `:1548-1640`, `:3069-3094` |
| The sealing page shows `provider-not-legal` (danger) from `/credits` `legal_grade` | `web/src/pages/sealing/sealing-page.tsx:84-88` |
| The app shell's banner slot (`<LicenseBanner />`) | `web/src/components/layout/app-shell.tsx:6`, `:55` |
| Scheduled tasks: ApplySystem, Backup, ClockCheck, Credit, Replica, Requests, Retention, Seal, VerifyDaily, VerifyMonthly, VerifyWeekly | `deploy/windows/tasks/*.xml`; pinned by `tests/test_windows_tasks.py`, `api/src/arcilogger_api/system_health/` (`TaskName`), `web/src/lib/system-health-types.ts` |
| Strict outbound firewall: Kamu SM hosts and `sertifikalar.kamusm.gov.tr` for roots/CRLs | `deploy/windows/scripts/Set-ArciFirewall.ps1:27-28`, `:96-97` |
| `seal.late_after_hours` = 48 | `config.py:104-105` |
| Docs tests force rows for alert codes (ILK-ADIMLAR/FIRST-STEPS §9), verifier codes (DENETCI §7), API areas and worker jobs (LISANS/LICENSING) | `tests/test_docs_consistency.py` (sections cited in the Wave 4 contract §1) |

---

## 2. Design

### 2.1 Re-stamping (W5-A)

#### 2.1.1 Renewal record (`arcilogger.renewal/1`, canonical JSON)

```json
{
  "schema": "arcilogger.renewal/1",
  "created_at": "2031-05-04T03:10:00.000+03:00",
  "system": {"name": "ARCILOGGER", "version": "0.5.0", "site": "Örnek A.Ş."},
  "tsa": {"provider": "zamane", "legal_grade": true, "digest": "sha256"},
  "reason": "due",
  "tokens": [
    {
      "kind": "day",
      "day": "2029-06-01",
      "path": "2029/06/01/manifest-2029-06-01.json.zd",
      "token_sha256": "<64 hex>",
      "manifest_sha256": "<64 hex>",
      "cert_not_after": "2031-10-01T00:00:00Z"
    },
    {
      "kind": "deletion",
      "path": "deletions/2029-07-01-retention.json.zd",
      "token_sha256": "<64 hex>",
      "record_sha256": "<64 hex>",
      "cert_not_after": "2031-10-01T00:00:00Z"
    },
    {
      "kind": "renewal",
      "path": "renewals/2029-01-03-1.json.zd",
      "token_sha256": "<64 hex>",
      "record_sha256": "<64 hex>",
      "cert_not_after": "2031-07-01T00:00:00Z",
      "protects": ["<64 hex: day manifest/token hashes the older renewal protected>"]
    }
  ]
}
```

- **Kinds:** `kind` is `day`, `deletion`, `report` (with `path` under `exports/`, e.g.
  `exports/reports/ARCILOGGER-5651-rapor-<id>.pdf.zd`, and `record_sha256` = the PDF's SHA-256)
  or `renewal`.
- **The `tsa` block:**
  - It has the same keys as the manifest's (`seal.py:1188-1199`).
  - Its `legal_grade` is the provider's **claim**.
  - Whether the renewal counts follows from D5.
- **Order and format:**
  - `tokens` are sorted by `path`.
  - Hashes are lower-case hex.
  - Times are ISO 8601. `created_at` is in site time, as manifests are. `cert_not_after` is in UTC.
- **Names:**
  - `<seq>` starts at 1 for each date.
  - The file is written with `atomic_write_bytes`, stamped, and both files are then locked like
    sealed files.
  - A run that fails after writing the record but before the token is complete deletes the
    record. It is not evidence yet: it has no token.

#### 2.1.2 Status per token

`core/src/arcilogger_core/renewal.py` provides:

- `renewal_status(layout, config, roots, now) -> RenewalReport`. For every renewable token it
  gives:
  - `path`;
  - `kind`;
  - `cert_not_after`;
  - `status` (`not_due` | `due` | `covered` | `late` | `uncovered`);
  - `covered_by`: the path of the renewal that counts, or null.

  It also gives the totals per status.
- `renew(layout, config, provider, roots, now) -> RenewalRun`. It writes at most one record
  (D4). It refuses with `RenewalNotNeeded` when nothing is due. When the provider's claim is not
  legal grade, it writes the record anyway with `reason: "test"`, and that record never counts
  (D5).

The rules:

- A token is `not_due` while `cert_not_after - now > renew_before_days`.
- A token is `covered` when a counting renewal lists its `token_sha256`, was stamped before
  `cert_not_after`, and its own status is `not_due` or `covered` (a recursive check over the
  renewal chain).
- A token is `late` when the counting renewal was stamped after `cert_not_after`.
- A token is `uncovered` when `cert_not_after <= now` and nothing counts.
- Otherwise it is `due`.

The function also writes `state/renewal.json` (the report, for the API and the alerts). Only
the task writes it; the API only reads it.

#### 2.1.3 Command line

- `arcilogger-core renew [--dry-run] [--json]`.
- Exit codes:
  - 0: renewed, or nothing was due;
  - 1: renewed with warnings (some tokens `uncovered`);
  - 2: the configuration refused (no provider credentials, no roots);
  - 3: the time-stamp server failed (retryable).
- The run also writes `state/renewal.json`.

#### 2.1.4 Standalone verifier

`check_renewals` adds one finding per packaged day token:

| Level | Code | Meaning |
|---|---|---|
| info | `renewal-not-due` | — |
| ok | `renewal-covered` | — |
| warn | `renewal-due` | — |
| warn | `renewal-late` | — |
| warn | `renewal-uncovered` | — |

The TR/EN texts are in §2.6.

- The verdict is computed from the package alone, with `now` = the verification time.
- `VERIFIER_VERSION` becomes `"5"`.
- The core verifier gives the same codes through `chain.verify_day(..., renewals=True)`.

### 2.2 Revocation lists (W5-B)

- **`core/src/arcilogger_core/crl.py`:**
  - `fetch_all(config, layout, roots, now) -> CrlRun`:
    - downloads every URL of D7 (timeout `tsa.timeout_seconds`, at most 20 MB each, through
      the D13 proxy);
    - verifies each list's signature with the issuer among the pinned roots and the
      intermediates seen in tokens;
    - stores verified lists as D8 says;
    - updates `state/crl/index.json`.
  - `crls_for(layout, issuer, moments) -> list[Path]` selects the lists that D9 describes.
- **`state/crl/index.json`:**

  ```json
  {"schema": "arcilogger.crl-index/1", "lists": [{"url": "...", "file": "...", "issuer": "<subject text>",
  "issuer_sha256": "<64>", "this_update": "...", "next_update": "...", "sha256": "<64>",
  "fetched_at": "..."}], "failures": [{"url": "...", "at": "...", "error": "unreachable|too_large|not_a_crl|bad_signature|unknown_issuer"}]}
  ```
- **Command line:** `arcilogger-core crl fetch [--json]`.
  - Exit codes:
    - 0: every URL fetched and verified;
    - 1: some failed (recorded);
    - 2: no URL known.
  - `crl status` prints the index.
- **Verification:**
  - `verify_token(..., crls: Sequence[Crl] | None = None)` applies D10.
  - `chain.verify_day` passes the stored lists.
  - A `crl-revoked` result fails the day check with the error text of §2.6.
- **Builder:** `_crls` uses `crls_for` (D9). With no list it still writes `NO_CRL_NOTE`.
- **Layout:** `Layout.crl_dir` (= `state/crl`) and `Layout.renewals_dir` (= `vault/renewals`).
  The literal paths in `cli.py:803`, `evidence_jobs.py:453` and `builder.py:452` move to these.

### 2.3 Legal-validity status, banner, Panel and alerts (W5-C API/worker, W5-D web)

#### 2.3.1 JSON — `web/src/lib/legal-status-types.ts` (verbatim; Pydantic models in `api/src/arcilogger_api/legalstatus/models.py`)

```ts
export type LegalLevel = 'ok' | 'warning' | 'danger';
export type LegalReasonCode =
  | 'provider_not_legal'      // mock, rfc3161 or a test server
  | 'credentials_in_clear'    // turktrust over plain HTTP (tsa.allow_plain_http)
  | 'legal_root_missing'      // legal-grade provider but no legal root fingerprint listed
  | 'unstamped_days'          // a closed day older than seal.late_after_hours is not stamped
  | 'renewal_due'             // tokens due for re-stamping
  | 'renewal_uncovered'       // tokens whose certificate expired with no counting renewal
  | 'certificate_revoked'     // a TSA certificate in use is on a CRL (crl-revoked)
  | 'crl_stale';              // no verified CRL newer than tsa.crl_max_age_days
export interface LegalReason {
  code: LegalReasonCode;
  level: Exclude<LegalLevel, 'ok'>;
  message_tr: string;
  message_en: string;
  since: string | null;          // ISO time the condition was first seen, if known
  count: number | null;          // days or tokens concerned, if countable
  link: 'sealing' | 'settings-tsa' | null; // nav key; the web hides it without permission
}
export interface LegalStatus {
  level: LegalLevel;             // the worst reason's level, 'ok' when none
  provider: string;              // tsa.provider
  provider_label: string;        // "Kamu SM", "TÜRKTRUST", "Test sunucusu", "Deneme (mock)"
  reasons: LegalReason[];        // sorted danger first, then by code
  checked_at: string;
}
```

- **Levels:**
  - `provider_not_legal`, `legal_root_missing`, `unstamped_days` and `certificate_revoked` are
    **danger**.
  - `renewal_due`, `renewal_uncovered`, `crl_stale` and `credentials_in_clear` are **warning** (owner answer for
    uncovered).
- **Endpoint:** `GET /legal-status`.
  - Any signed-in user may call it.
  - It is not audited.
  - It carries no data about log subjects.
  - It is cached for 60 s.
- **Panel:** `DashboardSeal` gains
  `blocking: {oldest_unstamped_day: string, hours_late: number} | null`. The Panel shows a
  blocking card at the top while it is not null.
- **Day detail** (`GET /seals/{day}`, `api/src/arcilogger_api/routers/seals.py:176`) gains:
  - `renewal: {status, cert_not_after, covered_by, renewed_at} | null`. `covered_by` is the vault-relative path of the counting renewal record (`renewals/2031-05-04-1.json`). `renewed_at` is its token's genTime (ISO); null when nothing counts (C3).
  - `revocation: {code, level, message_tr, message_en} | null`. `level` is the verifiers' own `fail` / `warn` / `info` / `ok` (C4).
- **Alerts (W5-E switches them on):**

  | Alert | Raised when |
  |---|---|
  | `R20-renewal` | any `due` or `uncovered`; also when the `Renew` task failed twice in a row |
  | `R20-cert` | a certificate is `crl-revoked` (critical), or expires within 30 days and is uncovered (high) |
  | `R20-crl` | no verified list newer than `tsa.crl_max_age_days` (default 7) for an issuer in use |

  Mail texts carry no names and no log lines.

#### 2.3.2 Web (W5-D)

- **`<LegalBanner />`:**
  - It sits in `app-shell.tsx` under `<LicenseBanner />`.
  - Danger is red, warning is amber, always with an icon and text.
  - It shows the worst reason, plus "ve N uyarı daha / and N more", which opens the list.
  - Danger cannot be closed. A warning can be hidden for 24 h per browser (localStorage,
    wrapped in try/catch).
  - It refreshes every 5 minutes with `X-Arci-Background: 1`.
- **Sealing page:**
  - The existing `provider-not-legal` alert stays, now fed from `/legal-status`, with the same
    text as the banner.
  - The day panel shows renewal and revocation.
- **Panel:** the blocking card.
- **Ayarlar → Zaman damgası:** a third server choice "TÜRKTRUST" (§2.4) with a username field,
  a password field and the "sahada doğrula" note.

### 2.4 TÜRKTRUST provider (W5-T)

- **Config:**
  - `tsa.provider` becomes `Literal["mock","rfc3161","zamane","turktrust"]`.
  - `TURKTRUST_PRODUCTION_HOST = "zd.turktrust.com.tr"` (T1).
  - `TsaConfig.legal_grade` is also true for `turktrust` on that host while
    `arcilogger_core.tsa.turktrust.TURKTRUST_LEGAL_GRADE` is True (a module constant, True
    by the owner's decision, D12).
  - With the provider `turktrust`, `tsa.url` must start with `https://` unless
    `tsa.allow_plain_http` is true. Otherwise the configuration error names the reason: the
    password would travel in clear text.
  - With plain HTTP the legal-status reason `credentials_in_clear` is shown (warning).
- **Secrets:**
  - `turktrust.username` (limit 128, not secret-flagged) and `turktrust.password` (limit 256,
    secret-flagged) in `SECRET_LIMITS`, `BrowserSecret` and `SECTION_SECRETS["tsa"]`.
  - The CLI's `secret set` accepts both names.
- **`core/src/arcilogger_core/tsa/turktrust.py` — `TurktrustHttpsProvider(Rfc3161Client)`:**
  - `name = "turktrust"`.
  - The `legal_grade` claim follows the rule above.
  - It posts the request with `Authorization: Basic …` (RFC 7617, UTF-8). TLS is verified with
    the operating system's trust store. Turning verification off is impossible.
  - It honours the proxy.
  - Errors:
    - HTTP 401 or 403 → `TsaConfigurationError("credentials")` (not retryable);
    - HTTP 5xx or network errors → `TsaUnavailableError`;
    - a rejection with a credit word → `TsaNoCreditError` (T2).
  - `credits()` returns None.
  - The password never appears in exceptions, logs or `raw_output`.
- **Browser administration:**
  - `TSA_SERVERS["turktrust"] = ("turktrust", "https://zd.turktrust.com.tr/", 443)`.
  - A second choice, `turktrust-http` = `("turktrust", "http://zd.turktrust.com.tr/", 80)`,
    is accepted only together with `tsa.allow_plain_http: true` and a typed confirmation
    (`tsa_confirm_required`). Its form text says that the password then travels
    unencrypted.
  - The test of a new server is a probe stamp, as for Kamu SM. It is refused without both
    secrets (`tsa_not_ready`, `missing: ["turktrust.username", "turktrust.password"]`).
  - The root upload is the existing one. Its text says "TÜRKTRUST kök sertifikası" when the
    provider is `turktrust`.
- **Credit:**
  - The `Credit` task records `{"supported": false}` for `turktrust`.
  - The credit card shows the neutral text of §2.6.
  - `R20-credit` is never raised for a provider without credits.
- **Firewall:**
  - With `-StrictOutbound`, `Set-ArciFirewall.ps1` opens TCP 443 to the host of `[tsa] url`
    when the provider is `turktrust`, and to the hosts of `tsa.crl_urls`.
  - The proxy's host:port replaces them all when `[network] https_proxy` is set.
  - TCP 80 is opened instead of 443 with `tsa.allow_plain_http`.
- **Installer:**
  - The Java and Zamane-jar checks and next steps apply only to the provider `zamane`.
  - For `turktrust`, the next steps say where to enter the username and password (Ayarlar →
    Zaman damgası) and where to get TÜRKTRUST's root certificates.
  - The secrets are never asked at the console, and never written into a log.
- **Tests:**
  - A local HTTPS server with a test CA trusted only in the test checks Basic authentication,
    SHA-256 and SHA-512, the 401 path, the credit-word rejection, the proxy, and that the
    password is absent from every error text.
  - **No test calls the real TÜRKTRUST server.**
  - A manual checklist (`docs/turktrust.md` §3) is used with the owner's TÜRKTRUST test account
    on site.

### 2.5 Configuration keys (all new keys; W5-T adds them all to `config.py` in its first commit)

| Key | Type, default | Bounds |
|---|---|---|
| `tsa.renew_before_days` | int, 180 | 30–730 |
| `tsa.crl_urls` | list of http(s) URLs, `[]` | ≤ 20 |
| `tsa.crl_max_age_days` | int, 7 | 1–60 |
| `network.https_proxy` | str, `""` | `http://host:port` or empty |
| `tsa.allow_plain_http` | bool, false | only meaningful for `turktrust` |

Browser-editable: `tsa.renew_before_days`, `tsa.crl_urls` and `tsa.crl_max_age_days` in the
`tsa` section (value, re-authentication), and `network.https_proxy` in `general` (value,
re-authentication).

### 2.6 Fixed texts (TR first, EN beside; W5-C owns the API strings, W5-D the locale keys)

| Code | TR | EN |
|---|---|---|
| `provider_not_legal` (mock/test) | Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu damgaların yasal geçerliliği yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim sunucusunu seçin. | Time stamps come from a trial or test provider and have no legal validity. Choose the Kamu SM production server under Settings → Time stamping. |
| `credentials_in_clear` | TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve parola ağda açık metin olarak gider. Mümkünse HTTPS'e dönün. | The TÜRKTRUST connection uses unencrypted HTTP; the username and password cross the network in clear text. Switch back to HTTPS if you can. |
| `legal_root_missing` | Kök sertifika parmak izi tanımlı değil; hiçbir gün yasal nitelikli gösterilemez. | No legal root fingerprint is set; no day can be shown as legal grade. |
| `unstamped_days` | {count} kapanmış gün {hours} saatten uzun süredir damgalanmadı. | {count} closed day(s) have not been time-stamped for more than {hours} hours. |
| `renewal_due` | {count} damganın sertifikası {days} gün içinde sona eriyor; yeniden damgalama bekleniyor. | {count} stamp certificate(s) expire within {days} days; re-stamping is due. |
| `renewal_uncovered` | {count} damganın sertifikası yeniden damgalanmadan sona erdi; damgalar geçerliliğini korur, ancak uzun süreli kanıt için yeniden damgalama önerilir. | {count} stamp certificate(s) expired without re-stamping; the stamps stay valid, but re-stamping is recommended for long-term evidence. |
| `certificate_revoked` | Zaman damgası sertifikası iptal listesinde: {name}. Zaman damgası sağlayıcısıyla görüşün; hukuki inceleme önerilir. | A time-stamp certificate is on a revocation list: {name}. Contact the provider; legal review is recommended. |
| `crl_stale` | {days} günden yeni doğrulanmış bir iptal listesi (SİL) yok. | No verified revocation list newer than {days} days. |
| credit, no credit query | Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin. | This provider offers no credit query; watch the balance in the provider's portal. |
| `renewal-covered` | Bu damga, sertifikası sona ermeden yeniden damgalanmış: {renewal}. | Re-stamped before its certificate expired: {renewal}. |
| `renewal-due` | Damga sertifikası {date} tarihinde sona eriyor; yeniden damgalama bekleniyor. | The stamp certificate expires on {date}; re-stamping is due. |
| `renewal-late` | Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({date}). | Re-stamped after the old certificate had expired ({date}). |
| `renewal-uncovered` | Damga sertifikasının süresi {date} tarihinde doldu ve yeniden damgalama yok; damga, alındığı an için geçerlidir. | The stamp certificate expired on {date} with no re-stamping; the stamp is valid for the moment it was made. |
| `renewal-not-due` | Yeniden damgalama henüz gerekmiyor (sertifika {date} tarihine kadar geçerli). | Re-stamping is not due yet (certificate valid until {date}). |

Every banner text ends with the documentation reference ("ZAMAN-DAMGASI.md bölüm N"). No text
says "uyumlu", "garanti" or "compliant".

### 2.7 Docs (W5-F)

- **New:**
  - `docs/ZAMAN-DAMGASI.md` / `docs/TIME-STAMPING.md`: providers, legal grade, re-stamping,
    revocation lists, banner reasons;
  - `docs/turktrust.md`: T1–T5, the account steps, the manual checklist, "sahada doğrula".
- **Updated:**
  - KURULUM/INSTALL (TÜRKTRUST setup, proxy, strict outbound);
  - AYARLAR/SETTINGS (the new keys and the third server choice);
  - ILK-ADIMLAR/FIRST-STEPS §9 (the three alerts, after W5-E);
  - DENETCI §7 (the renewal codes);
  - LISANS/LICENSING (`/legal-status` and the two jobs are 5651 Temel);
  - CORRELATION (the jobs);
  - SECURITY (the TÜRKTRUST secrets and Basic auth over HTTPS only);
  - ASSUMPTIONS (T1–T5);
  - CHANGELOG.

---

## 3. Tests (the minimum; packages add more)

- **W5-A:**
  - Renewal record format against the frozen fixture.
  - A stored report token (`exports/reports/*.pdf.zd`) is renewed, and a package with that
    report ships the renewal.
  - Each status from a clock fixture (not_due, due, covered, late, uncovered).
  - A renewal-of-renewal chain, three deep.
  - A test-provider renewal never counts (D5).
  - At most one record per run.
  - A failed stamp leaves no record.
  - The standalone and core verifiers agree on a shared case table (rule 5).
  - `VERIFIER_VERSION` is "5".
  - The builder picks up a three-deep chain for one day.
- **W5-B:**
  - Fetch from a local HTTP server: a good list, a bad signature, an unknown issuer, a list
    too large, unreachable.
  - Storage names, and the index.
  - No file is ever deleted.
  - `verify_token(crls=)` gives the six codes, from the shared table with the standalone
    verifier.
  - `crl-revoked` fails `verify_day`.
  - The builder's D9 selection.
  - **K10 test:** no CRL URL literal exists in `core/src` (fails loudly if someone hard-codes
    one).
- **W5-T:**
  - The HTTPS test server cases of §2.4.
  - `http://` is refused for `turktrust` unless `tsa.allow_plain_http` is true. With it, the
    stamp works over HTTP and `credentials_in_clear` is reported.
  - `legal_grade` is True for `turktrust` on `zd.turktrust.com.tr` only (any other host:
    False). A day stamped by TÜRKTRUST is legal grade only with its root in
    `legal_root_fingerprints`.
  - **A test pins `TURKTRUST_LEGAL_GRADE is True`**, citing the owner's decision, so that any
    change to it is deliberate.
  - Secrets never appear in logs or errors (caplog).
  - Admin rules: the server choice, missing secrets, and the probe.
  - Firewall Pester test: the strict outbound host.
- **W5-C:**
  - `/legal-status` for each reason and level, for every role.
  - No subject data in the answer.
  - The Panel `blocking` field at 47 h and at 49 h.
  - The day detail additions.
  - Alert raise/clear for the three codes (after W5-E).
  - The credit card for `turktrust`.
- **W5-D:**
  - Banner unit tests (levels, hide-for-24h, localStorage failing).
  - Axe on the banner.
  - E2E: a danger banner on every page for an auditor.
  - The Panel blocking card.
  - The TÜRKTRUST settings form.
- **W5-E:**
  - Task XML for `Renew` and `CrlFetch` (`tests/test_windows_tasks.py` `EXPECTED`).
  - `TaskName` in the API and the web.
  - The ACL for `state\crl` (tasks Modify, API/Worker read).
  - The installer's next-steps text.
  - The alert constants and their ILK-ADIMLAR rows.
  - Captured samples.

---

## 4. Work packages

### 4.1 Branch, gate and ownership rules

As in Wave 4 §4.1 (`design/wave-4-contract.md:1255-1273`):

- Start from `wave-5`, open the pull request into `wave-5`, one owner per file.
- The gates of `CLAUDE.md` apply.
- Deviations go under "## Deviations" at the end of this contract.

### 4.2 Coupling points

- **Alert codes** (D14): only W5-E adds the whole-string constants and the ILK-ADIMLAR rows.
  Until then W5-C uses `"R20-renewal:"`-style prefixes.
- **New API area `legal-status`:**
  - W5-C writes it in `api/src/arcilogger_api/legalstatus/`, which `routers/` does not discover.
  - W5-E mounts it with a two-line module and adds the LISANS/LICENSING row.
- **New tasks:** W5-E adds `Renew.xml` and `CrlFetch.xml` and every pin of the task list
  (tests, API `TaskName`, web types).
- **`config.py`:** W5-T opens a first small pull request on day 1 with the five keys of §2.5
  (and their validation tests only). W5-A and W5-B start at once and merge `origin/wave-5` once
  it has landed.
- **`web/src/lib/legal-status-types.ts`:** W5-C opens a first small pull request on day 1. It
  carries the file verbatim from §2.3.1 and the matching Pydantic models. W5-D imports it.

### 4.3 File ownership (main files; the issues list every file)

| Package | Owns |
|---|---|
| W5-T | `core/src/arcilogger_core/config.py`, `tsa/__init__.py`, `tsa/turktrust.py` (new), `secretstore.py`, `cli.py` (`secret set` names only), `admin/rules.py`, `admin/contract.py`, `admin/probe.py`, `tsa/rfc3161.py` (proxy), `api/src/arcilogger_api/routers/admin_config.py` (TSA section fields), the credit task's provider check, `deploy/windows/scripts/Set-ArciFirewall.ps1`, `deploy/windows/installer/Install-Arcilogger.ps1` (TSA checks and next steps only; W5-E edits it after W5-T has merged), regenerated API contract and samples, their tests |
| W5-A | `core/src/arcilogger_core/renewal.py` (new), `cli.py`, `evidence/standalone/verify.py`, `tests/fixtures/vault-w5/`, `tools/dev/make_vault_fixture.py`, their tests |
| W5-B | `core/src/arcilogger_core/crl.py` (new), `tsa/verify.py`, `chain.py`, `evidence/builder.py` (D9 selection, and the report-token match of D3 in `_renewals`), `layout.py`, their tests. Exception: the `crl` subcommand's own hunk in `cli.py` (W5-T adds the `secret set` names and W5-A the `renew` subcommand there too; the hunks are additive, and the later pull request merges `origin/wave-5` and keeps all of them). |
| W5-C | `api/src/arcilogger_api/legalstatus/` (new), `web/src/lib/legal-status-types.ts` (day 1), `routers/dashboard.py`, `sealinfo.py`, `routers/seals.py`, `routers/credits.py`, `worker/src/arcilogger_worker/alerts/system.py` (prefix-only until W5-E), their tests |
| W5-D | `web/src/components/legal/` (new), `app-shell.tsx`, `pages/sealing/`, `pages/dashboard/`, the TSA settings form, locale areas, their tests |
| W5-E (late) | `deploy/windows/tasks/Renew.xml`, `CrlFetch.xml`, `tests/test_windows_tasks.py`, `ArciInstall.psm1` (ACL: `state\crl`, and LOCAL SERVICE read on `exports\reports` for the `Renew` task), `Install-Arcilogger.ps1` (next steps), `api/.../routers/legal_status.py` (mount), `system_health` task names, `web/src/lib/system-health-types.ts`, alert constants and `alerts/texts.py`, `web/scripts/capture_api_samples.py` |
| W5-F (late) | every document of §2.7 |

### 4.4 The packages and owners (proposal)

| Package | Owner | Size | Depends on |
|---|---|---|---|
| W5-T TÜRKTRUST provider | Arman 1 | ~3 days | — (first, owner priority) |
| W5-B Revocation lists | Tayfun | ~3.5 days | W5-T's day-1 config pull request |
| W5-C API/worker: legal status, Panel, alerts (prefix) | Arman 2 | ~3 days | — (reads files; uses fixtures until A/B land) |
| W5-D Web: banner, Panel card, TÜRKTRUST form | Gülşah | ~3 days | W5-C's day-1 types pull request |
| W5-A Re-stamping | Arman 1 (after W5-T) | ~4 days | W5-T's day-1 config pull request |
| W5-E Switch-on: tasks, ACL, mount, alerts, samples | Tayfun (after W5-B) | ~2 days | A, B, C |
| W5-F Docs | Saray | ~3 days; starts at once with `docs/turktrust.md` and ZAMAN-DAMGASI, the rest after W5-E | — |

### 4.5 Merge order

1. W5-T.
2. W5-B and W5-C, in either order.
3. W5-D.
4. W5-A.
5. W5-E.
6. W5-F.
7. `wave-5` → `main` (owner's go).

**Estimate:** about 2–2.5 weeks with the five sessions in parallel.

---

## 5. Addendum A (2026-11-02): 30-day database window, xz sealing, desktop status window

Owner decisions of 2026-11-02, after the storage review:

- **"son 30 olsun"**: the database keeps the last 30 days of log rows. Older days come back from
  the sealed vault on request ("arşivden yeniden yükle"). The vault itself still keeps at least
  730 days (`retention`, unchanged).
- **"xz sıkıştırması yapabilirsin"**: days sealed from now on are compressed with xz instead of
  gzip. A day already sealed is never re-encoded (raw logs are evidence, `CLAUDE.md:40-41`).
- **"o küçük masaüstü programı yap sen"**: a small status window on the server's desktop, so a
  person at the server sees whether ARCILOGGER runs without opening the web interface. The owner
  asked the manager to write it: W5-I is a manager package (recorded under "Deviations —
  contract").

Packages: **W5-G** (database window and archive reload, back end), **W5-J** (its web part),
**W5-H** (xz sealing), **W5-I** (desktop status window). Rule 0.1 item 2 ("nothing in Wave 5
deletes anything") does not apply to W5-G: dropping old **database** rows is its purpose.
The vault, the spool and every sealed file stay untouched. D16 ("no migration") does not apply
to W5-G either.

### 5.1 Facts (read on `wave-5` at `babe0c8`)

| Fact | Where |
|---|---|
| `db.retention_days` = 400 is today only the ingest's vault re-load horizon; no job deletes log rows | `db/src/arcilogger_db/settings.py:143`; `ingest/src/arcilogger_ingest/service.py:7`, `:570-590`; `docs/DATA-MODEL.md:302-308` |
| 16 time-series tables (`TIME_SERIES_MODELS`); plain PostgreSQL: monthly partitions `<table>_pYYYYMM` plus `<table>_default`; TimescaleDB: 1-day chunks | `db/src/arcilogger_db/models.py:2164-2184`; `db/src/arcilogger_db/partitions.py:1-60` |
| `drop_before` drops whole months and keeps the month of the cutoff; it also deletes `search_counts` and `source_clock_samples`; nothing calls it | `partitions.py:271-282` |
| Exactly-once loading by `ingest_offsets (source, day)`; a day is finished when the offset equals the manifest's `bytes_uncompressed` | `models.py:700-718`; `ingest/service.py:19-20` |
| Measured: 28.25 M `fg_traffic` rows filled 5.8 GB of rows (indexes extra) | `docs/INGEST.md:536-537` |
| Sealing: gzip, `seal.gzip_level` 9, `compress_deterministic` (no name, mtime 0); the manifest entry keeps `path` (relative to the vault, e.g. `2026/11/01/fw.log.gz`), `bytes`, `bytes_uncompressed`, `sha256`, `sha256_uncompressed`, `lines` | `core/src/arcilogger_core/config.py:96-97`; `core/src/arcilogger_core/seal.py:256-271`, `:334-345`, `:1068-1073` |
| Code that builds the `.log.gz` name itself or reads gzip | `seal.py:324`, `:1068`; `evidence/extract.py:23-32`; `evidence/standalone/verify.py:1926`, `:3213`, `:3258`; `evidence/instructions.py:141`, `:260`; `ingest/service.py:723`; `ingest/files.py:124-190`; `api/src/arcilogger_api/rawlines.py:200`, `:400` (gzip decompressor checkpoints); `routers/raw.py:6`; `routers/evidence.py:358`; `search/refs.py:103`; `reports/inputs.py:243`; `reports/texts.py:487-494`; `worker/src/arcilogger_worker/lookup/types.py:288-299`; `db/src/arcilogger_db/audit.py:361` (`audit.jsonl.gz`) |
| Status files: `state/collector-status.json`, `ingest-status.json`, `worker-status.json`, `alerts.jsonl`, `seal/<day>.json` | `core/src/arcilogger_core/layout.py:79-128`; `core/src/arcilogger_core/alerts.py:1-60` |
| The data folder is readable only by SYSTEM, Administrators and the service accounts; a non-elevated session cannot read `state\` | `deploy/windows/installer/ArciInstall.psm1:2053-2060` |
| `GET /api/v1/health` needs no sign-in and no database; the API listens on `[api] host:port` (127.0.0.1:8000) | `api/src/arcilogger_api/app.py:75-85`; `config.py:266-274` |
| The Sistem page judges services by the age of their status files (`SERVICE_FRESH`, 2 min) and tasks by the evidence they leave | `api/src/arcilogger_api/system_health/routes.py:1-20`, `:64`; `system_health/tasks.py:1-40` |
| Before an upgrade the installer pauses every `\ARCILOGGER\` task and waits until none runs | `deploy/windows/installer/Install-Arcilogger.ps1:976-997` |
| `https://localhost` is always one of the web server's site addresses | `Install-Arcilogger.ps1:1040-1043` |

### 5.2 Decisions

| # | Decision |
|---|---|
| G1 | **One window, the existing key.** `db.retention_days` becomes the database window. Its default changes to **30** (owner decision), with bounds **7** and up. The migration of W5-G changes a stored 400 (the untouched old default) to 30, raises a stored value below 7 to 7 and keeps every other value. `usage.retention_days` (rollups, 400) is unchanged. |
| G2 | **Daily partitions** on plain PostgreSQL from the first day of the month after the migration: `<table>_pYYYYMMDD`, created for the window and 7 days ahead. Existing monthly partitions are not split; a monthly partition goes once it ends on or before the cutoff, so up to one extra month stays during the change-over (documented). TimescaleDB: `drop_chunks` older than the cutoff. |
| G3 | **The job.** Worker job `db.window`, daily after 04:00. It drops everything older than the cutoff: the time-series partitions/chunks, older rows of the default partitions, `search_counts` and `source_clock_samples` (the existing `drop_before` rules). **Cutoff** = the earliest of (today − `db.retention_days`), the oldest day that is not sealed yet, and the oldest day of an active archive reload (G4). A day that could not come back from the vault is therefore never dropped. A day before the chain's first day (`seal.first_day`, `config.py:110`) counts as sealed. A failed run raises `R20-db-window` (medium, key `none`). |
| G4 | **Archive reload** ("arşivden yeniden yükle"). A new table `archive_reloads` holds: `id`, `requested_by`, `requested_at`, `reason` (`lookup`/`search`), `first_day`, `last_day`, `status` (`queued`, `loading`, `ready`, `failed`, `expired`), `bytes_total`, `bytes_done`, `error_code`, `ready_at`, `expires_at`. **ArciIngest does the loading**, because it is the only writer of log rows. For each `(source, day)` of the request's sealed days, one transaction deletes that source's rows of that `raw_day` (all 16 tables, and their `search_counts`) and resets its `ingest_offsets` row; the normal sealed-file reader then loads the day. Live data still comes first. Progress is Σ committed offset / Σ `bytes_uncompressed`. `ready` is set when every stream has finished. `expires_at` = `ready_at` + `db.reload_keep_days` (new, default 7, bounds 1–30); G3 then drops the days again. One request loads at a time; the others wait in `queued`. |
| G5 | **Limits and refusals** (HTTP 422, each with `code`, `message_tr`, `message_en`): `archive_reload_too_large` (more than 14 days in one request); `archive_day_not_in_vault` (a day with no sealed manifest: not sealed, or removed by retention); `archive_reload_disk_low` (the newest disk sample of the PostgreSQL data drive, `state/system-history.json`, minus the estimated load would leave less than 15 % free; no sample, no refusal). The estimate is Σ `bytes_uncompressed` × a database-bytes-per-raw-byte factor that W5-G measures and documents. |
| G6 | **Who, and the audit.** Starting a reload needs the permission of the function that asks: the 5651 lookup's permission for `lookup`, the search page's for `search`. Every request is written to the audit log (`archive.reload`: who, days, reason, the lookup id when there is one) **before** it is queued. A reload brings personal data back into the database for `db.reload_keep_days` days (KVKK). It is 5651 Temel: no licence gate. |
| G7 | **The lookup.** The lookup engine names the raw days it reads in one function in `worker/src/arcilogger_worker/lookup/`: the day of the asked time ± 1, plus the lease look-back of the DHCP rules it applies. When any of those days is outside the window and not loaded, the lookup API answers **409 `archive_reload_required`** with `first_day`, `last_day`, `bytes_total` and the estimate; it never answers "not found" for a day it could not see. **Equality test:** a lookup over dropped and reloaded days gives the same answer and the same raw-line references as the same lookup before the drop. |
| G8 | **The search.** A search whose range starts before the window answers with the rows inside the window plus `archived: {first_day, last_day} \| null`. When the whole range is outside the window, it answers 409 `archive_reload_required`. Evidence packages and official reports are unchanged: they read the vault. |
| G9 | **API** (new area `api/src/arcilogger_api/archive/`): `POST /archive-reloads` with `{reason, first_day, last_day, lookup_id?}` returns 202 and the reload; `GET /archive-reloads/{id}`; `GET /archive-reloads` (the active and recent ones). W5-G opens a day-1 pull request with `web/src/lib/archive-reload-types.ts` and the matching Pydantic models, as W5-C did. |
| G10 | **Web (W5-J).** The lookup and search pages show the `archive_reload_required` / `archived` card. The confirm dialog says the days come back for `db.reload_keep_days` days and that the request is audited. A progress bar polls every 2 s. When the reload is `ready`, the page runs the lookup or search again by itself. |
| H1 | **`seal.compression`** (TOML, `"xz"` default, or `"gzip"`). It applies to every file sealed for a day from then on: `<source>.log.xz` and `audit.jsonl.xz`. A sealed day is never re-encoded, so old `.gz` days stay as they are, forever. `seal.gzip_level` stays for gzip. |
| H2 | **Format: seekable xz.** The file is a concatenation of independent `.xz` streams. Each stream holds exactly 16 MiB (16,777,216 bytes) of uncompressed input, the last one less. Each uses `FORMAT_XZ`, `CHECK_CRC64` and preset `seal.xz_preset` (default 6, 0–9), with no stream padding. Streams are compressed on `seal.xz_threads` threads (default 2, 1–8) and written in order. The bytes do not depend on the thread count, and the same input, preset and liblzma give the same bytes. Readers find the stream of an uncompressed offset from the xz index in the stream footers; there is no sidecar file. Any xz tool decompresses the concatenation. |
| H3 | **One reader for both formats.** A new `core/src/arcilogger_core/sealedfile.py` provides: the sealed path of `(day, source)` from the manifest's `path` (both a `.gz` and an `.xz` for one source is the error `sealed_file_ambiguous`); a sequential reader; and a seekable reader (xz: by stream; gzip: the existing checkpoint approach). Every place in 5.1 uses it. A test fails if a `.log.gz` or `.log.xz` literal appears outside `sealedfile.py`, tests and docs. The manifest schema stays `arcilogger.manifest/1`: the `path` suffix tells the format. |
| H4 | **Standalone verifier:** it reads both formats with the standard library (`lzma`). `VERIFIER_VERSION` goes up by one from the value W5-H finds on `wave-5`; W5-A does the same, so after both have merged it is two higher than today. The package instructions (`evidence/instructions.py`) name 7-Zip and `xz -d`. |
| H5 | **Measure before choosing the defaults.** On load-generator data (1 M and 5 M lines), measure gzip -9 against xz presets 6 and 9 with 1 and 2 threads: ratio, sealing time, peak memory, and the time to show one line from the middle of a day. Report it in the pull request and in `docs/DATA-MODEL.md`. If a 5 GB day takes more than half of the Seal task's limit (PT2H), lower the default preset or raise the default threads, and say why with the numbers. |
| H6 | **"Sahada doğrula" X1:** 7-Zip decompresses a multi-stream file to the same SHA-256. The Windows CI test uses `7z.exe` when the runner has it and skips with the reason otherwise. |
| I1 | **`arcilogger_core.desktop`**, run as `pythonw.exe -I -m arcilogger_core.desktop` (`--self-test` as in the Lisans Yöneticisi). tkinter + ttk only. It is **read-only**: it has no button that changes anything (no service start or stop), only "Web arayüzünü aç" (`https://localhost/`) and "Yenile". |
| I2 | **It reads:** <ul><li>the state of the six Windows services (query only);</li><li>`GET /api/v1/health` on the loopback address (2 s timeout);</li><li>the status files, judged by the Sistem page's rules (`SERVICE_FRESH`);</li><li>the newest `state/seal/<day>.json`;</li><li>the evidence-path alerts of the last 24 hours (`state/alerts.jsonl`, last 1 MiB);</li><li>the free space of the data drive.</li></ul>It uses no database, no network beyond loopback and no telemetry, and it writes nothing. Every 10 s; a file it cannot read shows "bilinmiyor / unknown". |
| I3 | **Start.** A logon task `\ARCILOGGER\Desktop\StatusWindow` (`deploy/windows/desktop/StatusWindow.xml`) runs for members of Administrators: group principal S-1-5-32-544, highest run level, no time limit, one instance per session. The highest run level lets it read `state\` with no UAC prompt. `Register-ArciTasks.ps1` registers it. A Start-menu shortcut "ARCILOGGER Durum" starts it by hand; started without administrator rights, it starts itself again elevated (UAC). |
| I4 | **The installer** stops the window before an upgrade or uninstall: `Stop-ScheduledTask`, then ends the processes that run `arcilogger_core.desktop` from the program folder. On uninstall it removes `\ARCILOGGER\Desktop\` and the shortcut. |
| I5 | **Levels:** `ok`, `warn` and `critical`, shown in the window's title and icon. On a rise to `critical` the taskbar button flashes; there is no pop-up that takes the focus and no sound. TR first, EN beside. |

### 5.3 New and changed keys

| Key | Where | Default | Bounds |
|---|---|---|---|
| `db.retention_days` (changed) | database setting | **30** (was 400) | 7 – |
| `db.reload_keep_days` | database setting | 7 | 1–30 |
| `seal.compression` | TOML `[seal]` | `"xz"` | `gzip`, `xz` |
| `seal.xz_preset` | TOML `[seal]` | 6 | 0–9 |
| `seal.xz_threads` | TOML `[seal]` | 2 | 1–8 |

### 5.4 Tests (the minimum)

- **W5-G:**
  - Daily partitions, and the change-over from monthly partitions.
  - The cutoff (G3): an unsealed day and an active reload are never dropped.
  - TimescaleDB, in the Linux TimescaleDB job.
  - The G7 equality test.
  - A reload run twice gives the same rows and the same `search_counts`.
  - The three refusals of G5.
  - The audit entry is written before the request is queued.
  - Expiry after `db.reload_keep_days`.
  - After a database loss, ingest reloads only the window.
  - The G1 migration (400 → 30, < 7 → 7, other values kept).
  - The 409 of the lookup and the search's `archived`.
  - Captured samples.
- **W5-J:** the card and the confirm dialog; progress; the automatic re-run on `ready`; each refusal text; axe; e2e with mocks from the samples.
- **W5-H:**
  - An xz round trip keeps `sha256_uncompressed` equal to the spool's.
  - The bytes are the same for 1 and 4 threads.
  - The stream boundaries.
  - Showing one line decompresses one stream.
  - A chain of gzip and xz days verifies in both verifiers (shared table).
  - Sealing with xz leaves old `.gz` days untouched (same SHA-256, same mtime).
  - The literal test of H3.
  - `sealed_file_ambiguous`.
  - A damaged xz gives `raw_unreadable` in the API and a failure in the verifiers.
  - 7-Zip (H6).
- **W5-I:**
  - Levels from snapshot fixtures.
  - An unreadable file gives "bilinmiyor".
  - The non-Windows service-query stub.
  - `--self-test` under `xvfb-run`.
  - The task XML is pinned: principal, run level, trigger, command and time limit.
  - `Register-ArciTasks.ps1` registers the desktop folder.
  - The installer stops the window before it waits for the tasks.

### 5.5 Ownership, owners, order

| Package | Owner | Owns | Depends on |
|---|---|---|---|
| W5-G | Tayfun (before W5-E, which waits on A and C) | `db/src/arcilogger_db/partitions.py`, `settings.py` (the two keys), a new migration, `models.py` (`ArchiveReload`), the job module and its line in `worker/src/arcilogger_worker/service.py`, `ingest/src/arcilogger_ingest/service.py` (reload streams, horizon), `api/src/arcilogger_api/archive/` (new), the 409/`archived` hunks of `routers/lookup.py` and `routers/search.py`, the lookup-days function (G7), `web/src/lib/archive-reload-types.ts` (day 1), regenerated contract and samples, `docs/DATA-MODEL.md` (retention), `docs/legal/saklama-imha-politikasi-taslak.md`, the AYARLAR/SETTINGS rows of its keys, tests | — |
| W5-J | Gülşah (after W5-D) | `web/src/components/archive/` (new), the hunks of `web/src/pages/lookup/` and `web/src/pages/search/`, locale keys, e2e mocks, tests | W5-G's day-1 types |
| W5-H | Saray (W5-F waits on the other packages) | `core/src/arcilogger_core/sealedfile.py` (new), `seal.py`, `config.py` (`[seal]` keys), `evidence/extract.py`, `evidence/instructions.py`, the decompression and path hunks of `evidence/standalone/verify.py`, `ingest/src/arcilogger_ingest/files.py` and the vault-path line of `ingest/service.py`, `api/src/arcilogger_api/rawlines.py`, `routers/raw.py`, `routers/evidence.py`, `search/refs.py`, `reports/inputs.py`, `reports/texts.py`, `worker/src/arcilogger_worker/lookup/types.py`, `db/src/arcilogger_db/audit.py`, `tests/fixtures/vault-xz/` (new), DENETCI/AUDITOR (decompressing xz), the vault lines of `docs/DATA-MODEL.md`, tests | — |
| W5-I | Manager (owner exception) | `core/src/arcilogger_core/desktop/` (new), `core/tests/test_desktop_*.py`, `deploy/windows/desktop/` (new), `tests/test_desktop_task.py` (new), the desktop hunk of `Register-ArciTasks.ps1`, the copy line of `Build-ArciBundle.ps1`, the stop/remove/shortcut hunks of `Install-Arcilogger.ps1`, the KURULUM/INSTALL section "Durum penceresi / Status window" | — |

Shared files: W5-G and W5-H both touch `ingest/service.py` (W5-H only its vault-path line). W5-H
and W5-A both touch `standalone/verify.py` (W5-H only decompression and paths). The later pull
request merges `origin/wave-5` and keeps both sides.

**Merge order:** W5-I at any time; W5-H at any time (before or after W5-A); W5-G at any time;
W5-J after W5-G and W5-D. All before W5-F's last part, so the docs describe the final state.

---

## Manager clarifications (2026-11-02, answers on #101 and #106)

These change the contract text above. Where a section says otherwise, the clarification wins.

**Mounting and API shapes**

- **C1 – Mounting moves to W5-C.**
  - W5-C's main pull request mounts `/legal-status` itself: a two-line `api/src/arcilogger_api/routers/legal_status.py`, the regenerated contract and samples, and the LISANS/LICENSING row (5651 Temel, a granted exception in W5-F's docs).
  - It also adds `legal-status-types.ts` to the `TYPES_FILES` lists of `web/src/lib/api-types.test.ts` and `api-samples.test.ts`.
  - W5-E no longer mounts it (§4.2, §4.3).
  - Reason: Wave 5 has no parallel wave to protect, and W5-D's `api-types.test.ts` needs the path in the contract.
- **C2 – Each backend package updates the web mirror of the API shape it changes**, since the web tests compare those files with OpenAPI:
  - W5-C: `web/src/lib/api-types.ts` (the named `DashboardSeal.blocking` interface) and `web/src/lib/seal-types.ts` (`renewal`, `revocation`);
  - W5-T: `web/src/lib/settings-types.ts` (TÜRKTRUST server choices, body fields, secrets).

  W5-D reads the new fields through small interim accessors in its own files until those land.
- **C3 – `GET /credits` gains `supported: boolean`** (false for a provider without a credit query, today `turktrust`). W5-C owns it. The web shows the §2.6 credit text when `supported === false`; it never tests the provider name.
- **C4 – The day detail's `renewal.renewed_at` and `revocation.level`** are as in §2.3.1 above. The web maps `fail` to danger and `warn` to warning, and `info`/`ok` to neutral/ok tones.

**TÜRKTRUST settings**

- **C5 – TSA section fields:**
  - The body fields are `turktrust_username`, `turktrust_password` and `allow_plain_http` (W5-T).
  - The plain-HTTP choice `turktrust-http` reuses the existing `confirm: true` of `tsa_confirm_required`.
  - The form asks the administrator to type **`HTTP`** before the button is enabled.
  - W5-T updates `web/e2e/mock-admin.ts` (`tsa()`, `REQUEST_BODIES`) with its schema; W5-D's TÜRKTRUST end-to-end tests follow it.
  - The §2.5 keys are browser-editable:
    - W5-T makes them editable in `admin/rules.py` (`tsa` section: `tsa.renew_before_days`, `tsa.crl_urls`, `tsa.crl_max_age_days`; `general` section: `network.https_proxy`; all with re-authentication);
    - W5-D adds their form fields.

**Revocation lists**

- **C6 – Hold, then revocation.** A revocation at or before the stamp time outranks an earlier hold (`crl-revoked`, D10).
  - W5-A changes the standalone `check_revocation`, and also fixes its `TypeError` when a hold and a revocation share a date (a missing reason sorts first, as in the core). W5-B changes the core.
  - The shared case table gets both cases.
- **C7 – CRL storage name** gets a content hash: `state/crl/<issuer-sha256-12>-<thisUpdate>-<sha256-8>.crl`, so two different lists with the same `thisUpdate` are both kept.
  - A delta CRL (DeltaCRLIndicator) is not stored. It is recorded in `failures` with `delta_not_supported`, because neither verifier applies delta lists.
- **C8 – Packages ship, besides the D9 subset, every stored list that has an entry for a packaged chain certificate.** A package and `verify_day` then see the same revocations.
- **C9 – Unblocking W5-B.** W5-B may merge before W5-T, with its `crl_options` adapter (`getattr` with the §2.5 defaults). W5-T replaces the adapter with plain attributes when its config lands: a granted one-hunk exception in `crl.py`.
- **C10 – `config.py` validation (W5-T):**
  - `tsa.crl_urls` refuses URLs with user information;
  - `network.https_proxy` requires `http://host:port` with a numeric port.

**Other owners and texts**

- **C11 – `api/src/arcilogger_api/issues.py`** belongs to W5-C for the Turkish patterns of the new day texts (the four patterns listed on #106).
- **C12 – Monitors read the state files, not exit codes.** W5-C and W5-E judge `CrlFetch` from `state/crl/index.json` (`failures`, the newest `fetched_at`), never from its exit code alone.
- **C13 – Texts and labels:**
  - `certificate_revoked` says "Zaman damgası sağlayıcısıyla görüşün" (§2.6).
  - The Ayarlar label "Kamu SM" becomes "Zaman damgası"; the route stays `/settings/kamusm`.
  - Sealing texts that apply to both providers become provider-neutral; texts about the Zamane client or Kamu SM's credit stay Kamu SM-specific.
  - The Panel blocking card is `role="status"`.
  - The web appends the "ZAMAN-DAMGASI.md bölüm N" reference from #105's numbering.
  - A banner hidden for 24 h comes back at once when a new reason appears.
- **C14 – A stored list that no longer matches its index entry** is recorded in `index.json` `failures` with the error word **`stored_file_changed`**. `crl fetch` exits 1. `verify_day` gives the warning "revocation list {file} does not match its index entry" instead of dropping the list silently. The file is never deleted or overwritten: it is evidence of tampering.
- **C15 – `crl-revoked` makes the day not legal grade.** A day whose chain is revoked at or before the stamp gets `legal_grade = false` everywhere: the day check, the API, reports and packages. The rule-4 test is "claim ∧ verified ∧ root listed ∧ not revoked".
- **C16 – Retention and a revoked day.** Retention keeps stopping at a day that fails verification, now including `crl-revoked`. That is the safe side for evidence, and `R20-retention` names the day. Whether such a day is deleted is a decision for people (legal and KVKK review), never the job's.
- **C17 – Scoped and indirect lists.** Neither verifier honours an issuing distribution point's scope or indirect-list (`certificateIssuer`) extensions in Wave 5. This is a known limit, listed in ASSUMPTIONS ("sahada doğrula") and kept as a follow-up in #40.
- **C18 – An evidence package never ships a `crl-revoked` day.** This is the rule that already refuses a stamp that does not verify.
  - The builder refuses such a day **early**, in `_collect_day`, before writing any part of the ZIP. Its TR/EN message names the day and the revoked certificate, and says that the decision belongs to people (legal review).
  - The 5651 lookup itself keeps working on the database.
  - Days with `crl-revoked-later` or `crl-hold` are packaged as usual, with their warnings.
- **C19 – Callers that verify a token without lists:**
  - `arcilogger-core verify-token` passes the stored lists (W5-B).
  - `api/src/arcilogger_api/sealinfo.py` passes them too (W5-C).
  - The legal grade written at seal time (`seal.py`) stays without lists: no list issued after a fresh stamp exists yet, and the day check later re-judges it with lists.
- **C20 – A stored list whose file is gone** counts like a changed one (C14). The run records `stored_file_changed` for that URL and exits 1, even when it stores the same bytes again. A deletion is something a person must see.

## Owner questions

All Wave 5 questions are answered (2026-11-02).

| # | Question | Answer |
|---|---|---|
| Q1 | Do TÜRKTRUST production stamps count as legal grade, like Kamu SM? | **Yes** (owner: "yasal"). `TURKTRUST_LEGAL_GRADE = True`; the rule-4 test still applies. The docs recommend legal review. |
| Q2 | TÜRKTRUST over port 80 (HTTP) as well? | **Whatever TÜRKTRUST needs** (owner). HTTPS by default; plain HTTP as an explicit administrator choice with a warning (`credentials_in_clear`). |
| Q3 | Renew report tokens as well? | **Yes, the stored ones** (owner: "ne daha iyi ve güvenliyse"): `exports/reports/*.pdf.zd` (D3). |
| Q4 | Revocation lists are never deleted. Limit them? | **Keep all** (default). `crl status` reports their count and size. Revisit after the first year on site. |
| Q5 | Renewal window? | **180 days** (default; configurable, 30–730). |

## Deviations

## Deviations — contract (2026-11-02)

- **SPEC §21** named Kamu SM as the only provider for production stamps, with a red banner for
  any other. By the owner's decision of 2026-11-02, the TÜRKTRUST production server
  (`zd.turktrust.com.tr`) is a second legal-grade provider (D12).
  - The banner rule stays for mock, rfc3161 and test servers.
  - No site setting can add another legal provider.

- **W5-I is written by the manager** (owner decision 2026-11-02, "o küçük masaüstü programı yap sen"). It is the one Wave 5 package the manager implements; it is reviewed like any other pull request and touches only the files of §5.5.
- **D16 and rule 0.1 item 2 do not bind W5-G** (owner decision "son 30 olsun"): it adds a migration and drops database rows older than the window. The vault is untouched.

## Deviations — W5-B (2026-11-02)

- **W5-B-1: superseded by C7.** `name_taken` is gone: the stored name carries the file's
  SHA-256 (`<issuer-sha256-12>-<thisUpdate>-<sha256-8>.crl`), a delta list is recorded as
  `delta_not_supported`. `load_stored` reads only names of this form (no site has a list stored
  under the earlier one: W5-B was not released with it); a delta list found in `state/crl` is
  neither used by `verify_day` nor shipped. A stored file whose bytes no longer match its index
  entry or its name (changed after it was stored), or that is gone, is never overwritten or
  deleted: after the URLs, every `crl fetch` hashes the file of every index entry (not only the
  lists served again, so also an older list whose URL serves a newer one by now), records the
  entry's URL in `failures` with `stored_file_changed` (C14) and exits 1; the other URLs still
  run, the changed file's index entry stays. A list served again whose name holds other bytes
  gives the same failure, whether or not the index still has the entry. The files are hashed,
  not parsed: in a fresh process (the `CrlFetch` task) each run reads every stored file once,
  about 3 ms per MB here (warm disk cache), so a thousand 1 MB lists add about 3 s. An entry
  whose unchanged bytes are no usable list (only an index edited by hand can name one) is not a
  fetch failure; `verify_day` names it (W5-B-6). Tests: `core/tests/test_crl.py`.
- **W5-B-2: a lock file `state/crl/.fetch.lock`.** Two runs (the `CrlFetch` task and a manual
  `crl fetch`) can overlap; the lock keeps the index from losing entries. The file stays, empty,
  and is never deleted; the builder and `crl status` ignore it. A held lock exits 1 and is not
  recorded in the index.
- **W5-B-3: stricter trust than D7/D8's wording.** Only certificates that chain to a pinned root
  give distribution points; a CRL issuer whose keyUsage lacks `cRLSign` is refused; a list
  signed with an algorithm the standalone verifier cannot check is neither stored nor used
  (rule 5); a URL with a user name or password is recorded as `unreachable` with the credentials
  hidden, never logged or printed.
- **W5-B-4: `tsa.timeout_seconds` bounds each URL's whole exchange**, not only each network
  wait: connection, status line and headers, redirects and body. Once the time is over, every
  connection of the exchange is shut down at the socket and the URL fails as `unreachable`, so
  a server that trickles bytes (headers or body) cannot hold the task. Only name resolution
  (the system's own limit), a TCP connection attempt already under way (at most
  `tsa.timeout_seconds` per address) and a TLS handshake already under way can take longer:
  the handshake is not cut (its socket belongs to the TLS module until it ends) but bounded by
  Python's own handshake timeout (one `tsa.timeout_seconds`), so one that starts just before
  the deadline can end up to about twice `tsa.timeout_seconds` after the start.
- **W5-B-5: `crl fetch` exit codes beyond §2.2:** 2 also for an invalid `network.https_proxy`;
  1 also for an unreadable index or stored file, a held lock, or a stored file that is changed
  or gone (`stored_file_changed`, C14; recorded like every other failure, also for a list no
  URL serves any more, W5-B-1). `crl status`
  prints JSON (`index`, `files`,
  `bytes`, `size`) and has no `--json` flag.
- **W5-B-6: day-check texts.** `crl-revoked` fails the day with the §2.6 `certificate_revoked`
  English text (without the revocation date); `crl-revoked-later`, `crl-hold` and an unreadable
  index give new English warnings in `chain.py`'s style. Each certificate of the chain gives its
  own text, as the standalone verifier lists each one (not only the most severe). C14's
  "revocation list {file} does not match its index entry" is a warning on every day; it also
  names an entry whose file is missing or holds no usable list. Their Turkish texts belong in
  `api/src/arcilogger_api/issues.py` (not W5-B's file; listed in the pull request).
- **W5-B-7: `NO_CRL_NOTE`'s text** (the constant stays): it said ARCILOGGER does not download
  revocation lists yet. It now says, TR / EN, that the CRL folder the package was built from
  (`state/crl` by default, or `--crl-dir`) holds no verified list for these days, where the URLs
  come from, and that Kamu SM's are to be confirmed on site (K10).
- **W5-B-8: the config adapter.** `crl.crl_options` reads `tsa.crl_urls`,
  `tsa.crl_max_age_days` and `network.https_proxy` with `getattr` and the §2.5 defaults until
  W5-T's day-1 pull request lands; then it becomes plain attribute access.
- **W5-B-9: an interim field of the shared revocation table (C6), for W5-A to clear.** The core
  applies C6 now; the standalone verifier follows with W5-A (merge order W5-B, then W5-A). The
  C6 cases are already in `core/tests/revocation_cases.py` (W5-A does not add them again). Five
  cases expect `crl-revoked` and carry `standalone_before_c6`, what the standalone verifier
  gives today:
  - `crl-hold`: a hold, then a revocation before the stamp; the same at the stamp time; a hold
    and a privilegeWithdrawn revocation of the same date;
  - `TypeError`: a hold and a revocation without a reason of the same date; two revocations of
    the same date, one without a reason and one for key compromise.

  `test_revocation.py` asserts exactly that, so it fails once W5-A changes `check_revocation`.
  W5-A then removes the value from each case and, with the last one, the dataclass field, the
  module-docstring paragraph and the interim branch of `test_revocation.py`, and this entry:
  both verifiers must agree on every case.
- **W5-B-10: C8's "packaged chain certificate"** is a certificate of a packaged day token's
  verified chain below its root (the TSA certificate and its intermediates; the chain
  `verify_token` checks, `tsa.verify.token_chain`). "Has an entry" means any entry for its
  serial, removeFromCRL too (which the revocation check then ignores), on a list signed by
  that certificate's issuer. The builder reads the lists from the folder `state/crl`,
  `verify_day` from its index (`load_stored`), so they see the same revocations as long as the
  folder and the index agree, as `crl fetch` keeps them. Tests:
  `core/tests/test_evidence_crl.py` (two- and three-level chains).
- **W5-B-11: issuers from every day token, URLs from the newest one of each provider.** Every
  CA certificate of any day token that chains to a pinned root may sign a list (each distinct
  certificate loaded once per run), so a list of an intermediate the provider no longer uses
  still verifies instead of `unknown_issuer`. The distribution points stay those of D7 (pinned
  roots and the newest token of each provider): an old CA's address that stops answering does
  not fail every run. Tests: `core/tests/test_crl.py`.
- **W5-B-12: one list per PEM file.** `parse_crl` refuses PEM data (any `-----BEGIN`) unless it
  holds exactly one `X509 CRL` block (`not_a_crl` in a fetch; left out by the builder): the
  standalone verifier reads every block of a file, the index describes one list per file.
- **W5-B-13: each stored file is parsed once per process** (`crl.read_crl_file`, keyed by the
  path and the file's size, mtime, ctime and inode), for `load_stored` and the builder alike.
  The DER is kept once (a DER file's own bytes); the builder reads only the files it ships. A
  stored file that cannot be read (permissions), like an unreadable `state/crl`, gives
  "revocation not checked" (a `CrlError`), never a crash and never a check without that list.
  Known limits, kept as a follow-up:
  - Memory is not bounded: every list a process has read stays parsed for the life of the
    process (the API and the worker too), its DER and its entries. Measured: a 1.06 MB list of
    20,000 entries takes about 1.3 s to parse and keeps about 3.4 MB of entries beside its DER
    (about 50 MB at the peak of the parse). Lazy entries would leave a list from `--crl-dir`
    unchecked until a day check needs it, so they are not used.
  - On Windows, `st_ctime` is the creation time: a rewrite in place that keeps the size and
    sets the old mtime back (`os.utime`) is not seen by a long-running process, which keeps
    the list as first read (the stored one, so no revocation is lost) and gives no C14 warning
    until it restarts. `crl fetch` runs as a fresh process and hashes every file (W5-B-1), so
    the change is still recorded in `failures` on its next run.
- **W5-B-14: C15 in the core.** `tsa.verify.confirmed_legal_grade` adds "not `crl-revoked`"
  (from `details["crl"]`, so only a result checked with lists can say revoked); `verify_day`
  and the builder (which checks the days against its CRL folder) pass the lists. Callers that
  verify without lists cannot see a revocation; they are listed on the pull request.
- **W5-B-15: C18–C20.** The builder refuses a `crl-revoked` day in `_collect_day`, before any
  part of the package is written, with a TR / EN message naming the day and the certificate
  (C18). `arcilogger-core tsa-test` (the only token check of the command line; there is no
  separate `verify-token` command) verifies its stamp with the stored lists, and an unreadable
  CRL folder leaves it unchecked with a warning (C19). A known list whose file is gone is stored
  again and recorded as `stored_file_changed`, exit 1 (C20).

## Deviations — W5-C (2026-11-02)

- **W5-C-1: the reader of `state/renewal.json`.** §2.1.2 fixes the per-token fields, not the
  top level. The reader (`alerts.system.read_stamp_renewals`, shared by `/legal-status`, the
  day page and the alerts) takes `tokens` of an object or a plain list, ignores unknown keys,
  leaves out an entry with an unknown `status` or an unreadable `cert_not_after`, and counts
  from each token's `status`, never from a totals object. A missing file is "no report yet"; an
  unreadable one is unknown and decides nothing (no reason, no alert raised or resolved,
  `renewal: null` and a page problem on the day page). An entry's `renewed_at` is taken when
  the writer gives one; otherwise the day page reads the genTime of the renewal token beside
  the `covered_by` record. `path` is vault-relative; a leading `vault/` and a `.zd` suffix of
  `covered_by` are tolerated.
- **W5-C-2: the day page's `revocation` texts** for the codes that §2.6 does not word
  (`crl-revoked-later`, `crl-hold`, `crl-none`, `crl-stale`, `crl-ok`) are W5-C's, after the
  standalone verifier's (`legalstatus/texts.py` `REVOCATION`); `crl-revoked` uses
  `certificate_revoked`. `level` is the verifier's own `crl_level`, else the D10 level of the
  code. The day page checks the token with the stored lists (`crl.load_stored`, read once
  per request, C19), so `revocation` is `crl-none` before the first `crl fetch`. It is `null`
  only when `state/crl` cannot be read, and the page's `problems` then say "revocation not
  checked"; a list whose file no longer matches its index entry is named there too (C14).
- **W5-C-3: `crl_stale`** is measured on each list's `this_update` ("newer than": a list exactly
  `tsa.crl_max_age_days` old is stale) for each issuer in use, an issuer without any stored list
  included. "Issuer in use" is a CA certificate of the newest sealed day's verified chain
  (`token_chain` against the pinned roots, the TSA certificate left out), by its SHA-256
  fingerprint. So an old provider's lists stop mattering, and before the first sealed day, or
  when the newest token reaches no pinned root, nothing is in use. Only the lists that
  `crl.load_stored` accepts count: a list whose file was changed or deleted is no verified list
  (C14, C20). When `state/crl` cannot be read, every issuer in use is stale; that is never a
  server error. `since` is the earliest `this_update + max_age` of the stale issuers that have a
  list, else `null`; `link` is `settings-tsa` (C5). `R20-crl` uses the same rule; its details
  add `changed_files` (the lists left out under C14). `certificate_revoked` and `R20-cert` also
  check the newest sealed day's chain against the stored lists at its genTime (`crl-revoked`
  only; `since` is the revocation date), beside the scheduled verifications' day errors.
- **W5-C-4: `credentials_in_clear`** holds for `turktrust` when the `tsa.url` scheme is `http`
  (§2.4 "with plain HTTP"). `tsa.allow_plain_http` with an `https://` URL raises nothing: the
  §2.6 text states that the connection is unencrypted. Until W5-T's configuration lands,
  `turktrust` is never a legal-grade claim, so it also gives `provider_not_legal` and the label
  "Test sunucusu".
- **W5-C-5: `DashboardSeal.blocking`** names the oldest closed day without a token more than
  `seal.late_after_hours` after its end; `hours_late` is the whole hours since that day's end
  (49 at 49 h 30 min). It uses the same rule as `unstamped_days` (D11); a spool that cannot be
  listed gives `null`, and a stamped day whose Seal task has not finished does not block.
- **W5-C-6: the API texts carry no ZAMAN-DAMGASI reference.** C13 has the web append "ZAMAN-
  DAMGASI.md bölüm N"; the `message_tr`/`message_en` of `/legal-status` are the §2.6 texts
  alone. `certificate_revoked`'s `{name}` is the certificate subject's CN, else its whole
  RFC 4514 text.
- **W5-C-7: the three alerts are prepared, not switched on.** `check_stamp_renewal`,
  `check_stamp_cert` and `check_stamp_crl` are in `alerts.system.LEGAL_CHECKS`, not in
  `CHECKS`: W5-E appends them with their texts and first-steps guide rows (§2.3.1). Each code
  is written only inside its dedup key (`R20-renewal:none` and so on) and the rule is taken from
  the key (D14); `worker/tests/test_alerts_legal.py::test_no_whole_string_constant_for_the_wave5_alert_codes`
  fails on a whole-string constant, and W5-E deletes or inverts it. The findings carry counts
  only. R20-cert's "expires within 30 days and is uncovered" is read as a `due` token whose
  certificate expires within 30 days. "The `Renew` task failed twice in a row" is left to W5-E:
  no state file records Renew runs yet (C12). `R20-crl` is judged from `state/crl` only (the
  stored lists and `index.json`, never an exit code); its details give the stale issuers, the
  failures, the changed files and the newest `fetched_at`.
- **W5-C-8: `Exclude<A, B>` in the web type tests.** `LegalReason.level` is
  `Exclude<LegalLevel, 'ok'>` (§2.3.1, verbatim), so `tsType()` of `api-types.test.ts` and
  `check()` of `api-samples.test.ts` learned that form.
- **W5-C-9: `SealDayDetail.renewal` and `revocation` are optional** in `seal-types.ts`, as
  W5-D asked and like `tsa_cert_not_after?`; the API always sends both keys (`null` when there
  is nothing). `Credits.supported` is required in `api-types.ts`, because the schema requires
  it and `api-types.test.ts` compares requiredness. It is false for a provider without a credit
  query and also when the newest `state/credit.jsonl` line of the configured provider says
  `"supported": false`.
- **W5-C-10: day-check Turkish texts beyond #106's four** in `issues.py`: C14's "revocation
  list {file} does not match its index entry" and the builder's "CRL file {file} changed while
  the package was built". `{when}` is shown in site time (`format_datetime`).
- **W5-C-11: damaged state files are read as unknown**, never a server error of the banner (the
  60 s cache keeps no failed answer). A scheduled verification whose `checks` is not a list is
  skipped. A re-stamping entry whose time is at the edge of the calendar is left out. JSON
  nested too deeply to parse counts as unreadable. A seal state that cannot be parsed makes the
  unstamped days unknown.
