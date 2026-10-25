# Usage reports — method, settings, rebuilds

> Türkçe: [KULLANIM.md](KULLANIM.md). Decision record: [adr/0011-usage-analytics.md](adr/0011-usage-analytics.md).
> API contract: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) section 12.
>
> **Licence:** usage analytics needs the **Usage analytics** module (Kullanım analizi, [LICENSING.md](LICENSING.md)); while it is off the Kullanım page, the dashboard's usage sections, the `/usage` endpoints and the `usage rebuild` command are locked; the 5651 functions are not affected.

ARCILOGGER **estimates** daily internet use per person, device, department and company from
connection logs: online time, active time, time per site and category, download/upload volume,
sessions, blocked attempts, first/last activity, an hourly heat map, applications and off-hours
use. **None of these numbers is exact**; the interface, the reports and the Excel files mark every
duration as estimated: *"Estimated value: computed from connection logs; tabs left open in the
background may be counted too."*

By default (owner decision of 2026-10-20) the reports are detailed and **carry names**
([ASSUMPTIONS.md](ASSUMPTIONS.md), "Owner 2026-10-20"); whether an organisation may process its
employees' usage this way (consent, information text) is that organisation's legal question.
Pseudonyms and leaving persons out are settings (section 6). ARCILOGGER is designed to support 5651 obligations; ask the legal
counsel about the rules for processing personal data
([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)).

## 1. Metrics

| Metric | Definition |
|---|---|
| **Online time** | 1-minute buckets in which the subject had at least one session open (background included) × 60 s |
| **Active time (estimated)** | Buckets with at least `usage.active_bytes_threshold` (2048 bytes) of non-background traffic × 60 s |
| **Time per site (estimated)** | The 60 s of each active bucket are split between the sites of that bucket by their non-background bytes: the site times add up to the active time. **Raw time**: 60 s for every bucket with any traffic of the site (not split; drill-down) |
| **Time per category** | Site times summed by FortiGuard category; sites without a category are **Bilinmiyor** |
| **Download / upload** | `rcvdbyte` / `sentbyte`: interim deltas plus the final remainder, never counted twice (background included) |
| **Sessions** | Distinct FortiGate sessions; counted once per subject, in the part (work / off hours) of its first minute; a session over midnight counts on both days |
| **Blocked attempts** | Every web filter `blocked`, DNS filter `block` and application control `block`/`reset` event, with its category; in a person's device breakdown every device counts all of its own attempts. A device with only blocked attempts that day is listed too (also on the report's Cihazlar sheet), with the IP and VLAN of those attempts |
| **First / last activity** | Start of the first / end of the last active minute |
| **Hourly heat map** | Day × hour, colour = active minutes of the hour (30 days ending on the selected day) |
| **Applications** | Time and bytes by FortiGate `app` / `appcat` |
| **Off-hours use** | Active time outside the work hours (and on holidays) |
| **URLs** | URLs the web filter logged (`fg_web.url`), the newest `usage.urls_per_day` (1000) per subject and day; with certificate inspection HTTPS mostly shows `/` only |

"Active users" counts the subjects (person, else device, else IP) with active time.

## 2. Method

### 2.1 Who? (the subject)

Traffic belongs to the person the device was assigned to **at that moment** (assignment history,
[adr/0008-device-assignment-history.md](adr/0008-device-assignment-history.md)); to the device
(the MAC of the DHCP/ARP lease) when it was assigned to nobody; to the bare IP address without a
lease. It is **the same code** as the 5651 lookup: IP + moment → lease → device → person then. A
device reassigned at noon gives the morning to the old and the afternoon to the new person (per
minute, by its first instant).

**Department:** the person's (current) department; for a person without one, or a device / IP
subject, the department of the VLAN with the most online minutes (the department linked to the
VLAN, else the VLAN's name; a subject with only blocked attempts or URLs that day takes the VLAN
of those events); none → **Bilinmiyor**. No department history is kept.

### 2.2 Which site? (in this order)

