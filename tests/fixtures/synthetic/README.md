# Synthetic log lines — synthetic, verify on site

**Sentetik — sahada doğrulanmalı.** These lines were **written by hand**. They exist so the
parsers have test input for formats that have no format sample yet (the build specification
asks for parsers of the expected FortiGate and RouterOS messages, their fixtures marked
"synthetic, verify on site"). The format ground truth stays `tests/fixtures/format/`.

| File | Built from | Not verified |
|---|---|---|
| `fortigate-synthetic.log` | FortiOS `format default` header of the format samples + the field lists of the specification's notes on expected FortiGate types (webfilter, dns, ips, virus, admin login, configuration change, VPN/user events, eventtime in µs/s, missing `eventtime`, relay prefix, unknown type) | every `logid` (placeholders `99000000xx`), FortiGuard category numbers/names, attack/virus ids and names, the `ipaddr` list format, `logdesc` texts other than the spec's, VPN/user event field names |
| `mikrotik-wire-synthetic.log` | The RouterOS `/log print` format sample (`tests/fixtures/format/mikrotik-log-print-sample.txt`) in the approximate wire format of `docs/PARSERS.md` §3.1 (BSD syslog header, ISO-8601 time, topics string), in header variants (with/without milliseconds, offset, host name, topics; BSD time; topics with a trailing `:`; a tag or a single topic word before the message), plus the other message shapes of spec 7.2 (ICMP, TCP flags, NAT, login failure, link up/down, configuration change, heartbeat, Wi-Fi, DHCP pool empty) | the whole header layout, login-failure and configuration-change wording, Wi-Fi/CAPsMAN messages, NAT and ICMP layout, DHCP pool-empty and offering wording |

Rules:

- One syslog message per line, exactly as it would arrive on the wire (PRI included). Lines
  starting with `#` are comments; the tests skip them.
- All values are fictional. Addresses are from the documentation ranges (`192.0.2.0/24`,
  `198.51.100.0/24`, `203.0.113.0/24`) or the fictional example network of the format samples;
  domains are `example.*`; user names, group, rule and interface names are made up; MAC
  addresses are from the documentation range of RFC 7042 (`00:00:5E:00:53:xx`), locally
  administered (`02:…`, `4E:…`) or made up with the middle bytes `00:00`.
- When a format has been seen on a device, add a format sample with fictional values to
  `tests/fixtures/format/` (with `SHA256SUMS`), compare it with these lines, fix the parsers and
  correct or delete the synthetic lines.
