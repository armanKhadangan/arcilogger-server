# Arama — sorgu dili, hız, istatistikler, dışa aktarma

> English: [SEARCH.md](SEARCH.md). API sözleşmesi: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 17.
> Karar kaydı: [adr/0015-search-live-sources.md](adr/0015-search-live-sources.md). Yetkiler:
> [roles.md](roles.md). Güvenlik: [SECURITY.md](SECURITY.md) bölüm 15.
>
> **Lisans:** Arama ve Canlı Akış **5651 Temel**'in parçasıdır; hiçbir lisans durumu onları
> kilitlemez ([LISANS.md](LISANS.md) bölüm 2.2).

Arama sayfası veritabanındaki log kayıtlarını (`messages` ve türlü tablolar: trafik, web
filtresi, DNS, DHCP …) bir zaman aralığında arar, sonuçları sayfa sayfa gösterir, zaman
çizelgesini ve en sık değerleri sayar, kaydedilmiş aramaları tutar, sonuçları CSV ya da Excel
olarak dışa aktarır ve bir aramanın ham satırlarını Kanıt Paketi'ne aktarır. **Ham log dosyaları
hiçbir zaman değiştirilmez**; Arama yalnızca okur, her sorgu salt okunur bir veritabanı
işleminde çalışır.

Bu sayfa kişisel veri gösterir; sorgular ve açılan ham satırlar denetim kaydına yazılır. Sayfayı
yalnızca KVKK aydınlatma metnini onaylamış Yönetici ve Denetçi açabilir (bölüm 11).

## 1. Sorgu dili

Bir sorgu boşlukla ayrılmış **koşullardan** oluşur; koşulların hepsi sağlanmalıdır (VE).

| Yazım | Anlamı | Örnek |
|---|---|---|
| `alan:değer` | alan bu değere eşit | `srcip:172.20.41.105` |
| `alan:a,b,c` | alan bu değerlerden biri (aynı alan içinde VEYA) | `action:deny,blocked` |
| `alan:*` / `-alan:*` | alan dolu / boş | `user:*` |
| `-koşul` ya da `NOT koşul` | koşulun tersi | `-action:passthrough` |
| `kelime`, `"bir ifade"` | serbest metin (bölüm 4) | `"login failure"` |
| `AND` | yok sayılır (koşullar zaten birlikte geçerlidir) | `type:web AND action:blocked` |

- **VEYA ve parantez yoktur:** tek başına `OR`, `(` ve `)` reddedilir (`search_or_unsupported`);
  aynı alanın değerleri arasında virgül kullanın.
- **Alan adları** büyük/küçük harf ayırmaz, Türkçe harfler sadeleştirilir (`Alanadı:` =
  `alanadi:`) ve her alanın Türkçe takma adları vardır (bölüm 3).
- **`:` içeren bir kelime** alan olarak okunur; öyle bir alan yoksa sorgu reddedilir
  (`search_unknown_field`), kelime metin olarak aranmaz. Bir URL'yi ya da IPv6 adresini metin
  olarak aramak için tırnak içine alın. Kelimenin tamamı bir MAC adresiyse ipucu `mac:<değer>`dir.
- **Tırnak içinde** yalnızca `\"` ve `\\` kaçış olarak yazılır.
- **Sınırlar** (`search_too_complex`): en fazla 1.000 karakter, 16 koşul, koşul başına 20 değer,
  5 serbest metin terimi ve değer başına 256 karakter.
- **Zaman sorguda yazılmaz:** zaman aralığı sorgunun yanında seçilir (bölüm 5).

Sorgu yazılırken her 250 ms'de bir denetlenir (`POST /search/parse`, veritabanına dokunmaz);
hatalı kısım altı çizili gösterilir. `?` düğmesi alan listesini, örnekleri ve hangi sorguların
hızlı olduğunu açar.

Örnekler: `srcip:172.20.41.105 type:web action:blocked` · `domain:example.com -action:passthrough`
· `"login failure" source:core-r1` · `vlan:41 dstport:80,443` · `type:dhcp mac:00:00:5e:00:53:21`
· `error:fortigate.*`

## 2. Değer biçimleri

