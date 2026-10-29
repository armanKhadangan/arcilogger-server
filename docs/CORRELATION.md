# Korelasyon ve 5651 sorgusu / Correlation and the 5651 lookup

> Durum: M3 (iş paketi B3); Wave 3: FortiGate DHCP kira kanıtı, srcmac ve internet çıkışları
> ([ADR-0016](adr/0016-fortigate-dhcp-and-internet-exits.md)). Kod:
> `worker/src/arcilogger_worker/correlation/` (IP–MAC zaman çizelgesi, DHCP kaynakları,
> cihazlar, IP çakışmaları), `worker/src/arcilogger_worker/lookup/` (5651 sorgu motoru),
> `db/src/arcilogger_db/egress.py` (internet çıkışları), `worker/src/arcilogger_worker/service.py`
> (worker hizmeti). Bağlayıcı arayüz: `docs/MVP-CONTRACTS.md` bölüm 5. ARCILOGGER **5651
> yükümlülüklerini desteklemek için tasarlanmıştır**; sonuçların resmi bir yanıtta nasıl
> kullanılacağı için hukuki görüş alınmalıdır.

## Özet (TR)

- **Kira zaman çizelgesi (`leases`)**: hangi MAC adresinin hangi iç IP adresini ne zaman
  kullandığı. Kaynaklar: DHCP sunucusunun kira kayıtları (MikroTik DHCP `assigned`/`deassigned`
  kayıtları ya da FortiGate DHCP sunucusunun olay kayıtları, DHCP Ack / Release; FortiGate'inkiler
  yalnızca yapılandırmada FortiGate olarak tanımlı kaynaklardan), saatlik `ARP-LOG` kayıtları ve
  FortiGate trafik kayıtları. Trafik yalnızca açık bir kaydı uzatır, asla yeni kayıt açmaz;
  FortiGate kiralarını varsayılan olarak uzatmaz (`dhcp.fortigate_traffic_extends`). RouterOS
  kira yenilemelerini loglamaz; bu yüzden açık bir MikroTik kaydı, son kanıttan sonra "kira
  süresi × %110" boyunca kanıt gelmezse zaman aşımıyla kapanır (kira süresi varsayılan 1 gün;
  farklı olan sunucular, ör. misafir ağınınki, `lease.server_seconds` ile girilir). Bir FortiGate
  kirası, son DHCP kaydından kayıttaki kira süresi kadar sonra (FortiOS varsayılanı 7 gün), pay
  eklenmeden biter: FortiGate'in kendi bitişi. Bir kira yalnızca aynı DHCP sunucusunun
  kayıtlarıyla sürer; öbür sunucunun Ack kaydı yeni bir kira açar.
- **srcmac hiçbir zaman kira kanıtı değildir:** FortiGate oturum kaydının srcmac alanı kira açmaz,
  uzatmaz, kapatmaz. Yalnızca 5651 sorgusunda, listelenen arayüzlerde
  (`lookup.srcmac_interfaces`, varsayılan boş: kapalı) kirası olmayan bir oturum için MAC adresini
  **kısmi kanıt** olarak, düşük güvenle verebilir.
