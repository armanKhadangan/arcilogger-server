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

`SHA256SUMS` pins the exact bytes. `tests/test_fixture_integrity.py` fails if a file changes, for
example when a Windows Git client converts line endings (see `.gitattributes`). After an
intentional change, regenerate it in this folder with `sha256sum *.log *.txt > SHA256SUMS`.