1. the web filter host name with the same `sessionid` (and source IP), `fg_web.hostname`;
2. the application control / SSL host name with the same `sessionid` (`fg_app.hostname`, SNI);
3. the **newest DNS answer** of the same source IP containing the destination IP, at most
   `usage.dns_window_seconds` (600 s) before the session started (`fg_dns`);
4. the FortiGuard service name (`dstinetsvc`, e.g. `Google-Gmail`), shown as a "service";
5. the destination IP address alone. (Spec 10.2's "reverse DNS" is not done: the server sends no
   DNS queries of its own, U3.)

A long session over midnight keeps its name on the next day: steps 1-3 look back to the session's
start, also when it started before the logs read for the day (from
`usage.session_overlap_seconds` before midnight); the background rules stay the same too.

Host names are reduced to the **registrable domain** (`www.youtube.com` → `youtube.com`,
`news.bbc.co.uk` → `bbc.co.uk`); the full name stays in the drill-down. The **Public Suffix List**
comes inside the `publicsuffixlist` package and is **never downloaded**. A newer list can be
placed on the server and named in `usage.psl_file` (missing or unreadable: the bundled list, the
job details say why). Only the ICANN section counts by default (`usage.psl_private_suffixes`):
otherwise a CDN such as `cloudfront.net` would split into thousands of "sites".

**Category:** the web filter's `catdesc` cached per domain (`domain_categories`, the most frequent
category of the newest day); without a web filter category the DNS filter's; else **Bilinmiyor**
(shown as "Unknown" in the English interface; its name in the API and the address is `Bilinmiyor`
in every language).

### 2.3 Spreading bytes over minutes

A session's logs are read in time order. An **interim log** (`0000000020`, with
`sentdelta`/`rcvddelta`) adds its deltas over `[time − durationdelta, time]`; every other log (the
final log) adds what its cumulative counters exceed the increments so far (**remainder**) from the
previous log to its own time. Interim and final logs are never counted twice; the bytes of a lost
interim log arrive with the final log. When the first log read is an interim log, what its
cumulative counters hold beyond its deltas (the bytes before its interval) is added from the
session start to that interval: the bytes of a lost first interim log count too, and the part of
a long session before midnight stays with the earlier day. Each increment is spread **evenly**
over its interval; the part inside the day counts for the day. A session is open (online) over
`[earliest (time − duration), last log]`.

A day is 00:00-24:00 Istanbul time. Logs arriving within `usage.session_overlap_seconds` (30 min)
after the end of a day still count for it (sessions over midnight).

### 2.4 Background traffic

Background traffic counts for **online time and volume**, **not for active time** (spec 10.3):

- sessions whose host name matches a pattern of `usage.background_domains` (OS updates and
  telemetry, Apple push, iCloud, Google APIs, OCSP/CRL certificate checks, FortiGuard, MikroTik
  update servers; the administrator adds the update and cloud hosts of the organisation's other
  products, e.g. an IP phone system or the antivirus); `*.example.com` also matches `example.com`
  itself, case does not matter;
- sessions to a port of `usage.background_ports` (DNS 53, NTP 123, DNS over TLS 853);
- sessions below `usage.background_session_bytes` (2048 bytes) in total;
- sessions whose application category is in `usage.background_appcats` (`Update`,
  `Network.Service`) **and whose host name is unknown**. (FortiOS 7.6 can log ordinary HTTPS
  browsing as `SSL_TLSv1.3.PQC` / `Network.Service`, verify on site; when the name is known the
  name rules decide — U4.)

Antivirus updates depend on the vendor: add your product's update domains. `*.googleapis.com` is
the specification's default; it may also hide app traffic such as the YouTube app — remove it if
needed.

### 2.5 Work hours and holidays

Every minute outside `usage.work_hours` (default Monday-Friday 08:00-18:00) and on the days of
`usage.holidays` is **off hours**. ARCILOGGER ships only the **fixed-date national holidays** of
Türkiye: 1 January, 23 April, 1 May, 19 May, 15 July, 30 August, 29 October. **The religious
holidays (Ramazan and Kurban Bayramı) move every year; the administrator enters their dates every
year as `YYYY-MM-DD`.** Half days (eves, the afternoon of 28 October) cannot be expressed in this
version: a day is a holiday or it is not.