- Statik IP'li cihazlar (yazıcı, sunucu) yalnızca ARP-LOG ile görünür: `[ilk görülme, son
  görülme + 1 saat]`, **orta güven**. `ARP-LOG` olmayan yalnız-FortiGate ağlarda statik
  cihazların kira kaydı yoktur: zincir iç IP adresinde biter (ya da srcmac ile kısmi kanıt olur).
- Aynı IP aynı anda iki MAC adresinde görünürse **IP çakışması** uyarısı üretilir (altyapı
  cihazları hariç) ve sorgu sonucu tek bir kişiyi kesinmiş gibi göstermez: tüm olası cihazlar
  "düşük güven" ile listelenir.
- Kayıtların karar veremediği durumlarda zincir **düşük güvenle** ve nedeniyle gösterilir: bir
  FortiGate kirasının son DHCP kaydından kira süresinin yarısından fazla sonra yenileme görülmediyse
  (`lease_renewal_not_seen`), FortiGate kirasının adresi tanımlı hiçbir ağda değilse
  (`fortigate_dhcp_no_network`), aynı ağda iki DHCP kaynağının kaydı varsa
  (`dhcp_sources_overlap`), kiranın son DHCP kaydından sonra kira zaman çizelgesinin bu kiraya
  uygulamadığı bir FortiGate DHCP kaydı varsa (`fortigate_dhcp_not_converted_since_lease`),
  atıf yapılan bir kaynağın saati o anda kayıksa (`source_clock_offset`) ya da srcmac kira
  kaydındaki MAC adresinden farklıysa (`srcmac_mismatch`).
- **5651 sorgusu** dört kipte çalışır: **kesin** (sorulan genel IP FortiGate'in kendi adresi:
  doğrudan bir internet çıkışının genel adresi, bir çıkışın NAT adresi ya da kayıtlarda o anda
  FortiGate'in kaynak NAT adresi olarak görünen adres; port verilmiş), **adaylar** (servis
  sağlayıcı NAT'ı, çift NAT: servis sağlayıcı yönlendiricisinin NAT kaydı olmadığı için zaman
  penceresindeki oturumlar hedef ve zaman yakınlığına göre sıralanır), **iç** (iç IP veya MAC
  adresi verilmiş) ve **sonuç yok** (nedeni ve hangi verinin yardımcı olacağı açıklanır).
- **İnternet çıkışları (`nat.egresses`)**: her internet hattı genel IP adresi (ve değişip
  değişemeyeceği), FortiGate'in o hattaki NAT adresi, arayüzleri (dstintf), NAT türü (doğrudan,
  servis sağlayıcı NAT'ı, FortiGate'ten geçmiyor, internet çıkışı değil) ve port korumasıyla
  tanımlanır. Sorgu istenen adresi bu tanımlara ve FortiGate kayıtlarına göre bir çıkışa bağlar,
  yalnızca o çıkışın oturumlarını arar ve kullandığı çıkışı sonuçta ve raporda yazar (bölüm 3,
  "Çıkışlar / Exits"). Eski NAT ayarları yükseltmede bu ayara çevrildi
  ([KURULUM.md](KURULUM.md) bölüm 13).
- Her eşleşme tam atıf zincirini taşır: oturum → kira (MAC) → cihaz → kişi/departman; her adım
  ham log satırına (`kaynak`, `gün`, `satır`, `bayt konumu`) işaret eder.
- Talepte hedef (IP/port/alan adı) varsa yalnızca o hedefe giden oturumlar aday olur; o hedefe
  giden oturum yoksa sonuç "bulunamadı" olur ve ilgisiz kişiler listelenmez.
- **IPv6 desteklenmez:** IPv6 adreslerini cihazlara bağlayan kayıtlar (NDP, DHCPv6) toplanmaz.
  Genel veya iç adresi IPv6 olan bir sorgu sonuç yerine açık bir retle yanıtlanır
  (`ipv6_not_supported`); böyle bir talep başka yollarla yanıtlanmalıdır. Hiçbir IPv4 oturumu bir
  IPv6 adresinin adayı olamaz.
- Geç gelen kayıtlar (ingest kesintisi sonrası birikmiş günler) otomatik olarak yeniden işlenir.
  Bir `dhcp.fortigate_*` ayarı (`dhcp.fortigate_format_verified` dışında), bir FortiGate kaynağı
  ya da bir ağ (VLAN alt ağı) değiştirildiğinde ise kiralar etkilenen ilk günden yeniden
  oluşturulmalıdır (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları
  yeniden oluştur); o zamana kadar o günlerin anları zincir kurulmadan yanıtlanabilir (güvenli
  taraf: yanlış kişi gösterilmez).
- **Yükseltmeden önce yüklenen kayıtlar:** o zamanki FortiGate DHCP kayıtları kira zaman
  çizelgesine çevrilmez; sorgu bunu belirtir (`fortigate_dhcp_not_converted`). Yükseltmeden önce
  (ya da bir geri alma sırasında önceki sürümce) yüklenen FortiGate oturum kayıtlarının srcmac
  alanı 5651 sorgusunda kullanılmaz; o anlar için srcmac ile kısmi zincir kurulmaz.
- **Geriye bakılan süreler** (bölüm 7): kira kaydı bulunamayan bir an için sorgu, en yeni DHCP
  biçimli kaydı o andan en çok max(en uzun kira penceresi, 604.800 sn) önceye kadar arar;
  daha eski, yükseltme öncesi bir Ack kaydı adlandırılmaz. Bir kiradan sonra kira zaman
  çizelgesine girmemiş FortiGate DHCP satırları, kiranın son DHCP kaydından itibaren FortiGate
  kirası için en çok kira süresinin yarısı (üst sınırsız), MikroTik kirası için en çok 7 gün
  boyunca okunur; çevrilmiş kayıtlarla denetim (MAC adresinin başka bir adres alması ya da
  adresin başka bir istemciye verilmesi) MikroTik kirası için en çok 100 gün (8.640.000 sn)
  geriye bakar. 7 günlük sınırın kalan riski: başka bir cihazın trafiğiyle açık kalan eski bir
  MikroTik kirası, istemcisini başka bir adrese taşıyan çevrilmemiş bir satırdan 7 günden fazla
  sonra o istemciyi uyarısız gösterebilir.
- Ölçüm: günde 3.000.000 trafik satırında (iki ardışık gün) ±60 sn aday araması **1,2–1,3 sn**,
  alan adıyla **1,1–1,5 sn**, hedef IP + port ile **0,04 sn** (hedef < 5 sn). Bu, sıkıştırılmamış
  veridir; TimescaleDB'nin sıkıştırdığı 7 günden eski bölümlerde (5651 taleplerinin çoğu bu
  günleri sorar) aday araması **1,15 sn**, alan adıyla **1,5–1,6 sn**, hedef IP + port ile
  **0,8 sn**.

## Summary (EN)

The worker turns DHCP records (MikroTik DHCP, or the FortiGate DHCP server's event records from
configured FortiGate sources), ARP and FortiGate traffic records into an IP ↔ MAC timeline
(`leases`), keeps a device inventory, raises IP-conflict alerts and answers 5651 requests through
the configured internet exits (`nat.egresses`): exact matches when the requested address is the
FortiGate's own (a direct exit's public address, an exit's NAT address, or an address the logs
show as its source-NAT address at that moment), ranked candidates behind an ISP router (double
NAT), the attribution chain for an internal IP or MAC, or a clear "nothing found" with what would
help. The srcmac field of FortiGate session records is never lease evidence; on listed interfaces
it may name a MAC as partial evidence with low confidence. Where the logs cannot decide, a chain
is shown with low confidence and the reason. Every step cites the raw spool line it rests on.
IPv6 lookups are not supported (no NDP or DHCPv6 evidence): they are refused, never answered with
IPv4 candidates.

---

## 1. Lease timeline (spec 9.1)

Evidence (`correlation/timeline.py`, a pure function — `build_timeline(events, rules, horizon=…,
seeds=…)` — tested without a database). The DHCP rows of both servers come through one loader,
`correlation/dhcp_sources.py` (`load_rows`, ADR-0016 D5), so live runs and rebuilds treat them
alike; "DHCP table" below means `mt_dhcp` (MikroTik) or `fg_dhcp` (FortiGate):

| Evidence | Effect |
|---|---|
| `mt_dhcp` **assigned** ip, mac; a kept `fg_dhcp` row whose logid `dhcp.fortigate_logids` maps to `assigned` (the FortiGate DHCP Ack, logid `0100026001` by default) | Opens an interval (ip, mac, hostname, vlan, server; `source_table` = the row's table; for `fg_dhcp` also the logged lease time, `lease_seconds`), confidence **high**. Every *other* open interval of that IP closes: **superseded** (at that moment), also an open interval of the **same** ip + mac from the **other** DHCP table (D5: after a move from MikroTik to FortiGate DHCP the FortiGate's Ack starts a lease of its own; a supersede across DHCP origins `(table, VDOM)` is counted `superseded_across_sources`). Same ip + mac already open from the **same** DHCP table: continuation — evidence kept, host name (and a logged lease time) updated. A continuation exists only within one DHCP table. |
| a kept `fg_dhcp` **assigned** of MAC m for IP y (D33) | Also closes m's open `fg_dhcp` interval on another IP of the same origin, interface (`server`) and **configured network** (the `vlans` row whose subnet holds the address, the most specific one, with or without a VLAN number), and m's open `mt_dhcp` interval on another IP of the same configured network (a migration), each **superseded** at the Ack, after that interval's own deadline has been applied (an interval already past its end times out at its end, as a live run would have closed it); counted `fg_mac_moved` and `superseded_across_sources`. A client holds one address per server interface and network, and a DHCPDECLINE is not logged. Nothing is closed this way when y lies in no configured network, and a MikroTik `assigned` never closes another IP's interval (section 7). |
| `mt_dhcp` **deassigned** ip, mac; a kept `fg_dhcp` row mapped to `deassigned` (the FortiGate DHCP Release, `0100026002` by default, derived from the documented logid layout: *verify on site*) | Closes the open interval of exactly that ip + mac: **dhcp**. Without one: counted as `orphan_deassign`, ignored. |
| `mt_arp` `ARP-LOG ip mac` | Extends the open interval of that ip + mac (DHCP or ARP); for an `fg_dhcp` interval the sighting is kept as evidence but moves its deadline only with `dhcp.fortigate_traffic_extends` (as traffic, below). Without one: opens an **ARP interval** (confidence **medium**) — except within `arp.ignore_after_release_seconds` (300 s) after a `deassigned` of that pair (a stale ARP table entry; counted as `ignored_arp_after_release`). |
| `fg_traffic` from `srcip` (configured FortiGate sources only) | Extends the open `mt_dhcp` intervals of that IP. An `fg_dhcp` interval only while `dhcp.fortigate_traffic_extends` is on (default `false`, D26): otherwise the latest traffic row is kept as its evidence (`last_traffic`) and its deadline stays. Never creates an interval, never extends an ARP interval (traffic proves the address was used, not by which MAC). |
| `fg_traffic.srcmac` | **Never lease evidence**: it never opens, extends or closes an interval and never writes `devices`, IP conflicts or usage; only the lookup reads it, as partial evidence (section 2). |
| nothing | An `mt_dhcp` interval, and an `fg_dhcp` interval without a usable logged lease, times out `lease time × lease.timeout_percent / 100` after its **last evidence** (default 1 day × 110 % = 26 h 24 min; a server given 1 h in `lease.server_seconds`: 1 h × 110 % = 1 h 6 min): **timeout**, ending at that moment. An `fg_dhcp` interval with a usable logged lease times out exactly `lease_seconds` after its **last DHCP evidence**, without the `lease.timeout_percent` margin (D6: the end the FortiGate itself gives the lease; 7 days after the last Ack with the FortiOS default of 604,800 s): **timeout**. An ARP interval ends `arp.interval_seconds` (1 h) after the last sighting: **arp**. |

**Departures from the earlier rules** (Wave 3; ADR-0016 and the "Deviations" of the Wave 3
contract): traffic extends MikroTik leases as SPEC 9.1 says, because RouterOS logs no renewals,
but not FortiGate leases by default, because FortiOS likely logs every DHCP Ack, renewals included
(D26); an `fg_dhcp` lease with a logged lease time ends at that time after its last DHCP evidence,
without the margin that configured times keep (D6); an `assigned` continues an open interval only
from the same DHCP table (D5); a FortiGate `assigned` also closes its MAC's interval on another
address of the same interface and configured network (D33); and an `ARP-LOG` sighting never
moves the deadline of an `fg_dhcp` interval (or of such a seed at midnight) while
`dhcp.fortigate_traffic_extends` is off, so the end stays the FortiGate's own on a site whose
ARP-LOG also lists FortiGate-served networks.

Details and decisions:

- **Renewals are silent** in RouterOS: an open MikroTik interval stays open as long as evidence
  keeps arriving within the timeout (hourly ARP-LOG, traffic). FortiOS likely logs every DHCP Ack,
  renewals included (*verify on site*, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4 step 5): a
  FortiGate interval stays open through its own Acks. A continuation Ack at least 40 % of the
  lease after the interval's previous DHCP evidence is counted as a renewal (`fg_renewal_ack`);
  leases that end without one while their address stays in use raise
  `R20-lease:fortigate-renewals-missing` (or `fortigate-renewals-not-logged` on a site whose check
  is done; [ALERTS.md](ALERTS.md) section 2).
- **ARP intervals** `[first_seen, last_seen + 1 h]`; sightings at most `interval × timeout_percent
  / 100` apart (66 min, scheduler jitter) are merged. An ARP interval closed later by an
  assignment **or a release** (inside the 66-minute grace but after `last_seen + 1 h`) never
  claims more than `last_seen + 1 h` (reason `arp`).
- **Stale ARP entries after a release**: the router keeps a released client in its ARP table for
  a while; an `ARP-LOG` of the same ip + mac within `arp.ignore_after_release_seconds` (default
  300 s, *verify on site*) after its `deassigned` opens nothing. A later sighting does. Other
  MACs are never ignored (that would hide an IP conflict). The rule carries across midnight and
  incremental runs (the releases just before the start are passed in), so it never depends on
  where a run was cut.
- **Deviation from the literal spec text** ("ARP intervals for IPs *without* DHCP"): an ARP
  interval is opened for **any** ip + mac that has no open interval, not only for IPs never seen
  in DHCP. Reason: DHCP clients whose lease was assigned before ARCILOGGER started (go-live, an
  always-on desktop that renews silently for weeks) would otherwise be invisible until their next
  `assigned`. For IPs never in DHCP (static devices) the behaviour is exactly the spec's. A later
  `assigned` of the same pair replaces the ARP interval by a DHCP interval (`superseded`;
  zero-length remainders are dropped). Side effects the owner should know: an `ARP-LOG` of a
  departed client more than 300 s after its release (a long-lived stale ARP entry) re-opens a
  medium-confidence interval for it until the next assignment or for 1 h; an ARP sighting of
  another MAC during an open DHCP lease is an overlapping interval and an IP conflict.
  *Owner to confirm.*
- **Equal timestamps** are ordered traffic → ARP → `deassigned` → `assigned`, then `mt_dhcp`
  before `fg_dhcp`, then row id, so "release and re-assign in the same second" is `dhcp` + new
  interval, not `superseded`.
- **VLAN** of an interval: the `mt_dhcp.vlan` column, else the DHCP server name
  (`dhcp.server_vlan_regex`, `dhcp60` → 60), else the subnet in `vlans`. An `fg_dhcp` interval
  takes it from the subnet only (`dhcp.server_vlan_regex` is not applied to FortiGate interface
  names); its server is the logged `interface`, so `lease.server_seconds` can key on it (one key
  space with the MikroTik server names).
- **Evidence stored per lease** (`leases.evidence`, contract format `EvidenceRef.to_dict()`): the
  opening row, up to 6 continuation `assigned` rows, the latest ARP row, the latest traffic row and
  the closing row — bounded, so a lease alive for months stays small. Each points at its raw line
  (`source`, `raw_day`, `raw_line`, `raw_offset`). An `fg_dhcp` row is summarised with its action
  and the lease it logged, e.g. `vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14
  (FortiGate DHCP, logid 0100026001, lease 604800)`.

### FortiGate DHCP rows (`correlation/dhcp_sources.py`, `lookup/trust.py`; ADR-0016)

Ingest files a FortiGate event line that carries `ip`, `mac` and `dhcp_msg` as an `fg_dhcp` row
(the shape of the line decides, [PARSERS.md](PARSERS.md)); what a row means is decided by the
worker at every run from the settings, so a changed setting applies to past days once their
leases are rebuilt, without re-ingesting anything. A row is checked in this order and counted
under the first rule that applies; such a row is never lease evidence:

1. its source is not trusted (`fortigate_dhcp_untrusted_source`): an unconfigured sender
   (`unknown-<ip>`) or a source whose type is not `fortigate`. An `unknown-<ip>` source whose
   address now lies in the address of an enabled, configured FortiGate source is trusted after
   all (a FortiGate whose sender address changed and was configured later); chains that cite its
   rows carry `source_configured_later`;
2. its `interface` is listed in `dhcp.fortigate_interfaces_excluded` (`fortigate_dhcp_excluded`;
   DHCP over IPsec, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4 step 2);
3. its `ip` is IPv6 (`ipv6_ignored`);
4. its logid is not in `dhcp.fortigate_logids` (`fortigate_dhcp_unmapped`);
5. `dhcp.fortigate_dhcp_msgs` lists the logid and the row's `dhcp_msg` is not among its values,
   compared case-insensitively, a missing one included (`fortigate_dhcp_unexpected_msg`);
6. its time is the receive time, not the FortiGate's event time (`fortigate_dhcp_receive_time`;
   no usable device time, or one more than a day off);
7. its MAC is zero, broadcast or multicast (`fortigate_dhcp_bad_mac`).

Rules 2 and 3 are configuration; rules 1 and 4–7 are refusals, named by
`R20-lease:fortigate-refused` and counted on the "Kira kanıtı" (lease evidence) card of the
Kaynaklar page. A kept row gives its server (`interface`), its VLAN (from the subnet), its
network (the subnet of the configured network holding its IP; a row in no configured network is
still used, counted `fortigate_dhcp_no_network` and named by `R20-lease:fortigate-dhcp-subnet`,
and its chains are `low`, section 2) and its lease time: the logged `lease` when
`dhcp.fortigate_use_logged_lease` is on and it lies in 300–8,640,000 s (the FortiOS `lease-time`
range), otherwise the configured time of its interface (`lease.server_seconds`, else
`lease.default_seconds`) with the margin, counted `lease_fallback` (and `lease_unlimited` for `0`,
which raises `R20-lease:fortigate-lease-unlimited`). The job report's `details.stats` holds
these counters, `kept_fg_dhcp`, `refused_fg_dhcp` and `excluded_fg_dhcp`, counted once, in the
run's own window.

**No backfill** (D8): FortiGate DHCP lines loaded before the upgrade to this version, loaded by
the previous version after a rollback, or whose `ip` or `mac` could not be read (parse failure
`fortigate.dhcp`, alert `R20-lease:fortigate-unconverted`) stay `fg_event` rows. They are kept,
searchable (`type:event`) and sealed with their day, but never become lease evidence; the lookup
names them where they matter (sections 2 and 3).

### Storage, rebuild and incremental mode (`correlation/leases.py`)

- A `leases` row belongs to the **site day of its start** (Europe/Istanbul).
- **`rebuild_day(day)`** — one transaction: recompute the day's intervals from the raw tables
  and write the **difference**: a recomputed interval takes the `id` of the stored row with the
  same (ip, mac, start) and is written only when something changed; rows that no longer follow
  from the evidence are deleted. **Lease ids are stable** across rebuilds, so `LeaseInfo.lease_id`
  in stored lookups and the lease ids in alert details keep pointing at their row (an id
  disappears only when its interval disappears). Idempotent: a second rebuild writes nothing.
- **Seeds** are the intervals of earlier days that were **alive at midnight**: stored open, or
  ending at/after midnight (their chain of evidence spans midnight), plus ARP intervals whose
  end is only decided after midnight (`last + 1 h` before, `last + 66 min` after midnight).
  A row that ended before midnight is final — its end rests only on evidence before midnight —
  and is never re-opened by a later sighting of the same pair or by another client's traffic on
  the address. A seed's state at midnight is its stored evidence before midnight plus the raw
  evidence of the last lease-timeout window (for a seed alive since its start, every sighting
  and `assigned` of its pair and every traffic row of its IP in that window belongs to it). The
  raw DHCP events of the window are also checked for a release or a new assignment that ended
  the seed (a continuation `assigned` of a MAC that already held a lease ends nothing): the seed
  is then closed exactly as a continuous run would close it. Its tail (end, last evidence,
  evidence) is rewritten by the rebuild. For FortiGate DHCP the raw window also holds the
  `fg_dhcp` Acks of the seeds' MACs (D33 closes a seed at its MAC's Ack for another address of
  the same interface and network). An `fg_dhcp` seed's last evidence is that of its DHCP refs
  and rows only while `dhcp.fortigate_traffic_extends` is off (traffic and ARP sightings stay
  its evidence), its lease time at midnight is that of the latest kept Ack of its pair that logs
  a usable one, and a rebuilt `fg_dhcp` seed has confidence `high` again: it is re-derived from
  its rows, which undoes the rollback step of section 7 for that lease.
- Intervals still open at the end of the day are followed into the next days (**look-ahead**,
  only for those IPs, one day per query) until they close: an interval crossing midnight is
  **one row with its real end**.
- **`rebuild_days(first, last)`** — rebuilds in order; only the last day looks ahead (the next
  day's seeds carry the state; intervals open at an intermediate midnight are written open and
  closed again by the next day).
- **Consistency (tested, `worker/tests/test_leases_consistency.py`)**: live incremental runs,
  `rebuild_days` and single-day rebuilds in any order (each day after the others, only the
  middle day, …) give the same rows with the same ids and the same alerts — the hand-computed
  ones, for a scenario with releases, timeouts across midnight, a static device with gaps,
  guest leases, a stale ARP entry right before midnight, an ARP interval decided after midnight
  and an IP conflict with a continuation `assigned`. Single days rely on the earlier days' rows:
  on an empty `leases` table rebuild **in order** (`rebuild --day <first> --to <last>`), which
  is what the catch-up does.
- **Incremental mode** — `run_incremental` every `[worker] interval_seconds` (60 s): evidence
  between the watermark (setting `worker.leases_watermark`, internal, author `:system`) and
  `min(now, ingest progress) − worker.leases_lag_seconds` (default 120 s) is fed into the open
  intervals; new rows are inserted, changed ones updated, the watermark advanced. **Ingest
  progress** (`correlation/watermark.py`) is the device time up to which the sources that feed
  the timeline (enabled FortiGate sources, every source with DHCP/ARP lines in the day before
  the watermark or `now`, whichever is earlier — so an ingest outage longer than a day still
  holds it) are loaded — the oldest of their newest `messages.time`. So the watermark never
  passes evidence that is not in the database yet: during an ingest outage or backlog it waits,
  and a device clock that runs behind moves it back by the same amount. A source stops holding
  the others back only when it is **silent and fully loaded**, judged by receive time, never by
  device time (a clock that runs behind is not silence): with the collector's status file
  (`state/collector-status.json`, written within the last 5 minutes) a source is active while the
  collector received from it within 30 minutes and has a backlog while the collector received
  later than its newest loaded line; without the file (collector down) a source whose newest
  loaded line was received 30 minutes before the newest loaded line of any other source counts
  as silent (device off; "log source silent" is its own alert). Until then lookups of the newest
  moments carry `lease_timeline_behind`. Remedy for a source that is gone for good: disable it
  (`sources.enabled`). Limit: after a backlog of several days ingest loads the newest day
  first, so the horizon may pass older days that are still loading; the late-evidence job
  (below) rebuilds them once they are loaded. Without a watermark (first start) or
  more than a day behind (worker stopped), it **catches up** with day rebuilds from the first
  day with DHCP/ARP data.
- Rebuilds never process evidence past the watermark — the two modes never apply the same row
  twice. A transaction-level advisory lock serialises all lease jobs (service and manual repair).
- **Late evidence** (ingest backlog after an outage, `correlation/late.py`): the incremental job
  has already passed it. Today's and yesterday's are picked up by the hourly re-check of today
  and the nightly rebuild of yesterday + today. Older days: `ingest_offsets.line_number` (per
  source and **raw day**) grows in the same transaction as the rows it loads (contract), so
  every rebuild of a complete day records the line count it started from (internal setting
  `worker.leases_ingest_seen`, `{"YYYY-MM-DD": lines}`), and the job `leases.late_evidence`
  (every 10 minutes) rebuilds — from the day before the first grown raw day (device clocks
  around midnight) up to today, in order — whenever a raw day has more lines than at its last
  rebuild. Days never recorded count as late once they are older than yesterday (yesterday
  belongs to the nightly rebuild). The first run on a database without the setting only records
  the current counts. Raw days older than `db.retention_days` are ignored. Tested end to end:
  the worker runs through a weekend without ingest, Saturday's rows arrive on Monday, the lease
  appears within one job interval and the lookup answers. A future "re-ingest from the vault"
  must advance `ingest_offsets` (or rebuild the days itself) to be noticed.
- **Traffic volume**: per source IP only the first and last row of every 5-minute bucket are read
  (exact because the bucket is shorter than the shortest lease timeout; checked at start). The
  rebuild of a day with 1,000,000 traffic rows and 300 devices took **2.4 s** (section 6).
- **Manual repair**: `arcilogger-worker rebuild --day 2026-10-12 [--to 2026-10-20]` (one
  transaction and one `job_runs` row per day), or in the browser Ayarlar → Uygulama ayarları →
  5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur (Settings → Application settings
  → 5651 lookup and lease timeline → Rebuild the leases; at most 31 days per request, run by the
  job `admin.requests`). Run it for the affected range, **in order**, from the first affected
  day to today, after changing lease settings (`lease.*`, `arp.*`, `dhcp.server_vlan_regex`, every
  `dhcp.fortigate_*` key except `dhcp.fortigate_format_verified`, which only changes texts), a
  FortiGate source (a sender configured later, a type changed) or a network on the Cihazlar ve
  Kişiler page (D33 compares configured networks): stored rows were computed with the old rules
  and are the next day's seeds. A change applies to past days only after the rebuild; until then
  lookups of moments of those days may answer without a chain, or with a chain lowered to `low`
  (section 7).

### Devices (`correlation/devices.py`, spec 9.2)

Updated from DHCP (MikroTik and FortiGate) and ARP evidence by every lease job (srcmac never
creates or updates a device): `first_seen` (earliest), `last_seen` (latest), `last_ip` /
`last_vlan` (of the latest sighting — rebuilding an old day never moves a device back), `hostnames` (DHCP host names, first seen first, at most 16), `randomized`
(locally administered unicast MAC: second hex digit 2, 6, A or E — e.g. iPhone private
addresses). **Never overwritten**: `name`, `type`, `person_id`, `notes`, `tags`,
`infrastructure` (only initialised for a new device from `devices.infrastructure_macs`) and
`vendor` once set. `person_id` is the current owner of the assignment history
(`device_assignments`, ADR-0008) and is written only by `arcilogger_db.assignments`; the upsert
writes device rows in MAC order, the order in which the assignment writers lock them.

**Vendor (optional OUI file).** ARCILOGGER does not ship or download the IEEE registry. The
administrator may download the IEEE Registration Authority's public **MA-L** listing (CSV or
text; find the current link on the IEEE RA website — *verify*), copy it to the server as
**`C:\ProgramData\ARCILOGGER\oui.csv`** and set the setting `devices.oui_file` to that path
(`arcilogger-db settings set devices.oui_file C:\ProgramData\ARCILOGGER\oui.csv --by NAME`; a text
value needs no JSON quotes, docs/ILK-ADIMLAR.md section 6). Put it there and nowhere under the
data root: the installer gives `NT SERVICE\ArciWorker` inherited read rights in
`C:\ProgramData\ARCILOGGER` (ADR-0007 section 4), while `D:\ARCILOGGER` is protected and grants
the services only "list this folder" — a file in a folder created there later (e.g.
`D:\ARCILOGGER\config`) is unreadable for the worker and the vendor stays empty. Check with
`icacls C:\ProgramData\ARCILOGGER\oui.csv`: it must list `NT SERVICE\ArciWorker:(I)(RX)`. Accepted:
IEEE CSV (`Registry,Assignment,Organization Name,…`, MA-L/MA-M/MA-S), the IEEE text listing
(`00-00-0C   (hex)   Cisco …`) and a simple `AABBCC<TAB>Vendor` file; the longest prefix wins;
unrecognised lines are skipped and counted. The file is re-read when it changes. Without it the
vendor stays empty; randomised MACs never get a vendor.

### IP conflicts (`correlation/conflicts.py`)

Two intervals of the same IP with different MACs that overlap for a positive time (`[start, end)` —
touching is not overlapping). Infrastructure MACs (setting `devices.infrastructure_macs` or the
device flag) are excluded (the core router answers on many VLANs, proxy ARP). Alert `ip_conflict`,
severity `medium`, deduplicated per IP, MAC pair and site day of the overlap start
(`ip_conflict:<ip>:<mac>,<mac>:<day>`): a rebuild never raises it twice, also not after an
administrator dismissed it. A rebuild of a day sees all of that day's evidence: an **active** alert
of that day (open, or acknowledged — İnceleniyor — by a person looking into it) whose conflict it no
longer finds (late evidence closed one of the leases earlier) is set to `resolved` with
`details.auto_resolved = {at, reason: "lease_rebuild"}` and `resolved_at`; if a later rebuild finds
the conflict again, that alert is re-opened (`details.reopened_at`). Both changes are `alert_events`
rows by `system` (reasons `lease_rebuild`, `conflict_found_again`; docs/ALERTS.md section 8). Alerts
a person resolved or dismissed are never changed. The incremental job only raises alerts.

---

## 2. Attribution chain (spec 9.4, `lookup/attribution.py`)

`attribute(session, ip, at)` → lease → device → person / department, or `None`.

- A closed lease covers `start ≤ at < end`. An **open** lease covers `at` only until it would
  time out without further evidence (`last_evidence + lease timeout`, the formula of section 1:
  for an `fg_dhcp` lease with a logged lease time its last DHCP evidence + `lease_seconds`; ARP:
  `last_evidence + 1 h`), so a stale open row never answers for a later moment. An `fg_dhcp`
  lease whose newest kept row of its pair at or before `at` has a logid that the settings now map
  to `deassigned` (a Release logid added after the build) does not cover `at`: what covers it is
  what the rebuild would open after that Release (the pair's ARP interval, from its `mt_arp`
  sightings), else nothing (`no_lease`, `chain_missing`) until the leases are rebuilt.
- **Several MACs** at `at` = IP conflict: the most likely chain (non-infrastructure, then DHCP
  before ARP, then the newest assignment; a MAC named in the request first) with the others in
  `alternatives`; that chain **and every alternative** have confidence **low** and the
  explanation `ip_conflict` (no device may read as more reliable; step confidences stay).
- **Confidence**: lease (`high` DHCP of either table, `medium` ARP) · device (`high`, `medium`
  for a randomised MAC) · person (`medium`: an administrative link, not log evidence); the chain
  has the weakest.
- **Last DHCP evidence** (`LeaseInfo.last_dhcp_evidence`, printed in the PDF with the lease time):
  the lease's **own** newest DHCP record at or before `at` — its opening Ack, a renewal or its
  own Release; never the line that ended a `superseded` lease (another device's Ack, or this
  MAC's Ack for another address) and never a renewal logged after `at`. For an `fg_dhcp` lease it
  is read from the `fg_dhcp` rows of the pair in `[start, at]` (one query per chain; the stored
  refs keep only the opening Ack and the last 6 continuations), otherwise from the stored refs.
- **Lowered to `low`** (the lease and the chain, or the chain with all its alternatives), each with
  its explanation:
  - `lease_renewal_not_seen` (an `fg_dhcp` lease): the moment lies more than half the lease after
    its last DHCP evidence, with no renewal in between. A client that still holds the address
    renews at half the lease; without that Ack the session may come from another device (a
    static device in the pool), and the records cannot show which (D6);
  - `fortigate_dhcp_no_network` (an `fg_dhcp` lease): the address lies in no configured network,
    where a declined address cannot be detected (D33);
  - `dhcp_sources_overlap`: the chain's network has usable DHCP rows of two origins (`MikroTik
    DHCP`, `FortiGate DHCP (VDOM <vd>)`) within W of the moment on either side, W the covering
    lease's alive window and at least the longest configured DHCP window (D27, alert
    `R20-lease:dhcp-sources-overlap`); for an address in no configured network the address
    itself stands for its network;
  - `fortigate_dhcp_not_converted_since_lease` (an `mt_dhcp` or `fg_dhcp` chain still `high` or
    `medium` after the steps above): after the lease's last DHCP record and before the moment
    there is a FortiGate DHCP record of the chain's IP or MAC that the lease timeline did not
    apply to this lease: (a) a mapped FortiGate DHCP line that stayed an `fg_event` row (loaded
    before the upgrade, loaded by the previous version after a rollback, or fields that could not
    be read), of either action, from a trusted source, unless an excluded interface, an unlisted
    `dhcp_msg` or a zero, broadcast or multicast MAC makes it no lease evidence — one matched by
    the MAC counts unless both addresses lie in configured networks and those differ; (b) a kept `fg_dhcp` Ack of the chain's MAC for another
    address that D33 would have applied with today's networks (a network entered or edited after
    the build); or (c) a kept `fg_dhcp` Ack of the chain's IP for another MAC (an `mt_dhcp` chain:
    any MAC) that the build refused and today's settings accept (a sender or HA member configured
    later, a logid mapped later). The record may be a release, a hand-over to another device or
    the MAC taking another address. Nothing of it is cited or shown (it may be another client's);
    its windows are in section 7;
  - `source_clock_offset`: a cited source's clock was off at the moment (the median of `time −
    time_received` over its rows nearest the moment, at least 10 rows, beyond 120 s, or beyond
    max(120 s, the lookup tolerance) for a source measured only for the session search; D28);
  - `srcmac_mismatch`: below.
- **FortiGate notes on a chain** whose lease is `fg_dhcp` (info): `fortigate_format_unverified`
  while `dhcp.fortigate_format_verified` is off, `fortigate_format_verified` (who marked it and
  when) once it is on ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4). A chain whose cited DHCP
  rows come from a sender configured later carries `source_configured_later` (info).
- **Department**: the linked person's department, else the VLAN's
  (`arcilogger_db.network.vlan_department` — the same rule as the usage reports).
- **Person at T** (assignment history, ADR-0008, `arcilogger_db.assignments`): the device's
  `device_assignments` row covering `at` (`valid_from ≤ at < valid_to`, open rows to infinity)
  names the person — `PersonInfo.link = "history"` with `assigned_from` / `assigned_to`; a later
  reassignment changes nothing for an earlier moment. A device **with** history but no row at
  `at` (assigned only later, or unassigned then) names **nobody**: `person_unassigned_at_time`
  (department from the VLAN), never the owner of another time. Only a device without any history
  row but with `devices.person_id` (written outside the assignment module) falls back to that
  current owner, `link = "current"`, and the outcome carries `person_link_at_lookup_time`. A row
  the migration backfilled from such a link (`backfilled`, start = the device's first sighting)
  adds `person_link_backfilled`. One person may hold several MACs (PC, phone, the phone's next
  randomised MAC): each device has its own history.
- **Department of a person**: always the person's **current** department — no department
  history is kept; for history links the outcome says so (`person_department_current`).
- Explanations added to a chain: `guest_vlan` (a VLAN marked `guest`, e.g. a guest Wi-Fi with
  another exit than the FortiGate: no FortiGate records), `arp_only`, `randomized_mac`,
  `infrastructure_device`, `no_person`, `person_unassigned_at_time`, `person_link_backfilled`, and
  the FortiGate and srcmac explanations of this section.

**srcmac: partial evidence, never a lease** (ADR-0016 D10, `lookup/sessions.py`). With device
identification on a FortiGate interface ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5), its
session records carry the client's MAC (`srcmac`). The lookup uses it only when the session row's
`srcintf` is listed in `lookup.srcmac_interfaces` (default `[]`: off), the MAC is no
infrastructure MAC, it was seen with at most `lookup.srcmac_max_ips` (default 1) distinct IPv4
source addresses on that interface within ±1 hour of the moment (a router's or switch's MAC is
seen with many; a dual-stack client's IPv6 addresses are not counted; a used row logged outside
that hour, such as a long session logged at its end, adds its own address), no lease of that MAC
on another IP covers a moment of that hour, and the row comes from a configured FortiGate source.
`TrafficInfo.srcmac` of a stored outcome is filled only for listed interfaces.

- A match or an internal IP **without a lease**: the MAC, device and person come from that row,
  confidence `low`, `mac_source = "srcmac"`, explanation `srcmac_only` ("kısmi kanıt": the
  FortiGate associated the source IP with this MAC; it does not prove that the MAC sent the
  session). Several MACs for one internal IP give `srcmac_ambiguous` and no chain; a MAC-only
  internal lookup reads the srcmac rows of the MAC within the tolerance (several IPs:
  `mac_multiple_ips`).
- A chain **with a lease**: `srcmac_agrees` when the MACs are equal; when they differ, an IP
  conflict of its own: the lease chain first, the srcmac chain as its alternative, both `low`,
  `srcmac_mismatch` (a static device on a pool address looks exactly like this).
- Refused srcmac values are named: `srcmac_shared`, `srcmac_infrastructure`, `srcmac_other_lease`.
- srcmac never reaches `leases`, `devices`, IP conflicts or usage.

## 3. 5651 lookup (spec 9.3, 9.5, `lookup/engine.py`)

`run_lookup(session, request, settings, *, layout=None, now=None)` — only reads; the API stores
the outcome (`lookups.outcome`) and writes the audit entry. `layout` (the data directory) lets
the data window say whether a day is in the sealed vault.

| Mode | When | What |
|---|---|---|
| `exact` | the requested public IP is the FortiGate's own address by the exits (below: a `direct` exit's public address, an exit's NAT address, or an address the logs show as the FortiGate's `transip` overlapping T ± 15 min, `observed`) **and** a port is given, and the log check passes | `transip = IP AND transport = port` (index `(transip, transport, time)`), score 100, explanation `exact_nat`. With a requested destination only the sessions on that port that went to it; sessions whose logged destination IP/port differs are counted, never listed (`exact_other_destinations`, or `exact_only_other_destinations` when none is kept); none → `none` + `destination_not_found`. A **domain-only** request is not filtered by a missing log: a missing DNS/web/app log for the name (another resolver, encrypted DNS, a cached answer) does not show another destination. Sessions tied to the name are listed; if none is, the sessions on the port (to the requested port, if given) are listed as `domain_unverified` with `exact_domain_unverified` ("hedef doğrulanmadı"); if some are, the untied ones are counted neutrally (`exact_unverified_destinations`). Without destination every session on the port is listed (`multiple_exact`) |
| `candidates` | double NAT: the address belongs to an ISP NAT exit (`isp_nat`), or no exit claims it (`public_unknown`, `unconfigured`); a `direct` exit or NAT address whose log check fails (`fallback`, `public_ip_not_transip`: the ISP router still translated, e.g. a moment before the switch to bridge mode); the FortiGate's own address without port; or only a destination | sessions in the window, ranked (below); explanation `double_nat` (spec 12.4 wording, only together with candidates), `port_missing`, `destination_search` and the exit notes (below) |
| `internal` | internal IP and/or MAC, no public IP | the attribution chain (`attribution`), no matches |
| `none` | nothing found, or nothing searched: an address no exit claims and no exit is searched for (`public_ip_no_egress`), a line that does not pass the FortiGate (`egress_not_logged`), a NAT address that is no internet exit (`egress_internal`) | why only a candidate search was possible (`double_nat_no_candidates` — the 12.4 first clause without "listed below" —, `port_missing`, `destination_search`), the exit note, `no_match`, `destination_not_found` when a destination was requested, and what would help: `no_match_exact`, `no_match_candidates`, `no_lease` (with the nearest leases before/after), `no_lease_for_mac`. No text in this mode promises a list. |

**Session matching** (contract): a row's interval is `[time − duration, time]`; it matches when
it overlaps `[at − tolerance, at + tolerance]`. Every query is bounded to `time BETWEEN at −
tolerance AND at + tolerance + lookup.max_session_seconds`. Interim updates (`0000000020`) and
the final row of one session (same `sessionid`, 5-tuple and NAT pair) are **one** match; its
interval spans all its rows and its evidence cites up to 10 of them (nearest to `at`, first,
last). Rows before `at − tolerance` are outside the scan; with cumulative `duration` in interim
rows (*verify*) the session start is still exact.

`public_ip_not_configured` and `wan_transip_not_seen` of earlier versions are no longer given;
stored lookups that carry them still show their texts.

### Çıkışlar / Exits (`nat.egresses`, `arcilogger_db.egress`, `engine._resolve_egress`; ADR-0016)

The setting `nat.egresses` (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi →
İnternet çıkışları (NAT); at most 8 exits) names each internet line: `mode` — `direct` (the
FortiGate holds the public address: exact), `isp_nat` (an ISP router translates once more:
candidates), `not_logged` (the line does not pass the FortiGate: no records) or `internal` (not an
internet exit: the FortiGate's source-NAT address towards a DMZ, another VLAN, a VPN or a branch;
never searched) —, `public` (IPv4 addresses or networks of at least /24, never private, shared or
reserved), `public_dynamic` (the address may change; `isp_nat` only), `transips` (the FortiGate's
NAT addresses on that line; `isp_nat` and `internal`), `interfaces` (the `dstintf` names, needed
with two or more lines, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.6), `port_preservation` and
`label`. `{}` (the default) is one implicit double-NAT exit. **Claimed** are every exit's
`transips` and every `direct` exit's `public`; **unclaimed** is a session whose `transip` is a
private or shared address (RFC 1918, RFC 6598) that no exit claims, with a `trandisp` listed in
`nat.snat_trandisp`. An unclaimed **public** `transip` is the FortiGate's own address (an IP pool,
a line nobody entered): it is answered as observed when it is asked about, and never searched as
a candidate for another address. The upgrade converted the old NAT settings into exits
([INSTALL.md](INSTALL.md) section 13).

**Which exit answers.** The requested public IP P is matched against every exit's `public`, then
against every exit's `transips`. An unclaimed P that the logs show as the `transip` of a trusted
session overlapping T ± 15 min is the FortiGate's own address at that time (`observed`), with or
without exits. Any other unclaimed P is searched in the `isp_nat` exits without a public address
or marked `public_dynamic` (`public_unknown`); without such an exit the answer is `none`
(`no_egress`). With no exits at all, P is searched over the unclaimed predicate (`unconfigured`).

**The log check** (one cached probe pass per lookup, trusted sources only, ±max(900 s,
tolerance) by row time): a `direct` public address or NAT address P of exit E passes when the
logs show P as `transip` near T, or show no private or shared NAT address outside E's own set
(E's own addresses and those of the other exits, except the `isp_nat` exits without a public
address and, when both declare interfaces, the exits on other interfaces), counting only rows on
E's interfaces when E declares any. It fails over to `fallback`. When P is seen but such an
outside address is seen as well ("seen, but not alone": near a change of the line's mode both
sides are in the window), the check passes, and the answer still becomes `fallback` when no
session through P spans T itself and the search on P (P + port, or P without a port, before any
destination filter) finds no session; a session through P at T keeps the exact answer, and an
empty exact search then stays `none` with `no_match_exact`.

| Resolution (`outcome.egress.resolution`) | Port | Search | Mode | Notes | `double_nat` |
|---|---|---|---|---|---|
| `public`, `direct` exit, check passes | yes | exact `transip = P AND transport = port` | exact / none (`fallback` instead of none when "seen, but not alone") | `exact_nat` | false |
| `public`, `direct` exit, check passes | no | candidates `transip = P` | candidates (`fallback` instead of an empty list when "seen, but not alone") | `port_missing` | false |
| `public`, `isp_nat` exit | any | candidates over E's `transips` (a catch-all exit without `transips`: the unclaimed predicate), on E's interfaces when declared; E's addresses not seen near T while unclaimed ones are: E's `transips` ∪ unclaimed (`egress_transip_not_seen`) | candidates | `egress_search`, `double_nat`; `egress_public_dynamic` when E is marked `public_dynamic` | true |
| `public`, `not_logged` exit, P not seen | any | none | none | `egress_not_logged` | false |
| `observed`, `not_logged` exit (P seen as a session's `transip` near T) | yes / no | as `observed` below | exact / none / candidates | `egress_not_logged_but_seen`, `exact_nat` or `port_missing` | false |
| `transip` (P is an exit's NAT address), check passes | yes | exact on P | exact / none (`fallback` as above) | `exact_nat`, `egress_search` | true |
| `transip`, check passes | no | candidates `transip = P` | candidates (`fallback` as above) | `port_missing`, `egress_search` | true |
| `transip`, `internal` exit | any | none (no check, no search) | none | `egress_internal` | false |
| `fallback` (a `direct` or `transip` resolution whose check fails, or "seen, but not alone" with no session) | any | candidates over E's own addresses ∪ the `transips` of F (the `isp_nat` exits without a public address that share an interface with E, or, when neither declares interfaces, both as converted by the upgrade); F empty or a catch-all: ∪ the unclaimed predicate; every part on E's interfaces when E declares any | candidates | `public_ip_not_transip` (+ `egress_transip_not_seen` when F's addresses are not seen) | true |
| `observed` (unclaimed P seen as a session's `transip` overlapping T ± 15 min) | yes | exact `transip = P AND transport = port` | exact / none | `egress_observed_not_configured`, `exact_nat` | false |
| `observed` | no | candidates `transip = P` | candidates | `egress_observed_not_configured`, `port_missing` | false |
| `public_unknown` | any | candidates over the exits that are searched for unknown addresses, each on its own interfaces and widened as an `isp_nat` exit is when its addresses are not seen | candidates | `egress_public_unknown`, `double_nat` | true |
| `no_egress` | any | none | none | `public_ip_no_egress` | false |
| `unconfigured` (no exits) | any | candidates over the unclaimed predicate | candidates | `double_nat`, `egress_unconfigured` | true |

- **Interfaces.** When E declares `interfaces`, `dstintf = ANY(E.interfaces)` applies to every
  part of E's search, so a question about one line's address never lists another line's
  sessions. It is dropped only on positive evidence that the names are wrong: rows of E's own
  addresses near T exist and none carries one of E's interfaces (`egress_interface_not_seen`); an
  idle line keeps the condition. An exit that shares a NAT address with another exit keeps it
  even then (`egress_interface_not_seen_kept`). Interfaces never apply to exact mode.
- **Unclaimed addresses seen near T** while exits are configured are named
  (`egress_unmapped_seen`, at most 5), the requested address left out only when it was answered
  as `observed`; sessions with a `transip` and a `trandisp` not in `nat.snat_trandisp` in a search
  over the unclaimed predicate are counted and named (`nat_unknown_trandisp`).
- **Every match** carries its exit (`NatMatch.egress`), taken from its row nearest T: the exit
  that claims that row's `transip` (a shared one: the first exit whose interfaces hold the row's
  `dstintf`), else none; a session whose rows resolve to different exits gets
  `session_changed_exit`. A match with a row of an unconfigured sender is marked on its own row
  (`NatMatch.untrusted_sources`) and the outcome carries `session_from_unknown_source`.
- **Exits changed after the event:** from the change record `nat.egresses_changes`, an exit of
  the answer whose content (mode, public, transips, public_dynamic) changed after T, or, for a P
  no exit claims now, any exit changed after T (deleted ones included), gives
  `egress_changed_after_event` with the time and author of each change; the earlier definitions
  are in the audit log (`settings.change`). Exits are not valid from or to a date (section 7).
- **`double_nat`** of a request without a public IP (an internal-IP, MAC or destination-only
  lookup) is true when no `direct` exit exists (or `nat.egresses` cannot be read).
- `outcome.egress` stores the resolution, the exits, what was searched and the exits' last
  changes; the PDF prints the exit row and one method paragraph per resolution
  ([RAPOR.md](RAPOR.md)).

**Candidates.** Only the source-NAT sessions of the resolution's search (the table above: an
exit's own addresses, its fallback exits' NAT addresses, or the unclaimed predicate), optionally
of the requested protocol; a session that left with any other address did not leave through the
requested address and is never listed. With a destination the search is restricted to it: `dest_ip` (index `(dstip, dstport, time)`), `dest_domain`, or only
`dest_port`. **When nothing goes to the requested destination the answer is `none`** with
`destination_not_found` (spec 2.3: candidates "to the given destination … if the request
contains one"; listing every client of the window would put unrelated persons into an official
answer — data minimisation, spec 3.3). The deliberate way to see the whole window is a request
without destination (`candidates_without_destination`); the explanation says so. An internal IP
or MAC given together with a public IP restricts the candidates to that client. Only sessions
that left with an address (`transip`, else the source) of the requested public IP's family are
candidates: no IPv4 session — the FortiGate's IPv4 WAN address included — is ever a candidate
for an IPv6 address (defence in depth behind the API's refusal, section 7).

**Domains.** Addresses of the name around the moment: DNS answers for the name and `www.` +
name (`fg_dns`, index `(qname, time)`) and the destinations of web filter / application control
logs with that host name or a sub-domain, both from `lookup.dns_lookback_seconds` (default 3600)
before the window to its end. A session whose **own** web/app log names the domain (same
`sessionid` and source IP, written within the session's span ± 60 s) is found inside the
bounded `fg_traffic` scan by a correlated `EXISTS` (index `(sessionid)` of `fg_web`/`fg_app`) —
never by collecting session ids, so a popular domain costs no more than the window scan (the
earlier version failed with more than 65,535 bind parameters). Every list of values is passed as
one array parameter (`= ANY(…)`). The lookup switches PostgreSQL JIT compilation off for its
transaction (`SET LOCAL jit = off`): the planner's cost estimate of the correlated sub-queries
crosses `jit_above_cost` and compiling took longer (0.8 s) than the query.

**Score** (integers; exact = 100, candidates at most 95):

| Part | Points |
|---|---|
| Time | 40 when `at` is inside the session, falling linearly to 0 at the tolerance |
| Destination | IP + port 50 · domain + port 50 · domain 45 · IP (no port asked) 45 · domain, other port 40 · only the domain's address known (another client's DNS/web log; CDN addresses serve many names) 35, with port 40 · IP, other port 30 · port only 20 · nothing requested 25 (neutral) · requested, not matched 0. The weaker kinds keep their own `destination_match` label and report text: `ip_other_port`, `domain_other_port`, `domain_address`; `domain_unverified` (exact mode only, see above) |
| Port hint | +5 when the candidate's exit has `port_preservation` `preserved` (measured on site, [DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.6) and the FortiGate port equals the requested public port; explanation `port_hint` with the exits' names; only a ranking, never an exact match |

Order: score ↓, destination match, time distance ↑, session start, source IP, source port,
session id, row id — deterministic. At most `lookup.max_candidates` (100) matches; beyond that
`truncated = true` with the explanation `truncated` (`total`, `shown`).

**Data window** (`lookup/window.py`): first/last day in the database, whether the requested day
has rows, whether its sealed manifest exists. Explanations: `not_in_database` /
`only_in_vault` / `no_data_for_day`, `older_than_db_retention` (`db.retention_days`),
`future_time`, and `lease_timeline_behind` when the requested window is after the lease
watermark. Re-ingesting a sealed day ("arşivden yeniden yükle") is a later feature.

**FortiGate DHCP lines that did not become lease evidence** (D8). When no lease chain could be
built (an internal lookup with `no_lease` or `no_lease_for_mac`; a match with `chain_missing`, or
with a srcmac chain only), one probe per lookup (at most 20 addresses; a MAC-only lookup by its
MAC) takes, per address, the **newest** DHCP-shaped row before the moment from a trusted source,
in `fg_event` (logids of `dhcp.fortigate_logids`; lines of excluded interfaces, with a
`dhcp_msg` that `dhcp.fortigate_dhcp_msgs` does not list, or with a zero, broadcast or multicast
MAC are left out, while a missing `dhcp_msg` passes) or `fg_dhcp`, within
max(the timeline's longest window, 604,800 s) (section 7). Only an `fg_event` row of a logid
mapped to `assigned` that would have covered the moment by the lease formula of section 1 counts:
the outcome gets `fortigate_dhcp_not_converted` (the addresses; the possible reasons: loaded
before the upgrade, fields that could not be read, loaded by the previous version after a
rollback). In internal mode and for an exact match the row is cited
(`LookupOutcome.unconverted_dhcp`, "kira zaman çizelgesine girmedi; zincir kurulmadı, güven
düzeyi verilmez"), never as an attribution; for candidate matches nothing is cited (other
people's DHCP lines would enter the report). A newer Release, a newer converted row (also one
at the same second) or a row after the moment means nothing is said.

All explanation texts (Turkish and English, no legal claims) are in `lookup/texts.py` and
`lookup/types.py` (`EXPLANATIONS`); `params` keep machine values (UTC ISO times, integers).

## 4. Worker service (`service.py`, `cli.py`)

| Job | When |
|---|---|
| `partitions.ensure` | daily after 00:15 and at start-up — `arcilogger_db.partitions.ensure_partitions` (no-op with TimescaleDB) |
| `license.check` | every 5 minutes — the licence state recomputed (`arcilogger_db.licensing.load_status`, never cached); a state change and every limit crossing since the last events become `license_events` rows and audit entries (`license.state`, `license.limit_exceeded`, `license.limit_ok`), each change once; the licence alerts opened and resolved in their own transaction (`R20-license`, `R20-license-limit`, `R20-license-maintenance`: docs/ALERTS.md; a failure there is only logged and reported as `alerts.error`, the job does not fail); `state/license-status.json` rewritten for the installer. Details `{"status", "enabled", "recorded", "alerts"}`. If the licence code cannot be loaded the job fails like any job (retries, `R20-job`); the other jobs run on (docs/LICENSING.md) |
| `leases.incremental` | every `[worker] interval_seconds` (default 60 s) |
| `leases.nightly` | daily after 00:05 — rebuild yesterday and today |
| `leases.recheck_today` | hourly — rebuild today (late evidence of today) |
| `leases.late_evidence` | every 10 minutes — rebuild older days whose evidence arrived after their rebuild (section 1) |
| `evidence.purge` | daily after 03:00 — deletes expired evidence packages (`evidence.keep_days`, 30 days; `arcilogger_worker.evidence_jobs.purge_evidence`, audited as `evidence.purge`) |
| `audit.import` | every 15 minutes — copies the results of the database-free scheduled sealing and retention runs into the audit log, each event once (`seal.run`, `retention.run`; docs/KANIT-PAKETI.md), and the browser administration's journals: the agent's `admin\journal\` (`admin.*`, `settings.secret_change`) and ApplySystem's `admin\system\journal.jsonl` (`admin.system`), read from the `admin` folder next to the worker's `--config` file (ADR-0014, `arcilogger_worker.admin_jobs`) |
| `admin.requests` | every 15 seconds — the browser administration's test e-mails and lease rebuilds (`admin_requests` rows of `channel = 'worker'`, claimed with `FOR UPDATE SKIP LOCKED`; a lease rebuild runs day by day as `leases.rebuild` job runs with the request's id; audited as `smtp.test` / `leases.rebuild`; ADR-0014, `arcilogger_worker.admin_jobs`) |
| `alerts.import` | every minute — new lines of `state/alerts.jsonl` and of the browser administration's `admin\status\alerts.jsonl` (the agent) and `admin\system\alerts.jsonl` (ApplySystem) into `alerts`, each line once (a byte offset per file in the internal settings `worker.alerts_import`, `worker.alerts_import.admin` and `worker.alerts_import.system`, written with the alerts; docs/ALERTS.md) |
| `alerts.r15` | every minute — R15 log source silent (`sources.last_seen`, the collector status, `[[sources]] heartbeat_minutes`; `alerts.r15_*` settings) |
| `alerts.system` | every 5 minutes — the R20 checks: disk, sealing, verification, credit, replica, parse failures, failing jobs, database backup, clock (`alerts.*` settings), the lease evidence and the internet exits (`R20-lease:<cause>`, `R20-nat:<cause>`, each check in its own transaction, so a NAT problem never stops a lease alert; ADR-0016, docs/ALERTS.md section 2), and the heartbeat of the administration agent ArciAdmin (`R20-admin` when `admin\status\agent.json` is older than 2 minutes or missing, from 5 minutes after the worker's start; ADR-0014) |
| `alerts.notify` | every minute — alert e-mail through `[smtp]` to `alerts.email_recipients` (`alert_deliveries`; skipped while `[smtp] enabled = false`; ADR-0010) |
| `usage.requests` | every 30 seconds — usage rebuild requests of the web interface (`usage_rebuilds`, `POST /api/v1/usage/rebuild`; ADR-0011, docs/USAGE.md) |
| `usage.daily` | daily after 00:45 — yesterday's usage rollup rebuilt as `final` |
| `usage.today` | every 5 minutes — today's usage rollup from the logs loaded so far (`partial`) |
| `usage.reattribute` | every 10 minutes — any of the last `usage.auto_rebuild_days` days whose device owners or leases (who an address belonged to when), late logs, `usage.*` settings or engine changed, or that is missing or still partial, is rebuilt; uses the attribution chain of section 2 at every moment the owner can change |
| `usage.retention` | daily after 03:30 — usage rollups older than `usage.retention_days` (400) |
| `reports.run_due` | every minute — scheduled usage reports: due schedules queued (one run for the newest occurrence after downtime, `reports.catch_up`), queued runs rendered (HTML, PDF, Excel into `exports/usage-reports/`) and e-mailed one per recipient (`report_runs`; ADR-0012, docs/USAGE-REPORTS.md) |
| `reports.purge` | daily after 03:20 — deletes the files of report runs older than `reports.keep_days` (400 days); the run rows and the hashes stay |
| `search.counts_backfill` | every 10 minutes, no new file after 60 s — the finished spool files loaded before revision `0016_search` (`ingest_offsets.counts_from_line` set) are counted into Arama's summary table `search_counts`, newest day first, one transaction per file: its `ingest_offsets` row locked and read again, `arcilogger_db.searchcounts.rebuild_stream`, the mark cleared (a second worker or `arcilogger-db search-counts rebuild` running at the same time waits and then finds the file done). A file still being loaded waits until it is finished (`waiting`); meanwhile the statistics of its days are counted directly. `skipped` when nothing is to count (a fresh installation never has anything). Details `{"files": [{"source", "day", "lines", "keys"}], "lines", "waiting", "more", "left"}` (docs/INGEST.md "Search counts", ADR-0015) |

**Licence gates** (ADR-0013, docs/LICENSING.md section 2.3): the jobs of a paid module —
`usage.requests`, `usage.daily`, `usage.today`, `usage.reattribute` (Kullanım analizi,
`usage_analytics`) and `reports.run_due` (Zamanlanmış kullanım raporları, `usage_reports`) — ask
the licence before their `running` row is written. While the module is off the job does not run:
its first skip of a site day is one `skipped` row with the details `{"license": {"module",
"status"}}` (for a daily job the run that counts as done), later ones only move its next due time;
one INFO line per job when its gate changes (`job usage.today skipped: module usage_analytics not
licensed (trial_expired)` / `job usage.today runs: module usage_analytics licensed`). When
`reports.run_due` is locked an active `R20-report` is resolved with the note "Zamanlanmış raporlar
lisansta kapalı / Scheduled reports are off in the licence". The status of the gates is kept 60 s
per worker process; a failed licence evaluation is never cached and locks only the paid jobs.
Every other job (the 5651 jobs, `license.check`, and the data-minimisation jobs `usage.retention`
and `reports.purge`) never asks and runs in every licence state; skipped runs do not count for
`R20-job`.

Daily jobs run once per site day as soon as their time has passed, also right after a start-up
(the last successful run is read from `job_runs`). Every run is a `job_runs` row (`running` →
`ok` / `failed` / `skipped`, JSON details with counts and `duration_ms`). A failing job never
stops the loop and is **retried with a growing delay**: daily jobs after 15 min, 30 min, 1 h,
2 h, then every 4 h; interval jobs after their interval, doubling up to 30 min (never sooner
than their interval). A success resets it. While the database is unreachable a job is retried
after 1 minute. Status for the dashboard: `state/worker-status.json` (state, database
reachability, per job last start/finish/status/error/next due, `failures`, `retry_at`). Stop:
Ctrl+C, service stop, SIGTERM (Windows also Ctrl+Break); the loop checks every second.

Commands: `arcilogger-worker run`, `arcilogger-worker rebuild --day YYYY-MM-DD [--to
YYYY-MM-DD]`, `arcilogger-worker usage rebuild --from YYYY-MM-DD [--to YYYY-MM-DD]` (usage
rollups, one `usage.rebuild` job run; refused with exit code 1 and the `module_not_licensed`
message while Kullanım analizi is off — the only gated command), `arcilogger-worker version`; exit codes 0 ok, 1 failed, 2 configuration/usage
(also lease settings the timeline cannot work with, e.g. a lease timeout shorter than the 300 s
traffic bucket), 3 database unreachable. A failed day is recorded as a `failed`
`leases.rebuild` run with the error type and message, never left `running`.

## 5. Settings used

Shipped (`arcilogger_db.settings.DEFAULT_SETTINGS`): `lookup.tolerance_seconds` (`60`),
`lookup.max_candidates` (`100`), `lookup.max_session_seconds` (`86400`),
`lookup.dns_lookback_seconds` (`3600`), `lease.default_seconds` (`86400`),
`lease.server_seconds` (`{}`), `lease.timeout_percent` (`110`), `dhcp.server_vlan_regex`,
`arp.interval_seconds` (`3600`), `arp.ignore_after_release_seconds` (`300`; 0 switches the rule
off; at most `arp.interval_seconds`), `devices.infrastructure_macs`, `devices.oui_file` (`""`),
`worker.leases_lag_seconds` (`120`), `db.retention_days` (`400`), and the keys of ADR-0016 in the
table below. Internal, written by the worker (author `:system`; not settings, not for editing):
`worker.leases_watermark`, `worker.leases_ingest_seen`. Status rows (not settings, never listed):
`nat.egresses_migration` (the upgrade's conversion of the old NAT settings), `nat.egresses_changes`
(the last content change of each exit, section 3) and `leases.rollback_lowered` (the rollback
step, section 7).

**Changing a setting:** on **Ayarlar → Uygulama ayarları** (Settings → Application settings,
[SETTINGS.md](SETTINGS.md) section 1), or on the server console with
`arcilogger-db settings set KEY JSON_VALUE --by NAME` (`list`, `get KEY` show the values). The installer does not put `venv\Scripts` on `PATH`,
so in an administrator PowerShell call it with its full path:
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set KEY JSON_VALUE --by NAME`
(the configuration is read from `C:\ProgramData\ARCILOGGER\arcilogger.toml`). It validates the key and the value, writes
`updated_by = NAME` — so `arcilogger-db upgrade` never puts the default back — and appends
`settings.change` with the old and new value to the audit log. A hand-written SQL change is not
validated, not audited, and reverted by the next upgrade while the row still says `:seed`.

