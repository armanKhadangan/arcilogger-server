# Settings: changing the server's configuration from the browser

> Türkçe: [AYARLAR.md](AYARLAR.md). Decision: [adr/0014-browser-administration.md](adr/0014-browser-administration.md).
> Installation: [INSTALL.md](INSTALL.md); security details: [SECURITY.md](SECURITY.md) section 14.
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek için
> tasarlanmıştır"); it makes no claim of legal compliance.

This document describes how the **Settings** pages of the web interface change the server's
configuration (`arcilogger.toml`, the secret store, the Kamu SM root certificates, the Zamane
client, the firewall, Windows Time), what deliberately stays on the server console, and what to
do when something goes wrong. Installation stays on the console; day-to-day changes need no
Remote Desktop session.

## 1. What changes from the browser?

The **Settings** pages are open to the **Administrator** role only (`settings.manage`; **Sources**
also needs `sources.manage`). **Seal now** and **Query credit** on the **Sealing** page need
`seal.manage`. Each page changes only the values below; every other field is shown with a lock
("only the server console can change this value, for security reasons", section 5). The API
endpoints the pages use are in section 10.

| Page (address) | Changed from the browser | Deliberately console only |
|---|---|---|
| **General** (`/settings/general`) | organisation name (`site_name`), local and fallback time source (`[clock] ntp_server`, `[clock] fallback_server`), clock offset alert threshold (`[clock] alert_offset_ms`); the last measured offset is shown, a new time server is tried before it is saved | `[clock] ntp_port` |
| **Application settings** (`/settings/application`) | the database settings by group (alerts and e-mail, 5651 lookup and lease timeline, search and live tail, usage analytics with the work hours and holidays, usage reports, evidence packages, sessions, passwords and privacy, database); in the 5651 lookup group the **Internet exits (NAT)** editor, under it the "Kayıtlarda görülen NAT adresleri" (NAT addresses seen in the logs) panel, and **Rebuild the leases** (at most 31 days) | `devices.oui_file`, `usage.psl_file`, which name files |
| **Zaman damgası** (Time stamping, `/settings/kamusm`) | a wizard: Java (one of those the administration service found on the server), Zamane client (upload), root certificates (add) and legal root fingerprints (installed roots only), customer number and password (tried with a credit query when saved), **Stamp test (uses 1 credit)**, **Switch to production** (and back to test), credit alert threshold, timeout and the stamp tests per day (`[tsa] tests_per_day`, 0–20, default 10; the server policy's `tsa_test_per_day` is the ceiling); four server choices: Kamu SM test, Kamu SM production, TÜRKTRUST (HTTPS) and TÜRKTRUST over plain HTTP (confirmed only by typing `HTTP`; [turktrust.md](turktrust.md)), the TÜRKTRUST username and password; the re-stamping and revocation-list settings (`[tsa] renew_before_days`, `[tsa] crl_urls`, `[tsa] crl_max_age_days`; [TIME-STAMPING.md](TIME-STAMPING.md) section 7); `[seal]` read-only | the mock or another provider, a free URL or port (another TÜRKTRUST URL path included, T1), the digest, removing a root or a fingerprint, a free `java` path, approving an unknown Zamane release (`approve-jar`, section 4), clearing the TÜRKTRUST account (`secret delete turktrust.username` / `turktrust.password`) |
| **Sources** (`/settings/sources`) | add a log source, edit it (the name is fixed), switch it on or off (`[[sources]] enabled`), remove it (typing its name); the table shows the last message and the firewall state; the source's SNMP part (`[sources.snmp]`: on/off, port, SNMPv3 user, protocols, WAN interfaces, capacity, read interval; the two keys `snmp.<source>.auth_key` and `snmp.<source>.priv_key` in the secret store), the connection is tried when saving, and the refusal of a wrong interface name lists the device's interfaces ([LIVE-STATUS.md](LIVE-STATUS.md) section 3) | the whole `[collector]` section (bind address, ports, TLS, `unknown_senders`) |
| **E-mail** (`/settings/email`) | `[smtp]` (on/off, server, port, security, user, sender, timeout), the SMTP password, a CA file for the server's certificate (upload, or back to the Windows certificate store), the alert e-mail settings (`alerts.email_*`: recipients, lowest severity, hourly limit …); a trial e-mail before saving; **Send a test e-mail** (with the saved settings, at most 5 an hour); the **Provider** preset (section 8.1) | — |
| **Replica** (`/settings/replica`) | on/off, target (`\\SERVER\share` only), user, password; the connection is tried when saving; **Test the connection** (the saved target, at most once a minute) | a local folder as target |
| **Retention** (`/settings/retention`) | deleting expired days on/off, the retention period (at least 730 days); the number of days it deletes is shown first and typed to confirm. Only in `arcilogger.toml`: `[retention] batch_days` (default 30, 1–180), the days the oldest expired day waits before every expired day is deleted under one stamped record, so expired days go at the first run that finds the batch due, not always the next one | — |
| **Backup** (`/settings/backup`) | on/off, `[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly`, `[backup] min_free_gb`, `[backup] mirror_target` (the NAS copy of the backups: a share of its own on the replica's NAS, with the replica's user; tried when saved; [BACKUP.md](BACKUP.md) section 4.1); the list of the dumps on the server (name, time, size, SHA-256) and the NAS copy's last result; a failed backup is an `R20-backup` alert, a failed copy an `R20-replica` alert | `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore` |
| **Change history** (`/settings/history`) | the requests, their states and changes, a link to the audit log; **Cancel**, **Revert**; the configuration versions; **Download the diagnostics package** (a ZIP for support, no secrets) | restoring a version (`versions restore`, the page shows the command) |
| **First-run wizard** (`/settings/setup`) | changes no value itself: it shows the steps after the installation in order and opens one of the pages above in each step (below) | — |

No page changes `data_dir`, `secrets_dir`, `[database]`, `[api]`, `[ingest]`, `[worker]`,
`[seal]`, the database password or the usage pseudonym key. They belong to the installer and the
administrator.

**ARP times** (Application settings, 5651 lookup and lease timeline): "Ignore ARP after a release"
(`arp.ignore_after_release_seconds`, 300 seconds) cannot be longer than the "ARP log interval"
(`arp.interval_seconds`); the browser and the console (`arcilogger-db settings set`) both refuse a
change that breaks this. When you lower the interval below 300 seconds, change both settings in
one save. A time written outside this rule (by hand in SQL) is read by the lease job as the
default (300 seconds, at most the interval), with a warning in the worker log.

**The days a lookup reads** (same group): the days a 5651 lookup reads (reloaded from the archive
when the database no longer has them) grow with the longest session, the DNS look-back and the
longest lease timeout; one reload holds at most 14 days. These settings therefore have upper
bounds, so that every lookup's days fit one reload:

| Setting | Default | Bounds |
|---|---|---|
| `lookup.max_session_seconds` | 86400 (1 day) | 60–172800 (2 days) |
| `lookup.dns_lookback_seconds` | 3600 | 0–604800 (7 days) |
| `lease.default_seconds` | 86400 (1 day) | 60–604800 (7 days, FortiOS's default `lease-time`, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4) |
| `lease.server_seconds` | `{}` | each server 60–604800 (7 days) |
| `lease.timeout_percent` | 110 | 100–125 |
| `arp.interval_seconds` | 3600 | 60–604800 (7 days) |

The browser and the console both refuse a value of these settings above its bound (for example
"Must be between 60 and 604800."); a server time above 604800 seconds in `lease.server_seconds` is
refused by the API when it is saved ("Each DHCP server needs a time between 60 and 604800 seconds
(7 days)."). For a server whose lease time is longer than 7 days, at most 7 days can be entered;
ARCILOGGER then closes that server's leases 7 days × `lease.timeout_percent` after their last
evidence. The logged lease time of FortiGate leases (`dhcp.fortigate_use_logged_lease`) is not
bound by it. MikroTik's own default lease time is not stated in these documents (**sahada
doğrula**, verify on site): the bounds lie above ARCILOGGER's defaults (a 1-day lease, ARP-LOG
every hour). The 2-day bound of `lookup.max_session_seconds` is to be verified on site against
the FortiGate's session times as well.

A value stored before these bounds is read as it is: while a lookup's days are in the database
the lookup answers, and when the days it needs back from the archive fit one reload (14 days) it
asks for them with 409 `archive_reload_required`. A lookup that needs more than 14 days back is
refused with 503 `lookup_settings_invalid`, whose text names the setting above its bound and the
days: bring the value within the bounds.

**When is an archive reload needed?** The database keeps the last 30 days (`db.retention_days`),
and with the default settings a lookup also reads up to 8 days before the moment asked for (the
longest lease timeout: 7 days × `lease.timeout_percent`). So for a moment older than about
**22 days** a 5651 lookup first asks for those days to be reloaded from the archive (409
`archive_reload_required`; the lookup page shows the archive card, and once the reload is ready
the same lookup runs again, [CORRELATION.md](CORRELATION.md)).
Reloaded days are dropped again `db.reload_keep_days` (7) days later.

The **first-run wizard** ("İlk kurulum sihirbazı", `/settings/setup`) shows what is left to do after
the installation as a checklist: Administrator and two-step verification, Site settings, Log
sources, Kamu SM: test server, Kamu SM: production, Replica, E-mail and Summary. It opens from the
**First-run wizard** card on the Settings overview and from the **Open the first-run wizard** link
on the Uyum Durumu page; it does not open by itself after the first sign-in. The wizard has no form
of its own and stores nothing: each step's state ("OK" / "Missing") is derived again on every
visit from the server's configuration and the stamp tests in the request history; the Log sources
step also shows whether data arrived from each configured source (this does not change the step's
state). **Open in Settings** opens the page concerned, where the change is saved and checked as
always; **Skip this step for now** goes on to the next step, and the skipped step stays "Missing".
The e-mail step is "OK" only while sending e-mail is switched on and an SMTP server and a sender are
set. On a page opened from the wizard the way back reads **Back to the first-run wizard** ("Kurulum
sihirbazına dön") instead of **Settings** and leads to `/settings/setup` (also between the steps of
the Kamu SM wizard); this is remembered in that browser tab only and ends back in the wizard or on
the Settings overview. The steps and when each counts as "OK":
[COMPLIANCE-STATUS.md](COMPLIANCE-STATUS.md) section 5.

**Switching a source off** (`enabled = false`): the firewall removes the sender's 514 opening;
the silent-source alert (R15) and the sealer's gap check no longer expect it. A message that
still arrives from it is **recorded** (raw logs are never dropped). A switched-off source still
counts for the licence's source number. A **removed** source closes the same way; its row stays in
the database for the history, switched off and no longer expected, so R15 and the dashboard stop
waiting for it.

**Secret values** (the Kamu SM customer number and password, the TÜRKTRUST username and password,
the SNMPv3 keys, the SMTP and replica passwords)
never reach the screen: the fields show "Stored ✓" and the time of the last change; **Change**
writes a new one. The password is asked again (within the last 5 minutes) for the Kamu SM settings
(except the credit threshold, the timeout and the stamp tests per day; the customer number and password included), every
SMTP, replica, retention and backup change, the clock sources and alert threshold, switching a
log source off or removing it; the application settings of sessions and passwords (`auth.*`),
privacy (`privacy.*`), the retention periods and the pseudonyms of the anonymous role, the alert
e-mail recipients and lowest severity; cancelling a held change, **Revert** and the diagnostics
package ([SECURITY.md](SECURITY.md) section 7).

The **Search and live tail** group (`search.*`, `live.*`; [ADR-0015](adr/0015-search-live-sources.md))
holds the limits of the Arama and Canlı Akış pages: every request has a time budget and every
user a share, so that no search or tail competes with loading the logs (ingest). The values are
starting values, not measured facts; no value can switch a limit or a budget off. Every request
reads them again (a live tail when it opens):

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `search.max_days` | 31 | 1–92 | longest range of one search (days, device time) |
| `search.page_size_max` | 500 | 50–1000 | most rows of one request (the web interface asks for 100) |
| `search.request_budget_ms` | 4000 | 500–30000 | time for rows; then what was found so far is shown with the interval scanned |
| `search.stats_budget_ms` | 8000 | 1000–60000 | time for the chart and the facets |
| `search.facet_sample_rows` | 100000 | 1000–1000000 | most matches for facets counted directly |
| `search.max_concurrent` | 3 | 1–10 | search requests running at once; at most `[database] pool_size` minus 2 |
| `search.runs_per_minute` | 30 | 1–600 | new searches per user and minute |
| `search.export_max_rows` | 100000 | 1–1000000 | most rows of one export file |
| `search.export_max_per_hour` | 10 | 1–100 | exports per user and hour |
| `search.export_budget_seconds` | 120 | 10–900 | time to build one export file |
| `search.saved_max_per_user` | 100 | 1–1000 | saved searches per user (personal) |
| `live.max_streams_total` | 4 | 1–20 | live tails open on the server at a time |
| `live.max_streams_per_user` | 2 | 1–10 | live tails open per user |
| `live.max_lines_per_second` | 100 | 10–500 | lines a second shown per tail; the rest are counted and reported |
| `live.parse_lines_per_second` | 1000 | 50–10000 | lines a second parsed for the live tails |
| `live.buffer_lines` | 5000 | 500–50000 | newest lines the server keeps for a resume |
| `live.max_minutes` | 60 | 5–480 | duration of one tail (unattended screen); Resume starts it again |
| `live.backfill_lines` | 50 | 0–200 | recent lines shown at once when a tail opens |

The **5651 lookup and lease timeline** group also holds, in this version
([adr/0016-fortigate-dhcp-and-internet-exits.md](adr/0016-fortigate-dhcp-and-internet-exits.md)),
the settings for the internet exits, srcmac and the FortiGate DHCP records. The values are
entered on site or verified there, never assumed; what to enter when is in
[FIRST-STEPS.md](FIRST-STEPS.md) section 6 and [DEVICE-SETUP.md](DEVICE-SETUP.md) sections
2.4–2.6. The old settings `nat.public_ips`, `nat.wan_transip` and `lookup.port_hint` are gone: the
upgrade converts them into the internet exits ([INSTALL.md](INSTALL.md) section 13).

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `nat.egresses` | `{}` | at most 8 exits; an exit name of lower-case letters, digits and hyphens (at most 32 characters, e.g. `hat-1`) | Internet exits (NAT): each line's public IP addresses and whether they may change, the FortiGate's NAT address on that line (transip), its interfaces (dstintf), the NAT type and port preservation; entered with the editor below. Empty: one double-NAT exit is assumed |
| `nat.snat_trandisp` | `["snat"]` | 1–8 values, each starting with a lower-case letter (at most 16 characters) | the FortiGate `trandisp` values a candidate search takes for addresses no exit claims; only `snat` was seen in logs, verify on site (the panel flags other values) |
| `lookup.srcmac_interfaces` | `[]` | an interface name of 1–35 characters | the FortiGate interfaces (`srcintf`) whose srcmac is used: for a session without a lease, the MAC address, the device and the person are shown from this field with low confidence, as partial evidence; no lease is ever created. Only interfaces checked on site with two clients ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5). Empty: srcmac is not used |
| `lookup.srcmac_max_ips` | 1 | 1–16 | a srcmac seen with more distinct IPv4 addresses within ±1 hour of the requested moment counts as a router's or switch's address and is not used (IPv6 addresses are not counted) |
| `dhcp.fortigate_logids` | `{"0100026001": "assigned", "0100026002": "deassigned"}` | at most 16 entries; a logid of 10 digits, meaning `assigned` or `deassigned` | the FortiGate DHCP events that become lease evidence; the defaults come from the FortiOS log reference (the Release logid derived from its numbering rule), verify them on site. Empty: FortiGate DHCP records are not used |
| `dhcp.fortigate_dhcp_msgs` | `{"0100026001": ["Ack"]}` | 1–8 values per logid, each of 1–32 characters | per logid, the `dhcp_msg` values that count as lease evidence (case-insensitive); a record with another value is not used and raises an alert; a logid without an entry is not restricted |
| `dhcp.fortigate_use_logged_lease` | on | — | the lease time in the DHCP Ack record is the lease's time; values below 300 seconds, above 100 days or 0 (unlimited) use `lease.server_seconds` / `lease.default_seconds` (with the `lease.timeout_percent` margin) |
| `dhcp.fortigate_traffic_extends` | off | — | when on, traffic extends a FortiGate lease; switch it on only when it is verified on site that renewals are not logged and the organisation accepts the wrong-person risk (while on, the help text shows as a warning) |
| `dhcp.fortigate_interfaces_excluded` | `[]` | an interface name of 1–35 characters | FortiGate interfaces whose DHCP records are no lease evidence: the interfaces of DHCP-over-IPsec servers (`server-type ipsec`) |
| `dhcp.fortigate_format_verified` | off | — | switched on once the record format has been verified on site as [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4 says; reports name who marked it and when; after every FortiOS upgrade switched off and the check repeated |

In the `lease.server_seconds` editor the server's name is the DHCP server's name on a MikroTik
(e.g. `dhcp61`) and the DHCP interface's name on a FortiGate.

**Rebuild the leases after a settings change.** After changing any `dhcp.fortigate_*` setting
(except `dhcp.fortigate_format_verified`, which only changes the lookup and report texts) or a
FortiGate source (**Sources**), rebuild the leases from the first affected day to today with
**Rebuild the leases** in the same group (at most 31 days per request; several requests for a longer
range): the change applies to past days only after the rebuild. After a **change that adds
evidence** (a logid or `dhcp_msg` value mapped, a FortiGate source added), a 5651 lookup for moments
of those days may answer without a chain until then (the safe side). After a **change that removes
or narrows evidence** (an interface added to `dhcp.fortigate_interfaces_excluded`,
`dhcp.fortigate_traffic_extends` or `dhcp.fortigate_use_logged_lease` switched off, a logid or
`dhcp_msg` value removed, a FortiGate source removed or retyped), the leases built under the old
settings stay, at their old confidence, until the rebuild, so a moment can still name the earlier
device. **Rebuild at once**, from the first affected day. For a network added or changed later:
[FIRST-STEPS.md](FIRST-STEPS.md) section 5.

**The Internet exits (NAT) editor** (`nat.egresses`). Each exit is a row: name, label, type,
public addresses, NAT addresses, interfaces (dstintf) and port preservation. The types are
"Direct (public IP on the FortiGate)", "ISP NAT (double NAT)", "Does not pass the FortiGate" and
"Not an internet exit (NAT towards a DMZ, VLAN, VPN or branch)"; public addresses are not entered
for the last, NAT addresses only for ISP NAT and the last, and port preservation ("Unknown",
"Kept (verified on site)", "Not kept") and the check box "Public IP may change (dynamic)" only for
ISP NAT. A public address cannot be a private (RFC 1918) or shared (RFC 6598, e.g. 100.64.x.x)
address: the FortiGate's address behind the ISP router is the NAT address of an ISP NAT exit. The
editor shows the server's rules next to the field before saving and warns about: an ISP NAT exit
without a public address or marked as changing (it is also searched for public IPs no exit
claims), a line without interfaces when there are two or more lines (without them a lookup cannot
tell by which line a session left), an ISP NAT exit whose NAT address is a public address (if the
FortiGate holds it itself, the line is direct), and exits converted at the upgrade without
interfaces ([INSTALL.md](INSTALL.md) section 13). The server has the last word: when it refuses a
value, its code and text show above the editor. The change is written to the audit log as
`settings.change` and applies to the next lookups at once. The console equivalent:
[FIRST-STEPS.md](FIRST-STEPS.md) section 6.

**The "Kayıtlarda görülen NAT adresleri (son 24 saat)" (NAT addresses seen in the logs, last 24
hours) panel** is under the editor, so that the values are read from the records instead of
guessed (counts and the FortiGate's own addresses only; not audited; the counts are kept 5
minutes, which exit claims an address is computed from the current settings every time):

- **NAT addresses:** the FortiGate's source-NAT address (`transip`), the outgoing interface
  (`dstintf`), the NAT type (`trandisp`), the number of sessions, first and last seen, and the
  exit that claims the address. For an address no exit claims, **Add as exit** prefills a row: a
  public address becomes a Direct exit, a private or shared address an ISP NAT exit with an empty
  public address (you enter the ISP's public address); beside it, **Add as not an internet exit**
  is for the address of the FortiGate's NAT towards a DMZ, another VLAN, a VPN or a branch (no
  session is searched on such addresses). An unknown `trandisp` value is flagged. When the stored
  exit definition cannot be read, the panel says so and the exit column stays empty (`R20-nat`).
- **Interfaces with srcmac (last hour):** per interface (`srcintf`) the number of records with a
  srcmac ("Records"), the number of distinct MACs ("Distinct MACs") and the most IP addresses seen
  with one MAC ("Most IPs per MAC", `max_ips_per_mac`); all three count only records with an IPv4
  source address (a dual-stack client's IPv6 addresses are not counted), and whether the interface
  is in `lookup.srcmac_interfaces` ("srcmac in use"). **Use for srcmac** is disabled on interfaces
  whose `max_ips_per_mac` is above `lookup.srcmac_max_ips`. Do the two-client check before you add
  an interface ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5).

The three `alerts.filter_*` settings of the **Alerts and e-mail** group say when content filter
records are expected; they are the thresholds of card 7 of the Uyum Durumu page and of the
`R20-filter` alert ("No content filter records", severity high). While an enabled FortiGate source
exists, if neither a web nor a DNS filter record has arrived for the last
`alerts.filter_quiet_hours` hours and every day the window touches is an expected day, card 7 shows
**Problem** and the alert opens; if the window touches a day that is not expected, card 7 shows
**Attention** without an alert. Either kind of record is enough: when only one of them falls silent
for that time, card 7 shows **Attention** too. The alert closes by itself when a new record arrives.
A day is expected when its ISO weekday is in `alerts.filter_days` and (while
`alerts.filter_skip_holidays` is on) it is not in the holiday list of the **Usage analytics** group
(`usage.holidays`). The defaults are the owner's decision (2026-10-28): every day of the week except
the listed holidays, 24 hours. `usage.holidays` ships only the fixed-date national holidays; the
administrator enters the dates of the religious holidays every year. Details:
[COMPLIANCE-STATUS.md](COMPLIANCE-STATUS.md) section 2.7, [ALERTS.md](ALERTS.md) section 2.

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `alerts.filter_days` | `[1,2,3,4,5,6,7]` (every day) | each 1–7 (1 = Monday, 7 = Sunday), stored sorted without duplicates; may be empty (no day expected) | the ISO weekdays on which content filter records are expected; one day number per line on the page |
| `alerts.filter_skip_holidays` | on | — | on: no record is expected on the days of `usage.holidays`; off: public holidays count as expected days |
| `alerts.filter_quiet_hours` | 24 | 6–168 | the longest time without a record on expected days (hours) |

The **Database** group says how long the parsed records stay in the database. The raw records in
the sealed vault are not affected. The worker job `db.window` drops the days older than the
window every night after 01:00; a day not sealed yet and the days of an active archive reload are
never dropped. When an older day is needed, the 5651 lookup and Search ask for it with "arşivden
yeniden yükle" (reload from the archive). Both settings are retention periods of personal data:
a change needs the password again and is audited as `settings.change`. Details:
[DATA-MODEL.md](DATA-MODEL.md) "Retention",
[legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md).

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `db.retention_days` | 30 (400 in earlier versions; the upgrade changes a 400 to 30 and a value below 7 to 7, and keeps any other value) | at least 7 | the days of logs the database keeps; older days are dropped every night and reloaded from the sealed vault on request |
| `db.reload_keep_days` | 7 | 1–30 | how many days the days reloaded from the archive stay in the database after their reload finished; then they are dropped again. These days contain personal data |

## 2. How it works

1. The page first shows the change for review (the changes, their effects, warnings: a dry run
   of the API, nothing is queued). **Save** makes the API check the change with the same rules
   (field limits, permission, a re-entered password where needed) and drop it as a signed
   request into `C:\ProgramData\ARCILOGGER\admin\inbox`. The API **cannot write** the
   configuration file, the secret store or the certificate folders.
2. The **ArciAdmin** service (`NT SERVICE\ArciAdmin`, "ARCILOGGER Yönetim (Admin)") checks the
   request's signature, expiry and rules again; where needed it first tries the connection with
   the new values (a Kamu SM credit query, a trial e-mail, the replica share, the time server:
   "Testing the connection"), keeps the previous version (`admin\versions`), writes the change
   and follows the services taking it. The services take it within seconds without a restart
   (the collector and ingest the log sources, the API and the worker the sections they read);
   the scheduled tasks at their next run.
3. The firewall, Windows Time and activating a Zamane client are the work of the task
   `\ARCILOGGER\ApplySystem`, which runs as **SYSTEM**. It takes no data from a request: it derives
   everything itself from the validated `arcilogger.toml` and from `admin\policy.json`, which only
   administrators can write, and checks every value again; ArciAdmin only wakes it (event 4101),
   and it also runs every 5 minutes on its own. In the firewall only the `ARCILOGGER Syslog UDP/TCP`
   rules (in strict outbound mode the `ARCILOGGER Out …` rules) are updated in place; no other
   rule, no profile, nor 443, 3389 or 5985 changes. For a new source, 514 opens once the collector
   knows it; for a source switched off or removed, it closes at once. The task sets the Windows
   Time peer list from `[clock]` at its first run too, even after an installation with
   `-SkipTimeSetup`; on a server whose time is managed another way (a domain, for example), switch
   the browser administration off with `-DisableBrowserAdministration` (section 6).
4. **Seal now** (Sealing page) and **Query credit** take the same channel: ArciAdmin writes a
   signed task request, and the task `\ARCILOGGER\Seal` (event 4110) or `\ARCILOGGER\Requests`
   (event 4111, LOCAL SERVICE) does the work. Sealing stays in its own task as always; the browser
   only brings the oldest unsealed day forward.
5. Every applied change goes into the audit log (`admin.request`, `admin.applied`, …; firewall,
   time and client changes as `admin.system`). Values are masked; a secret value, or a hash of
   one, is never written.

## 3. Status chip and request states

The chip in the Settings header gives ArciAdmin's state: **Administration service: running /
late / not running / switched off** (last answer younger than 30 seconds / younger than 2 minutes
/ older or none / browser administration switched off). While the service is not running, or the
browser administration is switched off (section 6), the machine settings are read-only and the
page names the console path; the application settings (database), the test e-mail and the lease
rebuild (worker) always work. **Seal now** and **Query credit** on the Sealing page go through
ArciAdmin as well and are unavailable then: the console path is the `\ARCILOGGER\Seal` or
`\ARCILOGGER\Credit` task in Task Scheduler (the nightly runs go on as always).

A request's state is shown on the page. The important ones:

| State | Meaning | What to do |
|---|---|---|
| **Written, services loading** | the file is written, the services are taking the new setting | wait (at most 2 minutes) |
| **Applied** | every service runs with the new setting | — ; with the warning "the administration service can no longer write the configuration files" (`acl_drift`) the change is in force, but the next one fails until you run `Set-ArciVaultAcl.ps1` (section 8) |
| **Done** | an action (sealing, credit query, a test) has finished; its result is in the request | — |
| **Held** | a guarded change, applied when the countdown ends (section 4); a Zamane client that is not a known release is never applied on its own: the page shows from when it can be approved on the console (`approve-jar`) | **Cancel** if needed |
| **Partly applied** | a service did not take the setting within 2 minutes, or the firewall could not be updated | the services and the **Security** page; ApplySystem tries again at its next run |
| **Connection test failed, nothing changed** | the server could not be reached with the new values | read the reason on the page, correct the value |
| **Not applied, the previous settings are in use** | a step failed, ArciAdmin put the previous version back | read the reason on the page, fix and save again; **Revert** for a change that was applied |
| **Manual action needed** | a step failed and putting the previous version back failed too (`rollback_failed`) | the steps on the page (section 8); note the version number |
| **Conflict: changed elsewhere** | the file changed meanwhile, by hand or by another request | reload the page and save again |
| **Rejected** | ArciAdmin did not accept the request (signature, expiry, a rule) | read the reason; a signature or installation error is an `R20-admin` alert |
| **Expired, nothing changed** | ArciAdmin did not take the request in time | check the service (section 8) |
| **Timed out** | no task (Seal, Requests) took the request within 15 minutes; it was withdrawn, nothing ran (a request the task took is waited for while the task may run it, e.g. a long Seal run) | the task's last result (Task Scheduler, `\ARCILOGGER\`) |

The short request number on the page is for support.

## 4. Held changes and the Zamane client

Most changes apply at once. These are **guarded**: they ask for a typed confirmation and raise an
`R20-config-change` alert (by e-mail when it is set up) and a Windows Event Log entry (event 4104):

| Change | Hold |
|---|---|
| switching retention on, or lowering its days | `hold_hours` of `admin\policy.json` (default 0: at once) |
| switching an enabled replica off, or changing its target | `hold_hours` (default 0) |
| a Zamane client that is not a known release | `approve-jar` on the console, at the earliest after `jar_hold_hours` (default 24) |

The holds are chosen at installation with `-AdminChangeHoldHours` and `-ZamaneHoldHours`
(section 6). A held change is stopped with **Cancel** on the **Change history** page or on its own
page.
First-time setup is never held (switching the replica on the first time, test → production).

**Zamane client:** a file uploaded in the Kamu SM wizard first waits in `zamane\web` and **never**
runs from there. If it is a known release, ApplySystem copies it into `zamane\approved`
(event 4107). An unknown release is only recorded (event 4104); an administrator approves it on
the console after comparing its SHA-256 with the value on the Kamu SM page. Time alone never makes
a client runnable. This version knows no Zamane release, so a client uploaded in the browser
always waits for `approve-jar`, at the earliest `jar_hold_hours` after the upload; meanwhile the
other steps of the wizard (Java, roots, credentials, the threshold) can be saved, and a second
client only once the first is approved or cancelled. To avoid the wait, install the client with
the installer's `-ZamaneJar` ([INSTALL.md](INSTALL.md) section 7). The command:

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
& $py -I -m arcilogger_core.admin approve-jar <SHA256> --config $cfg
```

## 5. What stays on the server console

These are deliberately not offered in the browser; each touches the evidence path or the server's
security directly:

- installation, upgrade, rollback, removal; PostgreSQL; a database restore;
- `[database]`, `[api]`, `[collector]`, `[ingest]`, `[worker]`, `[seal]`, `data_dir`,
  `secrets_dir` (`data_dir` must equal the installer's record
  `C:\ProgramData\ARCILOGGER\data-root.txt`; otherwise the services and tasks refuse the
  configuration and the installer stops in its preflight; to move the data folder deliberately,
  run the installer with `-DataRoot`);
- a provider other than Zamane, a free URL, port, digest, credit pattern, verify flag,
  `roots_dir`, a free `java` path; removing a root or a legal fingerprint;
- `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore`; the two database settings that name
  files, `devices.oui_file` and `usage.psl_file`;
- `admin\policy.json` (source networks, holds, trusted program folders, the off switch);
- the firewall's administrator addresses, strict outbound mode, WinRM and kept rules
  (`Set-ArciFirewall.ps1`);
- the database password, the usage pseudonym key, renewing `secret.key` and the administration
  keys; `approve-jar`; `setup-token` / `create-admin`;
- restarting a service after a hand edit of a key that is not reloaded live (section 7).

## 6. The installer's limits (`admin\policy.json`)

The installer writes the limits of the browser administration into
`C:\ProgramData\ARCILOGGER\admin\policy.json`, which only administrators and SYSTEM can change.
The values are chosen with installer parameters and remembered by later runs
([INSTALL.md](INSTALL.md) section 7.1):

| Parameter | Default | Meaning |
|---|---|---|
| `-SourceNetwork` | `10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16` | a log source's address must lie in one of these networks (at widest a /16) |
| `-AdminChangeHoldHours` | 0 | the hold of retention and replica changes (0–168 hours) |
| `-ZamaneHoldHours` | 24 | how long before an unknown Zamane client can be approved (1–168 hours) |
| `-DisableBrowserAdministration` | off | switches the browser administration off: ArciAdmin refuses every request, ApplySystem does nothing; the 5651 functions (collection, sealing, verification) are not affected at all. **Seal now** and **Query credit** are unavailable then (start the `\ARCILOGGER\Seal` or `\ARCILOGGER\Credit` task in Task Scheduler), and after a hand edit of `[[sources]]` run `Set-ArciFirewall.ps1` yourself (the **Sources** table then shows the firewall as **Not managed**) |

The installer compares every enabled log source of the configuration with these networks: when
one lies outside them it stops before any change and names the `-SourceNetwork` value to give
(otherwise ApplySystem would take that sender out of the 514 rule at its first run). Such a
source added by hand later stays open where the firewall has it open already and is not opened
otherwise; either way an `R20-admin` alert and the code `source_outside_policy` show it.

Running the installer again with `-Upgrade` and no parameter does not switch the browser
administration back on (the remembered choice applies); pass `-DisableBrowserAdministration:$false`.
The file can also be edited by hand: the installer keeps the hand-written values of a valid file;
an invalid file switches the browser administration off (the same meaning for every reader) and
the installer rewrites it with the defaults, with a warning.

The installer also records the programs the tasks run: a `java` outside Program Files, a
`[tsa] java` command with extra arguments, `pg_dump` / `pg_restore` (with their SHA-256) and the
Zamane client. The tasks run no other program; run the installer again when a recorded program
changes.

## 7. Hand edits and the browser together

**Use one way at a time.** `arcilogger.toml` can still be edited in Notepad; the browser notices
at the next save ("Conflict") and does not overwrite it. Hand edits are reloaded live too: the log
sources (collector, ingest) and the sections the API and the worker read need no restart. For the
sections that are not reloaded live (`[database]`, `[api]`, `[collector]`, `[ingest]`,
`[worker]`, `data_dir`, `secrets_dir`), restart the service concerned; the page shows "A change
waits for a restart". A hand edit is not listed on the **Change history** page ("The configuration
was changed by hand").
The first browser change of sources moves every `[[sources]]` table into a marked block; comment
lines inside those tables are lost then (the previous file stays in `admin\versions`).

**Secrets on the console.** `arcilogger-core secret set` / `secret delete` replace `secrets.json`
with a new file, which lacks ArciAdmin's write right. Afterwards run
`& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` in an administrator PowerShell (or
run the installer again); otherwise the next change made in the browser is applied with the
warning "the administration service can no longer write the configuration files" (`acl_drift`, a
critical `R20-admin` alert), and the change after it fails until the rights are repaired. The
installer does this itself after the values it stores (the database password, the Kamu SM
credentials at the end of the first installation).

## 8. Troubleshooting

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
Get-Service ArciAdmin                                    # running?
& $py -I -m arcilogger_core.admin status --config $cfg   # heartbeat, held changes, versions
& $py -I -m arcilogger_core.admin versions list --config $cfg
& $py -I -m arcilogger_core.admin recover --config $cfg  # finish an apply a crash interrupted
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'ARCILOGGER-Admin' } -MaxEvents 20
Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName ApplySystem   # bring the firewall in step now
```

- **The administration service is not running:** `Start-Service ArciAdmin`; its log is in
  `D:\ARCILOGGER\logs`. While it is stopped the 5651 functions go on; only the browser cannot
  change settings.
- **"Manual action needed"** (`rollback_failed`): put the previous version back on the console
  with the version number from the page (`& $py -I -m arcilogger_core.admin versions restore <N>
  --config $cfg`; the page shows the command with the full path); the services load the restored
  file by themselves. Restart a service that shows "A change waits for a restart".
- **For support:** **Download the diagnostics package** on the **Change history** page (with the
  password re-entered): the services' status files, the last log lines and the configuration,
  every secret replaced by `***`; no raw log, no vault, no export.
- **`acl_drift`:** the change was applied, but ArciAdmin can no longer write `arcilogger.toml` or
  `secrets.json` (most often after a console `secret set`, section 7): run
  `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` before the next change. Do not
  restore a version: that would undo the applied change and repair nothing.
- **The firewall could not be updated:** the last result of the ApplySystem task and event 4106;
  after an installation with `-SkipFirewall` the firewall is not ARCILOGGER's
  (`firewall_not_managed`) and its rules are kept by hand.
- Alerts: `R20-admin` ("Browser administration problem": a refused request, a stopped service, a
  failed sync, a rollback, manual action needed) and `R20-config-change` ("Significant
  configuration change": guarded changes) — [ALERTS.md](ALERTS.md) section 2.

Event IDs (source `ARCILOGGER-Admin`, Application log): 4100/4199 service started/stopping, 4101
system sync requested, 4102 applied, 4103 failed, 4104 held or a client recorded, 4105 cancelled,
4106 sync result, 4107 client promoted, 4110 seal request, 4111 task request, 4120 request refused,
4121 stored credential changed (its name only), 4130 the installer removed ARCILOGGER with its
data ([INSTALL.md](INSTALL.md) section 14.1). An event only wakes a task; the authority is the
signed request.


### 8.1 E-mail is not sent

The **Provider** list on **Settings → E-mail** fills the server, port and security and says what
that provider needs. The choice is not saved; it asks before changing a filled field. Every
option connects with a user name and password, or without a login (there is no OAuth).

| Provider | Server | Connection | Login |
|---|---|---|---|
| Google Workspace / Gmail | `smtp.gmail.com` | 587, STARTTLS | the full e-mail address and a 16-character **app password** (the account needs 2-Step Verification) |
| Google SMTP relay | `smtp-relay.gmail.com` | 587, STARTTLS | no login: the Google Admin console (Apps → Google Workspace → Gmail → Routing → SMTP relay service) allows the server's public IP address |
| Microsoft 365 / Outlook | `smtp.office365.com` | 587, STARTTLS (465 is not supported) | the mailbox's address and password; the administrator turns on SMTP AUTH for the mailbox, and the tenant's Security defaults must be off |
| Hosting company | the one they give | 587, STARTTLS (SSL/TLS if they say 465) | the user name and password they give |
| Company mail server | its name | 25, plain (no login) or STARTTLS (with a login) | what the server needs |

Since 1 May 2025 Google Workspace accounts no longer accept the account password for SMTP. Where
app passwords cannot be used, the SMTP relay is a way without a password.

When a trial or test e-mail is refused, the page shows **the mail server's answer** (never the
password). For known answers of Google and Microsoft 365 a **What to do** line comes with it:

| Answer | Meaning |
|---|---|
| Google `5.7.8` / `5.7.80`, `5.7.14` | the account password was refused: an app password or the relay |
| Google `5.7.9` / `5.7.90` "Application-specific password required" | 2-Step Verification is on: an app password is needed |
| Google `5.7.0` / `5.7.1` "relay" | the relay does not allow this IP address or sender domain |
| Microsoft `5.7.3`, `5.7.139` | sign-in refused: the password, SMTP AUTH on the mailbox, Security defaults |
| Microsoft `5.7.57` | sending without a login: enter the user name and password |
| Microsoft `5.7.60` | the sender is not the mailbox's address: "Send As" permission is needed |

Sources (read 2026-11-08): Google Workspace Admin Help "SMTP errors and codes", "Transition from
less secure apps to OAuth", "Route outgoing SMTP relay messages through Google"; Microsoft Learn
"How to set up a multifunction device or application to send email using Microsoft 365". The
providers may change this behaviour (**verify on site**).

## 9. Verify on site

Windows CI (the end-to-end job of the installer workflow, on a clean Windows server) tries:
ArciAdmin running as `NT SERVICE\ArciAdmin` and writing the Application log, the rights of 2.2
(the API cannot write the configuration, the secret store, `zamane` or the vault; ArciAdmin's own
entry stays on `arcilogger.toml` after it replaced the file), a site name change through Caddy
reaching "Applied", a source added in the browser reaching both syslog rules within 2 minutes,
**Seal now** making the Seal task (LOCAL SERVICE) seal a day, **Query credit** running in the
Requests task, and sealing going on after a rollback to a version without ArciAdmin. On site
also:

- that ArciAdmin can add and delete only its own files in `kamusm-roots`;
- that the event triggers wake the tasks without waiting for their 5-minute run (Seal, Requests,
  ApplySystem);
- that no syslog message is lost while the firewall rule is updated in place (with real devices);
- that a credit query costs no credit on the Kamu SM test server, and the customer number format;
- the format of the Zamane distribution (jar or zip), and that Java is found under
  `NT SERVICE\ArciAdmin`.

## 10. The API endpoints the pages use

The web interface uses only the endpoints below (all under `/api/v1`, with the session and CSRF
protection; the formats in detail: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) section 16). Every
endpoint that changes a machine setting or starts an action answers `202` with the request's
number and its `status_url`; the state is followed with `GET /admin/requests/{id}`. Every error
carries `code`, `message_tr` and `message_en`.

