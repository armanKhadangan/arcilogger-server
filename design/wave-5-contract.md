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

## 6. Addendum B (2026-11-05): breaks and per-day work hours, one-minute usage refresh, Canlı Durum

Owner decisions of 2026-11-05:

- **Breaks in the work hours.** The owner asked where the company's work hours and breaks are
  entered: "çalışanların aralardaki instagram kullanımlarını mesai saati içi kullanıma karıştırmak
  istemiyorum … yoksa onu da wave 5 ile birlikte yap".
- **Usage data every minute.** "kullanım ve tüm verilerin güncelleme hızı … mümkünse onu dakikada
  bire çek".
- **A live view of the network.** The owner asked for live download and upload speed and the
  computers sending data at the moment. He chose:
  - **"Loglar + SNMP"**: per-computer activity from the logs, and the internet line's speed read
    from the firewall's interface counters with SNMPv3, read only;
  - **"Wave 5'e ekle"**.

  This is the first time ARCILOGGER sends a request to a device. Until now it only received
  syslog. N3 limits that to the configured log sources.

Packages:
- **W5-K:** breaks and per-day hours, back end.
- **W5-L:** one-minute usage refresh.
- **W5-M:** Canlı Durum from the logs, back end.
- **W5-N:** the internet line's speed over SNMP, back end.
- **W5-P:** every web part of K, L, M and N.

W5-F (docs) gets the additions of 6.6.

### 6.1 Facts (read on `wave-5` at `76ee6bb`)

