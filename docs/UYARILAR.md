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
Temel'e aittir: her lisans durumunda çalışır, yalnızca lisansı bildirir. `R20-lease` (Kira
kanıtı sorunu) ve `R20-nat` (İnternet çıkışı tanımı sorunu) neden başına ayrı birer uyarıdır;
e-postada nedenin aşağıdaki tek satırlık eylemi, sayılar ve varsa kanıt tablosunun adı ya da
VLAN numarası yer alır; Güvenlik sayfası ayrıca ayrıntıları ve ne yapılacağını gösterir.

| Kod | Anlamı | Önem | Eşik (ayar) | Ne yapılmalı |
|---|---|---|---|---|
| `R15` | **Log kaynağı sessiz:** etkin, beklenen, yapılandırılmış bir kaynaktan kalp atışı süresinden uzun süredir mesaj yok (5651 açısından kritik). Hiç mesaj göndermemiş bir kaynak, kaydı oluştuktan `alerts.r15_never_seen_minutes` sonra uyarır. Worker başladıktan, kaynağın kaydı oluştuktan ya da ilk mesajından sonraki `alerts.r15_startup_grace_minutes` içinde uyarı yoktur. Kaynak yeniden mesaj gönderince kendiliğinden çözülür | Yüksek | `[[sources]] heartbeat_minutes` (varsayılan 15; örnek yapılandırmanın yorum satırı hâlindeki kaynaklarında güvenlik duvarı 10, ana yönlendirici 15; yeni bir kurulumdaki gibi hiç kaynak yoksa R15 hiçbir kaynağı izlemez); `alerts.r15_never_seen_minutes` (60), `alerts.r15_startup_grace_minutes` (15) | Cihaz açık mı, ağa bağlı mı, syslog ayarı ARCILOGGER'ı gösteriyor mu ([DEVICE-SETUP.md](DEVICE-SETUP.md)); toplayıcı çalışıyor mu (`Get-Service ArciCollector`) |
| `R20-seal` | Bir mühürleme denemesi başarısız (`alerts.jsonl`) ya da kapanmış bir gün ertesi sabah hâlâ mühürlenmemiş (gün başına bir uyarı). Gün mühürlenince çözülür | Yüksek (48 saatten sonra kritik) | `alerts.seal_overdue_hour` (09:00) | **Mühürleme** sayfası, `D:\ARCILOGGER\logs\core.log`; Kamu SM erişimi ve kontör |
| `R20-seal-config` | Seal görevi mühürlemeye **hiç başlayamadı**: Kamu SM kimlik bilgileri yok, `kamusm-roots` klasöründe kök yok, `[tsa] zamane_jar` ayarlı değil, gizli depo anahtarı okunamıyor, Java ya da Zamane istemcisi güvenilir değil (`executable`: ör. kurulumdan sonra `zamane\` içine elle kopyalanan bir jar; kurulumun `-ZamaneJar` seçeneğiyle kurun ya da Ayarlar → Kamu SM'den yükleyip onaylayın, [KURULUM.md](KURULUM.md) bölüm 7) ya da `secrets_dir` / `data_dir` kurulumun klasörü değil (`location`: sunucudaki `arcilogger.toml` dosyasını denetleyin). Görev 5 dakikada bir çalıştığı için gün ve neden başına **bir kez** yazılır. Sonraki başarılı mühürden sonra çözülür | Yüksek | — | Eksik olanı tamamlayın ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3, [kamusm.md](kamusm.md)) |
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
| `R20-backup` | Son veritabanı yedeği başarısız, son başarılı yedek `alerts.backup_stale_hours` saatten eski (`state\backup\last.json`) ya da yedek alındı ama durum dosyası yazılamadı; uyarıdan sonra başarılı olan bir yedekle çözülür. `R20-backup:missing`: kurulumdan `alerts.backup_missing_hours` saat sonra hâlâ hiç yedek yok (orta). `R20-backup:prune`: eski yedekler silinemedi (orta); sonraki başarılı bir yedekten bir saat sonra, o yedek bunu yeniden bildirmediyse çözülür. `R20-backup:clock`: yeni yedeğin adı klasördeki bir yedekten eski (saat geri alınmış; yedek saklanır, orta); sonraki başarılı bir yedekle çözülür. `[backup] enabled = false` iken (worker yapılandırmayı birkaç saniye içinde yeniden yükler) yedek uyarısı açılmaz, açık olanlar çözülür | Yüksek / orta | `alerts.backup_stale_hours` (36), `alerts.backup_missing_hours` (48) | Yedekleme görevi ve yedek klasörü ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 10) |
| `R20-filter` | **İçerik filtresi kaydı gelmiyor:** etkin, yapılandırılmış (bilinmeyen olmayan) bir FortiGate kaynağı varken son `alerts.filter_quiet_hours` saatte hiç web ya da DNS filtresi kaydı (`fg_web`, `fg_dns`) gelmedi ve bu sürenin dokunduğu her site günü beklenen bir gün (hafta günü `alerts.filter_days` içinde; `alerts.filter_skip_holidays` açıkken `usage.holidays` günleri beklenmez); iki türden biri yeter. Uyum Durumu'nun 7. kartıyla aynı koşul ([UYUM-DURUMU.md](UYUM-DURUMU.md)). Tek uyarı (`R20-filter:none`); e-posta yalnızca saat sayısını söyler, adres ya da kaynak adı içermez. Yeni bir içerik filtresi kaydı gelince **ya da** süre beklenmeyen bir güne (tatil ya da `alerts.filter_days` dışındaki bir gün) değince kendiliğinden çözülür; kayıt hâlâ gelmiyorsa sonraki beklenen günde yeni bir uyarı açılır | Yüksek | `alerts.filter_days` (her gün), `alerts.filter_skip_holidays` (açık), `alerts.filter_quiet_hours` (24) | 1. Uyum Durumu sayfasında 7. kartı açın: son kaydı ve beklenen günleri görün. 2. Güvenlik duvarında web ve DNS filtresi kullanan politikalarda kayıt tutmanın açık olduğunu ve kayıtların ARCILOGGER'a gönderildiğini denetleyin ([DEVICE-SETUP.md](DEVICE-SETUP.md); sahada doğrulayın). 3. Kurum bu günlerde çalışmıyorsa beklenen günleri ve tatil ayarını Ayarlar → Uygulama ayarları → Uyarılar ve e-posta'da düzeltin. 4. Yeni bir içerik filtresi kaydı gelince uyarı kendiliğinden kapanır. |
| `R20-email` | Uyarı e-postası gönderilemedi (6 denemeden sonra ya da kalıcı 5xx ret). **Tek** yerel uyarıdır, kendisi e-postayla gönderilmez; adı geçen her alıcıya bir e-posta ulaşınca ya da gönderilemeyen adresler `alerts.email_recipients` listesinden çıkarılınca çözülür | Yüksek | — | `[smtp]`, `smtp.password`, güvenlik duvarı; `alerts test-email` |
| `R20-report` | Zamanlanmış bir kullanım raporu üretilemedi ya da bir alıcıya gönderilemedi (6 denemeden sonra ya da kalıcı 5xx ret; [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md)). Tek yerel uyarıdır; adı geçen alıcılara sonraki rapor gidince (ya da adres bütün zamanlamalardan çıkarılınca) ve sonraki rapor üretilince çözülür. **Zamanlanmış kullanım raporları** modülüne aittir: modül lisansta kapalıyken açılmaz; modül kapanınca açık olan uyarı "Zamanlanmış raporlar lisansta kapalı / Scheduled reports are off in the licence" notuyla kendiliğinden çözülür | Orta | — | Raporlar sayfasında çalıştırmanın hatası; `[smtp]`, `alerts test-email` |
| `R20-license` | **Ücretli modüller kapanıyor ya da kapalı** (lisans denetimi, 5 dakikada bir, lisansın güvenilen zamanına göre: [LISANS.md](LISANS.md) bölüm 10): `R20-license:trial-ending` deneme süresinin bitmesine en çok 3 gün kaldı; `R20-license:expiring` süreli bir lisansın bitmesine en çok 14 gün kaldı; `R20-license:off` lisans durumu yüzünden ücretli modüller kapalı (`trial_expired`, `invalid`, `wrong_server`, `expired`, `version_not_covered`) — yalnızca denemenin ya da lisansın verdiği bir modül kapalıyken (yalnızca 5651 Temel içeren bir lisans uyarı açmaz). Uyarı modülleri adlandırır; 5651 işlevleri her durumda çalışır. Lisans geçerli olunca (`licensed`) ya da durum değişince (`license_changed`: ör. deneme bitince denemenin uyarısı çözülür, `R20-license:off` açılır) kendiliğinden çözülür | Yüksek: varsayılan `alerts.email_min_severity` ile ücretli modüller (zamanlanmış kullanım raporları da) kapanmadan önce ve kapandığında e-postayla gider, her uyarı için bir kez | 3 gün (deneme), 14 gün (`valid_until`) | Ayarlar → Lisans: istek kodunu gönderin, yeni `.lic` dosyasını yükleyin ([LISANS.md](LISANS.md) bölüm 4); sunucu değiştiyse yeni lisans (bölüm 9) |
| `R20-license-limit` | Kullanıcı (`R20-license-limit:users`: yeni kullanıcı eklenemez) ya da log kaynağı (`R20-license-limit:log_sources`: kayıtlar toplanmaya ve mühürlenmeye devam eder) sayısı lisans sınırını aşıyor; aşımın lisans olayından (`license.limit_exceeded`) itibaren. Kullanım sınırın içine dönünce (`limit_ok`) çözülür | Orta | lisansın `limits` değerleri | Kullanılmayan kullanıcıları devre dışı bırakın ya da daha yüksek sınırlı bir lisans isteyin; bir kaynak son logundan 30 gün sonra sayılmaz ([LISANS.md](LISANS.md) bölüm 6) |
| `R20-license-maintenance` | Geçerli bir lisansın bakım süresi 30 gün içinde bitiyor ya da bitti (bilgi: yalnızca yükseltmeler ve destek; lisans ve kurulu sürüm geçerli kalır). Bakım yenilenince (`maintenance_renewed`) ya da lisans geçerliliğini yitirince (`license_changed`) çözülür | Bilgi | 30 gün | Bakımı yenileyin ([LISANS.md](LISANS.md) bölüm 7) |
| `R20-admin` | **Tarayıcıdan yönetim sorunu** ([AYARLAR.md](AYARLAR.md)). Ayrıntıdaki koda göre: imzası ya da kurulumu tutmayan bir istek reddedildi (`signature_invalid`, `installation_mismatch`: olası kurcalama); yönetim hizmeti ArciAdmin 2 dakikadan uzun süredir yanıt vermiyor ya da hiç yanıt vermedi (worker'ın denetimi, worker başladıktan 5 dakika sonra; tarayıcı yönetimi kapalıyken değil); `ApplySystem` planı alamadı ya da reddetti (`system_plan_failed`, `system_plan_invalid`) ya da güvenlik duvarını, saati veya Zamane istemcisini eşitleyemedi (`system_sync_failed`); politika ağlarının dışında bir log kaynağı (`source_outside_policy`) ya da adındaki SHA-256 ile uyuşmayan bekleyen bir Zamane istemcisi (`payload_mismatch`) buldu (yüksek); bir değişiklik geri alındı (orta); geri alma da başarısız oldu, elle müdahale gerekiyor (`rollback_failed`, kritik); bir değişiklik uygulandı, ancak ArciAdmin ayar dosyalarına yazma hakkını kaybetti ve hak onarılana kadar sonraki değişiklik başarısız olur (`acl_drift`, kritik; çoğunlukla konsoldan bir `secret set` sonrası). Uyarı metni kodun kendi Türkçe ve İngilizce açıklamasıdır. 5651 işlevleri (toplama, mühürleme) sürer. "Yanıt vermiyor" uyarısı hizmet yeniden yanıt verince kendiliğinden çözülür; diğerleri bir kişi kapatana kadar açık kalır | Orta … kritik | — | Ayarlar → **Değişiklik geçmişi** ve isteğin numarası; `Get-Service ArciAdmin`, Olay Görüntüleyicisi → Windows Günlükleri → Uygulama, kaynak `ARCILOGGER-Admin` ([AYARLAR.md](AYARLAR.md) bölüm 8); reddedilen istek için denetim kaydında isteği kimin hazırladığına ve sunucuya kimin erişebildiğine bakın; elle müdahalede (`rollback_failed`) isteğin gösterdiği adımlar, kurulumun Python'uyla `versions restore <n>`; `acl_drift` için değişiklik yerinde kalır: `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` betiğini ya da kurulumu onarım olarak yeniden çalıştırın, `versions restore` kullanmayın; politika ağlarının dışındaki bir kaynak için kurulumu `-SourceNetwork` ile çalıştırın ([AYARLAR.md](AYARLAR.md) bölüm 6) |
| `R20-config-change` | **Önemli yapılandırma değişikliği** (tarayıcıdan uygulanan ya da bekletilen bir değişiklik, `ApplySystem`'in onayladığı bir Zamane istemcisi): saklama açıldı ya da gün sayısı düştü, replika kapandı ya da hedefi değişti, bir log kaynağı kapatıldı ya da silindi, üretimden test sunucusuna dönüldü, Kamu SM kimlik bilgileri değişti, bir kök ya da yasal parmak izi eklendi, bir Zamane istemcisi yüklendi ya da onaylandı (yüksek); SMTP ya da yedekleme kapatıldı (orta). Metin yalnızca bölümü ve değişen alanların adlarını (en çok 10) söyler: "Yapılandırma değişti: E-posta (SMTP) (smtp.host)."; değer ve kullanıcı adı yoktur (bölüm 5). Kendiliğinden çözülmez | Yüksek / orta | `admin\policy.json` `hold_hours`, `jar_hold_hours` (bekletme) | Ayarlar → **Değişiklik geçmişi** sayfasında isteği, denetim kaydında (`admin.request`, `admin.system`) kimin yaptığını inceleyin. Bilinen bir yönetici yapmadıysa hemen: bekletilen bir değişikliği (saklama, replika) süresi dolmadan **İptal et** ile durdurun ya da **Geri al** ile önceki ayarlara dönün; ilgili kullanıcının parolasını değiştirin. Bilinen bir yöneticinin değişikliğiyse not ekleyip kapatın |
| `R20-lease:none:<tablo>` | **Kira kanıtı kesildi** (kanıt tablosu başına bir uyarı: `fg_dhcp`, `mt_arp`, `mt_dhcp`): FortiGate son bir saatte iç ağdan (10/8, 172.16/12, 192.168/16) trafik kaydediyor, ancak bu tablonun kullanılabilir hiçbir kaydı penceresinde gelmedi; önceki 30 günde geliyordu (kesilen bir besleme). Pencere 24 saattir; açık FortiGate kiraları varken `fg_dhcp` için en uzun kiranın yarısı artı 24 saat. `mt_dhcp` uyarısı yalnızca ARP-LOG kaydı da gelmiyorken açılır. Tablonun kullanılabilir bir kaydı gelince çözülür | Yüksek (`fg_dhcp`, `mt_arp`: kayıtları düzenli gelir); orta (`mt_dhcp`: RouterOS yenilemeleri kaydetmez, sessiz bir dönem de aynı görünür) | 24 saat; 30 günlük geçmiş | Kira kanıtı kesildi: bu kaynağın olay kayıtlarını syslog'a gönderdiğini denetleyin. Kaynaklar → Kira kanıtı kutusu; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2 |
| `R20-lease:absent` | **Kira kanıtı yok:** iç ağdan trafik var, ancak son 24 saatte ve önceki 30 günde hiçbir kanıt tablosunun kullanılabilir kaydı yok (kira kanıtı göndermeyen, zincirin iç IP adresinde bittiği kurulumlar). Kullanılabilir bir kayıt gelince çözülür | Bilgi | — | Kira kanıtı yok: 5651 sorguları iç IP adresinde biter (Kurulum kılavuzu bölüm 0.1). [KURULUM.md](KURULUM.md) bölüm 0.1 |
| `R20-lease:fortigate-refused` | **FortiGate DHCP kayıtları kullanılmıyor:** `dhcp.fortigate_logids` boş değilken son 24 saatin FortiGate DHCP kayıtlarından bazıları kira kanıtı olarak alınmadı: güvenilmeyen gönderici (`untrusted`), eşlenmemiş logid (`unmapped`), beklenmeyen `dhcp_msg` (`unexpected_msg`), cihaz saati olmayan kayıt (`receive_time`), geçersiz MAC (`bad_mac`). Hariç tutulan arayüzler ve IPv6 kayıtları yapılandırmadır, reddedilmiş sayılmaz (`excluded`). Ayrıntılar neden başına sayıyı, en çok beş değeri (logid, `dhcp_msg`, kaynak adı) ve `first_day`'i (son 100 günde alınmayan kaydı olan en eski gün) verir; değerler yalnızca Güvenlik sayfasında görünür. Güncel ayarlarla son 24 saatte alınmayan kayıt kalmayınca çözülür | Orta; en az 10 kayıttan %10'undan azı kullanılabiliyorsa yüksek | `dhcp.fortigate_logids`, `dhcp.fortigate_dhcp_msgs`, `dhcp.fortigate_interfaces_excluded` | FortiGate DHCP kayıtları kullanılmıyor: uyarıdaki nedene göre ayarı düzeltip kiraları yeniden oluşturun. Kiraları `first_day`'den bugüne yeniden oluşturun: Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur (bir istekte en çok 31 gün) |
| `R20-lease:fortigate-unconverted` | **FortiGate DHCP olay kaydı kira kanıtına çevrilemedi:** son 24 saatte DHCP logid'iyle gelen, ancak `ip`, `mac` ya da `dhcp_msg` alanı olmayan kayıtlar, ya da son iki günde `fortigate.dhcp` ayrıştırma hataları. Bu adresler için 5651 sorguları iç IP adresinde durabilir. 24 saat sorunsuz geçince çözülür | Yüksek | `dhcp.fortigate_logids` | FortiGate DHCP kayıtları kira kanıtına çevrilemedi: kayıt biçimini sahada doğrulayın. Ham satırı Canlı Akış ya da Arama'da açın ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) |
| `R20-lease:fortigate-renewals-missing` | **FortiGate DHCP yenilemeleri görünmüyor:** son 24 saatte süresi dolarak biten FortiGate kiralarının adresleri kiranın ikinci yarısında ve bitişinden sonra yeni oturumlar açmaya devam etti (en az 3 kira ve FortiGate kiralarının en az %5'i), son 7 günün kira çalışmalarında hiç yenileme ACK'i yok ve kayıt biçimi doğrulanmadı ya da yenilemeler önceden görülüyordu. 24 saat sorunsuz geçince çözülür | Yüksek | `dhcp.fortigate_format_verified` | FortiGate DHCP yenilemeleri görünmüyor: önce kullanılmayan kayıtlara, sonra yenileme denetimine bakın. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4 (yenileme denetimi) |
| `R20-lease:fortigate-renewals-not-logged` | **FortiGate DHCP yenilemeleri bu kurulumda kaydedilmiyor:** aynı durum, son 30 günde hiç yenileme ACK'i yok ve `dhcp.fortigate_format_verified` açık (sahada kabul edilen kurulum). 24 saat sorunsuz geçince ya da bir yenileme görülünce çözülür | Bilgi | `dhcp.fortigate_format_verified` | FortiGate DHCP yenilemeleri bu kurulumda kaydedilmiyor: zincirler kira bitiminde biter (Kurulum kılavuzu bölüm 16). [KURULUM.md](KURULUM.md) bölüm 16 |
| `R20-lease:fortigate-decline:<ağ>` | **DHCP havuzunda statik adresli bir cihaz olabilir:** son 24 saatte bir adresin en az iki FortiGate kirası, başladıktan sonraki 300 saniye içinde aynı MAC aynı ağda başka bir adres alınca bitti (istemci adresi reddetti). Anahtar ve başlık ağı (`<ağ>`: VLAN numarası, yoksa alt ağ, `/` yerine `_`) ve sayıyı adlandırır; e-posta yalnızca VLAN numarasını taşır, adresi asla; en çok beş adres yalnızca Güvenlik sayfasında görünür. 24 saat sorunsuz geçince çözülür | Yüksek | — | DHCP havuzunda statik adresli bir cihaz olabilir: cihazı havuzun dışına taşıyın ya da adresini ayırın. Adres, uyarının ayrıntılarında (Güvenlik sayfası) |
| `R20-lease:dhcp-sources-overlap:<ağ>` | **Aynı ağda iki DHCP kaynağı:** 24 saat içinde tanımlı bir ağın kullanılabilir DHCP kayıtları iki kaynaktan geldi (MikroTik DHCP ve FortiGate DHCP ya da iki FortiGate VDOM'u); iki DHCP yetkilisi desteklenmez. 24 saat sorunsuz geçince çözülür | Yüksek | — | Aynı ağda iki DHCP kaynağı: fazla DHCP sunucusunu kapatın ya da ağları ayırın. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2 |
| `R20-lease:fortigate-lease-unlimited` | **FortiGate süresiz kira veriyor:** son 24 saatin kullanılabilir FortiGate DHCP kayıtlarında `lease=0`; bu kiralar ayarlardaki süreyle biter. 24 saat sorunsuz geçince çözülür | Yüksek | `lease.default_seconds`, `lease.server_seconds` | FortiGate süresiz kira veriyor: DHCP sunucusunda sınırlı bir lease-time ayarlayın. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4 |
| `R20-lease:source-clock` | **Bir log kaynağının saati kayık:** bugünün ya da dünün dosyalarında trafik ya da kira kaydı olan etkin bir kaynağın son bir saatte alınan en yeni 200 mesajında (en az 10) cihaz saati ile alınma zamanı farkının ortancası ±120 saniyeyi aşıyor. Ayrıntılar en çok beş kaynak adını ve saniye cinsinden kaymalarını verir. Sınırı aşan kaynak kalmayınca çözülür | Yüksek | 120 saniye | Bir log kaynağının saati kayık: cihazda NTP eşitlemesini açın. Cihazın saat ayarı ve NTP sunucusu |
| `R20-lease:fortigate-dhcp-mac` | **Bir MAC adresi tek bir ağda çok sayıda FortiGate adresi aldı:** son 24 saatte bir MAC, aynı kaynak, arayüz ve ağda 3'ten fazla adres için ACK aldı; ayrıntılar bu MAC'lerin sayısını verir, MAC'leri değil. 24 saat sorunsuz geçince çözülür | Orta | — | Bir MAC adresi tek bir ağda çok sayıda FortiGate adresi aldı: arayüz yapısını denetleyin. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4 |
| `R20-lease:srcmac-shared:<arayüz>` | **srcmac arayüzünde paylaşılan MAC adresi:** `lookup.srcmac_interfaces` listesindeki bir arayüzün son bir saatlik trafiğinde bir `srcmac`, `lookup.srcmac_max_ips` değerinden fazla IPv4 adresinden geldi (ör. arada bir yönlendirici var). 24 saat sorunsuz geçince çözülür | Orta | `lookup.srcmac_interfaces`, `lookup.srcmac_max_ips` (1) | srcmac arayüzünde paylaşılan MAC adresi: arayüzü srcmac listesinden çıkarın. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.6 |
| `R20-lease:fortigate-lease` | **FortiGate kira süresi beklenmeyen bir değerde:** son 24 saatin kullanılabilir kayıtlarında `lease` 300 saniyeden kısa ya da 100 günden uzun (0 dışında); bu kiralar ayarlardaki süreyle biter. 24 saat sorunsuz geçince çözülür | Düşük | `dhcp.fortigate_use_logged_lease` | FortiGate kira süresi beklenmeyen bir değerde: lease alanının birimini sahada doğrulayın. [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4 |
| `R20-lease:fortigate-dhcp-subnet` | **FortiGate DHCP kaydı tanımlı bir ağda değil:** son 24 saatin kullanılabilir FortiGate DHCP kayıtlarından bazılarının adresi tanımlı hiçbir ağda (Cihazlar ve Kişiler → Departmanlar ve VLAN'lar) değil; bu adreslerin zincirleri düşük güvenle gösterilir. Ayrıntılar en çok beş arayüz adını ve `first_day`'i (son 100 günde böyle bir kaydı olan en eski gün) verir. 24 saat sorunsuz geçince çözülür | Düşük | `dhcp.fortigate_interfaces_excluded` | FortiGate DHCP kaydı tanımlı bir VLAN'a ait değil: ağı (VLAN numarası olmasa da) ekleyip kiraları yeniden oluşturun ya da arayüzü hariç tutun. Kiraları `first_day`'den bugüne yeniden oluşturun: Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur (bir istekte en çok 31 gün); [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5 |
| `R20-lease:fortigate-unverified` | **FortiGate DHCP kayıt biçimi sahada doğrulanmadı:** son 7 günde FortiGate DHCP kaydı var ve `dhcp.fortigate_format_verified` kapalı. Ayar açılınca çözülür | Bilgi | `dhcp.fortigate_format_verified` | FortiGate DHCP kayıt biçimi sahada doğrulanmadı: DEVICE-SETUP.md bölüm 2.4'teki doğrulamayı yapın. Doğruladıktan sonra ayarı açın |
| `R20-nat:conversion-failed` | **Eski NAT ayarları aktarılamadı:** yükseltme eski NAT ayarlarını (`nat.public_ips`, `nat.wan_transip`, `lookup.port_hint`) internet çıkışlarına çeviremedi ve `nat.egresses` hâlâ yükseltmenin yazdığı değerde. Bir yönetici `nat.egresses`'i yazınca çözülür | Yüksek | — | Eski NAT ayarları aktarılamadı: internet çıkışlarını tanımlayın. Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT); [KURULUM.md](KURULUM.md) bölüm 13 |
| `R20-nat:invalid` | **İnternet çıkışları ayarı okunamıyor:** kayıtlı `nat.egresses` değeri doğrulanamıyor; ayrıntı yalnızca kuralın hata kodunu verir, değeri asla. Kira uyarıları çalışmaya devam eder. Değer okunabilir olunca çözülür | Yüksek | — | İnternet çıkışları ayarı okunamıyor: genel IP ile 5651 sorguları çalışmaz; ayarı düzeltin. Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) |
| `R20-nat:legacy-changed` | **Geri almada değiştirilen eski NAT ayarları çıkışlara aktarılmadı:** bir yöneticinin eski NAT satırı (`nat.public_ips`, `nat.wan_transip`, `lookup.port_hint`) `nat.egresses`'ten sonra değişti (geri almadan sonra önceki sürümde); ayrıntılar anahtarların adlarını verir. Bir yönetici `nat.egresses`'i yazınca çözülür | Orta | — | Geri almada değiştirilen eski NAT ayarları çıkışlara aktarılmadı: değişikliği çıkışlara elle işleyin. Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT); [KURULUM.md](KURULUM.md) bölüm 13 |
| `R20-nat:unmapped-transip` | **Hiçbir çıkışa tanımlı olmayan NAT adresi kullanılıyor:** çıkışlar tanımlıyken son bir saatin güvenilen trafiğinde hiçbir çıkışın sahiplenmediği bir `transip` (listelenen bir `trandisp` ile) en az 50 oturum taşıdı. Ayrıntılar sayıyı ve en çok beş adresi (güvenlik duvarının kendi adresleri; özel/paylaşılan ya da genel diye işaretli) yalnızca Güvenlik sayfasında verir. Bir çıkış her adresi sahiplenince ya da 24 saat sorunsuz geçince çözülür | Orta | `nat.snat_trandisp` | Hiçbir çıkışa tanımlı olmayan NAT adresi kullanılıyor: hattı çıkış olarak tanımlayın. Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) → "Kayıtlarda görülen NAT adresleri" → "Çıkış olarak ekle"; DMZ, başka bir VLAN, VPN ya da şube NAT'ı için "İnternet çıkışı değil" türü |
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
| `alerts.filter_days` | `[1,2,3,4,5,6,7]` | 1–7 | R20-filter ve Uyum Durumu kart 7: içerik filtresi kaydı beklenen ISO hafta günleri (1 = Pazartesi, 7 = Pazar; boş olabilir) |
| `alerts.filter_skip_holidays` | `true` | — | R20-filter ve kart 7 — açıkken `usage.holidays` listesindeki günlerde kayıt beklenmez; kapalıyken resmî tatiller de beklenen gün sayılır |
| `alerts.filter_quiet_hours` | `24` | 6–168 | R20-filter ve kart 7: beklenen günlerde kayıtsız geçebilecek en uzun süre (saat) |

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
   Worker değişen `[smtp]` bölümünü birkaç saniye içinde kendisi yükler (bu sürümden önceki bir
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
  modül adları, yapılandırmanın bölümü ve değişen alanlarının adları ya da aşağıdaki IP
  adreslerinden biri), ne yapılacağı (bir satır), ARCILOGGER web
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
- Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını kaydettiği ve ARCILOGGER'a
  gönderdiği (`R20-filter`, Uyum Durumu kart 7; [DEVICE-SETUP.md](DEVICE-SETUP.md)).

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
