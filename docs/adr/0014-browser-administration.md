# ADR-0014: Browser administration — an unprivileged administration agent, signed requests, an input-free SYSTEM step

- Status: accepted (2026-10-25). Decided by the developer on the owner's delegation; the owner
  may change any part of it (open points at the end).
- Related: [ADR-0001](0001-windows-server-browser-only.md), [ADR-0003](0003-integrity-on-windows.md),
  [ADR-0007](0007-windows-installer.md) (§4 accounts and rights, §8 task identity),
  [ADR-0009](0009-database-backup.md), [ADR-0010](0010-alerts-and-email.md),
  [ADR-0013](0013-offline-licensing.md); the API contract goes into
  [MVP-CONTRACTS.md](../MVP-CONTRACTS.md) as §16 with the API work. The channel's names, limits
  and file formats are code: `arcilogger_core.admin.contract`, `arcilogger_core.admin.envelope`,
  `arcilogger_core.admin.keys`, `arcilogger_core.admin.messages`, `arcilogger_core.execpolicy`,
  `arcilogger_core.configwatch`.

## Context

ADR-0001 says people use ARCILOGGER through a browser only. In practice an administrator still
logs on to the Windows server to change most settings: `C:\ProgramData\ARCILOGGER\arcilogger.toml`
in a text editor, `arcilogger-core secret set` for credentials, PowerShell for the firewall and the
time service, `arcilogger-db settings set` for the database settings. "Şimdi mühürle"
(`POST /seals/{day}/seal`) answers 501, and there is no "Kontör sorgula".

The constraints that shape the design:

- **The API keeps its rights.** `NT SERVICE\ArciApi` serves the network-facing web interface. It
  may read the configuration and `secret.key` (it needs the database password, the TOTP secrets
  and, for report time stamps, the Kamu SM credentials), but it must not gain write access to the
  configuration, the secret key or the vault.
- **The 5651 pipeline never stops** because of a settings change: the collector, the spool and
  sealing keep running; only a deliberate, announced restart may interrupt them.
- **A failed change leaves the previous working configuration in place** and is reported to the UI.
- **Secrets never appear in plain text** at rest outside the secret store, in logs, in audit rows
  or in API responses. Credential and retention changes need a recent re-authentication;
  retention below 730 days is impossible.
- **Every change and action is audited** with old and new values, secrets masked.
- **Offline**: closed networks, no telemetry, no external service.
- **Testable on Linux CI**; the Windows-only parts small and covered by pwsh tests and the
  Windows CI job.
- Shared identities: the evidence tasks run as LOCAL SERVICE, which W32Time, the firewall service
  and other Windows services share (ADR-0007 §8); `secret.key` is readable by ingest, worker, API
  and LOCAL SERVICE.

## Decision

1. **A new service, ArciAdmin**, runs as the virtual account `NT SERVICE\ArciAdmin`: no listener,
   no administrator rights, no service-control, task, firewall, registry, vault, spool or
   database rights. It alone writes `arcilogger.toml` and `secrets.json` (explicit file ACEs),
   may add Kamu SM root certificates (never change or delete one an administrator placed),
   and stages uploaded files.
2. **Signed request files.** The API writes one request per change into a protected inbox,
   signed with Ed25519 (RFC 8785 canonical payload, a domain prefix, a key id; the key readable by
   ArciApi only). Secrets travel as Fernet tokens under the existing `secret.key`. ArciAdmin
   claims a file before it checks it, checks size, signature, installation, expiry and its id
   ledger before it parses the payload, and applies each request at most once. The same envelope,
   with its own domain prefix and the agent's key, carries the agent's requests to the tasks.
3. **One rule set, two checks.** `arcilogger_core.admin.rules` is a closed allow-list of what the
   browser may change, with per-field validation. The API runs it for an immediate 422; ArciAdmin
   runs it again, authoritatively, against the file at apply time. Executables (`tsa.java`, the
   Zamane jar, `pg_dump`, `pg_restore`), paths, `[database]`, `[api]`, `[collector]`, `[ingest]`,
   `[worker]`, `[seal]`, `data_dir` and `secrets_dir` are never free values from the browser.