| Alan türü | Kabul edilen | Not |
|---|---|---|
| IP (`srcip`, `dstip`, `natip`, `ip`, `sender`) | `172.20.41.5`, `172.20.41.0/24`, `172.20.41.*`, `172.20.*` | IPv6 yazılabilir; IPv6 ağları desteklenen kapsamın dışındadır |
| Port ve sayı (`srcport`, `dstport`, `natport`, `port`, `sessionid`) | `443`, `1000-2000`, `>1024`, `>=1`, `<10`, `<=10`, virgüllü liste | |
| Protokol (`proto`) | `tcp`, `udp`, `icmp` ya da protokol numarası | |
| MAC (`mac`) | her ayraç ve harf büyüklüğüyle (`00-00-5E-00-53-21`, `0000.5e00.5321`); üretici öneki `00:00:5e:*` | |
| Alan adı (`domain`) | `example.com` (ad ve alt alan adları), `*.example.com` (yalnızca alt alan adları); küçük harfe çevrilir, uluslararası adlar punycode'a | |
| DNS adı (`qname`) | tam ad | |
| Sabit değer (`action`, `qtype`, `level`, `logid`) | tam değer; küçük harfe çevrilerek karşılaştırılır | |
| Metin (`app`, `category`, `service`, `user`, `host` …) | tam değer (İngilizce harflerde büyük/küçük ayrımı yok), `foo*` önek, `*foo` sonek, `*foo*` içerir | `%` ve `_` değerin içinde kendileri olarak aranır |
| Tür (`type`) | bölüm 6'daki türler, virgülle birden çok | |
| Önem (`severity`) | `emerg` … `debug` ya da 0–7, karşılaştırmayla (`<=err`) | |
| Ayrıştırma hatası (`error`) | kalıp adı, `onek*` ya da tek başına `*` | |
| VLAN (`vlan`) | VLAN numaraları (virgüllü) | istemci adresinin VLAN alt ağı (Cihazlar ve Kişiler → VLAN'lar) |
| Departman (`department`) | departman adı ya da numarası | VLAN'a tanımlı varsayılan departman; kişi ataması **değildir** (bildirim `department_by_vlan`) |

**Tersine çevirme:** `-action:accept` "eylemi accept olmayan kayıtları bulur (eylemi boş olanlar
dahil)": alanı boş olan bir kayıt da tersine çevrilmiş koşulu sağlar. Yalnızca bu alanı taşıyan
kayıt türleri aranır.

## 3. Alanlar

Liste sunucudaki alan kataloğuyla aynıdır (`GET /search/fields`; bir test bu tabloyu katalogla
karşılaştırır). "Hızlı" sütunu, alanın tek başına bir indeksle okunduğu tabloları gösterir.

