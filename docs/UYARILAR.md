# Uyarılar ve uyarı e-postası

> English: [ALERTS.md](ALERTS.md). Karar: [adr/0010-alerts-and-email.md](adr/0010-alerts-and-email.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz.

Bu belge her uyarı kodunun anlamını, eşiğini ve ne yapılacağını, uyarı e-postasının kurulumunu ve
neyi içerip neyi **asla** içermediğini, **Güvenlik** sayfasındaki durumları ve notları (bölüm 8)
anlatır. Kural motoru (YAML kuralları, kural düzenleyici) ve webhook'lar sonraki sürümdedir (M5).

## 1. Nasıl çalışır

- Veritabanı olmadan çalışan kanıt görevleri (mühürleme, doğrulama, replika, imha, saat, kontör) ve
  toplayıcı uyarılarını `D:\ARCILOGGER\state\alerts.jsonl` dosyasına yazar. Worker bu satırları
  dakikada bir `alerts` tablosuna **bir kez** aktarır (`alerts.import`): son tam satırın sonu
  veritabanında aynı işlemde saklanır; yarım kalmış son satır satır sonu gelene kadar bekler.
  Elektrik kesintisinden sonra gelen ilk uyarı yeni bir satırda başlar: yarım kalan parça atlanır
  (işin ayrıntılarında bildirilir), ardındaki uyarı aktarılır.
- Worker ayrıca kendisi denetler: R15 dakikada bir (`alerts.r15`), R20 sistem denetimleri 5
  dakikada bir (`alerts.system`); lisans denetimi (`license.check`, 5 dakikada bir) lisans
  uyarılarını (`R20-license…`, bölüm 2) açar.
- **Her sorun için tek etkin uyarı:** her kuralın bir tekilleştirme anahtarı vardır
  (`R15:fortigate`, `R20-seal:2026-10-18`, `R20-disk:D:` …). Uyarı `open` (açık) ya da
  `acknowledged` (görüldü) olduğu sürece aynı sorun yeni uyarı açmaz; tekrarı sayılır. Sorun
  geçince denetim uyarıyı **kendisi çözer** (`resolved`, `resolved_at`, `details.auto_resolved`).
  Bir denetim, bir kişinin kapattığı uyarıyı yeniden açmaz; sorun sürüyor ya da tekrar ediyorsa
  yeni uyarı açılır (bölüm 8).
- Uyarılar web arayüzünde **Güvenlik** sayfasındadır (bölüm 8); sunucuda komutla da listelenir:

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-worker.exe" alerts list --open
  ```

- Yüksek ve kritik uyarılar `[smtp]` ayarlanmışsa e-postayla bildirilir (`alerts.notify`, bölüm 4).

## 2. Uyarı kodları

Bütün uyarılar **5651 Temel**'e aittir ve hiçbir lisans durumunda kapanmaz; tek istisna
`R20-report`'tur (Zamanlanmış kullanım raporları modülü, [LISANS.md](LISANS.md) bölüm 2.1).
Lisans uyarıları (`R20-license`, `R20-license-limit`, `R20-license-maintenance`) da 5651
Temel'e aittir: her lisans durumunda çalışır, yalnızca lisansı bildirir.

| Kod | Anlamı | Önem | Eşik (ayar) | Ne yapılmalı |
|---|---|---|---|---|
| `R15` | **Log kaynağı sessiz:** etkin, beklenen, yapılandırılmış bir kaynaktan kalp atışı süresinden uzun süredir mesaj yok (5651 açısından kritik). Hiç mesaj göndermemiş bir kaynak, kaydı oluştuktan `alerts.r15_never_seen_minutes` sonra uyarır. Worker başladıktan, kaynağın kaydı oluştuktan ya da ilk mesajından sonraki `alerts.r15_startup_grace_minutes` içinde uyarı yoktur. Kaynak yeniden mesaj gönderince kendiliğinden çözülür | Yüksek | `[[sources]] heartbeat_minutes` (varsayılan 15; örnek yapılandırmanın yorum satırı hâlindeki kaynaklarında güvenlik duvarı 10, ana yönlendirici 15; yeni bir kurulumdaki gibi hiç kaynak yoksa R15 hiçbir kaynağı izlemez); `alerts.r15_never_seen_minutes` (60), `alerts.r15_startup_grace_minutes` (15) | Cihaz açık mı, ağa bağlı mı, syslog ayarı ARCILOGGER'ı gösteriyor mu ([DEVICE-SETUP.md](DEVICE-SETUP.md)); toplayıcı çalışıyor mu (`Get-Service ArciCollector`) |
| `R20-seal` | Bir mühürleme denemesi başarısız (`alerts.jsonl`) ya da kapanmış bir gün ertesi sabah hâlâ mühürlenmemiş (gün başına bir uyarı). Gün mühürlenince çözülür | Yüksek (48 saatten sonra kritik) | `alerts.seal_overdue_hour` (09:00) | **Mühürleme** sayfası, `D:\ARCILOGGER\logs\core.log`; Kamu SM erişimi ve kontör |
| `R20-seal-config` | Seal görevi mühürlemeye **hiç başlayamadı**: Kamu SM kimlik bilgileri yok, `kamusm-roots` klasöründe kök yok, `[tsa] zamane_jar` ayarlı değil ya da gizli depo anahtarı okunamıyor. Görev 5 dakikada bir çalıştığı için gün ve neden başına **bir kez** yazılır. Sonraki başarılı mühürden sonra çözülür | Yüksek | — | Eksik olanı tamamlayın ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3, [kamusm.md](kamusm.md)) |
| `R20-seal-skip` | Art arda `[seal] max_consecutive_empty_days` günden uzun hiç log olmayan dönem; günler tek tek damgalanmadı | Kritik | `[seal] max_consecutive_empty_days` (7) | Toplayıcı, sunucu saati |
| `R20-clock` | Eşiği aşan saat sapması ya da saat sunucusuna ulaşılamadı (`clock-check`): eşik içindeki sonraki saat ölçümüyle çözülür. Mühürlemenin gördüğü bir saat atlaması ya da mühürlenmeyecek bir spool dosyası (`R20-clock:<anahtar>`, dosya ya da atlama başına bir uyarı) kendiliğinden **çözülmez**: bir kişi kapatana kadar açık kalır | Yüksek / kritik | `[clock] alert_offset_ms` (1000) | Windows Saat hizmeti, NTP sunucusu (`Set-ArciTime.ps1`); mühürlemenin uyarılarında uyarıda adı geçen dosya, silinmemelidir ([KURULUM.md](KURULUM.md) bölüm 1.3) |
| `R20-verify` | Mühürlü bir günün **en son** doğrulaması başarısız (zamanlanmış doğrulama ya da Mühürleme sayfasındaki "Doğrula"). Gün yeniden doğrulanınca çözülür | Kritik | — | Aynı gün inceleyin ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md)) |
| `R20-credit` | Kamu SM kontörü eşiğin altında (son `credit` kaydı) ya da okunamadı. Eşiğin üstünde bir okumayla çözülür | Yüksek (okunamadı: orta) | `[tsa] credit_alert_threshold` (500) | Kontör alın ([kamusm.md](kamusm.md)) |
| `R20-replica` | Kopyalama başarısız ya da (`R20-replica:stale`) replika açık ve mühürlü bir gün `alerts.replica_stale_hours` saatten uzun süredir replikada yok; süre günün mühürlenmesinden, worker daha sonra başladıysa worker'ın başlangıcından sayılır (eski mühürlü günler varken replika sonradan açılırsa ilk kopyalara zaman kalır). Başarısız kopya, ondan sonra başarılı bir kopya yapıldığında ve o ana kadar mühürlenen her günün replikası olduğunda çözülür; gecikme uyarısı, eşikten uzun bekleyen gün kalmayınca. **Replika farklı** (kritik) uyarısı kendiliğinden çözülmez | Yüksek / kritik | `alerts.replica_stale_hours` (48) | Replika hedefi (`[replica] target`): `& $core replica --test` |
| `R20-retention` | İmha çalışması reddedildi ya da yarım kaldı; tamamlanan imha bilgi düzeyinde | Bilgi … kritik | `[retention]` | İmha tutanağı, `core.log` |
| `R20-spool` | Ham log diske yazılamıyor; düzelince kaybolan mesaj sayısı ikinci bir uyarıyla | Kritik | — | Veri diski (boş alan, donanım) **hemen** |
| `R20-spool-torn` | Yarım kalmış son satır kapatıldı (baytlar değişmez) | Orta | — | Elektrik kesintisi / çökme nedenini inceleyin |
| `R20-disk` | Veri diski ya da sistem diski eşikten fazla dolu (disk başına bir uyarı). %95'ten itibaren kritik. Eşiğin 2 puan altına inince çözülür | Yüksek / kritik | `alerts.disk_used_percent` (80) | Boş alan açın ya da diski büyütün; ham kayıtları ve kasayı **silmeyin** |
| `R20-parse` | Bir kaynağın son bir saatteki mesajlarının eşikten fazlası ayrıştırılamadı (kaynak başına) | Orta | `alerts.parse_failure_percent` (1), `alerts.parse_min_messages` (1000) | Cihaz yazılımı ya da log biçimi değişmiş olabilir ([PARSERS.md](PARSERS.md)) |
| `R20-job` | Bir worker işinin son N çalışması art arda başarısız (iş başına). Bir sonraki başarılı çalışmayla çözülür. Lisansta kapalı bir modülün atlanan çalıştırmaları (`skipped`) sayılmaz; lisans bileşeni yüklenemezse `license.check` işi başarısız olur ve N başarısızlıktan sonra bu uyarıyı açar ([LISANS.md](LISANS.md)) | Yüksek | `alerts.job_failures` (3) | `D:\ARCILOGGER\logs\worker.log`, `state\worker-status.json` |
| `R20-backup` | Son veritabanı yedeği başarısız, son başarılı yedek `alerts.backup_stale_hours` saatten eski (`state\backup\last.json`) ya da yedek alındı ama durum dosyası yazılamadı; uyarıdan sonra başarılı olan bir yedekle çözülür. `R20-backup:missing`: kurulumdan `alerts.backup_missing_hours` saat sonra hâlâ hiç yedek yok (orta). `R20-backup:prune`: eski yedekler silinemedi (orta); sonraki başarılı bir yedekten bir saat sonra, o yedek bunu yeniden bildirmediyse çözülür. `R20-backup:clock`: yeni yedeğin adı klasördeki bir yedekten eski (saat geri alınmış; yedek saklanır, orta); sonraki başarılı bir yedekle çözülür. `[backup] enabled = false` iken (worker yapılandırmayı 1 dakika içinde yeniden yükler) yedek uyarısı açılmaz, açık olanlar çözülür | Yüksek / orta | `alerts.backup_stale_hours` (36), `alerts.backup_missing_hours` (48) | Yedekleme görevi ve yedek klasörü ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 10) |
| `R20-email` | Uyarı e-postası gönderilemedi (6 denemeden sonra ya da kalıcı 5xx ret). **Tek** yerel uyarıdır, kendisi e-postayla gönderilmez; adı geçen her alıcıya bir e-posta ulaşınca ya da gönderilemeyen adresler `alerts.email_recipients` listesinden çıkarılınca çözülür | Yüksek | — | `[smtp]`, `smtp.password`, güvenlik duvarı; `alerts test-email` |
| `R20-report` | Zamanlanmış bir kullanım raporu üretilemedi ya da bir alıcıya gönderilemedi (6 denemeden sonra ya da kalıcı 5xx ret; [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md)). Tek yerel uyarıdır; adı geçen alıcılara sonraki rapor gidince (ya da adres bütün zamanlamalardan çıkarılınca) ve sonraki rapor üretilince çözülür. **Zamanlanmış kullanım raporları** modülüne aittir: modül lisansta kapalıyken açılmaz; modül kapanınca açık olan uyarı "Zamanlanmış raporlar lisansta kapalı / Scheduled reports are off in the licence" notuyla kendiliğinden çözülür | Orta | — | Raporlar sayfasında çalıştırmanın hatası; `[smtp]`, `alerts test-email` |
| `R20-license` | **Ücretli modüller kapanıyor ya da kapalı** (lisans denetimi, 5 dakikada bir, lisansın güvenilen zamanına göre: [LISANS.md](LISANS.md) bölüm 10): `R20-license:trial-ending` deneme süresinin bitmesine en çok 3 gün kaldı; `R20-license:expiring` süreli bir lisansın bitmesine en çok 14 gün kaldı; `R20-license:off` lisans durumu yüzünden ücretli modüller kapalı (`trial_expired`, `invalid`, `wrong_server`, `expired`, `version_not_covered`) — yalnızca denemenin ya da lisansın verdiği bir modül kapalıyken (yalnızca 5651 Temel içeren bir lisans uyarı açmaz). Uyarı modülleri adlandırır; 5651 işlevleri her durumda çalışır. Lisans geçerli olunca (`licensed`) ya da durum değişince (`license_changed`: ör. deneme bitince denemenin uyarısı çözülür, `R20-license:off` açılır) kendiliğinden çözülür | Yüksek: varsayılan `alerts.email_min_severity` ile ücretli modüller (zamanlanmış kullanım raporları da) kapanmadan önce ve kapandığında e-postayla gider, her uyarı için bir kez | 3 gün (deneme), 14 gün (`valid_until`) | Ayarlar → Lisans: istek kodunu gönderin, yeni `.lic` dosyasını yükleyin ([LISANS.md](LISANS.md) bölüm 4); sunucu değiştiyse yeni lisans (bölüm 9) |
| `R20-license-limit` | Kullanıcı (`R20-license-limit:users`: yeni kullanıcı eklenemez) ya da log kaynağı (`R20-license-limit:log_sources`: kayıtlar toplanmaya ve mühürlenmeye devam eder) sayısı lisans sınırını aşıyor; aşımın lisans olayından (`license.limit_exceeded`) itibaren. Kullanım sınırın içine dönünce (`limit_ok`) çözülür | Orta | lisansın `limits` değerleri | Kullanılmayan kullanıcıları devre dışı bırakın ya da daha yüksek sınırlı bir lisans isteyin; bir kaynak son logundan 30 gün sonra sayılmaz ([LISANS.md](LISANS.md) bölüm 6) |
| `R20-license-maintenance` | Geçerli bir lisansın bakım süresi 30 gün içinde bitiyor ya da bitti (bilgi: yalnızca yükseltmeler ve destek; lisans ve kurulu sürüm geçerli kalır). Bakım yenilenince (`maintenance_renewed`) ya da lisans geçerliliğini yitirince (`license_changed`) çözülür | Bilgi | 30 gün | Bakımı yenileyin ([LISANS.md](LISANS.md) bölüm 7) |
| `R20-admin` | **Tarayıcı yönetiminde bir sorun** ([AYARLAR.md](AYARLAR.md)): imzası ya da kurulumu tutmayan bir istek reddedildi ("olası kurcalama"), yönetim hizmeti ArciAdmin 2 dakikadan uzun süredir kalp atışı yazmıyor ya da `ApplySystem` güvenlik duvarını, saati veya Zamane istemcisini eşitleyemedi (yüksek); bir değişiklik geri alındı (orta); elle müdahale gerekiyor ya da yapılandırma dosyalarının izinleri değişmiş (`acl_drift`, kritik). 5651 işlevleri (toplama, mühürleme) sürer | Orta … kritik | — | Ayarlar → Geçmiş ve isteğin numarası; `Get-Service ArciAdmin`, olay günlüğü `ARCILOGGER-Admin` ([AYARLAR.md](AYARLAR.md) bölüm 8); izin sorunu için kurulumu onarım olarak yeniden çalıştırın |
| `R20-config-change` | **Korumalı bir ayar değişikliği** (tarayıcıdan ya da `ApplySystem`'den): saklama açıldı ya da gün sayısı düştü, replika kapandı ya da hedefi değişti, bir log kaynağı kapatıldı ya da silindi, üretimden test sunucusuna dönüldü, Kamu SM kimlik bilgileri değişti, bir kök ya da yasal parmak izi eklendi, bir Zamane istemcisi yüklendi ya da onaylandı (yüksek); SMTP ya da yedekleme kapatıldı (orta). E-posta yalnızca bölümü, alan adlarını ve zamanı taşır; değer ve kullanıcı adı yoktur | Yüksek / orta | `admin\policy.json` `hold_hours`, `jar_hold_hours` (bekletme) | Değişikliği siz yapmadıysanız bekleyen isteği **Geçmiş** sayfasında iptal edin ve denetim kaydına (`admin.request`, `admin.system`) bakın |
| `new-log-source` | Yapılandırmada olmayan bir gönderici (toplayıcı) | Orta | — | Bilinen bir cihazsa `[[sources]]`'a ekleyin ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4) |
| `new_source` | Aynı olayın veritabanı uyarısı (ingest) | Orta | — | Yukarıdaki gibi |
| `wrong-transport` | Kaynaktan izin verilmeyen taşıma yoluyla mesaj geldi; mesaj kaydedilmedi | Yüksek | `[[sources]] transport` | Cihaz ayarı ya da taklit gönderim ([DEVICE-SETUP.md](DEVICE-SETUP.md) 1.1) |
| `ip_conflict` | Aynı IP aynı anda iki MAC adresinde ([CORRELATION.md](CORRELATION.md)) | Orta | `devices.infrastructure_macs` | İki cihazı ağda inceleyin |

## 3. Veritabanı ayarları

Değiştirme: [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 6 (`& $db settings set …`; liste değerleri
boruyla). Ayarlar kullanıldıkları anda okunur; yeniden başlatma gerekmez.

| Ayar | Varsayılan | Aralık | Anlamı |
|---|---|---|---|
| `alerts.email_recipients` | `[]` | en çok 20 adres | Uyarı e-postası alıcıları (boş: e-posta yok) |
| `alerts.email_min_severity` | `"high"` | info … critical | Bu ve üstü önemdeki uyarılar e-postayla gider |
| `alerts.email_max_per_hour` | `20` | 1–1000 | Alıcı başına saatte en çok e-posta; aşılınca özet |
| `alerts.email_digest_seconds` | `300` | 0–3600 | Bu süre içindeki uyarılar tek e-postada |
| `alerts.email_resolved` | `true` | — | Çözülen uyarı için de e-posta |
| `alerts.web_base_url` | `""` | `https://…` | E-postadaki bağlantı; boşsa `https://<[collector] bind>` (sunucunun kendi adresiyse), yoksa `https://arcilogger.internal` (kurulumun varsayılan sunucu adı) |
| `alerts.r15_startup_grace_minutes` | `15` | 0–1440 | Worker ya da kaynak başladıktan sonra R15 beklemesi |
| `alerts.r15_never_seen_minutes` | `60` | 1–10080 | Hiç mesaj göndermemiş kaynağın R15 süresi |
| `alerts.disk_used_percent` | `80` | 50–99 | R20-disk eşiği |
| `alerts.seal_overdue_hour` | `9` | 1–23 | Kapanmış gün ertesi gün bu saate kadar mühürlenmemişse R20-seal |
| `alerts.replica_stale_hours` | `48` | 1–720 | R20-replica eşiği |
| `alerts.parse_failure_percent` | `1` | 1–100 | R20-parse eşiği (yüzde, üstü) |
| `alerts.parse_min_messages` | `1000` | ≥ 1 | R20-parse için saatlik en az mesaj |
| `alerts.job_failures` | `3` | 1–100 | R20-job: art arda başarısız çalışma |
| `alerts.backup_stale_hours` | `36` | 1–720 | R20-backup: son başarılı yedeğin en çok yaşı |
| `alerts.backup_missing_hours` | `48` | 1–720 | R20-backup:missing: kurulumdan sonra ilk yedeğe kadar süre |

## 4. E-posta kurulumu

Web arayüzünde **Ayarlar → E-posta** sayfası aşağıdaki bağlantı ayarlarını, SMTP parolasını, CA
dosyasının yüklenmesini (`C:\ProgramData\ARCILOGGER\smtp-ca\` klasörüne) ve uyarı e-postası
ayarlarını tek sayfada toplar; açarken ya da sunucuyu değiştirirken önce bir deneme e-postası
gönderilir, **Test e-postası gönder** düğmesi de vardır ([AYARLAR.md](AYARLAR.md)). Konsoldaki yol:

1. **Bağlantı** `C:\ProgramData\ARCILOGGER\arcilogger.toml` içinde (`[smtp] enabled` `false` iken
   hiçbir şey gönderilmez):

   ```toml
   [smtp]
   enabled = true
   host = "<SMTP sunucusu>"
   port = 587                    # 587 + starttls (önerilen) ya da 465 + ssl
   security = "starttls"         # "starttls" | "ssl" | "none" (yalnızca bu sunucudaki aktarıcı)
   username = "<kullanıcı>"      # kimlik doğrulama yoksa satırı silin
   from_address = "<gönderen adres>"
   timeout_seconds = 30
   # ca_file = 'C:\ProgramData\ARCILOGGER\smtp-ca.pem'   # şirket CA'sı; yoksa sistem deposu
   ```

   Sunucu sertifikası `starttls` ve `ssl` ile **her zaman** doğrulanır; kapatma seçeneği yoktur.
   Worker değişen `[smtp]` bölümünü 1 dakika içinde kendisi yükler (bu sürümden önceki bir
   sürümde: `Restart-Service ArciWorker`).
2. **Parola** yalnızca şifreli gizli depoya yazılır (komut satırında değil, konsoldan sorulur):

   ```powershell
   & $core secret set smtp.password
   ```

3. **Alıcılar** (liste değeri boruyla):

   ```powershell
   '["<e-posta adresi>"]' | & $db settings set alerts.email_recipients - --by <kullanıcı adınız>
   ```

4. **Deneme** (`[smtp] enabled = false` iken de çalışır; parola hiçbir zaman yazdırılmaz):

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-worker.exe" alerts test-email --to <e-posta adresi>
   ```

   "Gönderildi / sent" ya da SMTP hatasını (ör. `recipient refused: 550 …`, `timeout`) yazar.
5. **Güvenlik duvarı, sıkı giden mod:** `-StrictOutbound` açıksa `Set-ArciFirewall.ps1`, `[smtp]`
   sunucusuna giden kuralı (`enabled = true` ve `host` doluysa; bölümün `port` değeri, varsayılan
   587) her çalıştığında, kurulum ve yükseltmeler dahil, kendisi oluşturur. Ad olarak yazılmış bir
   sunucuyu **çalıştığı anda** IP adresine çevirir: sağlayıcının adresi değişirse betiği yeniden
   çalıştırın:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
   ```

   Başka bir posta sunucusuna `-SmtpServer <sunucu:port>` ile izin verilir; bu değer hatırlanır
   ([KURULUM.md](KURULUM.md) bölüm 12). Unutturmak için boş değeri `-Command` ile verin (Windows
   PowerShell 5.1, `-File` sonrasındaki boş bir bağımsız değişkeni iletmez):

   ```powershell
   powershell -ExecutionPolicy Bypass -Command "& 'C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1' -SmtpServer ''"
   ```

   Kural yoksa e-posta denemeleri başarısız olur ve `R20-email` açılır.

## 5. E-postada ne var, ne asla yok

**Sunucudan log içeriği çıkmaz** (şartname 0): e-posta yalnızca sabit Türkçe/İngilizce metinlerden
ve uyarının ayrıntılarından **biçimi denetlenmiş birkaç bilgiden** oluşur:

- kural kodu ve adı, önem, zaman (Europe/Istanbul), kaynak adı (yapılandırmadaki ad ya da aşağıdaki
  gibi IP adresli bir ad), kısa bir cümle (gün, eşik, sayı, disk harfi, iş adı, lisans durumu ve
  modül adları ya da aşağıdaki IP adreslerinden biri), ne yapılacağı (bir satır), ARCILOGGER web
  adresi (tek bağlantı);
- **IP adresi** yalnızca şu durumlarda: yeni göndericinin IP adresi (`new-log-source`,
  `new_source`); taşıma yolu izin verilmeyen bir mesajın gönderici adresi (`wrong-transport`:
  yapılandırılmış bir kaynağa ait bir adres — kaynağın adresi ya da ağ tanımının içinde —, mesaj bu
  adresi taklit eden bir göndericiden de gelmiş olabilir); IP çakışmasında çakışan IP adresi
  (`ip_conflict`); bir de kaynak adının içinde: bir ağ tanımındaki göndericinin ve yapılandırılmamış
  bir göndericinin adı IP adresini taşır (`ap-<IP adresi>`, `unknown-<IP adresi>`; konu satırında
  da);
- önce Türkçe, sonra İngilizce; düz metin ve basit HTML (satır içi stil, resim ve dış kaynak yok).

E-posta **asla** içermez: ham log satırı, kişi adı, cihaz sahibinin adı, IP–kişi eşleşmesi, MAC
adresi, hata metni, dosya yolu, lisans sahibi, sunucu kimliği, parola ya da başka bir gizli bilgi. Uyarının başlığı ve ayrıntıları
hiçbir zaman e-postaya kopyalanmaz. Ayrıntılar ARCILOGGER'dadır (`alerts list`).

## 6. Teslim

- Alıcı başına ayrı e-posta; bir alıcının reddi diğerlerini etkilemez.
- `alerts.email_digest_seconds` içindeki uyarılar tek e-postada (en önemlisi önce, en çok 50 uyarı
  listelenir, kalanı sayılır).
- Geçici hata (bağlantı, zaman aşımı, TLS, kimlik doğrulama, 4xx): 1, 5, 15, 60, 180 dakika sonra
  yeniden denenir; 6 denemeden sonra ya da kalıcı ret (5xx) olunca bu uyarılar e-postayla
  gönderilmez ve **tek** `R20-email` uyarısı açılır.
- Bir çökme gönderim ile kaydı arasına denk gelirse aynı e-posta **en çok bir kez** yinelenir.
- Saatlik sınır aşılınca uyarılar `suppressed` olur; sınırın izin verdiği ilk e-posta kaç uyarının
  (hangi kurallardan) gönderilmediğini söyler.
- Yalnızca sorunu son 24 saat içinde görülmüş uyarılar e-postayla gider — sorunun başladığı an,
  `alerts.jsonl` dosyasındaki bir tekrar ya da sorunu yeniden bulan bir denetim (en çok saatte
  bir kaydedilir, `details.seen_at`): eski `alerts.jsonl` satırlarının ilk aktarımı geçmişi
  e-postayla göndermez, sürmekte olan bir sorun ise daha önce başlamış olsa da (veritabanı ya da
  worker kesintisinden sonra, e-posta sonradan kurulduğunda) e-postayla gider.

## 7. Sahada doğrulanacaklar

- SMTP sunucusu, portu ve güvenlik türü ([ASSUMPTIONS.md](ASSUMPTIONS.md) A8, E1–E6); sunucu bir
  şirket CA'sı kullanıyorsa `ca_file`.
- E-posta sağlayıcısı şirket dışındaysa: e-posta kaynak adları, günler, sayılar ve gerektiğinde
  her uyarı için bir IP adresi (yeni gönderici, taşıma yolu izin verilmeyen gönderici, IP
  çakışması, adında IP adresi olan kaynak; bölüm 5) içerir — bir e-posta birden çok uyarıyı
  listeleyebilir (bölüm 6) —, log içeriği içermez; KVKK açısından hukuk müşaviriyle
  değerlendirin.

## 8. Güvenlik sayfası: durumlar, notlar, kim değiştirir

Uyarılar web arayüzünde **Güvenlik** sayfasındadır: liste (durum, önem ve kural süzgeçleri, arama
başlıkta, kural kodunda ve kaynak adında), ayrıntı ve **Güvenlik özeti** (önem derecesine göre
açık uyarılar, İnceleniyor sayısı, son 30 günde ortalama kapanma süresi). **Panel**'deki "Açık
güvenlik uyarıları" kutusu en yeni beş etkin uyarıyı gösterir.

| Durum (kod) | Anlamı |
|---|---|
| **Yeni** (`open`) | Denetim açtı, henüz kimse ilgilenmedi |
| **İnceleniyor** (`acknowledged`) | Bir kişi ilgileniyor; uyarı etkin kalır, aynı sorun için yeni uyarı açılmaz, sorun geçince denetim onu kendisi kapatır |
| **Kapandı** (`resolved`) | Sorun giderildi — bir kişi ya da denetimin kendisi kapattı ("kendiliğinden kapandı" işareti) |
| **Yanlış alarm** (`dismissed`) | Gerçek bir sorun değildi |

İzin verilen geçişler: Yeni → İnceleniyor, Kapandı ya da Yanlış alarm; İnceleniyor → diğer üçü;
Kapandı ↔ Yanlış alarm; Kapandı ya da Yanlış alarm → Yeni (**yeniden aç**). Kapanmış bir uyarı
doğrudan İnceleniyor yapılamaz: önce yeniden açın.

- **Kim değiştirir:** bu sürümde yalnızca **Yönetici** uyarıları görür ve değiştirir
  (`alerts.read`, `alerts.manage`; [roles.md](roles.md)). Denetçi Panel'de mühürleme, kontör ve
  saat kutularını görür, uyarıları görmez.
- **Notlar:** her durumda not eklenebilir (1–2000 karakter, satır sonları serbest); durum
  değiştirirken de bir not yazılabilir. Notlar değiştirilmez ve silinmez; düzeltme yeni bir
  nottur. Not kişisel veri içerebilir (KVKK): yalnızca gerekeni, olgusal olarak yazın.
- **Zaman çizelgesi:** her durum değişikliği ve her not, kimin yaptığı ve ne zaman yapıldığıyla
  kaydedilir. Denetimlerin kendiliğinden kapatması ve yeniden açması `system` olarak, neden
  koduyla görünür (ör. `messages_again`: kaynak yeniden gönderiyor; `condition_cleared`,
  `below_threshold`, `credit_ok`, `job_ok`, `backup_ok`, `verified_again`, `sealed`,
  `email_sent`, `lease_rebuild`, `conflict_found_again`; lisans uyarılarında `licensed`,
  `license_changed`, `limit_ok`, `maintenance_renewed`).
- **Denetim kaydı:** her durum değişikliği `alert.state` (önceki ve yeni durum), her not
  `alert.note` olarak yazılır. Denetim kaydında notun **yalnızca uzunluğu** vardır, metni yoktur;
  notlar e-postaya da girmez.
- **Sorun sürerken kapatmak:** tekilleştirme anahtarı yalnızca **etkin** (Yeni, İnceleniyor)
  uyarılar arasında tektir. Sorun sürerken bir uyarıyı Kapandı ya da Yanlış alarm yaparsanız
  denetimin bir sonraki çalışması (R15 dakikada bir, R20 beş dakikada bir) **yeni bir uyarı**
  açar; yüksek ve kritik uyarılar için yeni bir e-posta da gider. Kapattığınız uyarı o zaman
  yeniden açılamaz ("Aynı sorun için etkin bir uyarı var (#…)"): yeni uyarıyla devam edin.
  Bu yüzden sorunla uğraşırken uyarıyı **İnceleniyor** yapın; sorun giderilince denetim onu
  kendisi kapatır. Lisans uyarıları bunun istisnasıdır (`R20-license`, `R20-license-limit`,
  `R20-license-maintenance`): bir lisans durumu sürekli olabilir (yalnızca 5651 Temel ile çalışan
  bir kurulum), bu yüzden bir kişinin kapattığı uyarı aynı durum için yeniden açılmaz (e-postası da
  gitmez), yalnızca bir sonraki için (başka bir lisans durumu, sınırın yeniden aşılması, başka
  tarihler); Güvenlik sayfası bunu lisans uyarısının düğmelerinin yanında söyler.
- **Kendiliğinden kapanmayanlar** (`R20-spool`, replika farklı, `R20-seal-skip`,
  `R20-spool-torn`, `R20-retention`, `new-log-source`, `new_source`, `wrong-transport`):
  inceledikten sonra nedeni not olarak ekleyip Kapandı ya da Yanlış alarm yapın. `ip_conflict`
  çakışma gerçekse açık kalır; yalnızca o günün yeniden hesaplanması geç gelen kanıtla çakışmayı
  artık bulmazsa (Yeni ya da İnceleniyor iken) kendiliğinden kapanır.
- **Ayrıntı:** uyarının ayrıntıları (gizli değer gibi görünen alanlar gizlenir, e-posta adresleri
  `a***@firma.com.tr` biçiminde — SMTP yanıtı gibi metinlerin içinde, uyarının başlığında ve
  özetinde de; `R20-email` ve `R20-report` alıcıyı adlandırır), ne yapılacağı (adım adım, bölüm
  2'deki öneriyle aynı) ve e-posta teslimleri (alıcı maskeli, durum, deneme sayısı). Tam
  adresler `alerts.email_recipients` ayarında ve Raporlar sayfasında zamanlamanın alıcılarındadır.
- **Ortalama kapanma süresi:** şu an kapalı olan ve son kapanışı son 30 gün içinde olan
  uyarıların, açılmalarından son kapanışlarına (Yeni ya da İnceleniyor durumundan Kapandı ya da
  Yanlış alarm'a geçiş; Kapandı ↔ Yanlış alarm değişikliği kapanış sayılmaz) kadar geçen sürenin
  ortalaması (kişinin ve denetimin kapattıkları birlikte). Bu sürümden önce kapanmış uyarıların
  zaman çizelgesi yoktur, sayılmaz.
