# Yük üreteci / Load generator (`tools/loadgen`)

> **Sentetik — sahada doğrulanmalı / synthetic, verify on site.** Üretilen satırlar gerçek bir
> ağdan gelmez ve hiçbir şeyin kanıtı değildir; testler ve örnekler kurgusal bir kuruluş ve ağ
> kullanır. Biçimler, FortiOS 7.6 / RouterOS 7 çıktısı örnek alınarak hazırlanmış biçim
> örneklerine (`tests/fixtures/format/`) ve henüz biçim örneği olmayan türler için sentetik
> örneklere (`tests/fixtures/synthetic/`) dayanır: MikroTik kablo biçimi, FortiGate web filtresi /
> DNS `logid` değerleri (`99000000xx` yer tutucuları), FortiGuard kategorileri (`9xx` "Sentetik:
> …"), ara kayıt (interim) aralığı ve alan adları (`*.example`, RFC 2606) gerçek değer değildir.
> FortiGate DHCP satırları sentetik örnek satırların biçimindedir (FortiOS 7.6 alan listesi ve bir
> topluluk örneği): RELEASE `logid` değeri türetilmiştir, `dhcp_msg`/`msg` değerleri yer
> tutucudur; yenileme ve kira bitişi davranışı simülasyonun varsayımıdır (sahada doğrula).

## Türkçe

Yük testleri, demo verisi ve testler için kurgusal örnek ağı (bir güvenlik duvarı, DHCP sunucusu
olan ana yönlendirici ve bir anahtar) taklit eden syslog üretir: üç departman VLAN'ında (41
Finans, 42 Planlama, 43 İdari İşler) `--users` kişi (varsayılan 120), her birinin departman
VLAN'ında kablolu bir bilgisayarı ve personel Wi-Fi'sinde (VLAN 60; herkesin telefonu orada)
**rastgele MAC** kullanan bir telefonu; misafirler (dhcp61, 1 saatlik kira; güvenlik duvarından
herkes gibi geçen, kişisi olmayan cihazlar); VLAN 10'da statik ağ cihazları (erişim noktaları,
yazıcılar, anahtar, yönetici bilgisayarı 172.20.10.37), VLAN 20'de sunucular (kullanıcıların DNS
sunucusu 172.20.20.53). Mesai düzeni (geliş, öğle arası, çıkış, hafta sonu az kişi), DHCP
`assigned`/`deassigned` döngüsü, saatlik `ARP-LOG`, her MikroTik'ten 5 dakikada bir
`ARCI-HEARTBEAT`, anahtarın 48 portundaki bilgisayarlar için bağlantı açılıp kapanması (diğer
bilgisayarlar syslog göndermeyen anahtarlardadır); FortiGate'te DNS, web filtresi (izin ve
**engellenen denemeler**), uygulama kontrolü ve `sessionid` ile birleşen trafik kayıtları (uzun
oturumlarda `0000000020` ara kayıtları ve `…delta` alanları), `transip` (varsayılan 100.64.12.9)
üzerinden kaynak NAT (`transport` kaynak portla aynıdır; yalnızca o port başka bir oturumda
kullanılıyorsa bir sonraki boş porta kayar; simülasyonun varsayımıdır, sahada doğrulanmalı).
Aynı `--seed` ve seçenekler her zaman bayt bayt aynı satırları verir.

Wave 3 site planları (her seçeneğin varsayılanı yukarıdaki davranıştır; varsayılan çıktı bayt
bayt aynı kalır, aşağıdaki 2026-11-02 değişikliği dışında):

- **`--dhcp-server mikrotik|fortigate|both`** (varsayılan `mikrotik`). `fortigate`: yalnızca
  FortiGate olan bir site. FortiGate her VLAN'ın ağ geçidi ve DHCP sunucusudur: DHCP döngüsü
  FortiGate DHCP Ack (26001) satırlarıyla yazılır (`logid="0100026001"`,
  `logdesc="DHCP Ack log"`, `interface="vlan<VLAN>"`, `dhcp_msg="Ack"`, `lease=<saniye>`,
  cihazın adı varsa `hostname`, `msg="DHCP server sends a DHCPACK"`); kira verilirken ve kiranın yarısı dolunca
  yenilemede (cihaz bağlıysa o anda, değilse geri döndüğünde; simülasyon varsayımı, sahada
  doğrula). 26002 (RELEASE) yalnızca bir misafir ayrılırken yazılır; diğer kiralar kayıt
  bırakmadan biter (simülasyon tercihi, ölçülmüş bir davranış değildir). `core-r1`/`switch-1`
  satırı ve ARP-LOG yoktur; istemci trafiğinde `srcintf="vlan<VLAN>"`. `both`: VLAN 41–43
  MikroTik'te kalır, Wi-Fi VLAN'ları 60/61 FortiGate'tedir (ARP-LOG yalnızca MikroTik
  VLAN'larında).