| Fact | Where |
|---|---|
| Work hours are one database setting, `usage.work_hours` = `{"days": [1..5], "start": "08:00", "end": "18:00"}`. The validator accepts exactly these three fields. A night shift (start ≥ end) is refused. | `db/src/arcilogger_db/settings.py:215-217`, `:1086-1115`; texts `:394-409`; `settings_meta.py:754-762` |
| It is edited in the browser under Ayarlar → Uygulama ayarları ("Mesai saatleri"), with the holidays (`usage.holidays`). | `web/src/pages/settings/setting-values.ts:34-35`; `web/src/pages/settings/application-page.tsx` |
| Every minute of a site day is "off hours" or not. There is no third class. | `worker/src/arcilogger_worker/usage/config.py:48-70`, `:164-177`; `usage/engine.py:217`, `:255-257` |
| The seven rollup tables carry `off_hours boolean` in their primary keys. | `db/src/arcilogger_db/models.py:1451-1650` (`UsageDaily` … `UsageUrl`); migration `v0010_usage.py` |
| The API's `hours` filter is `all \| work \| off`. Totals carry `off_hours_active_seconds`. | `worker/src/arcilogger_worker/usage/query.py:37`, `:271-336`; `api/src/arcilogger_api/routers/usage.py:221-230` |
| The PDF and HTML reports print the off-hours time. | `worker/src/arcilogger_worker/reports/html.py:219`, `:324`; `reports/pdf.py:247`, `:468`, `:517` |
| A change of the work hours rebuilds the last `usage.auto_rebuild_days` days by itself: they are part of the rollup's fingerprint. | `usage/config.py:179-196`; `usage/jobs.py:155-176` |
| **Today's usage is recomputed every 5 minutes, not every hour.** The Kullanım page polls every 5 minutes; the Panel polls every 30 s. | `worker/src/arcilogger_worker/service.py:220`, `:419`; `usage/jobs.py:138-141`; `web/src/lib/api-hooks.ts:881-883` |
| A refresh recomputes the whole day. Measured: 1.7 s at the default load and 6.0 s at five times it. Extrapolated to the specification's busy day: about one minute. | `docs/USAGE.md:295-310` |
| Logs reach the database within about a second: ingest polls every 0.5 s. The 5651 lookup, Arama and Canlı Akış therefore already work on fresh data. | `core/src/arcilogger_core/config.py:348` |
| FortiGate traffic rows carry `sentbyte`/`rcvdbyte` and, for interim updates of long sessions, `sentdelta`/`rcvddelta`/`durationdelta` with `is_interim`. The usage engine counts each byte once: interim deltas at their own time, the final log only the rest. | `models.py:288-297`; `usage/engine.py:17-24` |
| How often FortiGate sends interim traffic logs is not confirmed ("sahada doğrula"). | `docs/USAGE.md:311-312`; `docs/CORRELATION.md:1036-1037` |
| `fg_traffic` has no index on `time` alone. Plain PostgreSQL uses daily partitions since W5-G; TimescaleDB uses 1-day chunks. | `models.py:253-257`; Addendum A G2 |
| `/api/v1/live/*` is Canlı Akış (the raw tail). `GET /live/status` already exists. | `api/src/arcilogger_api/routers/live.py:1-21` |
| Usage access: `usage.person` or `usage.anonymous` with the KVKK notice; pseudonyms by setting; module `usage_analytics`. | `api/src/arcilogger_api/routers/usage.py:6-20`, `:80-93`, `:179-184`; `web/src/nav.ts:57-65` |
| A log source is `[[sources]]` with a single address or a network. Secret names live in the secret store. The strict outbound firewall gets one rule per known target, as for SMTP. | `core/src/arcilogger_core/config.py:47-81`; `secretstore.py:30-38`; `admin/systemplan.py:184-197`; `deploy/windows/scripts/Set-ArciFirewall.ps1:25-35` |
| Status files of the services live under `state\`, written by their service, read by the API and the Sistem page. | `core/src/arcilogger_core/layout.py:88-106` |

### 6.2 Decisions

| # | Decision |
|---|---|
| K1 | **The same key, two optional fields.** `usage.work_hours` = `{"days", "start", "end", "breaks"?, "by_day"?}`. <ul><li>`breaks`: a list of `{"start", "end"}` (HH:MM, end excluded). At most 6, each at least 5 minutes, inside the day's `[start, end)`, not overlapping, sorted on save.</li><li>`by_day`: a map from an ISO weekday `"1"`…`"7"` that is in `days` to `{"start", "end", "breaks"?}`. It replaces that weekday's hours and breaks. **An entry without `breaks` has no breaks**: it does not inherit the general ones.</li><li>A stored three-field value stays valid and means no breaks and the same hours every work day. There is no settings migration.</li><li>New refusal codes, TR/EN in `settings.py`: `setting_work_hours_breaks` (a bad time, outside the day, overlapping, shorter than 5 minutes, more than 6) and `setting_work_hours_by_day` (a key that is not a work day, a bad entry). The `setting_work_hours_fields` text names the two optional fields.</li><li>Night shifts, and hours per person or department, are not in scope.</li></ul> |
| K2 | **Three classes of minute:** `work`, `break` or `off`. A holiday, or a weekday not in `days`, is `off` all day. A break minute is `break`. `UsageSettings.period(moment)` replaces `is_off_hours`. The fingerprint (`config.py:179-196`) includes `breaks` and `by_day`, so a change rebuilds the recent days through the existing path. |
| K3 | **Database.** Migration `0023_usage_period`: in the seven rollup tables, `off_hours boolean` becomes `period text NOT NULL CHECK (period IN ('work','break','off'))`, in the same primary-key position. Existing rows: `true` → `'off'`, `false` → `'work'`. Old days are not split by break unless the auto rebuild reaches them. With the 30-day database window, only days whose logs are still in the database can be rebuilt; the docs say so. JSON event records (`blocked_events`, URLs) carry `"period"`; the readers still accept an old `"off_hours"`. |
| K4 | **API: additive only**, so the web keeps working between the back-end and the web pull requests. <ul><li>`hours` accepts `all \| work \| break \| off`. `work` excludes breaks; with no breaks configured it is unchanged.</li><li>Every object that has `off_hours_active_seconds` gets `break_active_seconds`.</li><li>Records with `off_hours: bool` keep it and get `period` beside it.</li></ul>W5-K adds these fields to `web/src/lib/api-types.ts` (types only) and regenerates the contract and samples. |
| K5 | **Reports** (PDF, HTML, Excel): a "Mola / Breaks" column or row next to "Mesai dışı". It appears only when the work-hours setting has breaks for some day of the report, so existing reports look as before. |
| K6 | **KVKK:** break usage is personal data like any usage figure. It has the same roles, the same notice, the same `usage.view` audit and the same pseudonyms. Nothing more is shown than today; the docs recommend naming break measurement in the employee information text (legal review). |
| L1 | **`usage.today` every 60 s** (`USAGE_TODAY_EVERY`, was 5 minutes). |
| L2 | **Back-off.** The job skips (`_skipped`, details `{"reason": "backoff"}`) while `now` < the end of the last today-rollup + 2 × its duration. The duration and end are stored in `usage_days.details` (`duration_ms`, `finished_at`). A 2 s rollup therefore refreshes every minute; a 60 s rollup about every 3 minutes. `usage.daily` and rebuild requests never wait for it. |
| L3 | **Freshness on screen.** `GET /usage/company` and the subject answers carry `updated_at` (today's `computed_at`) and `data_until` (`computed_until`), additive. |
| L4 | **Unchanged on purpose:** sealing stays once a day (one time stamp per day; credits, ADR and Addendum A). Lookup, Arama and Canlı Akış are already live. A long session's bytes appear when FortiGate logs them (interim or final log, X-B1). |
| M1 | **Canlı Durum** (`/activity`): a new page after Canlı Akış in the menu. <ul><li>On top: "İnternet hattı / Internet line" (N).</li><li>Below: "Şu an / Right now", who is sending and receiving data over the last 1, 5 or 15 minutes, from the FortiGate traffic logs.</li></ul>MikroTik sends no byte counts and contributes nothing here (stated on the page). |
| M2 | **`GET /api/v1/activity/devices?window=1\|5\|15`** (minutes, default 5; any other value is 422 `activity_window_invalid`). New area `api/src/arcilogger_api/activity/`. <ul><li>**Per subject** (person, device or IP by the usage subject rules, attribution and `usage_excluded`): `download_bytes`, `upload_bytes`, `download_bps`, `upload_bps` (bytes × 8 / window seconds), `sessions`, `top_domains` (≤ 3, background excluded), `top_apps` (≤ 3), `last_seen`. Sorted by download, at most 200 rows, plus `more`.</li><li>**Company:** `totals`, and `per_minute` with download and upload bytes for each of the last 60 minutes.</li><li>**Freshness:** `data_until` (the newest traffic row) and `generated_at`.</li><li>**Counting:** bytes with the engine's increment rule (`engine.py:17-24`), spread over the minutes they cover, using the engine's code (the API already depends on the worker). An interim delta counts at its own time; the final log adds only what the interim logs did not.</li></ul> |
| M3 | **Access and audit.** <ul><li>Same as `/usage/subjects`: `usage.person` or `usage.anonymous` with the KVKK notice, pseudonyms by the same rule, module `usage_analytics` (owner question QB1).</li><li>Audit `activity.view` (target `activity:devices`, the window, the row count; no names) at the first request of a user and then at most once every 15 minutes while they keep polling.</li><li>The page polls every 10 s, only while visible.</li></ul> |
| M4 | **Performance.** Measure the plan of the 15-minute window on both layouts, on perf data at 2,000 lines/s. If plain PostgreSQL scans today's partition, add a BRIN index on `fg_traffic (time)` in migration `0024_fg_traffic_time_brin` (down revision `0023_usage_period`; the pull request that merges second renumbers). The ingest gates must hold. **Guard:** one request over 15 minutes in under 1 s (best of 5) on the perf data; the figures go in the PR and the contract. |
| N1 | **Configuration** on the log source (TOML, edited in the browser through Kaynaklar and the admin channel). <ul><li>`[sources.snmp]`: `enabled` (false), `port` (161), `user` (SNMPv3 user name, not a secret), `auth` (`sha1` \| `sha256` \| `sha512`, default `sha256`), `priv` (`aes128`, RFC 3826, default; `aes256` allowed, marked "sahada doğrula"), `interfaces` (1–4 `ifName` values of the WAN lines), `capacity_mbps` (optional map from interface to `{"down", "up"}`), `interval_seconds` (15, 10–60).</li><li>**Only SNMPv3 with authentication and privacy (authPriv).** v1/v2c are refused: their community string travels in clear text.</li><li>The target is the source's own `address`. A source whose address is a network is refused with `snmp_source_is_network`.</li><li>Secrets `snmp.<source>.auth_key` and `snmp.<source>.priv_key` (8–64 characters) go in the secret store only: never in TOML, logs or answers. A missing secret gives `snmp_secret_missing`.</li></ul> |
| N2 | **Poller:** worker job `snmp.poll` every 5 s; it polls each enabled source whose `interval_seconds` is due. <ul><li>**One GET** of `sysUpTime.0` (1.3.6.1.2.1.1.3.0) and, per interface, `ifHCInOctets` (1.3.6.1.2.1.31.1.1.1.6.i), `ifHCOutOctets` (…1.1.10.i), `ifOperStatus` (1.3.6.1.2.1.2.2.1.8.i) and `ifHighSpeed` (1.3.6.1.2.1.31.1.1.1.15.i).</li><li>**ifIndex lookup:** the ifIndex of each name comes from a walk of `ifName` (1.3.6.1.2.1.31.1.1.1.1) at start, and again when a name is missing or `sysUpTime` went back.</li><li>**Rate** = Δoctets × 8 / Δt. A sample after a counter or `sysUpTime` decrease is dropped (reset).</li><li>**64-bit counters only:** an agent without them gives `snmp_no_hc_counters`.</li><li>**Direction:** on a WAN interface, In = download and Out = upload (X-B2).</li><li>**Library:** a maintained SNMP library with an OSI-approved licence (for example pysnmp; confirm its licence in the PR), pinned in `uv.lock`. Its name, version and licence go in the PR, for Wave 7's third-party notices.</li></ul> |
| N3 | **Network and firewall.** <ul><li>The server sends UDP only to `<source address>:<port>` of SNMP-enabled sources.</li><li>In strict outbound mode, the system plan (`admin/systemplan.py`, as SMTP at `:184-197`) and `Set-ArciFirewall.ps1` add one outbound rule per such source: UDP, that remote address and port, named `ARCILOGGER SNMP <source>`.</li><li>Nothing inbound.</li><li>The device side (DEVICE-SETUP, "sahada doğrula") gets a read-only SNMPv3 user, answered only to the ARCILOGGER server's address, on a LAN interface, **never on a WAN interface**.</li></ul> |
| N4 | **Storage and state.** <ul><li>`state/wan-rates.json` (new `Layout.wan_rates()`), written atomically by the worker. Per source and interface: the last 60 minutes at full resolution and the last 24 hours as 1-minute averages, with `status` (`ok`, `timeout`, `auth_failed`, `no_such_interface`, `snmp_no_hc_counters`, `disabled`) and `last_error_at`.</li><li>No database table.</li><li>Alert `R20-snmp` (low, key = source) after 10 minutes without a good sample; cleared by the next good one.</li></ul> |
| N5 | **Probe.** "Bağlantıyı dene / Test connection" in Kaynaklar runs through the admin channel's probe (`admin/probe.py`). It walks `ifName` and returns each interface's name, `ifOperStatus` and `ifHighSpeed`, so the administrator picks the WAN lines from a list. Each error code of N4 has TR/EN text. |
| N6 | **`GET /api/v1/activity/wan?range=60m\|24h`.** <ul><li>Per SNMP source and interface: `name`, `oper_status`, `download_bps`, `upload_bps` (newest), `capacity_mbps`, `series`, `status`, `updated_at`.</li><li>Access: `dashboard.view` or `system.read`. It holds no person data.</li><li>Not licence-gated (QB1).</li></ul> |
| N7 | **Panel card "İnternet hattı":** download and upload of each line now, with a 60-minute sparkline. Hidden while no source has SNMP on; administrators see a link to Kaynaklar instead. |
| P1 | **Mesai saatleri editor** (Uygulama ayarları). <ul><li>A week table, Pazartesi…Pazar: work day, start, end, breaks (add or remove rows), and "Tüm günlere uygula".</li><li>A strip preview of a day in work/break/off colours.</li><li>The server's refusal texts.</li><li>A day whose hours equal the general ones is saved without a `by_day` entry.</li></ul> |
| P2 | **Kullanım.** <ul><li>The filter reads `Tüm saatler / Mesai içi / Mola / Mesai dışı`.</li><li>A "Mola" column beside "Mesai dışı", also in the exports.</li><li>The person timeline shades break bands.</li><li>"Son güncelleme hh:mm (veriler hh:mm'e kadar)" from L3.</li><li>`USAGE_POLL_MS` 60 s.</li></ul> |
| P3 | **Canlı Durum page.** <ul><li>The line cards: Mbps now, 60-minute chart, % of capacity when known.</li><li>The window selector 1/5/15.</li><li>The "Şu an" table: subject, download, upload (Mbps), top sites, top apps, last seen.</li><li>The company per-minute chart.</li><li>The `data_until` note: "Uzun bağlantılar FortiGate ara kayıtları geldikçe görünür".</li><li>Polling every 10 s while visible; the module gate and the permissions of M3 and N6, each part shown only to those who may see it.</li></ul> |
| P4 | **Kaynaklar → source → SNMP:** the N1 fields, write-only secret fields, the probe's interface list and the capacity. **Panel:** the N7 card. |

### 6.3 New and changed keys

| Key | Where | Default | Bounds |
|---|---|---|---|
| `usage.work_hours` (two optional fields) | database setting | unchanged: `{"days": [1,2,3,4,5], "start": "08:00", "end": "18:00"}` | K1 |
| `[sources.snmp]` `enabled`, `port`, `user`, `auth`, `priv`, `interfaces`, `capacity_mbps`, `interval_seconds` | TOML, per source | `false`, 161, `""`, `sha256`, `aes128`, `[]`, `{}`, 15 | port 1–65535; 1–4 interfaces; interval 10–60 |
| secrets `snmp.<source>.auth_key`, `snmp.<source>.priv_key` | secret store | — | 8–64 characters |

### 6.4 "Sahada doğrula"

- **X-B1:** how often FortiGate sends interim traffic logs. This is the delay of M's table for long sessions. A test pins that an interim delta counts at its own minute.
- **X-B2:** on each device, In/Out of the WAN interface match download/upload (compare with the device's own interface graph).
- **X-B3:** the SNMPv3 protocols each device accepts. Older firmware may need `sha1`, or may not offer `aes256`.
- **X-B4:** the interface names (`ifName`) the devices report for their WAN lines. The probe shows them; nothing is assumed.

### 6.5 Tests (the minimum)

- **W5-K:**
  - The validator: an old three-field value is valid; every refusal of K1.
  - Minute classes with breaks, `by_day`, a holiday and a non-work day.
  - Migration 0023: `true` → `off`, `false` → `work`, primary keys.
  - A golden day with a session across lunch, split into work and break.
  - A changed setting rebuilds the recent days.
  - `hours=break` and `break_active_seconds` in the API and the exports.
  - The reports' column only when there are breaks.
  - Captured samples.
- **W5-L:**
  - The 60 s interval.
  - The back-off: skip and run, `usage.daily` unaffected.
  - The L3 fields.
  - Default and heavy timings in the PR and in `docs/USAGE.md` §8.
- **W5-M:**
  - The increment rule against the usage engine on the same rows: equal byte totals over a whole day.
  - An interim delta counted at its own time.
  - Attribution, `usage_excluded` and pseudonyms.
  - Permissions, the KVKK notice, the module gate and the audit throttle.
  - `activity_window_invalid`.
  - The M4 guard.
  - Captured samples.
- **W5-N:**
  - Rate arithmetic.
  - Counter and `sysUpTime` resets.
  - `ifName` mapping and re-walk.
  - Each status code from a test double at the library boundary, and one round trip against an in-process SNMPv3 responder if the library offers one (otherwise say why).
  - The state file: atomic write and its bounds.
  - Secrets never in logs or answers (log capture).
  - The firewall plan: the rule only in strict mode and only for enabled single-address sources.
  - The TOML refusals.
  - The probe.
  - Captured samples.
- **W5-P:** unit tests of the editor model, the filter, the activity model and the charts; axe; e2e with mocks from the captured samples.

### 6.6 Ownership, owners, order

| Package | Owner | Owns | Depends on |
|---|---|---|---|
| W5-K | Manager (owner request 2026-11-05 21:10Z: "sende işin içine gir") | the `usage.work_hours` validator and texts in `db/src/arcilogger_db/settings.py`, its `settings_meta.py` row, `worker/src/arcilogger_worker/usage/config.py`, `usage/engine.py`, `usage/query.py`, the seven rollup models in `models.py`, migration `0023_usage_period`, the usage hunks of `api/src/arcilogger_api/routers/usage.py`, `usage_models.py`, `usage_exports.py`, the period hunks of `worker/src/arcilogger_worker/reports/` (`pdf.py`, `html.py`, `xlsx.py`, `texts.py`), the usage types of `web/src/lib/api-types.ts`, regenerated contract and samples, tests | — |
| W5-L | Manager (second pull request) | the `USAGE_TODAY_EVERY` line of `worker/src/arcilogger_worker/service.py`, `usage/jobs.py` (back-off, details), the L3 fields, `docs/USAGE.md`/`KULLANIM.md` §8 timings, tests | W5-K merged (same files) |
| W5-M | Tayfun (after #193; #96 after W5-M) | `api/src/arcilogger_api/activity/` (new: devices), `routers/activity.py` (mount), migration `0024_fg_traffic_time_brin` if M4 needs it, `web/src/lib/activity-types.ts` (day-1 pull request, as W5-C did), regenerated contract and samples, tests | — |
| W5-N | Arman 2 (after #136) | `worker/src/arcilogger_worker/snmp/` (new), its job line in `service.py`, `Layout.wan_rates()` in `core/src/arcilogger_core/layout.py`, the `[sources.snmp]` model in `config.py`, the two secret names in `secretstore.py`, the SNMP keys in `admin/rules.py` and `admin/contract.py`, the SNMP probe in `admin/probe.py`, the SNMP targets in `admin/systemplan.py`, the SNMP rule in `Set-ArciFirewall.ps1`, `GET /activity/wan` in `api/src/arcilogger_api/activity/`, the `R20-snmp` constant and texts, the dependency in `pyproject.toml`/`uv.lock`, regenerated contract and samples, tests | **W5-T merged**: it owns `config.py`, `secretstore.py`, `admin/rules.py`, `admin/contract.py`, `admin/probe.py`, `Set-ArciFirewall.ps1`. The SNMP module itself can start at once. |
| W5-P | Gülşah (after #195) | `web/src/pages/activity/` (new), the `activity` entry of `web/src/nav.ts` and its route, the work-hours editor in `web/src/pages/settings/` (`setting-editors.tsx`, `setting-values.ts`), the hunks of `web/src/pages/usage/`, `USAGE_POLL_MS` and the activity hooks in `web/src/lib/api-hooks.ts`, the Panel card, the SNMP section of `web/src/pages/sources/source-sheet.tsx`, locale keys, e2e mocks, tests | each part after its back end: the editor and usage parts after W5-K/L, the activity page after W5-M (and N for the line card) |
| W5-F | Saray (see the manager's note on #104) | <ul><li>KULLANIM/USAGE: breaks, `by_day`, the minute refresh and back-off, freshness, the 30-day limit on rebuilding old days.</li><li>New CANLI-DURUM.md / LIVE-STATUS.md.</li><li>DEVICE-SETUP: an SNMPv3 section per vendor, "sahada doğrula", goals and parameters only, no invented menu paths.</li><li>KURULUM/INSTALL: the strict outbound SNMP rule.</li><li>AYARLAR/SETTINGS rows.</li><li>LISANS/LICENSING: which part needs `usage_analytics`.</li><li>The KVKK information text draft: breaks and the live view, marked for legal review.</li></ul> | the packages above |

Shared files:
- `worker/src/arcilogger_worker/service.py`: W5-L and W5-N, one line each.
- `api/src/arcilogger_api/activity/`: W5-M, then W5-N.
- `web/src/lib/api-types.ts`: W5-K types, then W5-P.

The later pull request merges `origin/wave-5` and keeps both sides.

**Merge order:** W5-K → W5-L. W5-M any time. W5-N after W5-T. W5-P's parts after their back ends. W5-F last. Wave 5's estimate grows by about 3–5 days (owner informed 2026-11-05).

### 6.7 Owner questions (the defaults ship)

- **QB1 (licence):** the per-person "Şu an" table belongs to the paid `usage_analytics` module, like Kullanım. The internet line's speed (N) belongs to the base product.
- **QB2 (KVKK):** break usage is visible to the roles that already see usage, with the same notice and audit. No separate switch hides it.

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
- **C21 – `supported` follows the provider's credit query, not its name** (answer on #117).
  - `false` for every provider without a credit query: `rfc3161` (`Rfc3161Client.credits()` returns `None`, `core/src/arcilogger_core/tsa/client.py:58-59`) and `turktrust`.
  - `true` for `zamane` and `mock` (its simulated balance).
  - C3's "today `turktrust`" missed `rfc3161`.
- **C22 – What makes an issuer's revocation list fresh** (`crl_stale`, `R20-crl`; review of #117). It replaces W5-C-3's measure on `this_update`: a root CA may publish its list only every few months, so that measure would never clear.
  - An issuer in use is fresh when it has an accepted stored list (C14/C20) that meets both conditions:
    - its index `fetched_at` (the last successful fetch and verification of that list) is newer than `tsa.crl_max_age_days`;
    - its `next_update`, when present, has not passed.
  - A list past its `next_update` is stale, however recently it was fetched.
  - A fetch exactly `tsa.crl_max_age_days` old is stale.
  - An issuer in use without an accepted list stays stale.
  - Kamu SM's and TÜRKTRUST's real publication intervals are still to be confirmed (K10/T4, sahada doğrula).
- **C23 – The reload's keep time in the 409** (G7, G10; questions on #112 and #121). The `details` of `409 archive_reload_required` also carry `keep_days`: the current value of `db.reload_keep_days`.
  - Reason: a lookup or search user cannot read `/settings`, and the confirm dialog must name the real number of days the data comes back for (KVKK).
  - W5-G adds the field to the Pydantic model, to `web/src/lib/archive-reload-types.ts` and to the captured samples.
  - W5-J shows it in the dialog, and shows `expires_at` once the reload is `ready`.
  - Until W5-G has the field, W5-J names the setting with its default (7 days).

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
  leaves out an entry with an unknown `status` (a list or an object included) or an unreadable
  `cert_not_after`, and counts from each token's `status`, never from a totals object. A
  missing file is "no report yet"; an unreadable one is unknown and decides nothing (no reason,
  no alert raised or resolved, `renewal: null` and a page problem on the day page). An entry's
  `renewed_at` is taken when the writer gives one; otherwise the day page reads the genTime of
  the renewal token beside the `covered_by` record (`<record>.zd`). A record with a drive, a
  root (a UNC path included) or a `..` step is refused as written, before anything on the disk
  is opened; the token itself, a link followed, must resolve inside `vault/renewals`. Else
  `renewed_at` is `null`. `path` is vault-relative; a leading `vault/` and a `.zd` suffix of
  `covered_by` are tolerated.
- **W5-C-2: the day page's `revocation` texts** for the codes that §2.6 does not word
  (`crl-revoked-later`, `crl-hold`, `crl-none`, `crl-stale`, `crl-ok`) are W5-C's, after the
  standalone verifier's (`legalstatus/texts.py` `REVOCATION`); `crl-revoked` uses
  `certificate_revoked`. `level` is the verifier's own `crl_level`, else the D10 level of the
  code. The day page checks the token with the stored lists (`crl.load_stored`, read once
  per request, C19), so `revocation` is `crl-none` before the first `crl fetch`. It is `null`
  only when `state/crl` cannot be read, and the page's `problems` then say "revocation not
  checked"; a list whose file no longer matches its index entry is named there too (C14).
- **W5-C-3: `crl_stale`** follows C22 (review of #117; it replaces the first measure on
  `this_update`). An issuer in use is fresh while it has an accepted stored list whose index
  `fetched_at` is newer than `tsa.crl_max_age_days` and whose own `nextUpdate`, when it has one,
  is later than now. A fetch exactly `tsa.crl_max_age_days` old is stale, and so is a list at
  or past its `nextUpdate`, however recently it was fetched; an issuer without an accepted list
  stays stale. A list's fetch time is its index entry's (the entry of its stored name), and its
  `nextUpdate` is the list's own; an accepted list whose entry gives no readable `fetched_at`
  counts for nothing, and so does one whose `fetched_at` is later than now: a skewed clock never
  keeps a list fresh, and the clock check reports the skew (manager's re-review of #117). The
  issuer's best list counts. "Issuer in use" is a CA certificate of the
  newest sealed day's verified chain (`token_chain` against the pinned roots, the TSA
  certificate left out), by its SHA-256 fingerprint. So an old provider's lists stop mattering,
  and before the first sealed day, or when the newest token reaches no pinned root, nothing is
  in use. Only the lists that `crl.load_stored` accepts count: a list whose file was changed or
  deleted is no verified list (C14, C20). When `state/crl` cannot be read, every issuer in use
  is stale; that is never a server error. `since` is the earliest moment a stale issuer that has
  a list stopped being fresh (its best list's `fetched_at + max_age`, or its `nextUpdate` when
  that comes first), else `null`; `link` is `settings-tsa` (C5). `R20-crl` uses the same rule
  (`alerts.system.crl_status`, D11); its details add `changed_files` (the lists left out under
  C14). Kamu SM's and TÜRKTRUST's real publication intervals are still to be confirmed (K10/T4,
  sahada doğrula). `certificate_revoked` and `R20-cert` also check the newest sealed day's chain
  against the stored lists at its genTime (`crl-revoked` only; `since` is the revocation date),
  beside the scheduled verifications' day errors.
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
  only. R20-cert's "expires within 30 days and is uncovered" is read as in W5-C-12. R20-cert
  is resolved only when every input can be read: the re-stamping report, every scheduled
  verification (`state/verify/last-*.json`; one that cannot be read or holds no verification
  counts as unreadable), the newest sealed day's token, reaching a pinned root, and `state/crl`
  (before the first sealed day no list is needed). A stored list that was changed or deleted
  (C14/C20) keeps its index entry for good, so it holds open only an alert that carries a
  revocation (its `revoked` count, or the critical severity the store keeps): an expiry alert
  resolves once `state/renewal.json` says the tokens are covered (manager's re-review of #117).
  The damaged list is reported where C14/C20 put it, CrlFetch's `stored_file_changed` and the
  day page's problems. A revocation stays open while the input that found it cannot be read. The
  rule is one for both severities: a "high" raised from `state/renewal.json` alone also stays
  open while the newest token reaches no pinned root (for example a provider whose roots are
  not pinned yet), the safe side. "The `Renew` task failed twice
  in a row" is left to W5-E: no state file records Renew runs yet (C12). `R20-crl` is judged
  from `state/crl` only (the stored lists and `index.json`, never an exit code; C22, W5-C-3);
  its details give the stale issuers, the failures, the changed files and the newest
  `fetched_at`.
- **W5-C-8: `Exclude<A, B>` in the web type tests.** `LegalReason.level` is
  `Exclude<LegalLevel, 'ok'>` (§2.3.1, verbatim), so `tsType()` of `api-types.test.ts` and
  `check()` of `api-samples.test.ts` learned that form.
- **W5-C-9: `SealDayDetail.renewal` and `revocation` are optional** in `seal-types.ts`, as
  W5-D asked and like `tsa_cert_not_after?`; the API always sends both keys (`null` when there
  is nothing). `Credits.supported` is required in `api-types.ts`, because the schema requires
  it and `api-types.test.ts` compares requiredness. It is false for a provider without a credit
  query (`rfc3161` and `turktrust`, C21; `NO_CREDIT_PROVIDERS` of `routers/credits.py`) and also
  when the newest `state/credit.jsonl` line of the configured provider says `"supported": false`.
- **W5-C-10: day-check Turkish texts beyond #106's four** in `issues.py`: C14's "revocation
  list {file} does not match its index entry" and the builder's "CRL file {file} changed while
  the package was built". `{when}` is shown in site time (`format_datetime`).
- **W5-C-11: damaged state files are read as unknown**, never a server error of the banner (the
  60 s cache keeps no failed answer). A scheduled verification whose `checks` is not a list is
  skipped. A re-stamping entry whose time is at the edge of the calendar (one that UTC or site
  time cannot show, at either end) is left out; so is such an index `fetched_at` or failure
  `at`, and a moment shifted past the edge is unknown. The high edge is 9999-12-31T21:00Z, so a
  certificate with RFC 5280's "no well-defined expiration" (`99991231235959Z`) has its entry
  left out: its day page shows `renewal: null`. JSON nested too deeply to parse counts as
  unreadable. A seal state that cannot be parsed makes the unstamped days unknown.
- **W5-C-12: R20-cert "high"** (manager answer on #117, 2026-11-02 21:07Z, Q6) is a token of
  `state/renewal.json` that is `uncovered` (its certificate expired with no renewal that counts),
  or one that is `due` with at most 30 days left before its certificate's `cert_not_after`
  (exactly 30 days is high, 30 days and a second is not). The 30 days are
  `alerts.system.CERT_EXPIRY_WARNING` (`timedelta(days=30)`), not a setting. A revocation still
  makes it `critical`. The findings count both (`expiring`, `uncovered`, beside `revoked`).
  "The `Renew` task failed twice in a row" stays W5-E's (W5-C-7).
- **W5-C-13: `crl_stale` leaves out ARCILOGGER's test root, as `R20-crl` does (#160; W5-E-5,
  Q6 of #158).** A chain whose root carries ARCILOGGER's test organization has no issuer in use
  for `crl_stale` (W5-A D5: a test stamp never counts), so a mock or trial install no longer
  shows it beside its `provider_not_legal`. The rule is one helper,
  `alerts.system.crl_stamp` (the newest sealed day's stamp, or none for such a chain), which
  `check_stamp_crl` and `compute_legal_status` both pass to `crl_status` (D11); this ends the
  difference W5-E-5 left open. Only the root decides, and only the newest sealed day's chain is
  in use (W5-C-3): on a site that holds test days and real days, `crl_stale` holds while the
  newest one is a real CA's and that CA has no fresh list. `certificate_revoked` still reads the
  stamp's chain whatever its root. The legal-status tests and samples now seal through a made-up
  CA outside the test organization (`legalstatus.testing.simulated_ca`): the samples' Kamu SM
  cases run on a site of their own whose sealed days repeat the capture's. Tests:
  `test_crl_stamp_leaves_out_only_a_chain_to_the_test_root`,
  `test_crl_stale_not_for_a_mock_install`,
  `test_crl_stale_only_for_a_root_outside_the_test_organization` and
  `test_crl_stale_on_a_mixed_site_by_the_newest_sealed_day` (`api/tests/test_legal_status_api.py`).

## Deviations — W5-G (2026-11-03)

- **W5-G-1: reload days are device-time days.** Partitions and chunks are by device `time`, and
  one raw day's rows lie within ±1 day 1 h of it, so ArciIngest loads the raw days ±2
  (`RAW_DAY_MARGIN`) of a request; `bytes_total` covers those files.
- **W5-G-2: margins on the cutoff (G3).** The `db.window` cutoff uses the oldest unsealed day
  minus 2, and a sealed day whose manifest or files cannot be read counts as unsealed
  (`limited_by` `manifest`). A reload's first day needs no margin: its margin files are loaded
  with it.
- **W5-G-3: the API's "gone" rule.** The applied cutoff is kept in the status row
  `db.window_dropped_before` (written by `drop_before`). A day before it, or before the window
  with no rows, is gone; a `loading` reload makes its days ±4 gone even inside the window, a
  `queued` one does not. The 409 is given only when the vault holds a gone day; a day without a
  manifest keeps the existing notices.
- **W5-G-4: `POST /archive-reloads` behaviour.** Complete in-window days are refused with 422
  `validation_error`; when nothing needs a new load, the user's own active request is returned
  (audited as `existing`); another user's request never is, for an administrator either: each
  user's request, an administrator's included, keeps its own record and audit entry (review
  point 4 on #121; an administrator still sees every request in `GET /archive-reloads`). A
  skipped (empty) day passes with nothing to load. At claim time a file whose rows of the
  request's days are in the database already (its offset finished at `bytes_uncompressed`,
  nothing dropped from those days since) is neither reset nor loaded again: another user's
  request of loaded days becomes `ready` with its own record and its own audit entry, and one
  load serves both (manager answer 6 on #121). The disk check (G5) estimates only the files not
  loaded yet by that same rule (`IngestService.loaded_files`, called read-only by the API), so a
  request of days another user's `ready` reload holds is not refused with
  `archive_reload_disk_low` (review point 5 on #121); the audit entry's `estimated_bytes` is
  that estimate. `bytes_total` stays the sum of every file (the progress denominator: ingest
  counts the loaded files as done), and the 409's `details` keep G5's full estimate. The
  estimate is made when the request is made: a file dropped between the request and its claim
  is loaded without a second check.
- **W5-G-5: failure codes of a reload** (`error_code`): `archive_day_not_in_vault` (retention
  removed a day after queueing) and the new `archive_file_unreadable` (a sealed file is checked by
  size and SHA-256 when the request is claimed, and kept failing to read). Their TR/EN texts are
  `archive.service.FAILURE_MESSAGES`. A file that changes or becomes unreadable while it loads
  fails the request with `archive_file_unreadable`; its offset stays short of
  `bytes_uncompressed`, so its day never counts as loaded and the lookup keeps answering 409 for
  it, never a partial answer (manager answer 9).
- **W5-G-6: the reset deletes more than G4 names:** also the file's `parse_failures` and clock
  samples; and it deletes the file's rows by `(source, raw_day)` wherever their device times lie
  (manager answer 10). No index leads with `raw_day`, so each delete is bounded in device time
  for partition and chunk pruning, only where ingest guarantees the rows: the file's time window
  (`stream_window`: every line received on its day), and around each minute in which a line of
  the file was received outside its day (± 1 day 1 h, a device time further than a day from the
  receive time not being used). Those minutes are read from the sealed file at its reset (the
  bytes that were loaded, checked against the manifest at the claim), not from `search_counts`,
  so a file recounted from its rows (`search-counts rebuild`, `search.counts_backfill`: the rows
  within its window only) or loaded before the counts existed is reset as completely; on plain
  PostgreSQL the default partitions are swept as well. The clock samples of those lines (at
  minutes of other days, shared with those days' own lines) lose the file's part instead of
  being deleted; a sample of another file's line received in the reset file's day goes with
  that day's minutes (derived data, `clocksamples.rebuild`). A reload run twice leaves every row
  and every sample of its files once, on both back ends. Since W5-G-25 the rows are deleted in
  steps after a first transaction, not in one transaction.
- **W5-G-7: bounded steps (G2, G3).** At most 16 days of partitions are created per call (about
  600 partitions exhaust the default lock table); the job drops in bounded steps of 16 created
  plus dropped days per transaction, up to 64 steps a run; `maintain_window` refuses a cutoff
  less than 7 days before today.
- **W5-G-8: the ingest horizon** after a database loss is the window plus the 2 raw days before
  it.
- **W5-G-9: `SearchPage.archived` is optional** in the schema (`archived?:` in the web type); the
  API always sends it.
- **W5-G-10: G1 conversion.** A stored 400 becomes 30 whoever wrote it; an administrator's row
  then carries `:migration`. The revision writes no audit entry itself (no revision does): a
  change leaves the status row `db.retention_days_migration` (the old and the new value, who had
  set the old one), which the first `db.window` run turns into one `settings.change` audit entry
  (actor `system`) before it drops anything (manager answer 4). An administrator can set the
  value again afterwards (`PATCH /settings`, bounds 7 and up as G1 says; the database then grows
  accordingly). Accepted by the manager on #121.
- **W5-G-11: the factor (G5)** is `DB_BYTES_PER_RAW_BYTE = 2.6`, rounded up from 2.40 to 2.51
  measured on plain PostgreSQL 16 with daily partitions through the real ingest
  (`docs/DATA-MODEL.md`).
- **W5-G-12: daily partitions are UTC days, and the change-over slice (G2).** On plain
  PostgreSQL `<table>_pYYYYMMDD` is `[D 00:00 UTC, D+1 00:00 UTC)`, as TimescaleDB's 1-day
  chunks, not a site day: the lookup engine asks its D15 and probe steps one UTC day at a time
  (`lookup.engine.CHUNK`), and over a site-day partition such a step read two partitions under
  an ordered `LIMIT 1`, walking the second one's rows of a busy address (125,000 index entries,
  33 ms; the 3.8 guards failed). G2's "from the first day of the next month" becomes: a daily
  partition is its UTC day less every partition that overlaps it, so after the current month
  (ending at local midnight, 21:00 UTC) comes a 3-hour daily partition named for that UTC day,
  and before a monthly partition kept ahead a 21-hour one; no device time falls into the
  default partition. The window's site days take one more UTC day (31 with the default), and
  the downgrade of `0021` splits a daily partition's rows by local month. G2's "a monthly
  partition goes once it ends on or before the cutoff" becomes the drop rule: a partition goes
  once the UTC day it ends in has ended by the cutoff's local midnight. A daily partition then
  goes as a chunk does (the one holding that midnight keeps up to 21 hours of the day before),
  and a monthly partition (ending at 21:00 UTC) goes together with the daily partition of the
  rest of that UTC day, one day later than G2's wording: a month going alone would leave that
  daily partition's name keeping an archive reload of its last day from the rest of the UTC
  day, and those rows in the default partition. While a monthly partition is in the window, a
  lookup step over the UTC day it ends in still reads two partitions (up to one month after the
  upgrade). The cutoff, the margins (W5-G-1, W5-G-2) and the "gone" rule (W5-G-3) are
  unchanged: they are by site day and instant. The two plan checks of
  `worker/tests/test_lookup_performance.py` (not W5-G's file; accepted by the manager on #121)
  accept a sequential scan of a small partition where they accepted none: the continuous-data
  test one of at most 10 pages, the D15 test an empty one (0 pages: a future day's; on
  TimescaleDB no chunk at all), by the relation's `pg_class.relpages`, checked to be current
  (equal to its size after the fixture's `VACUUM (ANALYZE)`). Each also requires a partition or
  chunk of more than 10 pages read through an index (every plan of the continuous-data test, at
  least one step of each D15 case), so neither check can pass on empty partitions alone.
  Accepted by the manager on #121, for the lookup engine's per-UTC-day steps and the 3.8 guards.
- **W5-G-13: a refused reload writes no audit entry** (manager answer 4 on #121). G6's "every
  request is written to the audit log before it is queued" applies to the accepted requests:
  queued, or answered with the user's active request (`existing`, W5-G-4). A refusal (422:
  `archive_reload_too_large`, `archive_day_not_in_vault`, `archive_reload_disk_low`, or
  `validation_error`) queues nothing and brings no data back, so it writes none.
- **W5-G-14: the job's audit entries and the stuck-reload alert (G3; manager answers 2, 4 and
  5).** Answer 4 asks for one audit entry per run that drops something; it is one per *step*
  that drops something, written in that step's transaction, so that no drop is ever committed
  without its entry (an entry per run would be written after steps already committed); the
  manager accepted one entry per step on #121. A daily run is one step, so one entry; a run
  that works off a backlog (the first run after the upgrade: about 24 steps) writes one per
  step. Every step of `db.window` that drops something
  writes one `db.window` audit entry (actor `system`, target `database`, in the step's
  transaction): its cutoff, the partitions or chunks dropped (names, at
  most 100, and their count) and the exact counts of the rows deleted row by row (default
  partitions, `search_counts`, clock samples; a dropped partition's or chunk's rows are not
  counted). It is the record of the deletion; whether a deletion record ("imha tutanağı") is
  needed as well is left to legal review (the retention policy draft). A reload without
  progress since the previous successful run (the same status and `bytes_done`) raises
  `R20-db-window` (medium) with the key `R20-db-window:reload-<id>` beside G3's failure key,
  naming the reload and its days; nothing fails it, and the cutoff keeps waiting. A `loading`
  reload without progress raises it; a `queued` one only while nothing is `loading` and no
  reload progressed or ended since that run (manager answer 2 on #121: a queue that moves is not
  an incident). A reload queued behind a progressing load raises nothing, and an alert already
  open for it resolves (`reload_progressed`). A failure after the steps (the
  stuck-reload report, resolving the failure alert) fails the run, which the worker retries,
  but opens no alert: the steps' drops are done and audited.
- **W5-G-15: the downgrade of `0021` merges in steps and goes on after an interruption**
  (manager answers 3 and 8 on #121). In one transaction the whole window and the days ahead
  need about 6,700 or more lock-table entries (each partition with its indexes, TOAST table, row
  types, constraints and defaults), beyond PostgreSQL's default 6,400
  (`max_locks_per_transaction` 64 × `max_connections` 100). So the downgrade merges one local
  month of one table per transaction (about 500 entries on the full window) and commits each
  step itself, on the driver's connection; PostgreSQL's configuration is never changed. It first
  estimates its largest step and refuses with a TR/EN message, changing nothing, only when even
  that one cannot fit. A month's last UTC day stays as the next month's change-over slice until
  that month's step, so every committed step leaves a valid `0021` layout. The steps are planned
  from the partitions there are: a run interrupted part way leaves the merged months monthly and
  alembic's version at `0021_db_window`, and running the downgrade again goes on from there.
  Each step takes the partitions' advisory lock (`ARCIPART`), and the version moves only when,
  under that lock, no daily partition is left: one made between two steps (a service still
  running) makes the run refuse with a TR/EN message, and the next run merges it. A step that
  finds, under its locks, a daily partition in its own month that its plan does not name (one
  made since the plan) refuses with the same text before it changes anything, never with
  PostgreSQL's partition overlap error (review nit on #121).
  `archive_reloads`, the settings rows and `db.retention_days` 30 → 400 go last, in the caller's
  transaction, together with alembic's version move. Whatever the caller's transaction held
  before the downgrade, including a transaction-level advisory lock, is committed or released
  with the first step: a caller that serialises downgrades (#122) takes a session-level lock.
  The downgrade writes no audit entry for `db.retention_days` going back from 30 to 400 (no
  revision writes audit entries); by manager answer 4, #122's rollback step audits it. On
  TimescaleDB nothing is merged and the downgrade stays one transaction.
- **W5-G-16: audit labels and alert rows outside W5-G's files** (granted by the manager on
  #121). The audit labels of `db.window` and `archive.reload` (TR/EN, in `KNOWN_ACTIONS`; the
  action filter's prefixes `archive.` and `db.`) live in a new locale area
  `web/src/locales/areas/dbWindow/{tr,en}.json` beside `web/src/pages/audit/audit-labels.ts`;
  the grant named `db.window`, and `archive.reload` (written by W5-G's API, G6) had no label
  either. The `R20-db-window` rows of `docs/UYARILAR.md` / `docs/ALERTS.md` §2 are added.
- **W5-G-17: the lookup reads only its days (G7; review point 3 on #121).** (a) With
  `dhcp.fortigate_use_logged_lease` (the default) `lookup_days` reaches back as far as one
  reload holds, for the logged lease time of a FortiGate lease (D6), which is known only once
  the lease is read: `RELOAD_DAYS` = 14 days with the last one (the API's `MAX_DAYS`), at most
  the FortiOS maximum, never less than the configured rules reach. With the defaults the days
  are `T`'s day − 12 to + 1 (− 11 to + 2 in the last two minutes before a midnight; without the
  setting − 8 to + 1), so a moment more than about 18 days old needs a reload, of 14 days.
  (b) No log row outside those days is read (`lookup.days.reads_from` to `reads_until`). A
  look-back reaching before the first day (the 100-day cap of the missed-move check, round 7; a
  logged lease longer than 14 days, through the lease's newest record, the post-lease checks
  and D27's `T − W`; the ARP interval after a RELEASE) reads from it, and the answer then
  carries the explanation `lookback_clamped_to_window` (TR/EN, `warning`, naming the first and
  the last day; its text speaks of looking back before the first day), and a lease whose newest
  record lies before the first day is read from its stored record. D27's read past the moment
  (`T + W`, W up to a logged lease time) ends with the last day **without** that explanation
  (#126, manager answer Q2 on #121): "D27 reads up to the end of the lookup's last day" is a
  method rule, documented in `docs/CORRELATION.md` (section 3 "Data window", sections 2 and 7,
  Özet). That cut fires on nearly every FortiGate lookup (FortiOS's 7-day lease) and on MikroTik
  lookups after about 21:36 (1 day × 110 %); a warning on almost every report, printed in the PDF
  under "Sorgu motorunun açıklamaları", would say nothing about the case. The bound
  is the lookup's own days, not the window start: those depend only on the request and the
  settings, so the cut is deterministic and the answer is the same before a drop and after the
  reload (it never changes silently), while a bound at the window start would move with "now".
  So in a lookup the 100-day cap of the missed-move check
  is never reached any more (about 12 days are read with the defaults), and the ARP interval
  after a RELEASE is cut too (not reachable today: a late-mapped RELEASE is read from its row,
  inside the days). The cost: the missed-move check reads only the lookup's days, about 12 days
  instead of round 7's 100, for every lookup; accepted by the manager on #121. The 3.8 guards of `worker/tests/test_lookup_performance.py` (not W5-G's
  file) measure the converted-row query and the 30-day post-lease query from the first day,
  with the explanation asserted and their row counts recomputed for that range; the event
  site's 30-day leases now store their newest ACK among their refs, as the lease build keeps it
  (the opening and the latest continuations).
- **W5-G-18: the search holds the partition lock from before its snapshot (G8; review point 2
  on #121).** A clarification of G8's "under the shared partition lock": a search and its next
  pages read in one REPEATABLE READ snapshot, taken by the transaction's first statement, and a
  lock taken in that transaction is granted after it (a step of `db.window` the search waited
  for would leave it the old cutoff and the dropped partitions gone: a partial day with
  `archived: null`). So the search takes `ARCIPART` shared at session level, in an autocommit
  statement of its own before that transaction (`archive.service.hold_window`;
  `check_window(held=True)`), and releases it before the rollback; when the release fails (an
  aborted transaction, a lost connection) the connection is invalidated, never returned to the
  pool holding the lock. The wait is part of `search.request_budget_ms`: the scan gets what is
  left, and a wait that uses the budget up answers 504 `search_timeout` (audited `outcome:
  timeout`; since W5-G-24 with the text of a lock wait). The lookup (READ COMMITTED) keeps the lock in its transaction as before. On any
  failure of the statement that takes the lock other than its timeout (`lock_not_available`) —
  a failure after the lock was granted, a cancelled statement, an interrupt or a cancelled task
  (`BaseException`), a lost connection — `hold_window` invalidates the connection too (#126 item
  4): whether the session lock was granted is not known, and the connection must not go back to
  the pool holding it. The error itself goes on unchanged (a failure of the invalidation is
  added to it as a note); the timeout keeps the connection, as before.
- **W5-G-19: a lookup's days never exceed one reload: bounds on the settings that widen them
  (#126 item 3, option (b)).** Before, a long configured lease (for example a 14-day
  `lease.server_seconds`) or a large `lookup.max_session_seconds` (no upper bound) made
  `lookup.days.lookup_days` longer than 14 days; once those days were dropped, the 409
  `archive_reload_required` asked for a reload that `POST /archive-reloads` refuses (422
  `archive_reload_too_large`). The settings that widen the days get upper bounds
  (`arcilogger_db.settings.INTEGER_RANGES`): `lookup.max_session_seconds` 60–172800 (2 days),
  `lookup.dns_lookback_seconds` 0–604800, `lease.default_seconds` 60–604800 and each server of
  `lease.server_seconds` the same (`LEASE_SERVER_SECONDS`), `lease.timeout_percent` 100–125
  (was 100–1000), `arp.interval_seconds` 60–604800; `lookup.tolerance_seconds` and the request's
  tolerance were already at most 3600. With every value at its bound, 2 × (3600 + 172800 + 60) +
  604800 × 125 % = 1,108,920 s stays under 13 days, so a lookup touches at most 14 site days
  (`RELOAD_DAYS`, the API's `MAX_DAYS`), also on the 23-hour days of before 2016; the logged
  FortiGate lease was already capped at the 14 days (W5-G-17 (a)). 604800 s is FortiOS's
  default `lease-time` (DEVICE-SETUP.md section 2.4; `UNCONVERTED_WINDOW`), so the bound equals
  that default, not above it; the bounds lie above the shipped values (a 1-day lease, the hourly
  ARP-LOG). MikroTik's own default lease time is not stated in the repository (**sahada
  doğrula**), and the 2-day session bound is to be verified on site with the session cap itself
  (`docs/MVP-CONTRACTS.md`, open items). A value above a bound is refused as
  `setting_out_of_range` ("{low} ile {high} arasında olmalı." / "Must be between {low} and
  {high}."), for a server of `lease.server_seconds` as `setting_lease_seconds` ("Her DHCP
  sunucusu için 60 ile 604800 saniye (7 gün) arasında bir süre gerekir." / "Each DHCP server
  needs a time between 60 and 604800 seconds (7 days)."); the browser checks the integer
  settings' `max`, the API each server. **Values stored before the bounds** are not migrated
  and are read as they are: a lookup whose days are in the database answers, and one whose days
  to bring back (the 409's days) fit one reload answers the 409. Only when the days to bring
  back are more than 14 (`lookup.days.check_reload`, `LookupReachError`) is the lookup refused,
  before the 409, as 503 `lookup_settings_invalid` (no new error code) with a third text that
  names the settings above their bounds (`lookup.days.above_bounds`) and the days: "5651 sorgu
  ayarları geçersiz: sınırının üstünde kayıtlı bir değer var ({settings}). Bu sorgunun
  veritabanında olmayan günleri ({first_day} - {last_day}, {days} gün) tek bir arşivden yeniden
  yüklemeye (en çok {max_days} gün) sığmaz. Sistem yöneticisi değeri sınırların içine
  çekmelidir (Ayarlar → Uygulama ayarları)." / "The 5651 lookup settings are invalid: a value is
  stored above its bound ({settings}). The days of this lookup that are not in the database
  ({first_day_en} - {last_day_en}, {days} days) do not fit one archive reload (at most
  {max_days} days). An administrator must bring the value within its bounds (Settings →
  Application settings)." Like the other two texts of that code it is not audited. Tests:
  `worker/tests/test_lookup_reach.py` (the bounds, a grid of the accepted values, the refusal at
  15 days and not at 14), `db/tests/test_settings_reach.py`,
  `api/tests/test_archive_api.py` (the 503, and the 409 and the answer with the same stored
  value).

- **W5-G-20: the search's `archived` names `keep_days` too (an addition to C23; #126 item 5,
  from the review of #123).** C23 put `keep_days` only in the `details` of 409
  `archive_reload_required`. A search that answers 200 with `archived` days, the most common
  case, now carries it as well: `ArchivedRange.keep_days`, the current `db.reload_keep_days` at
  the time of the answer (the same value and read as the 409's), so W5-J's dialog names the real
  keep time there too. Test: `test_the_409_names_the_keep_days_of_the_setting` sets 12 and finds
  it in the lookup's and the search's 409 and in the search's 200 `archived`.

- **W5-G-21: the window step locks the chunks it drops before `drop_chunks` (#133).** On
  TimescaleDB, `drop_chunks` locks the catalog rows of its chunks before their tables, while the
  compression policy (`compress_chunk`, added to every time-series hypertable by `0001_baseline`)
  locks a chunk's table before its catalog row. A `db.window` step that dropped a chunk the
  policy had opened ended in `deadlock detected` (CI run 41669326395, TimescaleDB job, two W5-G
  tests; the other process was the `Columnstore Policy` worker). `partitions.drop_before` now
  takes `LOCK TABLE … IN ACCESS EXCLUSIVE MODE` on every chunk that goes (`show_chunks …
  older_than`), in the step's transaction after `ARCIPART` and before `drop_chunks`, so it waits
  for an open chunk without holding any catalog row; the policy finishes and commits (it commits
  per chunk), or a recompression gives up with TimescaleDB's own 40001 and runs again next time.
  The locks are taken in chunk-id order (`_timescaledb_functions.chunk_id_from_relid`), the order
  in which a query's planning locks the chunks: in name order (`_10_` before `_9_`) the step
  deadlocked with a reader of every chunk (the unbounded `min(time)` of `usage` and of the
  lookup's window). `drop_chunks` itself locks by OID, the same order until the OID counter wraps
  around (checked on TimescaleDB 2.30.2: after a wraparound the planner keeps chunk-id order).
  The same chunks are locked as before, so the step's bound (W5-G-7) and the lock budget do not
  change, and the drop and its audit entry stay in one transaction. Plain PostgreSQL has no
  chunks and no compression policy: nothing changes there. Tests (`db/tests/test_partitions.py`,
  TimescaleDB with compression, two or three connections, steps ordered by polling `pg_locks`):
  `test_a_compression_that_has_opened_a_chunk_is_not_a_deadlock_for_the_drop` (a compression and
  a recompression; both fail with the deadlock without the change),
  `test_a_compression_that_comes_after_the_drop_has_locked_the_chunk_is_no_deadlock` (the other
  arrival order: the compression fails with `55P03`, "chunk deleted by other transaction"),
  `test_the_drop_locks_the_chunks_in_the_order_a_reader_does` (12 chunks; fails in name order).

- **W5-G-22: a reload's file checks run beside the ingest loop (#124, G4 live data first).** The
  claim's SHA-256 of every file it would reset, and a file's size check and `received_outside`
  read before its reset, run in a daemon thread per check (`IngestService._beside`) that only
  reads files; every decision and every transaction stays on the loop, which goes on loading live
  batches and acts on the result in a later round, in W5-G's order: the claim transaction only
  after every file passed (W5-G-4), a file's reset transaction only after its scan, then a fresh
  stream at offset 0. A failed check fails the request with `archive_file_unreadable` before
  anything of the file is deleted, and the size check is made again on the loop right before the
  file's transaction, so a file gone during the rounds since its scan is not reset (W5-G-5). A
  stop ends a check within one 1 MiB step (`CheckStoppedError`); nothing is decided and the next
  start claims the request again. A scan of a request that ends meanwhile gives up the same way.
  A reload's partitions are made one day a round (`_ensure_days`), also while the scan runs
  (one table a round, changed by #182 (W5-G-25)).
  Measured once each (4 vCPU, plain PostgreSQL 16, live feed 160 lines/s, a one-day reload of a
  5 M line `.xz` day with its claim hashing the file 18 times, as a 14-day request of one source;
  latency of a live line from its append to its commit; no reload: median 0.36 s, max 0.75–1.30
  s): with the day's rows already dropped, the worst delay went from 31.2 s to 0.82 s while the
  claim hashed and 2.54 s while the file was scanned (the reload's own batches while it loads are as
  before: at most 4.59 s, median 2.59 s, in the run with the day's rows present; 4.28 s, median
  2.78 s, before, over the first two minutes of its loading); with the day's rows still in the database (the window's first
  days), from 79.5 s (scan and reset transaction in one round) to 41.7 s. Bound: while a check
  runs, a live line waits for the normal batch cycle plus about one check step (≤ 50 ms) and the
  loop's share of the interpreter lock; the test bound is 5 s (`LIVE_BOUND`). Not covered by the
  bound, still on the loop: the reset transaction (38.7 s for a 5 M line file whose rows are
  present; up to three such files per source when a request reaches the window's first day;
  split into steps of the loop by W5-G-25, #124 stage 2, which measured and did not take a
  second database connection for it), one day's partitions per round (up to 0.4 s; one table's,
  about 30 ms, changed by #182 (W5-G-25)) and the waits of up to `PARTITION_LOCK_TIMEOUT` (5 s)
  for the partition lock in `_ensure_days` and the claim (half of `search.request_budget_ms`
  for the lock and the tables together, at most `PARTITION_LOCK_TIMEOUT_MS` 2 s: the 5 s → 2 s
  cap changed by #182 (W5-G-25), W5-G-24 (b)). Tests
  (`ingest/tests/test_archive_reload.py`):
  `test_live_data_is_loaded_while_a_reload_checks_its_files`,
  `test_a_stop_during_a_reloads_check_is_prompt`,
  `test_a_check_that_fails_beside_the_loop_fails_the_request_and_deletes_nothing` (each for the
  claim and the reset), `test_the_scan_of_a_reload_that_ended_gives_up`,
  `test_a_sealed_file_gone_after_its_scan_is_not_reset`,
  `test_a_reloads_partitions_are_made_a_table_a_round` (`..._a_day_a_round` before #182).
- **W5-G-23: the search export holds the partition lock as the search does (W5-G-18; #148,
  found on #145).** `POST /search/export` reads its rows in pages of windows of device time, each
  window a statement of its own, in one REPEATABLE READ snapshot, but took no partition lock. On
  plain PostgreSQL a `db.window` step still waited for it (its `LOCK TABLE messages IN ACCESS
  EXCLUSIVE MODE` conflicts with the export's lock on the parent table, while it held `ARCIPART`
  exclusively and so stalled ingest and new searches behind it); on TimescaleDB the step locks
  only the chunks it drops (W5-G-21), which the export had not reached yet: a chunk dropped
  between two windows was missing from the later one, and the file left out the day's first
  hours with no `-kismi`, no `X-Arci-Export-Truncated` and `truncated: null` in its audit entry.
  The export now reads under `reading(..., window=True)`: `ARCIPART` shared at session level
  from before its snapshot (`hold_window`), released before the rollback and so before the
  `search.export` entry is written and committed, which still happens before the file's first
  byte is sent; the failure handling of W5-G-18 is the same (the connection invalidated on any
  failure but the lock's timeout). The lock is waited for at most `search.request_budget_ms`, as
  by a search; the wait is taken from `search.export_budget_seconds`, and a wait that uses either
  up answers 504 `search_timeout` (no new code; its `{seconds}` is the budget used up; the
  export's text of a lock wait since W5-G-24). That
  answer writes no `search.export` entry: no file and no row left the API (the search itself is
  audited by `search.run`), and the hourly limit counts only files. Whatever takes `ARCIPART`
  exclusively (a step of the window job, the daily `partitions.ensure`, ingest's reload claim)
  now waits for a running export before it locks any table, for the rest of the export's read
  and the writing of its file: about `search.export_budget_seconds` (at most 900), plus the last
  page with its fill-in, the xlsx's writing and the SHA-256 pass, which come after the read's
  deadline. Searches that come while such a step waits queue behind its exclusive request and
  may answer 504 `search_timeout`; on TimescaleDB an export did not delay searches before (open
  question on #148: a `lock_timeout` for the worker's step, or a cap on how long an export holds
  the lock — answered by W5-G-24). On plain PostgreSQL ingest no longer stalls behind a step
  that waits for an export. Since #165 the export checks the window (W5-G-24). Tests
  (`api/tests/test_search_window_lock.py`, two connections ordered by polling `pg_locks`):
  `test_an_export_in_progress_is_not_changed_by_a_drop` (fails without the lock on TimescaleDB),
  `test_an_export_that_cannot_get_the_lock_in_time_times_out`,
  `test_the_export_takes_the_wait_for_the_lock_from_its_budget`,
  `test_the_export_releases_the_lock_before_its_audit_entry`,
  `test_a_failed_export_does_not_keep_the_lock`; `api/tests/test_search_export.py`:
  `test_the_audit_entry_is_committed_before_the_first_byte_is_sent`.
- **W5-G-24: the export checks the database window; the worker's steps back off from a long
  export; a lock-wait text of `search_timeout` (#165, the open questions of W5-G-23 on #148).**
  - **(a) The export checks the window (G8).** A step of `db.window` that finished between
    `POST /search` and `POST /search/export` left the export the rows the database still held,
    in a file that did not say days were missing (no `-kismi`, no `X-Arci-Export-Truncated`,
    `truncated: null`). Now, under the shared `ARCIPART` the export holds from before its
    snapshot (W5-G-23), in that snapshot and before its first window, the export checks its
    search's range with the search's own G8 function (`routers.search._archived(…,
    refuse=True)`, `check_window(held=True)`) and answers as a search of that range would now.
    **Every day gone** (and in the vault or being loaded): the search's 409
    `archive_reload_required`, with the same `details` and texts; no file is built and no
    `search.export` entry is written, as for the lock's 504 (the hourly limit counts files).
    **Some days gone:** the rows the database holds, in a file marked partial with the new
    reason `archived` (`Truncated` = `rows` | `time` | `archived`): the name `-kismi`,
    `X-Arci-Export-Truncated: archived`, the "Bilgi" status "Bazı günler veritabanında yok
    (mühürlü arşivde ya da yeniden yükleniyor) / Some days are not in the database (in the sealed
    vault or being loaded again)" and a line "Veritabanında olmayan günler / Days not in the
    database" (`YYYY-MM-DD`, or `YYYY-MM-DD - YYYY-MM-DD` for more than one day), and the entry's
    `truncated: "archived"`. The `search.export` entry gets a new key `archived`: the search's
    `ArchivedRange` (`{first_day, last_day, keep_days}`), or `null`. When the reading also
    stopped at a limit, `X-Arci-Export-Truncated` and `truncated` keep where it stopped (`rows`,
    `time`: one value, as before, so a client that reads them is unchanged), and the new header
    `X-Arci-Export-Archived` (`YYYY-MM-DD`, or `YYYY-MM-DD/YYYY-MM-DD`), sent whenever days are
    gone, names them: a CSV has no "Bilgi" sheet. Days gone with nothing in the vault to reload
    give an unmarked file, as the search answers 200 with `archived: null` there (G8 unchanged).
    Tests (`api/tests/test_search_window_lock.py`, the drop being the real `drop_before`,
    committed after `POST /search` and before `POST /search/export`, no concurrency):
    `test_an_export_after_a_drop_of_its_whole_range_answers_as_the_search` (the error equals a
    fresh search's 409; no entry; the lock free),
    `test_an_export_after_a_drop_of_part_of_its_range_is_marked_partial` (CSV and xlsx, the
    rows a fresh search returns, the entry's `truncated` and `archived`, and with
    `search.export_max_rows` 1 `rows` beside `X-Arci-Export-Archived`),
    `test_the_days_gone_are_named_once_for_one_day_and_as_a_span_for_more`;
    `api/tests/test_search_export.py` `test_csv`, `test_xlsx` (`archived: null`, no header, no
    line).
  - **(b) The worker's steps wait for the lock at most half the search budget.** Since W5-G-23
    an export holds `ARCIPART` shared for up to `search.export_budget_seconds` (at most 900 s). A
    `db.window` step or the daily `partitions.ensure` that asked for it exclusively meanwhile
    kept that request queued, and every search that came behind it waited for the lock and
    answered 504 `search_timeout`. Both now wait for it at most half of
    `search.request_budget_ms` (`partitions.step_lock_timeout_ms`: 2000 ms at the default 4000,
    250 ms at 500, 15 s at 30000; read at every step; a value unset or outside its range reads
    as the default), by `partitions.lock_partitions(timeout_ms=…)`, which applies `lock_timeout`
    to the lock's statement only and restores the transaction's previous value for its other
    lock waits. Half, because a search waits for the lock shared at most its own budget
    (W5-G-18) and PostgreSQL queues a new shared request behind a waiting exclusive one: a search
    queued behind a step that does not get the lock keeps at least half its budget for its
    scan, and a step still outwaits a running search that has less than half its budget left.
    This bounded only the step's wait for the export: when the lock came free during the wait
    (the export ends), the step took it, and a search queued behind it waited for the step as
    before W5-G-23 (its table locks after `ARCIPART`, W5-G-21's wait behind a compression; since
    #124 stage 2 those waits share the budget, below). A
    step that does not get the lock in time (`lock_not_available`, `PartitionLockTimeoutError`)
    changes nothing (the lock comes first in the step: no reload expired, nothing dropped, its
    transaction rolled back; earlier steps of the run stay), writes a warning line, and fails
    the run, which the worker retries (15 min,
    30 min, … at most 4 h). It is not recorded `skipped`: a skipped run counts as the day's and
    would not be retried until the next day. `db.window` opens `R20-db-window` (G3's key
    `R20-db-window:none`, medium, `details.error` `PartitionLockTimeoutError`) for such a run
    only when it is the `alerts.job_failures`-th (default 3) failed run in a row, counted as
    `R20-job` counts them (`db_window.failed_in_a_row`: finished runs of the last 7 days,
    `JOB_WINDOW`; a run a person started ends the count). Three failed runs in a row (about 0,
    15 and 45 minutes) mean the lock was held longer than one export holds it. Every other
    failure still opens it at once (G3), and the next successful run resolves it as before;
    `R20-job:db.window` (high) is unchanged. `partitions.ensure` has no alert of its own
    (`R20-job` after `alerts.job_failures` failed runs, unchanged); on TimescaleDB it has no
    partitioned table and takes no lock. Tests: `worker/tests/test_db_window.py`
    `test_the_steps_wait_for_the_lock_half_the_search_budget`,
    `test_a_step_backs_off_from_an_export_and_a_search_meanwhile_is_not_timed_out` (an export's
    shared lock, the step's exclusive request and a search queued behind it, ordered by polling
    `pg_locks`; the search is granted when the step gives up; nothing changed; the retry 15 min
    later does the work), `test_only_the_third_backoff_in_a_row_raises_r20_db_window`,
    `test_the_failed_runs_in_a_row_are_counted_as_r20_job_counts_them`,
    `test_the_partitions_job_backs_off_from_an_export`; `db/tests/test_partitions.py`
    `test_the_lock_of_a_step_is_waited_for_its_timeout_only`.

    **Since #124 stage 2 (the manager's comments on #124 of 2026-11-05 00:19 and 01:07): the
    table locks after `ARCIPART`, and ingest's exclusive takers.** A step that held `ARCIPART`
    waited for its table locks without a bound (`lock_timeout` 0): a reader that takes no
    partition lock (a plain `SELECT`, a backup) holding a table kept the step, and every search
    queued behind its `ARCIPART`, waiting. Now the wait for `ARCIPART` and the step's table
    locks after it share one budget, `partitions.LockBudget` (the same half of
    `search.request_budget_ms`): `LOCK TABLE` of a parent table (which locks its partitions with
    it) in `ensure_partitions`, `ensure_days` and `drop_before`, and of each chunk that goes on
    TimescaleDB (W5-G-21), waits at most what the waits before it left, and its wait is counted;
    the step's other statements (the partition DDL, `drop_chunks`, the deletes of the counts and
    clock samples) wait at most what is left when they come, the bound set again after each
    wait (none is expected to wait: the table locks cover them); only waits count, not the
    step's work. `lock_timeout` bounds each lock a statement waits for, not the statement:
    `LOCK TABLE` of a parent waits for the parent, then for each partition that a reader holds
    without the parent (one that reads a partition by its own name, which the searches and
    exports do not), each up to what was left when the statement began. A wait that runs out
    raises `PartitionLockTimeoutError`, the step's transaction rolls back (nothing changed) and
    the run backs off exactly as from `ARCIPART` (counted toward `R20-db-window` at the
    `alerts.job_failures`-th failed run in a row; `partitions.ensure` likewise); after the
    partition functions the transaction's `lock_timeout` is what it was. Only `ARCIPART` and the
    table locks are bounded: the window step's own statements around them (`expire_reloads`,
    reading the cutoff, `audit_drop` under its own advisory lock) keep the transaction's
    `lock_timeout` (0); their holders are short. On TimescaleDB a step that meets the
    compression policy recompressing a chunk it drops (W5-G-21) now backs off after the budget
    too and tries again 15 min later. Ingest's exclusive takers, the reload claim and
    `_ensure_days` (`ingest/src/arcilogger_ingest/service.py`; before: `PARTITION_LOCK_TIMEOUT`
    5 s, above the default search budget of 4 s: the open question on #165), wait for
    `ARCIPART` and then for their tables (the claim for its request's row) the same half of
    `search.request_budget_ms` in all, read from the settings in their transaction
    (`IngestService._lock_budget`; a day's tables, a transaction each, share one budget; a
    round each: changed by #182 (W5-G-25)), and never longer than `PARTITION_LOCK_TIMEOUT_MS`
    (2000, the default's half; the 5 s → 2 s cap changed by #182 (W5-G-25)): the loop waits, so
    a budget of 30 s does not hold live data for 15 s, and a stop during the wait stays within the
    4.5 s of a Windows console stop (W5-G-25). On a timeout the loop goes on with live
    data: the claim is kept with its checked files and finished again after `archive_seconds`
    (5 s; its status and loaded files are read again under the lock then), so its files are not
    read again while an export runs; the request stays `queued`, not failed (`--once` ends, the
    next start claims it). `_ensure_days` returns `False` and asks for no partition for the
    next `archive_seconds`, so the loop, and searches behind its request, are held at most the
    budget every 5 s while an export runs, not every round (changed by #182 (W5-G-25): twice as
    long after each wait that runs out, up to `PARTITION_RETRY_MAX_SECONDS` 60 s). Tests:
    `db/tests/test_partitions.py`
    `test_a_steps_lock_waits_share_one_budget` (both back ends: an export holds `ARCIPART`, a
    plain `SELECT` the table or chunk; the step waits 1 s for the lock, then the rest for the
    table: 2 s in all, not 3 s; nothing dropped; `lock_timeout` restored),
    `test_ensure_days_waits_for_the_lock_and_a_table_within_one_budget`,
    `test_the_statements_after_a_lock_wait_get_what_is_left`,
    `test_a_budget_used_up_still_bounds_the_wait` (what is left is at least 1 ms: 0 is no limit
    to PostgreSQL); `worker/tests/test_db_window.py`
    `test_a_step_backs_off_from_a_table_a_reader_holds_and_a_search_is_not_timed_out`;
    `ingest/tests/test_archive_reload.py`
    `test_a_reloads_partitions_back_off_from_an_export_and_a_search_is_not_timed_out`,
    `test_a_claim_backs_off_from_an_export_and_a_search_is_not_timed_out` (ordered through
    `pg_locks` as the worker's, at the default budget: the search is granted within its 4 s; the
    kept claim is not hashed again), `test_a_claim_backs_off_from_its_requests_row_held`,
    `test_a_days_tables_share_one_lock_budget`,
    `test_ingest_waits_half_the_search_budget_at_most_two_seconds`.

    **A long backup (sahada doğrula).** `pg_dump` (the Backup task, daily at 01:40, YEDEKLEME/BACKUP
    §2) holds `ACCESS SHARE` on every table and chunk it dumps until it ends. A `db.window` run
    (daily after 04:00 then, 01:00 since W5-G-27) that meets a dump still running used to wait for
    the rest of the dump with `ARCIPART` held, so every search answered 504 until it ended; now the
    step backs off after the lock budget and tries again (15 and 30 minutes later, …), and if a
    dump still runs at the `alerts.job_failures`-th (3rd) run, `R20-db-window` opens (medium);
    `partitions.ensure` backs off likewise (`R20-job` at the 3rd failed run). An archive
    reload's partitions during the dump are tried after `archive_seconds` (5 s), then 10, 20, 40
    and every 60 s (`PARTITION_RETRY_MAX_SECONDS`; back to 5 s once a table is made; #182 review
    Q7, W5-G-25), each time holding `ARCIPART` up to 2 s (searches wait, they do not time out).
    How long a dump takes on site is to be checked (sahada doğrula). The owner's answers on #182
    (review Q6): the overlap keeps counting toward `R20-db-window` (three nights in a row is a
    real, lasting problem; hiding it is worse); whether `db.window` should run away from the
    backup was follow-up #188, not #182.

    **The default times (#182 review Q6, #188).** At 04:00 `db.window` lay inside the dump's
    default window of 01:40–05:10 (`DUMP_TIMEOUT_SECONDS`, 3 h 30 min). Since #188 it runs daily
    after 01:00, before the dump at 01:40, and only its rare second retry (01:45) or a dump
    started late can still meet one: W5-G-27.
  - **(c) A lock-wait text of `search_timeout`** (504, the same code; approved on #165). The
    contract's text of `search_timeout` ("Arama {seconds} saniyede en kısa zaman dilimini (5
    dakika) bile tarayamadı. …" / "The search could not scan even the shortest time slice (5
    minutes) within {seconds} s. …") stays for a scan that ran out. When the partition lock was
    not granted within `search.request_budget_ms`, or its wait used the request's budget up
    (`routers.search.LockWaitError`), nothing was scanned, and a search and its next pages answer
    with the variant `lock` (`search.messages.VARIANTS`): "Arama {seconds} saniyede başlayamadı:
    veritabanı bakımının (eski günlerin kaldırılması, yeni bölümlerin açılması ya da arşivden
    yeniden yükleme) bitmesi beklendi. Biraz sonra yeniden deneyin." / "The search could not
    start within {seconds} s: it waited for database maintenance (old days being dropped, new
    partitions being created or archived days being loaded again) to finish. Try again a little
    later." The export's 504 is always the variant `export_lock` (its read stops at its deadline
    with a `time` file, so its 504 is a lock wait): "Dışa aktarma kendisine tanınan {seconds}
    saniyede başlayamadı: veritabanı bakımının (eski günlerin kaldırılması, yeni bölümlerin
    açılması ya da arşivden yeniden yükleme) bitmesi beklendi. Biraz sonra yeniden deneyin." /
    "The export could not start within the {seconds} s it was allowed: it waited for database
    maintenance (old days being dropped, new partitions being created or archived days being
    loaded again) to finish. Try again a little later." `{seconds}` is the budget used up, as
    before: `search.request_budget_ms` when the lock was not granted within it, or
    `search.export_budget_seconds` when the wait used that up (`_budget_left`), which is not the
    wait; so the text names it as the time the export was allowed, true of either (#124 stage 2;
    it read "could not start within {seconds} s" before). The audit of a search's timeout
    (`outcome: timeout`) is unchanged. Tests (`api/tests/test_search_window_lock.py`):
    `test_the_lock_wait_texts_are_turkish_and_english_with_the_same_placeholders`,
    `test_a_scan_that_runs_out_keeps_the_text_of_the_time_slice`, and the exact texts in the
    lock-timeout and wait-from-budget tests of the search and the export.
- **W5-G-25: a reset deletes its file's rows in steps on the loop (#124 stage 2, G4 live data
  first).** The reset transaction of W5-G-6 (38.7 s for a 5 M line file whose rows are present,
  W5-G-22) is split, one file at a time. Its first transaction locks the file's
  `ingest_offsets` row, sets its offset to 0 and marks it (`counts_from_line = 0`, a value no
  file has otherwise: a file loaded before `0016_search` is marked from line 2 on; it also makes
  a search count the file's hours from the rows, `searchcounts.verify` leave it out and
  `search.counts_backfill` wait; kept as the mark, #182 review Q3, and said so on
  `IngestOffset.counts_from_line`, a comment, no migration), deletes its parse failures, its site
  day's clock samples and the file's part of the samples of the minutes its lines were received in
  outside its day (only in the transaction that sets the mark: one taken up again leaves them), on
  plain PostgreSQL its rows in the default partitions outside its time window, and its
  `search_counts` of the hours no step reaches. Then its rows (all 16 tables, by `(source,
  raw_day)`) are deleted in steps of device time, the reach of its stray receive times first and its
  time window last, between rounds of the loop (`RESET_ROUND_SECONDS` 0.25 s of steps a round, at
  least one; live batches between): each step a transaction of its own of at most `RESET_STEP_LINES`
  (10 000) lines by the `search_counts` of every file in its hours (what its deletes read: the
  `fg_*` tables have no index by source), an hour of more lines split evenly, with the file's counts
  of the hours it finishes. Each step first locks the offsets row and compares it as the loader does
  (`byte_offset = 0`, `line_number = 0`, unfinished): a file whose offset has moved (two instances,
  one with its lock connection down) is not touched, the offsets are read again and a file still
  marked is reset again from its first transaction. With two instances while the lock is lost, the
  clock samples of the file's lines received outside its day can be counted twice (#182 review 3):
  derived data, which `clocksamples.rebuild` counts again from the rows (a function; no command
  calls it yet, DATA-MODEL). The last step takes the mark off (the offsets row first, the loader's
  order) and deletes the file's counts left; only then is the file a stream at offset 0. From before
  its first transaction on the file is no stream (its batches in the queue are dropped), so no batch
  of it is read or committed under the mark. A marked file is never a stream (start-up, spool and
  vault scan) and never counts as loaded (its offset is 0). A start that finds one resets it again
  from its first transaction: for its request when that is still `loading` (the claim), or on its
  own when no request holds it (failed or changed by hand meanwhile: scan beside the loop,
  partitions, size check, steps; none is begun while a request is `loading`, and no request is
  claimed while one is checked). A file that cannot be read while its day is still sealed
  (missing, changed, damaged, its drive not ready) stays marked and is tried again after
  `vault_scan_seconds`, the others meanwhile, reported once a run; it may be put back, and then it
  loads exactly once. A file whose day vault retention has deleted was tried again the same way,
  with a warning each time, and stayed marked and unfinished for good (`search.counts_backfill`
  counting it `waiting`; #182 review 1). Now, when its day's seal state says `deleted`
  (`_gone_from_vault`, read afresh; retention deletes the day's files first and marks the state
  last), its reset is finished without the file, reported once: with no receive times to read, the
  hours that reach outside its window that its `search_counts` still name stand for them
  (`_counted_outside`; those of the steps already made went with their rows; a line its counts
  never told, of a file loaded before `0016_search`, is not found and its row stays until the window
  job drops its day), then its window, and its last step deletes its offsets row instead of taking
  the mark off. No stream follows: nothing loads a day that is not sealed (neither the vault scan
  nor a request), so no later start looks at it again. Only that day is given up: a sealed file
  missing from a day still `sealed` (an evidence incident, or a retention run cut short), or a state
  that is missing or cannot be read, waits as above (#182 review round 2: retired there, a file
  loaded before `0016_search` was loaded again by the vault scan once its file was back, with the
  row its counts never told twice). Accepted: a deleted day put back by hand as `sealed` brings
  that case back for a file retired with it (within the vault scan's horizon its row twice; asked
  for, its rows once but that row's clock sample taken off twice: derived data,
  `clocksamples.rebuild`). A step over rows already deleted deletes nothing, so a stop after any
  transaction re-resets the file and loads it exactly once. `arcilogger-db search-counts rebuild`
  refuses a file marked `0` and unfinished (exit 1, a TR/EN text, nothing written; it only takes the
  offsets row lock), and `search-counts status` lists it as "sıfırlanıyor [resetting]" (#182 review
  Q1, `db/src/arcilogger_db/cli.py`). So only the reset's last step takes the mark off. Before, a
  rebuild between two steps counted the rows still there and took the mark off, which left one case
  open: a file whose every line was received outside its day. Its window goes last, so a rebuild
  between its first steps found none of its rows, left no counts and took the mark off; a stop
  before its next step then left a file at offset 0 with no mark and no counts, which looks like a
  fresh stream, and the rows not yet deleted could be loaded a second time. That double load can no
  longer come through the command. Ingest's recount guard stays, a second line of defence for a mark
  taken off by hand in SQL: an unfinished file at offset 0 that has counts is a reset all the same
  (`_recounted`; a file loads its rows and counts in one transaction with its offset, and the last
  step deletes them with the mark), and the next step puts the mark back; a mark taken off by hand
  from such a file with no counts left is not caught (the case above). A step waits at most
  `RESET_LOCK_TIMEOUT` (1 s) for a lock (on TimescaleDB the compression policy recompressing a
  chunk the step deletes from, a rebuild holding the offsets row) and is made again in a later
  round. Steps are planned from the counts as they are: a file whose counts were not complete
  (`counts_from_line` set before the reset) is stepped at most `RESET_CAPPED_STEP` (5 minutes)
  in its time window. A reset taken up again with the file still marked is capped as well: the mark
  took the place of that value, so whether its counts were complete is no longer known (#182 review
  2: such a file taken up after a crash was stepped by counts that do not cover its lines, a step
  far above `RESET_STEP_LINES`; about 890 steps of 5 minutes for its 74 h window, most of them
  empty). One whose mark was taken off by hand is planned by its counts as
  they are (larger steps at worst, never other rows). A reset of a failed request's file taken up on
  its own makes its partitions and loads it although no request holds it, as after a crash before
  (the window job drops it again; #182 review Q4: accepted, no raw data is touched), unless vault
  retention has deleted its day (above). Two items of the review of #159 come with it: a claim is
  accepted when the files loaded at its check are still loaded (a file loaded since, a stream that
  finished meanwhile, is left alone, not hashed again); and while a claim's files are checked its
  request's status is read every `ARCHIVE_PROGRESS_SECONDS`: once it has changed, the check stops at
  its next slice and the next request is claimed.

  Two designs were measured (4 vCPU Linux, PostgreSQL 16 / TimescaleDB 2.30.2, the real ingest
  loop with 2 parse workers, live feed 160 lines/s; latency of a live line from its append to
  the commit that covers it; a 1 M line FortiGate day loaded by ingest, about 2 M rows in 16
  tables; the "5 M" day is its rows copied four more times into the same hours, counts × 5;
  "compressed": every chunk compressed, as the window's first days are). The second design, the
  same one transaction on a second ingest connection beside the loop, was not taken: on plain
  PostgreSQL it holds `RowExclusiveLock` on the 16 parent tables for the whole delete, and a
  partition DDL that arrives meanwhile (the worker's `partitions.ensure`, a `db.window` step,
  ingest's own `_ensure_days`: `ACCESS EXCLUSIVE` on the parent) waits for it while every later
  live `COPY` into that table queues behind the waiting request: a live line waited 4.86 s and
  5.08 s during a 6.8 s delete of a 1 M line file (up to about 38 s for 5 M). On TimescaleDB it
  did not wait (`drop_chunks` 0.07 s, a new chunk 0.06 s). Taking `ARCIPART` shared beside it
  would move the queue to searches and exports (504). The steps hold every lock for one step:
  the window job, `partitions.ensure`, `ARCIPART` and the single-instance lock behave as
  before, and ingest keeps its two connections. Measured with the implementation (once each;
  the reset's time, then live latency median / max around it; no reset: 0.30–0.39 s / 0.54–1.33 s;
  the synthetic 5 M day resets faster than stage 1's real one, 38.7 s):

  | Back end, file whose rows are present | One transaction (before) | In steps (W5-G-25) |
  |---|---|---|
  | PostgreSQL, 1 M lines | 4.5 s: 2.60 / 5.24 s | 7.3 s, 106 steps (median 42 ms, max 283 ms): 0.50 / 1.18 s |
  | the same with a `db.window` step / a day's partitions beside it | – | 0.51 / 1.31 s (drop 0.20 s) / 0.51 / 1.22 s (ensure 0.42 s) |
  | PostgreSQL, 5 M lines | 25.4 s: 14.01 / 26.74 s | 39.0 s, 514 steps (median 48 ms, max 379 ms): 0.52 / 1.29 s |
  | TimescaleDB, 1 M lines | 4.5 s: 2.64 / 5.30 s | 6.1 s, 106 steps (median 34 ms, max 57 ms): 0.49 / 1.10 s |
  | TimescaleDB compressed, 5 M lines | 1.7 s: 1.16 / 2.36 s | 41.5 s, 514 steps (median 67 ms, max 145 ms): 0.50 / 1.12 s |

  The steps take about 1.4–1.6 times as long as the one transaction, uncompressed. On compressed
  chunks a step scans the chunk's compressed batches of the source whatever its size (it deletes
  whole batches directly and decompresses only those at its edges): about 24 times the one
  transaction there, still small next to the file's load (about 19 minutes for 5 M
  lines). Fixed one-hour steps are not enough: a 5 M line day's peak hour holds about 545 000
  lines (5–8 s). Bound: a live line waits at most one round of steps (0.25 s and the step that
  ends it: up to about 1.2 s measured for an I/O spike) and a lock wait of up to 1 s; the test
  bound is 5 s (`LIVE_BOUND`). Not covered, still on the loop: a reload's partitions, one table
  of a site day a round since #182 (review Q2): a transaction of two partitions (its device
  times lie in two UTC days), 34 ms measured on Linux, where the day's 16 tables in one round
  took 0.3–0.45 s on Linux and 0.844 s on a Windows runner (on the runners of #159 a round took
  long enough that the latency tests could not count their rounds). A day's tables share one
  lock budget over their rounds (`DayPartitions`; a fresh one after it ran out), and the vault
  scan, every `vault_scan_seconds`, still makes a day in one call (`whole`). While the lock
  stays held (an export holds it up to 15 minutes, W5-G-23), the partitions are asked for again
  after `archive_seconds` (5 s), then twice as long after each wait that ran out, up to
  `PARTITION_RETRY_MAX_SECONDS` (60 s), and after 5 s again once a table is made (#182 review
  Q7: at a flat 5 s a 15-minute export meant about 130 asks, each holding the loop, and queueing
  searches, up to 2 s). `test_live_data_is_loaded_while_a_reload_checks_its_files` prints the
  longest round in the CI log's warnings summary. Moving that DDL off the loop would not help
  (its `ACCESS EXCLUSIVE` on the parent holds the loop's `COPY` all the same); one table-day a
  transaction came with the prompt stop below (`partitions.ensure_days(..., tables=)`). One
  table's round is not measured on Windows or a spinning disk (sahada doğrula, KURULUM/INSTALL
  §16).

  **A prompt stop (the manager's comment on #124 of 2026-11-05 01:07).**
  `test_a_stop_during_a_reloads_check_is_prompt[reset]` took 2.016 s against its 2.0 s bound on
  a Windows runner (run 41933802996, before stage 2). The stop waits for the loop's work under
  way, nothing beside it (a check thread gives up within one 1 MiB slice, W5-G-22); while a
  reset's scan runs, that work is the request's next day's partitions, which were one
  transaction of 16 tables (0.27–0.32 s measured here at the moment of the stop; several seconds
  on the Windows runners). `partitions.ensure_days` now takes `tables=`, and `_ensure_days`
  makes a day's partitions one table a transaction, the stop looked at before each (one table a
  round since #182 review Q2, above); a reset's round of steps looks at the stop after each step;
  and once the service stops, no unit of work that waits for a lock is begun: not a reset's first
  transaction (for the request's next file or for a file marked on its own), not a claim of a
  request, nor the end of a claim whose files are checked. A reset's first transaction (it sweeps
  the default partitions, which a `db.window` step holds with its parent) waits for a lock at most
  `RESET_LOCK_TIMEOUT` (1 s) as a step does, and is made again in the next round. Worst case of a
  stop, analytically: the single unit of loop work under way when it comes, then the shutdown's one
  batch and the last status write (no fsync, below). That unit is one of: one table's partitions
  (about 30 ms; 1/16 of a day's) plus its wait for `ARCIPART` and the table (none, or at most the
  lock budget of W5-G-24 (b), 2 s, while an export or the window job holds the lock or a reader the
  table); one step or a reset's first transaction (at most 0.38 s measured, about 1.2 s on an I/O
  spike) plus its lock wait (at most `RESET_LOCK_TIMEOUT`, 1 s); the claim's transaction (a few
  reads and writes, plus the same lock budget); one status write (no fsync, below); or one live
  batch as before. A stop does not cut short a lock wait under way (that would cancel the statement
  from another thread); the lock budget is never more than `PARTITION_LOCK_TIMEOUT_MS` (2 s)
  whatever `search.request_budget_ms` is, so the worst case is about 2.2 s and one batch, within the
  4.5 s of a Windows console stop by design. The budget bounds each lock a statement waits for, not
  the statement (`partitions._lock_table`, W5-G-24 (b)): `LOCK TABLE` of a parent waits for the
  parent, then for each partition a reader holds without the parent, each up to what was left when
  the statement began (`RESET_LOCK_TIMEOUT` is per lock too). "At most 2 s" assumes that readers
  reach the partitions through the parent, as the searches, exports, lookups, ingest, the worker and
  `pg_dump` (which locks the parent too) do; a session that holds partitions by their own names
  without the parent and lets each go within the budget (a hand-written query) can hold one table's
  statement up to 2 s for each such partition. Without a lock held the stop waits for one table's
  partitions or one step: 0.006–0.033 s measured here on plain PostgreSQL for the reset's check
  and 0.002–0.011 s for the claim's, 0.002–0.005 s on TimescaleDB, so the test's 2.0 s bound
  (no lock held) holds by design, not by a hair. The test reports the time as a warning, which
  the CI log's warnings summary shows on a passing run too (`-n auto` drops a passing test's
  output), and names it when it fails. Tests:
  `test_a_stop_while_a_days_partitions_are_made_waits_for_one_table`,
  `test_a_stop_during_a_resets_steps_ends_its_round` (a round of 30 s: without the look at the
  stop it made every step), `test_no_reset_is_begun_once_the_service_stops` (the request's next
  file and a file marked on its own), `test_no_claim_is_begun_or_finished_once_the_service_stops`,
  `test_a_resets_first_transaction_waiting_for_a_lock_is_made_again_later`.

  The status file was the rest of a stop (Windows run of `d7b6830`: the claim's stop took
  1.359 s, the reset's 0.031 s; 0.001–0.002 s on Linux). Every write of `ingest-status.json`
  ended in an fsync (`fsutil.atomic_write_text`; on POSIX two, the file and its folder), which
  waits for the disk's write-back, and a stop waited for up to three of them: the write under
  way, one more of the round (due again whenever the last took longer than the round's wait;
  the tests' `status_interval` is 0.05 s) and the last one (`stopped`). Neither the check nor
  the database was waited for: slower statements (30 ms each, 60 ms a `CREATE`) added only the
  unlock's round trip (0.31 s), CPU, I/O and PostgreSQL load did not reproduce it, and with
  every fsync 0.2 s slower on Linux the claim's stop took 1.339 s (the reset's 1.190 s), the
  trace showing the three writes; on the Windows runner each took about 0.45 s (an inference:
  its fsync was not measured). The status file is now replaced atomically without an fsync
  (`status.replace_text`, the same `.ingest-status.json.tmp` renamed over it): it is a report
  for monitoring, not evidence, written again every 5 s. The file is replaced atomically, not
  fsynced: a monitoring report; after a crash it may be empty or old and reads as missing until
  the next write (NTFS may keep the rename but not the data). The ingest service's
  `read_status` reads it as `None` and the API's System page as `missing`, and neither fails:
  `ingest/tests/test_reload.py::test_a_status_file_a_crash_left_empty_or_half_reads_as_missing`
  (empty and half a file). 5651 asks nothing of the file. The collector's and the worker's
  status writes stay as they are: no stop path waits on them, and nothing was measured. And once the
  service stops its loop writes none of its own, only the last one. A stop now waits for no fsync:
  with every fsync 0.2 s slower, 0.002–0.006 s (claim) and 0.009–0.027 s (reset), three runs, none
  of the trace's fsyncs; with 2 ms a statement and 50 ms a `CREATE` too, 0.004 / 0.014 s.
  Had the cost of a write on Windows lain in the file's close or rename (a virus scan, say)
  rather than in the fsync, a stop would still wait for two writes, the one under way and the
  last (on Linux with every rename 0.4 s slower, 0.78 s instead of 1.19 s). So both tests report
  beside the stop's time the status writes that ended after it and the part of its time they
  took, in the warnings summary. The Windows runner's run of 6a5c779 settles it: a stop during
  the claim check 0.015 s, one status write after it, 0.015 s; during the reset check 0.000 s;
  on the slowed disk 0.015 / 0.000 s (Linux: 0.001 / 0.002 s).
  Tests:
  `test_a_stop_during_a_reloads_check_waits_for_no_fsync` (every fsync 1 s slower, every
  statement 2 ms and every `CREATE`/`ALTER`/`DROP` 50 ms: less than one fsync, the status file
  `stopped`, and as in the test above the request still `queued` or `loading`, its offsets as
  they were and carried out exactly once at the next start; before, 5.99 s: three writes of
  two fsyncs), and in `ingest/tests/test_reload.py`
  `test_once_the_service_stops_its_loop_writes_no_status_but_the_last` (the stop at the loop's
  wait, every round's write due: before, the round's write after the stop too).
  A check whose service has stopped also opens no further file: the claim's check looks at the
  stop before each file, not only between two slices of one, and a reset's check before its
  file (#182 review: a check that outlived its stopped service by a file once ran into the next
  test's patched check, two check threads on a Windows runner). Tests:
  `test_a_claim_check_whose_service_stopped_opens_no_further_file` (the stop between two files:
  the second is not opened; before, it was), and a module fixture that fails a test whose check
  thread outlives it, by name.

  Tests
  (`ingest/tests/test_archive_reload.py`, both back ends):
  `test_a_resets_steps_hold_their_budget_of_lines`,
  `test_live_data_is_loaded_while_a_reset_deletes_a_files_rows` (held on an Event between two
  steps: a live line within `LIVE_BOUND`, the file marked, partly deleted, no stream, its sealed
  file not opened, not loaded; a `db.window` step and a day's partitions beside it with
  `lock_timeout` 1 s),
  `test_a_crash_after_each_step_of_a_reset_re_resets_and_loads_exactly_once` (after each
  transaction, for the request and on its own),
  `test_a_reset_whose_offset_has_moved_is_not_carried_on`,
  `test_a_batch_loaded_under_the_mark_begins_the_reset_again`,
  `test_a_recount_during_a_reset_does_not_load_its_file_twice` (a mark taken off by hand; while
  running, after a crash for the request and on its own; ingest's `RESETTING` is the CLI's),
  `test_a_first_transaction_whose_answer_is_lost_is_made_again`,
  `test_one_unreadable_file_does_not_hold_up_another_files_reset`,
  `test_a_file_of_a_loading_request_is_not_reset_on_its_own`, `test_one_file_is_reset_at_a_time`,
  `test_a_step_waiting_for_a_lock_is_made_again_in_a_later_round`,
  `test_a_file_loaded_during_the_claim_check_is_not_hashed_again`,
  `test_a_request_changed_during_its_claim_check_stops_reading`,
  `test_a_stop_while_a_days_partitions_are_made_waits_for_one_table`,
  `test_a_stop_during_a_resets_steps_ends_its_round`,
  `test_no_reset_is_begun_once_the_service_stops`,
  `test_no_claim_is_begun_or_finished_once_the_service_stops`,
  `test_a_resets_first_transaction_waiting_for_a_lock_is_made_again_later`; since the review
  of #182, `test_a_marked_file_whose_day_was_deleted_ends_its_reset_and_is_not_tried_again`
  (its day deleted as retention does, within the vault scan's horizon: its rows, one received
  outside its day found by its counts, its counts and its offsets row deleted, reported once,
  scanned once over two starts; put back and asked for, loaded exactly once; of a file loaded
  before `0016_search` that row stays, and once asked for its clock sample is taken off twice
  until `clocksamples.rebuild`; fix 1),
  `test_a_marked_file_missing_from_a_sealed_day_waits_and_loads_once_put_back` (both kinds of
  file, the day within the vault scan's horizon: marked, no stream, its rows as they were, tried
  again, reported once; put back, loaded exactly once with no request; fix 1, review round 2),
  `test_a_reset_taken_up_again_keeps_the_step_cap_of_a_file_loaded_before_the_counts` (fix 2),
  `test_a_reloads_partitions_ask_for_the_lock_less_often_while_it_stays_held` (5, 10, 20, 40,
  60, 60 s, then 5 s again after a table is made; Q7), and in `db/tests/test_cli.py`
  `test_search_counts_rebuild_refuses_a_file_being_reset` (exit 1, both texts, `ingest_offsets`
  and `search_counts` unchanged; `status`'s summary and resetting line; the mark taken off, it
  rebuilds; Q1) and
  `test_search_counts_rebuild_waits_for_the_offsets_row_and_then_refuses_a_reset` (a reset's
  first transaction holds the row while the rebuild starts and marks the file: the rebuild
  waits, then refuses, `search_counts` unchanged; Q1, review round 2); changed,
  `test_a_reloads_partitions_are_made_a_table_a_round` (named
  `..._a_day_a_round` before #182 review Q2: one table a round, the vault scan's day in one
  call), `test_a_days_tables_share_one_lock_budget` (over two rounds),
  `test_a_stop_while_a_days_partitions_are_made_waits_for_one_table` (the round after the stop
  makes none), `test_a_reloads_partitions_back_off_from_an_export_and_a_search_is_not_timed_out`
  (a table a round after the lock comes free),
  `test_live_data_is_loaded_while_a_reload_checks_its_files` (prints the longest round),
  `test_a_stop_during_a_reloads_check_is_prompt` (reports its time); unchanged, the equality
  tests
  (`test_a_dropped_day_comes_back_identically`, `test_a_reload_run_twice_gives_the_same_rows_and_counts`,
  `api/tests/test_archive_api.py` G7 and G8) and the throughput gate.
- **W5-G-26: the missed-move check's MAC branch tests its rows with a boolean `CASE` (#185).**
  `Attributor._missed_moves` writes its conversion test (`trust.kept`, `CASE … END IS NULL`) in
  the MAC branch as `CASE WHEN <kept> THEN true ELSE false END`: the same rows (`IS NULL` is
  never NULL), the same parameters, the address branch and the constant MAC list unchanged.
  Why: PostgreSQL has no statistics on the expression and estimates `IS NULL` of it at 0.005,
  a boolean expression at 0.5. The chains' ACKs (5,699 in the #179 guards' world) were
  estimated at 26 (86–91 at five times the rows), also after `ANALYZE fg_dhcp` and every
  partition; on the guards' layout (16 daily partitions, the older days in `fg_dhcp_default`,
  which a site reaches only while its worker's partition step is down for more than about a
  week, or until that step's first run after restoring an older backup; late or skewed lines,
  reloads and an install or upgrade put no window day there) a nested loop
  and a hash join then cost the same within the planner's 1 % fuzz, and it took the nested loop,
  which filtered every ACK against each of the 100 windows (564,201 rows removed). Wrapped,
  they are estimated at about 2,870 (9,100 at five times) and the branch hashes on the MAC on
  every plain-PostgreSQL layout measured. Medians on PostgreSQL 16.15 (Linux), before → after,
  MAC branch alone / whole statement: guards' layout 73.5 → 23.1 / 80.2 → 26.9 ms (1 M lines a
  day, as loaded), 72.2 → 20.7 / 72.1 → 25.7 ms (5 M, after `ANALYZE`); daily partitions (hash
  join before and after) 28.6 → 33.2, 23.1 → 23.4, 25.3 → 25.2, 25.4 → 27.9 ms and monthly ones
  19.6 → 21.3, 23.9 → 17.4, 19.9 → 21.4, 21.7 → 20.7 ms (1 M and 5 M, as loaded and after
  `ANALYZE`: within noise, best times within 1 ms). On CI (#187 at `2ebe2dc`), both forms timed
  side by side in the same transactions, alternating, both prepared: windows-latest (PostgreSQL
  17.11) guards' layout at 1 M nested loop → hash join, MAC branch 97.6 → 23.6 ms, whole
  statement 124.5 → 64.1 ms (as loaded; 98.8 → 24.4 and 119.6 → 61.4 after `ANALYZE`); at 5 M,
  and on daily and monthly partitions, both forms hash-join and stay within noise (MAC branch
  18.7–25.7 ms either way). The whole statement runs under a parallel Gather (2 workers) in the
  same worlds with either form: the wrapper does not cause it. TimescaleDB (2.30.2-pg16,
  one-day chunks): both forms hash-join, MAC branch 19.6 → 19.6 / 21.0 → 21.1 ms, whole
  statement 23.7 → 24.0 / 25.1 → 24.9 ms (1 M / 5 M, as loaded; the same after `ANALYZE`): no
  loss.
  Other forms measured and not taken: `IS NOT TRUE` of the refusal (the nested loop stays),
  `LATERAL` per window (fast, but drops the measured MAC list and reads 100 chunk appends on
  TimescaleDB), an `OFFSET 0` fence (7–11 ms slower on daily partitions at 5 M). An
  overestimate costs a hash build at worst; a planner that folds the wrapper back to the bare
  test would bring the 0.005 back, so `worker/tests/test_missed_moves_plan.py`
  `test_the_mac_branch_filters_no_ack_per_window` (`perf`, so `test-perf` on both CI runners,
  Linux with TimescaleDB and Windows; the guards' 400 days of ACKs, the fewest with which the
  bare test nested-looped in every run on PostgreSQL 16, and 100 chains, about 15 s)
  checks the two estimates against the rows and that the branch, on every layout it builds
  (TimescaleDB included), is no Nested Loop whose join filter removes rows, read from
  `EXPLAIN (ANALYZE, FORMAT JSON)`; the join taken instead (a Hash Join today, a Merge Join or
  another later) is not pinned. The bare test fails it on PostgreSQL 16 (Nested Loop, 564,201
  removed).
  `worker/tests/test_missed_moves_measure.py`, the measurement (both forms side by side), is
  opt-in (`ARCILOGGER_PERF_185=1`), so the default `test-perf` does not run it. G7 and the
  lookup tests are unchanged.

- **W5-G-27: `db.window` runs before the backup (#188, follow-up of #182 review Q6; changes
  G3's "daily after 04:00").** The manager's decision on #188 (option 1; a scheduling choice,
  "what is better and safer"): `db.window` is due daily after **01:00** site time
  (`worker/src/arcilogger_worker/service.py`, `daily_after=time(1, 0)`; a fixed schedule, no
  setting). It comes after `leases.nightly` (00:05), the seal (the Seal task, 00:10),
  `partitions.ensure` (00:15), `system.sample` (00:30) and `usage.daily` (00:45), and before the
  Backup task's `pg_dump` at 01:40 (`deploy/windows/tasks/Backup.xml`, ASSUMPTIONS B7, unchanged;
  its local time is the site's). The day it drops is about 31 days old (`db.retention_days` 30)
  and long sealed, so it does not wait for last night's seal (`oldest_unsealed_day` still holds
  the cutoff when sealing has stopped). **The rule:** the run and its first retry after a failure
  (`retry_delay(job, 1)`, `DAILY_RETRY_FIRST`, 15 min: 01:15) are due before the dump starts; the
  second retry (30 min later: 01:45) can meet a dump, and so can a dump started late
  (`StartWhenAvailable`, the server off at 01:40); both are rare, and a run that backs off from a
  dump stays counted toward `R20-db-window` (W5-G-24 (b), the owner's answer on #182: hiding it is
  worse). No new state, signal or setting; the backup's schedule and ADR-0009's order are
  unchanged; no raw data is touched and no licence state gates the job. The service's job table,
  docs/CORRELATION.md and YEDEKLEME/BACKUP §2 give the new time. Tests
  (`worker/tests/test_db_window.py`):
  `test_the_window_runs_and_retries_once_before_the_backup_starts` (the task's `StartBoundary`
  read from `Backup.xml` with the standard library, the job and its first retry delay from the
  worker: `daily_after` + `retry_delay(job, 1)` < the start, so a later change of either fails),
  `test_the_job_runs_daily_after_one_and_is_never_gated` (named `..._after_four_...` before);
  changed, `worker/tests/test_service.py` `test_default_jobs_run_against_the_database` (its
  03:30 tick now runs `db.window`, its 09:00 tick no longer).

- **W5-G-28: a write aborted by a conflict is made again, not reported as an outage (#149, a
  TimescaleDB recompression policy against a write; seen once in #133's loop).**
  **The cycle, reproduced.** Where the columnstore policy recompresses a partial chunk by rebuilding
  it (a hypertable it cannot recompress segment by segment), it holds `ShareLock` on the chunk and
  then asks for `AccessExclusiveLock` on its index. A transaction that has read the chunk
  (`AccessShareLock` on the index) and then writes it queues its `RowExclusiveLock` behind the
  policy's `ShareLock`: a cycle, and PostgreSQL aborts the side that waited longer with `40P01` (in
  #133's loop, and in the test, the policy, which leaves the chunk partial for its next run). On
  `messages` TimescaleDB 2.30.2 recompresses segment by segment and gives a chunk with concurrent
  writes up at once ("aborting recompression due to concurrent DML on uncompressed data, retrying
  with next policy run"), without waiting.
  **Not a statement of ours.** No ingest transaction reads a time-series chunk before it writes it:
  the loader's first lock on a chunk is its COPY's `RowExclusiveLock` (`ingest_offsets` comes first,
  and `search_counts` and `source_clock_samples` are plain tables), and a reset step's first lock on
  a chunk is its DELETE's. The time-series tables have no foreign keys. So nothing is reordered
  (#149 step 3: only if the reproduction showed the statement was ours); which transaction of #133's
  loop read before it wrote is not in the server log, which does not print the surviving side's
  query.
  **Ingest's handling** (`IngestService._flush`, `_archive`). A batch aborted by a deadlock
  (`40P01`) or a serialization failure (`40001`) was rolled back: offsets and the spool are as they
  were. It is made again at once, up to `CONFLICT_RETRIES` (3) times after 0.1, 0.2 and 0.4 s, each
  with a log line naming the conflict and its SQLSTATE. The database is not reported unavailable and
  its connections stay (no `engine.dispose()`). A conflict that keeps aborting after that, and every
  other error (a lost connection, a server down), still reads `database_unavailable` with the
  outage's backoff. A reload's step aborted the same way (a reset step, the claim, a partition) is
  made again in the next round, and its load goes through `_flush`. The status file's shape is
  unchanged (the conflict is in the log only), so no new TR/EN text.
  Tests: `ingest/tests/test_outage.py`
  `test_a_batch_aborted_by_a_conflict_is_made_again_without_an_outage[40P01|40001]`,
  `test_a_conflict_that_keeps_aborting_the_batch_is_reported_as_an_outage`,
  `test_a_lost_connection_is_still_reported_as_an_outage`; `ingest/tests/test_archive_reload.py`
  `test_a_reload_aborted_by_a_deadlock_is_made_again_in_the_next_round[reset|load]`; TimescaleDB
  with compression, `ingest/tests/test_recompression.py`
  `test_a_read_then_write_transaction_deadlocks_with_a_rebuilding_recompression` (the cycle, the
  policy's notice "deadlock detected", the chunk left partial) and
  `test_an_ingest_batch_and_the_recompression_do_not_deadlock` (a real batch held open after its
  COPY while the policy runs on `messages`: no deadlock, the batch not aborted, the next run
  recompresses the chunk), both ordered through `pg_locks`.

- **W5-G-29: the srcmac guard counts the sessions that overlap the hour (#96, #40 item 7;
  2026-11-06, the manager).**
  **The rule.** `lookup/trust.py` `srcmac_ip_counts` counts the IPv4 `srcip` of a
  `(srcintf, srcmac)` over the rows that overlap `[low, high]`: logged at `low` or later and started
  at `high` or earlier (`time − duration`, as the session probes compute it; no duration: started
  when logged). Its scan stops `max_session_seconds` after `high`, as the probes' does. Before, it
  counted only the rows logged in `[low, high]`, so a router MAC's long session of another address
  that started in the hour and was logged at its end was missed, and the lookup kept a `low`
  `srcmac_only` chain where `srcmac_shared` should have refused it.
  **The three callers change together.** Each passes `lookup.max_session_seconds`: the lookup
  (`_SrcmacGuard.load`), the `R20-lease:srcmac-shared` alert (`alerts/system.py`
  `_srcmac_shared`) and the interface list's observed counts (`routers/settings.py`
  `observed_counts`). The last two count windows that end now. Nothing is logged after now, so
  their reading changes only for rows logged ahead of the check (a source's clock).
  **One addition is removed.** The lookup used to add the address of a used row logged outside the
  hour (`sessions.srcmac_addresses_within`). That addition is gone, because every row a lookup uses
  overlaps the hour: `lookup.tolerance_seconds` is at most 3600, and the guard's window is ±1 hour.
  Keeping it would have counted such a row's address twice.
  **The plan (deviation from the brief's "keep the plan on an index").** The plan is held to its
  time bound instead: it reads only the partitions or chunks of the UTC days of
  `[T − 1 h, T + 1 h + max_session_seconds]`, each scan with both bounds. No index holds `srcmac`
  (D10). An index on `(srcmac, time)` would cost every ingested `fg_traffic` row an entry and would
  not spare the read:
  - nearly every row of those days is inside the bound;
  - the candidates' rows lie on every page of it.

  On TimescaleDB, chunk exclusion bounds the read the same way. Compressed chunks (segmented by
  `source`) are decompressed over the bound, as the session probes' scan already does.
  **Measured** (plain PostgreSQL, the traffic site, 1 M rows a day, 100 candidates and 67 MACs): a
  parallel sequential scan of the two day partitions, **0.157 s** against the guard's 1 s. The 2 h
  read before #96 took 0.044 s.
  **Tests:**
  - `worker/tests/test_trust.py` `test_srcmac_ip_counts_sessions_that_overlap_the_window`. It
    counts a session that started in the window and was logged after it. It does not count a
    session that started after the window, one that ended before it, one logged past the scan, or
    one logged after the window without a duration.
  - `worker/tests/test_lookup_srcmac.py`
    `test_a_long_session_of_another_address_that_overlaps_the_hour_makes_the_mac_shared`.
  - `worker/tests/test_alerts_lease.py`
    `test_a_srcmac_session_logged_ahead_of_the_check_still_counts`.
  - The perf guard `test_the_srcmac_guard_counts_two_hours_fast`: the call's longest session, the
    plan's scans limited to the window's UTC days, and both bounds in the plan.

## Deviations — W5-H (2026-11-03)

- **W5-H-1: the first line view of an xz file decompresses more than one stream.** §5.4 says
  "showing one line decompresses one stream". The manager's rule on #111 (2026-11-03) is that a
  cited raw line is never shown unchecked: both its offset and its line number are checked. The
  line number needs the line feeds before the line's stream, so the first view of a file counts
  the streams before the line once and keeps the counts in memory (option b of that comment;
  never written to `vault/`); every later view of the file decompresses only the line's stream,
  read to its end so its CRC64 is checked. Measured on a 5 M line day (docs/DATA-MODEL.md): first
  view 7.0 s for the middle line and 14.1 s for the last one, later views 0.06 s; gzip takes
  3.4 s, 6.4 s and 0.12 s. A reference whose offset does not hold its line is looked up by its
  line number, as on a `.gz` day, so both formats give the same error.
- **W5-H-2: `chain.py` (not W5-H's) has one hunk**, granted on #111 (2026-11-03): a sealed file
  that cannot be decompressed is a `verify_day` error, `<path>: cannot be decompressed (…)`,
  instead of an exception. Its Turkish text is one rule in `api/src/arcilogger_api/issues.py`
  (W5-C's), granted on #111 (2026-11-03 19:08): "{path}: sıkıştırılmış veri açılamıyor (dosya
  bozuk)"; the English text is the core's message itself.

## Deviations — W5-A (2026-11-03)

- **W5-A-1: the config adapter.** `renewal.renew_before_days` reads `tsa.renew_before_days`
  with `getattr` and the §2.5 default (180) until W5-T's day-1 pull request lands (as W5-B-8
  for `crl.crl_options`). It is the one place in `arcilogger_core` that reads the key; W5-C's
  `legalstatus/options.py` has its own reader. Both become plain attribute access when W5-T
  lands. Until then the 30–730 bounds are not enforced: they are W5-T's validation.
- **W5-A-2: who writes `state/renewal.json`, and what it holds.** §2.1.2 says the status function
  "also writes `state/renewal.json`" and "only the task writes it". `renewal_status` only
  computes; the `Renew` task's command line (`arcilogger-core renew`, also `--dry-run`) writes the
  file with `renewal.write_state` (`atomic_write_bytes`), so `verify_day`, the builder and the API
  never write it. Each entry of `tokens` is W5-C's shape (`path`, `kind`, `cert_not_after`,
  `status`, `covered_by`) plus `renewed_at` (UTC `…Z`, the counting renewal's genTime, else null),
  taken from the status **after** the run. Top-level keys beside `tokens`, ignored by W5-C's
  reader: `schema` (`arcilogger.renewal-status/1`), `at` (when the statuses were computed),
  `renew_before_days`, `totals` (all five keys), `unreadable`, `mismatched`, `notes` (TR/EN),
  `last_run {at, outcome, exit_code, record, error}` (`outcome`: `renewed`, `not_needed`,
  `failed` or `refused`; `error`: a code such as `tsa_unavailable`, `renewal_stamp_invalid`,
  `credentials`, `roots`) and `failures_in_a_row` (a failed or refused run adds one, any other
  run sets 0) for W5-E's "Renew failed twice in a row" (C12); the names were accepted on #132
  (A4), and W5-E follows them. The file is written after a failed (exit 3) or refused (exit 2) run too: the
  statuses are computed afresh when the pinned roots are known (time passes while the server
  fails, and a token turns `uncovered`), else the last known `tokens` stay with their `at`; with
  none known the file has no `tokens`, which W5-C's reader takes as unknown, never as "nothing
  due". A dry run writes the statuses and keeps `last_run` and `failures_in_a_row`. Every run
  holds `state/renewal.lock` from before it computes the statuses until it has written the file,
  so a dry run never writes over a run in progress; a run (dry or not) that finds the lock held
  writes nothing (the holder writes the file).
- **W5-A-3: the crash-safe write order.** §2.1.1 says the record "is written with
  `atomic_write_bytes`, stamped", and a failed run deletes it. `renew` writes it as
  `<name>.json.part` (with `atomic_write_bytes`), stamps it to `<name>.json.part.zd` and verifies
  that token against the pinned roots; only then is the token renamed to its final name, then the
  record, and both are locked with `immutability.lock`, as `retention` does for a deletion record.
  A failed stamp (`TsaError`, `OSError`, or anything else the provider raises) or a token that
  does not verify, or whose certificate the stored revocation lists show revoked at or before its
  genTime (it would never count, C15: `RenewalStampInvalidError`, a `TsaResponseError`), removes
  both files and is raised. So a record never exists under its final name without its token, and
  the builder (which ships `renewals/*.json`) never ships one. Leftovers of an interrupted run are
  tidied by the next run (`renewal.recover`): a part whose token (`.part.zd`, or the final `.zd`
  when the run stopped between the renames) verifies against it is promoted; any other part, a
  token without its record and an interrupted write's temporary file are removed, but never a
  locked file (a locked token without its record means a completed record was removed: it stays
  for a person, with a warning in the log; `recover` never unlocks). A record and its token under
  their final names that are not both locked (`lock` failed after the renames) are locked by the
  next run, as the seal locks every file of its day. One run at a time: `state/renewal.lock`
  (never inside `vault/renewals`, where the standalone verifier's vault mode would report an
  unknown file); a second run raises `RenewalBusyError` (`renewal_busy`), and a lock file that
  cannot be opened or locked for another reason `RenewalLockError` (`renewal_lock_failed`), both
  with TR/EN texts.
- **W5-A-4: token paths.** A deletion token's path is the real
  `deletions/<YYYY-MM-DD>[-N]-imha.json.zd` (`retention.records`, final names only, so no
  `.part` file and no `chain-anchor.json`); §2.1.1's `…-retention.json.zd` is illustrative. A
  report token's path is data-dir relative (`exports/reports/<name>.pdf.zd`), as §2.1.1 says;
  every other path is vault-relative, and a day's is its token's (`…/manifest-<day>.json.zd`),
  the key W5-C's day page looks it up by.
- **W5-A-5: the standalone verifier's rule and window.** The package carries neither
  `tsa.legal_root_fingerprints` nor `tsa.renew_before_days`, and §2.1.4 computes "from the
  package alone". So in `verify.py` a renewal record counts when its token verifies against the
  package's roots, the record is `arcilogger.renewal/1` with `tsa.legal_grade: true` and
  `reason: "due"`, the stamp is not one of ARCILOGGER's test TSA (a policy under 2.999 or a root
  of "ARCILOGGER TEST - NOT VALID", as `legal-claim-mismatch` judges a day) and the package's
  CRLs do not show its certificate revoked at or before the stamp; and its own stamp is
  `not_due` or `covered` (the chain of §2.1.2, the same rules as the core's, copied in the
  standard library as `RenewalRules`). The window is the default 180 days (`RENEW_BEFORE`). A
  production-like stamp whose root the site has not listed therefore counts in the standalone
  verifier and not in the core: that case is the core's only. Only a day whose token verified
  gets a finding (its certificate's `notAfter` comes from the verified signer); a day whose
  token fails has failed already. `{date}` is `_format_time` in the Turkish text and ISO 8601
  in the English one, as the other findings of the file write times; for `renewal-late` it is
  the time of the re-stamping (the counting renewal's genTime), for the other codes the
  certificate's expiry. `VERIFIER_VERSION` is "6" by addendum H4 (W5-A-8). At the edges both
  verifiers follow §2.1.2 literally (owner answer Q16): a renewal stamped at the certificate's
  notAfter itself is `late`, and a certificate counts as expired from its notAfter on
  (`cert_not_after <= now`), although `tsa/verify.py:_valid_at` (and the standalone
  `Certificate.valid_at`) treats a stamp made at notAfter as valid;
  `test_the_expiry_boundaries_are_pinned` pins both, and so do the shared case table's three
  notAfter cases (`renewal_cases.py`) and
  `test_a_renewal_stamped_at_its_own_not_after_verifies_but_does_not_count` (the stamp verifies
  in both verifiers, and the renewal does not count), so neither is "fixed" into the other
  unnoticed.
  Superseded in part by W5-A-19 (answer to Q3 on #177): `VERIFIER_VERSION` is "8", not "6"
  (W5-A-17 set "7"; W5-A-8).
- **W5-A-6: what stays in section 5.** The existing `renewal-ok` (each record's stamp) and
  `renewal-covers` (which packaged days a record names) stay beside the five new codes, and the
  section "5. Yenileme damgaları" now appears whenever the package has a day whose token
  verified, with one finding per such day, also when the package has no renewal record. None of
  the five codes is `fail` (D6): the verdict of a package does not change.
- **W5-A-7: a test record's own token is not renewable.** A renewal record written with
  `reason: "test"` is read as a renewal (it never counts, D5), but its own token is not listed
  as a token to renew: otherwise each test renewal would be renewed again, with nothing to
  protect. A record with `reason: "due"` that does not count (its root is not listed, its token
  does not verify, it was revoked) is still renewable.
- **W5-A-8: `VERIFIER_VERSION` is "6", not §2.1.4's "5".** Addendum H4: W5-H (#130) merged into
  `wave-5` first (84e71e1) and set "5", so W5-A, merging second, sets "6" (manager on #132,
  2026-11-03 20:51). Superseded by W5-A-17 and W5-A-19 (answer to Q8, #156 item 7): W5-A-17
  set "7", and W5-A-19 sets "8" because #136 has not merged, which
  `test_renewal_standalone.py::test_verifier_version_is_8` pins; #136 (`xz-unsupported`) takes
  "9" and updates that test when it merges.
- **W5-A-9: exit codes beyond §2.1.3's wording.** `renew` catches its own errors (`main` maps
  every `TsaError` to 1). Exit 3 also covers a new stamp that does not verify or is revoked
  (`renewal_stamp_invalid`), another run holding `state/renewal.lock` (`renewal_busy`), a lock
  file that cannot be used (`renewal_lock_failed`), a file error during the run (`io_error`) and a
  `state/renewal.json` that cannot be written: each is retried by the next run. Exit 2 is a
  provider that cannot be made (`credentials`, `zamane-jar`, `executable`, `secret-store`,
  `provider`), a provider configuration error found only while stamping (a
  `TsaConfigurationError` such as no Java or no Zamane client: `executable` or `provider`, it
  needs an administrator, not a retry), or no usable pinned root (`roots`: none, or one that
  cannot be read); a dry run needs only the roots. Another run holding the lock gives 3 before
  any of these is checked. Exit 1 is any token still `uncovered` after the run, so a
  test provider (whose records never count, D5) leaves an expired token `uncovered` and exits 1;
  "nothing due" with an `uncovered` token cannot happen, as an uncovered token is due for
  renewal. A dry run exits 1 when a token is `uncovered` now, else 0. The text output is Turkish
  with English in brackets, errors on stderr; `--json` prints `outcome`, `exit_code`, `dry_run`,
  `record`, `reason`, `due`, `renewed`, `discarded`, `error {code, message_tr, message_en}`,
  `state_written` and `report`. Its `outcome` is one of the four of `last_run` or `dry_run` (a
  dry run that ran) or `busy` (another run holds the lock); neither of the last two is ever a
  `last_run.outcome`.
- **W5-A-10: what the report adds.** `RenewalReport` also names the tokens and records it could
  not read (`unreadable`: a token that cannot be parsed or whose stamped file is missing, a record
  that is not `arcilogger.renewal/1` or has no token, a token whose imprint algorithm is not
  supported; none of them has a status), names in `mismatched` the tokens whose imprint is not the
  file they stamp as it is today (a changed manifest, deletion record, report or renewal record:
  no status, never renewed, so no renewal record ever lists the hash of a changed file for the
  builder to ship beside it), and carries notes with TR/EN texts for what it could not check:
  unreadable stored revocation lists (the renewal tokens are then verified without lists, Q12),
  stored lists left out because they do not match their index entry (`crl_mismatched`, naming the
  files, C14) and an `exports/reports` folder that cannot be read (the `Renew` task may lack the
  right; the run goes on without report tokens). A token that disappears during the scan (the
  retention job) is skipped. The certificate's `notAfter` is read from each token, never from
  `state/seal`.
- **W5-A-11: the core verifier's day status (A2 on #132).** `chain.verify_day(...,
  renewals=True)` puts the day's long-term status in `DayCheck.renewal`
  (`renewal.day_renewal`: `status`, `code`, `level`, `cert_not_after`, `covered_by`,
  `renewed_at` and the §2.6 TR/EN texts, the standalone verifier's codes, levels and texts) and
  `as_json` adds the key `renewal`; it never adds an error or a warning (D6). With the default
  `renewals=False` nothing reads the renewals and the output of every caller is byte for byte
  what it was (`test_without_renewals_every_caller_sees_the_same_bytes`); no caller passes
  `True` yet. As in the standalone verifier, only a day whose token verified gets a status (a
  day that fails for another reason, such as a changed log file, keeps the status of its
  token), and the window is the caller's `renew_before_days` (default the §2.5 180 days):
  `verify_day` reads no configuration (W5-A-16). Each renewal token is checked with the EKU
  rule (`tsa.require_critical_eku`, K6) its own record was written under, as a day's token is
  with its manifest's, in both verifiers and in `renewal_status` and `renew` alike (W5-A-17).
  The renewals are judged with the `legal_roots` and the
  revocation lists `verify_day` already has; each call reads and verifies every renewal record
  again, so a caller that checks many days with `renewals=True` pays days × records. In the
  core too, a renewal whose stamp is from ARCILOGGER's test TSA (policy under 2.999 or the test
  root, `is_test_stamp`) never counts, even under a listed root (D5), as in the standalone
  verifier (W5-A-5); the core looks for the test organization in the root's name only, the
  standalone verifier in every certificate of the chain, which differ only for a test
  intermediate under a root that is not the test root. The two verifiers agree on the shared
  case table `core/tests/renewal_cases.py` (rule 5); its one exception, a production-like stamp
  under a root the site has not listed, is W5-A-5's.
- **W5-A-12: a root `.gitleaks.toml` (granted on #132, 2026-11-03T22:08Z).** The frozen fixture
  (D15) holds the product's own JSON, and gitleaks' default `generic-api-key` rule reads its
  `"token_sha256":"<64 hex>"` fields (the manifests, the deletion record and its anchor, the
  renewal record: 9 matches) as secrets; they are SHA-256 digests of token files. JSON carries no
  `# gitleaks:allow`, and the fixture's bytes are pinned, so the file keeps every default rule
  (`useDefault`) and allows only a `token_sha256` field with exactly 64 lower-case hex digits; it
  clears those 9 matches and nothing else in the repository. No path allowlist, no rule turned
  off, `ci.yml` unchanged. Every new allowlist entry needs the manager's review.
- **W5-A-13: exit 2 cause `legal-roots` (answer to Q1 on #132, #137 item 1).** A run whose
  provider claims legal grade while no pinned root is listed in `tsa.legal_root_fingerprints`
  takes no stamp: a token verifies only by a chain ending at a pinned root, so with none listed
  no renewal it writes would ever count (D5), and each one spends credit (D4). `renew` raises
  `renewal.RenewalLegalRootsError` (a `TsaConfigurationError`, `cause = "legal-roots"`, TR/EN
  texts naming `tsa.legal_root_fingerprints`) before the provider is called, so the command
  exits 2 and writes `last_run {outcome: "refused", exit_code: 2, record: null, error:
  "legal-roots"}`, which adds one to `failures_in_a_row`; the statuses are computed afresh, as for
  any refusal with known roots. The provider's own chain is not known before a stamp, so the
  check is on the pinned roots: none of them listed (the list empty, or naming only roots that
  are not pinned). It runs after the status, only when something is due: "nothing due" stays
  `not_needed` (exit 0, no credit spent), and a provider that claims no legal grade still writes
  its `reason: "test"` record (D5, exit 1 for an expired token). With the claim, a pinned set
  that lacks the listed root, which the stamp's verification failed with exit 3
  (`renewal_stamp_invalid`, W5-A-9), is now refused with this exit 2 before the stamp. The dry
  run is unchanged. The cause is `legal-roots` in the JSON's `error.code` and in
  `last_run.error`: `cli.py`'s `_renew_cause` takes it from the error (`exc.cause`), and
  `_RenewError.refused` builds `message_tr` from the error's `message_tr` and `message_en` from
  its `message_en`, not its "TR [EN]" text into both (the `cli.py` hunk outside #137's files
  granted on #137, 2026-11-04T09:07Z: those two functions and the error's import).
  `test_a_legal_claim_without_a_listed_root_exits_2` pins the cause in both places and each
  text in its own language.
  **After the stamp too (#156 item 2).** The check before the stamp sees only the pinned set:
  with roots A (listed) and B (not listed) both pinned and the provider chaining to B, it
  passed, every due day was stamped and the renewal never counted (D5). `_write` now also
  checks the stamp's own root once its token verifies: when the provider claims legal grade and
  the root its chain ended at (`details["root_sha256"]`) is not in
  `tsa.legal_root_fingerprints`, the record and its token are removed, as C15 does for a
  revoked certificate, and the run raises `RenewalLegalRootsError(root=, root_sha256=)`. Its
  TR/EN texts name that root (subject and SHA-256 fingerprint) and say that the record and its
  token were removed, and that one stamp credit was spent and every later run spends another
  until the root is listed in `tsa.legal_root_fingerprints` or the provider is corrected (answer
  to Q6 on #177). The command handles it as above: exit 2,
  `legal-roots` in `error.code` and `last_run.error`, `failures_in_a_row` + 1, so `R20-renewal`
  raises after the second run. A run stopped between the stamp and this check (in
  `verify_token`, say) leaves a part whose token verifies, so `recover` applies it too, as it
  applies C15 (W5-A-14): `renew_held` passes `tsa.legal_root_fingerprints` as
  `recover(..., legal_roots=)`, and a part whose record claims legal grade while its chain ends
  at a root that is not listed is removed with its token, with a warning in the log naming that
  root, instead of being promoted and locked (a locked record that never counts could not be
  removed). `legal_roots` is a required keyword argument of `recover`, without a default, so no
  caller skips the check by leaving it out; it applies only to a record that claims legal grade
  (answer to Q5 on #177).
  `test_a_stamp_under_a_pinned_but_unlisted_root_is_removed` (two pinned roots, one listed; a
  stamp under the listed one is still kept),
  `test_a_stamp_under_a_pinned_but_unlisted_root_exits_2` (`failures_in_a_row` 1, then 2) and
  `test_an_interrupted_stamp_under_a_pinned_but_unlisted_root_is_removed` (both places the
  token can be, through `recover` and through the next run; `recover` without `legal_roots` is a
  `TypeError`) pin it.
- **W5-A-14: `recover` checks revocation as the run does (#137 item 2).** W5-A-3's `recover`
  promoted an interrupted part whose token verified, without the stored revocation lists, so a
  run stopped between the stamp and its C15 check left a record that never counts. It now
  verifies that token with the stored lists (`crls=`, read from `state/crl` when the caller
  passes none, and only when a part is to be checked; unreadable lists are logged and the token
  is verified without them, Q12) and refuses it as `_write` does (one helper, `_refusal`): a
  token whose certificate was revoked at or before its genTime is removed with its part, with a
  warning in the log, instead of being promoted (C15: a revoked new stamp leaves no record). A
  locked file is still never removed. `test_a_revoked_stamp_of_an_interrupted_run_is_removed`
  covers both places the token can be (`.part.zd`, and the final `.zd` after the first rename),
  through `recover` and through the next run.
- **W5-A-15: a status after the run that cannot be read keeps the renewal (#137 item 3).** Once
  the record and its token are renamed and locked, the run has renewed. `renew_held` used to read
  the status after the run (`report_after`) unguarded, so an `OSError` there made the command
  exit 3 as `failed` with `last_run.record` null while the record was in the vault. Now that
  error is logged as a warning and `report_after` is the status before the run: the command
  reports `renewed` with its record path and writes it to `last_run`, and the exit code and the
  statuses in `state/renewal.json` come from that before-status (a token that was `uncovered`
  before the run still gives exit 1, and a renewed token still shows `due`) until the next run
  reads the status again. Only `OSError` is caught; errors before the record is written are
  unchanged. `test_a_status_after_the_run_that_cannot_be_read_keeps_the_renewal` (core) and
  `test_a_status_after_the_run_that_cannot_be_read_still_renews` (command) cover it.
- **W5-A-16: `verify_day` takes the window from its caller (answer to Q3 on #132, #137 item 5).**
  `chain.verify_day(..., renewals=True, renew_before_days=180)` passes `renew_before_days` to
  `renewal.day_renewal`, which judges the day's token and, through the chain rule (§2.1.2), the
  renewals' own tokens with it; `verify_day` reads no configuration, and the default is §2.5's
  (`renewal.DEFAULT_RENEW_BEFORE_DAYS`, written as the number because `renewal` imports
  `chain`). Without `renewals` the window is not used and the output is unchanged. No caller in
  the product passes `renewals=True` yet (W5-A-11), so no caller passes the window either: the
  shared case table passes the standalone verifier's `RENEW_BEFORE` (W5-A-5), and when W5-T
  lands the A3 `config.py` hunk wires the command line's caller to `tsa.renew_before_days`
  (`renewal.renew_before_days`, W5-A-1). `test_the_caller_passes_the_window` pins the default,
  that no configuration is read, both edges of the window, and that it reaches a renewal's own
  token.
- **W5-A-17: each renewal token is judged by its own record's EKU rule (answer to Q6 on #132,
  #137 item 4).** A renewal record's `tsa` block carries `require_critical_eku: false` when the
  site had relaxed the rule (K6) as it wrote the record (`tsa_block`, the manifest's keys), so
  its token is now verified with that rule, as a day's token is with its manifest's: in the
  core's `renewal_status` (`_Reader`), `day_renewal` (and so `verify_day(..., renewals=True)`),
  `recover` (a part with its own record's rule) and `renew` (`_write`, whose record carries
  today's setting), and in the standalone verifier's `check_renewals` (`_renewal_eku`; a record
  that cannot be read is judged strict and never counts anyway). Neither the day's rule, the
  other manifests of a package (`_relaxed_eku` now judges deletion records and the packaged
  report's stamp only) nor the site's setting at the time of reading decides any more, so
  W5-A-11's split between the
  callers is gone: `day_renewal` and `recover` lose their `require_critical_eku` parameter,
  and `verify_day` no longer passes the day's rule. The standalone verifier's
  `token-eku-not-critical` warning on a renewal token says the site relaxed the rule in the
  renewal record ("bunu yenileme kaydında belirtmiştir" / "sealed that choice in the renewal
  record") instead of the manifest, and `DENETCI-KILAVUZU.md` §7's row for that code says
  either place (the item 6 hunk). The shared case table (rule 5) gains three cases: a strict
  day with a relaxed renewal record (`renewal-covered`), a relaxed day with a strict one
  (`renewal-due`), and a strict renewal record beside a relaxed day in one package
  (`renewal-due`; the standalone verifier relaxed it before); the two existing EKU cases keep
  their verdicts. `test_a_renewal_token_is_judged_by_its_own_records_eku_rule` (both settings
  of the site afterwards, and the warning text) and
  `test_a_renewal_keeps_the_eku_rule_its_record_was_written_under` (`recover` and the status
  after the site turned the rule strict again) cover the rest. The standalone verifier's output
  changes, so `VERIFIER_VERSION` is "7" (addendum H4): #136 has not merged, so W5-A-8's "7"
  for #136 becomes "8" when it does; `test_verifier_version_is_7` pins it.
  The evidence builder judges each renewal token by its own record's rule too: it picks the
  root to bundle for a record's token with `_root_of`, and a token with an EKU error yields no
  root, so with the package's rule (`_Records(require_critical_eku=all(day.require_critical_eku
  ...))`) a relaxed renewal whose non-critical-EKU certificate chains to a root no packaged
  strict day uses (a new root after a CA change) left that root out and the self-check refused
  the package (`token-chain`). `_Records.add` now passes a `renewal` record's own rule
  (`tsa.require_critical_eku is not False`; a record that cannot be read is judged strict, as
  `_renewal_eku` judges it) to `_root_of`; deletion records and the report's stamp keep the
  package's rule, and the renewals of a deletion record (W5-A-18) are `renewal` records like
  any other (the second `builder.py` hunk outside #137's files, granted on #137,
  2026-11-04T09:07Z). `test_a_relaxed_renewal_under_the_legal_root_is_packaged_and_judged_alike`
  (`test_renewal_evidence.py`) builds a package of a strict day renewed under the legal CA's
  root by a relaxed record with a non-critical EKU: the legal root is bundled, and the
  standalone verifier on the package and the core on the vault find the day
  `renewal-covered` with the same texts (rule 5).
  Superseded in part by W5-A-19 (answer to Q3 on #177): `VERIFIER_VERSION` is "8", not "7"
  (`test_verifier_version_is_8`), and #136 takes "9", not "8" (W5-A-8); `_relaxed_eku` no longer
  judges a deletion record that a packaged manifest lists (the manifest that lists it does), and
  the builder picks the root for that record's token by the same rule, not the package's.
- **W5-A-18: a package ships the renewals of its deletion records (answer to Q5 on #132, #137
  item 7).** D3 renews deletion-record tokens, but the builder's `_renewals` matched only the
  packaged days' manifest and token hashes and the report's token hash, so a package that ships
  a deletion record (listed in a day's manifest, or behind the chain anchor) left out the
  renewals of that record's token. `_renewals` now also matches the token hash of every
  deletion record the package ships, and the same raw-text match picks up a renewal of that
  renewal (D2, `protects`); each goes with its `.zd`, as D1. The standalone verifier checks them
  as any renewal (`renewal-ok`, `renewal-covers`) and judges only the days (§2.1.4), so its
  code is unchanged; the package's contents change, which W5-A-17's `VERIFIER_VERSION` "7"
  already covers. The module docstring of `builder.py` still says "renewal stamps that cover
  the included days or the included report's time stamp" (a second hunk, outside #137's grant).
  `test_a_package_with_a_deletion_record_ships_its_renewals` (`test_renewal_evidence.py`)
  covers a two-deep chain behind a deletion record, the core's status of its token, the
  auditor's verifier on the package, and that a package without the record ships none of them.
- **W5-A-19: a deletion record's token is judged by the manifest that seals it (answer to Q5,
  #156 item 1).** W5-A-17 left deletion records under the package's rule (`_relaxed_eku`:
  relaxed when any manifest of the package is), so the token of a record that a strict manifest
  lists, with a non-critical EKU, passed with a warning beside a relaxed day. The standalone
  verifier now judges it by the `tsa.require_critical_eku` of the manifest that lists it:
  `_check_deletions` by that day's manifest, and `_check_anchor_link` by `_deletion_eku` (strict
  when one of the package's manifests that list the record is strict; a record that no packaged
  manifest lists, reached only through the chain anchor, by the package's rule). The builder
  picks the root to bundle for that token as `_deletion_eku` does (rule 5): `_Records.collect`
  passes `add`, for a listed record and for the one `_anchor` ships, the rule of the packaged
  manifests that list it (strict when one of them is), else the package's. A record listed by
  two manifests with different rules (a seal run that stopped before it wrote the record's
  `.sealed` marker, so the next manifest lists it again) is judged strict for the anchor link
  and the builder's root, and its token still at each listing day by that day's manifest (answer
  to Q4 on #177): such a package fails at the strict day, and the builder's self-check refuses
  it. The packaged report's stamp keeps the package's rule (`_relaxed_eku`,
  `requires_critical_eku`), so W5-A-17's "`_relaxed_eku` now judges deletion records and the
  packaged report's stamp only" now reads "a deletion record that no packaged
  manifest lists and the packaged report's stamp". The core judges no deletion record's token
  once a manifest seals it (`renewal._Reader` reads only its hashes and certificate,
  `chain.verify_day` does not read `deletions`; `retention` verifies a new record with the
  site's setting at that time), so no core file changes. Retention's own check of a new record
  (`retention.recover`, `unfinished_record`) uses the `tsa.require_critical_eku` of its day, and
  the rule of the manifest that lists the record decides (answer to Q7 on #177). The
  standalone verifier's output changes, so `VERIFIER_VERSION` is "8" (addendum H4): #136 has
  not merged, so it takes "9" (W5-A-8), which replaces W5-A-17's "W5-A-8's '7' for #136
  becomes '8'"; `test_verifier_version_is_8` pins it. The shared case table gains `DELETION_CASES`
  (`renewal_cases.py`): a record listed by a strict manifest beside a relaxed day (`token-eku`,
  a warning before), one listed by a relaxed manifest beside a strict day
  (`token-eku-not-critical`), one listed by a strict manifest, one listed by a strict and by a
  relaxed manifest (`token-eku` at the strict day, `token-eku-not-critical` at the relaxed
  one), one only behind the chain anchor in a relaxed and in a strict package, and one behind
  the anchor and listed by a strict manifest in a relaxed package (`token-eku` at both days).
  `test_a_deletion_records_token_is_judged_by_the_manifest_that_lists_it`
  (`test_renewal_evidence.py`) runs each through the builder's choice of roots, the standalone
  verifier on the vault and on the package (built, or refused by the self-check).
- **W5-A-20: retention deletes in batches (#195, the manager's decision; the owner asked about
  stamp usage on 2026-11-05).** One deletion record a day would double the credit use once the
  vault passes `retention.days` (about 365 extra stamps a year). The `Retention` task stays daily,
  but a run deletes only once the oldest expired day has been expired `retention.batch_days` days
  (new key, `config.RetentionConfig`, default 30, 1–180; `retention.due_day`: that day plus
  `retention.days + batch_days`, so 1 deletes a day on the day it expires, as before), and then
  every expired day under one record, one stamp; about 12 records a year. Any other run does
  nothing and writes nothing (`RetentionResult.due` names the day); a failed run deletes nothing
  and the next one retries. 180 is KVKK's six-month ceiling of periodic destruction (sahada
  doğrula: legal review, not a legal claim). Unchanged: the 730-day floor, the re-verification,
  the record format, the chain anchor, the next manifest listing the record, the `.part` and
  verify-before-rename rules, and an unfinished record of an interrupted run (finished first,
  whatever the batch). The key is in `arcilogger.toml` only, not on the settings page. Tests:
  `test_a_batch_is_due_once_its_oldest_day_has_been_expired_batch_days`,
  `test_a_day_expired_fewer_than_batch_days_ago_is_not_deleted`,
  `test_a_due_batch_deletes_every_expired_day_under_one_stamp`,
  `test_a_failed_batch_deletes_nothing_and_the_next_run_retries` (`core/tests/test_retention.py`)
  and `test_retention_batch_days_outside_1_to_180_is_refused` (`core/tests/test_config.py`).

## Deviations — W5-E (2026-11-04)

- **W5-E-1: the `Renew` task's rights on `vault\renewals` and on its state files come from
  their folders.** A4 (manager on #103, 2026-11-04 00:18) asks for "create and write in
  `vault\renewals`" and "modify on `state\renewal.json` and `state\renewal.lock`" (since W5-E-4
  `state\renewal\renewal.json` and `state\renewal\renewal.lock`), and §4.3 gives W5-E the ACL of
  `state\crl` and LOCAL SERVICE read on `exports\reports`. `Get-ArciAclPlan` adds those two
  entries (`state\crl`: protected, owners full control, LOCAL SERVICE Modify, ArciApi and
  ArciWorker ReadAndExecute, no collector or ingest access, since W5-E-9 OWNER RIGHTS
  ReadPermissions; `exports\reports`: LOCAL SERVICE ReadAndExecute) and, since W5-E-4,
  `state\renewal` with the rights of `state\crl`. None is
  added for the three A4 paths themselves: `vault\renewals` gets A4's right from the inherited
  LOCAL SERVICE Modify of `vault`, the two state files from the inherited Modify of
  `state\renewal`. An entry of its own on `renewal.json` would be lost at the first run, since
  `renewal.write_state` replaces the file (`atomic_write_bytes`) and the new file carries only
  the inherited rights of its folder. W5-E adds no entry for `vault` and none below it (`vault`
  keeps its earlier entry), so none gives a write right on a sealed day or a deletion record;
  their files keep the `immutability.lock` deny entries. Tests:
  `test_acl_plan_gives_the_renew_and_crlfetch_tasks_their_rights` (one assertion per A4 right,
  no entry below `vault`, `state\renewal` protected without collector or ingest access and no
  entry for its files, the exact set of folders the tasks may write),
  `test_acl_plan_least_privilege` (`state\crl` and `state\renewal` protected, no ingest access;
  `exports\reports` read only), and on Windows `test_acl_applied_keeps_sealed_file_deny_entries`
  (LOCAL SERVICE keeps an inherited Modify on `state\renewal\renewal.json` after two
  `atomic_write_bytes` calls; the folder is protected, and the collector and ingest have no
  entry on it or on the file).
- **W5-E-2: how the Sistem page judges `Renew` and `CrlFetch`.** §3 W5-E says only "`TaskName`
  in the API and the web", and C12/A4 say to judge both tasks from their state files, not from
  their exit codes. Both rows come after ApplySystem, are `late` after 26 h as the other daily
  tasks, and are never `not_used` (both run for every provider). **Renew** shows `last_run.at` of
  `state/renewal.json`, not its top-level `at` (a run that does not know the pinned roots keeps
  the earlier `at`, W5-A-2), so a dry run, which keeps `last_run`, is no evidence. **CrlFetch**
  shows the newest of `lists[].fetched_at` and `failures[].at` of `state/crl/index.json`, read with
  W5-C's `read_crl_index` (another schema is unreadable; since W5-E-6, an index that records no
  failure shows its modification time too). A time later than now is no evidence,
  and a file that cannot be read shows `missing` and never fails the request. The row says the
  task ran: a refused or failed Renew run, or a fetch with failures only, is `ok` there, and
  whether it succeeded is `R20-renewal`'s and `R20-crl`'s question (the Backup row's precedent;
  the API has no failing task state). A run that finds its lock held writes nothing (W5-A-2,
  W5-B-2), so it leaves no evidence; the run that holds the lock writes its file. A run that
  exits 1 without writing (W5-E-6) cannot be shown as failed either (answer to Q9 on #158, an
  open question there). Tests:
  `test_renew_and_crlfetch_are_judged_by_their_state_files` (`api/tests/test_system_health.py`;
  the files are written by W5-A's `write_state` and the real `crl.fetch`), and
  `test_read_renew_runs_from_the_writers_file`.
- **W5-E-3: `R20-renewal`'s "failed twice in a row".** §2.3.1 raises it "also when the `Renew`
  task failed twice in a row"; A4 makes that `failures_in_a_row >= 2` of `state/renewal.json`
  (`RENEW_FAILURES_ALERT = 2`, read with `read_renew_runs`, never from the exit code). The count
  decides even while the token list is unknown, because a refused run without pinned roots writes
  no `tokens` (W5-A-2):

  | tokens | `failures_in_a_row` | `R20-renewal:none` |
  |---|---|---|
  | unknown (no token list, unreadable) | known, ≥ 2 | raised |
  | unknown | < 2 or unknown | an open alert stays (W5-C-1) |
  | known, a `due` or `uncovered` token | any | raised |
  | known, none due | ≥ 2 | raised |
  | known, none due | < 2 | resolved (`renewal_ok`) |
  | known, none due | unknown | an open alert stays |

  A count that is not a whole number ≥ 0 (text, bool, float, negative, null, a list) is unknown;
  a file without `failures_in_a_row` counts as 0, as `renewal.write_state` reads it. W5-A's
  writer continues a damaged count differently (`True` as 1, `"two"` as 0); only a hand-edited
  file has one, and the failure branch then decides nothing until the next run writes a number
  (the rows with "unknown" above). The details are `due`, `uncovered` (both null while the
  tokens are unknown), `failures_in_a_row` and `title_en`: counts only. The check reads the file
  twice (W5-C's token reader and `read_renew_runs`); a run in between can pair the earlier tokens
  with the new count, each half is true of a file the task wrote, and the next pass reads both
  alike. Tests:
  `test_stamp_renewal_raised_after_two_failed_runs`, `test_stamp_renewal_refused_runs_count`,
  `test_stamp_renewal_failures_without_tokens_raise`,
  `test_stamp_renewal_cleared_after_a_successful_run` (a dry run keeps the failures),
  `test_stamp_renewal_unreadable_runs_decide_nothing` and
  `test_read_renew_runs_from_the_writers_file` (`worker/tests/test_alerts_legal.py`).
- **W5-E-4: the re-stamping state moves into the protected `state\renewal` (#151; Q2 of #140).**
  A4, §2.1.2 and §2.1.3 put the report at `state/renewal.json` and the lock at
  `state/renewal.lock` (so do D16, W5-A-2, W5-A-3, W5-C-1 and W5-E-1 to W5-E-3). There they
  inherited the Modify of `state`, which ArciCollector (network-facing), ArciIngest and ArciWorker
  hold: a compromised service could write a report that silences `R20-renewal`, `R20-cert` and
  the legal status's `renewal_due` and `renewal_uncovered`, or hold the lock so that no run
  counts. They are now `state/renewal/renewal.json` and `state/renewal/renewal.lock`
  (`renewal.STATE_FOLDER`, `state_dir`, `state_path`); A4's paths change accordingly, its rights
  do not. `Get-ArciAclPlan` gives `state\renewal` the rights of `state\crl`: protected, SYSTEM
  and Administrators full control, LOCAL SERVICE Modify, ArciApi and ArciWorker ReadAndExecute,
  nothing for the collector, ingest, ArciWeb or ArciAdmin. Every file `atomic_write_bytes` writes
  there inherits these entries. `Set-ArciVaultAcl.ps1` (and with it the installer's ACL step)
  creates the folder in plan order; `renewal.one_run` creates it too when it is missing, as
  `crl fetch` does `state\crl`.
  **Upgrade.** `renewal.state_to_read` gives the new file, or the earlier `state/renewal.json`
  while the new one is missing. The first counted run after the upgrade (every run but a busy
  one or a dry run: while the earlier file is the only one, a dry run writes nothing; per W5-E-8
  it writes no state file, but the folder and `renewal.lock` are created) carries over only the
  earlier file's `failures_in_a_row` (W5-E-8); from then on the new file exists
  and the earlier one is never read again. Nothing writes, moves or removes the earlier
  `state/renewal.json` or `state/renewal.lock`, neither at the upgrade nor at a run (answer to
  Q3 on #158: deleting them at the upgrade would break the read below): both are left in place
  and, once `state\renewal\renewal.json` exists, ignored.
  The earlier lock is never taken, so a process holding it blocks no run. W5-C's readers
  (`read_stamp_renewals`, `read_renew_runs`; through them the alerts, the Sistem page's Renew
  row, `/legal-status` and the day page) read the same `state_to_read`, so an upgrade does not
  resolve an open `R20-renewal` or `R20-cert` before the first run (at most a day: the task runs
  at 02:30). Until then they trust a file every service could write, but no more than before the
  move, and a missing report already reads as nothing due (answer to Q2 on #158: the one-time
  read stays). `stamp_renewal_path` is `renewal.state_path`. While the readers read the earlier
  file they show its path as the evidence: the Sistem page's Renew row says
  `state\renewal.json` then, and `state\renewal\renewal.json` once that file exists, while
  neither does, or while it cannot be looked at (`TaskRow.shown`; `Path.exists()` raises
  PermissionError, and the evidence text never fails the request). Step 2 of the `R20-renewal`
  runbook names both files: `state\renewal\renewal.json`, and `state\renewal.json` until the
  first run after an upgrade. The day page's problem text stays `renewal.json: unreadable`,
  the name of both files.
  Tests: `test_an_earlier_state_file_is_read_once_and_never_written`
  (`core/tests/test_cli_renew.py`),
  `test_the_readers_read_an_earlier_report_only_until_the_new_one_exists`
  (`worker/tests/test_alerts_legal_state.py`),
  `test_the_writer_and_the_readers_meet_in_the_protected_folder`
  (`tests/test_renewal_state_reader.py`),
  `test_renew_row_reads_an_earlier_report_until_the_first_run`
  (`api/tests/test_system_health.py`; the evidence text, and the earlier file left in place),
  `test_renew_row_survives_a_state_folder_it_cannot_look_into` (the same file; the row is
  `missing` and names `state\renewal\renewal.json`), the runbook's two paths in
  `test_a_dry_run_after_an_upgrade_leaves_the_alerts_as_they_are`
  (`worker/tests/test_alerts_legal.py`), and the ACL tests of W5-E-1.
- **W5-E-5: ARCILOGGER's test root is no issuer in use for `R20-crl` (#151; Q8 of #140).**
  §2.3.1 raises `R20-crl` when an issuer in use has no verified list newer than
  `tsa.crl_max_age_days`, and W5-C's `pinned_roots` pins the mock TSA's root while
  `tsa.provider` is `mock`. That root publishes no list, so every mock or trial install had a
  permanent, e-mailed `R20-crl`. Following W5-A's rule that a test stamp never counts (D5),
  `check_stamp_crl` treats the newest sealed day's stamp as no stamp in use when its chain's
  root carries ARCILOGGER's test organization (`tsa.verify.is_test_stamp` on the root's
  subject, the detection W5-A, the chain check and the evidence package use). Only the root
  decides: a test policy on a real CA's chain still needs that CA's lists. An `R20-crl` raised
  before is resolved (`crl_fresh`) at the next pass, unless `state/crl` cannot be read.
  `pinned_roots`, `stamp_in_use`, `issuers_in_use` and `crl_status` are unchanged, so `R20-cert`
  still reads the mock stamp's chain against the stored lists, and `/legal-status` still gives
  `crl_stale` for a mock install (whether that banner reason should follow is a question on
  #151). Tests: `test_stamp_crl_not_raised_for_arcilogger_test_root`,
  `test_stamp_crl_raised_for_a_real_root_without_a_list` (`worker/tests/test_alerts_legal.py`;
  its other `R20-crl` tests now seal through a made-up CA outside the test organization, pinned
  in `tsa.roots_dir`), and `test_no_seal_alert_when_every_closed_day_is_sealed`
  (`worker/tests/test_alerts_system.py`: a sealed mock install raises no alert at all).
- **W5-E-6: a `crl fetch` that knows no list address writes its index (#151; Q3 of #140).**
  W5-B's `fetch` returned before the lock when no URL was known (neither `tsa.crl_urls` nor an
  http/https distribution point in a pinned root or a token), so on such a site
  `state/crl/index.json` never appeared and the Sistem page's `CrlFetch` row stayed `missing`
  for good although the task ran every day. Now that run takes the lock and writes the index as
  every run does: the lists stored before and their entries stay (D8), the failures of the run
  before drop out, and every stored file is still hashed (C14), so a changed one is still
  recorded as `stored_file_changed`. On a site that never stored a list that is
  `{"schema": "arcilogger.crl-index/1", "lists": [], "failures": []}`. It still fetches nothing
  and exits 2 ("no URL known" outranks a `stored_file_changed` failure, which exits 1 when URLs
  are known); like any run it raises `CrlError` (exit 1, "Hata [error]: …" on standard error)
  when the lock is held or the index or a stored file cannot be read, and leaves the index as it
  is (answer to Q9 on #158: a real failure). Nothing else in `crl.py` and
  nothing in the standalone verifier changes (it reads `paket.json`, never `state/crl/index.json`),
  so the verdicts of both verifiers stay the same (rule 5). W5-E-2 changes (answer to Q8 on
  #158): when the readable index records no failure, the `CrlFetch` row also counts the index's
  modification time as a run, beside `fetched_at` (later than now is no evidence, as for the
  other times). Each run that gets past its lock and its reads rewrites the index with the
  failures of that run only, so such an index was written by a run without a failure, and
  `state\crl` is protected, so its modification time can be trusted. With a failure the
  row reads `fetched_at` and `failures[].at` only, as before (the failures carry the run's
  time). A site that knew list addresses earlier and knows none now keeps its old lists, with
  their old `fetched_at`, in the index; its row shows each run and is not `late` while the task
  runs. The row says whether the task runs; how fresh the lists are is `R20-crl`'s question,
  raised when an issuer in use has no fresh list. A run that finds the lock held exits 1 and
  writes nothing, but the run that holds the lock writes the index, so the row stays fresh. A
  run that exits 1 because it cannot read the index or a stored list writes nothing, so the row
  shows no run for it: it keeps the run before and turns `late` 26 h after that run, or shows
  `missing` while the index cannot be read. The answer to Q9 asks the row to show such a run as
  failed; it cannot. `TaskHealthState` has no failed value (W5-E-2: the API has no failing task
  state; adding one changes the API contract and the web types, which this package does not
  own), and no file records the exit code, which C12 and A4 forbid judging by alone. That is an
  open question to the manager on #158. W5-C's readers need no change: an empty index is
  read as no list, so it raises no `R20-crl` and no `crl_stale` while no issuer is in use (an
  open `R20-crl` is resolved, `crl_fresh`), and an issuer in use without a list is stale as
  before. Tests:
  `test_no_url_known_writes_an_empty_index`, `test_no_url_known_keeps_the_stored_lists` and
  `test_cli_no_url_known_exits_2` (`core/tests/test_crl.py`),
  `test_crlfetch_without_a_list_address_shows_the_run` and
  `test_crlfetch_after_the_list_addresses_are_gone_shows_the_run`
  (`api/tests/test_system_health.py`), `test_stamp_crl_after_a_fetch_without_a_list_address` and
  `test_stamp_crl_raised_after_a_fetch_without_a_list_address_while_in_use`
  (`worker/tests/test_alerts_legal.py`).
- **W5-E-7: the title of an `R20-renewal` raised by the failures alone (#151; item 4).** W5-E-3
  titled every `R20-renewal` "Yeniden damgalama gerekiyor: … / Re-stamping needed: …", also when
  only `failures_in_a_row >= 2` raised it and no stamp was due (the token list known with none
  `due` or `uncovered`, or unknown). Then the title is
  `alerts.texts.RENEW_FAILED_TITLE`: "Yeniden damgalama görevi art arda {failures} kez başarısız /
  The re-stamping task failed {failures} times in a row", with the count from
  `failures_in_a_row`. The issue's example said "twice"; the count is used because the alert
  stays open and counts on (a third failure is "3 kez / 3 times"; at the threshold it reads
  "2 kez / 2 times"). With a `due` or `uncovered` token the title stays "Re-stamping needed: …"
  with the failures added, as before. The open alert's title follows each pass (the store
  replaces it when it changes). The rule's catalogue name (`RULES["R20-renewal"]`, "Yeniden
  damgalama gerekiyor / Re-stamping needed"), the details, the facts and the runbook are
  unchanged; the title carries the count only, no name, address or log line. Tests:
  `test_stamp_renewal_title_follows_what_raised_it`, and the titles in
  `test_stamp_renewal_raised_after_two_failed_runs` and
  `test_stamp_renewal_failures_without_tokens_raise` (`worker/tests/test_alerts_legal.py`).
- **W5-E-8: an upgrade takes only `failures_in_a_row` from the earlier `state/renewal.json`
  (#151; review of the follow-up).** W5-E-4 carried the earlier file over as W5-A-2 carries the
  previous one: a refused run (no pinned root, or the provider refused; no statuses computed)
  copied its `at`, `tokens`, `totals`, `unreadable`, `mismatched` and `notes`, a dry run its
  `last_run`, into the protected file, and every later refused or dry run kept them. Every
  service could write the earlier file, so a made-up token list ("nothing due") would have
  outlived the move for good. Now `renewal.write_state` takes from the earlier file only
  `renewal.EARLIER_KEPT`, that is `failures_in_a_row`, read as W5-A-2 reads it (a whole number
  ≥ 0, anything else 0). A refused run with no protected previous file writes no token list,
  which W5-C-1 reads as unknown, never as nothing due. While the earlier file is the only one, a
  dry run writes no state file (`write_state` returns None, `--json` gives `state_written` false;
  answer to Q5 on #158; #170, wording): it would have written no `last_run`, turning the Sistem
  page's Renew row `missing` (W5-E-2), and its own statuses in place of the earlier ones,
  changing `R20-renewal` and `R20-cert` before any counted run. A dry run never makes the state
  look worse. It is not a run that writes nothing: like every run it creates `state/renewal/`
  and `state/renewal/renewal.lock` if they are missing and holds the lock (`renewal.one_run`),
  but `state/renewal/` gets no `renewal.json`, so the readers keep reading the earlier file
  (W5-E-4, Q2); the first counted run migrates as above, and from then on a dry run writes its
  statuses and keeps `last_run` (W5-A-2). A made-up count can only do what a missing file (0)
  or a real count could: hide the failures before the upgrade from `R20-renewal`'s failure
  branch, or raise it a run early; it cannot make tokens known. Tests:
  `test_a_refused_run_takes_no_statuses_from_an_earlier_state_file` and
  `test_an_earlier_state_file_is_read_once_and_never_written` (`core/tests/test_cli_renew.py`),
  `test_renew_row_reads_an_earlier_report_until_the_first_run`
  (`api/tests/test_system_health.py`) and
  `test_a_dry_run_after_an_upgrade_leaves_the_alerts_as_they_are`
  (`worker/tests/test_alerts_legal.py`).
- **W5-E-9: the owner of `state\crl` and `state\renewal` cannot change their rights (#151;
  review of the follow-up).** Both folders are new in Wave 5 (W5-B, W5-E-4). Under the earlier
  release's ACL the collector, ingest and the worker hold Modify on `state`, so a compromised one
  could create either folder before the upgrade and own it. `Set-ArciVaultAcl.ps1` replaced only
  the DACL of a folder that exists (`Set-ArciAclEntry` set no owner before the answer to Q4
  below), and an owner keeps the implicit right to read and change the DACL whatever it says: it could give itself Modify
  again, write `renewal.json` (silencing `R20-renewal`, `R20-cert` and `renewal_due`), hold
  `renewal.lock`, or change a stored list. `Get-ArciAclPlan` now gives both folders an entry for
  OWNER RIGHTS (`S-1-3-4`) with `ReadPermissions` only, inherited by what is created below;
  Windows then gives the owner that entry's rights in place of the implicit ones.
  `ReadPermissions`, not `ReadAndExecute`, so that an owning service still cannot list or read
  the folder (nothing for the collector or ingest, W5-E-1). SYSTEM, Administrators and the
  tasks keep their own entries, and neither `Renew` nor `CrlFetch` changes a permission there
  (the immutability lock is used only below `vault`).
  **Administrators own everything below (answer to Q14 on #158; #170, wording).** After every
  run of the ACL step, `BUILTIN\Administrators` owns `state\crl`, `state\renewal` and every
  item below them, whichever account created them (the items below, next). An item a task
  creates there later (a new `renewal.json`, a stored list) is owned by the task's account
  (LOCAL SERVICE) until the next run of the step, and inherits the folder's OWNER RIGHTS entry,
  which leaves its owner `ReadPermissions` only; the tasks' Modify has no `ChangePermissions`.
  So no task can change an entry there, by ownership or otherwise. The immutability lock works
  by exactly that right (the owner of a sealed file changes its entries, as below `vault`), so a
  future immutability lock under `state\crl` or `state\renewal` would conflict with OWNER
  RIGHTS `ReadPermissions`: the plan must change first (its OWNER RIGHTS entry, or who owns
  what there, with its own tests and a contract change), never be worked around by the task.
  **The items below (answer to Q4 on #158).** A service could also have put items of its own in
  either folder before the upgrade, with their own owner and explicit or protected entries,
  which replacing the folder's DACL leaves as they are: a `state\renewal\renewal.lock` with its
  own Modify could hold the lock and stop every Renew run, a made-up `renewal.json` could be
  one the task cannot replace. The plan's entries now carry `ResetOwner` (false everywhere but
  `state\crl` and `state\renewal`), and `Set-ArciVaultAcl.ps1`, which the installer's ACL step
  runs, passes it to `Set-ArciAclEntry -ResetOwner`. That opt-in switch makes
  `BUILTIN\Administrators` (`S-1-5-32-544`) the owner of the folder and of every item below it
  and leaves each item below with the inherited entries only (what `icacls /reset /T` does),
  through the .NET security calls the module already uses (`Get-ArciAccessControl`,
  `Set-ArciAccessControl`); a folder's items are listed only after its own entries are reset.
  Every other entry and the installer's own calls (`admin\`, `admin\keys`, the configuration
  files) never write an owner (`Set-ArciAccessControl`): the tasks lock the sealed files they
  create, and unlock them for retention, by the owner's right to change their entries (their
  Modify has no ChangePermissions), and a folder another account created would hit Set-Acl's
  "not allowed to be the owner" failure. A link below either folder (a reparse point, or a hard
  link: the same file under another name elsewhere) stops the step with an error before the
  folder's own entries are written, since a reset would hand Administrators, and inherited
  entries only, whatever it points to. Every item below is checked first (listing needs only the
  access Administrators inherit from `state`): writing a folder's entries also writes the
  inherited entries of everything below it, and a hard link shares its entries with the file it
  names elsewhere. After the folder's reset its items are listed again, and each is checked once
  more before its own reset. Both folders are also checked before the plan's first entry is
  written (W5-E-11): `state`'s entries, written before them, reach a folder that is not
  protected yet on the first upgrade, and a link in it. In `state\crl`, `crl fetch` itself
  can leave a hard link: `_store_new` writes a list under a `.<random>.tmp` name, links it to its
  final name and then removes the temporary name, so a run stopped in between (a reboot, the
  task's time limit) or a failed removal leaves both names. The next `crl fetch` removes them
  (#170, item Q15): at its start, under its lock, it removes each regular file of `state\crl`
  named as `_store_new`'s temporary names are (`.`, eight of the tempfile module's characters
  `a-z0-9_`, `.tmp`) and logs each removal; a `.crl` file, the index, the lock and every other
  name stay, so D8 holds (the list a leftover name links to keeps its bytes under its own name).
  Until that run the step stops before it writes any entry (W5-E-11);
  the error for a hard link says to delete only the `.<random>.tmp` name in the same folder,
  never a `.crl` file, which is evidence. Tests (`core/tests/test_crl.py`):
  `test_fetch_removes_the_temporary_names_a_stopped_run_left` (a hard link to a stored list and
  a part of a list, both made by the tempfile module as `_store_new` makes them: both names go,
  the list keeps its bytes and its entry), `test_fetch_removes_only_the_temporary_names_of_store_new`
  (lists, one not in the index, the index, the lock, another file, a folder, a symbolic link
  and names that only look alike stay),
  `test_a_leftover_temporary_name_stays_while_another_run_holds_the_lock` and
  `test_the_crl_module_has_no_way_to_delete` (the one removal in `crl.py` is this one). An
  item whose entries give Administrators no right to read or take it over (its owner took them
  away) is taken over all the same by the elevated step: the Windows CI runner shows it for a
  `renewal.lock` that a service left as its own, with inheritance off, which is the case Q4 on
  #158 names. The step itself enables no privilege; where the take-over still fails (a policy that
  took the take-ownership privilege away), it stops with an error naming the item and the
  remedy (every read and write of an item, the link check, the listing and its entries, is
  wrapped alike; a link's own error stays as it is), and `takeown /A /R /F <folder>` in an
  elevated PowerShell takes it over by hand before the step is run again. An item below that
  is gone between its listing and its check or reset (`renewal.json` replaced in one step, a
  `crl fetch` temporary name removed; #170, item N2) is skipped, with what was below it, and
  the step goes on: `Test-ArciItemGone` takes a not-found error (`FileNotFoundException`,
  `DirectoryNotFoundException`, `ItemNotFoundException`, a Win32 error 2 or 3, also wrapped,
  as in a `MethodInvocationException`) for one only when the item is indeed no longer there:
  its folder, listed again, no longer lists its exact name (`Test-ArciItemListed`; a folder
  that is gone itself lists nothing), never `Test-Path`, since Windows drops a trailing dot or
  space from a path and an item still there would read as gone (#170, review of round 2).
  Every other error still stops the step with the take-over error. A name that Windows cannot
  open by its path, made through the `\\?\` prefix (a trailing dot or space, or a device name:
  `CON`, `PRN`, `AUX`, `NUL`, `COM0`–`COM9`, `LPT0`–`LPT9` and their superscript forms, `CONIN$`,
  `CONOUT$`, also with an extension such as `NUL.txt`; answer 5 on #170), stops the link
  check, the pre-flight (W5-E-11) as well as the owner reset, with its own error naming the
  item, Turkish first with English beside it, before anything of the item is read: by its path
  neither its link type nor its entries are its own, and a hard link under such a name would
  otherwise reopen the gap of W5-E-11. Tests:
  `test_acl_plan_least_privilege`, `test_acl_plan_gives_the_renew_and_crlfetch_tasks_their_rights`,
  `test_acl_plan_resets_the_owner_of_the_two_protected_state_folders_only` (exactly the two
  entries carry `ResetOwner`), `test_only_the_acl_script_passes_reset_owner`, and on Windows
  `test_acl_applied_keeps_sealed_file_deny_entries` (`state\crl` created before the ACL step
  with an entry of its own: that entry is gone, and OWNER RIGHTS has `ReadPermissions` only on
  `state\crl` and `state\renewal`), `test_acl_applied_takes_over_the_protected_state_folders`
  (both folders, `renewal.lock`, `index.json` and a folder below `state\crl` with a file in it,
  each owned by the collector or ingest with a Modify entry of its own, set with `icacls
  /setowner`, which uses the administrator's restore privilege; NETWORK SERVICE and Guests stand
  in for the two services, whose own SIDs exist only where they are installed: afterwards
  Administrators own
  them, the items below have inherited entries only, the collector and ingest have nothing,
  LOCAL SERVICE keeps its inherited Modify on the lock, and `logs` keeps the collector as its
  owner), `test_acl_step_stops_at_a_link_in_a_protected_state_folder` (a junction and a hard
  link in `state\renewal`, protected beforehand so that `state`'s entry does not reach them: the
  file they point to keeps its owner and every entry, the inherited ones too) and
  `test_acl_step_takes_over_a_lock_a_service_left_as_its_own` (a `renewal.lock` with
  inheritance off, a stand-in service as its owner and a Modify entry of its own: afterwards
  Administrators own it, it has inherited entries only, and LOCAL SERVICE keeps its inherited
  Modify), `test_acl_owner_reset_skips_only_an_item_that_is_gone` (the classification, with
  every PowerShell; an item in a folder that is gone too is gone, a `dotted.json.` still there
  is not), `test_acl_owner_reset_checks_vanished_items_where_it_lists_and_resets`,
  and on Windows `test_acl_step_skips_an_item_removed_after_its_listing` (a file and a folder
  with a file in it, removed by a wrapper of the module's own `Confirm-ArciNoLink` or
  `Get-ArciAccessControl` as the reset loop reaches them: the step succeeds and takes over
  `renewal.json`) and `test_acl_step_still_stops_at_an_item_it_cannot_reset` (access denied
  on reading an item's entries: the take-over error naming the item);
  `test_acl_link_check_stops_at_a_name_windows_cannot_open_by_its_path` (with every
  PowerShell: a trailing dot, a trailing space and every kind of device name, bare or with an
  extension, stop the check with the error naming the item; names that only look alike pass),
  `test_acl_link_check_reads_nothing_of_a_name_windows_cannot_open`, and on Windows
  `test_acl_step_stops_at_a_name_windows_cannot_open_by_its_path` (a hard link
  `renewal.lock.`, one in a folder `old `, or one named `NUL`, in a pre-created
  `state\renewal` that is not protected: the step stops with that error and nothing written,
  as in W5-E-11).
- **W5-E-10: a failed task state (#171; the follow-up of Q9 on #158).** The task rows gain
  `failed` with a `code` (never the error text): (a) a `crl fetch` that finds the lock held is
  still not recorded (W5-B-2), (b) an index `crl fetch` cannot read is judged by the reader, with
  core `read_index` (`index_unreadable`, no time; an `OSError` stays `missing`), (c) a stored list
  it cannot read is recorded in the index as `run_failure` (`stored_file_unreadable`), Backup's
  `ok` not `true` is `backup_failed`, and Renew is included (`renew_failed`, `renew_refused`).
  Details: "Deviations — #171" below.
- **W5-E-11: the ACL step checks both protected state folders for links before it writes any
  entry (#170, item N1).** `Set-ArciVaultAcl.ps1` wrote `state` before it reached the two
  `ResetOwner` entries, and writing `state` passes its inheritable Modify (collector, ingest,
  worker) on to a `state\renewal` or `state\crl` that a service created before the upgrade and
  that is not protected yet, and through a hard link in it to the file it names elsewhere,
  before the W5-E-9 link check of that folder ran. The script now runs that check, the module's
  `Confirm-ArciNoLink -Below` (exported; the W5-E-9 check and its error texts, moved out of
  `Set-ArciAclEntry` with the take-over error, `Get-ArciTakeOverError`), on every `ResetOwner`
  entry that exists and everything below it after its Windows and administrator checks and
  before its write loop: a link stops the step with no entry written and no folder of the plan
  created. `Set-ArciAclEntry -ResetOwner` keeps its own checks (the folder and everything below
  before the folder's entries, each item again before its reset), calling the same function:
  not a duplicate, since they also cover an item made between the pre-flight and the folder's
  write and any other caller of the switch. Tests:
  `test_acl_script_checks_both_protected_state_folders_for_links_first` (the order in the
  script, the calls in `Set-ArciAclEntry`) and on Windows
  `test_acl_step_finds_a_link_before_it_writes_any_entry` (a hard link in a pre-created
  `state\renewal` that is not protected: the step exits non-zero with the link error, the file
  the link names keeps its owner and every entry, the inherited ones too, the data root, `state`
  and `state\renewal` keep theirs, and neither `kamusm-roots` in the configuration folder nor
  `spool` in the data root is created).

## Deviations — #119 (2026-11-04)

- **#119-1: the activity commit is asynchronous.** `_record_activity`
  (`api/src/arcilogger_api/security.py`) commits `last_seen_at` with `SET LOCAL
  synchronous_commit = off` (decision A1 in the review of #145, 2026-11-04). It changes durability only,
  not the order of MVP-CONTRACTS §6: activity is still committed before the endpoint runs. A
  crash can lose the newest `last_seen_at` (at most about 3 × `wal_writer_delay`); the session
  then counts as idle from the activity before, never longer. That value is neither evidence nor
  audited personal data. `SET LOCAL` ends with that transaction, so the endpoint's transactions
  and every audit write (`append_entry`) stay synchronous. In `api/tests/test_activity_commit.py`,
  `test_every_audit_write_commits_synchronously` asserts it for representative requests
  (sign-in, search, settings change, user created, background request);
  `test_the_check_catches_an_audit_write_in_the_activity_transaction` shows that the check
  catches an audit entry written in the activity transaction; and
  `test_synchronous_commit_is_turned_off_only_for_the_activity` keeps every other engine and
  deployment file synchronous. It holds for every request because of the rule of MVP-CONTRACTS
  §6 that no dependency writes to the request's session before authentication. The guard
  `test_browse_under_concurrent_ingest` now holds the 95th percentile under ingest below the
  browse budget (500 ms) and the slowest request below 10 × that. In the same review A2 (an
  asynchronous spool watermark) was deferred, to be revisited with field figures from a Windows
  site (sahada doğrula), and A3 was declined (2026-11-04).
- **#119-2: the browse guard bounds what ingest adds, not the 95th percentile itself**
  (decided on #119, 2026-11-04; amends the budget of #119-1). `test_browse_under_concurrent_ingest`
  times a settled series before and after the series under ingest and asserts that the 95th
  percentile under ingest less the larger settled one stays below the browse budget (500 ms);
  `CEILING` (5 s) bounds the slowest request of each of the three series. Reason: on a Windows
  runner the audit spool's `os.fsync` waits for the disk's write-back (#90's case; out of scope
  by #119 item 3) in whichever series meets it, settled ones too. Run 41875393810 (#166 at
  `32d4d2a`, job 126733285902) failed the absolute bound: under ingest p50 124 / p95 590 / max
  757 ms (p95: sql 23, commit 7, fsync 511 ms), settled p95 331 ms (fsync 277 ms). Its re-run
  (job 126780370555) passed: under ingest p95 134 ms, settled p95 332 ms with one 507 ms fsync.
  Ingest held the stated rate in both (1,984 and 2,155 lines/s loaded). The absolute browse
  budget of a settled server stays with `test_search_perf`'s Q1 (best of rounds, 500 ms), so a
  regression that slows browse in every series is still caught there.

## Deviations — #122 (2026-11-04)

The rollback's schema downgrade (issue #122, follow-up of question 8 on #121). The interface the
issue fixed is kept (command, flags, exit codes 0/1/2, JSON fields); what differs or was added:

- **#122-1: `arcilogger-db schema downgrade --to <revision> [--json] [--config PATH]`.** It
  stops nothing (the installer stops the services first), takes `MIGRATION_LOCK_KEY` (the lock
  of `arcilogger-db upgrade`) at **session** level, since `0021`'s downgrade commits a step per
  table and month, sets `lock_timeout` 30 s and no statement timeout, and runs alembic's
  downgrade with no seed and no partition step (this version's would make daily partitions
  again). Exit 0 also when the database is at the target already (nothing done, no entry); 1
  when the downgrade fails or refuses (TR/EN on stderr naming `docs/YEDEKLEME.md bölüm 5` /
  `docs/BACKUP.md section 5`; only the committed resumable steps stay); 2 for a revision this
  version does not ship (exact ids only: no `base`, `heads`, `-1` or prefixes; checked before
  connecting) or one not below the database's; **3** (added) when the database cannot be
  reached at connect time. JSON only on exit 0; `retention_days` is `null` only when nothing was
  downgraded.
- **#122-2 (deviation): the `settings.change` entry is written in the transaction in which
  alembic moves its version**, not in a separate transaction after the downgrade. `0021`'s
  downgrade restores 400 in that transaction, so the value, the version and the entry commit
  together: an entry that fails rolls back the version's move, and the next run writes it once
  (with a separate transaction a failed entry after a committed downgrade would be lost: the
  next run finds the target reached). Entry: target `setting:db.retention_days`, actor
  `system`, details `old`, `new`, `was_set_by`, `downgrade {from, to}`, `via`, `os_user`.
  `0021`'s downgrade leaves an administrator's value; no entry is written then.
- **#122-3 (addition): the upgrade's change that `db.window` had not audited yet.** `0021`'s
  downgrade drops the status row `db.retention_days_migration`. When it still says `audited:
  false` (a rollback before the window job's first run after the upgrade), the command first
  writes that change's own `settings.change` entry (`old`, `new`, `was_set_by`, `migration`,
  `changed_at`, as `db.window` would, plus `audited_at_downgrade {from, to}`), in the same
  transaction, then its own. The JSON gets one more field, `upgrade_audit_id` (int or null);
  the installer prints it.
- **#122-4 (deviation, issue #164): when the installer downgrades.** `-Rollback` without
  `-RestoreDatabase` reads both programs' Alembic revisions from
  `venv\Lib\site-packages\arcilogger_db\migrations\versions` (no Python, no database;
  `Get-ArciAlembicChain`, as `Test-PreviousLeaseRollbackModule`'s capability check). Nothing is
  downgraded when this version has no `0021_db_window`, or when the previous version knows this
  version's head (the same revisions, or this version's followed by more). Otherwise the previous
  version's revisions must be the first ones of this version's (the same ids in the same order),
  and the step `database.downgrade` takes the database to max(previous head,
  `0020_fg_traffic_transip_time`) (the floor, #122-5). #122's rule was "only when the previous
  version has no `0021`; a previous version from `0021` on gets no downgrade, also of later
  revisions": after `0022_lease_moves` a rollback to a program with `0021` but without `0022`
  left the database at `0022`, and that program's own `arcilogger-db upgrade` (its repair run, an
  `-Upgrade` to another bundle without `0022`, what `arcilogger-db check`/`current` advise) fails
  with `Can't locate revision identified by '0022_lease_moves'` (issue #164, from the review of
  #163). The way down crosses only revisions whose downgrade is lossless for 5651 data (raw logs,
  log rows, leases and the audit log keep every row): `$LosslessDowngrades` in the installer,
  newest first, each with its judgement in a comment. Today `0022_lease_moves` (drops
  `leases.moves`, the recorded missed moves, and nothing else: derived data, the FortiGate DHCP
  raw logs keep the ACKs, while the database keeps `fg_dhcp` rows for its window only; the moves
  are not rebuilt: a later upgrade brings the column back NULL, no backfill (#127), the lease job
  records the moves it sees from its next run on, and a lease's moves from before the window are
  then gone from the database; the `"vd"` keys stay in `leases.evidence`, #127) and
  `0021_db_window` (merges the daily partitions into monthly ones with every row; drops
  `archive_reloads`, whose sealed days stay in the vault, `db.reload_keep_days` and the window's
  status rows; `db.retention_days` 30 → 400, a longer window, audited by the command, #122-2). A
  revision whose downgrade loses such data goes into `$LossyDowngrades` (none today), with what it
  loses; a rollback across it is refused (#122-7), the way back is the pre-upgrade dump. The plan
  checks the way from this version's head (the database is not above it) down to the target.
  `test_the_lossless_downgrades_are_this_versions_revisions_above_the_floor` asserts that every
  revision of this version above the floor is in exactly one of the two lists, each newest first
  and each entry with its judgement: a new revision fails it until it is judged and listed, and
  none enters the rollback silently. Right before the command the step reads the
  database's revision with this version's `arcilogger-db current`: a database at the target or
  below it (`-Upgrade -SkipDatabase`; one from before `0021_db_window` has no daily partitions) is
  left as it is. The command name is one variable, `$SchemaDowngradeCommand`. The release path
  wave-4 (`0020`) → wave-5 → `-Rollback` is unchanged: down to `0020`, through `0022`'s drop and
  `0021`'s merge. Tests: `test_dry_run_rollback_downgrades_to_a_previous_head_from_0021_on`,
  `test_rollback_from_0022_downgrades_to_the_previous_head`,
  `test_rollback_to_a_version_at_this_versions_head_runs_no_downgrade`,
  `test_rollback_on_a_database_at_the_target_runs_no_downgrade`, and against PostgreSQL
  `test_the_downgrade_to_0021_drops_only_the_lease_moves` (`schema downgrade --to
  0021_db_window` from the head). `arcilogger-db schema downgrade` needed no change: it already
  takes every revision this version ships (#122-1).
- **#122-5 (deviation, accepted by the manager on #147): the floor.** The target is the previous
  head but never below `0020_fg_traffic_transip_time`, the revision `0021` revises. The older
  revisions were made for a previous version to run on, and their downgrades delete or rebuild
  data: `0017` drops `fg_dhcp` (which contract 2.2.1's lease step expects the next upgrade to
  restore), `0018` rebuilds the old NAT settings (the docs said "-Rollback runs no schema
  downgrade"), `0019` drops `source_clock_samples`. The literal rule ("to the previous
  program's head") is one line away (`[Math]::Max($at, $window - 1)` in
  `Get-SchemaRollbackPlan`).
- **#122-6: order and failures.** Services stop (all, ArciIngest and ArciWorker too) →
  `leases.prepare-rollback` (works on the current schema) → `database.downgrade` →
  `program.swap`. A failed downgrade stops the rollback with 3 before the swap: this version
  restarts on its own schema; unless the command certainly changed nothing (exit 2, exit 3, or
  `arcilogger-db current` failed) its `arcilogger-db upgrade` runs first (no time limit; a
  no-op at its head; also when the command answered exit 0 with JSON whose `to` is not the
  target, or could not be started or ended without an answer), which also restores lowered
  leases (this re-upgrade rule: accepted by the manager on #147). The TR/EN text names
  `-Rollback` again and then `-Rollback -RestoreDatabase` (YEDEKLEME/BACKUP section 5); after
  exit 2 only the restore. A successful downgrade followed by a failed swap runs this version's
  `upgrade` too; if that fails, only the collector and the web server run, with a manual step
  naming the command.
- **#122-7: refusals before any change (exit 2).** A previous head that cannot be told (no
  folder, no revision file, an unreadable line, a duplicate, a branch, two bases, a broken
  chain, a head not among this version's revisions), previous revisions that are not the first
  ones of this version's (#164: one missing before its head, or one in another place; the text
  names the first that differs), a way down across a revision not on `$LosslessDowngrades`
  (#164; the text names it), or `-SkipDatabase` when the downgrade is needed. Each is TR/EN; all
  but the last give the command `-Rollback -RestoreDatabase`, the last says to run without
  `-SkipDatabase` or with `-RestoreDatabase`. `-RestoreDatabase` skips the schema check. Tests:
  `test_dry_run_rollback_refuses_previous_revisions_that_are_not_the_first_of_this_versions`,
  `test_dry_run_rollback_refuses_a_way_down_through_a_revision_not_listed_as_lossless`,
  `test_rollback_refuses_a_diverged_or_lossy_way_down_before_any_change`.
- **#122-8 (deviation, accepted by the manager on #147 with the review's items 1–6):
  `-RestoreDatabase` restores into a new database.** `pg_restore --clean --single-transaction`
  into the live database cannot restore a `0020` dump over a `0021` database (`archive_reloads`'
  foreign key on `pk_lookups`, which the dump does not know, makes the whole restore fail), and
  the swap has run by then. The installer now restores into `<name>_restore_<stamp>` (owner,
  encoding, libc locale, database settings and rights copied from the current database; the
  settings and rights in a psql run after `CREATE DATABASE`, so that a failure there drops the
  empty database too) with `pg_restore --exit-on-error`, one transaction per object, not
  `--single-transaction`, as `arcilogger-db restore` (`backup.py` `_restore_into`, for every
  database): one transaction holds a lock on every table, index and TOAST table, which a
  TimescaleDB database's chunks outgrow (`max_locks_per_transaction`); the new database is dropped
  on failure anyway. TimescaleDB: `timescaledb_pre_restore()` in the current database,
  `CREATE EXTENSION timescaledb VERSION '<the current database's extversion>'` and
  `timescaledb_pre_restore()` in the new one, `timescaledb_post_restore()` in the database in
  place afterwards. Then, in one transaction, the current database is renamed to
  `<name>_replaced_<stamp>` and the new one into place; when psql reports an error but the current
  database is already under its new name (a connection lost after the `COMMIT`), the rename counts
  as done. Before any change, the drive of PostgreSQL's data directory (`data_directory`, else the
  service's `-D`) must have the current database's `pg_database_size`, a quarter more and
  `max_wal_size` free (both databases stay and `pg_restore` writes its WAL there; the quarter is a
  margin for TimescaleDB chunks compressed since the dump, which come back uncompressed: sahada
  doğrula), or the restore is refused (TR/EN; a warning when the free space cannot be read or
  `[database] host` is not loopback, whose data directory is a path on another server). A failure
  before the rename drops the new database and leaves the current one as it was; a rename refused
  because another session is open on a database names those sessions (`pg_stat_activity`: a
  pgAdmin window). The replaced database is kept (the rows written after the dump are only there):
  right after the rename commits it is named on the console, then recorded in `install\state.json`
  as one more entry of `replaced_databases` (`name`, `dump`, `replaced_at`,
  `timescaledb_restoring`; a list, so that a second restore keeps the first one's record; a record
  that cannot be written is a warning that names it again, and the restore goes on); removing it
  is the owner's decision. On TimescaleDB it stays in restore mode (`timescaledb.restoring = on`,
  carried by the rename): the console and BACKUP/YEDEKLEME section 5 say to run
  `timescaledb_post_restore()` in it before putting it back; the installer does not (it would
  start that database's jobs). A database whose locale is not libc is refused (restore by hand).
  After the swap any failure of the restore, also a refusal before `pg_restore`, keeps the
  database services stopped. On the database-only path (no `.previous`: the program in place may
  not match the database) so does any failure from the restore's first change on (the current
  database's `timescaledb_pre_restore()`, else `CREATE DATABASE`); a failure before it (a cancel,
  a missing dump, psql not reaching the server, the locale or the space refusal) changed nothing,
  and the four database services go back as `services.stop` found them (issue #155,
  `Get-ArciServiceState`): one that ran (`Running` or `StartPending`) on automatic start is set to
  `delayed-auto` again and started (plain `auto` comes back as `delayed-auto`: the start type does
  not tell them apart), one that ran on manual start is started and keeps it, one that was
  stopped (on manual start after a failed upgrade) stays so, with its start type; each is
  attempted, also the start of one whose start type could not be set. The output says, TR/EN,
  that the database was not changed and that the services are as they were, naming each; if one
  could not be put back it says instead which, with the command that does it, and lists it under
  the manual steps. `install\state.json` stays as it was for the database: a failed upgrade's
  `pending_database_restore` stays recorded, and `last_failure.database_uncertain` keeps the
  previous `last_failure`'s value (false without one). Otherwise the output says the database
  was not changed when the failure came before the rename. Either way it names this version's
  installer for the retry (the copy in
  `<InstallDir>.rolled-back-<stamp>\installer\`, else the running one). A failed upgrade's
  migration names the same way the copy in `<InstallDir>.failed-<stamp>\installer\`: after either
  swap the one in `<InstallDir>\installer\` is the previous version's, whose restore is the
  `pg_restore --clean` above. A failure after the rename committed says the database was restored
  and not to restore again (that would replace the restored database once more); when
  `timescaledb_post_restore()` of the database in place did not run, the database services stay
  stopped and the output gives that command and the one that starts them, otherwise they start
  again. Tested against PostgreSQL 16 and TimescaleDB 2.30.2
  (`test_database_restore_puts_a_0020_dump_in_place_of_a_0021_database[timescaledb]`, run by the
  Linux CI job, failing there without TimescaleDB, psql/pg_dump/pg_restore or PowerShell) with the
  installer's own SQL and arguments; the restore of a site's real chunk count, the free-space
  margin and a TimescaleDB updated between the dump and the rollback stay "sahada doğrula"
  (INSTALL/KURULUM section 16).

## Deviations — #127 (2026-11-04)

- **#127-1: the lease records its missed moves; the missed-move check reads them before the
  first day.** W5-G-17 (b) cut the missed-move check (round 7, D33) to the lookup's days. The
  lease job now stores, with each DHCP lease, the moves the build saw and did not apply, and the
  check reads them where the rows are gone.
  - **Definition (at write time; `correlation/leases.py:579-745`, `_Recorded`):** a kept
    FortiGate ACK (logid mapped to `assigned`) of the lease's MAC for another address, strictly
    inside the lease (`start < t < end`, or the run's end while it is open); for an `fg_dhcp`
    lease on its own interface; not in another configured network than the lease's (the
    networks of the run). The build applies every other ACK of the MAC: it ends the lease at
    that time, which the strict bounds leave out. Every mode (live incremental, range rebuild,
    single days in any order) computes the same value from the same rows (except the bound's
    case below): the run's range `[lo, hi)`, the look-ahead's rows of each followed MAC, and
    the stored moves outside the range, all cut to the lease (a lease that late evidence ends
    earlier loses the moves after its new end), then bounded (below).
  - **Format (`leases.moves`, JSONB, nullable, no default, no index; revision
    `0022_lease_moves`, `models.py:931-945`):** `{"truncated": bool, "groups": [...]}`, a group
    `{"ip", "interface", "vd", "source", "logid", "dhcp_msg", "times"}`, one per those six
    values, `times` as integer epoch microseconds (UTC), sorted, once each; the groups sorted by
    the six fields, `null` after every text (`lookup/refs.py:382`, `moves_json`). No row id (a
    reload changes ids) and no `"from"` field. NULL: not recorded (a lease written before the
    revision, an ARP interval); `"groups": []`: recorded, none. `parse_moves` (`refs.py:431`)
    never raises on a stored value: a damaged value can only lack moves, and a `"truncated"`
    that is not `false` reads as truncated. The column is deferred and raises when loaded by
    accident; only the lease job and the check read it, by `select(Lease.id, Lease.moves)`.
  - **Bound (manager decision 1 on #127, 2026-11-04): lease rows are never dropped, so the
    value must not grow with the lease's life.** After the cut to the lease the job keeps only
    the times after the lease's newest moment minus `MISSED_MOVE_WINDOW` (100 days, the
    check's look-back from a moment; `leases.py:564`, `_MOVES_KEPT_US`), and of those at most
    `MOVE_TIMES_CAP` = 1,000 a lease over all its groups, the newest (of equal times, the
    earlier group's; `refs.py:71`, `bound_moves` at `refs.py:347`). 1,000 holds 100 days of
    ACKs of one other address renewed every 2.4 hours (T1 of a 4.8-hour lease), about 20 KB
    of JSON, and is what the perf guard below decodes for each of its 100 leases.
    - The newest moment is the lease's end; for an **open** lease its newest move, not the
      run's end (refined from the brief): the live job does not rewrite an open lease without
      a new move (`_Keep.STORED`), so an anchor at the run's end would store different values
      in the live job and a rebuild at the same horizon. The newest move changes only when a
      move is added, which every mode writes. Both cuts keep the newest times, so bounding the
      stored, bounded value together with the run's new times gives what bounding all the
      times once gives: live, range and single days still store the same value
      (`test_bounding_again_with_the_same_times_gives_the_same_value`, and the `truncated`
      variant of the consistency test below, which scales the bound down to one time and 12
      hours), except a lease that late evidence ends earlier after the bound dropped times
      from it: the times dropped against moves after its new end do not come back, so it
      keeps fewer times than a rebuild from scratch, marked `truncated` (positive evidence
      only: the check misses them). A rebuild from scratch (leases deleted) stores the full
      value (`test_a_late_end_keeps_fewer_times_of_a_truncated_lease`).
    - **`truncated`** becomes `true` when the bound drops a time, and stays `true` while a
      run takes part of the lease from the stored value (a dropped time cannot come back from
      it); a run that recomputes the whole lease from rows (a closed lease inside its range
      `[lo, hi)`) sets it from what it drops itself (`leases.py:699-705`). It records that
      the list may lack older moves; nothing is ever added that was not seen.
    - **How the check reads a truncated value:** as any other (`attribution.py:1353-1374`,
      `_recorded_moves`; `_recorded_move`, `_bears` and `_move_counts` are unchanged). The
      recorded moves are positive evidence only: each one is tested again and can only lower
      a chain; an absent move, whether never seen, before the upgrade or dropped by the bound,
      was never evidence of anything, so a truncated list is never read as "no move": the
      chain keeps the confidence its rows give, and the cut look-back keeps its
      `lookback_clamped_to_window` note, which every lookup reaching before its first day
      carries anyway. The flag is therefore not read by the check; it is stored so that a
      reader of the column (support, a later owner decision on the note) can tell a complete
      list from a truncated one.
  - **Read (`lookup/attribution.py:1353-1407`):** for a chain whose window starts before
    `reads_from`, one more query reads the moves of those leases; a move counts in
    `(since, min(reads_from, T − 1 µs)]` and is tested again with today's settings and networks:
    rules 1, 2, 4 and 5 of `trust.refusal` (`stored_refusal`, `attribution.py:232`; rule 5 lowers
    the message ASCII-only, as PostgreSQL's `lower()` does under the installer's C locale), the
    logid still mapped to `assigned`, `_bears` (interface, VDOM, D33's key) and an address other
    than the chain's. Inside the days nothing changes: the rows decide.
  - **Positive evidence only; the clamp note is unchanged (W5-G-17 (b)).** A recorded move can
    only lower a chain. `_targets` keeps `_from(since, mark=True)`
    (`attribution.py:1165-1171`), so a look-back that reaches before the first day still carries
    `lookback_clamped_to_window` with its text (`texts.py:247-257`), whether or not moves are
    recorded or truncated. Dropping the note for a fully recorded lease would need a texts
    change and is an owner question.
  - **Gaps that stay (Q1 and Q2: the defaults, accepted by the manager on #127):** (a) Q1: the
    held branch (c2, `attribution.py:1247`) keeps the days-only reach: it finds rows the build
    refused, which the job never keeps; (b) Q2: the network filter is applied at write time: a
    move between two networks configured differently at build time and merged or unconfigured
    since is recorded nowhere; it is on the field checklist as **V18** ("sahada doğrula",
    `docs/KURULUM.md` bölüm 16 / `docs/INSTALL.md` section 16: before merging two networks or
    deleting one, look for MACs that took an address in both, and record the change and its
    date); (c) rows refused at build time and later trusted, and rows loaded but never
    rebuilt, are not recorded; (d) a lease written before the upgrade (NULL) reads the days
    only, as under W5-G-17, until a run follows it; (e) a lease that outlives the bound lacks
    its older moves (`truncated`).
  - **No backfill and no recommended rebuild.** The revision cannot run D33 and the trust rules
    (revisions never import application code), and only the window's rows are in the database.
    Reloads do not trigger lease rebuilds: `leases.late_evidence` ignores days older than
    `db.retention_days` (`correlation/late.py:17-20`, `:72-75`), and an archive reload restores
    the same line counts per raw day (`IngestService._reset_file`), so nothing re-records old
    moves from reloaded rows. Every existing lease keeps NULL; the job records from its next run
    on.
  - **One-off write on the first incremental run after the upgrade:** every open DHCP lease of
    the older program (moves NULL) is followed by the run and written once with its moves
    (`{"truncated": false, "groups": []}` for most), with a new `updated_at`
    (`leases.py:663-706`). Nothing reads NULL and "none recorded" differently in a lookup;
    later runs read an open lease's moves only when they change (`run_incremental` tests them
    for NULL only, `leases.py:1382`). A lease that ended before a run's range stays NULL.
  - **Performance:** the new guard `test_the_recorded_moves_of_a_hundred_chains_are_fast` (100
    `mt_dhcp` chains, each lease at the cap: `RECORDED_TIMES = MOVE_TIMES_CAP`, 1,000 recorded
    times, marked truncated, decoded and tested in memory) stays under the 0.2 s of 3.8
    (0.098 s for the whole check, of which 0.032 s `_recorded_moves`, in the 2026-11-04 run with
    the bound; 0.10–0.14 s and 0.03–0.06 s in the runs before it; the guard asserts the
    decoded result, 100 leases with all their times, and `parse_moves` only checks a value
    already in the job's form, `refs.py:422-428`); the existing
    `test_the_converted_row_query_of_a_hundred_chains_is_fast` is unchanged (0.067 s). With
    the bound no lease decodes more than the guard's. `_seed_candidates` reads the moves of every
    seed on every rebuild (`leases.py:869`), so each same-day rebuild of the service
    (`worker/service.py:246`) reads all open leases' moves (at most 1,000 times each): their
    part outside `[lo, hi)` must be recomputed, so the incremental job's shortcut does not
    apply.
  - **Rollback (manager decision 4 on #127):** changed by issue #164 (#122-4): `-Rollback` to a
    `0021` program now downgrades to `0021_db_window`, which drops `leases.moves` only (below);
    a later upgrade brings the column back NULL and the lease job records from its next run on.
    The rest of this item is the decision as made on #127. Per #122-4, `-Rollback` to a
    previous version from `0021_db_window` on runs **no** downgrade, so after a rollback to a
    `0021` program the `leases.moves` column stays: the `0021` program inserts leases with
    `moves` NULL (it does not know the column) and leaves the moves of the rows it updates. Stale moves are harmless:
    moves only ever add evidence (a move the `0021` program saw is simply missing), and the
    `0022` program cuts them to the lease, and bounds them, on its next write of that lease.
    When a downgrade below `0022_lease_moves` does run (#122's command to an older target), it
    drops `leases.moves` only (`v0022_lease_moves.py:55-56`): no raw log, log row or other lease
    column changes. The `"vd"` keys of #127-2 stay in `leases.evidence` either way; the previous
    program ignores them (`EvidenceRef.from_dict` reads its own keys) and writes elements
    without them when it rewrites a lease. Nothing 5651-relevant is lost: the moves are derived
    data.
  - **Tests:** renamed `test_the_missed_move_check_reads_from_the_first_day_and_says_so` →
    `test_the_missed_move_check_reads_the_recorded_moves_before_the_first_day` (and its
    `EMITTED_BY` entry in `test_lookup_fortigate_only.py`); new in `test_lookup_lookback.py`:
    `test_the_lookup_reads_the_moves_the_lease_job_recorded` (job to lookup, nothing stored by
    hand), `test_a_recorded_move_is_tested_again_with_todays_settings`,
    `test_the_recorded_moves_reach_up_to_the_first_day`,
    `test_a_recorded_move_of_a_fortigate_lease_needs_its_interface_and_vdom` (also keeps the
    tests' `record_moves` equal to what a job run writes); `worker/tests/test_lease_moves.py`
    (with the bound: `test_a_flapping_mac_stays_at_the_cap_and_keeps_the_newest`, 1,200 hourly
    moves over seven weeks: under the cap after a first rebuild, then the newest 1,000 and
    `truncated` after the catch-up and after a rebuild from scratch;
    `test_moves_older_than_the_window_before_the_end_are_dropped`: the 100-day cut of a closed
    lease and the newest-move anchor of an open one),
    `worker/tests/test_lease_moves_format.py` (the bound, its tie order, bounding again, a
    damaged `truncated`), `worker/tests/test_lookup_stored_refusal.py`,
    `db/tests/test_migration_0022.py`; the `recorded` variants of
    `test_a_mikrotik_lease_kept_open_reads_back_one_probe_window`; the guard above. Live ==
    range == single days for the moves and the refs' VDOMs is checked in
    `worker/tests/test_leases_consistency.py` only (manager decision 2(d)): its `snapshot` now
    holds both, for every scenario there, and `test_the_recorded_moves_agree_in_every_build`
    (`recorded` and `truncated`, the latter with an open lease whose only move lies long before
    the last run's end) took over the scenario of `test_lease_moves.py`, whose own copy of the
    check is removed.
- **#127-2: a stored FortiGate ref keeps the VDOM of its row.** Every `fg_dhcp` ref the lease job
  writes into `leases.evidence` carries `"vd"` (the row's `extra.vd`, `null` without one;
  `refs.py:208`, `ref_dict`). It is not part of `EvidenceRef`, the API or the stored outcomes
  (`to_dict` is unchanged). A lease's newest record read from its stored ref (W5-G-17 (b)) now
  has its VDOM, so D33's VDOM rule applies to it as to the row:
  `test_a_newest_record_before_the_days_is_the_stored_one[ref-with-vdom]` goes from `low` to
  `high` (an ACK of the MAC in another VDOM is skipped). `_annotated` (`leases.py:888`) takes a
  seed's VDOM from its stored ref first, so D33 still closes it once the row of its newest ACK is
  gone. A ref written before the upgrade has no `"vd"` and keeps today's rule (any VDOM counts);
  it gains the key when a rebuild reads its row again, so an answer for an old moment can change
  once, when that happens.
- **Files outside #127's own, edited with the manager's grant (decisions 2 and 3 on #127):**
  `db/tests/test_migrations.py` (`HEAD`); `db/tests/test_cli_downgrade.py` (#147, merged): its
  `REVISION = "0021_db_window"` meant two things, so the pins that mean "the head" (the head
  assertion, the downgrade's `from` in the JSON, the audit entries and the text) use a new
  `HEAD = "0022_lease_moves"`, and `REVISION` stays for what is 0021's own (the version a run
  that fails in 0021's steps leaves, the upgrade change's `migration`, a target above the
  database); `docs/DATA-MODEL.md` (the revision list, the summaries TR/EN and the `leases` row:
  `moves` with its bound and `truncated`, the `"vd"` key of an evidence element);
  `docs/CORRELATION.md` (TR and EN: the recorded moves before the first day and their bound);
  `docs/KURULUM.md` bölüm 16 / `docs/INSTALL.md` section 16 (V18, above);
  `worker/tests/test_leases_consistency.py` (above); `db/tests/test_migration_0021.py` (its
  `upgrade(...)` calls pinned to `revision=REVISION`, as `test_migration_0020.py` does, so they
  stop at `0021_db_window` under head `0022_lease_moves`; granted on #127).
- **Pins outside #127's files still open, follow-ups for their owners:**
  `lookup/types.py:330-332` (`EvidenceRef.to_dict` says it is also the element
  format of `leases.evidence`; an `fg_dhcp` element now also carries `"vd"`);
  `docs/ASSUMPTIONS.md` (:226 and :479 name the checks V1–V17 of the Wave 3 contract; V18 is
  §16's own, as the list's introduction says, and not a Wave 3 number).

## Deviations — #171 (2026-11-04)

The Sistem page shows a task whose latest run recorded a failure (issue #171, follow-up of Q9 on
#158). The design comment on #171 and the manager's answers there are the brief; the deviation
line is W5-E-10, and what it amends in detail:

- **#171-1: a task state `failed` and a `code` (amends W4-D §2.6.1 and §2.6.2, and W5-E-2's "the API
  has no failing task state").** `TaskHealthState` (`system_health/models.py`) is `"ok" | "late" |
  "missing" | "not_used" | "not_observable" | "failed"`, mirrored by `system-health-types.ts`.
  `TaskHealth` gains `code: str | None` after `state`, as `ApplySystemHealth.code`: one of
  `backup_failed`, `index_unreadable`, `stored_file_unreadable`, `renew_failed`, `renew_refused`
  (`tasks.py`, `TaskFailure`) when `failed`, null in every other state. The answer never carries the
  error text a task wrote, which can hold paths, URLs, host and user names (KVKK; precedent
  `system_health/jobs.py`). `last_evidence_at` of a `failed` row is the failure's time (null:
  unknown). `judge` decides in this order: `not_used`, `not_observable`, `failed`, `missing`, `late`
  or `ok`. `failed` holds until the next completed run, however old the failure (it outranks
  `late`); a failure time later than now is no evidence, as for the other times (`_until_now`).
  `TaskRow.read` returns a datetime, or a `Reading` (`at`, `failure`) for the three tasks below.
  Backup's `last.json` and Renew's `renewal.json` are read once per request; CrlFetch's index is
  read by core `read_index` (what stops a run, and its `run_failure`) and, unless an accepted
  `run_failure` decides the row, once more by W5-C's `read_crl_index` for its times. Its
  modification time then counts only when it did not change between the two reads: a stopped run
  that writes its `run_failure` in between is no completed run.
- **#171-2: the Backup row (amends the Backup row of W4-D §2.6.2).** `state/backup/last.json`
  is read once (`_backup`): a file that is no object or cannot be read is no evidence
  (`missing`, as before); `ok` not `true` (as `R20-backup`'s `check_backup` reads it) is
  `failed`, code `backup_failed`, with `finished` as `last_evidence_at` (a failed attempt writes
  the time it failed there; null when it does not parse or is later than now); otherwise `late`
  or `ok` from `finished`. `[backup] enabled = false` stays `not_used` first, as `check_backup`
  resolves everything then. A later success writes `ok: true` and clears it. `error` is never
  read. No change to `db/src/arcilogger_db/backup.py` (there is no `last_failure` key; the
  writer's keys stay those of `write_last_state`).
- **#171-3: the CrlFetch row, three cases (amends W5-E-2: "a file that cannot be read shows
  `missing`" and "A run that exits 1 without writing (W5-E-6) cannot be shown as failed either";
  and W5-E-6: "… or the index or a stored file cannot be read, and leaves the index as it is"
  and "A run that exits 1 because it cannot read the index or a stored list writes nothing …
  `TaskHealthState` has no failed value … an open question to the manager on #158").**
  - **(a) The lock is held:** nothing is recorded (W5-B-2 unchanged; answer to Q1 on #171, no
    `run.json`). The run holding the lock writes the index, so the row stays fresh; a holder
    that hangs shows `late` 26 h after the run before (said in `tasks.py`'s docstring). Writing
    the index without the lock would also share `atomic_write_bytes`' temporary
    `.index.json.tmp` with the holder.
  - **(b) The index cannot be read:** `crl fetch` writes nothing (the index is left as it is).
    ArciApi judges it with the core parser of `crl fetch` itself,
    `arcilogger_core.crl.read_index(layout)`, not W5-C's `read_crl_index`, which is more lenient
    (it reads a UTF-8 byte order mark, which Windows PowerShell 5 `Set-Content -Encoding UTF8`
    writes and on which every run stops). A `CrlError` whose `__cause__` is not an `OSError`
    (bad JSON, a byte order mark, another schema, `lists` or `failures` that are no list), or a
    document nested too deeply to parse (`RecursionError`), is `failed`, code
    `index_unreadable`, `last_evidence_at` null (the file's modification time is no run). An
    `OSError` (ArciApi cannot read the file) is no evidence: `missing`, as before; no index is
    `missing` as before. This replaces W5-E-2's "a file that cannot be read shows `missing`"
    for content `crl fetch` rejects.
  - **(c) A stored list cannot be read** (`CrlError` from `_file_sha256` inside the run): the
    run writes the index **as it was read**, not its half-updated lists, with one more
    top-level key `"run_failure": {"at": <the run's at, site time, seconds>, "error":
    "stored_file_unreadable"}` (`_record_run_failure`; its code is the new Literal
    `RunFailureCode`, not the per-URL `FailureCode`; never `str(exc)`, which names the file),
    then raises the original `CrlError` (exit 1, as before). When that write itself raises
    `OSError`, it is logged and the original `CrlError` is still raised (the CLI catches only
    `CrlError`). Lists stored earlier in that run stay as files outside the index; the next
    completed run indexes them. The next completed run (exit 0, 1 with recorded failures, or 2
    with no URL known; an invalid `network.https_proxy` exits 2 before `fetch` and writes nothing)
    rebuilds the document from its literal, which has no `run_failure`, so the record clears
    without a deletion (`test_the_crl_module_has_no_way_to_delete` still holds). The API reads
    the record from the same `read_index` call as (b): a `run_failure` whose `at` parses and is
    not later than now and whose `error` is a known code (`CRL_RUN_FAILURES`) is `failed` with
    that code and `at` as `last_evidence_at`; any other record is ignored, and the row is then
    judged by its lists and failures as before.
  - **W5-E-6's modification-time rule (answer to Q8 on #158)** now also requires that the
    index has no `run_failure`: an index rewritten by a stopped run is no completed run. "Each
    run that gets past its lock and its reads rewrites the index with the failures of that run
    only" holds for completed runs; a stopped run (c) writes the index it read with its
    `run_failure`.
  - **Per-URL `failures` stay `ok`** (W5-E-2 unchanged): exit 1 "some failed (recorded)" is a
    completed run, and whether the lists are fresh is `R20-crl`'s question.
  - **`crl.py` hunks (coordination with #170), against `651c433`:** `@@ -110,0 +111,4 @@` (the
    Literal `RunFailureCode` after `FailureCode`), `@@ -931,3 +935,4 @@` (`fetch`'s docstring),
    `@@ -947,27 +952,34 @@` (the `try`/`except CrlError` around `fetch` from `tried: list[str] =
    []` through the index's `atomic_write_text`) and `@@ -1016,0 +1029,15 @@` (the helper
    `_record_run_failure`, before `_fetch_one`). `with _one_run(...)`, `index =
    read_index(layout)` and the `lists` dict (lines 942-946) are unchanged, so #170's cleanup
    at the start of the run under the lock does not touch the same lines; the docstring hunk
    is in `fetch` too.
- **#171-4: §2.2's index format gains an optional top-level key** `"run_failure": {"at": "...",
  "error": "stored_file_unreadable"}`, present only in an index written by a run that stopped
  on a stored list it could not read. `read_crl_index` (`worker/.../alerts/system.py`, W5-C) and
  `crl._index` ignore it, so `R20-crl`, `crl_stale`/`crl_fresh` and the legal status are
  unchanged; `crl status` prints it with the rest of the index. The standalone verifier never
  reads `state/crl/index.json`, so neither verifier's verdict changes (rule 5).
- **#171-5: the `fetch` docstring.** "(the index is then left as it is)" now holds for a held
  lock and an index that cannot be read; a stored file that cannot be read is recorded as
  `run_failure` (#171-3 (c)).
- **#171-6: the Renew row (answer to Q3 on #171; amends W5-E-2's "a refused or failed Renew run
  … is `ok` there").** `last_run.outcome` `failed` (exit 3) or `refused` (exit 2) of
  `state/renewal/renewal.json` (or of the earlier release's `state/renewal.json`, W5-E-4) is
  `failed`, code `renew_failed` or `renew_refused` (`RENEW_FAILURES`), with `last_run.at` as
  `last_evidence_at` (null when it is later than now), however old, until a run that renews or
  finds nothing due; a dry run keeps `last_run` and so the row. Read with W5-C's
  `read_renew_runs`, which already returns `outcome`; the run's `error` is never read, and
  `failures_in_a_row` stays `R20-renewal`'s. No change to `renewal.py` or to the worker.
- **Docs (W4-G files, minimal):** `docs/UYUM-DURUMU.md` / `docs/COMPLIANCE-STATUS.md` §4.6 (the
  state sentence with the five codes, and the Backup row), `docs/MVP-CONTRACTS.md` §19.5 (the
  state union, `code`, the Tasks sentence and the Backup row) and
  `docs/adr/0017-compliance-and-system-pages.md` D9 (the state list). The task tables still
  have no Renew or CrlFetch row (predates #171).
- **Answered on #171 (2026-11-04):** Q1, (a) stays unrecorded and there is no `run.json` (a
  writer without the lock is the larger risk; a holder that hangs shows `late` after 26 h). Q2,
  yes: (b) is judged by ArciApi with the core parser, `failed` / `index_unreadable` with no time,
  an `OSError` stays `missing`. Q3, Renew is included (#171-6).
- **Tests:** see the PR. Changed: in `api/tests/test_system_health.py`,
  `test_task_states_from_evidence_files` (a failed Backup attempt is `failed`, no longer `ok`),
  `test_renew_and_crlfetch_are_judged_by_their_state_files` (a refused or failed Renew run is
  `failed` / `renew_refused` or `renew_failed` until a run that works, no longer `ok`; a damaged
  index, also one nested too deeply to parse, is `failed` / `index_unreadable`, no longer
  `missing`) and
  `test_crlfetch_after_the_list_addresses_are_gone_shows_the_run` (Q9's damaged index, and one
  with a byte order mark that `read_crl_index` still reads, is `failed` with no time). New in
  `api/tests/test_system_health.py`: `test_backup_row_shows_a_failed_attempt`,
  `test_crlfetch_row_shows_a_run_that_stopped`,
  `test_crlfetch_index_rewritten_while_it_is_read_is_no_completed_run`,
  `test_crlfetch_index_arciapi_cannot_read_is_missing`, `test_failed_rows_name_no_error_text`
  (also Renew's `error`), `test_run_failure_leaves_the_alerts_as_they_are` and
  `test_the_failure_codes_are_the_ones_the_tasks_write`; in `core/tests/test_crl.py`:
  `test_a_stored_list_that_cannot_be_read_stops_the_run_and_is_recorded`,
  `test_a_stopped_run_whose_record_cannot_be_written_still_raises` and
  `test_a_list_that_cannot_be_stored_stops_the_run_unrecorded`.

## Deviations — #179 (2026-11-05)

- **#179-1: the two guards of the converted-row check take the best of 5 timed rounds and print
  their split.** `test_the_converted_row_query_of_a_hundred_chains_is_fast` and
  `test_the_recorded_moves_of_a_hundred_chains_are_fast` (`worker/tests/test_lookup_performance.py`)
  run the lookup once to warm up, then 5 timed rounds (`ROUNDS`, `_best_lookup`), and hold the best
  round's `_missed_moves` to the bound. The bound stays 0.2 s on both platforms (3.8, #127-1). The
  structural assertions hold for every round (one call, the statement's shape and first day, every
  chain's moves decoded); the rows are read again from the best round's statement. Past pytest's
  capture, each guard prints one `guard split, …` line every run: every round's seconds, and the
  best round's split into SQL (SQLAlchemy's cursor events while `_missed_moves` runs, with the
  statement count and the `leases.moves` statement's share), decode (`_recorded_moves` less that
  statement: SQLAlchemy's work for it, the jsonb values read at fetch and `parse_moves`), judging
  (`_bears` and `_recorded_move`, with the calls of `_move_counts` and `stored_refusal`) and other;
  a round with a full (gen-2) collection of Python's garbage collector inside `_missed_moves` shows
  their count and pause beside its seconds. `event_site` and `moves_site` end with a `CHECKPOINT`
  after their writes and `VACUUM (ANALYZE)` (as `test_search_perf` for #90), so their write-back
  does not run into the timed rounds; its seconds, or that it was skipped (without a superuser or
  `pg_checkpoint`), end the guard's line.
  - **Failures:** run 41883865419/1, job 126763249694 (Windows, recorded-moves guard): 0.216 s, of
    which `_recorded_moves` 0.050 s. Run 41913167039/1, job 126865764532 (Windows, recorded-moves
    guard): 0.211 s / 0.048 s. Run 41924238041/1, job 126904942486 (ubuntu-24.04): converted-row
    0.287 s, while the same job's C3 `converted_row` (the same `_missed_moves` on compressed chunks)
    was 0.048 s.
  - **Local figures (plain PostgreSQL, the Windows layout), before and after #163:** converted-row
    median 77.3 → 82.9 ms with the same plan; recorded-moves 117.4 ms, of which reading the moves is
    about 35 ms. The timed round is warm: the same connection as the warm-up, compilation paid in
    the warm-up, psycopg's prepare threshold not reached. With this change, six serial runs (two of
    the whole file, four of the two guards alone) gave a best of 0.078–0.080 s for the converted-row
    guard and 0.112–0.129 s for the recorded-moves guard; the other rounds of the whole-file runs
    took 0.080–0.092 s and 0.119–0.146 s, with one recorded-moves round of 0.349 s. Run alone, the
    converted-row guard's fifth round took 0.195–0.225 s in all four runs, a gen-2 collection of 133
    ms in the one measured; the best of 5 leaves it out.
  - **The main cost** is the nested loop of the statement's MAC branch: about 60 of 65 ms locally,
    the `fg_dhcp` Append estimated at 27 rows against 5,699 returned (its join filter discards
    564,201). Changing it is product code (`lookup/attribution.py`, `_missed_moves`): #185, not
    this change (the manager's answer to Q2 on #181).
- **#179-2: the recorded-moves guard's bound on windows-latest is 0.21 s** (decided on #181:
  Linux keeps 0.2 s; Windows takes the slowest best of 5 of passing CI runs plus a stated
  margin, unless its slow runners stay under about 0.17 s). Eleven passing readings of the two
  throughput jobs (runs 41950691995 and 41956302626, the latter's single jobs re-run one at a
  time), each a best of 5 with its split, are kept as `MOVES_READINGS` in
  `worker/tests/test_lookup_performance.py` with their run, job and gauge:
  - **windows-latest:** converted-row 0.052–0.132 s; recorded-moves 0.085–0.173 s. The four
    slow-class runners (gauge 16.72–17.87 µs a line, the class of both failures) gave 0.161,
    0.173, 0.169 and 0.170 s, almost all of it the statement's SQL (0.113–0.127 s) plus 0.034 s of
    decoding.
    Single rounds of those runs reached 0.209 and 0.216 s, which the one timed round of before
    would have failed on.
  - **ubuntu-24.04 (TimescaleDB):** converted-row 0.024–0.041 s; recorded-moves 0.047–0.090 s,
    gauges 7.97–15.86.
  - **The rule:** `MOVES_BOUNDS["win32"]` = 0.173 s × `MOVES_MARGIN` (1.2), rounded up to 10 ms,
    0.21 s. The converted-row query keeps 0.2 s on both (`CONVERTED_BOUND`; its slowest best is
    0.132 s), and Linux keeps 0.2 s for both guards. `test_the_bounds_hold_every_ci_reading` checks
    every reading against its bound and the Windows bound against the rule, so a reading added
    later that the bound no longer holds, or a bound that no longer follows from the readings,
    fails at once. When #185 makes the MAC branch cheaper, the readings are taken again and the
    Windows bound goes back to 0.2 s.

## Deviations — #136 (2026-11-05)

- **#136-1: `xz-unsupported` is a failure (`fail`, HATA), and the file is still checked.** A
  Python built without `lzma` cannot decompress an `.xz` file, so the deep content check of that
  file, and an extract taken from it, are not done. The verifier reports `xz-unsupported` for each
  such file (where: its day) and extract (where: "alıntı") instead of `file-content-mismatch` or
  `extract-mismatch`. Its text says that the check could not be done, not that the file was
  changed. The level is `fail` because a check the package relies on was not done, so the
  package is not verified (like `root-none`). The stored file's SHA-256 is still compared with
  the manifest (`file-ok`), and gzip days and their extracts verify as before.
  `VERIFIER_VERSION` is "9" (#177 took "8", W5-A-19). The case is in `sealed_format_cases.py`,
  run with `lzma` taken away in-process. `test_sealed_formats.py` also runs `verify.py` as a
  script with `lzma` and `_lzma` blocked, and covers an extract from an xz day.
- **#136-2: only a stamped manifest protects a file from `compress_deterministic`.**
  - **The refusal:** the sealer refuses with a TR/EN `SealError` (not retryable, so the alert is
    critical) when the same day's file in the other format is locked, or when a manifest of its
    folder with its time stamp beside it names it. A stamped manifest that cannot be read
    protects every file of the folder.
  - **The narrowing:** a manifest without a time stamp does not protect the file. It belongs to
    the same failed attempt (the stamp did not come back, e.g. the TSA was down), and the retry
    rewrites it. Taken literally, "a manifest names it" would block that retry for good after a
    `seal.compression` change.
- **#136-3: the guide's wording.**
  - **Which sentence:** the issue's "§2" is read as item 2 ("Mühürleme") of §1 of
    `docs/DENETCI-KILAVUZU.md`, where "only the line's stream" stands. §2 (the retention table)
    does not mention it.
  - **The Python commands:** the one-liner that opens xz files is dropped (TR §5 "Mühürlü
    dosyaları açmak", EN "Sealed file formats"). The guide now says to run C2 first, or to use
    7-Zip, `xz -dc` or `verify.py`.
  - **Not changed:** the `range_sha256` commands of §9 (TR and EN). They compare their output
    with a stored hash, so a shortened output fails and is never read as a match (question on
    #136).
- **#136-4: the memory ceiling is documented, not capped.** `seal.xz_preset` keeps 0–9 (the
  manager's preference on #136). `docs/DATA-MODEL.md` "Sealed files" gives the measured memory
  and the estimate for 8 threads. The H5 wording there now says "a fifth of half the limit, or a
  tenth of PT2H". The extra decompression that verifies each sealed file was measured on a 1 M
  line day: 4.4 s for xz 6 at 148 MB/s, about 35 s for a 5 GB day. The issue estimated about a
  minute.

## Deviations — W5-K (2026-11-05)

- **W5-K-1: the method names.** K2 names `UsageSettings.period(moment)`. The code has
  `UsageSettings.part_of(moment)` (one minute) and `day_parts(day)` (every minute of a site day),
  with the type `Part` and `PARTS = ("work", "break", "off")` in
  `worker/src/arcilogger_worker/usage/config.py`. The reason: `query.Period` is already the date
  range of every usage answer, and a second "period" in the same module would read as a date range.
  The database column is `period`, as K3 says. `is_off_hours()` stays: a break is not off hours.
- **W5-K-2: the `/usage` exports always have the break column.** "Molada aktif (sn)" follows
  "Mesai dışı aktif (sn)", and "Mola süresi (sn, tahmini)" follows "Mesai dışı süre", also
  without breaks (then 0). A CSV whose columns come and go with a setting breaks the tools that
  read it. K5's conditional row and column stay as written for the scheduled reports (PDF, HTML,
  Excel), which a person reads.
- **W5-K-3: the report data has a top-level `breaks` flag.** It is true when the work hours had a
  break on a work day of the report (`UsageSettings.has_breaks`), and the documents show the break
  row and column by it (K5). The change is additive; schema 1 stays.
- **W5-K-4: the rollback list.** `0023_usage_period` is in the installer's `$LosslessDowngrades`
  (#164's test requires every revision above the floor to be judged). Its downgrade first deletes
  the days that have break rows from the seven rollup tables and `usage_days`: usage analytics,
  derived data. The older program rebuilds by itself only the last `usage.auto_rebuild_days`
  days (default 7) of them; an older day inside the 30-day window stays empty until a rebuild is
  requested, and a day before the window also needs its logs reloaded from the archive
  (`docs/KULLANIM.md` §2.5). Folding the break rows into the work rows instead was weighed and
  left out: sessions across a break and the JSON columns (`hosts`, `top_domains`, timelines)
  cannot be merged exactly in SQL, so the older program's own rebuild is the honest source. Raw
  logs, log rows, leases and the audit log keep every row.
- **W5-K-5: files outside the ownership of §6.6.**
  - `deploy/windows/installer/Install-Arcilogger.ps1`: only the W5-K-4 list entry.
  - `db/tests/test_cli_downgrade.py`, `db/tests/test_migrations.py`: the head.
  - Three web places that build a `Metrics` object, so `npm run check` stays green with the new
    required field until W5-P: `web/src/pages/usage/usage-format.ts` (`sumMetrics`), its test and
    `web/e2e/mock-usage.ts`.
- **W5-K-6: the upgrade alone rebuilds nothing.** A work-hours value without breaks and `by_day`
  keeps the Wave 4 fingerprint (`[days, start, end]`), so an upgrade does not rebuild the recent
  days; only a changed setting does (test `test_breaks_and_per_day_hours_split_the_day_in_three_parts`).
- **W5-K-7: the subject answer carries the day's breaks.** P2 shades break bands on the person
  timeline, and the usage roles cannot read `/settings`. `GET /usage/subjects/{key}` therefore
  has `breaks` (`[{start, end}]` instants, one-day periods only, like `timeline`; empty
  otherwise). They come from `usage_days.details.breaks`, the minute runs the rollup itself
  counted as break, so the bands match the figures even after the setting changed. Additive.
- **W5-K-8: `0023_usage_period` changes the column in place.** `ALTER COLUMN off_hours TYPE text
  USING …`, then `RENAME COLUMN … TO period`: one rewrite per table with its indexes and no dead
  rows, instead of a new column, a full-table `UPDATE` and a new primary key (review of #206).
  The primary keys keep their names and column order; the downgrade is the mirror image.

## Deviations — W5-L (2026-11-05)

- **W5-L-1: one pull request with W5-K.** The manager's session can push only one branch, and
  W5-L changes the files W5-K changes. W5-K and W5-L therefore come as one pull request (#206),
  not two. #199 and #200 both close with it.
- **W5-L-2: `data_until` is the existing `computed_until`.** Every usage answer has carried
  `computed_until`, "how far the rollups have processed traffic", since group 2. L3's `data_until`
  is that field, so the answers add only `updated_at`. That is the `computed_at` of the period's
  last computed day, the day `computed_until` comes from: a rebuild of an earlier day does not
  move "Son güncelleme" (amended 2026-11-06 after #209's review; it was the newest `computed_at`
  of all the period's days). The web types get it (types only).
- **W5-L-3: the back-off measures from the last refresh's start.** `usage_days` keeps the start
  of the last rollup (`computed_at`) and its `duration_ms`, measured until the day's rows are
  written (the commit after it is not included; the job's log shows the same figure). A refresh
  starts when `now ≥ computed_at + 3 × duration_ms`, which is "twice the duration after it
  ended" (L2); no `finished_at` is stored. A `computed_at` later than now (the clock went back, or a rebuild ran
  with a later moment) never holds today back.
- **W5-L-4: measured** (`worker/tests/test_usage_performance.py`, the same container, plain
  PostgreSQL 16; seconds for the full-day rollup, "again" in brackets):

  | Load | wave-5 | W5-K/L |
  |---|---|---|
  | default | 1.77 (1.29) | 1.99 (1.55); another test ran at the same time |
  | heavy | 6.30 (7.10) | 6.09 (6.90) |

  Both loads refresh every minute. The specification's busy day (a rollup of about a minute late
  in the day) refreshes about every 3 minutes.

## Deviations — W5-P parts P1 and P2 (2026-11-06)

The manager took P1 and P2 (#203, comment of 2026-11-05); P3 and P4 stay with Gülşah.

- **W5-P-1: the general hours are those most work days share.** The editor is a week table, so
  the general `start`/`end`/`breaks` of the saved value are the hours that most work days have
  (on a tie, the earliest day's). Every other work day gets a `by_day` entry; a day equal to the
  general hours gets none (P1). A stored value reads back unchanged, and an untouched editor
  saves nothing. A value written outside the editor (`arcilogger-db settings set`) keeps its shape
  when one day changes (amended 2026-11-06 after #209's review): its general hours stay while a
  work day still has them, and then a stored `by_day` entry equal to them stays while its day
  keeps them.
- **W5-P-2: the browser names the day of a refusal.** It checks the server's rules (K1) before
  saving, per day: "Salı: her mola en az 5 dakika olmalı." The server's own refusal
  (`setting_work_hours_breaks`, `setting_work_hours_by_day`) is shown at the setting as before.
  It is captured as `settings.json` `errors.setting_invalid_work_hours`.
- **W5-P-3: "Mola" appears when the answer holds break time.** The usage roles cannot read the
  work hours, so the page cannot tell whether breaks are configured. The "Mola" column (person,
  device and department tables), tile and company card are shown when a row or the total has
  `break_active_seconds > 0`, as the reports do (K5). The filter always offers "Mola". The
  exports always have the column (W5-K-2).
- **W5-P-4: the timeline's bands come from the day's rollup** (`SubjectDetail.breaks`, W5-K-7).
  They are shaded behind the day's segments and named in the legend ("Mola (12:00–13:00)").
  The shared bar `web/src/components/ui/timeline.tsx` gets an optional `bands` property for
  them; it is outside §6.6's list.
- **W5-P-5: "Son güncelleme" only for today.** "Son güncelleme 14:36 (veriler 14:35'e kadar)"
  is shown when the period ends today. An earlier day keeps "Veriler … sonuna kadar işlendi":
  its last update says nothing new.
- **W5-P-6: the e2e usage mock has a break hour** (`UsageOptions.breakHour`), so the generated
  days split one hour of the work hours as the engine does. Its exports have the API's break
  columns (W5-K-2).
- **W5-P-7: files outside §6.6's list:**
  - `web/src/components/ui/timeline.tsx` (W5-P-4);
  - `web/src/pages/dashboard/dashboard-page.test.tsx`: the Panel's usage refresh, now
    `USAGE_POLL_MS` = 60 s;
  - `web/scripts/capture_api_samples.py`: the captured refusal of W5-P-2.

## Deviations — W5-T (2026-11-06)

Recorded on #204, from the manager's answers of 2026-11-06.

- **W5-T-1: TÜRKTRUST's account is cleared at the console only in Wave 5.** C5 keeps its body
  fields: `turktrust_username`, `turktrust_password`, `allow_plain_http`. No
  `clear_turktrust_credentials` is added, and `clear_credentials` clears only Kamu SM's account,
  as before. The console clears TÜRKTRUST's: `secret delete turktrust.username` and
  `secret delete turktrust.password`. Completing this in the browser is Wave 7's work.
- **W5-T-2: TÜRKTRUST's answers other than 2xx, 401 and 403 are split.**
  - **Configuration error, not retried:** a 3xx, and any 4xx other than 408 and 429.
    - Redirects are not followed, so a 3xx most likely means a wrong URL or path (T1).
    - The text carries the status code and points to `[tsa] url`, never the credentials.
  - **Unavailable, retried:** 408, 429, 5xx and network errors.
  - 401 and 403 stay `credentials` configuration errors.
  - Tests: `core/tests/test_tsa_turktrust.py` (`test_another_refusal_is_a_configuration_error`,
    `test_a_server_error_is_retried`, `test_a_redirect_is_not_followed`).
- **W5-T-3: until W5-T part 3, ApplySystem does not know the new outbound rules.**
  - `admin/systemplan.py` derives the strict outbound rules after a browser change. It puts the
    `[tsa] url` host under `ARCILOGGER Out Kamu SM` on `tsa.port`, and it knows neither
    `tsa.crl_urls` nor `network.https_proxy`.
  - So a browser switch to TÜRKTRUST reaches it on 443. The CRL hosts and the proxy, however, open
    only when the installer's firewall step (`Set-ArciFirewall.ps1`) runs.
  - **The follow-up:** a small pull request right after #204 (W5-T part 3) closes the gap. Its
    granted files are `_outbound` in `admin/systemplan.py` and the rule list in
    `ArciInstall.psm1`, with their tests. It adds the `TURKTRUST`, `CRL` and `Proxy` rules, with the
    names and ports of `Set-ArciFirewall.ps1`.
  - **Closed by W5-T part 3 (2026-11-06).**
    - `_outbound` plans `ARCILOGGER Out TURKTRUST` (the `[tsa] url` host on the port the provider
      uses, `tsa.url_with_port`) and `ARCILOGGER Out CRL` (each `tsa.crl_urls` host on 80 and
      443). With `network.https_proxy` set, it plans `ARCILOGGER Out Proxy` on the proxy's host
      and port instead of both. With `turktrust`, the Kamu SM rule keeps only Kamu SM's servers,
      on port 80.
    - `OutboundRule` and `ArciInstall.psm1`'s `OutboundRulePattern` accept the three names.
      `Set-ArciFirewall.ps1 -SyncFromConfig` sets them over TCP, and creates any that is missing.
    - As for every outbound rule, ApplySystem never removes one. A `TURKTRUST`, `CRL` or `Proxy`
      rule stays when its setting goes away, until the installer's firewall step runs.
- **W5-T-4: `turktrust-http` without `confirm` has its own code, `tsa_plain_http_confirm_required`**
  (independent review of #204, point 2).
  - §2.4 and C5 named `tsa_confirm_required` for this case. That code's text is about leaving
    production for the test server, not about the account travelling in clear text.
  - The body field is unchanged: `confirm: true`, with the form's typed `HTTP` (C5).
  - The new code's TR/EN text says that the TÜRKTRUST user name and password travel unencrypted.
    It lives in `api/src/arcilogger_api/admin_channel.py`, granted for this one code. It is
    answered with HTTP 422, like `tsa_confirm_required`.

## Deviations — W5-N (2026-11-05)

- **W5-N-1: the SNMP client lives in core** (answer to Q4 on #202, 2026-11-05 23:19).
  - **Where:** the library boundary is a new `core/src/arcilogger_core/snmp.py`, outside §6.6's
    list: the authPriv-only `Target`, the GET/walk client and the status mapping. The N5 probe
    runs in the administration agent's child process, which is core and never imports the worker
    (SMTP is the precedent, `core/smtp.py`).
  - **The dependency:** `pysnmp==7.1.30` and `pyasn1==0.6.5`, both BSD-2-Clause, are pinned in
    `core/pyproject.toml`.
  - **Imported only there:** pysnmp is imported only inside the client's methods.
    `core/tests/test_snmp_import.py` proves that `arcilogger_core.config`,
    `arcilogger_core.snmp`, the collector and ingest load without it.
  - **Unchanged:** the poller, the state file, the job and `R20-snmp` are in
    `worker/src/arcilogger_worker/snmp/`.
- **W5-N-2: `Layout.wan_rates` is a property**, like the other status files of `Layout`;
  §6.2 N4 writes `Layout.wan_rates()`.
- **W5-N-3: `[sources.snmp]` is a model in `config.py`; the adapter is gone** (Q3 on #202).
  Until W5-T merged, an adapter applied N1's rules; it was removed in the same PR.
  - **The model:** `SnmpConfig` on `SourceConfig.snmp` (`None`: no SNMP), with the defaults and
    bounds of §6.3. Only authPriv: there is no `version` field and no community string, so either
    is refused as an unknown field (`extra_forbidden`). `auth`/`priv` have no "none".
  - **Interfaces:** `ifName` values must be distinct; a capacity may name only a listed interface.
    Capacities are whole Mbit/s, so the browser's TOML writer needs no floats.
  - **Refusals:** an enabled SNMP needs a user and 1 to 4 interfaces. Refused values give the
    error texts `snmp_setting_invalid: …` and `snmp_source_is_network: …`. Refused input is never
    repeated (`hide_input_in_errors`).
  - **The keys:** `secretstore.snmp_auth_key()`/`snmp_priv_key()` name them, and
    `check_secret_value` refuses one outside 8 to 64 characters. A missing or bad key at poll
    time is `snmp_secret_missing`: the source is polled as `auth_failed` without a request.
- **W5-N-4: the statuses N4 leaves open, and "a good sample".**
  - **Failures:** a USM refusal (unknown user, wrong digest, decryption, unsupported level or
    protocol, not in the time window) is `auth_failed`. Any other request failure is `timeout`:
    no answer, a malformed answer, an error-status, or an exception of pysnmp itself, named by
    its type only. One source's failure never stops the others.
  - **A good sample** is a read of a line's 64-bit counters. The first read after a start or a
    reset is good, but gives no rate yet. `updated_at` is its time.
  - **The 10 minutes of `R20-snmp`** count from the later of a line's last good read and the
    start of its watch: when SNMP was switched on, or when the interface was added.
  - **The alert:** `R20-snmp:<source>` opens when one line of a source has had no good read for
    10 minutes, and resolves when every line has. Its title names the source, the lines and the
    reasons; the e-mail names only the source, the number of lines and the reasons.
- **W5-N-5: mounting.** W5-N's PR merged before W5-M's routes, so it created
  `api/src/arcilogger_api/routers/activity.py`: one `/activity` router (prefixes must be unique)
  that includes `activity/wan.py`. W5-M includes its devices router there.
  - **Samples:** `web/scripts/samples_activity_wan.py` captures `activity-wan.json`, entered in
    `capture_api_samples.py`'s `SAMPLE_MODULES`.
  - **Web tests:** `activity-types.ts` joins the type lists of `web/src/lib/api-types.test.ts`
    and `api-samples.test.ts`, as C1 asks of a package that mounts a route.
- **W5-N-6: the browser, the probe and the firewall rule** (N1, N3, N5 after W5-T).
  - **The keys in a request:** a source's keys are `secret_set`/`secret_delete` ops of
    `snmp.<source>.auth_key`/`priv_key` in the `sources` section. The contract's `SnmpKeyName`
    accepts these names next to `BrowserSecret`.
  - **The form:** the keys are the write-only `snmp_auth_key`/`snmp_priv_key` fields of the
    sources form. Removing a source deletes its keys.
  - **Refusals:** switching SNMP on without both keys, stored or set in the same request, is
    `snmp_secret_missing`; on a network source it is `snmp_source_is_network`. Both have TR/EN
    texts in `admin/messages.py`.
  - **The answer:** the sources etag follows the keys' set/unset state, and `GET /admin/config`
    gives each source's `snmp` with its keys as set/unset. A source's change rows leave `None`
    out, so a source without SNMP journals as before.
  - **Writing the TOML:** `admin/tomledit.py` writes `[sources.snmp]` after its `[[sources]]`
    table, defaults left out, and each capacity as `[sources.snmp.capacity_mbps."<ifName>"]`.
    It does so only inside the managed block; a hand-written sub-table outside it is still
    refused.
  - **The probe (N5):** it runs on the request whenever a change switches SNMP on, edits its
    settings or sets its keys, before anything is written.
    - **Strict outbound mode:** `ARCILOGGER SNMP <source>` exists only after ApplySystem. So a
      change that switches SNMP on, or moves its address or port, warns `probe_skipped` at
      `sources.<name>.snmp` instead, as the clock and the replica do. New keys for the same
      device are still probed.
    - **How:** an `snmp` probe of `admin/probe.py`, through `arcilogger_core.snmp.list_interfaces`.
    - **The list:** its message lists the interfaces the device reports (name, status, Mbit/s).
    - **A failure** gives N4's reason as its code: `snmp_timeout`, `snmp_auth_failed`,
      `snmp_no_such_interface` or `snmp_no_hc_counters`. The child passes the code through,
      and each has TR/EN text.
    - **Not here:** a separate "Bağlantıyı dene" action that returns the list as JSON
      (W5-N-7).
  - **The rule (N3):** `ARCILOGGER SNMP <source>`, without "Out", as N3 names it. UDP to the
    source's address and SNMP port.
    - **Where:** `OutboundRule`'s pattern and the installer's `OutboundRulePattern` accept the
      name. The system plan adds the rule, and `Set-ArciFirewall.ps1` adds it in full strict
      mode and applies it as UDP in a sync.
    - **A left-over rule** (accepted, Q7 on #207): after a source's SNMP is switched off, its
      rule stays until the installer's firewall step rebuilds the group, as W5-T-3 does for
      TURKTRUST, CRL and Proxy. ApplySystem never removes an outbound rule, and this one only
      allows UDP from the server to a LAN device.
- **W5-N-7: N5's "pick from a list" is the probe's refusal in Wave 5** (Q6 on #207).
  - A change is probed when it is saved. A wrong interface name fails with
    `snmp_no_such_interface`, whose message lists what the device reports (name, status,
    Mbit/s). P4's form takes the names as typed fields and shows that text.
  - A pre-save action that returns the list as JSON (its own task kind, route and types) is a
    Wave 6 follow-up: #217.
- **W5-N-8: the review of #207.**
  - **IPv4 only:** an enabled SNMP on an IPv6 address is refused as `source_ipv6_unsupported`
    (pysnmp's UDP transport is IPv4). The browser shows it at `sources.<name>.address`.
  - **The keys** go to pysnmp as their UTF-8 octets, so a key outside Latin-1 works.
  - **No row for an idle poll:** a `Job` may carry an `idle` check that the worker asks before
    it writes a `job_runs` row. `snmp.poll` is idle while no source is due and the state file
    has nothing left to clear: it then neither runs nor records, and is due 5 s later. An
    installation without SNMP writes no `snmp.poll` rows.
  - **Δt** of a rate is the change of `sysUpTime` (same GET) when it moved forward, else the
    time between the two polls.
  - **The state file** is written compact (one line). A write that fails (on Windows while the
    API reads it) is logged and written again at the next poll.
  - **A source whose SNMP is switched off** resolves its `R20-snmp` as `not_polled`.
- **W5-N-9: the interface list reaches the browser** (#224). W5-N-7's "the list comes with the
  probe's refusal" needed a field.
  - **The probe:** `ProbeResult.interfaces` holds what the device reported once the `ifName`
    walk answered: on success, `snmp_no_such_interface` and `snmp_no_hc_counters`. Each entry is
    a `ProbeInterface`: `name`, `oper_status` (an RFC 2863 name or null) and `speed_mbps` (a whole
    number from 0, or null). The other probes leave it empty.
  - **The child's line** gains `"interfaces"`. The parent reads it bounded:
    - at most 256 entries;
    - a name without any secret, its control characters replaced by `?`, cut to 64 characters;
    - any other status or speed is null, and anything else is left out.
  - **The request status:** `ErrorRef.interfaces` (contract.py) carries the list, allowed only
    with `snmp_no_such_interface` and `snmp_no_hc_counters`. The agent passes it there. The
    probe step stays a code.
  - **The answer:** `GET /admin/requests/{id}` gives it as `error.interfaces` (`RequestError` in
    `admin_channel.py`; empty for any other code). It holds no person data and no secret.
  - **The browser's types:** `ProbeInterface` and `RequestError.interfaces` in
    `settings-types.ts`. The captured `admin.json` has `sources.snmp_probe_failed`.
  - **History:** the list lives in the request's status file, so the request history keeps the
    code but not the list once the agent has cleaned that file up.

## Deviations — W5-F (2026-11-06)

Recorded on #104. W5-F moved to the manager on 2026-11-06 (05:50Z); part 1 is Saray's (#105,
`33d9421`), taken over unchanged and then brought up to the merged packages.

- **W5-F-1: part 1's own deviation is dropped.** It documented `crl-ok` at level OK against D10's
  "information"; D10 now says `crl-ok` has the level `ok`, so nothing deviates.
- **W5-F-2: files outside §2.7, the W5-G list on #104.**
  - `db/src/arcilogger_db/migrate.py` and `worker/src/arcilogger_worker/lookup/window.py`: their
    docstrings still named the 400-day database and the monthly partitions.
  - `window.py` also loses the dead `400` fallback of `get_setting(…, "db.retention_days", 400)`:
    `get_setting` returns the shipped default (30) for a known key, so nothing changes.
  - `tests/test_docs_consistency.py`
    (`test_auditor_guide_states_what_the_database_deletes`) pinned the old sentence "no deletion
    job exists yet". It now checks the database window (`db.window`, `db.retention_days`) in
    both languages.
- **W5-F-3: Canlı Durum is documented before its packages merge.** CANLI-DURUM.md / LIVE-STATUS.md,
  the SNMP part of AYARLAR/SETTINGS, the `ARCILOGGER SNMP <source>` rule in KURULUM/INSTALL and
  DEVICE-SETUP §6 follow §6 (M1–M3, N1–N7, P3, QB1, QB2). A package that deviates from §6 updates
  these docs in its own pull request. The CHANGELOG line of Canlı Durum comes with P3.
  - The docs follow the answers to #207's Q6 and Q7: the interface list comes with the probe's
    refusal when saving (W5-N-7; the pre-save list is #217), and a switched-off source's
    `ARCILOGGER SNMP` rule stays until the installer's firewall step, as W5-T-3.
- **W5-F-4: `turktrust.md` stays out of `OPERATOR_DOCS`** (part 1, Q6). The bundle ships the whole
  `docs` tree, so the guide reaches the server. Adding it would also change
  `Build-ArciBundle.ps1`'s required list, a deploy file.
- **W5-F-5: "Ayarlar → Kamu SM" in code texts.**
  - The docs name the page "Ayarlar → Zaman damgası", as the menu does.
  - Several code texts still say "Ayarlar → Kamu SM": the first-run wizard's step targets, the
    compliance card texts and the installer. The docs quote them unchanged where they quote
    them (UYUM-DURUMU, the wizard tables).
  - A follow-up for Wave 6.
- **W5-F-6: the banner texts.**
  - `certificate_revoked` (part 1, Q1): W5-C's Turkish text names no provider ("Zaman damgası
    sağlayıcısıyla görüşün"), and the guides quote it.
  - `provider_not_legal` (Q2) still points to Kamu SM's production server. That reason only
    appears for mock, rfc3161 and test servers, so the remedy holds; the guide's remedy names
    both providers.
  - The banner sections are 5.2–5.9 (Q3), as W5-D's locale keys have them.

## Deviations — W5-P part P4 (2026-11-06)

P4 is Gülşah's (#203, 23:40Z); it follows W5-N (#207) and the answers of 2026-11-06 07:10 on
#203.

- **W5-P-12: the SNMP section is in the source dialog of Ayarlar → Kaynaklar.** §6.6 names
  `web/src/pages/sources/source-sheet.tsx`, but that is Kaynaklar's read-only drawer; a source
  is changed in `web/src/pages/settings/sources-page.tsx`, where CANLI-DURUM / LIVE-STATUS §3
  and AYARLAR / SETTINGS put the section.
  - The model is `settings/snmp-model.ts` and the section `settings/source-snmp.tsx`, both new.
  - The fields are N1's: the switch, user, port, `auth`, `priv` ("sahada doğrula" beside
    `aes256`), 1 to 4 interface names typed (W5-N-7), a capacity per line in both directions
    or neither, and the interval. The two keys are write-only, as SMTP's password; the form
    knows only whether each is stored.
  - The browser checks N1's rules while SNMP is on, and a typed key's 8 to 64 characters
    (counted as the server counts them) also while it is off. Typed keys are sent only with an
    SNMP table: keys for a source without one would be stored and never shown. The server's refusals of the
    table are shown in the section, those of a key at the key.
- **W5-P-13: every change of a source carries its `[sources.snmp]`.** The server reads a
  source without `snmp` as one without SNMP. Since W5-N, a row's enable switch and the edit
  dialog sent the source without it, so either deleted a stored table. Both now send it
  unchanged (`sourceConfig`, `fromSourceForm`).
- **W5-P-14: the device's interfaces under the refused request.** W5-N-7 says the refusal
  `snmp_no_such_interface` lists what the device reports; until W5-N-9 (#226) the request
  carried only the probe's code. Now, when a change of a source ends with
  `snmp_no_such_interface` or `snmp_no_hc_counters`, Ayarlar → Kaynaklar shows the request's
  `error.interfaces` under its steps as a table (name, status in words, Mbit/s), with the
  code's text above it. The e2e mock serves the captured `sources.snmp_probe_failed`.
- **W5-P-15: the Panel card N7.**
  - It shows the lines whose source has SNMP on (`status` not `disabled`), each with download
    and upload now, the share of the capacity when one is set, a 60-minute sparkline in each
    direction, the reason while a line is not read and its last good reading.
  - A line whose `status` is not `ok` shows "—" for its rates, as Canlı Durum does: its last
    rate can be an hour old. An interface that is not up is named in words ("Arayüz: alt
    katman kapalı"), never by its raw `ifOperStatus` value.
  - It sits under the health cards, the whole width, and links to Canlı Durum for those who
    may open it.
  - It is asked for with `dashboard.view` or `system.read` (N6), every 10 s as Canlı Durum
    asks (`ACTIVITY_POLL_MS`), in the background, and with the Panel's "Yenile".
  - With no line, an administrator (`sources.manage`) gets a link to Ayarlar → Kaynaklar; the
    others see nothing. While it loads, or on an error, nothing is shown, as the disk card does.
- **W5-P-16: P4 does not wait for P3.** #221's web job needs W5-M's `/activity/devices`.
  P4 takes #221's hunks of `useActivityWan`, `nav.activity` and `e2e/samples.ts`
  `ACTIVITY_WAN` word for word; only `api-hooks.ts` then differs (#221 adds the devices hook
  beside it), and the later pull request keeps both. The rate texts of the card
  (`formatBps`, `capacityShare`, `shareText`) repeat #221's `activity-model.ts`; once both
  have merged, the card can import them from there. The e2e Panel routes `/activity/wan` to
  the captured answer itself.
- **W5-P-17: the review names the SNMP fields.** A source's change row lists its SNMP table
  field by field ("fw-1 · SNMP: WAN arayüzleri") and names the two keys
  (`lib/admin-requests.ts` `changeRows`, `components/admin/labels.ts` `pathLabel`).
- **W5-P-18: the e2e admin mock answers SNMP as W5-N's rules do**: the keys' length
  (`secret_invalid`), `snmp_source_is_network`, `snmp_secret_missing`, the probe on a changed
  source with SNMP on or new keys, its own failure code (`nextProbeFailure`), and
  `probe_skipped` in strict outbound mode (`strictOutbound`). Its texts are those of
  `admin/messages.py`. Its seed TSA section reports the two `turktrust.*` secrets as W5-T's API
  does, so `e2e/settings-kamusm.spec.ts` drops `reportTurktrust` (granted on #214).