| Page | Endpoints |
|---|---|
| Status chip, every page | `GET /admin/status`, `GET /admin/config` (values, `etag`s, locked fields) |
| General | `POST /admin/config/general/check`, `PUT /admin/config/general` |
| Application settings | `GET /settings`, `PATCH /settings`, `POST /settings/{key}/reset`; `GET /settings/fortigate/observed` (NAT addresses seen in the logs); `POST /admin/actions/lease-rebuild` |
| Kamu SM | `POST /admin/config/tsa/check`, `PUT /admin/config/tsa`; `POST /admin/uploads?kind=…` then `POST /admin/config/tsa/zamane`, `POST /admin/config/tsa/roots`, `POST /admin/config/tsa/legal-roots`; `POST /admin/actions/tsa-test` |
| Sources | `POST /admin/config/sources/check`, `POST /admin/config/sources`, `PUT /admin/config/sources/{name}`, `DELETE /admin/config/sources/{name}` |
| E-mail | `POST /admin/config/smtp/check`, `PUT /admin/config/smtp`, `POST /admin/config/smtp/ca`, `DELETE /admin/config/smtp/ca`; `POST /admin/actions/test-email`; the alert e-mail settings `PATCH /settings` |
| Replica | `POST /admin/config/replica/check`, `PUT /admin/config/replica`, `POST /admin/actions/replica-test` |
| Retention | `POST /admin/config/retention/check`, `PUT /admin/config/retention` |
| Backup | `POST /admin/config/backup/check`, `PUT /admin/config/backup` |
| Change history | `GET /admin/requests`, `GET /admin/requests/{id}`, `DELETE /admin/requests/{id}` (Cancel), `POST /admin/requests/{id}/revert` (Revert), `GET /admin/config/versions`, `GET /admin/diagnostics` |
| Sealing | `POST /seals/{day}/seal` (Seal now), `GET /credits`, `POST /credits/query` (Query credit) |
| First-run wizard | reads only: `GET /admin/config`, `GET /admin/status`, `GET /admin/requests?kind=action.tsa_test` (the stamp tests), `GET /sources` (the log sources step) |

The application settings and the worker's actions (the test e-mail, the lease rebuild) go
through the database and do not wait for the administration service; everything else reaches
ArciAdmin as a signed request.