- **`--srcmac`:** trafik satırları `srcmac` taşır (küçük harf): FortiGate'in ağ geçidi olduğu
  VLAN'da istemcinin MAC'i, ana yönlendiricinin arkasında her istemci için yönlendiricinin MAC'i
  (`00:00:5e:00:53:02`; birçok IP'de tek MAC, paylaşılan MAC denetimini sınar).
- **`--egress NAME:TRANSIP:DSTINTF:SHARE`** (çıkış adı, NAT adresi, arayüz, yüzde pay;
  tekrarlanabilir, paylar toplamı 100): internet çıkışları (çift WAN). Her oturum `rng(seed, "egress", oturum)` ile bir çıkışa düşer; satırları o çıkışın
  `dstintf` değeriyle ve `transip` adresine kaynak NAT ile yazılır; performans istatistiklerinin
  `waninfo` alanı arayüzleri listeler. Seçenek verilmezse tek `--transip` ve `dstintf="wan1"`
  (bugünkü gibi); `--transip` ile birlikte kullanılamaz.
- **Doğruluk verisi:** kiralar `"evidence": "mt_dhcp" | "fg_dhcp" | "none"` alır (FortiGate'in
  arkasındaki statik cihazlar, ilk günün kayıtlarında bir kez, `none` ile listelenir: onları
  adreslerine bağlayan DHCP ya da ARP-LOG satırı yoktur; `--srcmac` ile trafik satırları MAC
  adreslerini taşır, bu yalnızca kısmi kanıttır); oturumlar `"egress"` alır (çıkış yoksa
  `null`).
- Wave 3'ten beri kapsananlar: FortiGate DHCP (`--dhcp-server`), srcmac ve birden çok internet
  çıkışı.

**Saldırı senaryoları (Wave 6, `--scenario AD[,AD…]|all`).** Güvenlik kurallarının (R01–R19)
yakaladığı olayları normal sitenin üzerine ekler; demo ve kural motorunun performans testi `all`
kullanır. Her senaryo, üretilen her günün aynı site saatinde, örnek ağın hiçbir DHCP havuzunda
olmayan adreslerden satır yazar; `stats` bu satırları `scenario.<ad>` türü olarak sayar. Seçenek
verilmezse çıktı bayt bayt aynıdır.

| Senaryo | Kural | Ne ekler | Saat |
|---|---|---|---|
| `port-scan` | R01 | 172.20.41.196'dan 172.20.20.10'un 40 portuna 40 sn içinde reddedilen 40 bağlantı | 10:12 |
| `external-scan` | R02 | 203.0.113.77'den WAN adresinin 120 portuna 4 dk içinde reddedilen 120 bağlantı | 10:21 |
| `brute-force` | R03 | 172.20.41.196'dan 6 başarısız FortiGate yönetici girişi (`0100032002`) ve 3 başarısız yönlendirici girişi, 90 sn içinde | 10:30 |
| `off-hours-admin` | R04 | 172.20.10.37'den yönlendiriciye ve FortiGate'e (`0100032001`) yönetici girişi | 22:40 |
| `ips` | R05 | WAN adresine kritik bir IPS imzası | 11:00 |
| `botnet` | R06 | 172.20.41.231'den botnet sunucusuna bağlantı (`eventtype` `botnet`) | 11:10 |
| `malware` | R07 | 172.20.43.232'nin indirdiği test dosyasını antivirüs engeller | 11:20 |
| `dns-bypass` | R08 | 172.20.42.230'dan dış DNS sunucusu 198.51.100.53'e 40 dk içinde 25 sorgu | 11:30 |
| `blocked-category` | R09 | 172.20.42.230'dan 25 dk içinde engellenen kategoride 25 sayfa | 13:00 |
| `tor` | R10 | Uygulama kontrolü 172.20.42.230'da Tor görür | 13:40 |
| `new-device` | R11 | Hiç görülmemiş bir MAC (`00:00:5E:00:53:F0`–`FF`, her gün başka) 172.20.43.240'ı alır | 14:00 |
| `mac-flap` | R12 | 172.20.43.250 iki MAC ile dönüşümlü görülür, 30 dk içinde dört kez (ARP-LOG; FortiGate sitesinde DHCP Ack) | 14:10–14:40 |
| `big-upload` | R13 | 172.20.42.230 20 dk içinde 1,5 GiB gönderir | 15:20 |
| `night` | R14 | 172.20.42.230 gece 12 MiB gönderir, 50 MiB alır | 02:30 |
| `config-change` | R16 | FortiGate'te politika değişikliği (`0100044546`) ve yönlendiricide filtre kuralı değişikliği (admin) | 16:00 |
| `fw-health` | R17 | Performans istatistiklerinde CPU %96, bellek %91 (16:30–16:55); 16:45'te koruma (conserve) modu satırı | 16:30 |
| `pool-empty` | R18 | Misafir Wi-Fi havuzu (dhcp61) boş; FortiGate sitesinde FortiGate satırı | 17:00 |
| `dga` | R19 | 172.20.42.230 25 dk içinde 250 NXDOMAIN yanıtı alır (rastgele görünen adlar) | 18:30 |

**Bu satırların hepsi sentetiktir, sahada doğrulanmalıdır.** Reddedilen trafik satırlarının, IPS
botnet (`9900000041`), koruma modu (`9900000042`) ve FortiGate havuz doldu (`9900000043`)
satırlarının biçim örneği yoktur (`logid` değerleri yer tutucudur); yönetici girişi ve
yapılandırma değişikliği `logid` değerleri, `logdesc` metinleri, Tor'un `appid` değeri ve DNS
yanıt kodu alanı (`rcode`) kuralların varsayılan parametreleridir, yakalanmış değerler değildir
(Wave 6 sözleşmesi §0.4). Eşikler başlangıç değeridir: senaryolar varsayılan eşikleri aşacak
kadar satır yazar. Testler: `tests/test_loadgen_scenarios.py` (her senaryonun satırları gerçek
ayrıştırıcılardan geçer ve kuralının eşleşmesini sağlar; MikroTik ve yalnızca FortiGate olan
sitede).

```text
uv run python tools/loadgen/arcilogger_loadgen.py --help
# Bir günün spool dosyaları (toplayıcının yazdığı biçimde), demo için:
uv run python tools/loadgen/arcilogger_loadgen.py --day 2026-10-12 spool --data-dir D:\demo\data
# Çalışan bir toplayıcıya gönderim (FortiGate TCP, MikroTik UDP; kaynaklar IP ile ayrılır):
uv run python tools/loadgen/arcilogger_loadgen.py --users 120 send --udp-port 514 --tcp-port 514 \
    --protocol split --bind fortigate=127.0.0.1 --bind core-r1=127.0.0.2 --rate 2000 --live
# Doğruluk verisi (oturumlar ve kiralar, JSON satırları) ve sayımlar:
uv run python tools/loadgen/arcilogger_loadgen.py --users 10 truth > truth.jsonl
uv run python tools/loadgen/arcilogger_loadgen.py stats
# Yalnızca FortiGate olan bir site (srcmac ile) ve çift WAN'lı bir site:
uv run python tools/loadgen/arcilogger_loadgen.py --dhcp-server fortigate --srcmac --users 20 truth
uv run python tools/loadgen/arcilogger_loadgen.py --egress hat-1:100.64.12.9:wan1:60 \
    --egress hat-2:100.64.13.9:wan2:40 stats
# Saldırı senaryoları (hepsi ya da seçilenler):
uv run python tools/loadgen/arcilogger_loadgen.py --scenario all --day 2026-10-12 spool --data-dir D:\demo\data
uv run python tools/loadgen/arcilogger_loadgen.py --scenario brute-force,mac-flap stats
```

- **Üretim sunucusunda kullanmayın.** `spool`, kullanımda olan bir veri klasörünü (mühürlü
  günler, `state` klasörü, kendisinin yazmadığı spool dosyaları) reddeder; yalnızca bir test
  kurulumunda `--i-know-this-is-not-production` ile yazar. Yazdığı her veri klasörüne
  `LOADGEN-DEMO.txt` işaret dosyasını bırakır (bu klasördeki satırlar sentetiktir, delil
  değildir); işaretli klasöre sonraki günler bayraksız yazılabilir. Mevcut bir dosyaya
  `--append` olmadan yazmaz.
- Simülasyondaki her DHCP havuzunda 160 adres var (`.64`–`.223`) ve bir adres kirası bitene
  kadar tutulur: bütün telefonlar personel Wi-Fi'ında (60) olduğundan o yaklaşık 125 kullanıcıyla
  dolar; DHCP'yi FortiGate verdiğinde (`--dhcp-server fortigate` ya da `both`) yaklaşık 140
  kullanıcıyla. Daha fazlası açık bir hata ile durur (çıkış kodu 2).
- **Değişiklik (2026-11-02, #89):** varsayılan planda (MikroTik DHCP) bir adres, önceki sahibinin
  kirası bitene kadar başka bir cihaza verilmez; önceden kira cihazın etkinliğinden uzun sürünce
  doğruluk verisinde aynı adreste aynı anda iki kira görünebiliyordu. Bunun olduğu
  çalıştırmaların baytları değişir (satır sayısı aynı kalır), örneğin varsayılan ayarlarla iki
  günlük bir çalıştırma; denenen tek günler (2026-09-23, 2026-10-27) ve testteki sabit özet
  değişmedi. Personel Wi-Fi'ı artık
  yaklaşık 160 değil 125 kullanıcıyla dolar. Eski çıktılarla karşılaştırırken bunu hesaba katın.
- Çıktı her zaman UTF-8'dir (İngilizce yerel ayarlı bir Windows konsolunda da).
- Toplayıcı kaynakları gönderen IP'sine göre tanır: her kaynağa `--bind` ile kendi yerel
  adresini verin (127.0.0.0/8 Linux ve Windows'ta loopback'tir) ve aynı adresleri
  `arcilogger.toml` içindeki `[[sources]]` bölümlerine yazın. Üretecin kaynakları
  (`DEFAULT_SOURCES`), örnek yapılandırmanın (`deploy/windows/arcilogger.example.toml`) yorum
  satırı olarak gösterdiği kurgusal örnek ağın `[[sources]]` kayıtlarıdır; yeni bir kurulumda
  hiçbir kaynak tanımlı değildir.
- `--live` her mesajı gönderildiği andaki zamanla damgalar (bugünün yük testi); ayrıştırıcılar
  alım zamanından bir günden fazla sapan cihaz zamanlarını kullanmaz.
- Testler: `tests/test_loadgen.py` (aynı tohum aynı baytlar; her satır gerçek ayrıştırıcılardan
  hatasız geçer; oturumlar, NAT portları ve kiralar tutarlı; UDP/TCP gönderimi; kullanımdaki
  veri klasörü reddi; cp1252 konsolda UTF-8; havuz sınırı; varsayılan çıktının özeti sabit;
  site planlarında her DHCP satırı gerçek ayrıştırıcıda `fg_dhcp` satırı olur ve doğruluk
  verisiyle karşılaştırılır, yenilemeler kiranın yarısında, çıkış payları ±%2 içinde),
  `tests/test_mvp_end_to_end.py` (uçtan uca MVP testi bu üreteci kullanır).

## English

Generates syslog of the fictional example network (spec 17; a firewall, the core router that
serves DHCP and a switch; tests and examples use a fictional organisation and network) for load
tests, demo data and tests. The formats follow the format samples (`tests/fixtures/format/`,
modelled on FortiOS 7.6 / RouterOS 7 output) and the synthetic fixtures where there is no sample
yet. People in three department VLANs (41 Finans, 42 Planlama, 43 İdari İşler), each with a
wired PC and a phone with a randomised MAC on the staff Wi-Fi (VLAN 60, everyone's), guests (1 h
leases; anonymous devices that browse through the firewall like everyone else), static network
devices in VLAN 10 (access points, printers, a switch, the admin PC), servers in VLAN 20 (the
users' DNS server 172.20.20.53);
work-hour patterns, DHCP churn, hourly ARP-LOG, heartbeats, link events of the PCs on the
switch's 48 ports; FortiGate DNS, web filter (allowed and **blocked**), application control and
traffic logs joined by `sessionid` (interim logs with deltas for long sessions), source NAT to
`--transip` (`transport` = the source port unless another session still holds it, an assumption
of the simulation to verify on site; ports are given out in time order over all devices).
Deterministic per `--seed`.

Wave 3 site plans (each option defaults to the behaviour above; the default output stays the
same, byte for byte, apart from the 2026-11-02 change below):

- **`--dhcp-server mikrotik|fortigate|both`** (default `mikrotik`). `fortigate`: a
  FortiGate-only site, where the FortiGate is every VLAN's gateway and DHCP server. DHCP churn
  is written as FortiGate DHCP Ack (26001) lines (`logid="0100026001"`, `logdesc="DHCP Ack
  log"`, `interface="vlan<VLAN>"`, `dhcp_msg="Ack"`, `lease=<seconds>`, `hostname` when the
  device has one, `msg="DHCP server sends a DHCPACK"`) on assignment and on renewal at half the lease (at that
  moment while the device is connected, else when it is back; a simulation assumption, verify
  on site). 26002 (RELEASE) is written only when a guest leaves; every other lease expires
  unlogged (a simulation choice, not a measured behaviour). No `core-r1`/`switch-1` line and no
  ARP-LOG; client traffic has `srcintf="vlan<VLAN>"`. `both`: MikroTik keeps VLAN 41–43, the
  FortiGate serves the Wi-Fi VLANs 60/61 (ARP-LOG only in the MikroTik VLANs).
- **`--srcmac`:** traffic lines carry `srcmac` (lower case): the client's MAC in a VLAN whose
  gateway is the FortiGate, the core router's MAC (`00:00:5e:00:53:02`) for every client behind
  the router (one MAC on many IPs, which exercises the shared-MAC guard).
- **`--egress NAME:TRANSIP:DSTINTF:SHARE`** (repeatable; shares sum to 100): internet exits (dual
  WAN). Each session is assigned to an exit by `rng(seed, "egress", session)`; its lines carry
  the exit's `dstintf` and source NAT to its `transip`; `waninfo` of the performance statistics
  lists the interfaces. Without the option there is one `--transip` and `dstintf="wan1"` as
  before; it excludes `--transip`.
- **Truth:** leases gain `"evidence": "mt_dhcp" | "fg_dhcp" | "none"` (static devices behind the
  FortiGate are listed once, in the first day's records, with `none`: no DHCP or ARP-LOG line ties
  them to their address; with `--srcmac` their traffic lines carry their MAC, partial evidence
  only); sessions gain `"egress"` (`null` without exits).

The FortiGate DHCP lines follow the shape of the synthetic fixture lines (the FortiOS 7.6 field
list and one community sample): the RELEASE logid is derived and its `dhcp_msg`/`msg` are
placeholders (verify on site).

**Never on the production server.** `spool` refuses a data folder in use (sealed days, a `state`
folder, spool files it did not write) unless `--i-know-this-is-not-production` is given on a test
installation, and leaves the marker `LOADGEN-DEMO.txt` (synthetic lines, not evidence) in every
data folder it writes to. Each simulated DHCP pool has 160 addresses (`.64`–`.223`), and an
address stays held until its lease ends: every phone is on the staff Wi-Fi, which fills up at
about 125 users, or at about 140 when the FortiGate serves DHCP (`--dhcp-server fortigate` or
`both`); more stop with a clear error (exit code 2). Output is always UTF-8.

**Change (2026-11-02, #89):** in the default plan (MikroTik DHCP) an address is no longer handed
to another device while the earlier holder's lease runs; before, a lease that outlived its
device's activity could leave two leases on one address at one moment in the truth. Runs where
this happened change their bytes (the line count stays the same), for example two days with the
defaults; the single days checked (2026-09-23, 2026-10-27) and the pinned test digest did not
change. The staff Wi-Fi now fills up at
about 125 users instead of 160. Keep this in mind when you compare with earlier outputs.

Commands: `spool` (raw spool files exactly as the collector writes them, through
`arcilogger_core.rawlog.format_line`; refuses to append unless `--append`, and refuses a data
folder in use, see above), `send` (UDP and/or
TCP octet counting at `--rate`; `--protocol split` sends the FortiGate over TCP and the MikroTiks
over UDP; `--bind NAME=IP` gives each source its own local address, because the collector tells
sources apart by sender IP, and the same addresses go into `[[sources]]` of `arcilogger.toml`: the
generator's sources (`DEFAULT_SOURCES`) are the fictional example network's `[[sources]]` that
`deploy/windows/arcilogger.example.toml` shows as comments, a new installation has none; `--live`
stamps messages with the current time), `truth` (sessions
with device, MAC and person; DHCP leases — JSON lines) and `stats`. Python API: `Settings`,
`Simulation(settings).run(first, last)` → `DayOutput(messages, sessions, leases)`,
`write_spool`, `spool_lines`, `Sender`, `send_messages`. Tests: `tests/test_loadgen.py` (also: the
default output's digest is pinned; in the site plans every DHCP line is an `fg_dhcp` row of the
real parser, checked against the truth, renewals come at half the lease, exit shares stay within
±2 %).

**Attack scenarios (Wave 6, `--scenario NAME[,NAME…]|all`).** They add the events the security
rules (R01–R19) catch to the normal site; the demo and the rule engine's performance guard use
`all`. Each scenario writes its lines at the same site time of every generated day, from
addresses in none of the example network's DHCP pools; `stats` counts them as kind
`scenario.<name>`. Without the option the output is byte for byte the same.

| Scenario | Rule | What it adds | Time |
|---|---|---|---|
| `port-scan` | R01 | Internal 172.20.41.196 tries 40 ports of 172.20.20.10 in 40 s, all denied | 10:12 |
| `external-scan` | R02 | 203.0.113.77 tries 120 ports of the WAN address in 4 min, all denied | 10:21 |
| `brute-force` | R03 | 6 failed FortiGate admin logins (`0100032002`) and 3 failed router logins from 172.20.41.196 within 90 s | 10:30 |
| `off-hours-admin` | R04 | Admin logins to the router and the FortiGate (`0100032001`) from 172.20.10.37 | 22:40 |
| `ips` | R05 | A critical IPS signature against the WAN address | 11:00 |
| `botnet` | R06 | 172.20.41.231 contacts a botnet server (`eventtype` `botnet`) | 11:10 |
| `malware` | R07 | The antivirus blocks a test file 172.20.43.232 downloads | 11:20 |
| `dns-bypass` | R08 | 172.20.42.230 asks the outside DNS server 198.51.100.53 25 times in 40 min | 11:30 |
| `blocked-category` | R09 | 172.20.42.230 opens 25 pages of a blocked category in 25 min | 13:00 |
| `tor` | R10 | Application control sees Tor on 172.20.42.230 | 13:40 |
| `new-device` | R11 | A never-seen MAC (`00:00:5E:00:53:F0`–`FF`, another one each day) gets 172.20.43.240 | 14:00 |
| `mac-flap` | R12 | 172.20.43.250 is seen with two MACs in turn, four times in 30 min (ARP-LOG; DHCP Ack on a FortiGate site) | 14:10–14:40 |
| `big-upload` | R13 | 172.20.42.230 sends 1.5 GiB in 20 min | 15:20 |
| `night` | R14 | 172.20.42.230 sends 12 MiB and receives 50 MiB at night | 02:30 |
| `config-change` | R16 | A firewall policy change on the FortiGate (`0100044546`) and a router filter-rule change (admin) | 16:00 |
| `fw-health` | R17 | CPU 96 %, memory 91 % in the performance statistics (16:30–16:55); a conserve-mode line at 16:45 | 16:30 |
| `pool-empty` | R18 | The guest Wi-Fi pool (dhcp61) is empty; a FortiGate line on a FortiGate site | 17:00 |
| `dga` | R19 | 172.20.42.230 gets 250 NXDOMAIN answers in 25 min (random-looking names) | 18:30 |

**Every one of these lines is synthetic: verify on site.** The denied traffic lines, the IPS
botnet (`9900000041`), conserve-mode (`9900000042`) and FortiGate pool-exhausted (`9900000043`)
lines have no format sample (their logids are placeholders); the admin-login and
configuration-change logids, the `logdesc` texts, Tor's `appid` and the DNS response-code field
(`rcode`) are the rules' default parameters, not captured values (Wave 6 contract §0.4). The
thresholds are starting values: the scenarios write enough lines to pass the default thresholds.
Tests: `tests/test_loadgen_scenarios.py` (every scenario's lines go through the real parsers and
match their rule, on a MikroTik and on a FortiGate-only site).

Covered since Wave 3: FortiGate DHCP (`--dhcp-server`), srcmac and several internet exits; since
Wave 6, IPS, antivirus, admin-login and configuration-change events through the scenarios. Not
covered yet (later milestones): access points as log sources (`ap-<ip>` sources, Wi-Fi
connect events), VPN events, usage-report golden datasets. Volume with the
defaults: about 210,000 lines per workday for 120 people (measured on the development machine:
5 s to generate).
