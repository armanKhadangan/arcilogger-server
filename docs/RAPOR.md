# Resmi Rapor (5651 sorgu raporu) / Official lookup report

> ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Rapor bir hukuki uygunluk
> beyanı değildir; metninin ve kullanımının hukuk müşaviri tarafından incelenmesi önerilir.

## Türkçe

**Ne zaman üretilir?** "5651 Sorgu" sayfasında bir sorgu çalıştırıldıktan sonra "Resmi Rapor
(PDF)" düğmesiyle (`GET /api/v1/lookup/{id}/report.pdf`). Rapor **bir kez** üretilir ve
`exports/reports/ARCILOGGER-5651-rapor-<no>.pdf` olarak saklanır; sonraki her indirmede aynı dosya
verilir, böylece SHA-256 özeti değişmez. Her üretim (`lookup.report`) ve her indirme
(`lookup.report_download`) denetim kaydına yazılır. Raporu sorguyu yapan kişi ve yöneticiler
alabilir; denetçi yalnızca kendi sorgularının raporunu görür.

**İçerik:**

1. Rapor bilgileri — sorgu no, kurum (`arcilogger.toml` içindeki `site_name`; ayarlanmamışsa
   "–" yazılır ve ad giriş cümlesinden ve altbilgiden çıkarılır), sorguyu yapan, sorgu ve rapor
   zamanı, ARCILOGGER sürümü.
2. Talep bilgileri — makam, dosya/evrak no, talep tarihi, gerekçe (sorgudan önce zorunlu).
3. Sorgu ölçütleri — zaman (Europe/Istanbul ve UTC; girilen metin ve saat diliminin nasıl
   yorumlandığı), genel IP/port, protokol, hedef, tolerans, iç IP, MAC.
4. Yöntem — NAT kaydı + IP–MAC zaman çizelgesi; **çift NAT** açıklaması ve kesin sonuç için
   gerekenler (ISS modeminin NAT kaydı veya köprü modu) — "sonuç bir aday listesidir" cümlesi
   yalnızca aday varken yazılır, sonuç yoksa "aday oturum da bulunamadı" yazılır; aday puanının
   anlamı; sorgu motorunun açıklamaları; veri penceresi.
5. Sonuçlar — kesin eşleşmeler veya adaylar; her biri için oturum, NAT, zaman farkı, hedef
   eşleşmesi ve tam atıf zinciri (iç IP → IP–MAC kaydı → cihaz → kişi/departman, güven
   düzeyleri; IP çakışmasında diğer olası zincirler, hepsi "düşük" güvenle). Kişi bağlantısının
   idari bir atama olduğu belirtilir; kişi, atama geçmişine göre **olay anında** geçerli olan
   atamadır ve aralığıyla yazılır ("atama: 01.10.2026 09:00:00 tarihinden itibaren"), departmanı
   kişinin bugünkü departmanıdır (departman geçmişi tutulmaz). Olay anında atanmamış bir cihazda
   kişi yazılmaz; atama geçmişi hiç olmayan bir cihazda sorgu anındaki atama gösterilir ve bunun
   olay anının kanıtı olmadığı yazılır (ADR-0008). Hedef eşleşmesinin
   zayıf türleri ayrı yazılır ("hedef IP eşleşti, port farklı", "yalnızca adres eşleşti …
   paylaşılan CDN adresi olabilir"). Yalnızca alan adı verilen bir kesin (NAT) sorguda alan
   adı FortiGate DNS/web/uygulama kayıtlarında görülmediyse oturum yine listelenir, ancak hedef
   "hedef doğrulanmadı" olarak yazılır; kayıt eksikliği başka bir hedefe gidildiği şeklinde
   yorumlanmaz.
6. Kanıt listesi — her kanıt (K1, K2 …) için veritabanı satırı, kaynak dosya, satır numarası,
   bayt konumu, ham satırın kendisi ve SHA-256 özeti; ham dosyaların rapor anındaki özetleri
   (mühürlü günlerde manifestle karşılaştırma); her mühürlü gün için manifest ve zaman damgası
   dosyalarının özetleri, seri no, damga zamanı, TSA, sağlayıcının Kamu SM üretim sunucusu olup
   olmadığı (pozitif ifade yalnızca doğrulama geçtiyse ve damga zinciri `tsa.legal_root_fingerprints`
   listesindeki bir köke ulaşıyorsa; manifesti üretim sunucusunu gösteren ama kökü listede olmayan
   gün "yasal sınıf olarak teyit edilmedi" diye nötr yazılır, test/mock damgası sayılmaz; "YASAL
   GEÇERLİ DEĞİL" yalnızca üretim sunucusu olmayan sağlayıcılar için, "test/geliştirme" yalnızca
   test (mock) damgaları için; "yasal geçerli" ifadesi kullanılmaz) ve
   **rapor anında yapılan doğrulamanın sonucu** (zincirin ilk günü, `python -m arcilogger_core
   verify` ile aynı kuralla denetlenir). Ham dosyası hâlâ spool klasöründe olan günler "henüz
   mühürlenmedi" olarak, spool dosyasının rapor anındaki özetiyle listelenir (gün rapordan en az
   iki gün önce kapandıysa "mühürleme gecikmiş" uyarısıyla). Ham dosyası kasada olduğu hâlde
   manifesti veya zaman damgası bulunmayan günler kırmızı **"MÜHÜR BULUNAMADI"** olarak yazılır —
   asla "henüz mühürlenmedi" olarak değil. 72 KiB'den uzun ham satırlar raporda kısaltılır ve
   bu açıkça belirtilir; satırın SHA-256 özeti ve uzunluğu her zaman satırın tamamınındır.
