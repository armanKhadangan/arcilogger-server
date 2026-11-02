# Log format samples

**Biçim örnekleri — tüm değerler kurgusaldır.** Synthetic format samples, modelled on FortiOS
7.6 / RouterOS 7 output; all values fictional.

Ground truth for the parsers' format (milestone M2): the field names, their order, the quoting
and the separators are those the devices print. Every value is made up: the date, times and
`eventtime` values, device names, addresses, ports, session ids, counters, durations, countries,
application, service and host names. The application ids (`appid` 99101 and 99102) and the
application and Internet Service names (`Example.Docs`, `unknown-99102`, `Example-…`) are
fictional placeholders that only keep the shape of the fields. Addresses come from the fictional
example network of the tests and the documentation ranges (RFC 5737), MAC addresses from the
documentation range of RFC 7042 (`00:00:5E:00:53:xx`) or are locally administered (`02:…`),
domain names end in `.example`, `.example.org` or `.example.net`.

| File | Device | Format | Content |
|---|---|---|---|
| `fortigate-sample.log` | a firewall (`FIREWALL-01`), FortiGate, FortiOS 7.6 | One syslog datagram per line, as FortiOS sends it (`format default`, PRI included) | 7 lines of one morning, in time order: performance statistics, application control, an interim update of a long session, a failed connection, local traffic of the firewall, a certificate-inspection bypass, the end of the session the application-control line opened |
| `mikrotik-log-print-sample.txt` | a core router (`CORE-R1`), MikroTik RouterOS 7 | `topics \| message` as shown in RouterOS `/log print` — **not** the wire format | 11 lines: DHCP leases (with and without host name), `ARP-LOG` lines of static devices, two forward-chain drops (TCP), a WinBox login and logout |

The MikroTik wire format (BSD syslog header, ISO-8601 time, topics string) has no format sample
yet; `tests/fixtures/synthetic/` approximates it (synthetic, verify on site). Once it has been
seen on a device, a sample with fictional values is added here as `mikrotik-wire-sample.log`, and
the parser tests then use it.

**Awaited: `fortigate-dhcp-sample.log`** (FortiGate DHCP server lines, 26001 ACK and 26002
RELEASE; ADR-0016). There is no device capture yet: the parser and its tests rest on the FortiOS
7.6 field list and one community sample (`tests/fixtures/synthetic/`, sahada doğrula), and
`ingest/tests/test_fortigate_dhcp.py::test_real_fortigate_dhcp_sample` is skipped until the file
exists.

- İlk cihaz kaydından **biçimi** alınarak eklenir: alan adları, sıraları, tırnaklama ve
  ayırıcılar aynen korunur; her değer örnek dünyanın değeriyle değiştirilir (MAC
  `00:00:5E:00:53:xx`, IP `172.20.x.x`, `devname="FIREWALL-01"`, `devid="FGT0000000000000"`, ana
  bilgisayar adı ör. `PLN-PC14`). Gerçek bir MAC, IP, ana bilgisayar adı, seri numarası ya da
  cihaz adı depoya girmez.
- Added from the **shape** of the first device capture: field names, order, quoting and
  separators are kept exactly; every value is replaced by the example world's (MAC
  `00:00:5E:00:53:xx`, IP `172.20.x.x`, `devname="FIREWALL-01"`, `devid="FGT0000000000000"`, host
  name e.g. `PLN-PC14`). No real MAC, IP, host name, serial number or device name enters the
  repository.

Its `SHA256SUMS` line is added with it. The test then checks that each ACK/RELEASE line becomes an
`fg_dhcp` row with the device's own time, that its logid and `dhcp_msg` are accepted by the
default settings (`dhcp.fortigate_logids`, `dhcp.fortigate_dhcp_msgs`) and that `lease` is an
integer in the FortiOS range; it records the RELEASE `dhcp_msg` value it saw.

`SHA256SUMS` pins the exact bytes. `tests/test_fixture_integrity.py` fails if a file changes, for
example when a Windows Git client converts line endings (see `.gitattributes`). After an
intentional change, regenerate it in this folder with `sha256sum *.log *.txt > SHA256SUMS`.
