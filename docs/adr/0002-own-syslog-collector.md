# ADR-0002: Own syslog collector; the raw spool is the queue

- Status: accepted (2026-10-12)

## Context

The legal evidence is the raw log exactly as received. The original design used rsyslog to write
raw files and forward a copy to the parser through a disk-assisted queue. rsyslog does not run on
Windows. Third-party Windows syslog servers exist (e.g. NXLog CE) but add a separately licensed
component whose file format and failure behaviour we would have to wrap anyway.

## Decision

Write a small, dedicated collector (`arcilogger_collector`, Windows service `ArciCollector`):

- UDP and TCP (octet-counted and LF framing) on 514, optional TLS on 6514; large receive buffers.
- Allow-list of sender IPs → source names; unknown senders go to `unknown-<ip>` and raise an alert.
  In production the Windows firewall (spec 13, `Set-ArciFirewall.ps1`) admits UDP/TCP 514 only
  from the `[[sources]]` addresses, so an unknown sender is dropped by Windows before the
  collector sees it; the `unknown-<ip>` path covers only senders the firewall admits (e.g. on a
  server installed with `-SkipFirewall`). DEVICE-SETUP §4 says how to check every expected
  source.
- Each message is appended, before anything else, to
  `spool\<source>\<YYYY-MM-DD>.log` (receive day in Europe/Istanbul) as
  `<receive time RFC 3339 µs+03:00> <sender IP> <original message>\n`.
  Bytes below 0x20, DEL and `#` itself are written as `#` + three octal digits; every other byte
  is written unchanged. The escaping is **reversible** (`arcilogger_core.rawlog.unescape`
  restores the exact received bytes, tested with random binary input), and a line is always
  exactly one message.
- The collector switches files at local midnight itself — no external rotation, no HUP.
- **The spool file is the durable queue.** Ingest tails it with a persisted byte offset; if the
  database is down, ingest stops advancing and catches up later. No second queue, no loss.

## Consequences

- One codebase and one test suite for receive → raw file → seal, all testable offline.
- Throughput, measured on 2026-10-12 in the development sandbox (one Python process, UDP,
  100,000 FortiGate-sized messages): **no loss at 10,000 and 20,000 msg/s**; 3.5 % loss at
  30,000 msg/s and about 40 % at 50,000 msg/s. The specification's sustained target (10,000 msg/s)
  is met; its 50,000 msg/s burst target is **not met over UDP**. Expected real volume is
  12–60 msg/s on average. To be re-measured on the real server in M2 with the load generator.
  If real bursts ever approach the limit: TCP (no loss, the sender slows down), several
  receiver processes, or a rewrite of this small service in a compiled language — the file
  format stays the same.
- UDP has no delivery guarantee on any platform; TCP (FortiGate `mode reliable`, MikroTik
  `remote-protocol=tcp` where supported) is recommended in DEVICE-SETUP.
- UDP has no sender authentication: a host in the LAN can forge a UDP datagram with a configured
  device's address, and the collector would store it as that device's raw log. Mitigation:
  `[[sources]] transport = "tcp"` once the device sends over TCP 514 (forged UDP is then
  dropped, counted as `dropped_transport` and alerted once as `wrong-transport`) plus
  anti-spoofing filters on the core router (DEVICE-SETUP). The seal proves what the server received,
  not which device sent it.
- One line stays exactly one message after a crash, a power cut or a full disk: a write is
  repeated until the whole line is written; a line cut off by an error, or a partial last line
  found when a file is (re)opened, is terminated by **appending** `\n` before the next message
  (existing bytes are never changed or removed). The fragment remains one damaged line (a parse
  failure); the event raises `R20-spool-torn`, failed writes are counted per source
  (`write_failures`) and raise `R20-spool` once when a source starts failing and once when it
  recovers (its first good write a minute after its last failure, so a partly failing disk is
  one incident, not an alert per message), all in `collector-status.json`
  (`spool_write_error` stays set while any source fails; `spool_write_errors` per source).
