# Veri modeli / Data model

> Durum: MVP (sürüm 0.1.0). Kaynak: `db/src/arcilogger_db/models.py` ve
> `db/src/arcilogger_db/migrations/versions/` (`v0001_baseline`, `v0006_lookup_reports` …
> `v0022_lease_moves`). Bir test, modellerle migrasyonların birebir aynı olduğunu
> her çalıştırmada doğrular.

## Özet (TR)

- Veritabanındaki her şey **türetilmiş** veridir: ham spool dosyalarından ve mühürlü arşivden
  (vault) yeniden üretilebilir. Yasal delil veritabanı değil, mühürlü ham dosyalardır.
- PostgreSQL 16; TimescaleDB kuruluysa zaman serisi tabloları hypertable olur (1 günlük parçalar,
  7 günden eski parçalar sıkıştırılır). TimescaleDB yoksa aynı tablolar PostgreSQL'in kendi
  **bölümlemesiyle** (`PARTITION BY RANGE (time)`) oluşturulur: revizyon `0021_db_window`'dan
  sonra, içinde bulunulan ayın aylık bölümünün bittiği andan başlayarak **günlük** bölümler;
  her biri TimescaleDB'nin parçaları gibi bir **UTC günüdür** (UTC gece yarısından UTC gece
  yarısına; yerel saatle 03:00'ten 03:00'e). Önceki sürümlerin aylık bölümleri (sınırları yerel
  gece yarısında) bölünmez: ayın son UTC gününün aylık bölümden sonra kalan üç saati, o günün
  adıyla kısa bir günlük bölüm olur, böylece hiçbir satır varsayılan bölüme düşmez. Eski
  veriler satır satır silinmez, bölüm bölüm düşürülür.
- Her ayrıştırılmış satır, ham satırına geri götüren bir **ham referans** taşır:
  `(source, raw_day, raw_line, raw_offset)`.
- Kasadaki mühürlü dosyalar `xz` biçimindedir (`<kaynak>.log.xz`, `audit.jsonl.xz`; ayar
  `seal.compression`, varsayılan `xz`): her biri 16 MiB açılmış veri taşıyan bağımsız xz
  akışları, bu yüzden tek bir satır yalnızca kendi akışı açılarak gösterilir. Bu biçimden önce
  mühürlenen günler `.gz` olarak kalır ve hiçbir zaman yeniden sıkıştırılmaz; iki biçim de her
  zaman okunur. Ölçümler aşağıda, "Sealed files" bölümündedir.
- Veritabanı arşivden **daha kısa** süre tutar: log kayıtlarının **son 30 gününü** (ayar
  `db.retention_days`, varsayılan 30, en az 7; revizyon `0021_db_window`'dan önce 400). Worker
  işi `db.window` her gün 04:00'ten sonra daha eski günleri bölüm bölüm (TimescaleDB'de parça
  parça) düşürür; henüz mühürlenmemiş bir gün (satırlarının uzandığı önceki iki günle birlikte)
  ve etkin bir arşiv yüklemesinin günleri hiçbir zaman düşürülmez. Bir şey düşüren her adım
  denetim kaydına bir `db.window` girdisi yazar: silmenin kaydı budur (veritabanındaki satırlar
  çalışma kopyasıdır, mühürlü asılları kasada kalır). Daha eski bir gün
  gerektiğinde mühürlü arşivden **yeniden yüklenir** ("arşivden yeniden yükle", tablo
  `archive_reloads`; satırları yalnızca ArciIngest yazar) ve `db.reload_keep_days` (varsayılan
  7) gün veritabanında kalır. Bir 5651 sorgusu sorulan andan **12 gün** geriye okur (varsayılan
  ayarlarla: FortiGate kirasının günlüğe yazılan süresi için, `dhcp.fortigate_use_logged_lease`,
  günleri bir yeniden yüklemenin tuttuğu kadar, sonraki günle 14 gün; bu ayar kapalıyken 8 gün:
  7 günlük DHCP kira geriye bakışı, en uzun oturum 1 gün, tolerans ve log payı birer dakika;
  `lookup_days`), bu yüzden 30 günlük pencereyle yaklaşık **18 günden eski** bir anın sorgusu
  yeniden yükleme ister. Sorgu kendi günlerinin dışındaki hiçbir log satırını okumaz: daha
  uzağa bakacak bir denetim (100 günlük kaçırılmış taşınma sınırı, daha uzun bir kira) yalnızca
  sorgunun günlerini okur ve yanıt bunu söyler (`lookback_clamped_to_window`). Sorguların 30 gün
  geriye yeniden yüklemesiz çalışmasını isteyen yönetici `db.retention_days` değerini **42**
  yapabilir (30 + 12; veritabanı da o kadar büyür).
  Kullanım özetleri (`usage_*` tabloları, `domain_categories`) ise
  her gün 03:30'dan sonra worker işi `usage.retention` ile silinir:
  `usage.retention_days` (varsayılan 400) günden eski olanlar; biten yeniden hesaplama istekleri
  (`usage_rebuilds`) 90 gün sonra. Arşiv en az 730 gün (varsayılan 760 gün) tutar; arşiv imhası
  varsayılan olarak kapalıdır. Ayrıntı: "Retention" bölümü ve
  [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md).
- Lisans tabloları (`license_installation`, `license_files`, `license_events`; revizyon
  `0014_license`) yalnızca eklenerek yazılır ve hiçbir zaman silinmez. Lisansın kendisi bu
  tablolar değil, her değerlendirmede imzası yeniden doğrulanan lisans dosyasıdır
  ([LISANS.md](LISANS.md)).
- Tarayıcıdan yönetim istekleri (`admin_requests`; revizyon `0015_admin_requests`, ADR-0014)
  hiçbir gizli bilgi tutmaz: adı parola, müşteri numarası ya da belirteç gibi olan bir alanda
  yalnızca "kayıtlı / kayıtlı değil" bilgisi bulunabilir; bunu veritabanının kendisi denetler.
- Arama (revizyon `0016_search`, [ADR-0015](adr/0015-search-live-sources.md)): `search_counts`
  her saat, spool dosyası, kayıt türü, tablo, eylem ve özel /24 ağı için **kesin** satır
  sayısını tutar; ingest bunu satırlarla aynı işlemde yazar, böylece geniş bir aramanın
  istatistikleri milyonlarca satır sayılmadan gelir. `saved_searches` kişisel kayıtlı
  aramalardır. `ingest_offsets.counts_from_line` yükseltmeden önce yüklenmiş ve henüz
  sayılmamış dosyaları işaretler; `fg_web` üzerindeki `ix_fg_web_rhost_time` indeksi "bu alan adı
  ve alt alan adları" aramasını hızlandırır.
- FortiGate DHCP (revizyon `0017_fortigate_dhcp`, ADR-0016): `fg_dhcp`, FortiGate'in DHCP
  sunucusu satırlarını (ACK/RELEASE) tutan yeni zaman serisi tablosudur; bir satırın ne anlama
  geldiğine `logid` ve ayarlara bakarak worker karar verir. `fg_traffic.srcmac` oturum kaydının
  kaynak MAC adresidir (dizinsiz; kira kanıtı değildir). `leases.lease_seconds`, FortiGate'in
  kaydettiği kira süresidir (300–8.640.000 sn); `leases.source_table` artık `mt_dhcp`, `mt_arp`
  ya da `fg_dhcp` olabilir. Yükseltmeden önce yüklenmiş FortiGate DHCP satırları `fg_event`
  satırı olarak kalır, dönüştürülmez.
- İnternet çıkışları (revizyon `0018_nat_egresses`, ADR-0016): eski `nat.public_ips`,
  `nat.wan_transip` ve `lookup.port_hint` ayarları tek `nat.egresses` ayarına (varsayılan `{}`:
  tek bir çift NAT çıkışı varsayılır) dönüştürülür. Düzenlenemeyen üç durum satırı vardır:
  `nat.egresses_migration` (dönüşümün kaydı), `nat.egresses_changes` (her çıkışın son içerik
  değişikliği: zaman ve yazan) ve `leases.rollback_lowered` (`-Rollback` sırasında düşük güvene
  indirilen FortiGate kiraları; yeniden yükseltmede okunup silinir).
- Kaynak saatleri (revizyon `0019_source_clock_samples`, ADR-0016 D28): `source_clock_samples`
  her kaynak için satırların **alındığı** dakikada cihaz saatinin alma zamanından farkını
  (sıfırdan uzağa yuvarlanmış tam saniye; bir dakikanın altındaki fark 0) ve satır sayısını
  tutar. Ingest bunu satırlarla aynı işlemde yazar; `R20-lease:source-clock` uyarısı ve 5651
  sorgusunun saat denetimi saatleri geri ya da ileri atlayan bir cihaz saatini buradan ölçer.
- NAT adresi indeksi (revizyon `0020_fg_traffic_transip_time`): `fg_traffic` tablosunun yeni
  indeksi `ix_fg_traffic_transip_time (transip, time)`, 5651 sorgusunun bir NAT adresi için port
  olmadan sorduğu adımlara (kayıtlarda görülen adres denetimi; D15 denetiminin pencereden sonraki
  kuyruk adımları ve T'yi kapsayan oturum adımları; port olmadan tek bir adresle yapılan oturum
  araması da onu kullanabilir: aynı satırlar, aynı sırada) yalnızca o adresin aralıktaki
  satırlarını okutur; `(transip, transport, time)` indeksinde `time` üçüncü sütun olduğundan
  böyle bir adım adresin bölümdeki bütün indeks girdilerini yürüyordu. Milyon satır başına
  yaklaşık 31,5 MB yer tutar.
