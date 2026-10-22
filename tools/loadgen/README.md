# Yük üreteci / Load generator (`tools/loadgen`)

> **Sentetik — sahada doğrulanmalı / synthetic, verify on site.** Üretilen satırlar gerçek bir
> ağdan gelmez ve hiçbir şeyin kanıtı değildir; testler ve örnekler kurgusal bir kuruluş ve ağ
> kullanır. Biçimler, FortiOS 7.6 / RouterOS 7 çıktısı örnek alınarak hazırlanmış biçim
> örneklerine (`tests/fixtures/format/`) ve henüz biçim örneği olmayan türler için sentetik
> örneklere (`tests/fixtures/synthetic/`) dayanır: MikroTik kablo biçimi, FortiGate web filtresi /
> DNS `logid` değerleri (`99000000xx` yer tutucuları), FortiGuard kategorileri (`9xx` "Sentetik:
> …"), ara kayıt (interim) aralığı ve alan adları (`*.example`, RFC 2606) gerçek değer değildir.

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
```

- **Üretim sunucusunda kullanmayın.** `spool`, kullanımda olan bir veri klasörünü (mühürlü
  günler, `state` klasörü, kendisinin yazmadığı spool dosyaları) reddeder; yalnızca bir test
  kurulumunda `--i-know-this-is-not-production` ile yazar. Yazdığı her veri klasörüne
  `LOADGEN-DEMO.txt` işaret dosyasını bırakır (bu klasördeki satırlar sentetiktir, delil
  değildir); işaretli klasöre sonraki günler bayraksız yazılabilir. Mevcut bir dosyaya
  `--append` olmadan yazmaz.
- Simülasyondaki her DHCP havuzunda 160 adres var (`.64`–`.223`): bütün telefonlar personel
  Wi-Fi'ında (60) olduğundan o yaklaşık 160 kullanıcıyla dolar; daha fazlası açık bir hata ile
  durur (çıkış kodu 2).
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
  veri klasörü reddi; cp1252 konsolda UTF-8; havuz sınırı),
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

**Never on the production server.** `spool` refuses a data folder in use (sealed days, a `state`
folder, spool files it did not write) unless `--i-know-this-is-not-production` is given on a test
installation, and leaves the marker `LOADGEN-DEMO.txt` (synthetic lines, not evidence) in every
data folder it writes to. Each simulated DHCP pool has 160 addresses (`.64`–`.223`): every phone
is on the staff Wi-Fi, which fills up at about 160 users; more stop with a clear error (exit code
2). Output is always
UTF-8.

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
`write_spool`, `spool_lines`, `Sender`, `send_messages`.

Not covered yet (later milestones): access points as log sources (`ap-<ip>` sources, Wi-Fi
connect events), IPS/virus/VPN/admin-login events, usage-report golden datasets. Volume with the
defaults: about 210,000 lines per workday for 120 people (measured on the development machine:
5 s to generate).
