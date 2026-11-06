# Canlı Durum: the internet line, and who uses how much data right now

> Türkçe: [CANLI-DURUM.md](CANLI-DURUM.md). Usage reports: [USAGE.md](USAGE.md). Device settings
> (SNMPv3): [DEVICE-SETUP.md](DEVICE-SETUP.md). Licence: [LICENSING.md](LICENSING.md).
>
> ARCILOGGER is **designed to support 5651 obligations** ("5651 yükümlülüklerini desteklemek
> için tasarlanmıştır"). Canlı Durum is not a 5651 function: its person-level table is a usage
> analytics tool. Watching employees' internet use live is processing of personal data; naming it
> in the information text and a legal review are recommended
> ([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)).

The **Canlı Durum** page (`/activity`, below Canlı Akış in the menu) has two parts:

1. **İnternet hattı (Internet line):** the current download and upload speed (Mbps) of each WAN
   line, a chart of the last 60 minutes and, when the line's capacity is known, the share in use.
   The values are read from the device's interface counters over read-only SNMPv3 (section 3).
2. **Şu an (Right now):** who downloaded and uploaded how much data in the last 1, 5 or 15 minutes,
   one row per person, device or IP, the largest download first, and the company's last 60 minutes
   minute by minute. The values come from the FortiGate traffic logs, by the same rules as the
   usage reports (section 2).

While the page is open and visible it refreshes every 10 seconds; a tab in the background does
not ask. Each part is shown only to the roles allowed to see it (section 4).

## 1. What it shows and what it does not

- **Estimates.** The byte counts come from the devices' traffic logs; as on the Kullanım page, no
  value is an exact measurement.
- **MikroTik counts no bytes.** MikroTik logs carry no byte counts: traffic through a MikroTik is
  not in the "Şu an" table, and the page says so. A MikroTik's line still shows under "İnternet
  hattı" when SNMP is on for it.
- **Long connections show up with a delay.** FortiGate reports a session's bytes in interim logs
  while it runs and in a final log when it closes. How often the interim logs come depends on the
  device and is **verified on site** (X-B1). The delta of an interim log counts at its own minute;
  the final log adds only what the interim logs did not. The page shows the time of the newest
  traffic log: "Uzun bağlantılar FortiGate ara kayıtları geldikçe görünür" (long connections show
  up as FortiGate's interim logs arrive).
- **The internet-line part carries no personal data:** only the line's name, speed, status and
  times.

## 2. The "Şu an" table

| Column | Meaning |
|---|---|
| Person / device / IP | The usage reports' subject rules: the person when the device is assigned to one, else the device, else the IP address |
| Download, upload | Bytes over the window, and the average rate (bytes × 8 / the window's seconds) |
| Sessions | The sessions whose traffic counts in the window |
| Top sites, apps | At most 3 each; background traffic is not counted among the sites |
| Last seen | The subject's last traffic in the window |

- **Window:** 1, 5 or 15 minutes (default 5). Any other value is refused with 422
  `activity_window_invalid`.
- At most **200 rows** are shown; when there are more, the page says so. The totals and the
  per-minute chart always cover all traffic.
- **Persons excluded from usage analytics** (`usage_excluded`) get no row; their traffic counts in
  the company totals.
- **Pseudonyms:** with `usage.pseudonymize_anonymous_role` on, roles without `usage.person` see
  pseudonyms instead of names, by the same rule as the Kullanım page.
- The counting is the usage reports' own code: a whole day of minutes adds up to the same bytes as
  that day's usage rollup. The work hours and breaks ([USAGE.md](USAGE.md)) apply to each minute
  with that day's own hours.

## 3. The internet line (SNMPv3)

Each log source has an SNMP part on the **Kaynaklar** page (Ayarlar → Kaynaklar → the source →
SNMP). When it is on, ARCILOGGER reads the counters of the device's WAN interfaces itself:

| Setting (`[sources.snmp]`) | Default | Bounds | Meaning |
|---|---|---|---|
| `enabled` | off | — | Whether this source's line is read |
| `port` | 161 | 1–65535 | The device's SNMP port (UDP) |
| `user` | — | — | The SNMPv3 user name (not a secret) |
| `auth` | `sha256` | `sha1`, `sha256`, `sha512` | Authentication protocol |
| `priv` | `aes128` | `aes128`, `aes256` | Privacy protocol (`aes256`: **verify on site**) |
| `interfaces` | — | 1–4 interfaces | The interface names (`ifName`) of the WAN lines; "Bağlantıyı dene" (Test connection) fetches the list from the device |
| `capacity_mbps` | — | optional | Download/upload capacity per interface (Mbps), to show a share |
| `interval_seconds` | 15 | 10–60 | How often it is read |

- **SNMPv3 with authentication and privacy (authPriv) only.** SNMP v1/v2c are refused: their
  community string crosses the network in clear text.
- **The target is the source's own address.** SNMP cannot be switched on for a source whose address
  is a network (`snmp_source_is_network`).
- **The keys are in the secret store:** `snmp.<source>.auth_key` and `snmp.<source>.priv_key` (8–64
  characters). They are never written into the TOML file, the logs, the alerts or an API answer. A
  missing key gives `snmp_secret_missing`.
- **Direction:** on a WAN interface, In is download and Out is upload; compare with the device's
  own interface graph on each device (**verify on site**, X-B2).
- **64-bit counters only** (`ifHCInOctets`, `ifHCOutOctets`). A device without them gets the
  status `snmp_no_hc_counters`. When a counter or the device's uptime goes back (a restart), that
  read only starts the next rate.
- **Statuses:** `ok`, `timeout`, `auth_failed`, `no_such_interface`, `snmp_no_hc_counters`,
  `disabled`.
- **Alert `R20-snmp`** (low): raised when a source has given no good read for 10 minutes, resolved
  by the next good one ([ALERTS.md](ALERTS.md)).
- **Storage:** no database table. The worker keeps the last 60 minutes at full resolution and the
  last 24 hours as 1-minute averages in `D:\ARCILOGGER\state\wan-rates.json`.
- **Network:** the server sends UDP only to the address and port of the sources with SNMP on;
  nothing is inbound. With strict outbound, the firewall opens one `ARCILOGGER SNMP <source>` rule
  per source ([INSTALL.md](INSTALL.md) section 12).
- **On the device:** a read-only SNMPv3 user, answering only the ARCILOGGER server's address, on a
  LAN interface, **never on a WAN interface** ([DEVICE-SETUP.md](DEVICE-SETUP.md), "SNMPv3").

## 4. Who sees what

| Part | Permission | Roles | KVKK notice | Audit log | Licence |
|---|---|---|---|---|---|
| İnternet hattı | `dashboard.view` or `system.read` | admin, auditor, viewer | not needed (no personal data) | not written | 5651 Core |
| Şu an | `usage.person` or `usage.anonymous` | admin, auditor, hr, viewer | must be acknowledged | `activity.view` (section 5) | the **Usage analytics** module |

While the Usage analytics module is off, "Şu an" is locked and the internet line keeps working
([LICENSING.md](LICENSING.md)). The Panel's **İnternet hattı** card is hidden while no source has
SNMP on; administrators see a link to Kaynaklar instead.

## 5. Audit

Viewing the "Şu an" table is written to the audit log as `activity.view` (target
`activity:devices`; details: the window and the number of rows, **no names**). Since the page asks
every 10 seconds, the entry is written at a user's first request and then at most every 15
minutes while the user keeps asking.

## 6. To verify on site

| No | What | How |
|---|---|---|
| X-B1 | How often FortiGate sends interim traffic logs (the delay of long sessions in the table) | Watch when a row updates during a long download |
| X-B2 | In/Out of the WAN interface are download/upload | Compare with the device's own interface graph |
| X-B3 | The SNMPv3 protocols the device accepts (older firmware may need `sha1`, or offer no `aes256`) | "Bağlantıyı dene" (Test connection) |
| X-B4 | The interface names (`ifName`) the device reports for its WAN lines | "Bağlantıyı dene" lists them; no name is assumed |