- Kira hareketleri (revizyon `0022_lease_moves`, issue #127): `leases.moves` (JSONB, NULL
  olabilir), kira işinin gördüğü ama uygulamadığı FortiGate ACK'lerini (kiranın MAC adresinin
  başka bir adres alması) kirayla birlikte saklar; 5651 sorgusunun kaçırılmış taşınma denetimi
  veritabanı penceresinden önceki kısmı bunlardan okur. Değer sınırlıdır (kira satırları hiç
  silinmez): yalnızca kiranın bitişinden (açık bir kirada en yeni hareketinden) önceki 100
  günün en çok 1.000 zamanı, en yeniler tutulur; iş bir zamanı attığında `truncated` `true`
  olur. Hareketler yalnızca olumlu kanıttır: eksik ya da kesilmiş bir liste bir hareketi
  kaçırır, yanlış bir şey söylemez. NULL kaydedilmemiş demektir (revizyondan önce yazılmış
  kiralar, ARP aralıkları); geriye dönük doldurma yoktur. Ayrıca kira işinin `leases.evidence`
  içine yazdığı her `fg_dhcp` öğesi satırının VDOM'unu `"vd"` anahtarında taşır (şema
  değişikliği yok). Geri alma (downgrade) yalnızca `leases.moves` sütununu kaldırır; ikisi de
  türetilmiş veridir, 5651 verisi kaybolmaz.

## Summary (EN)

Everything in the database is derived and rebuildable from the raw spool and the sealed vault;
the legal evidence is the sealed raw files. PostgreSQL 16, with optional TimescaleDB
(hypertables for the time-series tables); without it the time-series tables are natively
partitioned by UTC day, as TimescaleDB's chunks (by month, at local midnight, before revision
`0021_db_window`; those monthly partitions are never split). Every parsed row carries a raw
reference to its line. The database keeps less than the vault: the **last 30 days** of log rows (`db.retention_days`, default 30, at least 7; 400 before
revision `0021_db_window`), against at least 730 days (default 760) in the vault. The worker job
`db.window` drops older days daily after 04:00, whole partitions (TimescaleDB: chunks) at a time;
a day not sealed yet (with the two days before it, which its rows reach into) and the days of an
active archive reload are never dropped. An older day
comes back from the sealed vault on request ("arşivden yeniden yükle", `archive_reloads`; only
ArciIngest writes its rows) and stays `db.reload_keep_days` (default 7) days. Every step of the job
that drops something writes a `db.window` audit entry, the record of the deletion (the rows are
working copies; their sealed originals stay in the vault). A 5651 lookup reads **12 days** back
from the asked moment (with the default settings: for the logged lease time of a FortiGate lease,
`dhcp.fortigate_use_logged_lease`, its days reach as far back as one reload holds, 14 days with
the day after; without that setting 8 days: the 7-day DHCP lease look-back, the 1-day longest
session, a minute each of tolerance and log slack; `lookup_days`), so with the 30-day window a
lookup of a moment more than about **18 days** old needs a reload. A lookup reads no log row
outside its days: a check that would look further (the 100-day missed-move cap, a longer logged
lease) reads only the lookup's days, and the answer says so (`lookback_clamped_to_window`). An
administrator who wants 30 days of lookups without a reload can set `db.retention_days` to **42**
(30 + 12; the database grows accordingly). The usage rollups
(`usage_*` tables, `domain_categories`) are deleted daily after 03:30 by the worker job `usage.retention` once older than
`usage.retention_days` (default 400), finished rebuild requests (`usage_rebuilds`) after 90 days
(see "Retention"). The licence tables (`license_*`, revision `0014_license`) are append-only and
never deleted; the licence itself is the signed file they hold, verified again on every
evaluation ("Licence tables"). The requests of the browser administration (`admin_requests`,
revision `0015_admin_requests`, ADR-0014) hold no secret: a field named like a password, a
customer number or a token may only say whether a secret is set, which the database checks.
Search (revision `0016_search`, [ADR-0015](adr/0015-search-live-sources.md)) adds the exact hourly
summary table `search_counts` (written by ingest in the transaction of the rows), the personal
`saved_searches`, `ingest_offsets.counts_from_line` (files loaded before the upgrade and not
counted yet) and the expression index `ix_fg_web_rhost_time` for "this domain and its
subdomains". FortiGate DHCP (revision `0017_fortigate_dhcp`, ADR-0016) adds the time-series
table `fg_dhcp` (the FortiGate DHCP server's ACK/RELEASE lines; the worker decides what a row
means from its `logid` and the settings), `fg_traffic.srcmac` (the session record's source MAC;
not indexed, never lease evidence) and `leases.lease_seconds` (the lease time the FortiGate
logged, 300–8,640,000 s); `leases.source_table` is `mt_dhcp`, `mt_arp` or `fg_dhcp`. FortiGate
DHCP lines loaded before the upgrade stay `fg_event` rows; they are not converted. Internet exits
(revision `0018_nat_egresses`, ADR-0016) replace the settings `nat.public_ips`, `nat.wan_transip`
and `lookup.port_hint` with the one setting `nat.egresses` (default `{}`: one implicit double-NAT
exit) and add three status rows that are never edited: `nat.egresses_migration`,
`nat.egresses_changes` and `leases.rollback_lowered` ("Status rows" below). Source clocks
(revision `0019_source_clock_samples`, ADR-0016 D28) add `source_clock_samples`: per source and
**received** minute, the device clock's offset from the receive time (whole seconds rounded away
from zero, 0 under a minute) and its rows, written by ingest in the transaction of the rows; the
`R20-lease:source-clock` alert and a lookup's clock check measure a clock that jumped by hours
there. The NAT address index (revision `0020_fg_traffic_transip_time`) is `fg_traffic`'s new
index `ix_fg_traffic_transip_time (transip, time)`: a lookup's steps without a port on one NAT
address (the overlap probe, and the D15 check's tail after its window and its steps for sessions
spanning the moment; its window step stays in time order; a port-less session search on one
address may take it too, the same rows in the same order) read that address's rows of their range
only, where on `(transip, transport, time)` they walked every index entry of the address in the
partition; it takes about 31.5 MB per million rows. Lease moves (revision `0022_lease_moves`,
issue #127) add `leases.moves` (JSONB, nullable): the FortiGate ACKs of the lease's MAC for
another address that the lease job saw and did not apply, which a lookup's missed-move check
reads for the part before the database window. The value is bounded (lease rows are never
dropped): at most 1,000 times in the 100 days before the lease's end (its newest move while it
is open), the newest kept; `truncated` is `true` once the job dropped one. Moves are positive
evidence only: a value that lacks a move, truncated or not, misses it and says nothing wrong.
NULL means not recorded (leases written before the revision, ARP intervals); there is no
backfill. Every `fg_dhcp` element the lease job writes into `leases.evidence` also carries its
row's VDOM as `"vd"` (no schema change). The downgrade drops `leases.moves` only; both are
derived data, no 5651 data is lost.

## Conventions

| Rule | Detail |
|---|---|
| Times | `TIMESTAMP WITH TIME ZONE`, always aware; every connection uses `TimeZone=UTC` |
| Addresses | `INET` (IPs), `CIDR` (networks), `MACADDR` (PostgreSQL prints `00:00:5e:00:53:57`) — returned to Python as **strings** |
| Unknown fields | `extra JSONB` (nothing a parser does not map is thrown away) |
| Ids | `BIGINT GENERATED BY DEFAULT AS IDENTITY` |
| Names | Column names of spec 7.3 where the spec names them; SQL keywords avoided (`user` → `user_name`). Exception from the spec: `leases.start` / `leases."end"` — quote `"end"` in raw SQL |
| Constraint names | Deterministic naming convention (`pk_<table>`, `ix_<table>_<cols>`, `ck_<table>_<name>` …) |
| Encoding | The migration refuses a non-UTF8 database (Windows clusters default to WIN1252) |

## Raw reference (ham referans)

Every time-series row has:

| Column | Meaning |
|---|---|
| `source` | Configured source name = spool folder (e.g. `fortigate`, `core-r1`, `ap-<ip>`, `unknown-<ip>`) |
| `raw_day` | Site day (Europe/Istanbul **receive** day) of the spool file |
| `raw_line` | 1-based line number in `spool/<source>/<raw_day>.log` |
| `raw_offset` | Byte offset of the line start in that (uncompressed) file |
| `time_received` | Collector receive time (prefix of the raw line) |
| `sender` | Sender IP as seen by the collector |
| `time` | Device/event time in UTC (FortiGate `eventtime` preferred); equals `time_received` when the message has no usable device time (a time without offset is never guessed; it stays in `extra.device_time`) |

Sealing compresses `spool/<source>/<day>.log` byte for byte into
`vault/<YYYY>/<MM>/<DD>/<source>.log.xz` (`.log.gz` for days sealed before Wave 5), so **line
`raw_line` of the decompressed file is the same line** and `raw_offset` is still its byte offset.
Today's lines are read from the spool, older ones from the sealed file; either way the reference
can be cited in an evidence package.

### Sealed files

- **Formats.** `seal.compression` (`"xz"` default, or `"gzip"`) applies to every file sealed from
  then on: `<source>.log.xz` and `audit.jsonl.xz`. A sealed day is never re-encoded, so days sealed
  as `.gz` (`gzip -n`, `seal.gzip_level`) stay as they are and both formats are read forever. The
  manifest's `path` suffix tells the format (schema `arcilogger.manifest/1` unchanged); a day
  folder holding one source in both formats is the error `sealed_file_ambiguous`.
- **Seekable xz.** The file is a concatenation of independent xz streams of 16 MiB (16,777,216
  bytes) of uncompressed input each, the last one less (`FORMAT_XZ`, `CHECK_CRC64`, preset
  `seal.xz_preset`, no stream padding), compressed on `seal.xz_threads` threads and written in
  order: the bytes do not depend on the thread count. Readers find a stream from the xz index in
  the stream footers (no sidecar file); every xz tool and 7-Zip decompress the whole concatenation.