**FortiGate DHCP evidence, srcmac and internet exits (ADR-0016).** Shipped as well, read by the
lease timeline, the lookup and the lease and NAT alerts of ADR-0016:

| Setting | Default | What it decides |
|---|---|---|
| `dhcp.fortigate_logids` | `{"0100026001": "assigned", "0100026002": "deassigned"}` | which FortiGate DHCP server events are lease evidence and what each means (10-digit logids, `assigned` or `deassigned`, at most 16); `{}` switches FortiGate DHCP evidence off; a change applies to past days when the leases are rebuilt (sahada doğrula: the RELEASE logid is derived) |
| `dhcp.fortigate_dhcp_msgs` | `{"0100026001": ["Ack"]}` | per logid, the `dhcp_msg` values its rows may carry (1–8 values of 1–32 characters, case-insensitive); a row with another value is refused and counted; a logid without an entry is not restricted |
| `dhcp.fortigate_use_logged_lease` | `true` | the lease time of the ACK (300–8,640,000 s) ends the lease that long after its last DHCP record, without the `lease.timeout_percent` margin; other values (`0` = unlimited, missing, out of range), and `false`, use the configured times with the margin |
| `dhcp.fortigate_traffic_extends` | `false` | whether traffic keeps a FortiGate lease open, as it does a MikroTik lease; on, another device using the address (a static device in the pool) is named as the former holder — the organisation's choice when renewals are not logged (DEVICE-SETUP.md section 2.4 step 5) |
| `dhcp.fortigate_interfaces_excluded` | `[]` | FortiGate DHCP interfaces whose rows are no lease evidence (DHCP over IPsec; at most 32 names) |
| `dhcp.fortigate_format_verified` | `false` | the site check of the FortiGate DHCP record format is done (DEVICE-SETUP.md section 2.4); lookups and reports name who marked it and when |
| `lookup.srcmac_interfaces` | `[]` | the `srcintf` names whose FortiGate srcmac may name a chain's MAC, at `low` confidence and as partial evidence, never as a lease; never a routed (L3) segment, with layer-2 switches in between only after the two-client check of DEVICE-SETUP.md section 2.5; at most 32 names; `[]` = off |
| `lookup.srcmac_max_ips` | `1` | the most IPv4 addresses one srcmac may show within ±1 hour of the moment (1–16) before it counts as a router's or switch's |
| `nat.egresses` | `{}` | the internet exits (direct, ISP NAT, not through the FortiGate, or an internal NAT address that is no internet exit) with their public and NAT addresses and interfaces (at most 8 exits; section 3); `{}` = one implicit double-NAT exit |
| `nat.snat_trandisp` | `["snat"]` | the FortiGate `trandisp` values a candidate search takes for NAT addresses no exit claims (1–8 values; sahada doğrula) |

