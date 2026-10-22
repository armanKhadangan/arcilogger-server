# Ayrıştırıcılar / Parsers

> Durum: M2, iş paketi B1. Kod: `ingest/src/arcilogger_ingest/parsers/`. Sözleşme:
> [MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 4. Tablolar ve sütunlar:
> [DATA-MODEL.md](DATA-MODEL.md) ve `db/src/arcilogger_db/models.py`.

## Özet (TR)

- Her ham satır **tek bir `messages` satırı** ve en fazla bir tipli satır (`fg_*` / `mt_*`)
  üretir. Her satır ham satıra geri götüren referansı (`source`, `raw_day`, `raw_line`,
  `raw_offset`) taşır; ham satır her zaman delildir, veritabanı türetilmiştir.
- **Hiçbir alan kaybolmaz:** sütuna eşlenmeyen her alan `extra` (JSONB) içinde kendi adıyla
  saklanır. Sütuna sığmayan bir değer (ör. `srcip="n/a"`) `extra`'da metin olarak kalır ve
  satır `parse_failures` tablosunda sayılır.
- **Zaman:** FortiGate `eventtime` (ns/µs/ms/s, büyüklükten anlaşılır), yoksa `date`+`time`+
  `tz`; MikroTik başlığındaki ISO-8601 zamanı. **Saat dilimi olmayan bir zaman tahmin
  edilmez**: alım zamanı kullanılır, metin `extra.device_time` içinde kalır. Alım zamanından
  **1 günden fazla** sapan (ayarlanabilir) ya da UTC'ye çevrilemeyen bir cihaz zamanı da
  kullanılmaz (bozuk saat); satır `<parser>.time` olarak sayılır.
- MikroTik kablo biçimi (wire format) **henüz sahada yakalanmadı**; ayrıştırıcı toleranslıdır
  (zaman damgası, ana bilgisayar adı ve konu dizisi isteğe bağlı). İlk gerçek kayıt geldiğinde
  aşağıdaki "Sahada doğrulanacaklar" listesi gözden geçirilmelidir.
- Hız (bu geliştirme ortamında, çekirdek başına): FortiGate ~29.000–32.000 satır/sn, MikroTik
  ~44.000–56.000 satır/sn.

## 1. Common rules (all parsers)

| Rule | Detail |
|---|---|
| One line → rows | `ParseOutcome.message` (table `messages`, always exactly one) + `ParseOutcome.rows` (at most one typed row for FortiGate/MikroTik) |
| Never raises | A parser exception becomes the parse failure `<parser>.exception` with the generic view of the line (`Parser.parse`); a device time that cannot be converted to UTC (year 1 with a positive offset, year 9999 with a negative one, a naive value from a third-party parser) becomes `<parser>.time` with the receive time (`base.complete`, a safety net — the built-in parsers check times themselves) |
| Device time | Used only when it has an offset **and** is within `max_clock_skew` (constructor parameter of every parser, default **1 day**, `None` = no limit) of the receive time and representable in UTC (`syslog.plausible_time`). Otherwise the receive time is used, the device text is kept in `messages.extra.device_time`, and an implausible time is the failure `<parser>.time` (a time without offset is not a failure: it is never guessed). Devices sync NTP from a local time source (docs/DEVICE-SETUP.md), so a larger distance is a broken clock (a router booted at 1970, a wrong year, a misread `eventtime` unit), not a delayed message |
| Text | Decoded with `decode_message`: UTF-8, invalid bytes → U+FFFD, NUL → U+FFFD. `messages.text` ≤ 2,000 characters, `url` columns ≤ 2,000 (`messages.extra.truncated`) |
| Uniform rows | Every row of a table has **every column** (absent values `None`); `arcilogger_db.bulk.copy_rows` takes its column list from the first row of a batch, so a missing key would silently drop later values (test: `test_rows_of_one_table_always_have_the_same_columns`) |
| Addresses | `INET` values as canonical text (IPv4 dotted quad without leading zeros; IPv6 compressed, no zone id); MAC addresses `aa:bb:cc:dd:ee:ff` (lower case, colons — the PostgreSQL output layout and `arcilogger_worker.lookup.normalize_mac`) |
| Integers | ASCII digits (optional `-`) within the column's range; anything else stays text in `extra` |
| Validity | Every emitted row passes `arcilogger_db.rowcheck.check_row(..., complete=True)` (contract test over the format samples and synthetic fixtures, junk and fuzzed bytes) |

### Parse-failure patterns (`messages.parse_error`, `parse_failures.pattern`)

A pattern is set when a line was not (fully) understood. Rows that could be built are still
returned, so B2 loads them **and** counts the failure.

One pattern per line, the most severe first (FortiGate: glued, unterminated, unparsed, type,
value, time; MikroTik: glued, the family's own failure, time).

| Pattern | Meaning | Typed row? |
|---|---|---|
| `fortigate.kv` | No `key=value` pair at all (generic view of the line) | no |
| `fortigate.glued` | Two or more envelope keys (`date`, `time`, `devname`, `devid`, `logid`, `type`, `eventtime`) occur twice: two messages glued into one line (framing error upstream). The row is the first message's; the second message's values are in `extra` as duplicate lists | yes |
| `fortigate.unterminated` | A quoted value has no closing quote (datagram cut short?) | yes |
| `fortigate.unparsed` | Tokens that are not `key=value` (kept in `messages.extra.unparsed`) | yes |
| `fortigate.type` | No `type=` field (all fields in `messages.extra.fields`) | no |
| `fortigate.value` | A mapped value does not fit its column (kept in `extra`); also an `eventtime` that is not a timestamp, and `date`/`time` that are not a date and time although `tz` is valid (`invalid value: eventtime`, `invalid value: date/time`) | yes |
| `fortigate.time` | Implausible device time (see "Device time"); detail `implausible device time: …` | yes |
| `mikrotik.glued` | A second syslog header (`<PRI>` + ISO, BSD or RFC 5424 timestamp) inside the message: cut there, the rest is in `messages.extra.glued` | first message's |
| `mikrotik.dhcp` | `assigned`/`deassigned` line that looks like DHCP evidence (topic `dhcp`, or a MAC address in the message) but does not match, or has a bad IP/MAC | no |
| `mikrotik.arp` | `ARP-LOG` line without a valid IP and MAC | no |
| `mikrotik.firewall` | Topic `firewall` (or `proto …` and `->` in the message) without the `chain: in:… out:…` layout (no row), or an invalid address/port/length (row, value in `extra`) | see left |
| `mikrotik.time` | Implausible header time | yes |
| `generic.time` | Implausible header time (generic parser) | — |
| `<parser>.exception` | A parser bug (generic view of the line) | no |

## 2. FortiGate (`parsers/fortigate.py`, `parsers/kv.py`)

Required device setting: `config log syslogd setting` → `set format default` (spec 2.1,
`docs/DEVICE-SETUP.md`). Lines in `csv`/`cef` format are counted as parse failures.

### 2.1 Tokenizer (spec 7.2)

- Tokens separated by ASCII whitespace; `key=value`, the key ends at the first `=`.
- **Unquoted** values end at the next whitespace (they may contain `=`, `"`, `,`, `;`).
- **Quoted** values may contain anything: spaces, `=`, `,`, `;` (e.g. `waninfo` of the format
  samples), and `\"` / `\\` escapes. Any other backslash is kept literally (Windows paths).
- **Unverified escaping:** if FortiOS does *not* escape backslashes, a value ending in one
  (`filename="C:\Temp\"`) turns its closing quote into an escaped quote — at the end of the line
  the value swallows the rest (unterminated), inside the line the quotes pair up wrongly and the
  next quoted value becomes a stray. When the escape-aware reading is unterminated or has strays
  and a reading with every backslash literal (`tokenize_reference(..., escapes=False)`) is
  clean, the literal reading is used and `messages.extra.backslashes = "literal"` records it
  (query it on site to settle the question, §7).
- `key=` and `key=""` are the empty string; an empty value is `NULL` in a typed column of
  **every** kind — text included (an empty `hostname` must not win the spec 10.2 usage join
  over the DNS fallback; an empty SSL `hostname` falls back to `sni`) — and kept in `extra` as
  `""`, not an error. An empty `devname` is not a host name (`messages.host` `NULL`, `extra`
  keeps it); an empty `subtype` is `messages.subtype` `NULL`.
- **Duplicate keys:** the column gets the **first** value; `extra[key]` holds **all** values as
  a JSON list (otherwise `extra` values are always strings, so a list always means "duplicate").
- A missing closing quote takes the rest of the line (`fortigate.unterminated`).
- Anything before the first `key=value` token — normally nothing, `date=` comes first — is kept
  in `messages.extra.prefix` (e.g. a relay's BSD header). The syslog PRI gives facility and
  severity; without a PRI the severity comes from `level`.
- Implementation: `tokenize_reference` is the specification as a character loop; `tokenize`
  answers ordinary lines with a few C-level string operations and falls back to the reference
  for anything unusual (backslash, control characters, unbalanced quotes, strays, duplicates).
  A hypothesis test compares both on arbitrary text.

### 2.2 Event time

| Source | Rule |
|---|---|
| `eventtime` (preferred) | UTC since the epoch; unit by magnitude: 10⁹–10¹¹ s, 10¹¹–10¹⁴ ms, 10¹⁴–10¹⁷ µs, 10¹⁷–10²⁰ ns (FortiOS 7.6: ns, 19 digits). A non-empty value that is not such a number (before 2001-09-09, not plain decimal) is `fortigate.value`; one outside the clock skew is not used (`fortigate.time`, `extra.eventtime` keeps it) and `date`/`time`/`tz` are tried |
| `date` + `time` + `tz` | When `eventtime` is missing or unusable; `tz` `+0300` (also `+03:00`, `Z`). Outside the clock skew: not used, `fortigate.time` |
| neither / no offset | Receive time; `messages.extra.device_time = "<date> <time>"` (never guessed); after an implausible time `"<date> <time> <tz>"` or the `eventtime` text |

The fields a time was derived from are not repeated in `extra` (`eventtime`, `date`, `time`,
`tz`); unused ones stay. The tests check on all 7 format-sample lines that `eventtime`
and `date`/`time`/`tz` agree to within one second — they fail loudly if a sample disagrees.

### 2.3 Type → table

| `type` / `subtype` | Table | `messages.type` / `subtype` |
|---|---|---|
| `traffic` / any (`forward`, `local`, …) | `fg_traffic` | `traffic` / subtype |
| `utm` / `webfilter` | `fg_web` | `utm` / `webfilter` |
| `utm` / `dns` | `fg_dns` | `utm` / `dns` |
| `utm` / `app-ctrl` | `fg_app` | `utm` / `app-ctrl` |
| `utm` / `ssl` | `fg_app` (decision below) | `utm` / `ssl` |
| `utm` / `ips` | `fg_ips` | `utm` / `ips` |
| `utm` / `virus` | `fg_virus` | `utm` / `virus` |
| `event` / any (`system`, `user`, `vpn`, …) | `fg_event` | `event` / subtype |
| anything else (e.g. `utm`/`dlp`) | — (`messages.extra.fields` holds all fields) | type / subtype |

**SSL logs go to `fg_app`** (not `fg_web`): spec 10.2 ranks `fg_web.hostname` first (the web
filter's verdict with category and the "blocked attempts" count) and puts the SSL
`sni`/`hostname` in the same second tier as `fg_app.hostname`. The usage engine joins
`fg_app.hostname` by `sessionid` for both. SSL rows have `app`/`appid`/`appcat` = `NULL`,
`hostname` = `hostname` (else `sni`), and `extra.subtype = "ssl"`, `extra.sni`,
`extra.eventtype`, `extra.eventsubtype`, `extra.profile`. App-control rows carry
`extra.subtype = "app-ctrl"`.

> **Consumers of `fg_app` must filter on the subtype** when they mean only one of the two:
> application-control counts (spec 10.3 "Engellenen denemeler", detection R10) use
> `extra->>'subtype' = 'app-ctrl'`; SSL inspection rows are `extra->>'subtype' = 'ssl'`. The
> usage engine's hostname join by `sessionid` wants both. A real `fg_app.subtype` column would
> make this a plain index condition; it needs a migration (`0002_parsers`) and a change to the
> migration test's head, so it is proposed as a shared change rather than done in B1.

**Interim traffic logs:** `fg_traffic.is_interim = logid ∈ interim_logids` (constructor
parameter, default `{"0000000020"}`). `sentdelta`/`rcvddelta`/`durationdelta` are
columns; `sentpktdelta`/`rcvdpktdelta` (format-sample line 3) go to `extra`.

### 2.4 Field mapping (FortiGate key → column)

Keys not listed go to the row's `extra` under their own name. In every typed table `type` is
implied by the table and `devname` is `messages.host`.

**`fg_traffic`** — `logid`, `subtype`, `action`, `policyid`*, `policyname`, `sessionid`*,
`proto`*, `srcip`†, `srcport`*, `srcintf`, `dstip`†, `dstport`*, `dstintf`, `transip`†,
`transport`*, `trandisp`, `service`, `dstinetsvc`, `app`, `appid`*, `appcat`, `apprisk`,
`dstcountry`, `duration`*, `sentbyte`*, `rcvdbyte`*, `sentpkt`*, `rcvdpkt`*, `sentdelta`*,
`rcvddelta`*, `durationdelta`*, `utmaction`, `crscore`*; derived: `is_interim`. (Spec 7.3 list,
complete.) Typical `extra`: `devid`, `vd`, `level`, `srcintfrole`, `dstintfrole`, `srccountry`,
`dstregion`, `dstcity`, `dstreputation`, `policytype`, `poluuid`, `applist`, `appact`,
`countapp`, `craction`, `crlevel`, `msg`, `sentpktdelta`, `rcvdpktdelta`.

**`fg_web`** — `logid`, `sessionid`*, `srcip`†, `srcport`*, `dstip`†, `dstport`*, `hostname`,
`url` (≤ 2,000), `cat`*, `catdesc`, `action`, `eventtype`, `profile`. (`subtype` implied.)

**`fg_dns`** — `logid`, `sessionid`*, `srcip`†, `srcport`*, `dstip`†, `dstport`*, `qname`,
`qtype`, `cat`*, `catdesc`, `action`, `eventtype`; `ipaddr` → `answers` (`INET[]`, split at
`,`, `;` and spaces). If any `ipaddr` token is not an address, `answers` holds the valid ones,
`extra.ipaddr` the text as sent and the line is `fortigate.value`. (`subtype` implied.)

**`fg_app`** — `logid`, `sessionid`*, `proto`*, `srcip`†, `srcport`*, `dstip`†, `dstport`*,
`app`, `appid`*, `appcat`, `apprisk`, `action`, `hostname` (SSL: else `sni`), `url` (≤ 2,000).
`subtype` stays in `extra`.

**`fg_ips`** — `logid`, `eventtype`, `proto`*, `sessionid`*, `srcip`†, `srcport`*, `dstip`†,
`dstport`*, `service`, `attack`, `attackid`*, `severity`, `action`, `msg`. (`subtype` implied.)

**`fg_virus`** — `logid`, `eventtype`, `proto`*, `sessionid`*, `srcip`†, `srcport`*, `dstip`†,
`dstport`*, `service`, `virus`, `virusid`*, `dtype`, `filename`, `url` (≤ 2,000), `hostname`,
`action`, `msg`. (`subtype` implied.)

**`fg_event`** — `logid`, `subtype`, `level`, `logdesc`, `action`, `status`, `user` →
`user_name`, `ui`, `srcip`†, `dstip`†, `cfgpath`, `cfgobj`, `cfgattr`, `msg`. Everything else
(perf-stats `cpu`, `mem`, `waninfo`…; VPN `remip`, `tunnelip`…; admin login `reason`…) in
`extra`. Detections (spec 11): R03/R04 use `logdesc`, `status`, `user_name`, `srcip`, `ui`;
R16 `cfgpath`/`cfgobj`/`cfgattr`; R17 `extra.cpu`/`extra.mem` of `action="perf-stats"`.

\* integer column (range-checked: `proto` SMALLINT; ports, `cat`, `crscore` INTEGER; the rest
BIGINT) · † `INET`.

**`messages`** — `type`, `subtype` (FortiGate's), `severity`/`facility` (PRI; severity from
`level` without PRI), `host` = `devname`, `program` = `NULL`, `text` = the line after the PRI.

## 3. MikroTik (`parsers/mikrotik.py`)

### 3.1 Header (wire format **not verified**)

Accepted, each part optional and in this order: `<PRI>`; an RFC 5424 header, an ISO-8601
timestamp (fraction and offset optional) or a BSD timestamp (`Sep 23 09:38:26`); a host name;
a **topics** token; the message.

- **Topics token:** lower-case words (`[a-z0-9-]`) joined by `,` (`;` as in `/log print` is
  tolerated), at least two words, optionally followed by `:` (`dhcp,info`,
  `system,info,account`, `script,info:`). Stored without the `:` in `messages.program` and
  `mt_event.topics`. A single word (`dhcp`, `script:`) cannot be told from a host name or a tag
  and is not taken as topics; it stays in front of the message (see the evidence families).
- **Host name:** the token before a topics token (confirmed); or, by BSD convention, the token
  after a timestamp (unconfirmed) — never a token ending in `:` (a tag or a firewall chain such
  as `forward:`).
- **Host names given (`host_names`, constructor parameter / `build_parsers`):** the device
  identities (`/system identity`, case-insensitive). An unconfirmed word is then the host name
  exactly when it is one of them, also when no timestamp precedes it. **Recommended on site:**
  without it, `<ts> DROP-WAN input: in:…` and `<ts> CORE-R1 input: in:…` (no topics) have the
  same shape, and the BSD convention reads `DROP-WAN` as the host (prefix `NULL`), `<ts>
  filter rule changed by admin` as host `filter` / target `rule`, and `<30>CORE-R1 DROP-FWD
  forward: …` (no timestamp) as prefix `CORE-R1 DROP-FWD`. With topics in every line (as in the
  format samples) none of this matters.
- **Without `host_names`:** an unconfirmed host name is tried both ways (as host name, and as
  the first message word); when neither a timestamp nor topics mark the header, the line is
  tried as is and with its first word as host name (`<30>CORE-R1 dhcp60 assigned …`). The
  reading with the best result wins — a clean match before one that skipped residue before the
  keyword, before a family failure; on a tie the first.
- **Level** (`mt_event.level`, `messages.subtype` of unrecognised lines): the most severe of
  `critical`, `error`, `warning`, `info`, `debug` among the topics; without one, from the PRI
  severity (0–2 critical, 3 error, 4 warning, 5–6 info, 7 debug).
- **Time:** the header time when it has an offset (`Z`, `+03:00`, `+0300`) and is plausible
  (§1 "Device time"); otherwise the receive time and `messages.extra.device_time` (BSD
  timestamps never have an offset; an implausible time is `mikrotik.time`).
- **Two messages in one line:** a second header (`<PRI>` followed by an ISO, BSD or RFC 5424
  timestamp) inside the message is cut off into `messages.extra.glued` (`mikrotik.glued`), so
  the first lease keeps its own host name instead of swallowing the second message.

### 3.2 Message families

| Message (regular expression, spec 7.2) | Row | `messages.type` / `subtype` |
|---|---|---|
| `<server>[:] assigned\|deassigned <ip> for <mac>[ <hostname…>]` (`to` instead of `for` tolerated), found anywhere in the message | `mt_dhcp`: `server`, `vlan`, `action`, `ip`, `mac`, `hostname` (rest of the line, trimmed; empty → `NULL`); header residue before the server → `extra.prefix` | `dhcp` / action |
| `<server> offering lease <ip> for <mac> …` | `mt_event` `dhcp_offering`: `ip`, `mac`; `extra.server`, `extra.vlan`, `extra.tail` | `dhcp` / `offering` |
| `<server> failed to give out IP address … pool <x> is empty` | `mt_event` `dhcp_pool_empty`; `extra.server`, `extra.vlan`, `extra.pool` (without "pool … is empty": `dhcp_failed`) | `dhcp` / `pool_empty` |
| `ARP-LOG <ip> <mac> [<interface>]`, found anywhere (after the start, a space or `:`) | `mt_arp`: `ip`, `mac`, `interface`; extra words → `extra.tail`; residue before `ARP-LOG` → `extra.prefix` | `arp` / — |
| `ARCI-HEARTBEAT [<identity>]`, found anywhere like `ARP-LOG` | `mt_event` `heartbeat`: `identity`; residue → `extra.prefix` | `heartbeat` / — |
| `[<prefix> ]<chain>: in:<if> out:<if>[, connection-state:<s>][ src-mac <mac>][, proto <P>[ (<info>)]][, <src>[:<sport>]-><dst>[:<dport>]][, NAT <…>][, len <n>]` | `mt_fw`: `prefix`, `chain`, `in_if`, `out_if` (`(unknown 0)` kept as sent), `state`, `src_mac`, `proto`, `src`, `sport`, `dst`, `dport`, `len`; `extra.proto_info` (TCP flags or `type 8, code 0`), `extra.nat`, `extra.tail` (anything after the known parts) | `fw` / chain |
| `user <u> logged in\|out[ from <ip>][ via <v>]` | `mt_account`: `action` `login`/`logout`, `user_name`, `ip`, `via` (a non-IP "from", e.g. a WinBox MAC → `extra.from`) | `account` / action |
| `login failure for user <u>[ from <ip>][ via <v>]` | `mt_account` `login_failure` | `account` / `login_failure` |
| `<if> link up\|down[ (<detail>)]` | `mt_interface`: `interface`, `state`, `detail` | `interface` / state |
| configuration-change patterns (3.3) | `mt_config`: `action`, `user_name`, `target`, `via`; other groups → `extra` | `config` / action |
| `<mac>@<if>[:] connected\|disconnected[, <detail>]` | `mt_event` `wifi_connect`/`wifi_disconnect`: `mac`, `interface`; `extra.detail` | `wifi` / state |
| anything else, level critical/error/warning | `mt_event` `log`: `topics`, `level`, `text` | `log` / level |
| anything else | — | `log` / level |

`mt_event.text` is the message; `mt_event.topics` and `level` are filled for every event.

**Evidence is never silently a plain log line.** DHCP leases, `ARP-LOG` and the heartbeat are
the 5651 attribution evidence and our own scripts' output, so they are searched in the whole
message: header residue that the header rules did not recognise (`dhcp dhcp60 assigned …`,
`script: ARP-LOG …`, `dhcp,info CORE-R1 dhcp41 assigned …`) is skipped and kept in the row's
`extra.prefix`. A line with ` assigned `/` deassigned ` that is DHCP evidence (topic `dhcp`,
or a MAC address anywhere in the message) but does not give a valid lease is `mikrotik.dhcp`;
without such evidence (`ether1 assigned address to bridge`) it is an ordinary log line. Within
one reading the families are tried in order and the first clean match wins; a match that
skipped residue does not hide a clean match of a later family (a DHCP host name containing
`ARCI-HEARTBEAT` stays a lease).

**VLAN of a DHCP server:** constructor parameter `server_vlan_regex`, default = the database
setting `dhcp.server_vlan_regex` (`^dhcp(\d+)$`, first group = VLAN id, 1–4094), so `dhcp60` →
60 by the same rule as `arcilogger_db` (the VLAN table is not duplicated). A name without a
VLAN (e.g. `defconf`) gives `NULL`.

### 3.3 Configuration changes (configurable, verify on site)

Constructor parameter `config_patterns` (regular expressions, tried in order with `search`; an
invalid pattern raises `re.error` at start-up). Named groups `target`, `action`, `user`
(→ `user_name`), `via` fill the columns; any other named group goes to `extra`. Default:

```text
^(?P<target>.+?) (?P<action>changed|added|removed) by (?P<user>\S+)(?: (?P<detail>.+))?$
```

It matches the forms the specification names (`… changed by <user>`, `… added by …`,
`… removed by …`). The `user` token is stored exactly as sent: if RouterOS 7 appends session
details to it, adjust the pattern on site (e.g. with a `via` group) instead of the code.

## 4. Generic parser (`parsers/generic.py`)

`messages` only: syslog PRI, RFC 5424 or ISO/BSD timestamp, host, `tag:` program, text.
Used for unknown senders without `devname=`/`logid=` and for configured `generic` sources.
Changes in B1: the row now always has `subtype` (`NULL`), like the other parsers; a header time
outside the clock skew (or not representable in UTC — it used to raise `OverflowError` out of
`parse_line`) is not used (`generic.time`, `extra.device_time`).

## 5. Throughput

Measured with `parse_line` (registry + parser + `Parser.parse` completion, no database) on the
development VM: Intel Xeon @ 2.10 GHz, Python 3.12.13, Linux, while other jobs used the other
cores (load average ≈ 2 on 4 cores); best of 5 runs of 20,000 lines, one core, after the review
fixes (clock-skew check, glued/empty-value/backslash handling, MikroTik header readings):

| Input | Lines/s | µs/line |
|---|---|---|
| FortiGate traffic (sample line 7, 49 fields) | 30,900 | 32.4 |
| FortiGate interim (sample line 3) | 31,900 | 31.4 |
| FortiGate, all 7 sample lines | 30,700 | 32.6 |
| FortiGate, unknown sender (content detection) | 28,700 | 34.9 |
| MikroTik DHCP (sample, wire format) | 56,400 | 17.7 |
| MikroTik firewall (sample, wire format) | 43,700 | 22.9 |
| MikroTik, all 11 sample lines | 50,500 | 19.8 |

An interleaved A/B run against the code before the review fixes (same machine, same minute)
measured FortiGate ≈ 3–5 % slower (≈ 1 µs/line: the clock-skew check ≈ 0.6 µs, issue
tracking) and MikroTik ≈ 12 % slower (header readings, evidence search). Where a FortiGate line
goes: tokenizer ≈ 13 µs, column mapping ≈ 4 µs, `Parser.parse` completion ≈ 4 µs, time ≈ 1.6 µs.

Target: the whole ingest ≥ 10,000 messages/s on the 4-core server (spec 7.2), i.e. the parser
alone ≥ 30,000 lines/s per core. FortiGate is **at** that target, not above it: if the
ingest service (B2) measures less than 10,000 messages/s end to end, parse in several worker
processes (the parsers are stateless) before tuning the parsers further. `test_throughput_floor` (FortiGate and MikroTik) only fails
below 3,000 lines/s, to catch a pathological regression without being flaky on CI runners.
64 KB pathological inputs (long runs of quotes, `=`, spaces, firewall fragments) parse in
< 50 ms each.

## 6. Fixtures and tests

| Fixture | Content |
|---|---|
| `tests/fixtures/format/` | **Synthetic format samples, modelled on FortiOS 7.6 / RouterOS 7 output; all values fictional**: 7 FortiGate lines (`fortigate-sample.log`), 11 MikroTik `/log print` lines (`mikrotik-log-print-sample.txt`); bytes pinned by `SHA256SUMS` |
| `tests/fixtures/synthetic/fortigate-synthetic.log` | **synthetic, verify on site**: webfilter blocked/passthrough, DNS query/response/blocked, IPS, virus, admin login ok/failed, configuration change, VPN, user, `eventtime` in µs and s, no `eventtime`, no `tz`, relay prefix, an unmapped subtype |
| `tests/fixtures/synthetic/mikrotik-wire-synthetic.log` | **synthetic, verify on site**: the 11 sample messages in the approximate wire format, header variants (also topics with `:`, a tag or a single topic word before the message), and every message family |

Tests: `ingest/tests/test_fortigate*.py`, `test_mikrotik*.py`, `test_parser_contract.py`
(golden values for every format-sample line, tokenizer edge cases, hypothesis properties: never
raises, one complete `messages` row, unknown keys preserved, tokenizer round trip and fast path =
reference, every row loadable, uniform columns per table, any device time from year 1 to 9999
with any offset in every parser, DHCP/ARP/firewall/configuration lines in every header layout
with and without `host_names`).

## 7. Sahada doğrulanacaklar / Verify on site

1. **MikroTik wire format**: PRI/facility, ISO-8601 time with or without
   milliseconds and offset, host name present or not, topics token present (`add-topics…`
   option name, `docs/DEVICE-SETUP.md`), separator `,`. Once seen on a device, add a format
   sample with fictional values (`tests/fixtures/format/mikrotik-wire-sample.log`, +
   `SHA256SUMS`; never a device's real lines) and replace the synthetic wire lines.
2. **RouterOS time offset:** does `syslog-time-format=iso8601` include the offset? Without it
   every MikroTik row uses the receive time (`extra.device_time` holds the device text).
3. **MikroTik login failure** wording (`login failure for user … from … via …`).
4. **MikroTik configuration changes** (`… changed|added|removed by <user>` in `system,info`);
   RouterOS 7 may append session details to the user token → adjust `config_patterns`.
5. **Wi-Fi / CAPsMAN** connect/disconnect messages (`<mac>@<interface> connected, …`).
6. **DHCP** `offering lease … without success` and `failed to give out IP address … pool <x> is
   empty` wording; whether RouterOS ever logs `assigned … to` (tolerated).
7. **Firewall log** layout beyond the two format-sample lines (TCP SYN): comma after
   `connection-state` when there is no `src-mac`, ICMP `(type x, code y)`, TCP flags, NAT part,
   IPv6 addresses (not expected on this IPv4 network; bare IPv6 without port, bracketed IPv6 with
   port accepted).
8. **FortiGate escaping:** whether FortiOS escapes `\` inside quoted values (the tokenizer reads
   `\\` as one backslash and `\"` as a quote; other backslashes stay). Lines that only parse with
   literal backslashes carry `messages.extra.backslashes = "literal"`: count them on site
   (`SELECT count(*) FROM messages WHERE extra ? 'backslashes'`); if FortiOS does not escape,
   they will appear for every Windows path ending in `\`.
9. **FortiGate `eventtime`** on other FortiOS versions (µs/s accepted; tests fail loudly if a
   sample line's `eventtime` and `date`/`time`/`tz` disagree).
10. **FortiGate interim logids:** only `0000000020` (forward) is known; local-traffic interim
    logs, if any, need their logid added to `interim_logids`.
11. **FortiGate UTM/event fields** of the synthetic lines: webfilter (`cat`, `catdesc`,
    `eventtype` `ftgd_blk`/`ftgd_allow`, `profile`), DNS (`qname`, `qtype`, the **`ipaddr` list
    format**, `eventtype`), IPS, virus, admin login (`logdesc` "Admin login successful|failed",
    `user`, `ui`, `status`), configuration change (`logdesc`, `cfgpath`, `cfgobj`, `cfgattr`),
    VPN/user events. All logids in the synthetic file are placeholders.
12. **Sender address of the core router** (the address its `[[sources]]` entry names; a router
    with several VLAN interfaces may use another one — check with the first capture) — a sender
    mapping matter for B2.
13. **Clock skew limit** (`max_clock_skew`, default 1 day): device times further from the
    receive time are not used (`<parser>.time`). One day only catches broken clocks; a device
    a few hours off (wrong time zone) still uses its own time — watch `time - time_received`
    per source (a clock-check matter, spec 2.4) and decide with the owner whether to tighten it.
14. **MikroTik device identities** (`host_names`): configure them once the wire format is
    known if lines arrive without a topics token — otherwise the host name / firewall prefix /
    configuration target can be ambiguous (§3.1).
15. **MikroTik topics token** variants: `dhcp,info:` (trailing colon) is accepted; a single
    word (`dhcp`, `script:`) is not taken as topics (evidence is still found, `extra.prefix`).

## 8. Notes for the ingest service (B2) and later packages

- Build the parsers from settings when the service starts and pass them to `parse_line`:
  `parsers = build_parsers(interim_logids=…, config_patterns=…, server_vlan_regex=get_setting(
  session, "dhcp.server_vlan_regex"), host_names=…, max_clock_skew=…)`, then
  `parse_line(record, position, parsers)` (default: `PARSERS`, the shipped defaults). Settings
  for all but the VLAN rule do not exist yet (proposed keys `fortigate.interim_logids`,
  `mikrotik.config_patterns`, `mikrotik.host_names`, `ingest.max_clock_skew_seconds`; see the
  B1 report).
- `fg_app` holds app-control **and** SSL rows: filter on `extra->>'subtype'` (§2.3).
- Count every `outcome.error` in `parse_failures`, including the new patterns `*.time`,
  `*.glued` (§1); rows returned with an error are still loaded.
- Pass `columns=` explicitly to `copy_rows` (all columns of the table except `id`) or rely on
  the uniform rows of the built-in parsers; a third-party parser may emit fewer keys.
- The parsers are stateless per line (only small bounded caches), safe to call from several
  worker processes.