| Alan (takma adlar) | Türü | Ne | Hızlı |
|---|---|---|---|
| `source` (`kaynak`) | kaynak | log kaynağının adı; sonunda `*` olabilir; ağ olarak tanımlı bir kaynak (ör. `ap`) üyelerini de bulur (`ap-<ip>`) | `messages`, MikroTik olay tabloları |
| `sender` (`gonderen`) | IP | satırı gönderen cihazın adresi (bilinmeyen göndericiler dahil) | — |
| `type` (`tur`, `tip`) | tür | kayıt türü (bölüm 6) | `messages` |
| `subtype` (`alttur`) | metin | cihazın gönderdiği alt tür | — |
| `host` (`cihaz`) | metin | syslog başlığındaki ya da FortiGate `devname` alanındaki cihaz adı | — |
| `program` (`konu`) | metin | syslog programı ya da MikroTik konuları | — |
| `severity` (`onem`) | önem | syslog önemi | — |
| `error` (`hata`) | hata | satırın ayrıştırılamayan kalıbı | — |
| `srcip` (`kaynakip`, `istemci`) | IP | istemcinin adresi ya da ağı — en hızlı alan | trafik, web, DNS, uygulama, IPS, virüs, DHCP, ARP, MikroTik güvenlik duvarı, oturum açma |
| `dstip` (`hedefip`) | IP | bağlanılan adres | trafik, MikroTik güvenlik duvarı |
| `natip` (`transip`) | IP | FortiGate'in kaynak NAT sonrası adresi | trafik |
| `ip` (`adres`) | IP | kaynak, hedef ya da NAT adreslerinden biri | trafik, MikroTik güvenlik duvarı, DHCP, ARP, oturum açma |
| `srcport` (`kaynakport`) | port | istemcinin portu | — |
| `dstport` (`hedefport`) | port | bağlanılan port | `dstip:` ile birlikte trafik |
| `natport` (`transport`) | port | kaynak NAT sonrası port | `natip:` ile birlikte trafik |
| `port` | port | kaynak ya da hedef port | — |
| `proto` (`protokol`) | protokol | tcp, udp, icmp ya da numara | — |
| `action` (`eylem`) | sabit | cihazın eylemi (deny, blocked, passthrough, assigned …) | — (istatistikte özet tablodan) |
| `domain` (`alanadi`, `site`) | alan adı | alan adı ve alt alan adları | web filtresi |
| `qname` | DNS adı | DNS'te sorulan tam ad | DNS |
| `url` | URL | URL'nin içerdiği metin | — |
| `app` (`uygulama`) | metin | FortiGate uygulama adı | — |
| `appcat` | metin | FortiGate uygulama kategorisi | — |
| `category` (`kategori`) | metin | FortiGuard kategori adı (web ve DNS filtresi) | — |
| `service` (`servis`) | metin | FortiGate servis adı | — |
| `country` (`ulke`) | metin | hedefin ülkesi | — |
| `policy` (`politika`) | politika | FortiGate politika numarası ya da adı | — |
| `sessionid` (`oturum`) | sayı | FortiGate oturum numarası | trafik, web, uygulama |
| `user` (`kullanici`) | metin | cihaza giriş yapan ya da değişiklik yapan kullanıcı | — |
| `mac` | MAC | cihazın MAC adresi ya da üretici öneki | DHCP, ARP |
| `vlan` | VLAN | istemci adresinin VLAN'ı | `srcip` gibi |
| `department` (`departman`) | departman | istemci adresinin VLAN'ına tanımlı departman | `srcip` gibi |
| `interface` (`arayuz`) | metin | gelen ya da giden arayüz | — |
| `qtype` | sabit | DNS sorgusunun tipi (A, AAAA, MX …) | — |
| `attack` (`saldiri`) | metin | IPS imzasının adı | — |
| `virus` | metin | bulunan virüsün adı | — |
| `logid` | sabit | FortiGate `logid` alanı | — |
| `level` | sabit | olayın seviyesi (FortiGate, MikroTik) | — |
| `text` (`metin`) | serbest metin | kaydın metni (bölüm 4); alan adı yazmadan da aranır | — |

## 4. Serbest metin

- Bir kelime ya da tırnaklı ifade, **en az 3 karakter** (`search_term_too_short`).
- Kaydın metninin (`messages.text`) **ilk 2.000 karakterinde** aranır; satırın tamamı ham
  kayıttadır (bildirim `text_truncated`). FortiGate satırlarında metin key=value gövdesinin
  tamamıdır.
- **Büyük/küçük harf** yalnızca İngilizce harflerde yok sayılır: İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç
  yazıldığı gibi aranır (bildirim `text_case`).
- Serbest metin bir alan değildir: `172.20.41.10` metin olarak `172.20.41.105`'i de bulur; bir
  adres için `srcip:` ya da `ip:` kullanın.
- Serbest metin indeks kullanmaz; yalnızca serbest metinden oluşan bir sorgu zaman dilimleri
  halinde taranır (bölüm 5).

## 5. Zaman, sayfalar ve tarama

- **Zaman aralığı** `[başlangıç, bitiş)` biçimindedir ve **cihaz zamanına** (`time`, satırın
  cihazın bildirdiği zamanı) göredir; Alınma zamanı ayrıca gösterilir. En fazla `search.max_days`
  (31) gün. Hazır aralıklar: son 15 dakika, 1 saat, 24 saat, bugün (gece yarısından beri), dün,
  son 7 ve 30 gün.
- **Veritabanından önceki kayıtlar:** başlangıç en eski veritabanı kaydından önceyse bildirim
  `vault_only_before` gösterilir; o tarihten önceki satırlar mühürlü arşivdedir ve Kanıt Paketi
  ile alınır.