After a change of a `dhcp.fortigate_*` key other than `dhcp.fortigate_format_verified`, rebuild
the leases from the first affected day (section 1, "Manual repair"). The upgrade to revision
`0018_nat_egresses` converted the old NAT settings `nat.public_ips`, `nat.wan_transip` and
`lookup.port_hint` into `nat.egresses` (audited as `settings.change`); an administrator's old
rows are kept, unread, for one version, so that `-Rollback` runs the previous version with its own
NAT values ([INSTALL.md](INSTALL.md) section 13). Labels, help texts and refusal codes:
`arcilogger_db.settings_meta` and `SETTING_ERRORS`; the exit rules: `arcilogger_db.egress`.

An unreadable `devices.oui_file` (a folder, no permission, missing) is logged once and treated
as no file: the vendor stays empty and the lease jobs carry on. On the Windows server the usual
cause is the file's location — see "Vendor" in section 1.

Also shipped, read by the API's 5651 lookup and report: `lookup.timeout_seconds` and
`lookup.report_timestamp` (`false`; docs/RAPOR.md). The alert jobs read the `alerts.*` settings
(recipients and policy of the e-mail, the R15 grace periods, the R20 thresholds); they are listed
with their ranges in docs/ALERTS.md section 3. The usage jobs read the `usage.*` settings
(thresholds, background rules, work hours, holidays, re-attribution window, retention; docs/USAGE.md
section 4); their subject step is the attribution chain of section 2 (`Attributor`, extended with
`covered_until`, `leases_overlapping` and `assignments` for the usage rollups). Internal, written
by `alerts.import` (author `:system`): `worker.alerts_import`, `worker.alerts_import.admin`,
`worker.alerts_import.system`. Operator steps and PowerShell quoting:
docs/ILK-ADIMLAR.md section 6. Settings are read from the database when used, so no service
restart is needed.