- **Line view.** A cited xz line is shown from its own stream. Its line number is checked against
  the line feeds before that stream, counted once per file (in memory, never in `vault/`): the
  first view of a file decompresses the streams before the line, later views one stream. gzip
  cannot seek: the first view decompresses up to the line and leaves checkpoints every 64 MiB.
- **Measurements (H5)** with `tools/bench/seal_compression.py` on load-generator days of 1 M lines
  (645 MB) and 5 M lines (3.23 GB), cloud container with 4 vCPUs and 16 GB (some 5 M runs shared
  the machine with test runs, so their times are on the slow side). "5 GB day" is the sealing
  time extrapolated from the measured speed, against half of the Seal task's limit (PT2H: 3 600 s).

  | Format (5 M lines) | Size of raw | Sealing | Peak memory | 5 GB day |
  |---|---|---|---|---|
  | gzip -9 | 6.06 % (196 MB) | 47 s | 24 MB | 72 s |
  | xz 6, 1 thread | 4.44 % (143 MB) | 750 s | 154 MB | 1 162 s |
  | **xz 6, 2 threads (default)** | 4.44 % (143 MB) | 473 s | 269 MB | 733 s |
  | xz 9, 1 thread | 4.45 % (144 MB) | 860 s | 288 MB | 1 332 s |
  | xz 9, 2 threads | 4.45 % (144 MB) | 478 s | 511 MB | 741 s |

  On 1 M lines the ratios are the same; xz 6 on 2 threads seals at 8.3 MB/s (5 GB day: 603 s).
  Preset 9 gains nothing on logs (4.45 % against 4.44 %) for twice the memory, so the defaults
  stay preset 6 on 2 threads: a 5 GB day takes about 12 minutes, a fifth of the limit.

  Showing one cited line (`/raw`, the code the API runs) on the 5 M line day:

  | Format | First view, middle line | First view, last line | Later views of the file |
  |---|---|---|---|
  | gzip -9 | 3.4 s | 6.4 s | 0.12 s (checkpoints) |
  | xz 6 | 7.0 s | 14.1 s | 0.06 s (one stream) |

  The first xz view of a file counts the line feeds of the streams before the line (the line
  number is always checked); xz decompresses about half as fast as gzip, so that first view
  costs about twice as much. Counting on every view instead of keeping the counts would cost the
  first-view time each time.

## Time-series tables

Primary key `(time, id)` on all of them: on a TimescaleDB hypertable and on a natively
partitioned table every unique index must contain the partitioning column `time`. Insert
through the table itself (never into a partition), so the identity column is used.

| Table | Content | Indexes besides the primary key |
|---|---|---|
| `messages` | Every received message (lightweight): severity, facility, program/topics, host, type, subtype, short text, `parse_error` | (source, time), (type, time) |
| `fg_traffic` | FortiGate traffic (incl. interim `0000000020`, `is_interim`), source NAT `transip:transport`; `srcmac` (`MACADDR`, revision `0017_fortigate_dhcp`): "MAC address associated with the Source IP", filled only where the FortiGate's device identification is on, NULL in older rows; never lease evidence (ADR-0016, D10) | (srcip, time), (transip, transport, time), (transip, time) (`ix_fg_traffic_transip_time`, revision `0020_fg_traffic_transip_time`: the lookup's steps without a port on one NAT address, in the overlap probe `_Probe.seen_overlap` and the tail and spanning-sessions steps of the D15 check `_Lookup._carried`; a port-less search on one address may take it too (the same rows, in the same order); about 31.5 MB per million rows), (sessionid), (dstip, dstport, time) — none on `srcmac` (the search field `srcmac:` scans) |
| `fg_web` | Web filter: hostname, url (≤ 2,000 chars), cat, catdesc, action, eventtype, profile | (sessionid), (srcip, time), `ix_fg_web_rhost_time` (reverse(lower(hostname)), time) — the search field `domain:` (revision `0016_search`) |
| `fg_dns` | DNS: qname, qtype, answers `INET[]`, catdesc, action | (srcip, time), (qname, time) |
| `fg_app` | Application control: app, appid, appcat, hostname, url | (sessionid), (srcip, time) |
| `fg_ips` / `fg_virus` | IPS and antivirus events | (srcip, time) |
| `fg_event` | Admin logins, configuration changes (`cfgpath`, `cfgobj`, `cfgattr`), system/perf, VPN, user | (subtype, time) |
| `fg_dhcp` | FortiGate DHCP server events (revision `0017_fortigate_dhcp`, ADR-0016): an `event` line with `ip`, `mac` and `dhcp_msg` that convert (else it stays `fg_event`, parse failure `fortigate.dhcp`): logid, subtype, level, logdesc, dhcp_msg, interface (the DHCP server's interface), ip, mac (both NOT NULL), hostname, lease (`BIGINT`, seconds as logged, NULL when absent; verify on site), msg, `device_time` (`true`: `time` is the FortiGate's event time; `false`: the receive time, never lease evidence). What a row means (ACK, RELEASE) is decided by the worker from its `logid` (`dhcp.fortigate_logids`), so a settings change re-reads old rows; lines loaded before the upgrade are not converted | (ip, time), (mac, time) |
| `mt_dhcp` | `assigned`/`deassigned`: server, vlan, ip, mac, hostname | (ip, time), (mac, time) |
| `mt_arp` | Hourly `ARP-LOG ip mac interface` | (ip, time), (mac, time) |
| `mt_fw` | MikroTik firewall: prefix, chain, in/out interface, state, src-mac, proto, src:sport → dst:dport, len | (src, time), (dst, time) |
| `mt_account` | login / logout / login_failure, user, ip, via | (ip, time) |
| `mt_interface` | link up/down per interface | (source, time) |
| `mt_config` | `… changed/added/removed by <user>` (patterns configurable, verify on site) | (source, time) |
| `mt_event` | Everything else that matters: `heartbeat` (`ARCI-HEARTBEAT <identity>`), Wi-Fi, critical/error/warning, `dhcp_pool_empty` | (event, time), (source, time) |

Column lists per table: `models.py` (each column documented). Columns the specification lists
but which are not in a table go to `extra`.

## Reference and derived tables