7. Raporun doğrulanması — özetin nasıl kontrol edileceği ve (açıksa) zaman damgası.
8. Yasal not. Her sayfada "Sayfa n / N", sorgu no, sürüm ve ifade kuralı vardır.

**Ham satır karşılaştırması (bölüm 4 ve 6):** sorgunun sonucu veritabanı satırlarından
(`fg_traffic`, `mt_dhcp`, `mt_arp`, `leases`) gelir; veritabanı türetilmiş veridir, kanıt ham
kayıt satırıdır. Rapor oluşturulurken her kanıt satırı ingest hizmetinin kullandığı ayrıştırıcıyla
yeniden okunur ve raporda gösterilen değerlerle karşılaştırılır: oturumun kaynak/hedef adres ve
portları, protokol, FortiGate oturum no, NAT adresi/portu; IP-MAC kaydının IP ve MAC adresi
(IP'yi başka bir cihaza veren kapanış satırında yalnızca IP); her satırda kayıt türü ve zamanı.
Bir fark varsa rapor bunu kırmızı uyarıyla yazar (bölüm 4 ve 5'in başında; farklar bölüm 6'da
ilgili kanıtın altında, "raporda X, ham satırda Y"); o sonuç **"kesin eşleşme" olarak değil
"kanıtla uyuşmayan sonuç"** olarak gösterilir ve zincire güven düzeyi verilmez. Karşılaştırma
veritabanının kayıtlar alındıktan sonra değişmesini (elle düzeltme, hatalı yeniden hesaplama,
müdahale, ayrıştırıcı sürümü değişikliği) ortaya çıkarır; ayrıştırıcının kendi hatasını
ortaya **çıkaramaz** (aynı ayrıştırıcı aynı hatayı yineler) ve cihazın kişiye bağlanması bir
log satırına dayanmaz, idari bir atamadır. 5651 Sorgu sayfası veritabanı sonucunu gösterir;
karşılaştırma resmi rapordadır.

**Raporun özeti nasıl doğrulanır?** PDF kendi özetini içeremez. Özet ARCILOGGER'da sorgu
kaydında (`report.sha256`), indirme yanıtının `X-Arci-Sha256` başlığında ve denetim kaydında
(`lookup.report`) bulunur. Dosyanın özetini yeniden hesaplayıp karşılaştırın:

```
Windows:      certutil -hashfile ARCILOGGER-5651-rapor-17.pdf SHA256
Linux/macOS:  sha256sum ARCILOGGER-5651-rapor-17.pdf
```

**İsteğe bağlı zaman damgası:** `lookup.report_timestamp` ayarı açıksa (varsayılan kapalı;
yönetici PowerShell'inde
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set lookup.report_timestamp true --by AD`) PDF'in
özeti, `arcilogger.toml` içindeki `[tsa]` sağlayıcısıyla RFC 3161 zaman damgasına gönderilir
(Kamu SM ile rapor başına **1 kontör**). Damga `….pdf.zd` olarak PDF'in yanında saklanır ve
`GET /api/v1/lookup/{id}/report.pdf.zd` ile indirilir. PDF, sağlayıcıyı ve sağlayıcı Kamu SM üretim
sunucusu değilse damganın **yasal geçerli olmadığını** açıkça yazar. Doğrulama, mühürlü günlerle
aynı yöntemle ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) Yöntem B ve C1; istemci sürümü ve
çıktı biçimi orada ve [kamusm.md](kamusm.md)'de):

```text
java -jar tss-client-console-3.1.33.jar -CC ARCILOGGER-5651-rapor-17.pdf ARCILOGGER-5651-rapor-17.pdf.zd
openssl ts -verify -in ARCILOGGER-5651-rapor-17.pdf.zd -data ARCILOGGER-5651-rapor-17.pdf \
    -CAfile kokler.pem -attime <genTime>      # yalın belirteçse -token_in
```

Damga alınamazsa rapor yine verilir; durum `failed` ve hata metni sorgu kaydında ve denetim
kaydında görülür (otomatik yeniden deneme yoktur).

**Rapor üretilemezse hiçbir şey saklanmaz:** Kanıt dosyalarından biri (ham dosya, manifest,
zaman damgası) var olduğu hâlde şu anda okunamıyorsa — API hizmetinin klasör izni yoksa 503
`report_access_denied`, başka bir okuma hatasında 503 `report_evidence_unreadable` —, dayandığı
bir gün tam o sırada mühürleniyorsa (503 `report_seal_in_progress`; rapor sırasında mühürlenen
gün yeniden okunur ve hiçbir zaman "mühür bulunamadı" olarak kaydedilmez) veya PDF
dizilemezse (500 `report_render_failed`) rapor kaydedilmez; sorun giderildikten sonra aynı istek
eksiksiz raporu üretir. Geçici bir sunucu sorunu, bir kez üretilip hep aynı verilen rapora
"dosya okunamadı" olarak işlenmez. Kanıtın kendisine ait durumlar (bozuk sıkıştırılmış veri,
mühürden sonra değişmiş dosya, kaybolmuş mühür) ise raporda açıkça yazılır.

**Yeniden üretme yok:** Saklanan dosya eksik veya özeti farklıysa ARCILOGGER raporu yeniden
üretmez (409 `report_missing` / `report_damaged`), çünkü verilen özet geçersiz kalırdı ve yeni
bir rapor kasayı *bugünkü* hâliyle okurdu. Raporun dayandığı girdiler (`lookup_reports.inputs`)
saklanır; aynı girdiler aynı ARCILOGGER, reportlab ve yazı tipi sürümleriyle **bayt bayt aynı**
PDF'i verir (inceleme için).

## English (short)

The official report is rendered once per lookup, stored under `exports/reports/` and served
unchanged, so its SHA-256 ("ARCILOGGER signature digest", kept in `lookup_reports.sha256`, the
`X-Arci-Sha256` header and the audit entry `lookup.report`) stays valid. It contains the
organisation (`site_name`; a dash while it is not set, and the name is left out of the opening
sentence and the footer), the request details, the query as entered and as interpreted, the method
(with the double-NAT explanation and what would make the answer exact), the results with full
attribution chains (the person is the assignment valid at the moment of the event, printed with its
interval; the department is the person's current one; ADR-0008), the evidence (raw lines with file,
line number, byte offset and hash; file hashes; for every sealed day the manifest and token hashes,
serial, genTime, TSA, the provider row and a fresh verification (the provider row keeps three facts
apart, decision 5: a production day is named positively only when its chain reaches a root listed in
`tsa.legal_root_fingerprints`; a production day whose root is not listed is "not confirmed as legal
grade", never a test stamp; "YASAL GEÇERLİ DEĞİL" only for other providers, "test/geliştirme" only
for mock stamps); days not sealed yet with the spool file's hash at report time, flagged when
sealing is overdue; days whose files are in the vault without manifest or token as **seal not
found**, in red), how to verify the report, and the legal note; every page is numbered. Nothing is
stored when a cited file exists but cannot be read now (503 `report_access_denied` /
`report_evidence_unreadable`) or the layout fails (500 `report_render_failed`); the next request
tries again. Every cited raw line is parsed again with the ingest parser and compared with what the
report shows from the database (session addresses, ports, protocol, session number, NAT; IP and MAC
of an IP-MAC record; record type and time): a difference is printed in red, and that result is shown
as "contradicted by its evidence", never as an exact match or with a confidence. The check finds a
database changed after ingest, not a defect of the parser itself. With `lookup.report_timestamp` the
PDF's hash is time-stamped (1 kontör), the `.zd` is stored next to it and the PDF names the provider
and warns when it is not the Kamu SM production server (it never states that a stamp is legally
valid). Verify with `certutil -hashfile … SHA256` / `sha256sum`, and the token with Zamane `-CC` or
`openssl ts -verify`.

## Verify on site / open items

- Zamane from the API service: the stamp runs the Java client as `NT SERVICE\ArciApi`; the
  service needs read access to `secret.key` (it has it, ADR-0007), Java and the jar, and write
  access to `exports\reports` (it has it). Test once with the Kamu SM **test** server.
- The mock provider keeps its keys in `state\mock-tsa`, which the API may only read (ADR-0007):
  the sealing task (or `arcilogger-core tsa-test`) must create them first; a report stamp from the
  API does not count down the mock's credit counter (it cannot write there). The mock is never
  legal, and its root is trusted only while `tsa.provider` is `mock`.
- The API service reads the raw spool of days not sealed yet: the installer's ACL plan
  (`ArciInstall.psm1` `Get-ArciAclPlan`, ADR-0007) grants `NT SERVICE\ArciApi` read
  (ReadAndExecute, no write or delete) on `<data>\spool`; re-running the installer or
  `Set-ArciVaultAcl.ps1` gives existing installations the right. On site: as ArciApi, view a
  raw line of today and make a report that cites today.
- Reading cost of a report: each cited raw file is read once (hash + lines); a sealed day is
  decompressed up to the last cited line and `verify_day` re-hashes the day's sealed files —
  roughly 1–4 s per GB on the server (measure on site with a full day).
- Cost of `GET /raw` (measured on the development machine; measure on site): the first view of
  a late line reads the file up to it — spool about 0.45 s per 500 MB, a sealed `.log.gz` about
  1.1–2 s per 500 MB of uncompressed log (so up to ~20 s for a late line of a 5 GB day). It
  leaves checkpoints in memory (spool: line starts every 4 MiB, 32 files; sealed files: a copy
  of the decompressor every 64 MiB, ~40 KB each, 8 files), so every later view in that file
  costs well under a second (measured 0.07 s for 500 MB). Views of one sealed file wait for each
  other and at most two sealed files are decompressed at once, which bounds the CPU a user can
  take; there is no per-user rate limit yet.
- `GET …/report.pdf` creates the report on first use (a GET with a side effect; cookies are
  SameSite=Strict, so another site cannot trigger it). Lookups and reports are not rate-limited
  yet (spec 13 "rate limits on … exports").
- Stored reports are kept with the lookup (no retention job for `exports/reports` yet); they
  contain person-level data — listed as an open decision in the draft retention/deletion policy
  for legal review ([legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md)).
- The report wording (Turkish and English) needs legal review before the first official use.