## 6. Performance (spec 14: lookup < 5 s for ±60 s)

`worker/tests/test_lookup_performance.py`, PostgreSQL 16 with native monthly partitions,
developer machine, warm cache (`VACUUM ANALYZE` after loading); uncompressed data (compressed
TimescaleDB chunks: below). The synthetic traffic is
**continuous** — it does not stop at midnight — so the scan tail of
`lookup.max_session_seconds` (24 h after the window) is full: 300 clients, sessions of 0–96 s
and one 2-hour session in 500, web filter logs for every fourth session (a fifth of them
`*.google.com`, the rest one of 5,000 rare sites), DNS answers for `www.google.com`, DHCP and
hourly ARP-LOG for 300 devices.

| Step | Default (`ARCILOGGER_PERF` unset): 1 M rows/day, 1.1 days | `ARCILOGGER_PERF=large`: 3 M rows/day, 2 days (6 M rows, 1.5 M web rows) |
|---|---|---|
| Loading the rows (`INSERT … SELECT`, all indexes) | 18.5 s | 125–129 s |
| `rebuild_day` of the first day (with look-ahead into the next) | 2.5 s | 7.3–7.5 s |
| Candidates, ±60 s, **no destination** (100 shown, chains included) | 0.47 s | 1.24–1.34 s |
| Candidates, popular domain (`google.com`) | 0.37 s | 1.11–1.52 s |
| Candidates, rare domain | 0.35 s | 1.08 s |
| Candidates to a destination IP + port | 0.02 s | 0.04 s |
| Exact (`transip`, `transport`) | 0.02 s | 0.02 s |