4. **Atomic apply.** A write-ahead record, a version store (20 configurations, 5 secret stores,
   agent-only), temporary files on the same volume, `ReplaceFileW` (keeps the file's rights),
   verification by loading the result, rollback on any failure and recovery after a crash. The TOML
   is edited by a narrow in-house editor whose result must parse to exactly the expected document.
5. **Input-free SYSTEM step.** The task `\ARCILOGGER\ApplySystem` (SYSTEM; event-triggered and
   every 5 minutes) re-derives the firewall's syslog addresses, the strict outbound rules and the
   Windows Time peers from the validated configuration and the administrator-only `policy.json`,
   updates rules in place, and widens the syslog rule only after the collector has loaded the new
   configuration. It also promotes a staged Zamane client that is a known release, by hash only.
6. **Evidence actions under their own identity.** Seal now runs in the Seal task (LOCAL SERVICE),
   woken by an Event Log entry and authorised by a request file signed by ArciAdmin; `seal_due`
   ignores the retry wait once, for the oldest due day only. Credit query, TSA test and replica
   test run in the new LOCAL SERVICE task `\ARCILOGGER\Requests`. Test e-mail and lease rebuild are
   worker jobs on a database queue that never holds a secret.
7. **Executable policy at run time.** The tasks, the backup and the API's report stamps accept
   `tsa.java` (one program, no extra arguments), the Zamane jar and the backup tools only from
   administrator-only locations (Program Files, `zamane\`), a known release list in code, a
   console approval, or a SHA-256 the installer recorded for that very path. The trust anchors
   (`admin\policy.json`, `zamane\`, the installer's jar record, `admin\system\jars.json`) come
   from the folder of the fixed `--config` path, never from a configuration key such as
   `secrets_dir`; a checked path may contain no link or junction, and the caller runs exactly
   the path it checked. A compromised ArciAdmin (or API) can therefore write configuration, or
   upload a Zamane client, but cannot get code to run as LOCAL SERVICE or SYSTEM.
8. **Live reload, no restart.** Collector (sources), ingest (sources), worker and API (everything
   except their own process settings) reload a validated configuration within seconds, keep the
   old one on any error and report the SHA-256 they loaded. Tasks read the file on every run.
9. **Holds for the most consequential changes.** Enabling or lowering retention and disabling or
   retargeting the replica wait `hold_hours` (installer policy, default 0). A Zamane client that
   is not a known release becomes runnable only when an administrator approves it on the console
   (`approve-jar`), at the earliest `jar_hold_hours` (default 24) after it was staged; time alone
   never approves it. Holds can be cancelled by any administrator; every hold, application and
   refusal raises an alert and an Event Log entry.
10. **Database settings through the API**, on top of the existing validator and audit writer, with
    Turkish and English errors and re-authentication for security- and retention-relevant keys.
11. **Two audit trails.** The API records each request in the audit chain in the same transaction
    that queues it; ArciAdmin's own journal of outcomes is imported by the worker; ArciAdmin and
    ApplySystem also write the Windows Application log (source `ARCILOGGER-Admin`). Secrets appear
    only as `set`/`unset`.
12. **Off switch.** `-DisableBrowserAdministration` writes `enabled: false` into `policy.json`; the
    agent then refuses every request and the web shows the console path. No licence state ever
    affects any of this.

## Consequences

- Almost everything an administrator configured on the console is a browser page: site name,
  clock, Kamu SM (credentials, test/production, roots, legal fingerprints, Zamane client, credit
  threshold, TSA test, credit query), SMTP, replica, retention, backup, log sources with their
  firewall rule, the database settings, Şimdi mühürle and the lease rebuild.
- What stays on the console: installation, upgrade, PostgreSQL, `[database]`, `[api]`,
  `[collector]`, `[ingest]`, `[worker]`, `[seal]`, paths and executables, approving a Zamane
  client that is not a known release, removing roots or legal fingerprints, the admin policy,
  administrator addresses and strict outbound mode of the firewall, `database.password`,
  `usage.pseudonym_key`, key rotation, recovery (`create-admin`).
- A compromised API can request any change of the allow-list (it already reads every stored
  secret). It cannot lower retention below 730 days, choose an executable, switch to the mock
  stamp, hide the agent's journal or the Event Log, or make a held change take effect early; a
  Zamane client it uploads stays staged until an administrator approves it. A compromised
  ArciAdmin can write configuration (visible, alerted) but cannot reach LOCAL SERVICE or SYSTEM
  code execution.
- One more service (ArciAdmin), two more tasks (ApplySystem as SYSTEM, Requests as LOCAL SERVICE),
  one Event Log source. Windows behaviour to verify on site and in Windows CI: `ReplaceFileW` keeps
  the explicit ACE, a virtual account writes the Application log, EventTrigger delivery,
  in-place address filter updates without dropped datagrams.
- A valid hand edit of the configuration now takes effect without a restart; an invalid one is
  reported and the services keep the previous configuration (the next start would fail).
- Comments inside `[[sources]]` tables are lost on the first browser edit of sources (the version
  store keeps the previous file); other comments are kept.
- A downgrade after a source was disabled restores the pre-upgrade configuration copy, losing the
  later browser changes (they stay in the audit log).

## Alternatives considered

- **Write rights for ArciApi** — the simplest; forbidden by the constraints. Rejected.
- **A SYSTEM runner** (a scheduled task or a LocalSystem service doing all of it) — fewer parts,
  but it parses API-written JSON, X.509 and ZIP files in folders the API can modify: any parser or
  file-system bug (junctions, time-of-check races) becomes SYSTEM compromise. Rejected.
- **An agent with Modify on all of ProgramData** — it could replace the Zamane jar (code as LOCAL
  SERVICE), delete Kamu SM roots (past days no longer verify) and read old secret backups.
  Rejected.
- **A local HTTP or named-pipe channel** — a new listener and its own authentication. Rejected.
- **The database as the channel** — encrypted credentials would end up in the nightly dumps
  (ADR-0009 §6) and the agent would depend on PostgreSQL. Rejected for anything secret; kept for
  the worker's non-secret actions.
- **HMAC instead of signatures** — would put the verification key into the shared LOCAL SERVICE
  identity on the task hop. Rejected.
- **A deterministic TOML renderer or tomlkit** — the renderer drops the bilingual comments of the
  shipped file; tomlkit is a new dependency on the evidence configuration path. Rejected.
- **Restarting services on a change** — the collector would lose UDP datagrams. Rejected.

## Open points (owner decision)

- `hold_hours` (default 0) and the list of held changes; `jar_hold_hours` (default 24).
- Whether a Zamane client that is not a known release may become runnable after
  `jar_hold_hours` without a console approval. The developer chose no: an upload by a
  compromised API or agent would otherwise run as LOCAL SERVICE (sealing, the vault) once the
  hold ends unless an administrator cancels it in time, which an unattended server makes likely.
- The content of `arcilogger_core.tsa.zamane_releases.KNOWN_RELEASES` (empty until the owner
  downloads the files from Kamu SM and compares them with the published SHA-256).
- The default source networks (RFC 1918) and the widest network (/16).
- Whether removing a root certificate or a legal fingerprint should ever be offered.
- The test e-mail recipient rule (alert recipients or the user's own address).
