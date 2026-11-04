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
  and every sample of its files once, on both back ends.
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
  timeout`). The lookup (READ COMMITTED) keeps the lock in its transaction as before. On any
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
  2026-11-03 20:51). `test_renewal_standalone.py::test_verifier_version_is_6` pins it. If #136
  (`xz-unsupported`) merges after W5-A, it takes "7" and updates that test.
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
  W5-B-2), so it leaves no evidence. Tests:
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
  one or a dry run: while the earlier file is the only one, a dry run writes nothing) carries
  over only the earlier file's `failures_in_a_row` (W5-E-8); from then on the new file exists
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
  `state\renewal.json` then, and `state\renewal\renewal.json` once that file exists or while
  neither does (`TaskRow.shown`). The day page's problem text stays `renewal.json: unreadable`,
  the name of both files.
  Tests: `test_an_earlier_state_file_is_read_once_and_never_written`
  (`core/tests/test_cli_renew.py`),
  `test_the_readers_read_an_earlier_report_only_until_the_new_one_exists`
  (`worker/tests/test_alerts_legal_state.py`),
  `test_the_writer_and_the_readers_meet_in_the_protected_folder`
  (`tests/test_renewal_state_reader.py`),
  `test_renew_row_reads_an_earlier_report_until_the_first_run`
  (`api/tests/test_system_health.py`; the evidence text, and the earlier file left in place),
  and the ACL tests of W5-E-1.
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
  raised when an issuer in use has no fresh list. A run that exits 1 (Q9) writes nothing, so
  the row shows no run for it: it keeps the run before and turns `late` after 26 h, or shows
  `missing` while the index cannot be read. W5-C's readers need no change: an empty index is
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
  dry run writes nothing (`write_state` returns None, `--json` gives `state_written` false;
  answer to Q5 on #158): it would have written no `last_run`, turning the Sistem page's Renew
  row `missing` (W5-E-2), and its own statuses in place of the earlier ones, changing
  `R20-renewal` and `R20-cert` before any counted run. A dry run never makes the state look
  worse. `state/renewal/` gets no report (only the lock every run holds), so the readers keep
  reading the earlier file (W5-E-4, Q2); the first counted run migrates as above, and from then
  on a dry run writes its statuses and keeps `last_run` (W5-A-2). A made-up count can only do
  what a missing file (0) or a real count could: hide the failures before the upgrade from
  `R20-renewal`'s failure branch, or raise it a run early; it cannot make tokens known. Tests:
  `test_a_refused_run_takes_no_statuses_from_an_earlier_state_file` and
  `test_an_earlier_state_file_is_read_once_and_never_written` (`core/tests/test_cli_renew.py`),
  `test_renew_row_reads_an_earlier_report_until_the_first_run`
  (`api/tests/test_system_health.py`) and
  `test_a_dry_run_after_an_upgrade_leaves_the_alerts_as_they_are`
  (`worker/tests/test_alerts_legal.py`).
- **W5-E-9: the owner of `state\crl` and `state\renewal` cannot change their rights (#151;
  review of the follow-up).** Both folders are new in Wave 5 (W5-B, W5-E-4). Under the earlier
  release's ACL the collector, ingest and the worker hold Modify on `state`, so a compromised one
  could create either folder before the upgrade and own it. `Set-ArciVaultAcl.ps1` replaces only
  the DACL of a folder that exists (`Set-ArciAclEntry` never sets the owner), and an owner keeps
  the implicit right to read and change the DACL whatever it says: it could give itself Modify
  again, write `renewal.json` (silencing `R20-renewal`, `R20-cert` and `renewal_due`), hold
  `renewal.lock`, or change a stored list. `Get-ArciAclPlan` now gives both folders an entry for
  OWNER RIGHTS (`S-1-3-4`) with `ReadPermissions` only, inherited by what is created below;
  Windows then gives the owner that entry's rights in place of the implicit ones.
  `ReadPermissions`, not `ReadAndExecute`, so that an owning service still cannot list or read
  the folder (nothing for the collector or ingest, W5-E-1). SYSTEM, Administrators and the
  tasks keep their own entries, and neither `Renew` nor `CrlFetch` changes a permission there
  (the immutability lock is used only below `vault`). Not covered, and outside W5-E's files: an
  item a service created inside such a folder before the upgrade keeps its owner and its
  explicit or protected entries, which `Set-ArciVaultAcl.ps1` does not replace (for example a
  made-up `state\renewal\renewal.json` the Renew task cannot replace). Setting the owner of a
  protected entry to Administrators, and resetting the items below `state\crl` and
  `state\renewal`, belong to `Set-ArciAclEntry` and `Set-ArciVaultAcl.ps1`; they are proposed on
  #151. Tests: `test_acl_plan_least_privilege`,
  `test_acl_plan_gives_the_renew_and_crlfetch_tasks_their_rights`, and on Windows
  `test_acl_applied_keeps_sealed_file_deny_entries` (`state\crl` created before the ACL step
  with an entry of its own: that entry is gone, and OWNER RIGHTS has `ReadPermissions` only on
  `state\crl` and `state\renewal`).

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
