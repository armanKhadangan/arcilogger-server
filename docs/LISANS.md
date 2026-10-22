# Lisans ve etkinleştirme

> English: [LICENSING.md](LICENSING.md). Karar kaydı:
> [adr/0013-offline-licensing.md](adr/0013-offline-licensing.md). Kurulum:
> [KURULUM.md](KURULUM.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Lisans yalnızca ücretli modülleri açar ya da kapatır; 5651 işlevleri hiçbir lisans
> durumunda durmaz.

Bu belge ARCILOGGER'ı kullanan kuruluş içindir: hangi modüllerin lisansa bağlı olduğunu, deneme
süresini, lisansın nasıl istenip yüklendiğini, durumları ve uyarı bantlarını, sınırları, bakım
süresini ve yükseltmeleri, sunucu değişikliğini ve lisanslamanın neyi koruyamadığını anlatır.

Lisans veren (hak sahibi): **Arman Khadangan, arman@khadangan.com**.

## 1. Kısaca

- **Süresiz lisans ve yıllık bakım.** Bir lisansın süresi dolmaz. Bakım tarihi
  (`maintenance_until`) yalnızca **yükseltmeleri ve desteği** sınırlar: bakım tarihinden sonra
  yayımlanan bir sürüm o lisansla ücretli modülleri açmaz. Kurulu sürüm çalışmaya devam eder.
- **14 günlük deneme:** yeni bir kurulum ilk 14 gün bütün modüllerle çalışır.
- **Çevrimdışı etkinleştirme:** sunucu bir **istek kodu** gösterir; kodu e-postayla lisans verene
  gönderirsiniz; gelen lisans dosyasını (`.lic`) web arayüzünden, komutla ya da kurulum
  programıyla yüklersiniz.
- **Hiçbir ağ bağlantısı yok:** ARCILOGGER lisans için hiçbir zaman dışarıya bağlanmaz,
  telemetri göndermez. Lisans tamamen sunucunun üzerinde denetlenir; istek kodu ve lisans
  dosyası bir kişi tarafından e-postayla taşınır.
- **5651 Temel her zaman çalışır:** lisans yokken, süresi dolmuşken, geçersizken ya da başka bir
  sunucuya aitken de log toplama, mühürleme, saklama, 5651 sorgusu, kanıt paketleri, denetim
  kaydı, giriş ve kullanıcılar, işletim uyarıları ve panelin sağlık kutuları çalışır. Yalnızca
  ücretli modüller kapanır ve uyarı bandı görünür.

## 2. Modüller

Her lisans modülleri ve sınırları listeler. **5651 Temel** her lisansta ve her durumda açıktır.
Fiyatlar üründe yer almaz.

| Anahtar | Modül | English | Ne içerir | Gerekir |
|---|---|---|---|---|
| `core_5651` | 5651 Temel | 5651 Core | Log toplama, mühürleme, saklama, 5651 sorgusu, kanıt paketleri, cihazlar ve kişiler, uyarılar, denetim kaydı | — (her zaman açık) |
| `usage_analytics` | Kullanım analizi | Usage analytics | Kişi, cihaz, site ve kategori bazında tahmini kullanım süreleri ve dışa aktarma: **Kullanım** sayfası ve paneldeki kullanım bölümleri | — |
| `usage_reports` | Zamanlanmış kullanım raporları | Scheduled usage reports | Kullanım raporlarının zamanlanmış olarak üretilip e-postayla gönderilmesi: **Raporlar** sayfası | Kullanım analizi |
| `bulk_import` | Toplu içe aktarma (CSV) | Bulk import (CSV) | Kişilerin ve cihaz atamalarının CSV dosyasından toplu içe aktarılması (Cihazlar ve Kişiler → **Aktarım** sekmesinin içe aktarma bölümü); elle giriş ve CSV dışa aktarma her zaman açıktır | — |

### 2.1 5651 Temel neyi her zaman yapar?

Lisans durumu ne olursa olsun şunlar çalışır ve hiçbir lisans durumu bunları kapatmaz: syslog
toplayıcı ve ham log dosyaları (spool), ayrıştırma ve veritabanına yükleme, gece mühürlemesi (Kamu
SM zaman damgası), zincir doğrulaması, saklama ve imha, replika, veritabanı yedeği; 5651 sorgusu,
resmi PDF raporu ve zaman damgası, ham satırlar, kanıt paketleri ve bağımsız doğrulayıcı;
Mühürleme sayfası ve kontör; zincirli denetim kaydı ve doğrulaması; giriş, iki adımlı doğrulama,
kullanıcılar ve roller; cihazlar, kişiler, departmanlar, VLAN'lar ve cihaz-kişi atamaları (elle);
işletim uyarıları (sessiz kaynak, disk, mühürleme, kontör, saat, replika, yedek, uyarı e-postası,
Güvenlik sayfası); panelin sağlık kutuları (log alımı, mühür, kontör, saat, uyarılar) ve lisans
sayfası.

Yalnızca bir uyarı kodu ücretli bir modüle aittir: `R20-report` (zamanlanmış bir rapor
gönderilemedi) Zamanlanmış kullanım raporları modülüne aittir; modül kapalıyken açılmaz, modül
kapanınca açık olanı kendiliğinden çözülür ([UYARILAR.md](UYARILAR.md)).

Kişisel verilerin zamanında silinmesi de lisansa bağlı değildir: kullanım özetlerinin ve rapor
dosyalarının saklama işleri (`usage.retention`, `reports.purge`) her durumda çalışır.

### 2.2 API alanları

Web arayüzü sunucunun `/api/v1` altındaki API'sini kullanır. Kapalı bir modülün uç noktaları 403
`module_not_licensed` yanıtı verir; 5651 Temel'in uç noktaları hiçbir lisans durumunda bu yanıtı
vermez.

| API alanı | Ne | Modül |
|---|---|---|
| `/health` | Sağlık denetimi | 5651 Temel |
| `/auth` | Giriş, iki adımlı doğrulama, parola, ilk yönetici | 5651 Temel |
| `/users` | Kullanıcılar ve roller (yeni kullanıcıda kullanıcı sınırı, bölüm 6) | 5651 Temel |
| `/audit` | Denetim kaydı ve zincir doğrulaması | 5651 Temel |
| `/dashboard` | Panelin sağlık kutuları | 5651 Temel |
| `/alerts` | Uyarılar, durumları ve notlar (Güvenlik sayfası) | 5651 Temel |
| `/lookup` | 5651 sorgusu, resmi PDF raporu ve zaman damgası | 5651 Temel |
| `/lookups` | Kaydedilmiş 5651 sorguları | 5651 Temel |
| `/raw` | Ham log satırları | 5651 Temel |
| `/evidence` | Kanıt paketleri | 5651 Temel |
| `/seals` | Mühürlü günler, manifest, zaman damgası, doğrulama | 5651 Temel |
| `/credits` | Kamu SM kontörü | 5651 Temel |
| `/devices` | Cihazlar ve cihaz-kişi atamaları | 5651 Temel |
| `/persons` | Kişiler | 5651 Temel |
| `/departments` | Departmanlar | 5651 Temel |
| `/vlans` | VLAN'lar ve alt ağlar | 5651 Temel |
| `/license` | Lisans durumu, lisans dosyası yükleme ve kaldırma | 5651 Temel |
| `/usage` | Kullanım sayfasının verileri, dışa aktarma, yeniden hesaplama | Kullanım analizi |
| `/reports` | Zamanlanmış raporlar, çalıştırmalar, önizleme | Zamanlanmış kullanım raporları |
| `/imports` | CSV içe aktarma ve şablonları | Toplu içe aktarma (CSV) |

### 2.3 Arka plan işleri (worker)

Kapalı bir modülün işi çalışmaz: gün başına bir kez `skipped` (atlandı) olarak kaydedilir, worker
günlüğüne modül her açılıp kapandığında bir satır yazılır. Atlanan çalıştırmalar `R20-job`
uyarısı açmaz. Kapalı modülün verileri silinmez.

| İş | Modül | Lisans kapalıyken |
|---|---|---|
| `license.check` | — (lisans durumunu her 5 dakikada yeniden hesaplar) | her zaman çalışır |
| `partitions.ensure` | 5651 Temel | her zaman çalışır |
| `leases.incremental` | 5651 Temel | her zaman çalışır |
| `leases.nightly` | 5651 Temel | her zaman çalışır |
| `leases.recheck_today` | 5651 Temel | her zaman çalışır |
| `leases.late_evidence` | 5651 Temel | her zaman çalışır |
| `evidence.purge` | 5651 Temel | her zaman çalışır |
| `audit.import` | 5651 Temel | her zaman çalışır |
| `alerts.import` | 5651 Temel | her zaman çalışır |
| `alerts.r15` | 5651 Temel | her zaman çalışır |
| `alerts.system` | 5651 Temel | her zaman çalışır |
| `alerts.notify` | 5651 Temel | her zaman çalışır |
| `usage.requests` | Kullanım analizi | atlanır |
| `usage.daily` | Kullanım analizi | atlanır |
| `usage.today` | Kullanım analizi | atlanır |
| `usage.reattribute` | Kullanım analizi | atlanır |
| `usage.retention` | — (kişisel verilerin silinmesi) | her zaman çalışır |
| `reports.run_due` | Zamanlanmış kullanım raporları | atlanır |
| `reports.purge` | — (rapor dosyalarının silinmesi) | her zaman çalışır |

Komut satırında yalnızca `arcilogger-worker usage rebuild` Kullanım analizi kapalıyken reddedilir
(çıkış kodu 1, `module_not_licensed` iletisi). `arcilogger-worker run`, `rebuild` (kira zaman
çizelgesi, 5651 zincirinin onarımı) ve `alerts …` her zaman çalışır.

### 2.4 Bir modül kapalıyken ve yeniden açılınca

- Menüde modülün sayfası yetkisi olanlara görünmeye devam eder, yanında bir kilit simgesi
  bulunur; sayfa "… lisansınızda yok" der ve hiçbir istek göndermez. Panelin kullanım bölümleri
  küçük bir kilitli kutuya döner; sağlık kutuları değişmez. Aktarım sekmesinde içe aktarma
  bölümü kilitlenir, dışa aktarma düğmeleri kalır.
- Zamanlanmış raporlar gönderilmez; kuyruktaki yeniden hesaplama istekleri bekler.
- Modül yeniden açıldığında (yeni lisans dosyası yüklendiğinde) API en geç 30 saniyede, worker
  en geç 60 saniyede bunu görür; yeniden başlatma gerekmez (dosyayı yükleyen tarayıcı hemen
  güncellenir). `usage.reattribute` son `usage.auto_rebuild_days` (varsayılan 7) günün eksik ya
  da yarım kalmış kullanım özetlerini kendiliğinden yeniden hesaplar; daha eski günler için Kullanım sayfasındaki **Yeniden hesapla**
  ya da `usage rebuild` komutu ([KULLANIM.md](KULLANIM.md)). Her rapor zamanlaması kaçırdığı en
  yeni dönem için bir kez çalışır.

## 3. Deneme süresi

- Yeni bir kurulum **14 gün** bütün modüllerle ve sınırsız çalışır. Deneme, kurulum programı
  veritabanı şemasını ilk kez kurduğunda başlar. Her sayfada bir bant "Deneme sürümü — N gün
  kaldı." der.
- Deneme bitince durum **Lisanssız** olur (`trial_expired`): ücretli modüller kapanır, en çok
  **3** etkin kullanıcıya kadar yeni kullanıcı eklenebilir (bölüm 6), log kaynağı sınırı yoktur.
  5651 işlevleri çalışmaya devam eder.
- Deneme süresi yeniden kurulumla baştan başlamaz. Başlangıcı, kurulumun ilk kaydından ve şunların
  en eskisinden hesaplanır: kasadaki en eski mühürlü gün, spool'daki en eski gün, denetim
  kaydının ilk kaydı, önceki deneme başlangıçları ve lisans geçmişi. Eski veri klasörüyle yeni bir
  veritabanı kurulursa deneme, eski günlere göre çoktan bitmiş sayılır.
- **Saat denetimi:** deneme başlangıcı (ya da veritabanındaki bir iz) güvenilen zamandan
  (bölüm 10) 10 dakikadan fazla ilerideyse deneme kullanılamaz (`trial_clock_inconsistent`):
  örneğin kurulum sırasında sistem saati yıllar ileri alınmışsa. Saati kurulum sırasında
  gerçekten yanlış olan bir kuruluş, lisans verenden süreli bir deneme lisansı (`valid_until`)
  ister.

## 4. Etkinleştirme

Önce kuruluş adını (`site_name`) girin: istek koduna o girer, lisans veren lisans sahibinin
adını ona bakarak yazar ([KURULUM.md](KURULUM.md) bölüm 7.1). Ad boşsa web sayfası, komut ve kurulum programı "Kuruluş
adı ayarlanmamış" diye uyarır.

### 4.1 İstek kodunu alma

İstek kodu `ARCIREQ1-` ile başlayan bir metindir. İçinde sunucu kimliği, kurulum kimliği, ürün
sürümü, kuruluş adı, platform, sunucunun donanım kimliğinin (SMBIOS UUID) okunup okunmadığı, gün
ve bir denetim toplamı vardır. **Gizli değildir** (parola ya da anahtar içermez). Aynı UTC günü
içinde (Türkiye saatiyle 03:00'ten ertesi gün 03:00'e kadar) aynı sunucu için aynıdır. Üç yerden
alınır:

- **Web arayüzü:** Ayarlar → Lisans → **Etkinleştirme**: kod kutusu, **Kopyala** ve **İndir
  (.txt)** (`arcilogger-istek-kodu.txt`); sunucu kimliği, kurulum kimliği ve kuruluş adı da
  burada görünür. Sayfayı yalnızca yöneticiler görür.
- **Komut** (yönetici olarak açılmış Windows PowerShell'de; tek satır, yalnızca kod):

  ```powershell
  $db  = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe"
  $cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
  & $db license request-code --config $cfg
  ```

- **Kurulumun sonu:** kurulum programı sonraki adımların 6. adımında kodu yazar ("6. Lisans
  [licence]: deneme süresi 14 gündür [14-day trial]. İstek kodu [request code]:").

Sunucu kimliği `XXXX-XXXX-XXXX-XXXX` biçimindedir ve Windows'un makine kimliğinden
(MachineGuid) ve anakartın SMBIOS UUID'sinden türetilir. Sunucu kimliği okunamazsa istek kodu
oluşmaz (`license_server_id_unavailable`).

### 4.2 Kodu gönderme

İstek kodunu **arman@khadangan.com** adresine e-postayla gönderin; istediğiniz modülleri, log
kaynağı ve kullanıcı sayısını yazın. E-posta programının kodu satırlara bölmesi, boşluk eklemesi
ya da tireleri düşürmesi önemli değildir. Lisans veren sunucunuza hiçbir zaman bağlanmaz; başka
bir bilgi gerekmez. Gelen yanıt `ARCI-L-….lic` adlı bir lisans dosyasıdır (en çok 64 KiB, imzalı
JSON).

### 4.3 Lisans dosyasını yükleme

Üç yol vardır; hepsi aynı denetimleri yapar (imza, sunucu kimliği, geçerlilik ve bakım tarihi):

- **Web arayüzü:** Ayarlar → Lisans → **Lisans dosyası**: dosyayı seçip yükleyin (parolanız
  yeniden sorulur). Ret nedeni sayfada Türkçe ve İngilizce görünür.
- **Komut:** önce denetleyin (hiçbir şey yazmaz), sonra yükleyin:

  ```powershell
  & $db license verify "C:\Kurulum\ARCI-L-<numara>.lic" --config $cfg
  & $db license install "C:\Kurulum\ARCI-L-<numara>.lic" --config $cfg
  ```

- **Kurulum programı:** `-LicenseFile <dosya.lic>` ile; dosya veritabanı şeması kurulduktan
  hemen sonra yüklenir ([KURULUM.md](KURULUM.md) bölüm 7). Ürünün reddettiği bir dosya kurulumu
  durdurmaz: uyarı verilir, kurulum çıkış kodu 1 ile biter.

Aynı lisansı yeniden yüklemek bir şey değiştirmez ("değişmedi" olarak kaydedilir). Yeni bir lisans
öncekinin yerini alır. Her yükleme, kaldırma ve ret denetim kaydına ve lisans geçmişine yazılır.
Web arayüzü ve worker yeni durumu yeniden başlatma olmadan görür.

### 4.4 Lisansı kaldırma ve durumu görme

- Kaldırma: Ayarlar → Lisans → **Lisansı kaldır** (onay ister) ya da
  `& $db license remove --yes --config $cfg`. Durum deneme süresi içindeyse deneme, değilse
  Lisanssız olur; kullanıcı sınırı 3'tür (bölüm 6).
- Durum: Ayarlar → Lisans ya da `& $db license status --config $cfg` (`--json`: web sayfasının
  verisi). Bir lisans kuruluysa kenar çubuğu her kullanıcıya sürümün altında "Lisans: <lisans
  sahibi>" gösterir.

## 5. Durumlar ve uyarı bantları

| Durum | Ne zaman | Açık modüller | Kullanıcı sınırı | Bant (herkes görür) |
|---|---|---|---|---|
| `trial` (Deneme) | Lisans yok, deneme süresi içinde | hepsi | yok | Deneme sürümü — N gün kaldı. |
| `trial_expired` (Lisanssız) | Lisans yok, deneme bitti | 5651 Temel | 3 | Deneme süresi doldu: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor. |
| `trial_expired` + `trial_clock_inconsistent` | Deneme başlangıcı saatten ileride (bölüm 3) | 5651 Temel | 3 | `trial_clock_inconsistent` iletisi (bölüm 13) |
| `licensed` (Lisanslı) | Geçerli lisans | 5651 Temel + lisanstaki modüller | lisanstaki | — |
| `invalid` | Lisans dosyası doğrulanamadı (imza, tanınmayan anahtar, iptal edilmiş lisans) | 5651 Temel | 3 | Lisans dosyası doğrulanamadı: ücretli modüller kapalı. 5651 kayıt toplama, mühürleme, sorgu ve kanıt paketleri çalışmaya devam ediyor. |
| `wrong_server` | Lisans bu sunucu için değil ya da sunucu kimliği okunamıyor | 5651 Temel | lisanstaki | Lisans bu sunucu için değil (sunucu kimliği <kimlik>): ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor. |
| `expired` | Süreli bir lisansın (`valid_until`) süresi doldu | 5651 Temel | lisanstaki | Lisansın süresi <tarih> tarihinde doldu: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor. |
| `version_not_covered` | Bu sürüm bakım tarihinden sonra yayımlandı (bölüm 7) | 5651 Temel | lisanstaki | Bu sürüm (<sürüm>, <yayın tarihi>) bakım süresinden (<bakım tarihi>) sonra yayımlandı: ücretli modüller kapalı. Bakımı yenileyip yeni lisans dosyasını yükleyin. 5651 işlevleri çalışmaya devam ediyor. |

Yalnızca yöneticilerin gördüğü bantlar:

- Kullanıcı sayısı sınırı aşıyorsa: "Kullanıcı sayısı lisans sınırını aşıyor (<kullanılan>/<sınır>):
  yeni kullanıcı eklenemez."
- Log kaynağı sayısı sınırı aşıyorsa: "Log kaynağı sayısı lisans sınırını aşıyor
  (<kullanılan>/<sınır>; <n> yapılandırılmamış). Kayıtlar toplanmaya ve mühürlenmeye devam
  ediyor."
- Bakım 30 gün içinde bitecekse: "Bakım süresi N gün sonra bitiyor (<tarih>)."
- Bakım bittiyse (bilgi; hiçbir şey kapanmaz): "Bakım süresi doldu (<tarih>): yeni sürümler ve
  destek için bakımı yenileyin. Kurulu sürüm çalışmaya devam eder."

Aynı anda en çok iki bant görünür. Yöneticiler banttan lisans sayfasına gider; diğer kullanıcılar
"Yöneticinize başvurun." görür. Ücretli modül içermeyen bir lisansta (yalnızca 5651 Temel)
`expired`, `version_not_covered` ve `wrong_server` kapanan modül olmadığı için kullanıcılara bant
göstermez; yöneticiler bir bilgi bandı görür.

Sorunlu bir lisansta deneme süresine geri dönülmez ve sunucu değişikliğinden sonra bir ek süre
yoktur: yeni lisans dosyası gerekir.

## 6. Sınırlar

### 6.1 Log kaynakları

Bir **log kaynağı**, yapılandırılmış bir cihaz ya da son **30 gün** içinde log göndermiş herhangi
bir göndericidir. Yapılandırılmamış göndericiler (`unknown-<ip>`) de sayılır (sayfada ayrıca
"yapılandırılmamış" olarak gösterilir); bir ağ kaynağının her göndericisi ayrı sayılır.
Yapılandırmadan çıkarılan bir cihaz ya da artık log göndermeyen bir adres, son logundan 30 gün sonra
sayılmaz olur: sınırın altına dönmek için başka bir şey yapmak gerekmez.

Sınır **yumuşaktır:** sınırın üstündeki kaynakların logları da toplanır, mühürlenir ve sorgulanır;
hiçbir veri kaybolmaz. Yöneticiler bant görür; sınırın aşılması ve altına dönülmesi denetim
kaydına yazılır (`license.limit_exceeded`, `license.limit_ok`).

### 6.2 Kullanıcılar

Sayılan, **etkin** kullanıcı hesaplarıdır. Sınıra ulaşıldığında yeni kullanıcı eklemek ya da pasif
bir kullanıcıyı yeniden etkinleştirmek reddedilir (`license_limit`); var olan kullanıcılar
çalışmaya, giriş yapmaya devam eder. Yer açmak için bir kullanıcıyı pasifleştirin. Rol değiştirme
ve pasifleştirme hiçbir zaman sınırlanmaz.

| Durum | Kullanıcı sınırı |
|---|---|
| Deneme | yok |
| Lisanslı, `wrong_server`, `expired`, `version_not_covered` | lisanstaki sayı (lisansı kaldırmak ya da yükseltmek satın alınan sınırı kaldırmaz) |
| Lisanssız (`trial_expired`), `invalid` | **3** |

- İlk yöneticinin kurulumu (kurulum anahtarıyla) hiçbir zaman sınırlanmaz.
- Kurtarma komutu `arcilogger-api create-admin` sınır doluyken reddeder; ancak hiçbir yönetici
  giriş yapamıyorsa (bütün yöneticiler pasif ya da kilitli: gerçek kurtarma) yine çalışır. Sınır
  doluyken bir kullanıcıyı aynı işlemde pasifleştirmek için `--deactivate KULLANICI_ADI` verin:

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" create-admin --deactivate <kullanıcı adı> --config $cfg
  ```

- Lisans bileşeni yüklenemezse (`license_unavailable`) ücretli modüller kapanır, ama hiçbir
  kullanıcı işlemi reddedilmez; 5651 işlevleri ve kullanıcı yönetimi çalışır.

## 7. Bakım süresi ve yükseltmeler

- Bir sürümün **yayın tarihi**, o sürümle birlikte gelen sabit bir tarihtir (lisans sayfasının
  Hakkında bölümünde, kurulum paketinin `bundle-manifest.json` dosyasında ve kurulu programın
  `VERSION.json` dosyasında `release_date`). Paketin oluşturulduğu gün değildir; bir sürümü sonra
  yeniden oluşturmak hangi lisansların onu kapsadığını değiştirmez.
- Lisans, yayın tarihi bakım tarihinden önce ya da o günde olan sürümleri kapsar. Daha sonra
  yayımlanmış bir sürüm kurulursa durum `version_not_covered` olur: ücretli modüller kapalıdır,
  5651 işlevleri çalışır. Bakımı yenileyip yeni lisans dosyasını yükleyince modüller açılır.
- **Kurulum programının bakım kuralı:** kurulum ve yükseltmede, **hiçbir şey değiştirmeden önce**
  paketin yayın tarihini `-LicenseFile` dosyasının ya da kurulu lisansın bakım tarihiyle
  karşılaştırır. Kurulu lisansı veri klasöründeki `state\license-status.json` dosyasından okur
  (ürün bu dosyayı her 5 dakikada yazar). Sürüm kapsanmıyorsa durur (çıkış kodu 2):

  ```
  Bu sürüm (<version>, yayın tarihi <release_date>) lisansınızın bakım süresinden (<until> tarihine kadar) sonra yayımlandı; hiçbir şey değiştirilmedi. Bakımı yenileyip yeni lisans dosyasını -LicenseFile ile verin ya da yalnızca 5651 Temel ile devam etmek için -AllowUncoveredUpgrade kullanın.
  [This version (<version>, released <release_date>) was released after your licence's maintenance period (until <until>); nothing was changed. Renew maintenance and pass the new licence file with -LicenseFile, or use -AllowUncoveredUpgrade to continue with 5651 Temel only.]
  ```

  İki yol vardır: bakımı yenileyip yeni lisans dosyasını `-LicenseFile` ile vermek ya da
  `-AllowUncoveredUpgrade` ile sürümü yine de kurmak. Yalnızca `ARCILOGGER-Setup-<sürüm>.exe`
  kullanıyorsanız aynı seçenekler `/LICENSEFILE="<dosya>"` ve `/ALLOWUNCOVEREDUPGRADE`
  olarak ona verilir ([KURULUM.md](KURULUM.md) bölüm 7). İkincisinde sürüm **yalnızca 5651 Temel**
  ile çalışır (ücretli modüller, sürümü kapsayan bir lisans yüklenene kadar kapalıdır): böylece bir
  5651 yükümlülüğü için gereken bir düzeltme her zaman kurulabilir. Aynı paketi yeniden çalıştırmak
  (onarım) yalnızca uyarı verir.
- Bu kural **yol göstericidir:** kurulum programı imzayı denetlemez ve durum dosyası
  değiştirilebilir. Asıl denetimi çalışan ürün yapar (`version_not_covered`).
- **Eski bir sürüm, daha yeni bir veritabanının üzerine yeniden kurulamaz** (şema geri gitmez).
  Yükseltmeden hemen sonra geri dönmenin tek yolu kurulum programının `-Rollback -RestoreDatabase`
  seçeneğidir: veritabanı yükseltme öncesi yedeğe döner ([KURULUM.md](KURULUM.md) bölüm 13).
  Ücretli modülleri geri açmanın yolu bakımı yenileyip yeni lisans dosyasını yüklemektir.

## 8. Mevcut kurulumlar

Lisanslamadan önceki bir sürümden yükseltilen bir kurulumun kasasında ve denetim kaydında 14 günden
eski günler vardır. Bu yüzden yükseltmeden hemen sonra deneme süresi dolmuş sayılır: Kullanım
analizi, Zamanlanmış raporlar ve CSV içe aktarma kapanır; 3'ten fazla etkin kullanıcı varsa yeni
kullanıcı eklenemez. 5651 işlevleri etkilenmez. Kurulum programı yükseltmeden önce uyarır
(durmaz; ne lisans dosyası ne de okunabilir bir lisans durum dosyası varsa, kasada 14 günden
eski mühürlü gün olduğunda):

```
Bu kurulumun lisansı yok ve deneme süresi kasadaki eski mühürlü günler nedeniyle dolmuş sayılacak: Kullanım analizi, Zamanlanmış raporlar ve CSV içe aktarma kapanacak; etkin kullanıcı sayısı 3'ün üzerindeyse yeni kullanıcı eklenemez. 5651 işlevleri etkilenmez. Yükseltmeden sonra web arayüzünde Ayarlar → Lisans sayfası istek kodunu gösterir (kurulumun sonunda da yazılır); kodu lisans verene gönderin, gelen .lic dosyasını aynı sayfadan yükleyin. Lisans yüklenene kadar ücretli modüller kapalı kalır; lisans yüklenip Kullanım analizi açılınca son günlerin (varsayılan 7 gün, usage.auto_rebuild_days) kullanım özetleri kendiliğinden, daha eskileri Kullanım sayfasındaki Yeniden hesapla ile yeniden hesaplanır.
[This installation has no licence and its trial counts as over because of older sealed days: usage analytics, scheduled reports and CSV import will be off; with more than 3 active users no new user can be added. 5651 functions are not affected. After the upgrade the web interface shows the request code under Settings → Licence (the end of the installation prints it too); send it to the licensor and upload the .lic you receive on the same page. Until the licence is installed the paid modules stay off; once it is installed and usage analytics is on, the usage rollups of the recent days (7 by default, usage.auto_rebuild_days) are rebuilt by themselves, older days with Recompute on the Usage page.]
```

Ne yapmalı:

- **Kurulu sürümde lisanslama varsa** (Ayarlar → Lisans sayfası ya da `arcilogger-db license`
  komutu var): yükseltmeden **önce** istek kodunu alıp gönderin (bölüm 4), gelen lisans dosyasını
  yükleyin ya da yükseltmede `-LicenseFile` ile verin. Bakımı yeni sürümü kapsamalıdır (bölüm 7).
- **Kurulu sürümde lisanslama yoksa** istek kodu ancak lisanslamalı sürümle alınabilir (kurulum
  kimliğini yükseltme oluşturur; eski sürümde `license request-code` komutu yoktur). Uyarı bu
  yüzden `-LicenseFile` istemez. Yükseltmeyi lisans verenle önceden planlayın: yükseltin,
  istek kodunu (Ayarlar → Lisans sayfasında; kurulumun sonunda 6. adımda da yazılır) hemen
  gönderin ve gelen `.lic` dosyasını aynı sayfadan yükleyin. Aradaki sürede ücretli modüller
  kapalıdır; kullanım özetleri modül açılınca son `usage.auto_rebuild_days` (varsayılan 7) gün için kendiliğinden
  yeniden hesaplanır, daha eski günler için Kullanım sayfasındaki **Yeniden hesapla** kullanılır
  (bölüm 2.4). İlk `license.check` çalışması modüllerin kapandığını denetim kaydına yazar.

## 9. Sunucu değişikliği

- Lisans, sunucu kimliğine bağlıdır. Yeni bir sunucu, Windows'un yeniden kurulması (yeni
  MachineGuid), anakart değişikliği (yeni SMBIOS UUID) ya da bu kimlikleri değiştirilen bir sanal
  makine yeni bir sunucu kimliği verir: durum `wrong_server` olur, ücretli modüller kapanır, 5651
  işlevleri çalışır. Ek süre yoktur.
- Yeni sunucuda istek kodunu alıp gönderin; lisans veren lisansı yeni sunucu için yeniden düzenler.
  Eski lisans sonraki bir sürümde iptal edilir; iptal, o sürüme yükseltilen kurulumlara ulaşır
  (`license_revoked`). İptal edilmiş bir lisans `invalid` durumundadır ve yüklenmez.
- Aynı sunucuda veritabanı geri yükleme ya da ARCILOGGER'ı yeniden kurma lisansı geçersiz kılmaz
  (bağlayan sunucu kimliğidir, kurulum kimliği yalnızca bilgidir).

## 10. Saat ve güvenilen zaman

ARCILOGGER deneme süresinin sonunu, süreli lisansların bitişini ve bakım tarihini yalnızca Windows
saatine göre değerlendirmez. **Güvenilen zaman** şunların en geç olanıdır: sistem saati, mühürlü
günlerin en yeni Kamu SM zaman damgası, kasadaki en yeni mühürlü günün sonu, kurulu sürümün yayın
günü ve kurulu lisansın düzenlenme zamanı. Bu yüzden mühürlü günler olduğu sürece saati geri
almak hiçbir süreyi uzatmaz: kasadaki en yeni günün sonu ve en yeni zaman damgası taban olarak
kalır (henüz hiç mühürlü gün yoksa taban yalnızca sürümün yayın günü ve kurulu lisansın
düzenlenme zamanıdır). Saati ileri almak süreleri kısaltır; deneme başlangıcını ileri almanın
sonucu bölüm 3'teki saat denetimidir.

Lisans sayfasının Hakkında bölümü hangisinin kullanıldığını yazar ("Zaman kaynağı": Kamu SM zaman
damgası, sistem saati, kasadaki son mühürlü gün, sürümün yayın tarihi ya da lisansın düzenlenme
tarihi) ve sistem saati gerideyse bunu belirtir. Saati doğru tutun ([KURULUM.md](KURULUM.md)
bölüm 1.3): geri kalan saat mühürlemeyi de durdurur.

## 11. Lisanslamanın koruyamadıkları

ARCILOGGER çevrimdışı çalışır; hiçbir denetim dışarıya sormaz. Sunucunun yöneticisi (Windows
yöneticisi ve veritabanının sahibi) aşağıdakileri yapabilir ve hiçbir çevrimdışı denetim bunları
tam olarak engelleyemez:

- Sunucu kimliğini taklit etmek: MachineGuid değerini değiştirmek; sanal makinelerde SMBIOS UUID'yi
  ayarlamak ya da aynı kimliklerle bir sanal makineyi kopyalamak. Ağ bağlantısı olmadan fark
  edilemez.
- Yeni bir deneme için her şeyi sıfırdan kurmak (yeni veritabanı **ve** boş veri klasörü): bütün
  mühürlü günler ve denetim kaydı kaybolur.
- Veritabanı sahibi olarak lisans tablolarının "yalnızca ekleme" korumasını kapatmak: bu hiçbir
  lisans kararını değiştirmez, çünkü lisans her seferinde yeniden doğrulanan imzalı dosyadır.
- Kasadaki günleri gizleyip saati geri almak: mühürleme durur ve mühürleme uyarıları açılır, yani
  işletimde görünür.
- Bir modül kapandıktan sonra daha önce hesaplanmış kullanım tablolarını SQL ile okumak.
- Kararlı bir yöneticinin program dosyalarını (Python kodu) değiştirmesi ya da program klasörüne
  dosya eklemesi. Sonraki bir aşama (derlenmiş lisans modülü, imzalı dosya listesi, Authenticode
  imzası) bunu zorlaştıracak, ama engellemeyecektir.

Bunlara karşı önlem sözleşmeseldir: lisans sözleşmesi, lisans verenin düzenlediği lisansların
kaydı ve kullanıcı arayüzünde görünen lisans sahibi adı.

Lisanslamanın koruduğu ise şunlardır: değiştirilmiş ya da uydurulmuş bir lisans dosyası (Ed25519
imzası), donanım kimliği taklit edilmemiş başka bir sunucunun lisansı, veritabanındaki lisans
satırlarının değiştirilmesi, mühürlü günler varken saatin geri alınması ve kurulumda saatin
ileri alınması, deneme süresini yeniden başlatma girişimleri (yeni veritabanı ve eski veri
klasörü, kurulum satırının silinmesi), ortam değişkeni, yapılandırma dosyası, veritabanı ya da API
ile yeni bir güvenilen anahtar eklenmesi (hizmetler Python'u yalıtılmış çalıştırır, `python -I`)
ve sınırların dolanılması (yapılandırılmamış göndericiler de sayılır; kurtarma komutu ve art arda
kullanıcı ekleme kullanıcı sınırına uyar; lisansı kaldırmak satın alınan sınırı kaldırmaz).

## 12. Kanıtlara dokunulmaz

Lisans hiçbir zaman kanıtı değiştirmez: ham loglara, mühürlü günlere, manifestlere, zaman
damgalarına, kanıt paketlerine, bağımsız doğrulayıcıya ve 5651 PDF raporuna lisans işareti
konmaz. Lisans sahibinin adı web arayüzünde (kenar çubuğu, lisans sayfası) ve lisans
komutlarının çıktısında görünür; kanıtın hiçbir parçasında yer almaz.

Lisans işlemleri denetim kaydına yazılır (kaydın kendisi her gece günle birlikte mühürlenir).
Denetim kaydının ayrıntıları lisans sahibinin adını ve sunucu kimliklerini taşımaz; bunlar lisans
geçmişindedir (Ayarlar → Lisans → **Geçmiş**).

| Eylem | Denetim Kaydı sayfasında | English |
|---|---|---|
| `license.trial_start` | Deneme başladı | Trial started |
| `license.install` | Lisans yüklendi | Licence installed |
| `license.remove` | Lisans kaldırıldı | Licence removed |
| `license.refuse` | Lisans reddedildi | Licence refused |
| `license.limit_refused` | Lisans sınırı: kullanıcı eklenemedi | Licence limit: user not added |
| `license.limit_exceeded` | Lisans sınırı aşıldı | Licence limit exceeded |
| `license.limit_ok` | Lisans sınırı içinde | Back within licence limit |
| `license.state` | Lisans durumu değişti | Licence state changed |

## 13. Komutlar ve hata kodları

Komutlar yönetici olarak açılmış Windows PowerShell'de, tam yoluyla çalışır (program `PATH`
üzerinde değildir; `$db` ve `$cfg` bölüm 4.1'deki gibi):

```powershell
& $db license status --config $cfg             # durum (--json: web sayfasının verisi)
& $db license request-code --config $cfg       # istek kodu, tek satır (--json: ayrıntılarıyla)
& $db license verify <dosya.lic> --config $cfg # denetler, hiçbir şey yazmaz
& $db license install <dosya.lic> --config $cfg
& $db license remove --yes --config $cfg
```

Çıkış kodları: `0` tamam; `1` reddedildi (lisans dosyası kabul edilmedi, kaldırılacak lisans yok,
sunucu kimliği okunamadı ya da lisans bileşeni yüklenemedi); `2` kullanım ya da yapılandırma
hatası (ör. dosya okunamadı, `remove` için `--yes` yok); `3` veritabanına ulaşılamadı. `status`,
`install` ve `remove` kurulum programı için `state\license-status.json` dosyasını yazar.

Bütün iletiler Türkçe ve İngilizcedir. Web arayüzü ve API'nin hata yanıtları `code`,
`message_tr` ve `message_en` taşır; `{…}` yerine gerçek değer gelir.

| Kod | HTTP | Türkçe | English |
|---|---|---|---|
| `license_too_large` | 413 | Lisans dosyası çok büyük (en çok 64 KiB). | The licence file is too large (at most 64 KiB). |
| `license_malformed` | 422 | Dosya bir ARCILOGGER lisans dosyası değil ya da bozuk. | The file is not an ARCILOGGER licence file or it is damaged. |
| `license_unknown_key` | 422 | Lisans, bu sürümün tanımadığı bir anahtarla imzalanmış ({key_id}). | The licence is signed with a key this version does not know ({key_id}). |
| `license_signature_invalid` | 422 | Lisans dosyasının imzası geçersiz: dosya değiştirilmiş olabilir. | The licence file's signature is invalid: the file may have been changed. |
| `license_revoked` | 422 | Bu lisans iptal edilmiş ({license_id}). | This licence has been revoked ({license_id}). |
| `license_server_id_unavailable` | 409 | Sunucu kimliği okunamadı; lisans bu sunucuya göre denetlenemiyor. | The server id cannot be read; the licence cannot be checked against this server. |
| `license_other_server` | 409 | Lisans bu sunucu için düzenlenmemiş (bu sunucu: {server_id}). Yeni lisans için istek kodunu gönderin. | The licence was not issued for this server (this server: {server_id}). Send the request code for a new licence. |
| `license_expired` | 409 | Lisansın geçerlilik süresi {date} tarihinde doldu. | The licence expired on {date}. |
| `license_version_not_covered` | 409 | Bu sürüm ({version}, yayın tarihi {release_date}) lisansın bakım süresinden ({until} tarihine kadar) sonra yayımlandı. | This version ({version}, released {release_date}) was released after the licence's maintenance period (until {until}). |
| `license_not_installed` | 404 | Kurulu bir lisans yok. | No licence is installed. |
| `license_unavailable` | 503 | Lisans bileşeni yüklenemedi: ücretli modüller kapalı. 5651 işlevleri çalışmaya devam ediyor. | The licence component could not be loaded: paid modules are off. The 5651 functions keep working. |
| `trial_clock_inconsistent` | — | Deneme başlangıcı sistem saatinden ileride: deneme süresi kullanılamıyor. Deneme lisansı için arman@khadangan.com ile iletişime geçin. | The trial start lies after the current time: the trial is unavailable. Contact arman@khadangan.com for a demo licence. |
| `module_not_licensed` | 403 | Bu özellik lisansınızda yok: {module_tr}. Ayrıntılar Ayarlar → Lisans sayfasında. | This feature is not part of your licence: {module_en}. See Settings → Licence. |
| `license_limit` | 409 | Kullanıcı sayısı lisans sınırında ({used}/{max}): yeni kullanıcı eklenemez ya da yeniden etkinleştirilemez. Var olan kullanıcılar çalışmaya devam eder. | The number of users is at the licence limit ({used}/{max}): no user can be added or re-enabled. Existing users keep working. |
| `request_malformed` | — | İstek kodu okunamadı. | The request code cannot be read. |
| `request_checksum` | — | İstek kodu eksik ya da hatalı kopyalanmış (denetim toplamı tutmuyor). | The request code is incomplete or mistyped (checksum mismatch). |

`trial_clock_inconsistent` bir durum sorunudur (lisans sayfası, bant, komut); `request_malformed`
ve `request_checksum` lisans verenin aracında, bozuk kopyalanmış bir istek kodu için görünür:
o zaman kodu yeniden kopyalayıp gönderin. Tarihler Türkçede `GG.AA.YYYY`, İngilizcede
`YYYY-AA-GG` biçimindedir.