- **Tarama:** sonuçlar en yeniden eskiye gelir. Sunucu aralığı en yeni uçtan başlayarak zaman
  dilimleriyle okur: ilk dilim 1 saattir, hızlı yanıt veren dilim iki katına çıkar (2, 4, 8 saat
  …). Bir istek en fazla `search.request_budget_ms` (4 saniye) sürer. Sayfa dolar ya da aralığın
  başına ulaşılırsa tarama **tamamdır** (`complete`). Süre biterse sayfa o ana kadar bulunanları
  gösterir ve "… tarihine kadar tarandı" der; sayfa taramayı 5 kez kendiliğinden sürdürür, sonra
  "Devam et" düğmesini gösterir. İlk dilim süreyi aşarsa yarıya indirilir (en az 5 dakika); 5
  dakikalık dilim bile aşarsa arama `search_timeout` (504) ile biter ve denetim kaydına
  `outcome: "timeout"` olarak yazılır.
- **Sayfalar:** bir arama bir `search_id` alır; sonraki sayfalar, istatistikler, dışa aktarma ve
  kanıt aktarımı yalnızca bu numarayla istenir. Numara son kullanımdan sonra 30 dakika geçerlidir
  (kullanıcı başına en fazla 20 arama tutulur); süresi dolmuş ya da başka bir oturumun aramasıysa
  sayfa aramayı bir kez kendiliğinden yeniden çalıştırır (yeni bir `search.run` kaydı). Sayfa
  başına en fazla `search.page_size_max` (500) satır; görünümde en fazla 2.000 satır.
- **Sınırlar:** aynı anda en fazla `search.max_concurrent` (3) arama isteği çalışır, kullanıcı
  başına 2; boş yer 2 saniyede açılmazsa `search_busy` (429). Kullanıcı başına dakikada
  `search.runs_per_minute` (30) yeni arama (`search_rate_limited`).

### 5.1 Hangi sorgular hızlıdır?

