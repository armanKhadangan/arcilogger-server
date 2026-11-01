# Denetçi Kılavuzu / Auditor Guide

> Durum: **MVP — kanıt paketi (web arayüzünden ve komut satırından), 5651 raporu ve bağımsız
> doğrulayıcı**. ARCILOGGER, 5651 sayılı Kanun ve İnternet
> Toplu Kullanım Sağlayıcıları Hakkında Yönetmelik kapsamındaki yükümlülükleri **desteklemek için
> tasarlanmıştır**; hukuki uygunluk iddiasında bulunmaz. Kayıtların ve bu kılavuzun hukuki
> değerlendirmesi için bir hukuk danışmanına başvurun.
>
> Status: **MVP — evidence packages (web interface and command line), the 5651 report and the
> standalone verifier**. ARCILOGGER is *designed to support* the
> obligations of Law 5651; it does not claim legal compliance. English version: [below](#english).

## İçindekiler

1. [Delil zinciri nasıl çalışır?](#1-delil-zinciri-nasıl-çalışır)
2. [Ne saklanır, ne kadar süre?](#2-ne-saklanır-ne-kadar-süre)
3. [Bir günü sunucuda doğrulamak](#3-bir-günü-sunucuda-doğrulamak)
4. [Kanıt paketi](#4-kanıt-paketi)
5. [Bağımsız doğrulama adım adım](#5-bağımsız-doğrulama-adım-adım)
6. [Doğrulama neyi gösterir, neyi göstermez?](#6-doğrulama-neyi-gösterir-neyi-göstermez)
7. [Doğrulayıcı çıktısını yorumlamak](#7-doğrulayıcı-çıktısını-yorumlamak)
8. [Sahada netleşecek konular](#8-sahada-netleşecek-konular)
9. [Arama, Canlı Akış ve Kaynaklar denetim kayıtları](#9-arama-canlı-akış-ve-kaynaklar-denetim-kayıtları)
10. [5651 raporunu okumak (okuma tablosu)](#10-5651-raporunu-okumak-okuma-tablosu)

## 1. Delil zinciri nasıl çalışır?

```
FortiGate / MikroTik ── syslog ──► ArciCollector ──► spool\<kaynak>\<gün>.log      (1) ham kayıt
                                                          │  gün bitince (00:10)
                                                          ▼
                     vault\YYYY\MM\DD\<kaynak>.log.gz  + SHA-256                    (2) mühürlü dosya
                                                          │
                     manifest-YYYY-MM-DD.json  (dosya özetleri + önceki günün özeti) (3) manifest, zincir
                                                          │  yalnızca SHA-256 özeti
                                                          ▼
                     Kamu SM zaman damgası ──► manifest-YYYY-MM-DD.json.zd          (4) RFC 3161 damga
                                                          │
                     doğrulama → salt okunur + NTFS ACL → WORM ikinci kopya         (5) koruma
```

1. **Ham kayıt.** Toplayıcı her syslog mesajını alındığı anda, değiştirmeden, tek satır olarak yazar:
   `<alınma zamanı, +03:00> <gönderen IP> <mesaj>`. Mesajdaki kontrol karakterleri ve `#` işareti
   geri döndürülebilir biçimde `#` + üç sekizlik rakamla yazılır (satır sonu `#012`, `#` → `#043`);
   diğer bütün baytlar — UTF-8 olmayanlar dahil — cihazdan geldiği gibidir. Böylece bir mesaj asla
   iki satıra bölünmez ve orijinal baytlar birebir geri elde edilebilir.
2. **Mühürleme.** Gün (Türkiye saatiyle 00:00–24:00, alınma zamanına göre) kapandıktan sonra her
   kaynak dosyası belirlenimci olarak sıkıştırılır (`gzip -n`: dosya adı ve zaman yok), sıkıştırılmış
   ve açılmış halinin SHA-256 özetleri ve satır sayısı hesaplanır.
3. **Manifest.** Günün bütün dosyaları, ARCILOGGER denetim kaydı, kesintiler (bir kaynaktan uzun
   süre kayıt gelmemesi), saat eşitleme bilgisi ve **bir önceki günün manifest ve zaman damgası
   özetleri** kanonik JSON (RFC 8785) olarak `manifest-YYYY-MM-DD.json` dosyasına yazılır. Her gün
   bir öncekine bağlandığı için geçmişteki bir günün değiştirilmesi sonraki günlerde fark edilir.
4. **Zaman damgası.** Manifestin SHA-256 özeti TÜBİTAK BİLGEM Kamu SM'nin zaman damgası
   sunucusuna gönderilir; dönen RFC 3161 belirteci `.zd` dosyası olarak saklanır. Kamu SM'ye
   **yalnızca özet** gider. Damga, manifestin o anda var olduğunu ve sonradan değişmediğini gösterir
   (5070 sayılı Kanun); veriyi kimin oluşturduğunu göstermez.
5. **Doğrulama ve koruma.** Damga alındıktan hemen sonra ARCILOGGER'ın kendi doğrulayıcısı damgayı,
   imzayı, sertifika zincirini ve bütün dosya özetlerini kontrol eder; ancak bundan sonra gün
   "mühürlü" sayılır, dosyalar salt okunur yapılır (NTFS reddetme ACL'si) ve ikinci kopyanın
   hedefindeki (ör. bir NAS) WORM klasörüne kopyalanır. Bir yönetici dosyayı yine de silebilir ya da
   değiştirebilir; ancak değişiklik özetler ve damgalar sayesinde **tespit edilir** (ayrıntı:
   `adr/0003`).

Manifestin alanları:

| Alan | Anlamı |
|---|---|
| `schema`, `system` | Biçim sürümü (`arcilogger.manifest/1`), ürün, sürüm, sunucu, kurum |
| `day`, `tz`, `window` | Gün, saat dilimi (Europe/Istanbul) ve günün `[başlangıç, bitiş)` aralığı |
| `files[]` | Kaynak başına: `path`, `bytes`, `sha256` (sıkıştırılmış), `sha256_uncompressed`, `lines`, ilk/son alınma zamanı, gönderen IP'ler; çökme ya da dolu disk yüzünden son satırı yarım kalmış dosyada `unterminated_last_line: true` (baytlar olduğu gibi mühürlenir, yarım satır da satır sayısına girer) |
| `audit_log` | ARCILOGGER denetim kaydının o günkü dosyası ve özeti (dosyanın kendisi kanıt paketine varsayılan olarak alınmaz, bkz. bölüm 4) |
| `gaps` | Beklenen bir kaynaktan uzun süre kayıt gelmeyen aralıklar (bilgi); `kind: "unsealed_empty_days"` kaydı, hiç log olmadığı için tek tek damgalanmamış uzun bir gün dizisini (`first_day` … `last_day`) listeler — bu günün `previous` bağlantısı o dizinin öncesindeki mühürlü güne gider |
| `clock` | Saat eşitleme örnekleri ve en büyük sapma |
| `tsa` | Zaman damgası sağlayıcısı ve `legal_grade`: manifestin kendi beyanı (yapılandırma Kamu SM üretim sunucusunu gösteriyordu). ARCILOGGER bir günü ancak doğrulanan damga zinciri yapılandırmadaki bir Kamu SM köküne (`tsa.legal_root_fingerprints`) ulaşıyorsa "yasal sınıf" gösterir. `require_critical_eku: false` varsa kurum, Kamu SM'nin EKU uzantısını kritik işaretlemeyen sertifika profilini kabul etmiştir (K6) |
| `previous` | Önceki günün manifest ve damga dosyalarının SHA-256 özetleri |
| `deletions` | Saklama süresi dolduğu için silinen günlerin damgalı imha tutanakları |
| `stamped_late`, `created_at` | Geç mühürleme notu, manifestin oluşturulma zamanı |

## 2. Ne saklanır, ne kadar süre?

| Veri | Yer | Süre |
|---|---|---|
| Günün ham kayıtları (yazılmakta olan) | `D:\ARCILOGGER\spool\` | Gün mühürlenip kasadaki kopya doğrulanınca silinir |
| Mühürlü günler (ham kayıt, denetim kaydı, manifest, damga) | `D:\ARCILOGGER\vault\YYYY\MM\DD\` | Varsayılan **760 gün**; ayar 730 günün (2 yıl) altına indirilemez |
| İmha tutanakları ve zincir çapası | `vault\deletions\` | Süresiz (silinen günlerin özetlerini ve zinciri korur) |
| Yenileme damgaları (TSA sertifikası bitmeden yeniden damgalama) | `vault\renewals\` | Süresiz (M6'da etkinleşir) |
| İkinci kopya | Replika hedefindeki WORM klasörü (`[replica] target`, ör. bir NAS) | Kasa ile aynı |
| Kanıt paketleri | `D:\ARCILOGGER\exports\` | **30 gün** (`evidence.keep_days`; worker her gece siler, elle: `arcilogger-core evidence purge`) |
| Resmi 5651 raporları (PDF ve `.zd` damgası) | `D:\ARCILOGGER\exports\reports\` | Sorguyla birlikte kalır; **henüz süre sınırı ve silme işi yok** |
| 5651 sorguları (talep bilgileri, sorgu, sonuç) | PostgreSQL `lookups` | **Henüz süre sınırı yok** |
| ARCILOGGER denetim kaydı | PostgreSQL `audit_log` + günlük dosyası kasada (`audit.jsonl.gz`) | Veritabanındaki kayıt süresiz (silinmesi tetikleyiciyle engellenir); kasadaki günlük dosyası günüyle birlikte saklanır ve imha edilir |
| Ayrıştırılmış kayıtlar (arama, sorgu) | PostgreSQL | Türetilmiş veridir; ham kayıtlardan yeniden üretilebilir. Hedef 400 gün (`db.retention_days`), ancak bu sürümde bu tabloların **imha işi yoktur**: ayrıştırılmış kayıtlardan hiçbir şey silinmez |
| Kullanım özetleri (kişi, cihaz ve departman bazında, adlarla: siteler, URL'ler, engellenen denemeler, saatlik satırlar) | PostgreSQL `usage_*` tabloları, `domain_categories` | **400 gün** (`usage.retention_days`): worker işi `usage.retention` her gece 03:30'dan sonra daha eski günleri siler; biten yeniden hesaplama istekleri (`usage_rebuilds`) 90 gün sonra silinir. İmha tutanağı tutulmaz, silinen satır sayıları işin çalışma kaydına (`job_runs`) yazılır. Ayrıştırılmış kayıtlar veritabanında durdukça yeniden hesaplanabilir |
| Kayıtlı aramalar (ad, sorgu, zaman aralığı; yalnızca sahibine görünür) | PostgreSQL `saved_searches` | Sahibi silene ya da kullanıcı silinene kadar |
| Arama dışa aktarımları (CSV, Excel) ve canlı akış satırları | Sunucuda dosya olarak saklanmaz: dışa aktarma bellekte hazırlanıp indirilir, canlı akış son satırları yalnızca bellekte tutar | Dışa aktarmanın boyutu ve SHA-256 özeti denetim kaydındadır (`search.export`, bölüm 9) |
| Kullanım raporları (HTML, PDF, Excel; kişi adlarıyla) | `D:\ARCILOGGER\exports\usage-reports\` | **400 gün** (`reports.keep_days`; worker her gece 03:20'den sonra siler). E-postayla gönderilen kopyalar alıcılarda kalır |

Saklama ve imha kararları için hukuk müşavirine verilecek taslaklar:
[legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md),
[legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md).

Kasadan silme yalnızca saklama görevi (`retention`, varsayılan **kapalı**) tarafından yapılır:
süresi dolan gün önce yeniden doğrulanır, silinen günlerin listesi ve özetleri bir **imha
tutanağına** yazılır, tutanak zaman damgasıyla mühürlenir, son silinen günün özetleri **zincir
çapası** olarak saklanır ve tutanak ertesi günün manifestine eklenir. Böylece zincir, kalan en eski
günden itibaren doğrulanabilir kalır.

## 3. Bir günü sunucuda doğrulamak

Sunucuda, yönetici olarak açılmış PowerShell'de (program `PATH` üzerinde değildir):

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core verify --day 2026-10-12 --deep    # bir gün, açılmış içerik dahil
& $core verify --last 90                   # son 90 gün
& $core verify --all                       # bütün zincir (her ayın 1'inde otomatik)
```

Web arayüzünde **Mühürleme** sayfasındaki "Doğrula" düğmesi aynı kontrolü bir gün için yapar.

Doğrulama her gün, her hafta (son 90 gün) ve her ay (bütün zincir) Görev Zamanlayıcı ile otomatik
çalışır; aylık sonuç bir sonraki manifeste `chain_check` olarak mühürlenir.

## 4. Kanıt paketi

Bir 5651 talebi ya da denetim için seçilen günlerin **kendi başına doğrulanabilen** bir kopyası.
Paket iki yoldan oluşturulur: web arayüzünde **Kanıt Paketleri** sayfasından ya da bir 5651
sorgusundan (bölüm 4.1; olağan yol) veya sunucuda komut satırından:

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core evidence build --from 2026-10-10 --to 2026-10-12 `
    [--source fortigate --source core-r1] [--extract satirlar.json] [--pdf rapor.pdf] `
    [--password] [--lookup-id L-42] [--reference "Savcılık 2026/123"] [--out KLASÖR] `
    [--include-audit-log]
```

| Paket içeriği | Açıklama |
|---|---|
| `paket.json` | Paket dizini (kanonik JSON): her dosyanın boyutu ve SHA-256 özeti, kapsam (günler, kaynaklar, alıntılanan satırlar, sorgu no, oluşturan, zaman), kök sertifika parmak izleri, ARCILOGGER sürümü |
| `vault/YYYY/MM/DD/…` | Mühürlü günler, sunucudaki yapıyla aynı: kaynak dosyaları (**her zaman dosyanın tamamı**: 5651 sorgusundan oluşturulan pakette varsayılan olarak alıntılanan günlerin **bütün** kaynakları — bölüm 4.1; aksi hâlde seçilen kaynaklar), manifest, `.zd` damga; günün ARCILOGGER denetim kaydı (`audit.jsonl.gz`) yalnızca yönetici açıkça istediyse (`--include-audit-log`; web arayüzünde yalnızca yöneticinin işaretleyebildiği, varsayılan olarak boş kutu) — yoksa `paket.json`'da `excluded` altında nedeni ve SHA-256 değeriyle listelenir |
| `vault/deletions/`, `vault/renewals/` | Gerektiğinde imha tutanakları, zincir çapası, yenileme damgaları |
| `extract/GÜN/<kaynak>.txt` | İsteğe bağlı: sorguyla ilgili ham satırlar, her biri `<satır no><TAB><ham satır>` biçiminde, bayt bayt aynı |
| `sertifikalar/kokler/`, `sertifikalar/kokler.pem` | Paketteki damgaların gerçekten bağlandığı kök sertifikalar (yalnızca onlar; `tsa.roots_dir`'dan) |
| `sertifikalar/tsa/` | Damgalardan çıkarılan TSA sertifikaları |
| `sil/` | Varsa sertifika iptal listeleri (SİL); yoksa `paket.json` bunu belirtir |
| `verify.py`, `verify.ps1`, `verify.sh` | Bağımsız doğrulayıcı ve çalıştırma betikleri |
| `DOGRULAMA.txt`, `VERIFY-EN.txt` | Bu paketin dosya adlarıyla hazırlanmış adım adım doğrulama talimatı (dosya adı bilinçli olarak ASCII: her ZIP aracı aynı adla açar; içerik Türkçe) |
| `rapor/…pdf`, `rapor/….pdf.zd` | 5651 sorgusundan oluşturulan pakette sorgunun saklanan resmi raporu ve (varsa) raporun zaman damgası (bölüm 4.1); komut satırında `--pdf` ile verilen isteğe bağlı rapor |

- **Parola:** `--password` paketi AES-256 ile şifreler (WinZip AES, 7-Zip ile açılır). Parola
  rastgele üretilir, **yalnızca bir kez gösterilir ve hiçbir yerde saklanmaz**. Dosya adları
  ZIP'te şifrelenmez, içerikler şifrelenir. Windows'un yerleşik ZIP açıcısı AES şifreli paketleri
  açamayabilir.
- **Tekrarlanabilirlik:** aynı girdilerle iki kez üretilen paket, oluşturma zamanı dışında bayt
  bayt aynıdır (ZIP içi tarihler bilinçli olarak 01.01.1980).
- **Kasaya dokunulmaz:** paket oluşturulurken kasadaki dosyalar yalnızca okunur. Damgası
  doğrulanmayan, özeti manifestle uyuşmayan bir dosya ya da pakete alınan ardışık günler arasında
  kırık bir zincir bağlantısı varsa paket **oluşturulmaz**.
- **Kendi doğrulayıcısından geçmeyen paket verilmez:** bitmiş ZIP, `verify.py`'nin kendi koduyla
  (ZIP'ten geri okunarak) kontrol edilir; tek bir HATA bulunursa paket silinir ve verilmez.
- **Doğrulayıcının özeti:** `evidence build`, paketteki `verify.py`'nin SHA-256 özetini de yazar
  (`PackageInfo.verifier_sha256`). Bu değeri alıcıya **paketten ayrı bir kanaldan** (teslim
  tutanağı, resmî yazı) iletin; paketin içindeki `verify.py` kendi kendine kefil olamaz.
- **Denetim kaydı:** web arayüzünden (API'den) oluşturulan paketler denetim kaydına yazılır:
  istek `evidence.request`, bitmiş paket `evidence.create` (ZIP'in, `paket.json`'ın ve
  `verify.py`'nin SHA-256 özetleriyle), her indirme `evidence.download`, süresi dolup silinme
  `evidence.purge`. `paket.json`'ın SHA-256 değeri (doğrulayıcının `index-hash` satırı) denetim
  kaydındaki değerle karşılaştırılabilir. Komut satırındaki `evidence build` denetim kaydına
  **yazmaz** (veritabanı kullanmaz); bu yol sunucu yöneticisi içindir.

### 4.1 Web arayüzünden ve 5651 sorgusundan oluşturulan paket

Ayrıntı: [KANIT-PAKETI.md](KANIT-PAKETI.md).

- Paketleri yalnızca **yönetici** oluşturur (`POST /api/v1/evidence`, son 5 dakika içinde parola
  onayıyla); denetçi görebildiği paketleri indirir. Yönetici olmayan bir kullanıcı başka bir
  kullanıcının sorgusunu ve ondan oluşturulan paketleri görmez.
- **Kapsam:** bir 5651 sorgusundan oluşturulan paket varsayılan olarak (`content: "full_days"`)
  alıntılanan günlerin **bütün kaynaklarının** mühürlü dosyalarını tam olarak içerir — yalnızca
  sorgunun dayandığı kaynakları değil; alıntılanan satırlar ayrıca `extract/` altındadır.
  `content: "extract"` ile yalnızca alıntılanan satırların geldiği kaynakların tam dosyaları girer.
  Hangi günlerin ve kaynakların pakette olduğu `paket.json`'ın kapsamında (`index-scope`) yazılıdır
  (ayrıntı: [KANIT-PAKETI.md](KANIT-PAKETI.md)).
- Bir 5651 sorgusundan (`lookup_id`) oluşturulan paket, sorgunun **saklanan resmi raporunu**
  `rapor/ARCILOGGER-5651-rapor-<id>.pdf` olarak içerir. Bu, sorgu sırasında üretilip saklanan
  **aynı baytlardır** (paket için yeniden üretilmez); bu yüzden SHA-256 özeti şu değerlerle aynı
  olmalıdır: ARCILOGGER'daki sorgu kaydı (`lookup_reports.sha256`), raporu indirirken gelen
  `X-Arci-Sha256` başlığı ve denetim kaydındaki `lookup.report`, `evidence.request` ve
  `evidence.create` girişleri (`details.report.sha256`). `verify.py` bu özeti `report-hash`
  satırında yazar. Raporun nasıl okunacağı (okuma tablosu): bölüm 10.
- Raporun zaman damgası (`….pdf.zd`) pakete yalnızca rapor saklanırken doğrulanmışsa **ve**
  paketteki köklerle hâlâ doğrulanıyorsa girer. Girmezse `paket.json`'daki `report_note` nedenini
  söyler ve doğrulayıcı `report-token-omitted` uyarısı verir (paket yine doğrulanır). Rapora damga
  hiç alınmadıysa (isteğe bağlı ayar) `report-not-stamped` bilgisi çıkar.
- `paket.json` raporu `report`, damgasını `report_token` alanında adlandırır; `rapor/` klasöründe
  bunlardan başka bir dosya ya da adı geçip olmayan bir dosya HATA'dır.
- Raporun damgası OpenSSL ile de doğrulanabilir (Yöntem C gibi):

  ```bash
  openssl ts -verify -in rapor/ARCILOGGER-5651-rapor-<id>.pdf.zd \
      -data rapor/ARCILOGGER-5651-rapor-<id>.pdf -CAfile sertifikalar/kokler.pem
  ```

  (`.zd` yalın belirteçse `-token_in` eklenir; [kamusm.md](kamusm.md) K1.)
- Paketin kapsamı ve ARCILOGGER'ın kendi denetim kaydının pakete girip girmediği `paket.json`'da
  yazılıdır; denetim kaydı varsayılan olarak pakete **girmez** (kişisel verinin en aza
  indirilmesi) ve doğrulayıcı bunu hata saymaz.
- **Lisans kanıtı değiştirmez** ([LISANS.md](LISANS.md) bölüm 12): ham kayıtlarda, mühürlü
  günlerde, manifestlerde, damgalarda, kanıt paketinde, doğrulayıcıda ve 5651 raporunda lisans
  işareti yoktur; 5651 sorgusu, rapor ve kanıt paketleri her lisans durumunda çalışır. Denetim
  kaydındaki `license.*` girişleri lisans işlemleridir (deneme başlangıcı, yükleme, kaldırma, ret,
  sınır, durum değişikliği); lisans sahibinin adını ve sunucu kimliklerini taşımazlar.

## 5. Bağımsız doğrulama adım adım

Paketi alan kişi ARCILOGGER'a ya da şirketin sunucusuna ihtiyaç duymaz. Üç bağımsız yöntem vardır;
en az ikisinin kullanılması önerilir.

> **`verify.py`'ye ne zaman güvenilir?** `verify.py` paketin içinde gelir; paketi değiştirebilen
> biri onu da değiştirip her zaman "DOĞRULANDI" yazdırabilir. Sonucuna ancak (a) özetini paketten
> ayrı bir kanaldan aldığınız değerle karşılaştırdıysanız ya da (b) kendi edindiğiniz bir kopyayı
> pakete karşı çalıştırdıysanız (`python -I -S <kendi kopyanız>/verify.py <paket klasörü>`)
> güvenin. İkisi de mümkün değilse **Yöntem C**'yi uygulayın: C yalnızca OpenSSL'e ve standart özet
> araçlarına dayanır ve damgaları, dosyaları, zinciri ve alıntıyı `verify.py` olmadan kontrol eder.

### A — `verify.py` (önerilen)

1. Python 3.9 veya üstünü kurun (<https://www.python.org/downloads/>). Ek paket gerekmez.
2. Paketi bir klasöre açın (şifreliyse parolayla, örneğin 7-Zip ile).
3. Klasörde çalıştırın:
   - Windows: `py -3 -I -S verify.py` ya da `powershell -ExecutionPolicy Bypass -File .\verify.ps1`
   - Linux/macOS: `python3 -I -S verify.py` ya da `sh verify.sh`
4. Son satır `SONUÇ: DOĞRULANDI` ise bütün kontroller geçmiştir; `DOĞRULANDI — N uyarıyı okuyun`
   ise [UYARI] satırları ayrıca incelenmelidir (örneğin geç damga, eksik gün); `DOĞRULANAMADI` ise
   `[HATA]` satırları sorunun yerini gösterir. Çıkış kodu: `0` doğrulandı, `1` hata, `2` paket
   okunamadı (güvenli olmayan ya da çakışan dosya adları dahil).

`-I -S` Python'u yalıtır: bilgisayardaki ek paketler ve ortam değişkenleri kullanılmaz; sonuç
yalnızca `verify.py` dosyasındaki koda dayanır. `verify.py` ağa bağlanmaz ve dosya yazmaz.

Seçenekler: `--kokler KLASÖR` (yalnızca bu klasördeki köklere güven — örneğin Kamu SM'den kendi
indirdikleriniz; paketteki kökler kullanılmaz), `--hizli` (gzip dosyalarını açmadan yalnızca
özetler), `--json` (makine tarafından okunabilir çıktı). `verify.py`, paket olmadan **kasanın bir
kopyasını** (örneğin bir NAS'taki replika) da doğrulayabilir:
`python -I -S verify.py \\<NAS>\arcilogger-worm --kokler KLASÖR`. Klasörde `paket.json`, `verify.py` ya da `vault/` yoksa
"kasa kopyası" kipinde çalışır: mühürlü günler ve kayıtlar doğrulanır; gün klasörlerinin dışındaki
bilinmeyen dosyalar yalnızca uyarıdır. Bir **kanıt paketinde** (ZIP, `vault/` klasörü, `paket.json`
ya da `verify.py` içeren klasör) `paket.json`'ın olmaması, listede olmayan ya da paketin bilinen bir
parçası olmayan her dosya HATA'dır.

`verify.py` şunları kontrol eder:

| # | Kontrol |
|---|---|
| 0 | Dosya adları: Windows'ta değişmeden açılabilir ve büyük/küçük harf farkı gözetilmeden birbirinden farklı mı (`..`, mutlak yol, sonda nokta/boşluk, `CON` gibi aygıt adları, denetim karakterleri, `x.txt` / `X.TXT` çakışması → çıkış kodu 2) |
| 1 | `paket.json` var mı ve kanonik mi; listedeki her dosya var mı, boyutu ve SHA-256 özeti doğru mu; listede olmayan dosya var mı; kapsamdaki (`scope.days`) her gün pakette mi |
| 2 | Kök sertifikalar ve SHA-256 / SHA-1 parmak izleri (ekrana ve özete yazılır) |
| 3 | Her gün: manifest kanonik JSON mu (yinelenen anahtar yok); manifestteki her dosyanın boyutu ve SHA-256 özeti; açılmış içeriğin özeti ve satır sayısı |
| 4 | Zaman damgası: `TimeStampResp` ya da yalın `TimeStampToken`; durum; manifest özeti (messageImprint, en az SHA-256); `content-type` ve `message-digest` öznitelikleri; CMS imzası (RSA PKCS#1 v1.5, RSA-PSS, ECDSA P-256/P-384/P-521; SHA-256/384/512; ECDSA imzası yalnızca tek DER biçimiyle); ESSCertID / ESSCertIDv2 bağı; TSA sertifikasında kritik ve tek amaçlı `id-kp-timeStamping` ve (varsa) imzalamaya izin veren keyUsage; damga anında geçerli sertifikalarla köke kadar zincir (veren CA'larda keyCertSign ve pathLenConstraint); paketteki SİL'ler; gelecekteki tarih |
| 5 | Damganın zamanı damganın kendisinden hesaplanır: gün bitiminden 48 saatten sonra ise geç damga (manifest bunu söylemese bile); gün bitmeden önce ya da manifestin yazılma zamanından (`created_at`) çok farklı ise uyarı |
| 6 | Günler arası zincir (`previous`); önceki gün pakette yoksa "zincir bu pakette başlıyor"; pakette daha önceki bir gün varken aradaki gün eksikse ya da zincir yeniden başlıyorsa uyarı; silinmiş günler için zincir çapası ve imha tutanağı; manifestin `unsealed_empty_days` olarak listelediği boş günler atlanarak bağlantı, o dizinin öncesindeki güne |
| 7 | İmha tutanakları ve yenileme damgaları (varsa) |
| 7b | Resmi rapor (varsa, bölüm 4.1): `paket.json`'ın `report` / `report_token` alanlarında adı geçen dosyalar var mı; `rapor/` klasöründe başka dosya var mı; raporun SHA-256 özeti ARCILOGGER kayıtlarıyla karşılaştırılmak üzere yazılır; raporun damgası, imha tutanaklarının damgası gibi paketteki köklere karşı doğrulanır |
| 8 | `extract` satırlarının mühürlü dosyadaki aynı numaralı satırlarla bayt bayt aynılığı |
| 9 | Paketin içeriği: gün klasöründe olup manifestte olmayan dosya hiç mühürlenmemiştir (HATA, kasa kopyasında da); kanıt paketinde bilinen bir parça olmayan her dosya HATA |
| 10 | Manifestteki `legal_grade` ve damga politikası: test (mock) damgaları açıkça uyarılır; yasal damga diyen bir gün ARCILOGGER'ın test köküne ya da test politikasına bağlanıyorsa HATA; damgalar birden çok köke bağlanıyorsa uyarı |

### B — Kamu SM'nin resmî Zamane istemcisi

Kamu SM'nin "Ücretsiz Zaman Damgası İstemci Yazılımı" sayfasından konsol istemcisini
(`tss-client-console-3.1.33.jar`, Java 1.8 gerekir) indirin ve SHA-256 değerini sayfada yayımlanan
değerle karşılaştırın. Kamu SM'nin belgelediği kullanım (bkz. [kamusm.md](kamusm.md)):

```text
java -jar tss-client-console-3.1.33.jar -c  manifest-2026-10-12.json manifest-2026-10-12.json.zd
java -jar tss-client-console-3.1.33.jar -CC manifest-2026-10-12.json manifest-2026-10-12.json.zd
```

`-c` damgayı kontrol eder, `-CC` ayrıca sunucu sertifikasını kontrol eder. İstemcinin ekran
çıktısının biçimi belgelenmemiştir. Bu yöntem yalnızca Kamu SM damgalı günler için anlamlıdır ve
yalnızca damgayı kontrol eder (dosyalar, zincir ve alıntı için Yöntem C'nin C2–C4 adımları).
`DOGRULAMA.txt` her gün için tam komutları içerir.

### C — OpenSSL ve özet araçlarıyla, `verify.py` olmadan

`DOGRULAMA.txt` aşağıdaki adımların komutlarını bu paketin dosya adlarıyla verir:

1. **C1 — damga:** her gün için
   ```bash
   openssl ts -verify -in vault/2026/10/12/manifest-2026-10-12.json.zd \
       -data vault/2026/10/12/manifest-2026-10-12.json -CAfile sertifikalar/kokler.pem -attime <genTime>
   ```
   Beklenen çıktı `Verification: OK`. `-attime` sertifikaları damga anında kontrol ettirir (TSA
   sertifikasının süresi sonradan dolsa da çalışır). `.zd` dosyası yalın belirteçse `-token_in`
   eklenir. Bu adım manifestin damga anından beri değişmediğini gösterir.
2. **C2 — dosyalar:** `sha256sum vault/2026/10/12/*.gz` (PowerShell: `Get-FileHash -Algorithm SHA256
   vault\2026\10\12\*.gz`, cmd: `certutil -hashfile <dosya> SHA256`); her değer, aynı günün
   manifestindeki `files[].sha256` ya da `audit_log.sha256` ile aynı olmalı. Manifestte olup klasörde
   olmayan ya da klasörde olup manifestte olmayan dosya, paketin eksik ya da eklenmiş olduğunu gösterir
   — ARCILOGGER denetim kaydı (`audit_log`) hariç: varsayılan olarak pakete alınmaz ve `paket.json`
   `excluded` altında listelenir.
3. **C3 — zincir:** önceki günün `manifest-…json` ve `.zd` dosyalarının SHA-256 özetleri, sonraki
   günün manifestindeki `previous.manifest_sha256` ve `previous.token_sha256` ile aynı olmalı.
4. **C4 — alıntı:** `gzip -dc vault/2026/10/12/<kaynak>.log.gz | sed -n 'Np'` çıktısı, alıntı
   dosyasındaki `N<TAB>…` satırıyla bayt bayt aynı olmalı (Windows'ta Git Bash ya da WSL ile).

### Kök sertifikalar

Zaman damgasına duyulan güven, zincirin ulaştığı kök sertifikaya dayanır. Hiçbir yazılım paketteki
bir kökün gerçekten Kamu SM'ye ait olduğunu kendi başına kanıtlayamaz: `verify.py`'nin ve
`DOGRULAMA.txt`'nin gösterdiği **SHA-256 parmak izlerini**, Kamu SM'nin `sertifikalar.kamusm.gov.tr`
adresinde yayımladığı değerlerle karşılaştırın ya da kökleri oradan kendiniz indirip
`verify.py --kokler KLASÖR` ile verin.

## 6. Doğrulama neyi gösterir, neyi göstermez?

**Gösterir:** manifestin zaman damgasındaki anda var olduğunu ve o andan beri değişmediğini;
pakette bulunan her kayıt dosyasının mühürlendiği andan beri bayt bayt aynı olduğunu ve pakette
mühürlenmemiş bir kayıt dosyası bulunmadığını; pakette art arda bulunan günler arasında zincirin
kopmadığını (eksik günler uyarı olarak gösterilir); alıntı satırlarının mühürlü dosyadaki satırlarla
aynı olduğunu; her günün gün bitiminden ne kadar sonra damgalandığını.

**Göstermez:**
- kayıtları kimin ürettiğini ya da cihazların doğru kayıt ürettiğini (zaman damgası verinin kimin
  tarafından oluşturulduğunu kanıtlamaz); gönderen IP adresi doğrulanmaz (UDP syslog), bu yüzden
  bir satırın gerçekten o cihazdan geldiğini de göstermez (cihaz ayarları, DEVICE-SETUP 1.1);
- gün içinde, mühürlemeden önce bir değişiklik yapılmadığını (bunun için sunucunun işletim ve
  güvenlik kayıtları değerlendirilmelidir; geç damgalanan günler bu yüzden uyarı olarak gösterilir:
  bir gün sonradan değiştirilip yeniden mühürlenirse geriye kalan tek iz geç damga zamanıdır);
- satırlardaki alınma zamanlarının doğruluğunu (sunucu saatine dayanır; eşitleme bilgisi manifestin
  `clock` bölümündedir);
- bir IP adresinin belirli bir kişiye ait olduğunu (bu bir analizdir; raporda yöntemiyle verilir,
  bölüm 10);
- paketteki kök sertifikanın Kamu SM'ye ait olduğunu (parmak izi karşılaştırması gerekir);
- paketin içindeki `verify.py`'nin değiştirilmediğini (özetini ayrı bir kanaldan doğrulayın ya da
  Yöntem C'yi kullanın);
- sertifikaların iptal durumunu **çevrim içi** (SİL indirme, OCSP yapılmaz; paketteki SİL'ler
  kontrol edilir);
- kapsam dışı bırakılan kaynakların dosyalarını (pakette yoktur);
- test (mock) damgalı günlerin yasal geçerliliğini — bunların yasal geçerliliği **yoktur**.

## 7. Doğrulayıcı çıktısını yorumlamak

Tablo, `verify.py`'nin yazabileceği **bütün** kodları listeler (`--json` çıktısındaki `code`
alanı); ekrandaki her satır ayrıca Türkçe ve İngilizce açıklamasını taşır. Bir HATA paketi
"DOĞRULANAMADI" yapar; UYARI incelenmelidir; BİLGİ ve OK yalnızca bilgi verir.

| Kod | Düzey | Anlamı |
|---|---|---|
| (çıkış kodu 2) | — | Paket okunamadı: şifreli, bozuk ZIP, güvenli olmayan ya da (büyük/küçük harf farkıyla) çakışan dosya adları |
| `file-hash-mismatch`, `file-size-mismatch`, `file-content-mismatch` | HATA | Kayıt dosyası mühürlendikten sonra değiştirilmiş |
| `file-not-sealed` | HATA | Gün klasöründe manifestte olmayan, yani hiç mühürlenmemiş bir dosya var |
| `file-unexpected` | HATA (kasa kopyasında UYARI) | Paketin bilinen bir parçası olmayan dosya |
| `file-missing`, `index-file-missing`, `manifest-missing` | HATA | Dosya ya da (kapsamdaki) bir gün eksik |
| `index-missing`, `index-unlisted-file` | HATA (kasa kopyasında UYARI) | `paket.json` yok ya da bir dosyayı listelemiyor |
| `manifest-not-canonical`, `manifest-invalid` | HATA | Manifesti ARCILOGGER yazmamış (kanonik değil, yinelenen anahtar) ya da okunamıyor |
| `nothing-verified` | HATA | Pakette mühürlü gün yok |
| `token-weak-digest` | HATA | Damga SHA-1 gibi zayıf bir özetle alınmış |
| `token-key-usage`, `token-path-length` | HATA | Sertifikaların anahtar kullanımı ya da zincir uzunluğu sınırı damgaya izin vermiyor |
| `token-imprint` | HATA | Damga bu manifeste ait değil ya da manifest değiştirilmiş |
| `token-swapped` | HATA | Damga dosyaları günler arasında yer değiştirmiş |
| `token-signature`, `token-message-digest`, `token-content-type`, `token-ess` | HATA | Damganın imzası ya da imzalı öznitelikleri geçersiz |
| `token-eku` | HATA | TSA sertifikası zaman damgası için yetkili değil |
| `token-eku-not-critical` | UYARI | EKU uzantısı kritik değil; kurum bunu mühürlü manifestte kabul etmiş (`tsa.require_critical_eku = false`, K6). OpenSSL `ts -verify` böyle bir damgayı "unsuitable certificate purpose" diyerek reddeder |
| `chain-skip-invalid` | HATA | Manifestteki boş gün dizisi geçersiz ya da bu günden hemen önce bitmiyor |
| `summary-several-roots` | UYARI | Paketteki damgalar birden çok köke bağlanıyor; her kökün parmak izini ayrı karşılaştırın |
| `token-chain`, `token-not-ca` | HATA | Sertifika zinciri güvenilen bir köke ulaşmıyor |
| `token-cert-validity` | HATA | Bir sertifika damga anında geçerli değil |
| `crl-revoked` | HATA | Bir sertifika damgadan önce iptal edilmiş |
| `chain-broken`, `chain-not-previous-day` | HATA | Önceki gün sonradan değiştirilmiş ya da bağlantı yanlış |
| `index-hash-mismatch`, `index-not-canonical` | HATA | `paket.json` ya da listelediği bir dosya değiştirilmiş |
| `extract-mismatch`, `extract-unknown` | HATA | Alıntı satırı mühürlü dosyadakiyle aynı değil; alıntı klasöründe beklenmeyen dosya |
| `legal-claim-mismatch` | HATA | Manifest yasal damga diyor, damga test TSA'sından |
| `manifest-day-mismatch`, `manifest-misplaced` | HATA | Manifest başka bir güne ait ya da yanlış gün klasöründe |
| `file-unreadable`, `file-path-invalid` | HATA | Dosya okunamadı; manifestteki yol geçersiz (`..`, mutlak yol vb.) |
| `file-excluded-in-scope` | HATA | `paket.json` dosyayı kapsam dışı sayıyor ama kaynağı kapsamda: dosya eksik |
| `index-invalid`, `index-path-invalid`, `index-size-mismatch` | HATA | `paket.json` okunamıyor, geçersiz ya da yinelenen bir yol içeriyor, ya da bir dosyanın boyutu dizindekinden farklı |
| `root-invalid`, `root-none` | HATA | Bir kök sertifika okunamadı; güvenilecek hiç kök yok (damgalar doğrulanamaz) |
| `token-format`, `token-status` | HATA | Damga dosyası çözümlenemedi (bozuk ya da damga değil); damga sunucusu isteği reddetmiş |
| `token-signers`, `token-signed-attrs`, `token-no-cert` | HATA | Damgada tam bir imzacı yok, imzalı öznitelikler yok ya da TSA sertifikası damganın içinde değil (`certReq`) |
| `token-future` | HATA | Damganın zamanı gelecekte (saat hatası ya da sahte damga) |
| `token-index-mismatch` | HATA | `paket.json`'daki damga bilgisi (seri no, zaman) damga dosyasıyla uyuşmuyor |
| `algorithm`, `signature` | HATA | Desteklenmeyen özet/imza algoritması; bir imza (sertifika zincirinde ya da SİL'de) geçersiz |
| `anchor-invalid` | HATA | Zincir çapası okunamadı |
| `deletion-hash-mismatch`, `deletion-token-mismatch` | HATA | İmha tutanağı ya da damgası manifestteki özetle uyuşmuyor |
| `extract-format`, `extract-index-mismatch` | HATA | Alıntı dosyasının biçimi hatalı; satır numaraları `paket.json`'dakilerle aynı değil |
| `extract-no-source`, `extract-source-unverified` | HATA | Alıntının alındığı mühürlü dosya pakette yok ya da doğrulanamadı (alıntıya da güvenilemez) |
| `report-missing`, `report-token-missing` | HATA | `paket.json`'ın `report` / `report_token` alanında adı geçen rapor ya da damgası pakette yok (bölüm 4.1) |
| `report-unlisted`, `report-token-unlisted` | HATA | `rapor/` klasöründeki dosya, `paket.json`'ın raporu ya da rapor damgası olarak adlandırdığı dosya değil |
| `report-token-orphan` | HATA | Rapor damgası var ama ait olduğu rapor pakette yok |
| `legal-test-stamp` | UYARI | Gün test (mock) damgasıyla mühürlenmiş; yasal geçerliliği yok |
| `stamped-late` | UYARI | Gün, bitiminden 48 saatten sonra damgalanmış (manifest de bunu söylüyor) |
| `stamped-late-computed` | UYARI | Gün geç damgalanmış ama manifest bunu söylemiyor: gün sonradan yeniden mühürlenmiş olabilir; sunucu kayıtlarıyla karşılaştırın |
| `stamped-before-day-end`, `stamp-time-differs` | UYARI | Damga zamanı gün bitiminden önce ya da manifestin yazılma zamanından çok farklı (saat hatası ya da sonradan damgalama) |
| `chain-gap`, `chain-restart` | UYARI | Pakette daha önceki bir gün varken aradaki gün(ler) eksik ya da zincir yeniden başlıyor |
| `crl-revoked-later`, `crl-hold` | UYARI | Sertifika damgadan sonra iptal edilmiş ya da askıya alınmış; incelenmeli |
| `crl-invalid` | UYARI | Paketteki bir SİL okunamadı |
| `index-schema` | UYARI | `paket.json` bilinmeyen bir biçim sürümü bildiriyor (daha yeni bir ARCILOGGER) |
| `summary-test-days` | UYARI | Özet: yasal geçerliliği olmayan test damgalı günler var |
| `report-token-omitted` | UYARI | Raporun zaman damgası pakete konmamış; nedeni `paket.json`'daki `report_note` alanında (bölüm 4.1) |
| `chain-start` | BİLGİ | Önceki gün pakette yok; zincir bu pakette başlıyor |
| `crl-none`, `crl-stale` | BİLGİ | Paketteki SİL'ler bu sertifikayı ya da damga anını kapsamıyor; iptal durumu bilinmiyor |
| `vault-copy` | BİLGİ | Paket dizini yok: kasa kopyası olarak doğrulandı |
| `os-metadata` | BİLGİ | İşletim sisteminin eklediği dosyalar (`.DS_Store`, `._*`, `Thumbs.db`, `desktop.ini`) okunmadan yok sayıldı |
| `file-excluded` | BİLGİ | Kaynak kapsam dışı bırakılmış; dosya pakette yok, doğrulanmadı |
| `audit-log-excluded` | BİLGİ | ARCILOGGER denetim kaydı pakete alınmamış (varsayılan, KVKK); hata değil |
| `file-torn-last-line` | BİLGİ | Dosyanın son satırı yarım (çökme ya da dolu disk); olduğu gibi mühürlenmiş |
| `chain-skipped-days` | BİLGİ | Önceki günlerde hiç log yoktu; tek tek damgalanmadılar, bu manifest onları listeliyor |
| `gaps` | BİLGİ | Manifestte kayıt gelmeyen süreler var |
| `chain-first` | BİLGİ | Bu gün ARCILOGGER zincirinin ilk günü (önceki gün bağlantısı yok) |
| `audit-log-absent` | BİLGİ | Günün ARCILOGGER denetim kaydı pakette yok ve `paket.json` okunamadığı için kapsam bilinmiyor; denetim kaydı varsayılan olarak pakete alınmadığından tek başına hata sayılmaz |
| `crl-absent`, `crl-loaded` | BİLGİ | Pakette SİL yok (iptal durumu kontrol edilmez); yüklenen SİL sayısı |
| `deletion-absent` | BİLGİ | Manifestte adı geçen imha tutanağı pakette yok |
| `index-hash`, `index-scope` | BİLGİ | `paket.json`'ın SHA-256 özeti (denetim kaydıyla karşılaştırın); paket no, oluşturan, zaman, günler, kaynaklar, sorgu |
| `manifest`, `root`, `root-trust`, `roots-external` | BİLGİ | Manifest dosyası ve özeti; kök sertifika ve parmak izleri; parmak izlerini Kamu SM'nin yayımladığı değerlerle karşılaştırma hatırlatması; yalnızca `--kokler` ile verilen köklere güveniliyor |
| `renewal-covers` | BİLGİ | Yenileme damgasının kapsadığı günler |
| `report-hash` | BİLGİ | Raporun SHA-256 özeti: ARCILOGGER'daki sorgu kaydı ve denetim kaydıyla (`lookup.report`) aynı olmalı |
| `report-not-stamped` | BİLGİ | Rapora ayrıca zaman damgası alınmamış (isteğe bağlı ayar) |
| `summary-days`, `summary-root` | BİLGİ | Özet: incelenen günler; damgaların bağlandığı kök (Kamu SM'nin yayımladığı parmak iziyle karşılaştırın) |
| `file-ok`, `token-ok`, `chain-ok`, `chain-anchor`, `contents-ok`, `crl-ok`, `extract-ok`, `index-files`, `deletion-ok`, `renewal-ok`, `report-ok` | OK | İlgili kontrol geçti (dosya, damga, zincir bağlantısı, imha edilmiş önceki gün için zincir çapası, paket içeriği, SİL, alıntı, paket dizini, imha tutanağı, yenileme ve rapor damgası) |

## 8. Sahada netleşecek konular

- Kamu SM `.zd` dosyasının biçimi (`TimeStampResp` ya da yalın belirteç), `certReq`'in karşılanıp
  karşılanmadığı, EKU'nun kritik olup olmadığı ve imza algoritmaları test sunucusunda
  doğrulanacak ([kamusm.md](kamusm.md) K1, K5, K6). `verify.py` ikisini de kabul eder. EKU kritik
  değilse kurum `tsa.require_critical_eku = false` ayarını kullanabilir; bu seçim her manifeste
  mühürlenir ve `verify.py` o günler için hata yerine uyarı verir.
- Kamu SM kök sertifikalarının ve SİL'lerin kesin adresleri (K10); SİL indirme henüz yok.
- Kamu SM sertifika profili (K6): TSA sertifikasında keyUsage (digitalSignature / nonRepudiation),
  ara ve kök sertifikalarda keyCertSign ve pathLenConstraint; tamsayıların en kısa DER biçimiyle
  kodlanması. `verify.py` bunları zorunlu tutar; gerçek Kamu SM damgalarıyla denenmeli.
- `verify.ps1`'in denetçi bilgisayarlarında (Windows PowerShell 5.1, yürütme ilkesi) denenmesi.
- `verify.py` özetinin alıcıya paketten ayrı iletileceği kanal (teslim tutanağı, indirme sayfası,
  sürüm notları) ve `verify.py`'nin lisansı: işletme sahibinin kararı.

## 9. Arama, Canlı Akış ve Kaynaklar denetim kayıtları

Arama, Canlı Akış ve Kaynaklar sayfaları kişisel veri gösterir (istemci adresleri, MAC adresleri,
alan adları, kullanıcı adları). Bu yüzden Arama ve Canlı Akış, Kaynaklar'da da bir kaynağın "Son
satırlar" sekmesi ile ayrıştırma hatalarının ayrıntısı KVKK bilgilendirme metni onaylanmadan
açılmaz (Kaynaklar listesi ve bir kaynağın sayıları metin beklemeden açılır). Aşağıdaki tablodaki
işlemler, yanıt gönderilmeden önce ARCILOGGER denetim kaydına (`audit_log`, web arayüzünde
Denetim Kaydı sayfası) yazılır; tablonun altındaki okumalar yazılmaz. Ayrıntılarda (`details`) girdiler, sayılar, satır
aralıkları ve özetler bulunur; bulunan satırların içeriği yazılmaz. Aramanın sorgusu (`q`)
normalleştirilmiş hâliyle yazılır: 5651 sorgusunda olduğu gibi bir IP, MAC adresi ya da metin
içerebilir.

| Eylem | Ne zaman yazılır | `details` |
|---|---|---|
| `search.run` | Her arama (zaman aşımına uğrayan da: `outcome: "timeout"`, `search_id: null`) | `search_id`, `q`, `from`, `to`, `kinds`, `driver`, `tables`, `rows` (ilk sayfadaki satır sayısı), `complete`, `outcome`, `elapsed_ms`, `saved_id` |
| `search.event` | Bir kaydın bütün alanları açıldığında | `table`, `id`, `time`, `raw` (kaynak, gün, satır, bayt konumu) |
| `search.refs` | "Kanıt paketine aktar" | `search_id`, `q`, `from`, `to`, `refs` (satır sayısı), `truncated`, `complete`, `sha256` (referans listesinin kanonik JSON'unun özeti) |
| `search.export` | CSV ya da Excel indirme (yalnızca yönetici, parolayı yeniden girerek) | `search_id`, `q`, `from`, `to`, `format`, `rows`, `truncated`, `bytes`, `sha256` (dosyanın) |
| `search.saved_create`, `search.saved_update` | Kayıtlı arama eklenince ya da değişince | `saved_id`, `name`, `q` |
| `search.saved_delete` | Kayıtlı arama silinince | `saved_id`, `name` |
| `live.open` | Canlı akış açılınca | `stream_id`, `q`, `folders` (izlenen klasörler), `backfill` |
| `live.close` | Akış kapanınca (`reason`: kullanıcı kapattı `closed`, duraklatılmış akışın süresi doldu `lingered`, sunucu durdu `server_stopping`, akışı bitiren durumun kodu, ör. `time_limit`, `session_idle`) | `stream_id`, `seconds`, `delivered` (gönderilen satır), `dropped` (nedenlerine göre gösterilemeyen satırlar), `reason`, `ranges` (klasör, gün, ilk ve son satır numarası) |
| `raw.tail` | Kaynaklar → bir kaynağın "Son satırlar" sekmesi | `source`, `day`, `origin` (`spool`), `from_line`, `to_line`, `from_offset`, `to_offset`, `count`, `range_sha256` |

Yazılmayanlar: bir aramanın sonraki sayfaları (`POST /search/page`) ve istatistikleri
(`POST /search/stats`) — ikisi de yalnızca kaydı yazılmış bir `search_id` ile çalışır —, sorgu
çözümleme (`POST /search/parse`), alan listesi (`GET /search/fields`), canlı akışın durumu
(`GET /live/status`), Kaynaklar listesi ve bir kaynağın ayrıntısı (sayılar; bir hata kalıbının
örnek değeri yalnızca `search.read` ya da `lookup.run` yetkisi ve onaylanmış KVKK metniyle
gösterilir). Bir satırın tamamı üç sayfada da `GET /raw` ile açılır ve `raw.view` olarak yazılır.

**Bir `raw.tail` girdisini yeniden hesaplamak.** `range_sha256`, kaynağın o günkü dosyasının
`[from_offset, to_offset)` bayt aralığının (gösterilen satırlar, satır sonlarıyla birlikte) SHA-256
özetidir. Gün mühürlenmeden önce bu baytlar `D:\ARCILOGGER\spool\<kaynak>\<gün>.log`
dosyasında, mühürlendikten sonra kasadaki `vault\YYYY\MM\DD\<kaynak>.log.gz` dosyasının açılmış
içeriğinde ve o günün o kaynağını içeren her kanıt paketindedir (dosyalar mühürlenirken
değiştirilmez). Python ile (örnekte `from_offset` 0, `to_offset` 498):

```powershell
python -I -c "import gzip,hashlib,sys; d=gzip.open(sys.argv[1]).read(); print(hashlib.sha256(d[int(sys.argv[2]):int(sys.argv[3])]).hexdigest())" core-r1.log.gz 0 498
```

Çıkan değer girdideki `range_sha256` ile aynı olmalıdır; satır sayısı `to_line - from_line + 1`,
yani `count` olmalıdır. Spool dosyası için `gzip.open` yerine `open(sys.argv[1], 'rb')` kullanın.

**Dışa aktarma ve aramadan kanıt paketi.** `search.export` girdisindeki `sha256`, indirilen
dosyanın özetidir (`Get-FileHash -Algorithm SHA256 <dosya>` aynı değeri verir; yanıt da
`Repr-Digest` başlığıyla gönderilir). Dışa aktarılan dosya mühürlü değildir; dosyanın "Bilgi"
sayfası da bunu söyler: bütünlük kanıtı kanıt paketidir. Arama'dan kanıt paketi oluşturulduğunda
denetim kaydında sırayla `search.refs` (satır sayısı ve listenin özeti), ardından aynı kullanıcının
`evidence.request` ve `evidence.create` girdileri görünür; paket her paket gibi bölüm 5'e göre
doğrulanır.

## 10. 5651 raporunu okumak (okuma tablosu)

Bir 5651 sorgusunun resmi raporu (PDF; kanıt paketinde `rapor/` altında, bölüm 4.1) bir andaki
IP adresinden oturuma, cihaza ve kişiye giden zinciri, dayandığı ham satırlarla verir. Raporun
bölümleri ve metinleri [RAPOR.md](RAPOR.md) belgesindedir. Denetçinin bilmesi gerekenler:

- **Kira kanıtı:** iç IP adresinden MAC adresine ve cihaza giden adım DHCP sunucusunun (MikroTik
  ya da FortiGate) kira kayıtlarına, MikroTik `ARP-LOG` kayıtlarına (orta güven) ya da — kira
  kaydı yoksa ve yalnızca ayarlarda listelenen arayüzlerde — FortiGate oturum kaydının srcmac
  alanına dayanır. srcmac **kısmi kanıttır**: MAC adresinin oturumu gönderdiğini kanıtlamaz,
  güven düzeyi düşüktür. FortiGate DHCP kayıtlarının biçimi sahada doğrulanana kadar rapor bunu
  belirtir.
- **Yöntem notları sorgu anından gelir:** yöntem bölümündeki notlar ve çıkış satırları sorgu
  yapıldığında saklanan sonuçtan yazılır, raporun indirildiği günün ayarlarından değil; çıkış
  (internet hattı) tanımlarının önceki hâlleri kurumun denetim kaydındadır (`settings.change`).
- **Ham satırlar değiştirilmeden basılır:** FortiGate'in aygıt tanıması açık olan bir arayüzün
  oturum satırları kaynak MAC adresini (`srcmac`), cihaz adını, işletim sistemini, donanım
  üreticisini ve sürümünü, cihaz türünü ve FortiGate'in pasif olarak öğrendiği kullanıcı adlarını
  taşır. Bu alanlar raporun alıntıladığı her oturum satırında, çift NAT raporunda başka kişilerin
  aday oturumları dahil, görünür; aynı satırlar kanıt paketinin `extract/` klasöründe ve mühürlü
  dosyalarında da vardır ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.5). ARCILOGGER sorguda
  bunlardan yalnızca srcmac'i, yalnızca listelenen arayüzlerde ve kısmi kanıt olarak kullanır;
  öteki alanlar sorguda kullanılmaz.

**Okuma tablosu:** bu tablo, PDF'te görülen her çıkış etiketi ve yöntem bölümünde ya da bir
zincirin yanında yazılan her açıklama için üç şeyi ayırır: kayıtta kesin olan, kurumun ayarlarına
dayanan ve varsayılan. İlk sütun PDF'in kendi etiketlerini ve arkalarındaki açıklama anahtarlarını
(`…`) kullanır. Her hücre önce Türkçe, eğik çizgiden sonra İngilizce yazılır. Aynı tablo
[RAPOR.md](RAPOR.md) belgesinde de vardır.

| PDF'te görülen / What the PDF shows | Kesin olan (kayıtta) / Exact (in the records) | Ayarlara dayanan / Configured | Varsayılan / Assumed |
|---|---|---|---|
| Çıkış "Genel IP", doğrudan, kesin eşleşme / exit "Public IP", direct, exact | FortiGate oturum kaydı: transip ve transport / the FortiGate session record: transip and transport | genel IP'nin bu hatta ait olduğu / that the public IP belongs to this line | — |
| Çıkış "Genel IP" ya da "NAT adresi", servis sağlayıcı NAT'ı / ISP NAT | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın ikinci NAT yaptığı, genel adresi / that the line does a second NAT, its public address | genel port → FortiGate portu eşlemesi yoktur; liste adaydır / no public port → FortiGate port mapping; the list is candidates |
| "Genel adresi değişebilir" uyarısı / "public address may change" (`egress_public_dynamic`) | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın genel adresi ve değişebilir işaretlendiği / the line's public address and that it is marked as changing | adresin o anda bu hatta ait olduğu; yalnızca servis sağlayıcı doğrulayabilir / that the address was this line's at that moment; only the ISP can confirm it |
| "Doğrudan hat doğrulanamadı" (fallback) | o anın çevresinde istenen adresin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü ve o hatta özel ya da paylaşılan başka bir NAT adresinin de göründüğü / that around the moment the requested address did not appear in the records, or only part of the time, and another, private or shared NAT address did on that line | hattın tanımı / the line's definition | o anda ikinci NAT yapılmış olabileceği; liste başka bir hattın kullanıcılarını içerebilir (paragraf söyler) / that a second NAT may have happened; the list may hold another line's users (the paragraph says so) |
| "NAT adresi doğrulanamadı" (fallback of an ISP NAT address) | o anın çevresinde istenen NAT adresinin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü / that around the moment the requested NAT address did not appear in the records, or only part of the time | hattın tanımı ve NAT adresi / the line's definition and NAT address | listenin bu hattın o anki oturumlarını içerdiği; başka adreslerle çıkan oturumlar da aday olabilir / that the list holds this line's sessions of that moment; sessions that left with other addresses may be candidates too |
| "Kayıtlarda görülen adres" (observed) | FortiGate'in o adresi kaynak NAT adresi olarak kullandığı / that the FortiGate used the address as source NAT | — | adresin o tarihte kuruma ait olduğu (servis sağlayıcıdan doğrulanır) / that the address belonged to the organisation then (confirmed with the ISP) |
| "Genel adresi bilinmeyen hatlar", "Çıkış tanımlı değil" | özel ya da paylaşılan NAT adresiyle çıkan FortiGate oturumları / the FortiGate sessions that left with a private or shared NAT address | — | ikinci NAT; liste ilgisiz kullanıcılar içerebilir / a second NAT; the list may hold unrelated users |
| "Kısmi kanıt: srcmac" (`srcmac_only`) | FortiGate'in aygıt tanımasının kaynak IP'yi bu MAC ile ilişkilendirdiği / that the FortiGate's device identification associated the source IP with this MAC | arayüzün listelendiği / that the interface is listed | MAC'in oturumu gönderdiği kanıtlanmaz; güven düşük / not proof that the MAC sent the session; low confidence |
| Düşük güvenli srcmac çakışması / low srcmac conflict (`srcmac_mismatch`) | iki farklı MAC kaydı / two different MAC records | — | o anda iki olası cihaz vardır; ikisi de gösterilir / there are two possible devices; both are shown |
| "Kayıt biçimi sahada doğrulanmadı" / "verified by … on …" (`fortigate_format_unverified`, `fortigate_format_verified`) | FortiGate DHCP kaydı / the FortiGate DHCP record | biçimin doğru okunduğu (doğrulandıysa kim, ne zaman; sorgu anındaki durum) / that the format is read correctly (if verified, by whom and when; as at the time of the lookup) | doğrulanmadıysa biçim FortiOS belgelerine dayanır / if not verified, the format rests on the FortiOS documents |
| "Kira zaman çizelgesine girmedi; zincir kurulmadı" (`fortigate_dhcp_not_converted`) | ham FortiGate DHCP kaydı ve mührü (iç IP sorgusu ve kesin eşleşme) / the raw FortiGate DHCP line and its seal (internal lookup and exact match) | — | zincir kurulmadı ve güven düzeyi verilmez; kayıt yalnızca o anı kapsayan bir kaydın var olduğunu gösterir, bir eşleştirme değildir / no chain was built and no confidence is given; the line only shows that a record covering the moment exists, it is not an attribution |
| "Kira yenilemesi görülmedi" (güven düşük) / no renewal seen (low) (`lease_renewal_not_seen`) | FortiGate DHCP Ack kaydı ve zamanı (kiranın istenen andan önceki kendi son kaydı) / the FortiGate DHCP Ack and its time (the lease's own last record before the moment) | kira süresi (kayıttan ya da ayardan) / the lease time (logged or configured) | Ack sahibinin adresi istenen anda hâlâ kullandığı; kayıtlar bunu gösteremez, güven bu yüzden düşük / that the Ack's holder still used the address at the requested moment; the records cannot show it, hence low confidence |
| "Kiradan sonra uygulanmamış DHCP kaydı" (güven düşük) / DHCP record after the lease not applied to it (low) (`fortigate_dhcp_not_converted_since_lease`) | DHCP kira kaydı (kiranın istenen andan önceki kendi son kaydı) ve ondan sonra o adres ya da kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı bir FortiGate DHCP kaydının var olduğu (kayıt gösterilmez) / the DHCP lease record (the lease's own last record before the moment) and that a FortiGate DHCP record of that address or of the lease's MAC address after it was not applied to this lease by the lease timeline (the record is not shown) | kira süresi / the lease time | kira sahibinin adresi istenen anda hâlâ kullandığı; sonraki kayıt bir bırakma, başka bir cihaza verilme ya da MAC adresinin başka bir adres alması olabilir, güven bu yüzden düşük / that the holder still used the address at the moment; the later record may be a release, a hand-over to another device or the MAC taking another address, hence low confidence |
| "Tanımlı bir ağda değil" (güven düşük) / not in a configured network (low) (`fortigate_dhcp_no_network`) | FortiGate DHCP kira kaydı / the FortiGate DHCP lease record | kurumun tanımlı ağları (adres hiçbirinde değil) / the organisation's configured networks (the address is in none) | kira sahibinin adresi o anda hâlâ kullandığı; adresin reddedilip başka bir istemciye geçtiği yalnızca tanımlı ağlarda anlaşılır, güven bu yüzden düşük / that the holder still used the address then; a declined address is only detected in configured networks, hence low confidence |
| "Aynı ağda iki DHCP kaynağı" (güven düşük) / two DHCP sources on one network (low) (`dhcp_sources_overlap`) | o ağda o anın çevresinde iki DHCP kaynağının kayıtları / the records of two DHCP sources in that network around the moment | ağın (VLAN alt ağının) tanımı (tanımlı bir ağda olmayan adreste adresin kendisi) / the network's (VLAN subnet's) definition (for an address in no configured network, the address itself) | zincirin dayandığı kaydı yazan sunucunun o anda adresi gerçekten verdiği; kayıtlar iki sunucudan hangisinin geçerli olduğunu gösteremez / that the server whose record the chain rests on really gave the address then; the records cannot show which of the two applied |
| "Gönderici sonradan yapılandırıldı" / sender configured later (`source_configured_later`) | kayıtların o göndericiden geldiği / that the records came from that sender | göndericinin adresinin sonradan bir FortiGate kaynağı olarak tanımlandığı / that the sender's address was configured as a FortiGate source later | kayıtların alındıkları sırada da kurumun FortiGate'inden geldiği / that the records came from the organisation's FortiGate when they arrived as well |
| "Kaynak saati kayık" (güven düşük) / source clock off (low) (`source_clock_offset`) | kayıtlardaki zaman ile alma zamanı arasındaki ölçülen fark / the offset measured between the record times and the receive times | sınır: 120 saniye; yalnızca oturum araması için ölçülen bir kaynakta sorgu toleransı daha büyükse tolerans / the limit: 120 seconds; the lookup tolerance when larger, for a source measured only for the session search | doğru oturumun kaymış zaman aralığında olduğu; bulunan oturum başka biri olabilir / that the right session lies in the shifted window; the session found may be someone else's |
| "Oturum aranmadı" (Çıkış yok / no exit) | — | sorgu anındaki çıkış listesi (PDF'te yazılı) ve hatların türü / the exit list at the time of the lookup (printed in the PDF) and the lines' types | çıkış listesinin olay tarihinde de tam olduğu / that the exit list was complete on the date of the event too |
| "Oturum aranmadı" (FortiGate'ten geçmeyen hat / line not passing the FortiGate) | — | hattın FortiGate'ten geçmediği / that the line does not pass the FortiGate | hattın olay tarihinde de FortiGate'ten geçmediği / that it did not pass it on the date of the event either |
| "Oturum aranmadı" (internet çıkışı olmayan NAT adresi / not an internet exit) (`egress_internal`) | — | adresin FortiGate'in iç ağlara yaptığı NAT'ın adresi olarak tanımlandığı / that the address is defined as the FortiGate's NAT towards internal networks | adresin olay tarihinde de bir internet çıkışı olmadığı / that it was not an internet exit on the date of the event either |
| "Tanımsız gönderici" uyarılı eşleşme / match with the unconfigured-sender warning (`session_from_unknown_source`) | yapılandırılmamış bir göndericinin oturum kaydı / a session record of an unconfigured sender | — | kaydın kurumun FortiGate'inden geldiği doğrulanmadı; kanıt olarak kullanılmamalıdır / not confirmed that the record came from the organisation's FortiGate; not to be used as evidence |
| "Çıkış tanımı olaydan sonra değişti" / exit changed after the event (`egress_changed_after_event`) | — | bugünkü tanım (PDF'te yazılı); önceki tanımlar kurumun denetim kaydında / today's definition (printed in the PDF); earlier definitions in the organisation's audit log | o tarihteki tanımın aynı olduğu / that the definition was the same on that date |

---

<a id="english"></a>

## English

**How the evidence chain works.** The collector writes every received syslog message unchanged as
one line (`<receive time> <sender IP> <message>`, control bytes and `#` reversibly escaped as `#ooo`)
into the day's spool file. After the day closes (Europe/Istanbul, by receive time) each file is
compressed deterministically into the vault, hashed (SHA-256 of the compressed and uncompressed
bytes), and listed in the daily **manifest** (canonical JSON, RFC 8785) together with the audit log,
gaps, clock data and the **hashes of the previous day's manifest and token** (the chain). Only the
manifest's SHA-256 is sent to TÜBİTAK BİLGEM Kamu SM, which returns an RFC 3161 time-stamp token
(`.zd`). ARCILOGGER verifies the token and all hashes before it marks a day sealed, makes the files
read-only and copies them to the WORM share of the replica (e.g. on a NAS).

**Retention.** Spool: until the day is sealed. Vault: 760 days by default, never below 730; the
retention job is off by default. Vault days are deleted only by the retention job, with a
time-stamped deletion record and a chain anchor. Evidence packages in `exports/`: 30 days. Stored
official 5651 reports (`exports\reports\`) and the 5651 lookups: no expiry yet. ARCILOGGER's audit
log: no expiry in the database; its daily file is sealed with the day. Parsed records in the
database: derived and rebuildable from the vault; intended 400 days, but no deletion job exists yet
for them. Usage rollups in the database (per person, device and department with names: sites,
URLs, blocked attempts, hourly rows; `usage_*` tables, `domain_categories`): 400 days
(`usage.retention_days`), deleted every night after 03:30 by the worker job `usage.retention`,
finished rebuild requests (`usage_rebuilds`) after 90 days; no deletion record is kept, the job's
run (`job_runs`) holds the deleted row counts. Usage report files (`exports\usage-reports\`, with
names): 400 days (`reports.keep_days`); copies sent by e-mail stay with the recipients. Saved
searches (`saved_searches`, visible to their owner only): until the owner deletes them or the user
is deleted. Search exports and live tail lines are not kept as files on the server (the export is
built in memory and downloaded; its size and SHA-256 are in the audit log). Drafts for the lawyer:
`docs/legal/`.

**Evidence package.** `arcilogger-core.exe evidence build --from D --to D [--source S] [--extract F]
[--password] [--pdf F] [--include-audit-log]` (on the server, by its full path) creates a ZIP with
the sealed files (whole files of the chosen sources), manifests, tokens, deletion/renewal records
when relevant, the roots the tokens chain to (only those) and the TSA certificates, CRLs when a
CRL folder exists, an optional byte-exact extract of raw lines, the standalone verifier, the
guides (`DOGRULAMA.txt`, `VERIFY-EN.txt`) and `paket.json` (canonical index with size and SHA-256
of every file). Optional AES-256 encryption with a random password shown once and never stored.
Building twice from the same inputs gives the same bytes except the creation time. The vault is
only read; a day whose token or hashes do not verify, or a broken chain link between included
days, is refused, and the finished ZIP is checked with the standalone verifier's own code before
it is handed out. The build also prints the SHA-256 of `verify.py`: give it to the recipient
through a channel separate from the package. ARCILOGGER's own audit log (`audit.jsonl.gz`) is left
out unless asked for (it holds other requests and staff data); `paket.json` lists it under
`excluded` with its sealed SHA-256 and `verify.py` reports `audit-log-excluded` (information, not a
failure; `audit-log-absent` when the package has no readable `paket.json`). A torn last line
(crash, full disk) is sealed as it is and reported as `file-torn-last-line`; a run of days without
any log may be listed in the next manifest (`unsealed_empty_days`) instead of being stamped day by
day, and the chain links past it (`chain-skipped-days`; an invalid run is `chain-skip-invalid`).
Stamps chaining to more than one root give `summary-several-roots`; a TSA certificate whose
extended key usage is not critical gives `token-eku-not-critical` (a warning) only where the site
accepted that profile (`tsa.require_critical_eku = false`, K6), otherwise `token-eku`. A day is
shown as legal grade only when its manifest claims the Kamu SM production server and the verified
chain reaches a root listed in `tsa.legal_root_fingerprints`.

**Packages from the web interface and the 5651 report (section 4.1).** Administrators create
packages in the web interface (re-authentication); auditors download those they may see. Web
packages are audited (`evidence.request`, `evidence.create` with the SHA-256 of the ZIP,
`paket.json` and `verify.py`, every `evidence.download`, `evidence.purge`); the command-line
`evidence build` is not. A package built from a 5651 lookup contains, by default (`content:
"full_days"`), the whole sealed files of **all** sources of the cited days (not only the cited
sources) plus the cited lines under `extract/`; with `content: "extract"` only the sources of the
cited lines. It carries the lookup's stored official
report `rapor/ARCILOGGER-5651-rapor-<id>.pdf` — the same bytes, so its SHA-256 (`report-hash`)
equals `lookup_reports.sha256`, the `X-Arci-Sha256` header and the audit entries `lookup.report`,
`evidence.request` and `evidence.create` — and its `.pdf.zd` only when that token verified when
stored and still verifies against the package's roots (otherwise `report_note` in `paket.json`
and the warning `report-token-omitted`). `paket.json` names them in `report` / `report_token`;
any other file under `rapor/` is an error. Check the token with `openssl ts -verify -in
rapor/<x>.pdf.zd -data rapor/<x>.pdf -CAfile sertifikalar/kokler.pem`. ARCILOGGER's own audit
log is not in a package by default (data minimisation). Section 7 lists every code the verifier
prints; section 10 explains how to read the report (below). The licence never changes evidence ([LICENSING.md](LICENSING.md) section 12): no licence
mark in raw logs, sealed days, manifests, time stamps, packages, the verifier or the 5651 report,
and lookups, reports and packages work in every licence state; the audit log's `license.*`
entries are licence actions (trial start, installation, removal, refusal, limits, state changes)
without the licensee name or server ids.

**Independent verification.**
- A) `python -I -S verify.py` in the extracted folder (Python 3.9+, standard library only, no
  network, writes nothing). Exit code 0 = verified (read the warnings), 1 = a check failed, 2 = the
  package cannot be read (including unsafe or case-colliding file names). `--kokler DIR` trusts
  only roots you bring. The verify.py inside a package cannot vouch for itself: trust its result
  only if its SHA-256 matches a value received separately, or run your own copy against the package;
  otherwise use C.
- B) Kamu SM's official console client: `java -jar tss-client-console-3.1.33.jar -c|-CC <file>
  <file>.zd` (usage as documented by Kamu SM; see [kamusm.md](kamusm.md)); it checks the stamps only.
- C) Without verify.py: C1 `openssl ts -verify -in <manifest>.zd -data <manifest> -CAfile
  sertifikalar/kokler.pem -attime <genTime>`; C2 the SHA-256 of every file of a day folder equals
  its value in the manifest; C3 the SHA-256 of the previous day's manifest and `.zd` equal
  `previous` in the next manifest; C4 line N of an extract equals line N of the decompressed sealed
  file. `DOGRULAMA.txt` lists the exact commands.
- Compare the root fingerprints with the values Kamu SM publishes at `sertifikalar.kamusm.gov.tr`;
  no software can prove on its own that a root in the package is Kamu SM's.

**What verification shows and what it does not.** It shows that each manifest existed at its stamp
time and is unchanged, that the packaged log files match their sealed hashes and that no unsealed
log file is in the package, that consecutive days are chained (missing days are warnings), that
the extract equals the sealed lines, and how long after the end of the day each day was stamped
(more than 48 hours is a warning, whatever the manifest says: re-sealing a rewritten day leaves only
this trace). It does not show who produced the logs, that nothing changed before sealing, that
receive times were correct, that an IP belonged to a person (an analysis, given with its method
in the report; section 10), that the verify.py inside the package is genuine, or the online revocation status; test (mock) stamps have no legal validity.

**Search, live tail and sources in the audit log (section 9).** Arama, Canlı Akış and Kaynaklar
show personal data, so Arama, Canlı Akış and, in Kaynaklar, a source's "Son satırlar" tab and the
detail of its parse errors wait for the KVKK notice (the Kaynaklar list and a source's counts open
without it). The calls listed here are written to ARCILOGGER's audit log (`audit_log`, the
Denetim Kaydı page) before the answer is sent; the reads named as not audited are not. The
details hold inputs, counts, line ranges and hashes, never the content of the lines found; the
search query (`q`) is stored normalised and can hold an IP, a MAC address or text, as a 5651 lookup
does. `search.run` (every run, a timed-out one too with `outcome: "timeout"`): `search_id`, `q`,
`from`, `to`, `kinds`, `driver`, `tables`, `rows`, `complete`, `outcome`, `elapsed_ms`,
`saved_id`; `search.event` (a record's fields opened): `table`, `id`, `time`, `raw`; `search.refs`
("Kanıt paketine aktar"): `search_id`, `q`, `from`, `to`, `refs`, `truncated`, `complete`,
`sha256` of the reference list as canonical JSON; `search.export` (administrators, with
re-authentication): `search_id`, `q`, `from`, `to`, `format`, `rows`, `truncated`, `bytes`,
`sha256` of the file; `search.saved_create` and `search.saved_update`: `saved_id`, `name`, `q`;
`search.saved_delete`: `saved_id`, `name`; `live.open`: `stream_id`, `q`, `folders`, `backfill`;
`live.close`: `stream_id`, `seconds`, `delivered`, `dropped` by reason, `reason` (`closed`,
`lingered`, `server_stopping` or the code that ended the stream, e.g. `time_limit`,
`session_idle`), `ranges` (folder, day, first and last line); `raw.tail` (Kaynaklar, "Son
satırlar"): `source`, `day`, `origin`, `from_line`, `to_line`, `from_offset`, `to_offset`,
`count`, `range_sha256`. Not audited: further pages and the statistics of an audited search, the
parser, the field list, the live tail's status, the sources list and detail (counts). A whole line
opens through `GET /raw` on all three pages and is audited as `raw.view`.

`range_sha256` is the SHA-256 of the bytes `[from_offset, to_offset)` of the source's file of that
day (the lines shown, with their line feeds): before sealing in `D:\ARCILOGGER\spool\<source>\<day>.log`,
after sealing in the decompressed `vault\YYYY\MM\DD\<source>.log.gz` and in every evidence
package holding that day of that source (sealing does not change the bytes). Recompute it with
`python -I -c "import gzip,hashlib,sys; d=gzip.open(sys.argv[1]).read();
print(hashlib.sha256(d[int(sys.argv[2]):int(sys.argv[3])]).hexdigest())" <source>.log.gz <from_offset>
<to_offset>` (`open(path, 'rb')` for the spool file); `count` is `to_line - from_line + 1`. The
`sha256` of `search.export` is the downloaded file's (`Get-FileHash -Algorithm SHA256`; the answer
carries it as `Repr-Digest`); an export is not sealed, as its "Bilgi" sheet says: the evidence
package is the integrity proof. A package built from a search shows `search.refs`, then the same
user's `evidence.request` and `evidence.create`, and verifies like any package (section 5).

**Reading the 5651 report (section 10).** The official report of a 5651 lookup (the PDF; under
`rapor/` in a package, section 4.1) gives the chain from an IP address at a moment to the session,
the device and the person, with the raw lines it rests on; [RAPOR.md](RAPOR.md) describes its
parts and texts. Lease evidence, the step from the internal IP address to the MAC address and the
device, comes from the leases of the DHCP server (MikroTik or FortiGate), from MikroTik `ARP-LOG`
records (medium confidence) or, without a lease and only on interfaces listed in the settings,
from the srcmac field of the FortiGate session record. srcmac is **partial evidence**: it does not
prove that the MAC sent the session, and its confidence is low. Until the format of the FortiGate
DHCP records is verified on site, the report says so. Method notes and exit rows come from the
outcome stored when the lookup was made, not from the settings of the day the report is
downloaded; earlier exit (internet line) definitions are in the organisation's audit log
(`settings.change`). Raw lines are printed unchanged: the session lines of an interface with
FortiGate device identification on carry the source MAC address (`srcmac`), the device name, the
operating system, the hardware vendor and version, the device type and the user names the
FortiGate learned passively. These fields appear in every session line the report cites, other
people's candidate sessions of a double-NAT report included, and the same lines are in the
package's `extract/` folder and its sealed files ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.5).
A lookup uses only srcmac of them, only on listed interfaces and as partial evidence; it uses none
of the other fields. The reading table (exact in the records / configured / assumed, with the
PDF's own labels and the explanation keys behind them) is bilingual and sits in the Turkish part,
section 10; [RAPOR.md](RAPOR.md) has the same table.
