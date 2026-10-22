# ADR-0013: Offline licensing — signed licence files, paid modules, 5651 Temel never locked

- Status: accepted (2026-10-20)
- Deciders: the product owner and rights holder (Arman Khadangan); owner brief and licensing
  contract of 2026-10-20, owner confirmations of [C-1], [C-6] and [C-11]
  ([ASSUMPTIONS.md](../ASSUMPTIONS.md), "Licensing (owner 2026-10-20)")

## Context

ARCILOGGER is now sold to many organisations. The owner chose a **perpetual licence plus annual
maintenance**: a licence never expires, its `maintenance_until` limits only upgrades and support;
an optional `valid_until` serves time-limited demos. Every module has its own price; a licence
lists the enabled modules and two limits (log sources, user accounts); the base module **5651
Temel** is in every licence. A new installation gets a **14-day trial** with every module.

The constraints that shape the design:

- **Never block a 5651 obligation**, whatever the licence state: log collection, spool, ingest,
  sealing (Kamu SM time stamps), retention, replica, backups, chain verification, the 5651 lookup,
  evidence packages and the standalone verifier, the audit log, sign-in and users, the
  operational alerts and the dashboard health boxes. An expired, missing, invalid or other-server
  licence locks only paid modules and shows banners.
- **Never alter evidence:** no licence mark in raw logs, sealed days, evidence packages or the 5651
  PDF.
- **Offline:** the server runs in closed company networks; a call to the vendor would be
  telemetry. Activation is a request code e-mailed by a person and a signed file sent back.
- **No secrets in the repository:** the vendor's private signing key never enters it; the owner
  generates the key pair offline and commits only the public key. Until then the production key
  set is empty and every installation stays in trial or unlicensed mode.
- **Trusted time:** setting the Windows clock back must not extend a trial, `valid_until` or the
  maintenance; a reinstallation must not restart the trial while the vault holds older days.
- **The customer's administrator is the attacker** (Windows administrator and owner of the
  PostgreSQL database), and no check can phone home.

## Decision

1. **Signed licence file.** A `.lic` is UTF-8 JSON `{"payload", "signature"}` (at most 64 KiB):
   the payload names the licence id, licensee, issue time, one or more server ids, the modules,
   the limits, `maintenance_until`, `valid_until` and the hash of the request it answers. The
   signature is **Ed25519** (`cryptography`) over the **RFC 8785 canonical JSON of the payload**
   (`arcilogger_core.canonical`), with a key id so the key can be rotated. Parsing is strict
   (duplicate keys, floats, non-canonical base64 refused); every failure is a published error
   code, never another exception. The stored bytes are verified again on every evaluation.
2. **Trusted keys and revocation are code.** `arcilogger_core.license.keys.TRUSTED_KEYS` (empty
   until the owner commits the first public key) and `revoked.REVOKED_LICENSE_IDS` are module
   constants read at call time. No environment variable, configuration file, database row or API
   adds a key or lifts a revocation; tests swap the module attribute. The services and scheduled
   tasks run Python isolated (`python.exe -I -X utf8 -u -m …`), so `PYTHONPATH`, `PYTHONSTARTUP`
   and user site-packages cannot load code that swaps them.
3. **Offline activation.** The installation shows a **request code** (`ARCIREQ1-…`, Crockford
   base32 of the canonical request with a 40-bit checksum): server id, installation id, product
   version, organisation name, platform, whether the SMBIOS UUID was read, the UTC day. The
   customer e-mails it to the licensor, who issues the file with a standalone issuer tool kept on
   an offline machine (not shipped in the bundle, with an append-only register of issued
   licences). The customer uploads the file in the web interface, with `arcilogger-db license
   install` or with the installer's `-LicenseFile`.
4. **Server binding.** The server id (`XXXX-XXXX-XXXX-XXXX`) is a hash of the Windows MachineGuid
   and the SMBIOS UUID. A changed server needs a re-issued licence; the old one is revoked by id
   in a later release. The installation id is informational, so a database restore on the same
   server keeps the licence.
5. **One place for each question** (the hooks of phase 2): "is module X enabled" —
   `LicenseStatus.module_enabled` on a status from `arcilogger_db.licensing.load_status`, reached
   from the shared paths only through `arcilogger_db.license_guard.safe_status`; "what is the
   state" — `state.evaluate`; "what time is it" — `trusted_time.trusted_now`; "which keys" —
   `keys.TRUSTED_KEYS`; "which server" — `fingerprint.current_server_id`.
6. **Modules from the real features.** `usage_analytics` (owner-named), `usage_reports` (needs
   usage analytics) and `bulk_import` (the CSV import; manual entry always works, [C-1]) are paid;
   everything a 5651 obligation needs stays in `core_5651`. The API gates whole routers
   (`require_module`, 403 `module_not_licensed`), the worker skips the paid jobs (one `skipped` row
   per job and site day), the web shows a locked page and a lock in the menu. Collector, ingest,
   the core evidence modules and the scheduled tasks never import the licence code.
7. **Containment.** Only the licence packages import licence code at module level; every shared
   path imports it inside a guarded function. A broken licence subsystem (an import error, a bug,
   a database error inside its savepoint) yields "5651 Temel only, no users limit": no 5651 path
   and no user creation ever fails because of the licence code.