The default runs with the test suite (about 27 s; `ARCILOGGER_PERF=0` skips it); `large` takes
about 2.5 minutes and should be run before releases. `EXPLAIN` (asserted): no sequential scan;
the exact lookup uses `…_transip_transport_time_idx` with `transip`, `transport` and both time
bounds in the index condition; the destination search a bitmap scan of
`…_dstip_dstport_time_idx`; the window without destination and the domain search an index scan
of the partition's primary key `(time, id)` limited to `[at − 60 s, at + 60 s + 86,400 s]`, the
domain's correlated sub-queries an index scan of `fg_web`/`fg_app` `(sessionid)`.

**Compressed chunks (TimescaleDB).** On TimescaleDB the test above covers uncompressed chunks
only: it pauses the compression policies before loading. TimescaleDB compresses every chunk older
than 7 days (`segmentby source`, `orderby time DESC, id DESC`), and most 5651 requests ask about
such days. A compressed chunk's btree indexes hold only its uncompressed rows: the lookup finds
the compressed batches by their time range and decompresses them, so the plans above do not apply
there. `test_lookup_is_fast_on_compressed_chunks` (TimescaleDB with compression only; the Linux CI
job runs it) compresses every loaded chunk after the lease rebuild and checks the same answers and
the same 5 s budget; of the plan it checks only that the domain search's web/app sub-query bounds
the batches by each session's own start. TimescaleDB 2.30.2 on PostgreSQL 16, developer machine,
warm cache, `ANALYZE` after compressing:

