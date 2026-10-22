# ADR-0010: Alerts in the database, R15/R20 checks, alert e-mail without log content

- Status: proposed (2026-10-19)
- Deciders: the product owner; proposed with group 1, area E (alerts), build specification 0 and 11

## Context

Specification 11 lists the alert rules. Two of them protect the evidence itself: **R15 "Log kaynağı
sessiz"** (no message from a source for longer than its heartbeat — in the example configuration the
firewall 10 minutes, the core router 15 minutes; compliance-critical) and **R20 "ARCILOGGER
sistem"** (sealing or verification failed, Kamu SM credit below its threshold, disk more than 80 %
full, clock offset above 1 s, replica older than 48 hours, parse failures above 1 %). Alerts have
states, and every rule needs a positive and a negative test. The rule engine, the Security pages and
webhooks follow in M5.

Until now the evidence tasks (sealing, verification, replica, retention, clock, credit — all run by
Task Scheduler without the database, ADR-0003/0007) and the collector appended their alerts to
`state/alerts.jsonl`; nothing read that file except the audit import, and nothing sent an e-mail.
A configuration error of the Seal task (no Kamu SM credentials, no pinned roots, no Zamane client)
wrote no alert at all.

Specification 0 forbids sending any log content outside the server: only SHA-256 hashes go to Kamu
SM. An alert e-mail leaves the server, usually to a mail provider outside the company.

## Decision