| Table | Key | Content |
|---|---|---|
| `sources` | `name` | Type (fortigate/mikrotik/generic), address, enabled, expected, heartbeat minutes, `unknown` (sender not configured → alert "yeni log kaynağı"), first/last seen |
| `parse_failures` | id; unique (source, day, pattern) | Count of unparsed lines per source/day/pattern, first/last seen, **one example** raw reference (`example_line`, `example_offset`), last error |
| `ingest_offsets` | (source, day) | Ingest resume point: `byte_offset` and `line_number` of complete lines loaded; `finished_at` when the day is done; `counts_from_line` (revision `0016_search`): NULL when every loaded line of the file is in `search_counts`, else the lines before it were loaded before the upgrade and wait for the worker job `search.counts_backfill`, which recounts the finished file and clears it (the migration sets `line_number + 1` on every file with loaded lines); `0` while an archive reload resets the file (offset 0, its rows deleted in steps, W5-G-25) |
| `vlans` | `subnet` | The site's VLANs and subnets (none are shipped): `vlan_id` (unique, NULL for a network without one, e.g. a transit network), `name_tr`, `purpose`, `dhcp_server` (e.g. `dhcp60`), `department_id`, `guest` |
| `departments` | id | The site's departments, entered by its administrator (none are shipped, see "Seed data") |
| `persons` | id | Name, department, title, e-mail, employee number (unique), active (persons are deactivated, never deleted), `usage_excluded` (left out of person-level usage views and reports; revision `0010_usage`) |
| `devices` | `mac` | First/last seen, last IP/VLAN, `hostnames[]`, vendor, `randomized`, type (pc, phone, printer, ip_phone, ap, server, camera, other), `person_id` (the **current** owner, mirrored from `device_assignments`), `infrastructure`, notes, `tags[]`, label `name` |
| `device_assignments` | id; partial unique `mac` where `valid_to` IS NULL | Assignment history (revision `0008_device_assignments`, ADR-0008): `mac` → `person_id` for `[valid_from, valid_to)` (`valid_to` NULL = current), `assigned_by`, `note` (given when the row is opened; an unassign takes none), `backfilled` (made by the migration from an older link; `valid_from` = the device's first sighting). No two intervals of one MAC overlap — exclusion constraint `ex_device_assignments_no_overlap` (`btree_gist`) or, where the extension cannot be created, a trigger with the same rule. Rows are never deleted; foreign keys RESTRICT. The 5651 person step reads the row valid at T. Not a time-series table (small) |
| `leases` | id | IP ↔ MAC interval: vlan, server (a MikroTik DHCP server name, or the FortiGate interface of an `fg_dhcp` lease), hostname, `start`, `"end"` (NULL = open), `end_reason` (dhcp, timeout, superseded, arp, open), confidence (high/medium/low), `source_table` (`mt_dhcp`, `mt_arp` or `fg_dhcp`), `last_evidence`, `lease_seconds` (revision `0017_fortigate_dhcp`: the lease time a FortiGate ACK logged, when usable; NULL for every other lease), `evidence` (JSON list of `arcilogger_worker.lookup.EvidenceRef.to_dict()` objects: `{"table", "row_id", "time", "raw": {"source", "day", "line", "offset"} \| null, "summary"}`; an `fg_dhcp` element written from revision `0022_lease_moves` on also has `"vd"`, the VDOM of its row, `null` without one, which `EvidenceRef` ignores), `moves` (revision `0022_lease_moves`, JSONB, nullable, no default, no index, never loaded with the row: the FortiGate ACKs of the lease's MAC for another address that the lease job saw and did not apply, `{"truncated": bool, "groups": [{"ip", "interface", "vd", "source", "logid", "dhcp_msg", "times": [epoch µs, …]}, …]}`; at most 1,000 times in the 100 days before `"end"` (an open lease: before its newest move), the newest; `truncated` true once the job dropped one; NULL: not recorded, `"groups": []`: recorded, none; positive evidence only for the missed-move check). Checks: `end ≥ start`; `end_reason = 'open'` exactly when `end` is NULL; `ck_leases_source_table` (the three tables); `ck_leases_lease_seconds` (NULL or 300–8,640,000, the FortiOS `lease-time` range). Indexes (ip, start), (mac, start), `ix_leases_source_table_end` (source_table, "end") (open and recently ended leases per table, revision `0017_fortigate_dhcp`). The downgrade of that revision deletes the `fg_dhcp` leases first (the previous code cannot read them; they are rebuildable); that of `0022_lease_moves` drops `moves` only (the `"vd"` keys stay, the previous code ignores them) |
| `job_runs` | id | Worker job runs: job, started/finished, status (running/ok/failed/skipped), details |
| `settings` | `key` | Canonical-JSON value (no floats: every change is audited), `updated_at`, `updated_by` (`:seed` while it is the shipped default, else the admin's user name; `:system`, `:migration` for internal keys — `:` never occurs in a user name). Besides the keys of `DEFAULT_SETTINGS` it holds the **status rows** below, which are not settings: never listed by `GET /settings`, never editable (`settings set` refuses them) |
| `users` | id; unique `username` | Lower-case ASCII user name, display name, role (admin/auditor/hr/viewer), Argon2id hash, `must_change_password`, encrypted TOTP secret, `totp_enabled`, `totp_last_counter` (replay guard), failed attempts, `locked_until`, last login, active |
| `user_recovery_codes` | id | Hashed one-time recovery codes |
| `user_sessions` | `token_hash` | SHA-256 of the session cookie token; created, last seen, `expires_absolute`, ip, user agent, `mfa_passed`, `csrf_token_hash`, `revoked_at`, `reauthenticated_at` |
| `privacy_notice_acceptances` | id; unique (user, version) | KVKK notice acceptance |
| `audit_log` | id; unique `hash`, unique `prev_hash` | Hash-chained audit log (spec 8.5), see below |
| `lookups` | id | 5651 lookup: user, **makam** (`authority`), **dosya/evrak no** (`file_no`), **talep tarihi** (`request_date`), **gerekçe** (`reason`) — all four required (spec 12.3 page 6; NOT NULL, text not blank), `query` (JSON), `outcome` (JSON), mode, result count |
| `lookup_reports` | id; unique `lookup_id` | The stored official 5651 report of a lookup (revision `0006_lookup_reports`, docs/RAPOR.md): user, `path` (relative to `exports/`, `reports/ARCILOGGER-5651-rapor-<id>.pdf`), `sha256`, `size`, `pages`, `version`, `inputs` (everything the PDF was rendered from), `timestamp_status` / `timestamp` (the optional time stamp of the PDF's hash), `download_count`. Rendered once, never rendered again; no expiry yet |
| `evidence_packages` | id | Evidence ZIP in `exports/`: scope (JSON: content, days, sources, extract lines, lookup, reference, `estimated_bytes` — the size estimate used by the request limits), path (file name, empty until ready), SHA-256, size, password protected (the password is never stored), status (queued/building/ready/failed/expired/deleted), error, expiry (`evidence.keep_days`), download count, `package_name` (id in `paket.json`), `index_sha256`, `verifier_sha256` (SHA-256 of the package's `verify.py`), `legal_grade`, `finished_at` (revision `0007_evidence_seals`; `docs/KANIT-PAKETI.md`) |
| `evidence_downloads` | id | Each download (who, when, IP) |
| `seal_verifications` | id; index (day, finished_at) | "Doğrula" on the sealing page: day, started/finished, user, `deep`, outcome (ok/failed/timeout/error), errors, warnings, details. The scheduled verifications stay in `state/verify` (the API may not write there) |
| `audit_imports` | `key` | Events of the scheduled sealing/retention tasks already copied into `audit_log` (`seal:<day>:sealed`, `seal:<day>:attempt:<n>`, `retention:<record>`, `retention:<record>:incomplete`, `retention:alert:<sha256 of the alert line>`) and of the browser administration's journals (`admin:<request>:<state>`, `admin-secret:<line>:<name>`, `admin-system:<line>`) and the entry id: each event is imported once |
| `admin_requests` | id (32 hex); index (channel, state, created_at), (kind, created_at DESC), (section) where not terminal | Requests of the browser administration (ADR-0014, revision `0015_admin_requests`): `kind` (`config.apply`/`revert`/`cancel`, `action.seal`/`credit`/`tsa_test`/`replica_test`, `worker.test_email`/`lease_rebuild`), `section`, `channel` (`agent`: a signed request file for ArciAdmin; `worker`: taken by the worker job `admin.requests` with `FOR UPDATE SKIP LOCKED`), `state` and `terminal` (checked against each other), the requester (`requested_by_id`: users, SET NULL; `requested_by`: the name), `expires_at`, `effective_at`, `params` and `result` (never a secret: a key named like `password`, `customer` or `token` must hold a masked secret `{"secret": true, …}`, checked by the database), `error_code`, `rolled_back`, `audit_id` (the `admin.request` entry) |
| `alerts` | id | time (when it happened), rule, severity, state (open/acknowledged/resolved/dismissed), title, details, `dedup_key` (at most one *open* alert per key), `created_at` (when the row was written), `resolved_at`; written by ingest, correlation and the alert jobs (docs/ALERTS.md), resolved automatically by the check that owns the key (`details.auto_resolved`); a person changes the state on the Security page (`alert_events`) |
| `alert_deliveries` | id; unique (alert_id, kind, recipient) | Alert e-mail per recipient (ADR-0010): kind (new/resolved), status (queued/sent/failed/suppressed), attempts, last error (never a password), next attempt, `message_id` of the e-mail that carried it, created/sent |
| `usage_days` | `day` | Usage rollups (revision `0010_usage`, ADR-0011, [USAGE.md](USAGE.md)): one row per rolled-up site day — status (`partial` today, `final` after its late window), computed at, `computed_until`, `inputs_hash` (engine version, `usage.*` settings, suffix list, ingest progress of the day, taken before the logs are read), `attribution_hash` (who every source address belonged to when), the day's source `ips`, run details. The re-attribution job compares the hashes with the current state |
| `usage_daily` | (day, subject, off_hours) | Per subject (`person:<id>`, `device:<mac>`, `ip:<addr>`) and day, split into work hours / off hours: kind, person id, MAC, IP, VLAN (most online minutes, else of the blocked attempts and URLs), online/active seconds, download/upload bytes, sessions, blocked attempts, first/last activity, `timeline` (runs of active minutes with their dominant domain). Index (subject, day) |
| `usage_device_daily` | (day, mac, subject, off_hours) | The same per device (MAC) and subject (per-device breakdown of a person), main IP and VLAN (most online minutes, else of the blocked attempts and URLs), `top_domains` (≤ 20); `blocked` counts every attempt of the device (a device with blocked attempts only has a row too) |
| `usage_domain_daily` | (day, subject, domain, off_hours) | Per subject, registrable domain (or FortiGuard service, or address: `kind`) and day: estimated `seconds` (share of the active minutes), `raw_seconds`, bytes, sessions, `background`, `hosts` (`{hostname: [seconds, bytes, sessions]}`). Indexes (domain, day), (subject, day) |
| `usage_app_daily` | (day, subject, app, off_hours) | Per FortiGate application: category, seconds, bytes, sessions |
| `usage_hourly` | (hour, subject, off_hours) | Per subject and hour: online/active seconds, bytes, top domain (heat map, hourly tables, part-day report windows). Indexes (day), (subject, hour) |
| `usage_blocked` | id | Every blocked attempt (web filter, DNS filter, application control): time, subject, MAC, source, action, domain, host name, category, URL, application; at most `usage.blocked_per_day` per subject and day |
| `usage_urls` | id | URLs of the web filter log per subject and day: host name, domain, hits, first/last seen, category, blocked; the newest `usage.urls_per_day` |
| `domain_categories` | `domain` | FortiGuard category per registrable domain (web filter `catdesc`, else DNS filter), number, source, hits, `last_day` (an older day never overwrites a newer category) |
| `usage_rebuilds` | id | Rebuild requests of `POST /api/v1/usage/rebuild`: range, status (queued/running/done/failed), days done, error, requested by; run by the worker job `usage.requests` |
| `alert_events` | id; index (alert_id, at), (at) | Every change of an alert's state (`from_state` → `to_state`) and every note (`text`, 1–2000 characters) with `actor` (user name, or `system` for the checks' automatic resolution and re-opening, the reason code then in `text`), `actor_id` (users, SET NULL) and `at` (revision `0012_alert_events`, ADR-0010 amendment). Written only by `arcilogger_worker.alerts.store`; the audit log gets `alert.state` / `alert.note` (the note's length only) |
| `report_schedules` | id | Scheduled usage reports (revision `0011_reports`, ADR-0012): name, kind (end_of_day/full_day/weekly/monthly), `enabled`, `time` (HH:MM, site time), `weekday` (weekly) / `monthday` (monthly), `recipients`, `formats` (`email_html`, `pdf`, `xlsx`), `person_level` (default true: names), `pseudonymize`, `next_run_at`, `updated_by`. The four shipped schedules are inserted once by the migration, disabled |
| `search_counts` | id; unique `uq_search_counts_key` (hour, source, raw_day, kind, tbl, action, net) `NULLS NOT DISTINCT`; index (source, hour), (raw_day, source) | Exact hourly line counts of the search statistics (revision `0016_search`, ADR-0015, `arcilogger_db.searchcounts`): `hour` (`date_trunc('hour', time)`, device time, UTC), the spool file (`source`, `raw_day`), `kind` (`arcilogger_db.kinds.kind_of` of the `messages` row), `tbl` (the line's typed table, `''` without one), `action` (the typed row's, lower-cased), `net` (the /24 of the typed row's client address when it is private IPv4: 10/8, 172.16/12, 192.168/16, 100.64/10), `n` (> 0). One row per key, about 450 keys an hour for 150 users. The ingest loader adds a batch's counts in the transaction that loads its rows; `searchcounts.rebuild_stream` recounts one file from its rows (`arcilogger-db search-counts rebuild`); derived, like every table |
| `source_clock_samples` | (source, minute, seconds) | Device clock offsets by receive time (revision `0019_source_clock_samples`, ADR-0016 D28, `arcilogger_db.clocksamples`): `minute` (`date_trunc('minute', time_received)`, UTC), `seconds` (`time − time_received` in whole seconds rounded away from zero, `0` under a minute either way), `n` (> 0). Rows whose `time` is the receive time (no usable device time) are not counted. About one row a minute for a source on time. The ingest loader adds a batch's samples in the transaction that loads its rows; the `R20-lease:source-clock` alert reads a source's newest received minutes, a lookup the minutes received in [T − 1 h, T + 1 h] and (#82, `clocksamples.shifted_into`) the minutes received within a day and an hour of T whose offset, held by most of the minute's rows and beyond 120 s, puts their device times into [T − 1 h, T + 1 h]; `clocksamples.rebuild` counts a source's received range again from its rows; the upgrade counts the rows received in its last two hours (0.2 s on 26 hours of `messages` at 1 M lines a day: 83,271 rows, 242 keys), older lines have none; no command calls `rebuild` yet; derived, like every table |
| `archive_reloads` | id; index (status, id); unique `uq_archive_reloads_loading` (status) where `status = 'loading'` | Archive reloads ("arşivden yeniden yükle", revision `0021_db_window`, Wave 5 contract G4): sealed days older than the database window brought back from the vault on request. `requested_by` (user name), `requested_at`, `reason` (`lookup`/`search`: whose permission was needed), `lookup_id` (lookups, SET NULL; only with `reason = 'lookup'`), `first_day` ≤ `last_day` (site days, both included), `status` (`queued`, `loading`, `ready`, `failed`, `expired`; at most one `loading`), `bytes_total` (Σ `bytes_uncompressed` of the sealed files), `bytes_done` (Σ committed ingest offsets), `error_code` (exactly when `failed`), `ready_at` and `expires_at` (`ready_at` + `db.reload_keep_days`; both set once `ready`) |
| `saved_searches` | id; unique `uq_saved_searches_owner_name` (owner_id, lower(name)) | Personal saved searches (revision `0016_search`): `owner_id` (users, CASCADE), `name` (1–80 characters, unique per owner ignoring case), `query` (≤ 1,000 characters, parsed again on every run), the range as a preset `range_preset` (`15m`, `1h`, `24h`, `today`, `yesterday`, `7d`, `30d`) **or** a fixed `[range_from, range_to)`, never both (`ck_saved_searches_range`), created/updated, `last_run_at` |
| `report_runs` | id; index (schedule_id, id), index (status) | Every report run (scheduled or "run now"): period, kind (+ `custom`), the schedule's settings then (formats, recipients, person level), status (queued/running/sent/partial/failed), `files` (name, format, size, SHA-256 of each file in `exports/usage-reports/<id>/`), `deliveries` (per recipient: status, attempts, error, sent), `expired_at` (files deleted after `reports.keep_days`). Kept when the schedule is deleted |

**Status rows** (`settings` rows outside `DEFAULT_SETTINGS`; each written only by the code
named, read by the code named):

| Key | Written by | Value | Read by |
|---|---|---|---|
| `db.timescale` | the baseline (`:migration`); revision `0017_fortigate_dhcp` appends `fg_dhcp` to its table list (and records `compression_reason = "fg_dhcp: <reason>"` when compressing `fg_dhcp` is refused in `auto` mode; the downgrade removes both) | storage outcome (`enabled`, `mode`, `storage`, `hypertables` or `partitioned`, `compression`, `reason`, …), see "TimescaleDB" | `arcilogger-db check` and `arcilogger-db upgrade` (printed), the search (`arcilogger_api.search.windows.read_storage`: `storage`, `compression`), the tests |
| `nat.egresses_migration` | revision `0018_nat_egresses` (`:migration`), only when an administrator had set a legacy NAT key; the seed sets `audited` once it wrote the audit entry | `{"migration": "0018_nat_egresses", "from": {the three legacy values}, "new": <the converted exits>, "dropped": [{"value", "reason": "ipv6" \| "invalid" \| "reserved"}], "conversion_failed": bool, "audited": bool}` | the seed (one `settings.change` entry, target `setting:nat.egresses`), the lookup (whether an exit is still *as converted*, ADR-0016 D15), the alert `R20-nat:conversion-failed` |
| `nat.egresses_changes` | revision `0018_nat_egresses` (per converted exit, with the legacy rows' author and time) and `settings_admin.change_setting` (`:system`, in the transaction of the `settings.change` entry) | per exit name `{"fingerprint": <SHA-256 of the canonical JSON of mode, public, transips and public_dynamic>, "changed_at", "changed_by", "deleted": bool}`; a label-, interface- or port-preservation-only edit changes nothing; no earlier contents (the audit log holds them) | the lookup (`egress_changed_after_event`, D32) |
| `leases.rollback_lowered` | `arcilogger-db leases prepare-rollback` (`:system`), the lease step of `Install-Arcilogger.ps1 -Rollback` | `{"at": <one timestamp>, "count": <FortiGate leases lowered to low>}` | the seed of the re-upgrade, which restores `high` on the `fg_dhcp` leases whose `updated_at` equals `at` and deletes the row |

The administrator-owned rows of the legacy keys `nat.public_ips`, `nat.wan_transip` and
`lookup.port_hint` are kept unread for one version after revision `0018_nat_egresses` (a
rollback without a database restore then runs the previous version with its own NAT values); the
new code never reads, lists or writes them, and the first revision after Wave 3 deletes them.

Not created yet (later milestones):
`nat_index` (the `fg_traffic` indexes `(transip, transport, time)` and `(transip, time)` serve
spec 9.3 for now; the matching rule and the bounded time scan, setting
`lookup.max_session_seconds`, are in `docs/MVP-CONTRACTS.md` section 5).

## Audit log (`audit_log`)

`hash = sha256(prev_hash || canonical_json(entry))`, `prev_hash` of the first entry is 64 zeros.
The entry fields and the spool line format are specified in `docs/MVP-CONTRACTS.md` and
`api/src/arcilogger_api/audit.py`. A trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` on the
table; there is deliberately no foreign key on `actor_id`, so no change elsewhere can alter a
hashed row. `prev_hash` is unique, so the chain can never fork. Every entry is also written to
`spool/_audit/<day>.jsonl` (the day it is written; that file is never appended to after
midnight), which the nightly sealer puts into the vault and covers by the manifest. An
incomplete last line left by an interrupted write is cut off by the API before its next append
(the entry is rewritten from the database); the sealer refuses such a file and retries.

## Licence tables (`license_*`)

Revision `0014_license` (licensing contract 4.1, [ADR-0013](adr/0013-offline-licensing.md),
customer guide [LICENSING.md](LICENSING.md)). All three tables are **append-only**: one function
`arci_license_immutable()` and, per table, a `BEFORE UPDATE OR DELETE … FOR EACH ROW` trigger
(`<table>_no_update_delete`) and a `BEFORE TRUNCATE … FOR EACH STATEMENT` trigger
(`<table>_no_truncate`) reject every change with `insufficient_privilege` ("<table> is append-only
(<op> rejected)"), the pattern of the audit log. The tables are an index of what happened, not the
licence: every licence field that is displayed, audited, written to the status file or used for a
decision comes from `license_files.content`, verified again at that moment. A database owner who
disables the triggers therefore changes no licence decision.

| Table | Key | Content |
|---|---|---|
| `license_installation` | `id` = 1 (`ck_license_installation_single`) | The installation's identity and trial start (one row, never changed): `installation_id` (UUID4, unique; in the request code, informational), `trial_started_at` (Python clock; backdated to the earliest audit entry, `license.trial_start` entry or licence event when such traces exist), `created_at`. Created by the seed of `arcilogger-db upgrade`, else by the first status read |
| `license_files` | id; unique `sha256` | Licence files as installed (exact bytes; never changed or deleted): `content` (BYTEA, 1–65,536 bytes), `sha256` of the bytes, `payload_sha256` (SHA-256 of the canonical payload, the same for every reformatted copy; indexed), and as an index only `license_id` (indexed), `key_id`, `licensee`, `issued_at`, `first_installed_at`, `first_installed_by` (user name, or `os:<windows user>` for the CLI) |
| `license_events` | id; index `ix_license_events_kind_id` (kind, id) | Licence history: `at` (Python clock), `kind` (`install`, `remove`, `refuse`, `limit`, `state`), `source` (`web`, `cli`, `worker`, `seed`), `actor` (user name, `os:<user>` or `system`), `license_file_id` (FK `license_files`, RESTRICT; required for `install` / `remove`), `details` (JSONB: the full details, incl. licensee and server ids, which the audit entries leave out). The **active licence** is the file of the newest `install`/`remove` event when that event is `install` |

The licence state itself is never stored: it is computed (`arcilogger_db.licensing.load_status`)
from the active file, the server id, the installation row, the trusted time and the usage counts
(active `users`; `sources` configured or seen within 30 days). The worker job `license.check`
records state changes and limit crossings as events and audit entries and writes
`state/license-status.json` for the installer.

## TimescaleDB (optional) or native partitions

The baseline migration checks `pg_available_extensions` **before** it creates the time-series
tables:

| `[database] timescale` | TimescaleDB installed | Result |
|---|---|---|
| `auto` (default) | yes | `CREATE EXTENSION timescaledb`; the 15 time-series tables of the baseline become hypertables (`time`, 1-day chunks); compression (`segmentby source`, `orderby time DESC, id DESC`) with a 7-day policy where the edition supports it; `timescaledb.telemetry_level = off` for the database. Revision `0017_fortigate_dhcp` creates the 16th, `fg_dhcp`, in the storage the baseline chose (a hypertable with the same chunks and compression when `messages` is one, else natively partitioned) and appends it to the `db.timescale` status |
| `auto` | no, or `CREATE EXTENSION` fails (e.g. not in `shared_preload_libraries`) | native partitions (below); the reason is recorded and `arcilogger-db check` warns when TimescaleDB is installed but unused |
| `require` | no | the migration fails |
| `off` | any | native partitions |

**Native partitions** (plain PostgreSQL, the likely Windows production mode until TimescaleDB on
Windows is verified): every time-series table is `PARTITION BY RANGE (time)` with a
`<table>_default` partition; `arcilogger_db.partitions.ensure_partitions` creates **daily**
partitions `<table>_pYYYYMMDD`, one per **UTC day** (`[D 00:00 UTC, D+1 00:00 UTC)`, 03:00 to
03:00 site time, as TimescaleDB's 1-day chunks), for the device times of the `db.retention_days`
window (today − 30 days by default; its first local midnight is 21:00 UTC the day before, so the
window takes 31 UTC days) up to 7 days ahead (Wave 5 contract G2). UTC days, not site days: the
5651 lookup asks a range of a day or more one UTC day at a time (`lookup.engine.CHUNK`, written
for the chunks), and each such step then reads one partition; with partitions cut at site
midnight a step read two, and an ordered `LIMIT 1` over two partitions walked the second one's
rows of the address before it answered (the D15 check's 3.8 guards: 125,000 index entries and
33 ms for a busy address instead of a few). One call creates at most 16 days (today and the days ahead first, then the past days newest
first): 256 partitions with their indexes take about 2,700 locks until the commit, within
PostgreSQL's default lock table (`max_locks_per_transaction` 64 per connection, measured
2026-11-02 on PostgreSQL 16), as the 17 monthly partitions of every table an earlier version's
first upgrade created did; a new database gets the rest of the window from the next runs
(`arcilogger-db partitions`, the worker at its start-up and daily). `ensure_days` creates the
partitions of the site days an archive reload brings back (two UTC days a site day). The monthly
partitions `<table>_pYYYYMM` of earlier versions (bounds at local midnight Europe/Istanbul) are
never split; revision `0021_db_window` drops the empty ones of the months after the migration
(one holding rows, a device clock in the future, stays). **The change-over rule:** a daily
partition is its UTC day less every partition that overlaps it. After the current month, which
ends at local midnight (21:00 UTC on its last day), comes a 3-hour daily partition named for
that UTC day (e.g. `messages_p20261130`, `[30.11. 21:00 UTC, 01.12. 00:00 UTC)`), then whole UTC
days; before a monthly partition kept ahead, the daily partition of its UTC day ends at its
start (21 hours). No device time between them falls into the default partition. The monthly
ones of the past go together with the daily partition after them (the drop rule below) — up to
one extra month during the change-over. It runs in `arcilogger-db upgrade`, in `arcilogger-db
partitions` and daily in the worker. Rows of a day without a partition wait in the default
partition and are moved when the partition is created; afterwards the default partition only
holds outliers (e.g. a device clock in 2001). `arcilogger-db check` warns when fewer than the
next 3 days have partitions (the last site day covered whole). `partitions.drop_before` drops
everything older than a cutoff day: the partitions whose UTC day (the one they end in) has
ended by its local midnight (**the drop rule**), the older rows of the default partitions, on
TimescaleDB the chunks that end on or before that instant (`drop_chunks`) — the same rule: the
daily partition or chunk holding that instant, a UTC day, is kept whole, with up to 21 hours of
the day before; a monthly partition, ending at 21:00 UTC, goes with the daily partition of the
rest of that UTC day, so an archive reload of its days later gets whole UTC days and none of
their rows waits in the default partition —, and the `search_counts` and `source_clock_samples`
of the rows that went, and records the cutoff in the status row `db.window_dropped_before` (`{"cutoff",
"at"}`, never in the defaults and never editable, like `db.timescale`; the newest cutoff ever
applied, written in the drop's transaction). `partitions.maintain_window` is one step of the
window: the missing partitions (never a day before the cutoff), then a drop up to the next step
towards the cutoff (`next_drop_cutoff`: what ends within 16 days of the start of the oldest
partition or chunk to go, at least that partition whole), created and dropped days together at most 16 a transaction, for the
same lock table; it refuses a cutoff less than 7 days before today.

The outcome is stored in the setting `db.timescale` (`storage`: `hypertables` or `partitions`)
and shown by `arcilogger-db check`. `timescaledb.telemetry_level = off` (TimescaleDB reports
telemetry by default; ARCILOGGER sends nothing anywhere): the installer sets it for the
database before `CREATE EXTENSION` and server-wide (`ALTER SYSTEM`) right after it, and
KURULUM 2.1 adds it to `postgresql.conf`. Tested on PostgreSQL 16.15 (plain, native partitions) and TimescaleDB 2.30.2 on
PostgreSQL 16.

## Retention

- Vault (legal store): ≥ 730 days, default 760 (`arcilogger-core retention`; off by default,
  `[retention] enabled = false`). Sealed files are `.xz` (older days `.gz`; see "Sealed files").
- **Database: the last 30 days of log rows** (setting `db.retention_days`, default 30, at least
  7; owner decision 2026-11-02, "son 30 olsun", Wave 5 contract G1–G3). Revision `0021_db_window`
  changes a stored 400 (the old default) to 30, raises a value below 7 to 7 and keeps every other
  value. The window applies to the 16 time-series tables and, with them, `search_counts` and
  `source_clock_samples`; the vault, the spool and every sealed file are never touched.
  - **Days.** Partitions and chunks are by device time (`time`); a raw day's file (`raw_day`, a
    spool file) has rows up to 25 hours either side of its day (`COUNTS_TIME_MARGIN`: a device
    time more than a day from the receive time is not used), so in the partitions of
    `RAW_DAY_MARGIN` = **2 days** either side of it. The window, the cutoff and the days of a
    reload are device-time days; the margin is added wherever a raw day must stay whole or be
    loaded again.
  - **The job** `db.window` (`arcilogger_worker.db_window`; daily after 04:00, and at the worker's
    start-up when that day's run is missing; 5651 Temel, no licence gate) works in steps, each
    one transaction under the partition maintenance lock: archive reloads whose `expires_at` has
    passed become `expired`; the cutoff is read; then `partitions.maintain_window` creates the
    daily partitions of the window and the 7 days ahead and drops everything older than the next
    step towards the cutoff (above: whole partitions, on TimescaleDB chunks, the older rows of the
    default partitions, their `search_counts` and `source_clock_samples`; the applied cutoff
    recorded). A run takes up to 64 steps of 16 days: the first run after the upgrade from the
    400-day window, about 370 days on 16 tables, would otherwise lock thousands of partitions or
    chunks in one transaction and fail for want of shared memory every day. A dropped partition
    gives its disk space back at once; only the default partitions' few rows wait for autovacuum.
  - **The cutoff** is the earliest of today − `db.retention_days`, the oldest day not sealed yet
    **minus 2 days** (the margin: its rows before its midnight stay too), and the oldest
    `first_day` of an active archive reload (`queued`, `loading` or `ready`; a reload's days are
    device-time days and its files' rows around them are loaded with it). The database then holds
    the cutoff day up to today (31 site days with the default, and up to 21 hours of the day
    before in the UTC day that holds the cutoff's midnight), and a day that could not come
    back from the vault is never dropped, not even in part. A day whose `state\seal` file says
    `sealed` but whose manifest, or a file it names, is missing or unreadable counts as not
    sealed (`limited_by` `manifest`, `details.unreadable_manifest`): only the days whose rows the
    step would drop for the first time are checked. A day before the chain's start
    (`[seal] first_day`, or the first sealed day) counts as sealed — its rows go, and since it has
    no manifest an archive reload cannot bring it back (its raw lines stay in the spool) — and so
    do a skipped (empty) day and a day the vault's retention removed; today is never sealed.
    While nothing is sealed and `[seal] first_day` is not set (a new installation before its
    first seal), nothing is dropped. A day that stays unsealed (sealing stopped) holds the cutoff,
    and the database grows past the window until it is sealed (`R20-seal` reports that). So does
    a reload that stays `queued` or `loading` (nothing is dropped because of its age; the run
    names those older than a day in `details.reloads_waiting`). One that made no progress since
    the previous successful run (the same status and `bytes_done` as in that run's
    `details.reload_progress`) raises `R20-db-window` (medium, key `R20-db-window:reload-<id>`,
    naming the reload and its days); the first run that sees it move on or end resolves the
    alert. `maintain_window` refuses a cutoff less than 7 days before today.
  - **The run** records `today`, `cutoff`, `applied` (the cutoff the last step reached), `done`,
    `steps`, `limited_by` (`window`, also on a tie, `unsealed`, `manifest`, `reload` or
    `nothing_sealed`), each limit (`window_from`, `unsealed_from`, `reload_from`,
    `unreadable_manifest`), `reloads_waiting`, `reload_progress`, `reloads_stuck`, the counts of
    all steps (`expired`, `created`, `remaining`, `dropped`, `purged`, `counts_deleted`,
    `clock_samples_deleted`) and `audit_ids` in `job_runs.details`; the applied cutoff itself is
    in the status row `db.window_dropped_before`, written with each drop.
  - **The record of the deletion** is the audit log (actor `system`): every step that dropped
    something writes one `db.window` entry (target `database`) in its own transaction, so no drop
    is committed without it: the step's cutoff, the names of the partitions or chunks it dropped
    (at most 100, their count complete) and the exact counts of the rows that went row by row
    (the default partitions' rows, `search_counts`, clock samples); the rows of a dropped
    partition or chunk are not counted (that would read the whole day). The rows in the database
    are working copies: the sealed original stays in the vault for at least 730 days and comes
    back on request. No separate deletion record ("imha tutanağı") is written; whether one is
    needed is left to legal review ([retention policy draft](legal/saklama-imha-politikasi-taslak.md)).
    The first run after revision `0021_db_window` also turns the revision's change of a stored
    `db.retention_days` (400 → 30, below 7 → 7; the status row `db.retention_days_migration`)
    into one `settings.change` entry with the old and the new value, before its first step: no
    revision writes audit entries.
  - **A failed step** drops nothing (its transaction is rolled back, expired reloads included;
    earlier steps of the run stay, each complete and recorded), opens `R20-db-window`
    (medium, key `R20-db-window:none`) and the worker retries the run; the next successful run
    resolves the alert.
  - **The change-over** from monthly partitions: the monthly partitions of earlier versions are
    never split. Revision `0021_db_window` drops the empty ones of the months after the upgrade,
    so the daily partitions start where the current month ends (its last UTC day's three hours
    after local midnight, then the UTC days of the next month; above); a monthly partition goes
    with those three hours once the UTC day it ends in has ended by the cutoff (the drop rule).
    The first run after the upgrade drops every such month (in steps of at least one month); the
    month of the cutoff stays whole, so up to one extra month of rows remains during the
    change-over (an upgrade in November 2026: the November partition goes on the run of
    1 January, when the cutoff reaches 2 December). While a monthly partition is in the window,
    a lookup's step over the UTC day it ends in still reads two partitions (it and the three
    hours after it), the only steps that do; this passes with the month.
  - **Archive reload** ("arşivden yeniden yükle", `archive_reloads`, Wave 5 contract G4–G9):
    a lookup or search that needs a day the database no longer holds asks for it instead of
    answering without it. A 5651 lookup reads the asked day − 1 to + 1 plus the widest look-back
    of its steps (`arcilogger_worker.lookup.days.lookup_days`; with the default settings the day
    − 8 to the day + 1: the effective reach below); when any of those days is gone, `POST
    /lookup/5651` answers **409 `archive_reload_required`** (`details`: `first_day`,
    `last_day`, `bytes_total`, `estimated_bytes`) and stores no lookup: it never answers "not
    found" for a day it could not see. A search whose range starts before the window answers
    with the rows the database holds plus `archived: {first_day, last_day}` (else `null`), and
    409 when every day of its range is gone. A day is **gone** when it lies before the applied
    cutoff (`db.window_dropped_before`; also inside a window raised since) or before the window
    with no row at all (a lost database: ingest loads only the window and its 2 raw days before
    back), unless a `ready` reload holds it; and while a `loading` reload replaces the files
    that have rows in it (its days ± 4, inside the window too). A `queued` request changes
    nothing yet. The lookup and the search check the days and read them in one transaction
    holding the partition maintenance lock shared, so neither the job's drop nor a reload's
    start comes between. Evidence packages and official reports read the vault and are
    unchanged.
    - **Effective reach of a lookup without a reload.** The earliest instant a lookup reads is
      `T − t − S − LOG_SLACK − L` (`lookup.days.lookup_range`): the tolerance `t`
      (`lookup.tolerance_seconds`, 60 s), the longest session `S` (`lookup.max_session_seconds`,
      1 day), the slack of the web/app/DNS logs (60 s) and the DHCP look-back `L`
      (`dhcp_lookback`: the longest configured lease timeout or ARP grace, at least FortiOS's
      default lease of 604,800 s, 7 days). With the defaults that is 8 days and 2 minutes. With
      `dhcp.fortigate_use_logged_lease` (the default) the days reach back as far as one reload
      holds (`RELOAD_DAYS`, 14 days with the last one), because the logged lease time of a
      FortiGate lease (up to FortiOS's 100 days) is known only once the lease is read: the first
      day read is `T`'s day − 12 (− 11 for a moment in the last two minutes before a midnight,
      whose last day is `T`'s day + 2; without the setting `T`'s day − 8). The window holds the
      site days from today − `db.retention_days` to today, so a lookup needs no reload while its
      first day ≥ today − `db.retention_days`: with the default 30, for a moment at most **18
      days** old (19 in the last two minutes before a midnight). An older lookup needs a reload
      of its 14 days. A look-back of the engine that reaches before the first day (the 100-day
      cap of the missed-move check, a logged lease longer than 14 days: the lease's newest
      record, the post-lease checks, the two-origin check) reads only from the first of the
      lookup's days (`lookup.days.reads_from`), and the answer says so
      (`lookback_clamped_to_window`, TR/EN, naming the days; W5-G-17); the two-origin check's
      read past the moment ends with the last day (`reads_until`) without a note, a method rule
      (docs/CORRELATION.md section 3), so the answer is the same before a drop and after the
      reload. An administrator who wants **30 days** of lookups without a reload sets
      `db.retention_days` to **42** (30 + 12): the database then holds 43 site days, about 69 GB
      at 1 M lines a day instead of 49.6 GB ("Size" below). A longer
      `lookup.max_session_seconds`, or a lease time in the DHCP settings that reaches further
      than those 12 days, takes as much off the reach; their upper bounds (2 days; 7 days ×
      125 %; [SETTINGS.md](SETTINGS.md) section 1) keep every lookup's days within one reload
      (#126, W5-G-19). Both stay as they are (manager
      answer 3 on #121): the 30-day window is the owner's decision, and the look-back is needed
      for correct answers. Until the day's run of
      the job (after 04:00) the database holds one day more; a cutoff held by an unsealed day or
      a reload keeps more, never less.
    - `POST /archive-reloads` (`{reason, first_day, last_day, lookup_id?}`, 202) queues a request;
      `GET /archive-reloads/{id}` and `GET /archive-reloads` show it (administrators see every
      request, other users their own). A request of days the window holds whole is refused (422
      `validation_error`: nothing to reload); when none of its days needs a new load and an
      active request of the same user covers it, that request is the answer (one load for two
      clicks; audited with `existing`); another user's request never is, for an administrator
      either: each person's request keeps its own record. A day the sealer skipped (no
      line) has nothing to load. Refusals are 422 with `code`, `message_tr` and
      `message_en`: `archive_reload_too_large` (more than 14 days), `archive_day_not_in_vault`
      (a day without a sealed manifest: not sealed yet, before the chain's start, or removed by
      the vault's retention), `archive_reload_disk_low` (the newest disk sample of the data drive
      in `state\system-history.json`, minus the estimate of the files not loaded yet, would leave
      less than 15 % free; a file another user's `ready` reload holds takes no room; no sample,
      no refusal).
    - **Who:** the permission of the function that asks — `lookup.run` for `reason = "lookup"`
      (with a `lookup_id`, one the user may see), `search.read` for `"search"`; today both are
      the Yönetici and Denetçi roles ([roles.md](roles.md)) — and the accepted KVKK notice. No
      licence gate (5651 Temel).
    - **Audit first:** the audit entry `archive.reload` (target `archive_reload:<id>`; who, the
      days, the reason, the lookup id, `bytes_total` and the estimate) is written **before** the
      `queued` row, in the same transaction. A refused request (any 422: the refusals above or
      `validation_error`) is not queued and writes no audit entry: nothing is loaded and no data
      comes back (W5-G-13). Reading the
      reloaded rows is audited like every lookup and search.
    - **Loading:** ArciIngest, the only writer of log rows, takes one request at a time (the
      others stay `queued`; after a restart the `loading` one is resumed), in a transaction that
      holds the partition maintenance lock, so the job either sees it as active or has finished
      its drop first. It loads the raw days `first_day − 2` … `last_day + 2` (the margin: a line
      received after midnight with a device time before it), every day of the request sealed or
      skipped, a margin day when it is sealed. A file whose rows of the request's days are in the
      database already (its offset finished at `bytes_uncompressed`, nothing dropped from those
      days since: `db.window_dropped_before`, or another `ready` reload holding them) is left
      alone, so a second user's request of loaded days becomes `ready` with its own record and
      no load. For each other `(source, day)` it first checks the sealed file against its
      manifest (size and SHA-256, read afresh): a missing or changed file fails the request
      before anything of it is deleted. Then it resets the file, one file at a time (W5-G-25):
      one transaction sets its `ingest_offsets` row to 0 and marks it (`counts_from_line = 0`),
      and deletes its parse failures, its clock samples (the file's part of the samples of the
      minutes in which a line of it was received outside its day, taken off once, here), on
      plain PostgreSQL its rows in the default partitions outside its time window, and its
      `search_counts` of hours no later step reaches. Then its rows, by `(source, raw_day)`
      wherever their device times lie (all 16 tables): around every minute in which a line of
      the file was received outside its day (a damaged or hand-restored spool line; ± 1 day 1 h,
      read from the sealed file before the reset), then within its time window, are deleted in
      steps of device time between rounds of live data, each a transaction of at most 10 000
      lines by `search_counts` (about 0.1 s), with the counts of the hours it finishes; the
      last takes the mark off. Only then does it load the sealed file with the normal reader,
      after every newer day (live data first). A marked file is never loaded and never counts
      as loaded: after a stop between two steps its reset is taken up again (for its request,
      or on its own when no request holds it), and it loads exactly once. The checks that read
      whole files (the SHA-256 at the claim, a file's receive times before its reset: about
      0.5 s and 25 s for a 5 M line day) run in a thread beside ingest's loop, which goes on
      loading live data and acts on each result in a later round, in the same order (the size
      is checked again on the loop right before the file's first transaction); the reload's
      partitions are made a day a round (#124, W5-G-22), one table a transaction (a stop waits
      for one table's). Its claim and its partitions wait for the partition maintenance lock,
      and then for the tables, at most half of `search.request_budget_ms` in all (never more
      than 2 s; W5-G-24): a search queued behind them keeps half its budget; they are tried
      again at the next look at the requests (the claim keeps its checked files), the request
      is not failed. A live line waits at most about one
      round of steps (0.25 s and the step that ends it) instead of about 5 to 8 s per 1 M lines of a
      file whose rows are still there (the window's first days).
      A reload run twice gives the same rows and
      counts, on both back ends. `bytes_done` is Σ committed offset of `bytes_total` (Σ `bytes_uncompressed` of
      those files). `ready` sets `ready_at` and `expires_at` = `ready_at` +
      `db.reload_keep_days` (default 7, 1–30); `failed` carries `error_code`
      `archive_day_not_in_vault` (a day no longer sealed) or `archive_file_unreadable` (a sealed
      file that does not match its manifest, or unreadable for 60 s). After `expires_at` the
      next `db.window` run marks the request `expired` and drops its days again. Row ids change
      on a reload; the raw-line references stay.
    - **KVKK:** a reload brings personal data (addresses, MACs, user names in the logs) back into
      the database for `db.reload_keep_days` days after it is ready; the drop that follows is
      recorded like every other (`db.window` audit entry). The period, and whether the drops need
      a deletion record ("imha tutanağı") as well, are open decisions of the
      [retention policy draft](legal/saklama-imha-politikasi-taslak.md); legal review is
      recommended.
  - **Size** (G5). The estimate of a reload is Σ `bytes_uncompressed` × **2.6 database bytes per
    raw byte** (`arcilogger_api.archive.service.DB_BYTES_PER_RAW_BYTE`). Measured 2026-11-02 on
    plain PostgreSQL 16 with daily partitions: `tools/loadgen` days loaded by the real ingest
    (`IngestService.run(once=True)`, `batch_rows` 5,000) into a fresh database, then the growth of
    every table a line writes to (the 16 time-series tables, `search_counts`,
    `source_clock_samples`, `parse_failures`; rows, indexes and TOAST after `VACUUM ANALYZE`)
    divided by the spool bytes:

    | Load-generator data | Lines | Raw bytes | Database bytes | Factor (rows alone) |
    |---|---|---|---|---|
    | default site, 2026-10-27 | 218,170 | 139,102,963 | 349,200,384 | 2.51 (1.97) |
    | default site, 2026-10-26 and 27 | 426,905 | 272,266,266 | 682,049,536 | 2.51 (1.97) |
    | FortiGate-only site with `srcmac`, 2026-10-27 | 214,836 | 140,590,432 | 337,584,128 | 2.40 (1.96) |

    The factor is rounded up from 2.51, so a reload is not under-estimated (its deletes leave dead
    rows until autovacuum). Per line: 637.6 raw bytes and about 1,600 database bytes; the largest
    tables per row were `messages` 911 bytes (every line has one) and `fg_traffic` 929 bytes
    (667 of them the row), `fg_app` 698, `fg_web` 638 and `fg_dns` 527. The Wave 4 index
    measurement ([INGEST.md](INGEST.md), 28.25 M `fg_traffic` rows, 5.8 GB of rows, 4.5 GB for
    its five indexes) gives 205 bytes of row per `fg_traffic` row (5.8 GB / 28.25 M), 396 bytes
    with the six indexes of today ((5.8 + 4.5 + 28.25 × 0.0315) GB / 28.25 M): one table on rows
    made for an index test, a lower bound and not the size of a loaded line. On TimescaleDB the
    chunks older than 7 days are compressed (not measured here): the figures are those of plain
    PostgreSQL.
  - **Disk saving at 1 M lines a day** (plain PostgreSQL; each line one `messages` row and its
    typed row):

    | | 30 days (`db.retention_days` 30) | 400 days (before) | Saving |
    |---|---|---|---|
    | Every table, measured 1,600 bytes a line (2.51 × 637.6) | 30 × 1 M × 1,600 B = **48 GB** (44.7 GiB) | 400 × 1 M × 1,600 B = **640 GB** (596 GiB) | **592 GB** (551 GiB) |
    | Every table, the estimate's 2.6 (1,658 bytes a line) | 49.7 GB | 663 GB | 613 GB |
    | `fg_traffic` alone, Wave 4 measurement with indexes (396 B a row) | 11.9 GB | 158 GB | 147 GB |
    | `fg_traffic` alone, rows only (205 B a row) | 6.2 GB | 82 GB | 76 GB |

    The window holds 31 days (the cutoff day to today): 49.6 GB at 1,600 bytes a line, plus up to
    one month during the change-over and the reloaded days (a 14-day reload of such days:
    14 × 637.6 MB × 2.6 ≈ 23.2 GB). The vault keeps the raw days unchanged (at least 730 days).
- Usage rollups (`usage_*`, `domain_categories`): `usage.retention_days` (400), deleted by whole
  days by the worker job `usage.retention` (daily after 03:30; `domain_categories` rows whose
  `last_day` is before the cutoff); they are derived and can be rebuilt from the logs while those
  are in the database. The same job deletes finished rebuild requests (`usage_rebuilds`, done or
  failed) 90 days after they finished.
- The licence tables (`license_installation`, `license_files`, `license_events`) are append-only
  and have no expiry.
- The search summary table (`search_counts`) follows the log tables: `partitions.drop_before`
  also deletes its rows with `hour` before the start of the oldest `messages` partition (or
  chunk) it keeps, or before the cutoff when that is earlier, so the counts and the rows stay in
  step. The clock samples (`source_clock_samples`) go with them: `drop_before` deletes the minutes
  received before the same instant. Saved searches (`saved_searches`) are deleted with their
  user.
- `lookups`, `lookup_reports` (and the PDFs in `exports\reports\`), `audit_log`,
  `user_sessions`, the assignment history (`device_assignments`, `persons`) and the history of
  the browser administration (`admin_requests`) and the archive reload requests
  (`archive_reloads`, kept as `expired`) have no expiry yet; the open decisions are
  listed in
  [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md).
- Older data stays available in the vault; a sealed day comes back into the database on request
  (archive reload, above).

## Seed data

`arcilogger-db upgrade` seeds after every migration run (idempotent, `arcilogger_db.seed`):

- **default settings only** (`arcilogger_db.settings.DEFAULT_SETTINGS`), e.g.
  `nat.egresses = {}` (no internet exit entered: one implicit double-NAT exit until the site
  enters its lines), `lookup.tolerance_seconds = 60`, `lookup.max_session_seconds = 86400`,
  `lease.server_seconds = {}` (every DHCP server `lease.default_seconds`, 1 day, until the site
  enters other lease times), `lease.timeout_percent = 110` (spec 9.1 "× 1.1" as an integer),
  `auth.idle_minutes = 15`, `db.retention_days = 30` (400 before revision `0021_db_window`),
  `db.reload_keep_days = 7`. All values are canonical JSON (no floats),
  so every change can be audited.
- **no network** (owner decision 2026-10-20, ASSUMPTIONS P4): ARCILOGGER ships no VLAN plan, no
  departments and no log sources — they are the site's own data. A new installation starts with
  empty `vlans` and `departments` tables; the administrator enters both on the Cihazlar ve
  Kişiler page (`/api/v1/vlans`, docs/MVP-CONTRACTS.md section 11). A VLAN without a linked
  department has **its Turkish name** as department (spec 2.2), applied in one place:
  `arcilogger_db.network.vlan_department` / `department_for_ip`.
- Rows an earlier version seeded (a VLAN plan and departments; marker setting
  `seed.network_version`) stay as they are. Revision `0013_site_data` hands a
  `lease.server_seconds` row the seed still owned to `:migration`, so such a site keeps its lease
  times instead of following the new empty default.
- The tests use a fictional example site explicitly (`arcilogger_db.testing.seed_example_network`,
  `EXAMPLE_VLANS`: a FortiGate in front of a MikroTik core router; static VLANs 10 for network
  devices and 20 for servers, three department VLANs 41, 42 and 43 and the staff Wi-Fi 60 with a
  DHCP server each, `dhcp<VLAN id>`, a guest Wi-Fi 61 marked `guest` with 1 h leases, a /30
  transit network without a VLAN id); the product never writes it.

A value an administrator changed is never overwritten; a row still owned by the seed follows a
changed default in a later version. Settings are changed with
`arcilogger-db settings set KEY JSON_VALUE --by NAME` — on the server
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe"`, the installer does not put `venv\Scripts` on `PATH` —
(validated, written with `updated_by = NAME`, audited as `settings.change`). The validation keeps
the spec 13 protections: `auth.totp_required_roles` must contain `admin` and `auditor`, and the
`auth.*` limits have bounds (`idle_minutes` 1–120, `absolute_hours` 1–24, `lockout_attempts`
1–20, `lockout_minutes` 1–1440, `password_min_length` 12–128; `INTEGER_RANGES`). A hand-written SQL `UPDATE` that leaves
`updated_by = ':seed'` is **reverted by the next `arcilogger-db upgrade`** and is not audited.

## Schema changes

The baseline revision is frozen. A change goes into a **new revision file** in
`arcilogger_db/migrations/versions/` together with the model change; the sync test fails if the
two disagree, and a test requires a single head (linear history).