8. **Trusted time** is the maximum of the system clock, the newest verified TSA genTime of the
   sealed days, the end of the newest vault day by its name, the start of the version's release
   day and the verified licence's issue time. The trial start is pulled back by the oldest vault
   day, the oldest spool day, the audit log, earlier trial-start entries and the licence events;
   a trial start more than 10 minutes after the trusted time makes the trial unavailable.
9. **Maintenance is enforced at run time.** A version whose release date (`RELEASE_DATE`, also in
   the bundle manifest) is after `maintenance_until` is in the state `version_not_covered` (paid
   modules off). The installer refuses such an upgrade before any change, as advice;
   `-AllowUncoveredUpgrade` installs it with 5651 Temel only, so a 5651 fix can always be
   installed ([C-6]).
10. **Soft limits.** Log sources over the limit keep being collected and sealed (banner, audit);
    users over the limit keep working, only creating or re-enabling one is refused. Without a valid
    licence the users limit is 3 ([C-11]).
11. **Audit.** Every installation, removal and refusal of a licence and every limit crossing and
    state change is an audit entry (`license.*`), without the licensee name or server ids; the
    full details are in the append-only `license_events` table.

## Consequences

- No installation gets paid modules until the owner commits the first public key and issues
  licences: the product ships in trial / unlicensed mode, and the 5651 functions work throughout.
- Existing installations upgrade into `trial_expired` (their sealed days are older than 14 days)
  unless a licence is installed with or before the upgrade; the installer warns. An installation
  of a version without licensing can get its request code only after the upgrade, so its paid
  modules are off until the licensor answers ([LISANS.md](../LISANS.md) section 8).
- **Revocation by licence id reaches only installations that upgrade** to a release containing the
  id: maintenance customers upgrade, others keep a revoked licence working on the version they
  run.
- **Threat model.** Phase 1 closes: a forged or edited licence file; another server's licence on
  hardware whose MachineGuid and SMBIOS UUID are not spoofed; editing the licence rows of the
  database (they are an index; the licence is the verified file); a naive clock rollback and the
  clock-forward trial trick; a trial reset by deleting the installation row, by a fresh database
  with the old data folder or by re-stamping the oldest vault day; adding a trusted key through an
  environment variable, a configuration file, a database row or the API; the limits (unconfigured
  and disabled senders count; `create-admin` and repeated user creation respect the users limit;
  removing the licence does not lift it); a second server from one sale after a re-issue, for
  installations that upgrade.

  **Made harder only in phase 2:** patching the Python files of the licence code, or dropping a
  `.pth`, `sitecustomize` or `usercustomize` file into the venv's site-packages. Phase 2 raises the
  cost; it does not stop a determined administrator.

  **Never closable offline:**
  - spoofing the identity (editing the MachineGuid; on virtual machines the SMBIOS UUID) or
    cloning a virtual machine with the same ids — not detectable without a network;
  - a full fresh install (a new database **and** an empty data folder) for a new trial — it loses
    every sealed day and the audit log;
  - disabling the append-only triggers as database owner — it changes no licence decision;
  - hiding vault days and rolling the clock back — sealing stops and the sealing alerts fire, so
    it is visible in production;
  - reading usage tables already computed, with SQL, after the module is locked;
  - a determined administrator patching the checker.

  The remedies for these are contractual: the licence agreement (EULA), the vendor's register of
  issued licences, and the licensee name shown in the user interface. No document presents the
  licensing as impossible to copy or to break.
- The installer's maintenance rule reads unverified data (the `-LicenseFile` payload without
  Python, the customer-writable status file `state\license-status.json`); editing either gets past
  the refusal, which is accepted because the running version enforces `version_not_covered`.
- Licence evaluation adds a few small queries per cached status (API 30 s, worker 60 s) and the
  `license.check` job every 5 minutes; the dashboard summary and every 5651 route are unchanged.

## Phase 2 (designed for, not built)

Compile `arcilogger_core.license` into one extension module (without the test helpers and the
test override hook); check a signed manifest of the whole venv (every `*.pth` in site-packages
included), the base `python\` runtime and the WinSW XML files at service start before a status is
trusted, signed with its own key set (never a licence key); Authenticode-sign the executables and
the setup. Code outside the licence package never re-implements a date comparison, signature check
or module test (a source-scan test), so the compiled module stays the one place.

## Alternatives considered

- **Online activation** (the server calls a vendor service to activate or to check): rejected.
  ARCILOGGER runs in closed company networks, and any call to the vendor would be telemetry; the
  brief forbids it.
- **Hardware dongle** (USB key): rejected. It adds hardware to ship and replace, needs a driver on
  the server, fails in virtual machines and still does not stop a patched checker.
- **Licence in the configuration file** (`arcilogger.toml` or a key in the secret store): rejected.
  Anything the customer's administrator can write cannot carry trust; the trusted keys and
  revocations are code, the licence itself a signed file verified on every evaluation.
- **A domain prefix on the signed message** (signing `"arcilogger.license/1\n" + payload`):
  rejected, because the brief binds the signature to the canonical JSON of the payload. Domain
  separation comes from the key set instead: licence keys sign licences only, a later signed
  manifest gets its own key set and `schema`, and the payload's own `schema` field is signed.
- **A grace period or a trial fall-back after an unusable licence:** rejected ([C-5]); a changed
  server needs a re-issued licence, and the 5651 functions keep working meanwhile.