Hız, sorgunun bir **indeksle** okunup okunamamasına bağlıdır. Bölüm 3'teki "Hızlı" sütunundaki
alanlardan biri (ör. `srcip:`, `domain:` + `type:web`, `qname:`, `mac:`, `dstip:` + `dstport:`)
sorguda olumlu bir koşul olarak bulunduğunda sunucu yalnızca eşleşen kayıtları okur ve 30 günlük
bir aralık da saniyeler içinde yanıtlanır. Yalnızca serbest metin, `action:`, `user:` gibi
indekssiz alanlar ya da yalnızca tersine çevrilmiş koşullar içeren bir sorgu dilim dilim
taranır; sayfa bunu `query_not_selective` bildirimiyle söyler ("Daha hızlı sonuç için srcip:,
domain:, mac: veya qname: gibi bir alan ekleyin"). Ölçülen süreler bölüm 13'tedir.

## 6. Kayıt türleri

`type:` alanının değerleri; aynı adlar istatistiklerde ve dışa aktarmada kullanılır.

| `type:` | Türkçe | Nereden |
|---|---|---|
| `traffic` | Trafik | FortiGate trafik |
| `web` | Web filtresi | FortiGate UTM webfilter |
| `dns` | DNS | FortiGate UTM DNS |
| `app` | Uygulama ve SSL | FortiGate UTM uygulama kontrolü, SSL |
| `ips` | Saldırı önleme | FortiGate UTM IPS |
| `virus` | Virüs | FortiGate UTM antivirüs |
| `utm` | Diğer UTM | diğer FortiGate UTM alt türleri |
| `event` | FortiGate olayı | FortiGate olayları |
| `dhcp` | DHCP | MikroTik DHCP (havuz olayları dahil) |
| `arp` | ARP | MikroTik ARP |
| `fw` | MikroTik güvenlik duvarı | MikroTik firewall |
| `account` | Oturum açma | MikroTik oturum açma |
| `interface` | Arayüz | MikroTik arayüz |
| `config` | Yapılandırma | MikroTik yapılandırma değişikliği |
| `wifi` | Kablosuz | MikroTik kablosuz bağlanma/ayrılma |
| `heartbeat` | Heartbeat | MikroTik heartbeat |
| `log` | MikroTik günlüğü | MikroTik uyarı ve üstü günlük |
| `syslog` | Genel syslog | genel syslog satırları |
| `unknown` | Tanınmayan | tanınmayan satırlar |
| `other` | Diğer | yukarıdakilerin dışındaki türler |

## 7. İstatistikler

Bir aramanın zaman çizelgesi (histogram), toplamı ve en sık değerleri (kaynak, tür, eylem, VLAN,
departman; ilk 10 değer ve "diğer") ayrıca istenir (`POST /search/stats`) ve üç yoldan biriyle
sayılır:

| Yol (`mode`) | Ne zaman | Nasıl |
|---|---|---|
| `rollup` | sorgu yalnızca `source`, `type`, `action`, `vlan`, `department` (ve tersleri) içeriyor, serbest metin yok, VLAN'lar en fazla /24 özel IPv4 ağları, aralık 12 saatten uzun ve özet sayılar hazır | saatlik özet tablodan (`search_counts`); 30 gün için bile anında |
| `hybrid` | aynı, ama aralık saat başında başlamıyor ya da bitmiyor | tam saatler özetten, kenardaki kısmi saatler doğrudan sayılır |
| `direct` | diğer her durum | kayıtlar doğrudan sayılır, `search.stats_budget_ms` (8 saniye) içinde |

- **Kova boyu:** 2 saate kadar 1 dakika, 12 saate kadar 5 dakika, 3 güne kadar 1 saat, 14 güne
  kadar 3 saat, daha uzun aralıklarda 6 saat; kovalar site gece yarısına hizalıdır.
- **Doğrudan sayımda** toplam ve histogram eşleşen her kaydı sayar; en sık değerler en yeni
  `search.facet_sample_rows` (100.000) eşleşmeden hesaplanır ve o zaman "son 100.000 eşleşmeye
  göre" yazılır (`sampled`). Süre biterse sayım o ana kadar sayılanı gösterir (`complete:
  false`, sayılmayan kısım gölgeli, toplam "≥ N").
- Bir türü olmayan (yalnızca `messages`'tan okunan) sorguda eylem, VLAN ve departman sayılamaz
  ("Bir tür seçin (ör. type:web)"); kaynak ve tür sayılır.
- **VLAN ve departman** istemci adresinin /24 ağından bulunur; içinde birden çok küçük VLAN olan
  bir /24 "Birden çok VLAN" olarak gösterilir. Departman VLAN'a tanımlı varsayılandır, kişi
  ataması değildir (kişi bazında kullanım: [KULLANIM.md](KULLANIM.md)).
- **Özet sayılar hazırlanıyorken** (yükseltmeden önce yüklenmiş günler) istatistikler doğrudan
  sayılır ve `counts_incomplete` bildirimi gösterilir; özet tablo `search.counts_backfill` işiyle
  arka planda doldurulur.

## 8. Kayıtlı aramalar

Sorgu ve zaman aralığı (hazır aralık ya da sabit tarih) bir adla kaydedilir. Kayıtlı aramalar
**kişiseldir**: her kullanıcı yalnızca kendininkileri görür; başka bir kullanıcının numarası
"bulunamadı" (404) yanıtı alır. Kullanıcı başına en fazla `search.saved_max_per_user` (100);
adlar büyük/küçük harf ayırmadan tekildir ve denetim ya da görünmez biçim karakteri (ör. NUL,
yön değiştirme) içeremez. Kayıtlı bir aramayı çalıştırmak normal bir aramadır
(`search.run`, `saved_id` ile); hazır aralık çalıştırma anına göre hesaplanır.

## 9. Dışa aktarma (CSV, Excel)

- Yalnızca **Yönetici** (`search.export`), aydınlatma metni onaylıyken ve **son 5
  dakika içinde parolasını yeniden doğrulamışsa** dışa aktarabilir.
- Aramanın satırları en fazla `search.export_max_rows` (100.000) satır ya da
  `search.export_budget_seconds` (120 saniye) boyunca okunur; sınıra takılan dosya **kesilmiş**
  olarak işaretlenir: dosya adı `-kismi` ile biter, yanıtta `X-Arci-Export-Truncated: rows|time`
  başlığı ve Excel'in "Bilgi" sayfasında nedeni bulunur. Satırların alanlarını tamamlayan
  sorgular da bu süreden yer; süre biterse dosya orada kesilir (`time`), hiçbir satır boş
  sütunlarla yazılmaz.
- **CSV:** UTF-8 (BOM), `;` ayraç, CRLF satır sonu. **Excel:** "Sonuçlar" ve "Bilgi" sayfaları,
  her hücre metin. Sütunlar: Zaman, Alınma zamanı, Kaynak, Gönderici, Tür, Alt tür, Kaynak IP,
  Kaynak port, Hedef IP, Hedef port, NAT IP, NAT port, Eylem, Alan adı, URL, Kullanıcı,
  Uygulama, Kategori, MAC, Metin (en fazla 2.000 karakter), Ham kayıt
  (`kaynak/gün/satır/konum`). CSV'de `=`, `+`, `-`, `@`, sekme (TAB) ya da satır başı karakteri
  (CR) ile başlayan değerler formül olarak çalışmasın diye başına `'` alır; Excel'de her hücre
  metindir. Denetim ve görünmez karakterler iki biçimde de görünür kaçışlarla (`\u0007`) yazılır.
- **Dosya mühürlü değildir.** "Bilgi" sayfası bunu söyler: "Bu dosya mühürlü değildir; bütünlük
  için Kanıt Paketi'ni kullanın." Resmi bir yanıt için Kanıt Paketi kullanın (bölüm 10).
- Dosya oluşturulur, SHA-256 değeri hesaplanır ve **gönderilmeden önce** denetim kaydına yazılır
  (`search.export`: satır sayısı, bayt, SHA-256, kesilme nedeni); tarayıcı `Repr-Digest`
  başlığıyla gelen dosyanın değerini denetler.
- **Sınırlar:** kullanıcı başına aynı anda bir dışa aktarma (`search_export_running`), saatte en
  fazla `search.export_max_per_hour` (10); bu sayı denetim kaydından sayılır, sunucu yeniden
  başlasa da geçerlidir (`search_export_rate_limited`).
- Dışa aktarılan dosya kişisel veri içerir; saklanması ve paylaşılması kuruluşun KVKK
  kurallarına tabidir.

## 10. Aramadan Kanıt Paketi

"Kanıt paketine aktar" (yalnızca `evidence.create` yetkisiyle, Yönetici) aramanın ham
satır başvurularını alır (`POST /search/refs`, en fazla 10.000 satır, Kanıt Paketi'nin satır
sınırı), denetim kaydına sayı ve listenin SHA-256 değeriyle yazar (`search.refs`) ve Kanıt
Paketleri sayfasını bu satırlarla doldurur. Arama tamamlanmadıysa, satır sınırına takıldıysa ya
da satırların bir kısmı henüz mühürlenmemiş günlerdeyse sayfa bunu paket oluşturulmadan önce
söyler. Paketin kendisi her zamanki gibi oluşturulur ([KANIT-PAKETI.md](KANIT-PAKETI.md)).

## 11. Kim ne görür, ne denetlenir

| | Yönetici | Denetçi | İK Raporları | Rapor İzleyici |
|---|---|---|---|---|
| Arama, sayfalar, istatistikler, kayıt ayrıntısı, kayıtlı aramalar, Canlı Akış | ✔ | ✔ | — | — |
| Dışa aktarma (yeniden doğrulama 5 dk) | ✔ | — | — | — |
| Kanıt paketine aktar | ✔ | — | — | — |

Arama ve Canlı Akış kişisel veri gösterir: aydınlatma metni onaylanmadan açılmaz
(`privacy_notice_required`). Denetim kaydına yazılanlar:

| Kayıt | Ne zaman | İçerik |
|---|---|---|
| `search.run` | her arama (yeniden çalıştırma dahil) | arama numarası, normalleştirilmiş sorgu, aralık, türler, ilk sayfadaki satır sayısı, tamamlandı mı, sonuç (`ok`/`timeout`), süre, kayıtlı arama numarası |
| `search.event` | bir kaydın ayrıntısı açılınca | tablo, numara, zaman, ham kayıt başvurusu |
| `search.refs` | kanıt paketine aktarım | arama numarası, sorgu, aralık, satır sayısı, kesildi mi, SHA-256 |
| `search.export` | dosya gönderilmeden önce | arama numarası, sorgu, aralık, biçim, satır, kesilme nedeni, bayt, SHA-256 |
| `search.saved_create` / `search.saved_update` | kayıtlı arama eklenince ya da değişince | numara, ad, sorgu |
| `search.saved_delete` | kayıtlı arama silinince | numara, ad |
| `live.open` / `live.close` | canlı akış açılınca / kapanınca | akış numarası, sorgu, klasörler; kapanışta süre, gösterilen ve gösterilmeyen satırlar, satır aralıkları |
| `raw.view` | bir ham satır açılınca | ham kayıt başvurusu |
| `raw.tail` | Kaynaklar'da bir kaynağın "Son satırlar" sekmesi açılınca | kaynak, gün, satır ve bayt aralığı, satır sayısı, aralığın SHA-256 özeti ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) bölüm 9) |

Denetlenmeyenler: sonraki sayfalar ve istatistikler (yalnızca denetlenmiş bir aramanın
numarasıyla istenebilirler), sorgu denetimi (`/search/parse`) ve alan listesi. Kayıtlarda satır
içeriği bulunmaz; yalnızca girdiler, sayılar ve özet değerleri bulunur.

## 12. Canlı Akış

Canlı Akış yeni gelen satırları **ham kayıt klasöründen** (spool) okuyarak gösterir; ingest
gecikse ya da dursa da çalışır ve veritabanına yük bindirmez. Bir cihazın log gönderip
göndermediğini görmenin en kısa yoludur ([DEVICE-SETUP.md](DEVICE-SETUP.md)).

- Filtre Arama ile aynı sorgu dilidir (boş olabilir). Ayrıştırılan alanlar "önizleme" olarak
  gösterilir (ingest'in son kaydı ayrıntıda farklı olabilir); toplayıcı biçiminde olmayan bir
  satır "biçim dışı" rozetiyle gösterilir.
- **Sınırlar:** akış başına saniyede en fazla `live.max_lines_per_second` (100) satır; sunucuda
  aynı anda `live.max_streams_total` (4), kullanıcı başına `live.max_streams_per_user` (2) akış
  (`live_stream_limit`). Gösterilemeyen satırlar sayılır ve nedeniyle bildirilir; 30 saniye
  satırlara yetişemeyen (ör. okumayı bırakan) bir bağlantının akışı durdurulur (`slow_client`).
  Sayfa en fazla 2.000 satır tutar (eskiler düşer).
- **Duraklat / Sürdür:** duraklatılan akış 120 saniye bekler ve yerini korur; "Sürdür" kaldığı
  yerden devam eder. Akış `live.max_minutes` (60) dakika sonra durur (gözetimsiz ekran).
- **Oturum:** akış 30 saniyede bir oturumu, yetkiyi ve aydınlatma metni onayını denetler; boşta
  kalan kullanıcı her sayfada olduğu gibi `auth.idle_minutes` sonunda çıkış yapar. Akışın
  arka plan istekleri boşta kalma süresini sıfırlamaz.
- **Yeniden bağlanma:** ağ hatasında 1, 2, 5, 10, sonra 30 saniye arayla yeniden bağlanır.
  Aradaki satırlar gösterilemediyse sayfa "Aradaki satırlar gösterilemedi; bu aralığı Arama'da
  açabilirsiniz." der ve aralığı Arama'da açan bağlantıyı verir.
- Akış açılışı ve kapanışı denetlenir (`live.open`, `live.close`); bir satırın ham hali her
  zamanki ham satır görünümüyle açılır (`raw.view`).

## 13. Performans

`api/tests/test_search_perf.py` (`poe test-perf`) ölçer; veriler ve sınırlar MVP-CONTRACTS.md
bölüm 17'dedir. Ölçüm verisi: en yeni gün 2.000.000 satır ve önceki 29 günün her biri 100.000
satır (4,9 milyon satır ve türlü kayıtları), yaklaşık 150 istemci, aranan nadir değerler.
Süreler API üzerinden (tarayıcı olmadan), bir ısınma çalıştırmasından sonra üç denemenin en
iyisidir.

Ölçüm (2026-10-26): 4 işlemcili, başka işlerle paylaşılan bir Linux geliştirme makinesi, PostgreSQL 16 yerel aylık bölümlerle (TimescaleDB yok); 4.913.180 `messages` satırı 130 saniyede yazıldı, özet tablo ve istatistiklerle birlikte veri 689 saniyede hazırdı. TimescaleDB'li CI işi aynı testi 7 günden eski parçalar sıkıştırılmış olarak çalıştırır.
Bu ölçümden sonra veriye günde 4.320 MikroTik olayı (`mt_event`; Q6 artık olay indeksini de
denetler) eklendi; aşağıdaki sayılar bu ekten önceki veriyle alınmıştır ve TimescaleDB'li ile
Windows CI işlerinin sayıları henüz kaydedilmedi.

| # | Sorgu | Aralık | Bütçe | İlk çalıştırma | En iyi | Not |
|---|---|---|---|---|---|---|
| Q1 | boş sorgu, ilk sayfa | 1 gün | 500 ms | 216 ms | 59 ms |  |
| Q1 | boş sorgu, ilk sayfa | 30 gün | 500 ms | 67 ms | 53 ms |  |
| Q2 | `srcip:<aranan istemci>` | 1 gün | 2.000 ms | 211 ms | 65 ms |  |
| Q2 | `srcip:<aranan istemci>` | 30 gün | 10.000 ms | 87 ms | 66 ms |  |
| Q3 | `srcip:172.20.41.0/24` | 1 gün | 2.000 ms | 70 ms | 70 ms | yaygın değer: birincil anahtar |
| Q4 | `srcip:<aranan> type:web action:blocked` | 30 gün | 10.000 ms | 72 ms | 57 ms |  |
| Q5 | `domain:<nadir alan adı> type:web` | 30 gün | 10.000 ms | 70 ms | 61 ms |  |
| Q6 | `type:dhcp mac:<nadir MAC>` | 30 gün | 10.000 ms | 72 ms | 73 ms |  |
| Q7 | `qname:<nadir ad>` | 30 gün | 10.000 ms | 58 ms | 58 ms |  |
| Q8 | istatistik `type:web action:blocked department:Finans` | 30 gün | 500 ms | 24 ms | 20 ms | yol `rollup` |
| Q9 | istatistik `srcip:<aranan>` (doğrudan) | 1 gün | 2.000 ms | 66 ms | 50 ms |  |
| Q10 | 10.000 satırlık dışa aktarma (Q2) | 30 gün | 10.000 ms | 2.715 ms | 2.549 ms |  |
| S1 | nadir serbest metin terimi | 1 gün | 4.500 ms | 2.377 ms | 2.386 ms | tamam, 10 satır |
| S2 | `dstport:3389 action:deny` | 1 gün | 4.500 ms | 94 ms | 62 ms | tamam, 100 satır |
| S3 | `ip:<aranan>` | 30 gün | 4.500 ms | 162 ms | 114 ms | tamam, 100 satır |

Ayrıca her sorgunun planı denetlenir: indeksli alanlarla (`srcip:`, `domain:`, `mac:`, `qname:`)
yüksek hacimli tablolar sırayla taranmaz.

## 14. Hata kodları

Her hata `code`, `message_tr` ve `message_en` taşır; reddedilen sorgunun hatalı kısmı
`details` içinde karakter konumuyla gelir.

| Kod | HTTP | Ne zaman |
|---|---|---|
| `search_syntax` | 422 | sorgu okunamadı (kapanmamış tırnak, boş değer …) |
| `search_unknown_field` | 422 | öyle bir alan yok |
| `search_value_invalid` | 422 | değer alanın biçimine uymuyor |
| `search_term_too_short` | 422 | serbest metin 3 karakterden kısa |
| `search_too_complex` | 422 | sorgu sınırları aşıldı |
| `search_or_unsupported` | 422 | `OR` ya da parantez |
| `search_fields_incompatible` | 422 | alanlar hiçbir kayıt türünde birlikte yok |
| `search_vlan_unknown` | 422 | VLAN tanımlı değil |
| `search_department_unknown` | 422 | departman yok |
| `search_range_invalid` | 422 | zaman aralığı geçersiz |
| `search_range_too_long` | 422 | aralık `search.max_days` gününden uzun |
| `search_cursor_invalid` | 422 | sayfa konumu bu aramaya ait değil |
| `search_expired` | 409 | arama numarasının süresi doldu (sayfa aramayı yeniden çalıştırır) |
| `search_timeout` | 504 | en kısa dilim (5 dakika) bile süreye sığmadı |
| `search_busy` | 429 | çok sayıda arama çalışıyor |
| `search_rate_limited` | 429 | kısa sürede çok fazla arama |
| `search_event_not_found` | 404 | kayıt veritabanında yok |
| `search_event_timeout` | 504 | kayıt süresi içinde okunamadı (veritabanı yoğun; kayıt var olabilir) |
| `search_export_running` | 409 | önceki dışa aktarma sürüyor |
| `search_export_rate_limited` | 429 | saatlik dışa aktarma sınırı |
| `saved_search_not_found` | 404 | kayıtlı arama yok |
| `saved_search_name_taken` | 409 | aynı adla bir kayıtlı arama var |
| `saved_search_limit` | 409 | kayıtlı arama sınırı |