1. **The worker owns the alert table.** Four jobs (`alerts.import`, `alerts.r15`, `alerts.system`,
   `alerts.notify`; docs/ALERTS.md). `alerts.import` copies new `alerts.jsonl` lines into `alerts`
   **exactly once**: the byte offset after the last complete line is stored (internal setting
   `worker.alerts_import`, with the SHA-256 of the file's first line) in the same transaction as
   the alerts of those lines, under an advisory lock; a torn last line waits for its newline; a
   replaced file (shorter, other first line) is read again from the start. The file format and
   the signature of `raise_alert` stay as they are (it only starts on a fresh line when a power
   cut left a torn tail, which the import then skips): the evidence path keeps working without
   the database.
2. **One active alert per problem.** Every rule has a dedup key (`R15:<source>`, `R20-seal:<day>`,
   `R20-disk:<drive>`, …). An alert is active while `open` or `acknowledged`; a repeated event
   is counted on it, a check that still finds its problem rewrites it only when something changed
   (and records the time it saw it at most once an hour, `details.seen_at`), and the check that
   owns a key **resolves** its active alert itself once the problem is gone (`resolved_at`,
   `details.auto_resolved`). An alert a person resolved or dismissed is never re-opened.
3. **Checks read what is already recorded** — `sources`, `state/collector-status.json`, the seal
   states the sealing page uses, `state/verify`, `seal_verifications`, `state/credit.jsonl`,
   `state/replica`, `messages`, `job_runs`, `state/backup/last.json` — and never contact a device,
   Kamu SM or the replica. Thresholds are database settings (`alerts.*`) except the credit
   threshold, which stays `[tsa] credit_alert_threshold`. R15 waits a grace period after the worker
   or a source row started, so a reboot does not alert every source.
4. **An e-mail carries no log content.** It is built only from a fixed Turkish/English catalogue
   (rule name, one line on what to do) and from **whitelisted facts** of the alert's details, each
   checked for its shape (a source name, a day, a number, a drive letter, a job name, the sender IP
   address of a new source or of a message refused for its transport, the IP address of an IP
   conflict; the source name of a sender inside a configured network or of one that is not
   configured holds its IP address, e.g. `unknown-<address>`). It never quotes an
   alert's title or details — they can hold error texts, MAC addresses or, for later rules, log
   content — and so never a raw log line, a person's or device owner's name, an IP-to-person link
   or a secret. No images, no external resources, one link: the ARCILOGGER web address. A test
   feeds alerts whose titles and details contain a person's name and a raw FortiGate line and
   checks every part of the message.
5. **Delivery semantics.** One `alert_deliveries` row per alert, kind (`new` / `resolved`) and
   recipient, unique on `(alert_id, kind, recipient)`. The rows of one e-mail are leased (attempt
   count, retry time) and committed before sending and marked `sent` after: a crash between
   sending and recording sends that e-mail **once more**, never more (exactly-once is not possible
   across SMTP). One e-mail per recipient, so a refusing address affects only itself. Alerts
   within `alerts.email_digest_seconds` go into one e-mail. Temporary failures are retried with a
   growing backoff (1, 5, 15, 60, 180 minutes); after 6 attempts, or at once for a 5xx refusal, the
   rows are `failed` and **one** local alert `R20-email` is opened — it is never e-mailed (no
   loop) and is resolved once an e-mail reached each recipient it names or they were taken out of
   `alerts.email_recipients`. At most `alerts.email_max_per_hour` e-mails per recipient; beyond
   it alerts are `suppressed` and the next e-mail the limit allows says how many and of which
   rules. Only alerts whose problem was last seen within 24 hours are e-mailed (first occurrence,
   imported repeat or a check finding it again): importing an old `alerts.jsonl` never mails
   history, a problem still going on after an outage is mailed.
6. **Where the settings live.** The SMTP connection (`[smtp]`: host, port, security, user,
   sender, timeout, CA file) is machine configuration in `arcilogger.toml`, like `[database]` and
   `[replica]`: it changes together with the firewall (`Set-ArciFirewall.ps1 -SmtpServer`), it is
   needed by `arcilogger-worker alerts test-email` before anything is stored, and the password is
   the secret `smtp.password` in the encrypted secret store — never in the database, whose setting
   changes are written with their old and new values into the audit log. The **policy** (who,
   which severity, digest, limit, resolved e-mails, web address) is in the database settings:
   validated, audited, changed at run time without a restart, and editable on the settings page
   later.
7. **SMTP from the standard library** (`smtplib`): `starttls` (default, port 587) or `ssl`
   always verify the server certificate (system trust store or `[smtp] ca_file`); there is no
   switch to turn verification off. `none` is plain SMTP for a relay on the server itself.
8. **Sealing configuration errors alert.** The Seal task raises `R20-seal-config` (high, Turkish
   and English text naming what is missing) at most once per site day and cause, because it runs
   every 5 minutes; the error still goes to `logs\core.log` on every run.

## Consequences

- Operators get an e-mail for every high or critical problem without any log content leaving
  the server; the e-mail says what and where, the details are in ARCILOGGER
  (`arcilogger-worker alerts list`, later the Security page).
- A crash can repeat one e-mail; a full SMTP outage longer than about four and a half hours drops
  the e-mail (the alert itself stays, plus `R20-email`).
- R15 judges silence by what ingest loaded and what the collector received: a stopped collector
  makes every source silent, which is correct (nothing is being recorded).
- Acknowledging and resolving by hand, webhooks and the YAML rule engine come with M5 on top of
  the same table and dedup keys.

## Alternatives considered

- **The alert title in the e-mail** — simpler, but titles carry exception texts, paths, MAC and IP
  addresses, and rules of M5 may quote log fields. Rejected (specification 0).
- **SMTP password in the database settings** — every settings change is audited with its values.
  Rejected.
- **One e-mail to all recipients** — one refused address would fail the e-mail for everyone and
  there would be no per-recipient state. Rejected.
- **A third-party mail library or service** — not needed for plain SMTP with TLS; no external
  service may receive alert data. Rejected.

## Amendment 2026-10-20: the alert workflow, notes and the dashboard (group 2, area S)

- Status: proposed (2026-10-20).
- Why: specification 11 gives alerts the states Yeni, İnceleniyor, Kapandı and Yanlış alarm,
  notes, and a "Güvenlik Özeti" with the mean time to close; 12.3 page 2 puts the log intake, the
  sealing, the credit, the clock and the open alerts on the dashboard. The decision above left
  acknowledging and closing by hand to a later step.

### A1. Every change is an event

`alert_events` (revision `0012_alert_events`): one row per change of state (`from_state` →
`to_state`) and per note (`text`, 1-2000 characters), with who (`actor`: the user name, or
`system`; `actor_id`) and when (`at`). The API calls `actor` `by` (`by` is an SQL keyword). The
checks' automatic resolution and the IP-conflict rebuild's re-opening write `system` events with
their reason code in `text` (`messages_again`, `condition_cleared`, `lease_rebuild`,
`conflict_found_again`, …); the creation of an alert is not an event (`alerts.created_at`).
Nothing is backfilled: alerts closed before the revision have no events.

### A2. One writer, in one transaction

`arcilogger_worker.alerts.store` is the only writer: `resolve` / `reopen` for the checks,
`change_state` / `add_note` for a person (the API). A person's change writes the event, the
alert (`state`, `updated_at`, `resolved_at` — set for `resolved`, cleared otherwise — and drops
`details.auto_resolved`: the alert is now the person's, and a rebuild never re-opens it) and the
audit entries in the request's transaction: `alert.state` with from/to and the rule,
`alert.note` with the note's **length only**. A note is free text and may name people; the
sealed audit log keeps neither its text nor any part of it, and the e-mail never carries it.
Notes cannot be edited or deleted (the timeline is a record); a correction is a new note.

### A3. Transitions and the dedup key

| From \ to | open (Yeni) | acknowledged (İnceleniyor) | resolved (Kapandı) | dismissed (Yanlış alarm) |
|---|---|---|---|---|
| open | — | ✔ | ✔ | ✔ |
| acknowledged | ✔ | — | ✔ | ✔ |
| resolved | ✔ re-open | 409 | — | ✔ |
| dismissed | ✔ re-open | 409 | ✔ | — |

The same state answers 409 `alert_state_unchanged`, a missing transition 409
`alert_transition_not_allowed` (re-open first). A dedup key is unique among **active** alerts
only. So a person who closes an alert whose problem persists does not silence it: the next run
of the check opens a **new** alert (and, from `alerts.email_min_severity`, a new e-mail). The
closed alert can then not be re-opened (409 `alert_key_active`, `details.alert_id` = the active
one): work continues on the new alert. While a problem is being worked on, the alert is set to
"İnceleniyor": it stays active, the check neither duplicates nor closes it until the problem is
really gone, and then resolves it by itself. The partial unique index
`uq_alerts_open_dedup_key` decides a race with a check that opens the key at the same moment.

### A4. What the web reads

Rule names, a one-sentence summary (from the shape-checked facts of `texts.py`, else the title)
and a step-by-step runbook per rule (`texts.RUNBOOKS`, the same advice as docs/UYARILAR.md) come
from the catalogue, never from the database. The details are shown sanitised: secret-looking
keys and `password=…` pairs redacted, e-mail addresses masked like the deliveries' recipients
(`a***@firma.com.tr`) — also inside a text, such as an SMTP reply, and in the title and the
summary (`R20-email` and `R20-report` name the recipient) —, texts cut at 1,000 characters,
lists at 100 items.

### A5. Summary and dashboard

The mean time to close counts the alerts that are closed now and whose newest closing event (a
change from open or acknowledged to resolved or dismissed, by a person or a check; switching
between resolved and dismissed is no closing) lies within the last 30 days, from `created_at`. `GET /dashboard/summary` returns every box the user may see and `null` for the
others (docs/MVP-CONTRACTS.md section 14). The messages per minute of the last hour are counted
from `messages` through the primary key `(time, id)` of the month's partition (index-only
scan): no new index, no counter table; a test keeps the query under 1 s with 300,000 messages
in the hour.

### A6. Not decided here

The assignee of spec 11, the YAML rule engine, its editor and webhooks stay open. `alerts.read`
and `alerts.manage` stay administrator-only (docs/roles.md); the auditor sees the sealing,
credit and clock boxes of the dashboard, not the alerts.

### Consequences of the amendment

- The Security page can show and change the state of every alert with its full history, and
  the audit log shows who changed what without holding a note's content.
- Closing an alert whose problem persists produces a new alert; the documents tell operators
  to use "İnceleniyor" while they work on a problem.
- Alerts closed before revision `0012_alert_events` do not count in the mean time to close.

### Alternatives considered for the amendment

- **A database trigger writing the events** — it cannot know the person (or every writer would
  have to set a session variable) and the API still writes the audit entry. Rejected.
- **Notes in `alerts.details`** — one JSON document rewritten on every note: no per-note author,
  time or count, and the checks rewrite `details` themselves. Rejected.
- **Re-opening by merging the closed alert into the active one** — two histories in one alert
  and a moving dedup key. Rejected: the active alert is the one to work on.