| Step | Default: 1 M rows/day, 1.1 days | `ARCILOGGER_PERF=large`: 3 M rows/day, 2 days |
|---|---|---|
| Compressing the loaded chunks | 4.7 s | 26 s |
| Candidates, ±60 s, **no destination** | 0.41 s | 1.15 s |
| Candidates, popular domain (`google.com`) | 0.51 s | 1.56 s |
| Candidates, rare domain | 0.47 s | 1.45 s |
| Candidates to a destination IP + port | 0.27 s | 0.77 s |
| Exact (`transip`, `transport`) | 0.04 s | 0.06 s |

At `large` the domain search took 6.0 s (popular) and 7.2 s (rare) on compressed chunks, over the
budget, until the sub-query's bound on the session's start was written without `COALESCE` (a
sub-query for rows with a duration and one for rows without): TimescaleDB turns a bound into a
condition on the batches' time range only when it is plain arithmetic on the outer row, so each
of the window's 6,300 sessions read every batch of the web filter chunk. A cold cache was not
measured on compressed chunks either.

**Scaling.** The search without a specific destination IP reads every `fg_traffic` row in that
24-hour range (about `rows per day × max_session_seconds / 86,400` index entries plus their heap
pages) and keeps the few thousand whose session started before the window: 1.0 M rows read →
0.33 s, 3.0 M → 1.05 s of execution — linear. At 5 M rows a day expect about 1.8 s warm; a cold
cache (after a restart) reads those heap pages from disk and was **not measured** — measure on
the production server. If it gets close to 5 s: lower `lookup.max_session_seconds` (verify the
FortiGate session TTLs; 4 h cuts the read by 6×), or add a partial index on `fg_traffic (time)
WHERE duration >= 300` and split the scan into "logged within 5 minutes of the window" plus
"long sessions logged later" (schema change, later item), or materialise the NAT index of spec
9.3.

## 7. Limits

- **IPv6 is not supported.** ARCILOGGER collects no evidence that ties an IPv6 address to a
  device (no NDP neighbour tables, no DHCPv6 logs; DHCP and `ARP-LOG` are IPv4 only). A lookup
  whose public or internal address is IPv6 — also an IPv4 address written as IPv6,
  `::ffff:a.b.c.d` — is refused before it runs (API 422 `ipv6_not_supported`, audited as
  `lookup.run_failed`; the web form says so before sending): this version cannot answer such a
  request, it must be answered by other means. A destination may be IPv6 (it only filters).
  Internet exits take IPv4 addresses only.
