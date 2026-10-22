# ADR-0001: Windows server, browser-only access

- Status: accepted (2026-10-12)
- Deciders: the product owner

## Context

The original build specification targeted a Debian 12 VM (rsyslog, systemd, chrony, nftables,
Docker Compose). On 2026-10-12 the owner asked for ARCILOGGER to **run on Windows**, as **a server
with no client software for users** — a web interface is enough.

## Decision

1. ARCILOGGER runs natively on **one Windows server** (Windows Server 2022/2025 recommended,
   Windows 11 Pro acceptable on a dedicated machine).
2. All people use it through a **browser only**. There is no desktop client and no agent on user
   or employee PCs; data comes from the network devices via syslog.
3. No Docker in production. Components run as Windows services (collector, ingest, API, worker,
   Caddy, PostgreSQL) and the evidence jobs run from **Task Scheduler**.
4. Python 3.12 is bundled with the installer; the web interface is shipped pre-built.
5. The code stays OS-neutral (pathlib, zoneinfo + tzdata, binary file I/O). CI runs the Python
   tests on Windows **and** Linux; only the Windows installer is supported for production.

## Consequences

- rsyslog is not available → own collector ([ADR-0002](0002-own-syslog-collector.md)).
- `chattr +i` is not available → [ADR-0003](0003-integrity-on-windows.md).
- Time sync uses the Windows Time service configured to the site's local time source (usually
  the core router; `[clock] ntp_server`, entered at installation with `-NtpServer` since
  2026-10-20), plus ARCILOGGER's own SNTP probe that measures and records the offset (the value
  written into each manifest).
- Firewalling uses Windows Defender Firewall rules created by the installer.
- The Zamane console client receives the Kamu SM password on its command line. On Windows,
  standard users cannot read another account's process command line, administrators can. The
  client runs under a dedicated service account; the residual risk is documented, and the native
  client (no command line) remains the preferred long-term option.
- A Windows Server licence is needed (or Windows 11 Pro on a dedicated box).
- Windows Update reboots must not lose logs: services restart automatically; UDP senders cannot
  retransmit, so the maintenance window should be outside working hours (documented in M7).
