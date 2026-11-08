# Time stamping: providers, legal grade, re-stamping and revocation lists

> Türkçe: [ZAMAN-DAMGASI.md](ZAMAN-DAMGASI.md). Kamu SM: [kamusm.md](kamusm.md). TÜRKTRUST:
> [turktrust.md](turktrust.md). Settings: [SETTINGS.md](SETTINGS.md). Auditor:
> [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md).
>
> ARCILOGGER is *designed to support* 5651 obligations ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance. "Legal grade" in this document is a
> technical mark of ARCILOGGER (section 2), not a legal assessment. Ask your legal counsel to
> assess the stamps and the record keeping.

This document explains everything about time stamping in ARCILOGGER in one place: which providers
can be chosen (section 1), when a day is shown as "legal grade" ("yasal nitelikli", section 2),
re-stamping before a TSA certificate expires (section 3), certificate revocation lists (CRLs,
"SİL", section 4), the legal-validity banner at the top of every page and its reasons (section
5), the Panel, the Mühürleme (sealing) page and the e-mail alerts (section 6), the settings
involved (section 7) and what to verify on site (section 8). The banner's texts refer to the
Turkish document as "ZAMAN-DAMGASI.md bölüm N"; this document has the same section numbers, and
each reason has its own section (sections 5.2–5.9).