```powershell
'["01-01", "04-23", "05-01", "05-19", "07-15", "08-30", "10-29", "2027-03-20", "2027-03-21"]' | & $db settings set usage.holidays - --by tolga
'{"days": [1, 2, 3, 4, 5], "start": "08:30", "end": "18:00"}' | & $db settings set usage.work_hours - --by tolga
```

(The 2027 dates of the example are placeholders, not real dates; take the year's dates from the
official calendar.)

## 3. Why everything is an estimate — known blind spots

- Logs show **connections**, not the person in front of the screen: a tab left open that polls in
  the background may count as active; a page read without traffic does not.
- **QUIC / HTTP3** (UDP 443) and other traffic the web filter does not log can only be named by a
  DNS answer or the FortiGuard service name; without them only the IP address is shown.
- **DNS over HTTPS** (the browser's encrypted DNS) hides the names: the session shows as an address
  or a service name.
- Devices and traffic **not passing the FortiGate** are invisible: a guest Wi-Fi with another exit
  (a separate line or a VPN tunnel; [DEVICE-SETUP.md](DEVICE-SETUP.md) section 5), LAN-internal
  traffic (file shares, printers), mobile data.
- Several people on one device, or devices behind their own NAT, cannot be split into persons.
- Phones with randomised MAC addresses are a new device with every new MAC: assign them to the
  same person.
- The interim-log interval is a FortiGate setting (verify on site); a session without interim
  logs that ends more than 30 minutes after midnight loses its part before midnight on the final
  rollup of the earlier day.

## 4. Settings

All are database settings (`& $db settings set …`, every change audited). A changed setting is
applied to the **last `usage.auto_rebuild_days` days automatically** (section 5); rebuild older
days by hand.

| Setting | Default | Meaning |
|---|---|---|
| `usage.active_bytes_threshold` | 2048 | Non-background bytes for an active minute (1 - 1,000,000,000) |
| `usage.background_session_bytes` | 2048 | A session with less traffic in total is background |
| `usage.dns_window_seconds` | 600 | A DNS answer this old still names a session (0 - 86400) |
| `usage.session_overlap_seconds` | 1800 | Logs this long after the end of a day still count for it |
| `usage.background_domains` | spec 10.3 list | Background host name patterns (`*`, `?`) |
| `usage.background_appcats` | `["Update", "Network.Service"]` | Background application categories (sessions without a host name) |
| `usage.background_ports` | `[53, 123, 853]` | Background destination ports |
| `usage.work_hours` | Mon-Fri 08:00-18:00 | `{"days": [1..7], "start": "HH:MM", "end": "HH:MM"}` (end exclusive) |
| `usage.holidays` | 7 fixed national holidays | `MM-DD` every year, `YYYY-MM-DD` once |
| `usage.psl_file` | `""` | Path of a newer `public_suffix_list.dat` (empty: the bundled one) |
| `usage.psl_private_suffixes` | `false` | Also honour the private section of the list |
| `usage.auto_rebuild_days` | 7 | Past days rebuilt automatically (0 - 62) |
| `usage.retention_days` | 400 | Rollups older than this are deleted (the logs: `db.retention_days`) |
| `usage.urls_per_day` | 1000 | Newest URLs kept per subject and day |
| `usage.blocked_per_day` | 5000 | Blocked-attempt rows kept per subject and day (the daily person and device counts are always complete) |
| `usage.pseudonymize_anonymous_role` | `false` | When on, viewers with only `usage.anonymous` see pseudonyms |
| `usage.change_min_percent` | 50 | Report "notable changes": at least this % against the 7-day average |
| `usage.change_min_seconds` | 3600 | … and at least this many seconds of time (on one side) |
| `usage.change_min_bytes` | 1073741824 | … and at least this many bytes of volume (1 GiB) |

## 5. Computing and rebuilding

Jobs of the `ArciWorker` service ([CORRELATION.md](CORRELATION.md) section 4):

| Job | When | What |
|---|---|---|
| `usage.today` | every 5 minutes | Recomputes today from the logs loaded so far (`partial`) |
| `usage.daily` | daily after 00:45 | Rebuilds yesterday as `final` |
| `usage.reattribute` | every 10 minutes | Rebuilds any of the last `usage.auto_rebuild_days` days whose inputs changed: a device was (re)assigned or the lease timeline changed, late logs were loaded (also while the day was being computed), the `usage.*` settings changed; also missing or still `partial` days |
| `usage.requests` | every 30 seconds | Rebuilds requested in the web interface (`POST /api/v1/usage/rebuild`) |
| `usage.retention` | daily after 03:30 | Deletes rollups older than `usage.retention_days` |

**After linking devices to persons:** the last 7 days follow within 10 minutes. For an older
assignment (e.g. a backdated hand-over) rebuild those days — in the web interface or on the server
(administrator PowerShell, [FIRST-STEPS.md](FIRST-STEPS.md) section 0):

```powershell
& "$arci\arcilogger-worker.exe" usage rebuild --from 2026-10-01 --to 2026-10-19 --config $cfg
```

Each day is computed in its own transaction (a reader sees the old or the new day, never a mix);
the command prints one line per day and adds one `usage.rebuild` row to `job_runs`. Exit codes: 0
done, 1 a day failed, 2 invalid dates/configuration, 3 database unreachable.
A rebuild needs the day's logs: the web interface (API) refuses a range that starts before the
day of the oldest log in the database (before today while there is none; 422
`usage_rebuild_no_logs`, the answer names the earliest day); a day without logs would be computed
empty and its rollups replaced by an empty day, so those days keep their rollups (the server
command `usage rebuild` does not check this). The database's log retention is the setting
`db.retention_days` (400 days); this release deletes no logs from the database
([DATA-MODEL.md](DATA-MODEL.md)).

## 6. Privacy and audit

- **Names are shown by default** (the owner's decision): every role that may see usage
  (`usage.person` or `usage.anonymous`) sees person and device names, MAC/IP addresses, host names
  and URLs. `dashboard.usage` alone sees only the company totals.
- Every endpoint that returns person-level data requires the **accepted KVKK notice**
  (`require_privacy_notice`). Every role that sees names accepts it: in this release all four
  roles (admin, auditor, hr, viewer; [roles.md](roles.md)).
- **Audit log** (ids and counts, never names): viewing one person's or device's detail
  (`usage.view`, spec 8.5), every export (`usage.export`: view, format, period, rows, filters
  used), rebuild requests (`usage.rebuild`), showing the pseudonym mapping (`usage.pseudonyms`).
  The target of a detail view and of a one-subject export is the real person or device
  (`usage:person:12`), also when the viewer sees pseudonyms: "who viewed this person?" finds the
  pseudonymous views too. The entry is never changed, but a reader who sees pseudonyms on the
  Kullanım pages (the auditor while the setting is on) sees this target in **Denetim Kaydı** as
  the person's or device's pseudonym token (`usage:p:3f2a…`, `usage:d:…`; `target_masked: true`
  in the answer, `usage:masked` without the key). The target filter compares with the value shown.
  The entry's hash covers the real target and would let a guess be tested, so such a reader gets
  no entry's hashes (`prev_hash` and `hash` empty, `last_hash` of `/audit/verify` empty); the
  chain is still verified on the server. Administrators and readers who see names see the real
  target and the hashes.
- **Pseudonyms (optional):** with `usage.pseudonymize_anonymous_role` on, viewers with
  `usage.anonymous` but without `usage.person` see `Kullanıcı-7F3A` / `Cihaz-1A2B` (MAC/IP
  hidden). A pseudonym is HMAC-SHA-256 with the secret `usage.pseudonym_key`; it is the same
  everywhere and on every day. Store the key once (a long random text; changing it changes every
  pseudonym):

  ```powershell
  & $core secret set usage.pseudonym_key
  ```

  Without the key the API answers `409 pseudonym_key_missing` where pseudonyms are needed. Only
  administrators see the mapping (pseudonym → person).

  Pseudonyms are weak for the auditor: with `search.read` and `lookup.run` it can reach IP and MAC
  addresses, and persons, through the raw logs and 5651 lookups. The day's seal manifest
  (Mühürleme) carries the hash of the day's last audit entry: on a day with few usage views a
  guess can still be tested against it. Evidence packages with the audit log (only an
  administrator can add it) hold the entries with their real targets and hashes; while the setting
  is on they are left out of the list of a reader who sees pseudonyms on the Kullanım pages, and
  their detail and download answer 404.
- **Leaving a person out of person-level reports:** Cihazlar ve Kişiler → Kişiler → the person's
  actions menu → **Düzenle** → **Kullanım raporlarından hariç tut** ("Exclude from usage reports";
  also on the "Kişi ekle" form; `persons.usage_excluded`). The person does not appear in subject
  lists, details, site/category user lists or the person tables of the reports; the traffic still
  counts in the company and department totals. The setting applies as soon as it is read (past days
  too; no rebuild; report files made and sent before stay as they are) and can be undone. The
  persons list shows **Kullanım: hariç** ("Usage: excluded") next to the name. The change is written
  to the audit log with its old and new value (`person.update`); for many persons use the optional
  `usage_excluded` column (evet/hayır) of the persons CSV. Only `devices.manage` (the administrator
  in the default roles) can change it. 5651 lookups, their official reports and evidence packages
  are not affected: the person always appears there.
- Exported files (CSV, Excel) contain personal data: keep them only as long as needed.

## 7. Web interface and API

The pages (Kullanım: company / department / person / device / site / category) use
`/api/v1/usage` ([MVP-CONTRACTS.md](MVP-CONTRACTS.md) section 12): period (`from`, `to`, days),
hours filter (`hours` = `all` / `work` / `off`), department filter (`department`), search,
sorting, paging (`limit` 1-100,000, `offset`; the interface reads a whole list in one request),
CSV and Excel exports (Excel: one sheet per section plus a "Yöntem" sheet with the method note).
Every answer carries `estimated: true` and `computed_until` (how far the rollups have processed
traffic).

The **users** of a site or category show only their figures there: time, off-hours time,
download/upload, sessions, and for a site its raw time as well. The rollups keep no online
minutes per category and no blocked attempts or first/last activity per site, so those columns
are left out. The detail view shows the 200 users with the most time, a category's 100 sites and
a site's 50 host names; a shortened user or site list says how many there are. The exported file
holds every one.

## 8. Performance

On the development machine (4 shared cores, PostgreSQL 16, no TimescaleDB), one Wednesday of the
load generator, 50 people (100 user devices plus infrastructure):

| Load | Rows | Full-day rollup (compute + write) |
|---|---|---|
| Default (30 browsing sessions per person and work hour) | 92,674 lines; 24,309 traffic, 16,185 web, 32,370 DNS, 15,866 application control | **1.7 s** (again: 2.1 s) |
| Five times the intensity | 450,182 lines; 123,097 traffic, 81,191 web, 162,382 DNS, 79,568 application control | **6.0 s** (again: 5.8 s) |

Budget (`worker/tests/test_usage_performance.py`, heavy load with `ARCILOGGER_USAGE_PERF=heavy`):
30 s and 120 s. Linear extrapolation to the specification's busy day (5 million messages a day,
~1.4 million traffic rows): a bit more than one minute per rollup late in the day.

## 9. Verify on site

- The FortiGate interim-log interval, and that interim logs carry `sentdelta`/`rcvddelta` (the
  format samples do).
- The `action` values of the web filter (`blocked`), the DNS filter (`block`) and application
  control (`block`, `reset`).
- The FortiGuard and MikroTik host names of the background list (`*.fortiguard.net`,
  `*.fortinet.net`, `upgrade.mikrotik.com`, `download.mikrotik.com`); add the update and cloud
  host names of the organisation's other products (IP phone system, antivirus …).
- The work hours and the year's religious holiday dates.