- **Double NAT.** Several internet lines are supported (section 3), but the public port of a
  request for an ISP NAT line cannot be mapped (the ISP router's NAT table is not logged):
  candidates only, ranked; port preservation (`preserved`, measured on site) only reorders them.
  When the public IP is shared with other customers (CGNAT) the list may not hold the right
  session; an exact answer needs the ISP's NAT record. The fix is the ISP router in bridge mode,
  the line then a `direct` exit (network admin). That a changing public address
  (`public_dynamic`) belonged to the line at T only the ISP can confirm.
- **Exits are not valid from or to a date.** A lookup uses today's exit definitions; an exit
  whose addresses changed after the event is named (`egress_changed_after_event`), and the
  earlier definitions are in the audit log. A line that does not pass the FortiGate
  (`not_logged`) has no records: a lookup of its address lists nobody (`egress_not_logged`).
- The FortiGate's own outbound sessions (`subtype="local"`, `trandisp="noop"`) are not candidates.
- A guest network that does not leave through the FortiGate (a separate line or a VPN tunnel; a
  configuration option, DEVICE-SETUP §5) has no FortiGate records: when its VLAN is marked
  `guest` (`vlans.guest`), guests never appear as candidates; their DHCP/ARP leases are still
  attributed (`guest_vlan`). Its line is entered as a `not_logged` exit.
- **Static devices on FortiGate-only sites** have no lease evidence (no `ARP-LOG`): their chain
  ends at the internal IP (`no_lease`, `chain_missing`), unless srcmac gives the MAC as partial
  evidence on a listed interface. The same holds for clients of another DHCP server (e.g.
  Windows), whose records this version does not read.
- **Two DHCP authorities on one network** (MikroTik and FortiGate, or two VDOMs) are detected,
  not supported: `R20-lease:dhcp-sources-overlap` and, for chains of that network around those
  moments, `low` with `dhcp_sources_overlap`. A FortiGate that only relays DHCP gives no
  FortiGate lease (lease evidence must come from the actual DHCP server); DHCP over IPsec is
  excluded by interface (`dhcp.fortigate_interfaces_excluded`).
- **Declined addresses (D33)** are detected for FortiGate DHCP only, keyed on the configured
  network: the most specific `vlans` row that holds the address, so each DHCP scope must be
  entered as exactly one network (a narrower network inside a scope counts as another network).
  A MikroTik client that declines an address keeps its interval until a RouterOS record or the
  timeout closes it (traffic extends MikroTik intervals; whether RouterOS logs a DECLINE is not
  known). A FortiGate client that declines and gets no new Ack keeps the declined address at
  `high` until half its lease, then `low`. FortiGate chains of addresses outside every configured
  network are `low` (`fortigate_dhcp_no_network`).
- **A network added or edited later** applies to past leases only after a rebuild (section 1,
  "Manual repair"); until then the lookup lowers the chains a missed D33 close leaves open to
  `low` with `fortigate_dhcp_not_converted_since_lease`. **After a change of a `dhcp.fortigate_*`
  setting (other than `dhcp.fortigate_format_verified`) or a FortiGate source** the leases must be
  rebuilt from the first affected day as well; moments before the rebuild answer without a chain
  until then (the safe side: never a wrong person).
- **Long FortiGate leases.** Without a logged Release a FortiGate lease stays open until its last
  DHCP evidence + the lease time (7 days with the FortiOS default); traffic does not extend it by
  default. Past half the lease without a renewal its chain is `low` (`lease_renewal_not_seen`).
  If renewals are not logged at all (DEVICE-SETUP.md section 2.4 step 5), every always-on
  client's chain is `low` from half its lease and ends at the lease end (`no_lease`), unless the
  organisation switches on `dhcp.fortigate_traffic_extends` and accepts its wrong-person risk.
- **The FortiGate DHCP record format** comes from the FortiOS documentation and one community
  sample; until the site check of DEVICE-SETUP.md section 2.4 is done
  (`dhcp.fortigate_format_verified`), every chain on a FortiGate lease, the "Kira kanıtı" card and
  the PDF say so (`fortigate_format_unverified`).
- **Windows of the FortiGate DHCP checks** (ADR-0016 D8; the 7-day fallback of the Wave 3
  contract's 3.8 guard, because `fg_event` has no index on a line's address):
  - the D8 probe (section 3) reads the newest DHCP-shaped row up to max(the timeline's longest
    window, 604,800 s) before the moment; a pre-upgrade Ack older than that is not named
    (`fortigate_dhcp_not_converted`);
  - the `fg_event` check after a lease (`fortigate_dhcp_not_converted_since_lease`, section 2)
    reads from the lease's last DHCP record: at most half a lease for a FortiGate lease (no cap;
    past half a lease the chain is `low` already), at most 7 days (604,800 s) for a MikroTik
    lease;
  - the converted-row check (a MAC move, or the address given to another client) reads from the
    lease's last DHCP record as well, at most 100 days (8,640,000 s) back for a MikroTik lease;
  - the residual of the 7-day fallback: an old MikroTik lease kept open by another device's
    traffic can be named, at its own confidence and without a warning, more than 7 days after an
    unconverted line that moved its client.
- **Rows loaded before the upgrade.** FortiGate DHCP lines loaded before the upgrade are not
  converted (D8; the lookup names them, sections 2 and 3), and for each client no chain is built
  until its first Ack under the new version, up to half its lease (3.5 days with the FortiOS
  default). The srcmac of FortiGate session records loaded before the upgrade (or by the
  previous version during a rollback) is not used by the 5651 lookup; no partial srcmac chain is
  built for those moments.
- **Rollback and re-upgrade** ([INSTALL.md](INSTALL.md) section 13). `-Rollback` runs no schema
  downgrade, and the previous version cannot read FortiGate DHCP lines (it files them as
  `fg_event` rows again): left as they are, it would keep a FortiGate lease open past its end and
  name the former holder at `high`. So `-Rollback`, before the program is swapped, runs
  `arcilogger-db leases prepare-rollback`, one transaction on derived rows only: every `fg_dhcp`
  lease, ended ones too, gets confidence `low`, the status row `leases.rollback_lowered` records
  the step's time and count, and the count is printed into the installation log (raw and sealed
  data are not touched; with `-RestoreDatabase` the step is skipped; a failure of the step does
  not stop the rollback, and the installer then warns). Every FortiGate-DHCP chain of the
  previous version is therefore `low`. The step is unconditional: after a rollback between two
  versions that both read FortiGate DHCP, FortiGate-DHCP chains are `low` as well, until the next
  `arcilogger-db upgrade` (false lows only, never a wrong person). **The restore:** the seed of
  `arcilogger-db upgrade` sets the `fg_dhcp` leases that still carry the step's time back to
  `high`, deletes the status row and prints how many were restored, how many stay `low` and the
  days to rebuild: from the rollback day minus the longest FortiGate lease (604,800 s when none
  is logged) to the re-upgrade day. The leases the previous version closed early, deleted or
  re-derived stay so until that rebuild (a rebuilt `fg_dhcp` lease is `high` again, section 1);
  without it a moment between such an early end and the FortiGate's own end may answer a plain
  `no_lease`, or a chain `low`. The lines the previous version loaded stay `fg_event` rows and
  are named as above.
- **Source clocks.** A device clock that is off by less than the limit (120 s; max(120 s, the
  lookup tolerance) for a source measured only for the session search) is neither alerted
  (`R20-lease:source-clock`) nor lowers a chain, yet with the default ±60 s tolerance a drift of
  one to two minutes can still move a session out of the window. A source with fewer than 10 rows
  around T is not measured. An empty result states the searched sources' offset
  (`source_clock_offset`) but is not corrected: run the lookup again as its text says.
- **srcmac is only as good as the interface setting.** The shared-MAC guard, the other-lease
  refusal and `R20-lease:srcmac-shared` catch a routed segment or a switch listed as directly
  connected; a layer-2 segment with a single client behind a NAT device is not caught (not
  supported; DEVICE-SETUP.md section 2.5 asks for a two-client check). An internal lookup of one
  address does not see a router MAC's long session of another address logged after the guard's
  hour, so its srcmac chain stays `low` with `srcmac_only`.
- **Unknown senders.** Their FortiGate DHCP rows are never lease evidence and their traffic never
  keeps a lease alive or feeds srcmac or the log check, but the session search reads every
  source: such a match is marked on its own row and the outcome carries
  `session_from_unknown_source`.
- A domain is matched only through FortiGate web filter / application control / DNS logs; a
  session whose DNS query is older than `lookup.dns_lookback_seconds` is matched by IP only.
  DNS logs are searched for the exact name and `www.` + name (index `(qname, time)`); other
  sub-domains (`cdn.example.com`) are found through web filter / application control logs.
- Lookups read the database only; a day that is only in the sealed vault must be re-ingested
  first (later feature).
- A requested destination that no session reached gives `none`, never the whole window, in
  both NAT modes; the whole window (or every session on an exact NAT port) is listed only for a
  request without destination. A domain is matched only through FortiGate DNS, web filter and
  application control logs.
- The person of a chain is the assignment valid at T; its department is the person's current
  one (no department history, section 2). Assignments can only be appended to the history,
  not corrected afterwards (ADR-0008).
- Late evidence older than `db.retention_days`, or loaded without advancing `ingest_offsets`,
  is not detected (manual `rebuild`).

## 8. Verify on site

The operator's checklist with the recorded outcomes is [KURULUM.md](KURULUM.md) bölüm 16 /
[INSTALL.md](INSTALL.md) section 16; the FortiGate steps are DEVICE-SETUP.md sections 2.1 and
2.4–2.6. Repeat the FortiGate DHCP checks after every FortiOS upgrade.

- **FortiGate interim logs** carry the cumulative `duration` (session start = `time − duration`)
  and how often they come; long sessions without interim logs are only found up to
  `lookup.max_session_seconds` after their start.
- **MikroTik DHCP wire format** and that `assigned` is logged on every (re)binding, `deassigned`
  on expiry/release; RouterOS logs no renewals (the timeline relies on it).
- **FortiGate DHCP record on the syslog wire** (the Ack, logid `0100026001`: field names `ip`,
  `mac`, `dhcp_msg`, `lease`, `interface`, `hostname`, quoting, MAC case; the `dhcp_msg` values
  under each logid, `Ack` seen once): the check of DEVICE-SETUP.md section 2.4 step 5, then
  `dhcp.fortigate_format_verified`.
- **The Release logid** `0100026002` (derived) and whether clients' releases are logged at all
  (`dhcp.fortigate_logids`).
- **Renewals produce an Ack** (FortiOS likely logs every DHCP Ack): this decides whether
  `dhcp.fortigate_traffic_extends` may stay off (DEVICE-SETUP.md section 2.4 step 5).
- **The lease unit** (seconds) and how an unlimited lease (`0`) is written
  (`dhcp.fortigate_use_logged_lease`).
- **DHCP event lines reach syslog** with the shipped settings (severity `information`, no
  exclude filter); the "Kira kanıtı" card shows `ok`.
- **Relay, DHCP over IPsec and relayed networks:** relay mode gives no FortiGate lines; the
  `interface` of a relayed client's Ack when one interface serves several networks; whether a
  reply to DHCPINFORM is logged as an Ack.
- **HA pairs and VDOMs:** `vd` and `devid` on HA pairs and multi-VDOM units (two `vd` values on
  one network raise `R20-lease:dhcp-sources-overlap`).
- **srcmac:** present only with device identification; the client's own MAC on a listed
  interface (two-client check of DEVICE-SETUP.md section 2.5); which device-identification
  fields the unit fills.
- **Each line's `transip` and `dstintf`** (SD-WAN member names) and whether a session changes
  exit between its interim and final logs: the observed table "Kayıtlarda görülen NAT adresleri"
  under the exit editor, DEVICE-SETUP.md section 2.6.
- **`trandisp` values** of outbound sessions (central NAT, IP pools): `nat.snat_trandisp`.
- **ISP router port preservation** per line before marking an exit `preserved`: compare the
  source port a server the organisation controls sees with the FortiGate `transport` of the same
  connection (DEVICE-SETUP.md section 2.6).
- **Carrier-grade NAT** at the ISP (a public IP shared with other customers) and **whether each
  line's public IP is static** (`public_dynamic`): ask the ISP.
- **The FortiGate's clock** syncs with NTP (DEVICE-SETUP.md section 2.1) and its DHCP lines carry
  a usable device time (`R20-lease:source-clock`).
- **DECLINE:** whether FortiOS logs a DHCPDECLINE or checks an address before it offers it, and
  that a client holds one address per server interface and network.
- **ARP-LOG script interval** (`arp.interval_seconds`, 1 h) and its jitter (merge grace 66 min).
- **Lease times per DHCP server** (`lease.server_seconds`, empty by default: every server
  `lease.default_seconds`, 1 day; enter servers with other lease times, e.g. a guest network's).
- **Infrastructure MACs** (core router, FortiGate, switches): enter them in
  `devices.infrastructure_macs` (or flag the devices) so they never raise IP conflicts or serve
  as srcmac.
- **OUI file**: download location and format of the IEEE public listing.
- **Stale ARP entries after a release**: how long RouterOS keeps a released DHCP client in the
  ARP table that `ARP-LOG` prints (`arp.ignore_after_release_seconds`, 300 s; the DHCP server's
  `add-arp` option changes this).
- **Cold-cache lookup time** at the real daily volume on the production disk (section 6).
- **Owner decisions**: ARP intervals also for DHCP IPs without an open lease, with its side
  effects (section 1); `none` instead of the whole window when the requested destination has no
  session (section 3); the defaults of ADR-0016 ("Open points (owner decision)").