Paths are those of a default installation: the data folder `D:\ARCILOGGER` (the vault
`D:\ARCILOGGER\vault\`, status files `D:\ARCILOGGER\state\`), the configuration
`C:\ProgramData\ARCILOGGER\arcilogger.toml`. Console commands run in an elevated PowerShell with
their full path:

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
```

## 1. Providers

The provider is chosen with `[tsa] provider`, the server with `[tsa] url`. In the web interface
the choice is on the **Ayarlar → Zaman damgası** (Settings → Time stamping) page (administrator,
with re-authentication):

| `[tsa] provider` | Server | Legal-grade claim | Credentials | Credit query |
|---|---|---|---|---|
| `zamane` | Kamu SM production, `zd.kamusm.gov.tr` | **yes** | `kamusm.customer`, `kamusm.password` | yes (Zamane client) |
| `zamane` | Kamu SM test, `tzd.kamusm.gov.tr` | no — the test server's stamps have no legal validity (Kamu SM) | the same (test user) | yes |
| `turktrust` | TÜRKTRUST production, `zd.turktrust.com.tr` (HTTPS, port 443) | **yes**, by the product owner's decision (section 2.2) | `turktrust.username`, `turktrust.password` | **no** |
| `turktrust` | any other server | no | the same | no |
| `rfc3161` | a generic RFC 3161 server (development only) | no, never | none | no |
| `mock` | ARCILOGGER's own trial certificate authority | no, never | none | no |

- **Kamu SM (`zamane`):** works with the official Zamane console client. Account, credit, test
  and the switch to production: [kamusm.md](kamusm.md) and [INSTALL.md](INSTALL.md) section 10.
- **TÜRKTRUST (`turktrust`):** RFC 3161 with HTTP Basic authentication. HTTPS is the default and
  recommended way; the TLS certificate is verified with the operating system's trust store, and
  this verification cannot be turned off. Plain HTTP (port 80) is used only when an
  administrator explicitly switches on `[tsa] allow_plain_http` (by typing a confirmation); the
  username and password then cross the network unencrypted, and the banner shows the warning
  `credentials_in_clear` (section 5.3). The username and password are kept in the secret store,
  not in the TOML file: they are entered under Ayarlar → Zaman damgası (or on the console with
  `secret set turktrust.username` / `secret set turktrust.password`); the password never appears
  in a log, an error text or an output. A `[tsa] url` that carries a username or password
  (`https://user:password@…`) is refused, and the error text does not repeat it. In this release
  the stored TÜRKTRUST account is **cleared on the console only**: `secret delete
  turktrust.username` and `secret delete turktrust.password` (the browser's **Sil** (Delete)
  clears only the Kamu SM customer number and password). Opening the account, the root certificates and the manual
  checklist: [turktrust.md](turktrust.md).
- **TÜRKTRUST's answers:** 401 and 403 are a credentials error (not retried). A redirect (3xx) is
  not followed; a 3xx, and any 4xx other than 408 and 429, is a configuration error, is not
  retried, and its text gives the status code and points to `[tsa] url` (the URL path, T1,
  **verify on site**), never to the credentials. 408, 429, 5xx and network errors count as "the
  server cannot be reached" and are retried.
- **TÜRKTRUST offers no credit query.** The Credit task reads no credit for TÜRKTRUST, the credit
  card shows this text, and no `R20-credit` alert is raised:
  "This provider offers no credit query; watch the balance in the provider's portal."
  **Ask TÜRKTRUST** how to watch the balance in its portal and whether an optional e-mail can be
  received when the balance runs low.
- **`rfc3161`** sends no credentials; it does not work with Kamu SM and is for development only.
- **`mock`** is for development and trial servers only: its root is trusted only while
  `provider = "mock"`. Do not use it on the server; once the provider changes, mock-stamped days
  fail verification (`R20-verify`; [FIRST-STEPS.md](FIRST-STEPS.md) section 3).

**What is sent?** Hashes only. A day's stamp sends the hash of the daily manifest; a re-stamping
sends the SHA-256 of a renewal record, which itself lists hashes only (section 3). No log line
leaves the server.

**Proxy:** when `[network] https_proxy` (`http://host:port`, empty by default) is set, the CRL
download and the `rfc3161` and `turktrust` providers connect through it. The proxy takes no
credentials in this release. The Zamane console client does **not** use this setting: how the
client takes a proxy is unknown (**verify on site**, "sahada doğrula").

**Strict outbound** (`Set-ArciFirewall.ps1 -StrictOutbound`): the firewall opens these outbound
rules (all TCP):

| Rule | When | Host and port |
|---|---|---|
| `ARCILOGGER Out TURKTRUST` | provider `turktrust` and no proxy | the host of `[tsa] url`; the port is the URL's own, else `[tsa] port` unless it is 80 or 443, else 443 (80 for an `http://` URL with `[tsa] allow_plain_http`) |
| `ARCILOGGER Out CRL` | `[tsa] crl_urls` set and no proxy | the host of each CRL URL, on 80 and 443 (the lists are signed, so plain HTTP is safe for them) |
| `ARCILOGGER Out Proxy` | `[network] https_proxy` set | the proxy's host and port, in place of the TÜRKTRUST and CRL rules |
| `ARCILOGGER Out Kamu SM` | always | Kamu SM's two servers; with the provider `turktrust`, port 80 only (the Zamane client takes no proxy, so this rule stays with a proxy too) |

After a change in the browser (Ayarlar → Zaman damgası, or Genel) the management service
(ApplySystem) updates these rules with the same names and ports, and creates one that is missing.
It never removes an outbound rule: a `TURKTRUST`, `CRL` or `Proxy` rule whose setting went away
stays until the installer's firewall step (`Set-ArciFirewall.ps1`) runs again. The hosts opened
for Kamu SM: [INSTALL.md](INSTALL.md).

## 2. What "legal grade" means

### 2.1 Three conditions

ARCILOGGER shows a day as "legal grade" ("yasal nitelikli"; in the 5651 report, the seal
calendar, the Mühürleme page and the evidence package) only when **all three** conditions hold:

1. **The claim:** the stamp claims to come from a production provider. This release has two
   production providers: `zamane` on `zd.kamusm.gov.tr` and `turktrust` on
   `zd.turktrust.com.tr`. The claim is sealed into the manifest's `tsa` block (`legal_grade`).
2. **Verification:** the stamp (token) verifies: signature, manifest hash, the
   `id-kp-timeStamping` usage, and a chain to a root with certificates valid at the stamp time.
3. **The root:** the chain ends at a root whose SHA-256 fingerprint the site listed in
   `[tsa] legal_root_fingerprints`. With an empty list no day is shown as legal grade
   (section 5.4).

There is no fourth way. The list of production providers is a product constant: no site setting
can make another provider or another server "legal grade". The root list, on the other hand, is
the site's own decision: a root is added with the root upload under Ayarlar → Zaman damgası with
the "legal" confirmation, after its fingerprint has been compared with the value the provider
publishes and recorded.

### 2.2 TÜRKTRUST

Stamps from TÜRKTRUST's production server (`zd.turktrust.com.tr`) count as legal grade **by the
product owner's decision** (2026-11-02), in the same way as Kamu SM's. The decision is a product
constant (`TURKTRUST_LEGAL_GRADE`); the root check is the same as for Kamu SM: a day stamped by
TÜRKTRUST is shown as legal grade only when its stamp verifies and its chain ends at a TÜRKTRUST
root listed in `[tsa] legal_root_fingerprints` (when the root is uploaded, the text says
"TÜRKTRUST kök sertifikası").

Before choosing TÜRKTRUST:

- have the choice reviewed by your **legal counsel**;
- ask TÜRKTRUST for its **written statement** on the standing of its stamps, and keep it with the
  site's records.

### 2.3 Stamps that are not legal grade

The stamps of the trial provider (`mock`), of `rfc3161` and of test servers are **not** legal
grade; while one of them is chosen, the banner shows `provider_not_legal` (section 5.2). Days
stamped earlier do not change when the provider changes: sealed data is never rewritten. The
`[tsa] require_critical_eku` choice (whether the TSA certificate marks the EKU extension
critical; K6 for Kamu SM, T3 for TÜRKTRUST, **verify on site**) is sealed into every manifest.

## 3. Re-stamping

### 3.1 Why and when

A time stamp is signed with the TSA certificate valid at the moment it was made; that certificate
has an end date (`notAfter`). Re-stamping seals the hashes of the old stamps with a new stamp
before the certificate expires, so that the old stamps can still be shown, with a certificate that
is still valid, to have existed at that date. Re-stamping is supporting evidence: it does not
change whether a stamp was valid for the moment it was made. The day's verification result stays
the same.

**What is renewed?**

- day stamps (`vault\YYYY\MM\DD\manifest-<day>.json.zd`);
- the stamps of deletion records (`vault\deletions\*.zd`);
- the stamps of the stored official reports (`exports\reports\*.pdf.zd`);
- the stamps of earlier renewal records.

Report stamps that exist only inside exported packages are not renewed (the copies on the server
in `exports\reports\` are). Hash-algorithm renewal (re-hashing the raw files with a new algorithm)
is not part of this release.

**When?** A stamp becomes **due** (`due`) when fewer than `[tsa] renew_before_days` days (default
180, 30–730) are left until its TSA certificate expires and no counting renewal covers it. The
`Renew` task runs `arcilogger-core renew` every day. One run writes at most **one** renewal record
and puts every stamp due at that moment into it: at most one stamp a day, so credit use stays
predictable. When nothing is due, no stamp is taken.

### 3.2 The renewal record

Each renewal is two files: `vault\renewals\<YYYY-MM-DD>-<seq>.json` (canonical JSON, schema
`arcilogger.renewal/1`) and its stamp `<same name>.json.zd`. The sequence number starts at 1 for
each date. The record holds hashes only:

```json
{
  "schema": "arcilogger.renewal/1",
  "created_at": "2031-05-04T03:10:00.000+03:00",
  "system": {"name": "ARCILOGGER", "version": "0.5.0", "site": "<organisation name>"},
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

- **`kind`:** `day`, `deletion`, `report` or `renewal`. For `report`, `path` is under `exports\`
  (e.g. `exports/reports/ARCILOGGER-5651-rapor-<id>.pdf.zd`) and `record_sha256` is the PDF's
  SHA-256; for `deletion` and `renewal`, `record_sha256` is the record's hash.
- **The `tsa` block** has the same fields as the manifest's; its `legal_grade` is the provider's
  **claim**. Whether the renewal counts follows from the rule of section 3.3.
- **`reason`:** `due`; when the provider's claim is not legal grade, the record is still written,
  but with `reason: "test"`, and it never counts.
- **Format:** `tokens` are sorted by `path`; hashes are lower-case hex; times are ISO 8601
  (`created_at` in site time, as in manifests; `cert_not_after` in UTC).
- **A renewal of a renewal:** when a renewal stamp's own certificate comes due, the next renewal
  lists that stamp's hash **and** repeats every day hash it protected (`protects`). An evidence
  package for one day then picks up the whole chain by the same hash match.
- **Files:** the record is written, stamped, and both files are then locked like sealed files. If
  the stamp cannot be completed after the record was written, the record is deleted: a record
  without a stamp is not evidence yet. Re-stamping never touches a day folder, a manifest or a
  stamp; it only adds new files under `vault\renewals\`.

### 3.3 Which renewal counts?

A renewal **counts** only when:

- its own stamp verifies;
- it was taken from a provider whose claim is legal grade, and its chain ends at a root in
  `[tsa] legal_root_fingerprints` (section 2.1);
- its own certificate has not become due (or it, too, is covered by a counting renewal).

A renewal taken with `mock`, `rfc3161` or a test server is written but **never counts**: a trial or
test renewal can never hide a production stamp that is due.

### 3.4 Status per stamp

The status of each renewable stamp is in `state\renewal\renewal.json`. The folder is protected:
only the `Renew` task (LOCAL SERVICE) and administrators write there; the API and the worker only
read. Until the first counted run after an upgrade, the earlier release's `state\renewal.json` is
read; only its count of failures in a row is taken over, and the file stays in place and is not
read again.

| Status | Condition | Level |
|---|---|---|
| `not_due` | More than `[tsa] renew_before_days` days are left until the certificate expires | — |
| `due` | Fewer than `[tsa] renew_before_days` days are left and no renewal counts | warning |
| `covered` | A counting renewal lists this stamp's hash, was stamped before the certificate expired, and its own status is `not_due` or `covered` | — |
| `late` | The counting renewal was stamped **after** the old certificate expired | warning |
| `uncovered` | The certificate has expired and no renewal counts | warning |

Even `uncovered` is a warning, not a failure: the stamp stays valid for the moment it was made;
re-stamping is recommended for long-term evidence. An expired stamp is renewed too; its status then
becomes `late`.

### 3.5 Command line

```powershell
& $core renew --dry-run      # shows what is due; takes no stamp, writes no record
& $core renew                # re-stamps everything due in one record
& $core renew --json         # gives the result as JSON
```

| Exit code | Meaning |
|---|---|
| 0 | Renewed, or nothing was due |
| 1 | Renewed with warnings (some stamps `uncovered`) |
| 2 | The configuration refused (no provider credentials, no roots) |
| 3 | The time-stamp server or the run failed, or another run holds the lock (retryable) |

Every run but one that finds the lock held writes `state\renewal\renewal.json`, a failed or refused
run too (`last_run`, `failures_in_a_row`: `R20-renewal` reads them). `--dry-run` computes the
statuses and writes them to the file, but takes no stamp, writes no renewal record and leaves
`last_run` as it is; after an upgrade, while only the earlier release's file exists, it writes
nothing. The `Renew` task is the same command; the result of
its last run is shown under Sistem → Zamanlanmış görevler (System → Scheduled tasks;
[COMPLIANCE-STATUS.md](COMPLIANCE-STATUS.md) section 4.6).

### 3.6 The standalone verifier and the evidence package

The evidence package ships, under `vault/renewals/`, the renewal records that contain a day's
manifest or stamp hash (and a packaged report's stamp hash); a renewal chain three deep thus gets
into the package too. The standalone verifier (`verify.py`; it prints its version at the top of the report) adds one
finding per packaged day stamp. The result is computed from the package alone, with "now" being
the moment of verification; the verifier on the server gives the same codes:

| Level | Code | Text (TR) | Text (EN) |
|---|---|---|---|
| INFO | `renewal-not-due` | Yeniden damgalama henüz gerekmiyor (sertifika {date} tarihine kadar geçerli). | Re-stamping is not due yet (certificate valid until {date}). |
| OK | `renewal-covered` | Bu damga, sertifikası sona ermeden yeniden damgalanmış: {renewal}. | Re-stamped before its certificate expired: {renewal}. |
| WARN | `renewal-due` | Damga sertifikası {date} tarihinde sona eriyor; yeniden damgalama bekleniyor. | The stamp certificate expires on {date}; re-stamping is due. |
| WARN | `renewal-late` | Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({date}). | Re-stamped after the old certificate had expired ({date}). |
| WARN | `renewal-uncovered` | Damga sertifikasının süresi {date} tarihinde doldu ve yeniden damgalama yok; damga, alındığı an için geçerlidir. | The stamp certificate expired on {date} with no re-stamping; the stamp is valid for the moment it was made. |

The date of `renewal-late` is the time of the re-stamping (the renewal stamp's genTime); in the
other rows the date is the certificate's expiry.

The check of the renewal stamp itself (`renewal-ok`) and the days it covers (`renewal-covers`) are
reported as in earlier releases. The whole verifier output:
[DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) section 7.

## 4. Revocation lists (CRLs)

### 4.1 Download

The `CrlFetch` task runs `arcilogger-core crl fetch` every day. The addresses:

- when `[tsa] crl_urls` is set, this list only (at most 20 http(s) URLs);
- otherwise the CRL distribution points (`http` and `https` only) of the certificates in the pinned
  roots folder (`[tsa] roots_dir`) and in the newest stamp of each provider.

**No CRL address is built into the program**: Kamu SM's CRL addresses are unknown
([kamusm.md](kamusm.md) K10), TÜRKTRUST's come from TÜRKTRUST's "kök sertifikaları ve iptal
listeleri" (root certificates and revocation lists) page (T4); both are **to be verified on
site** ("sahada doğrula"). With strict outbound, the firewall opens the hosts of `[tsa] crl_urls`; a
site that uses strict outbound should therefore put its CRL addresses into `[tsa] crl_urls`.

Each list is downloaded with the `[tsa] timeout_seconds` timeout, at most 20 MB, and through
`[network] https_proxy` when it is set. The job only downloads; it never uploads anything. A list
whose signature verifies with one of the pinned roots or with an intermediate certificate seen in
the stamps is stored byte for byte as:

```text
D:\ARCILOGGER\state\crl\<issuer-sha256-first-12>-<thisUpdate, YYYYMMDDTHHMMSSZ>.crl
```

and described in `state\crl\index.json` (schema `arcilogger.crl-index/1`: URL, file, issuer,
`this_update`, `next_update`, SHA-256, download time). A list that does not verify is not stored;
its failure is recorded in the index:

| Error | Meaning |
|---|---|
| `unreachable` | The address could not be reached |
| `too_large` | The list is larger than 20 MB |
| `not_a_crl` | The downloaded file is not a CRL |
| `bad_signature` | The list's signature did not verify |
| `unknown_issuer` | The issuer's certificate is neither among the pinned roots nor among the intermediates in the stamps |

The job **never deletes a file**: a certificate authority may drop an expired certificate from
later lists, so older lists keep their worth. `crl status` also shows the number and the size of
the stored lists.

```powershell
& $core crl fetch            # downloads and verifies every address
& $core crl fetch --json     # gives the result as JSON
& $core crl status           # shows the index (stored lists, failures)
```

| Exit code (`crl fetch`) | Meaning |
|---|---|
| 0 | Every address was downloaded and verified |
| 1 | Some failed (recorded in the index) |
| 2 | No address is known |

### 4.2 Verification

The verifier on the server checks revocation with the stored lists; the codes and levels are those
of the standalone verifier:

| Code | Level | Meaning |
|---|---|---|
| `crl-revoked` | ERROR | A certificate was revoked before the stamp; the day's check fails (section 5.8) |
| `crl-revoked-later`, `crl-hold` | WARN | The certificate was revoked after the stamp, or is on hold; to be examined |
| `crl-none`, `crl-stale` | INFO | The lists do not cover this certificate or the stamp time; the revocation status is unknown |
| `crl-ok` | OK | The CRL check passed |

The evidence package does not ship every list, only those it needs: for each issuer the newest
list, and the first list after each packaged day's stamp time (`thisUpdate` ≥ the stamp time).
With no list, the package writes the note that says so. The standalone verifier makes no online
check (and no OCSP); it reads only the lists in the package
([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) sections 6 and 7).

## 5. The legal-validity banner

### 5.1 How the banner works

**Every signed-in user** (every role) sees the legal-validity banner at the top of every page,
under the licence banner. The banner is fed by the answer of `GET /legal-status`. The status is
computed by one server function, and the same source also feeds the Panel's blocking card, the
Mühürleme page and the e-mail alerts (section 6).

- The level is **danger** (red) or **warning** (amber), always with an icon and text. With no
  reason, the banner is not shown.
- The banner shows the worst reason; when there are more, it says "ve N uyarı daha / and N more"
  and opens the list when clicked.
- **Danger cannot be closed.** A warning can be hidden for 24 hours in that browser.
- The banner refreshes every 5 minutes. Depending on the condition, a reason may link to the
  Mühürleme page or to Ayarlar → Zaman damgası; a link to a page the user may not open is not
  shown.
- `GET /legal-status` is open to every signed-in user, is not written to the audit log, carries no
  data about log subjects, and is cached for 60 seconds. It names the provider: "Kamu SM",
  "TÜRKTRUST", "Test sunucusu" (test server), "Deneme (mock)".

The reasons, their texts and their sections (the banner's text ends with the reference to the
section, "ZAMAN-DAMGASI.md bölüm N"; the `{…}` fields in the texts are filled with the current
values):

| Reason | Level | Section |
|---|---|---|
| `provider_not_legal` | danger | 5.2 |
| `credentials_in_clear` | warning | 5.3 |
| `legal_root_missing` | danger | 5.4 |
| `unstamped_days` | danger | 5.5 |
| `renewal_due` | warning | 5.6 |
| `renewal_uncovered` | warning | 5.7 |
| `certificate_revoked` | danger | 5.8 |
| `crl_stale` | warning | 5.9 |

The list shows the dangers first, then the warnings, each sorted by code.

### 5.2 `provider_not_legal` — a trial or test provider (danger)

> Time stamps come from a trial or test provider and have no legal validity. Choose the Kamu SM
> production server under Settings → Time stamping.

**Why?** The provider is `mock`, `rfc3161` or a test server (section 1). The red alert on the
Mühürleme page shows the same text.

**What to do?** Switch to a production provider: Kamu SM ([kamusm.md](kamusm.md),
[INSTALL.md](INSTALL.md) section 10) or TÜRKTRUST ([turktrust.md](turktrust.md); section 2.2
first). Days stamped earlier by a trial or test provider do not change.

### 5.3 `credentials_in_clear` — TÜRKTRUST over plain HTTP (warning)

> The TÜRKTRUST connection uses unencrypted HTTP; the username and password cross the network in
> clear text. Switch back to HTTPS if you can.

**Why?** The provider is `turktrust` and `[tsa] allow_plain_http` is on (TÜRKTRUST's plain-HTTP
choice under Ayarlar → Zaman damgası). Stamps are taken, but the HTTP Basic credentials travel
unencrypted.

**What to do?** Go back to the HTTPS TÜRKTRUST choice under Ayarlar → Zaman damgası
(`https://zd.turktrust.com.tr/`, port 443) and switch `[tsa] allow_plain_http` off. If HTTPS does not
work, find out why on site (firewall, proxy, the URL path T1; [turktrust.md](turktrust.md)). If the
password travelled in clear for a while, consider changing it.

### 5.4 `legal_root_missing` — no root fingerprint (danger)

> No legal root fingerprint is set; no day can be shown as legal grade.

**Why?** The provider's claim is legal grade (Kamu SM or TÜRKTRUST production), but
`[tsa] legal_root_fingerprints` is empty. The third of the three conditions can never hold
(section 2.1).

**What to do?** Download the provider's root certificate, compare its SHA-256 fingerprint with the
value the provider publishes, record it, and add the root under Ayarlar → Zaman damgası with the
"legal" confirmation. Kamu SM: [kamusm.md](kamusm.md) section 4 step 3; TÜRKTRUST:
[turktrust.md](turktrust.md) section 5.

### 5.5 `unstamped_days` — closed days not stamped (danger)

> {count} closed day(s) have not been time-stamped for more than {hours} hours.

**Why?** A closed day has been without a stamp for longer than `[seal] late_after_hours` (default
48) hours after the end of the day. The blocking card also appears at the top of the Panel
(section 6).

**What to do?** Check the pending days and the last error on the Mühürleme page and the `Seal` task
under Sistem → Zamanlanmış görevler; look at the `R20-seal` and `R20-seal-config` alerts. Possible
causes: missing credentials or roots, credit or balance used up, the server unreachable (firewall,
proxy). Days stamped after this period are permanently marked "stamped late" ("geç damgalandı";
[FIRST-STEPS.md](FIRST-STEPS.md) section 3).

### 5.6 `renewal_due` — re-stamping is due (warning)

> {count} stamp certificate(s) expire within {days} days; re-stamping is due.

**Why?** There are stamps with the status `due` (section 3.4); `{days}` is the value of
`[tsa] renew_before_days`.

**What to do?** The `Renew` task does this by itself every day. If the warning stays for several
days, look at the last result of the `Renew` task under Sistem → Zamanlanmış görevler, see what
would be renewed with `& $core renew --dry-run`, and run `& $core renew` if needed (section 3.5).
If the provider is not legal grade, its renewals do not count (section 3.3): section 5.2 first.

### 5.7 `renewal_uncovered` — certificates expired without re-stamping (warning)

> {count} stamp certificate(s) expired without re-stamping; the stamps stay valid, but re-stamping
> is recommended for long-term evidence.

**Why?** There are stamps with the status `uncovered` (section 3.4).

**What to do?** Re-stamp now with `& $core renew`; the stamps then become `late` (section 3.4).
Find out why the renewal was not done in time (the `Renew` task, credentials, balance). Assess the
evidential value of these days with your legal counsel.

### 5.8 `certificate_revoked` — a certificate is on a revocation list (danger)

> A time-stamp certificate is on a revocation list: {name}. Contact the provider; legal review is
> recommended.

**Why?** A TSA certificate in use appears, on a stored and verified revocation list, as revoked
before the stamp (`crl-revoked`, section 4.2). The verification of the days concerned fails.

**What to do?** Contact the provider that issued the stamp (Kamu SM or TÜRKTRUST); ask for legal
review. Do not delete or change any file: the sealed data, the stamps and the revocation lists
are evidence. `& $core crl status` shows which list gave this result.

### 5.9 `crl_stale` — no current revocation list (warning)

> No verified revocation list newer than {days} days.

**Why?** For the issuer of a certificate in use, there is no verified list newer than
`[tsa] crl_max_age_days` (default 7) days.

**What to do?** Run `& $core crl fetch` and look at the failures with `& $core crl status`
(section 4.1): for `unreachable`, the firewall, the proxy and the address; for `unknown_issuer` or
`bad_signature`, the pinned roots. If the CRL addresses are unknown, put them into `[tsa] crl_urls`
(K10, T4: **verify on site**).

## 6. The Panel, the Mühürleme page and the e-mail alerts

- **Panel:** when a closed day has been unstamped for longer than `[seal] late_after_hours` hours,
  a blocking card appears at the very top of the Panel: the oldest unstamped day and how many
  hours late it is. The card goes away once the day is stamped.
- **Mühürleme page:** the trial-or-test-provider alert (section 5.2) shows the same text as the
  banner. A day's details also show that day's re-stamping status (section 3.4: status,
  certificate end, the counting renewal) and the result of the revocation check (section 4.2).
- **E-mail alerts** (each one alert, dedup key `<code>:none`; the e-mails carry no person names
  and no log lines). Every code and what to do: [FIRST-STEPS.md](FIRST-STEPS.md) section 9.

| Alert | Severity | Raised when |
|---|---|---|
| `R20-renewal` | high | A stamp is `due` or `uncovered`; also when the `Renew` task failed twice in a row |
| `R20-cert` | critical / high | A certificate is on a revocation list (`crl-revoked`, critical), or expires within 30 days and is not covered by a renewal (high) |
| `R20-crl` | medium | For the issuer of a certificate in use, no verified list is newer than `[tsa] crl_max_age_days` days |

Re-stamping, the CRL download, the banner and the TÜRKTRUST provider are **5651 Temel**
functions: no licence state switches them off ([LICENSING.md](LICENSING.md)).

## 7. Settings

The new settings of this release (`arcilogger.toml`):

| Setting | Type, default | Bounds | Meaning | From the browser |
|---|---|---|---|---|
| `[tsa] renew_before_days` | integer, 180 | 30–730 | How many days before the certificate expires a stamp is re-stamped (section 3.1) | Ayarlar → Zaman damgası (with re-authentication) |
| `[tsa] crl_urls` | list of http(s) URLs, `[]` | at most 20 | CRL addresses; when empty, the certificates' distribution points (section 4.1) | Ayarlar → Zaman damgası (with re-authentication) |
| `[tsa] crl_max_age_days` | integer, 7 | 1–60 | A list older than this many days gives `crl_stale` and `R20-crl` (section 5.9) | Ayarlar → Zaman damgası (with re-authentication) |
| `[network] https_proxy` | string, `""` | `http://host:port` or empty | Outbound proxy: CRL download, `rfc3161`, `turktrust` (section 1) | Ayarlar → Genel (General; with re-authentication) |
| `[tsa] allow_plain_http` | boolean, false | meaningful only for `turktrust` | Plain HTTP (port 80) to TÜRKTRUST; `credentials_in_clear` (section 5.3) | only with TÜRKTRUST's plain-HTTP choice, by typing a confirmation |

With the provider `turktrust`, `[tsa] url` must start with `https://`; while
`[tsa] allow_plain_http` is off, an `http://` address is a configuration error (the password would
travel in clear text). Related existing settings: `[tsa] provider`, `[tsa] url`,
`[tsa] roots_dir`, `[tsa] legal_root_fingerprints`, `[tsa] require_critical_eku`,
`[tsa] timeout_seconds`, `[tsa] credit_regex` (a credit word is looked for in TÜRKTRUST's rejection
text too, T2), `[seal] late_after_hours`. Secrets: `turktrust.username` (at most 128 characters)
and `turktrust.password` (at most 256 characters). All the Settings pages:
[SETTINGS.md](SETTINGS.md).

## 8. Verify on site

Every behaviour the repository cannot confirm is configurable and guarded by a test that fails
loudly if the assumption breaks. Details and the manual checklist: [turktrust.md](turktrust.md)
sections 2 and 3, [kamusm.md](kamusm.md) section 3.

| Id | Assumption | Where it is configured |
|---|---|---|
| T1 | TÜRKTRUST's TSA URL `https://zd.turktrust.com.tr/` (confirmed 2026-11-08: the root path stamps) | `[tsa] url` |
| T2 | A wrong password: HTTP 200 and a rejection without text (confirmed 2026-11-08; reported as a credentials error). Its answer for an empty balance is unknown: a rejection whose status text contains a credit word counts as "no credit" | `[tsa] credit_regex` |
| T3 | TÜRKTRUST's TSA certificate marks the EKU extension critical (confirmed 2026-11-08) | `[tsa] require_critical_eku` |
| T4 | TÜRKTRUST's CRL: `http://www.turktrust.com.tr/sil/TURKTRUST_Kok_SIL_h7.crl` (confirmed 2026-11-08) | `[tsa] crl_urls`; the firewall opens these hosts |
| T5 | HTTPS works (confirmed 2026-11-08); plain HTTP is not needed | `[tsa] allow_plain_http` (off by default) |
| K10 | Kamu SM's CRL addresses | `[tsa] crl_urls`, otherwise the certificates' distribution points |
| — | How the Zamane console client takes a proxy | not in this release: Zamane does not use `[network] https_proxy` |
