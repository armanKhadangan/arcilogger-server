# Ayarlar: sunucu ayarlarını tarayıcıdan değiştirmek

> English: [SETTINGS.md](SETTINGS.md). Karar: [adr/0014-browser-administration.md](adr/0014-browser-administration.md).
> Kurulum: [KURULUM.md](KURULUM.md); güvenlik ayrıntıları: [SECURITY.md](SECURITY.md) bölüm 14.
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz.

Bu belge web arayüzünün **Ayarlar** sayfalarıyla sunucunun yapılandırmasının (`arcilogger.toml`,
gizli depo, Kamu SM kök sertifikaları, Zamane istemcisi, güvenlik duvarı, Windows Saati) nasıl
değiştirildiğini, neyin bilerek yalnızca sunucu konsolunda kaldığını ve bir şey ters gittiğinde ne
yapılacağını anlatır. Kurulum yine sunucu konsolundadır; günlük ayar değişiklikleri için Uzak
Masaüstü gerekmez.

## 1. Ne tarayıcıdan değişir?

**Ayarlar** sayfaları yalnızca **Yönetici** rolüne açıktır (`settings.manage`; **Kaynaklar** için
ayrıca `sources.manage`). **Mühürleme** sayfasındaki **Şimdi mühürle** ve **Kontör sorgula**
`seal.manage` ister. Her sayfa yalnızca aşağıdaki değerleri değiştirir; başka her alan kilit
simgesiyle gösterilir ("Bu değer güvenlik nedeniyle yalnızca sunucu konsolundan
değiştirilebilir", bölüm 5). Sayfaların kullandığı API uçları bölüm 10'dadır.

| Sayfa (adres) | Tarayıcıdan değişen | Bilerek konsolda kalan |
|---|---|---|
| **Genel** (`/settings/general`) | kuruluş adı (`site_name`), yerel ve yedek saat kaynağı (`[clock] ntp_server`, `[clock] fallback_server`), saat sapması uyarı eşiği (`[clock] alert_offset_ms`); son ölçülen sapma gösterilir, yeni saat sunucusu kaydetmeden önce denenir | `[clock] ntp_port` |
| **Uygulama ayarları** (`/settings/application`) | veritabanındaki ayarlar, gruplarıyla (uyarılar ve e-posta, 5651 sorgu ve kira zaman çizelgesi, arama ve canlı akış, kullanım analizi ile mesai saatleri ve tatiller, kullanım raporları, kanıt paketleri, oturum, parola ve gizlilik, veritabanı); 5651 sorgu grubunda **Kiraları yeniden oluştur** (en çok 31 gün) | dosya yolu veren `devices.oui_file`, `usage.psl_file` |
| **Kamu SM** (`/settings/kamusm`) | sihirbaz: Java (yönetim hizmetinin sunucuda bulduklarından biri), Zamane istemcisi (yükleme), kök sertifikalar (ekleme) ve yasal kök parmak izleri (yalnızca kurulu kökler için), müşteri numarası ve parola (kaydederken kontör sorgusuyla denenir), **Damga testi (1 kontör harcar)**, **Üretime geç** (ve teste dönüş), kontör uyarı eşiği ve zaman aşımı; `[seal]` yalnızca okunur | deneme (`mock`) ya da başka bir sağlayıcı, serbest adres veya port, özet algoritması, bir kökü ya da parmak izini kaldırmak, serbest bir `java` yolu, bilinmeyen bir Zamane sürümünün onayı (`approve-jar`, bölüm 4) |
| **Kaynaklar** (`/settings/sources`) | log kaynağı ekleme, düzenleme (ad değişmez), açma/kapama (`[[sources]] enabled`), silme (adı yazılarak); tabloda son kayıt ve güvenlik duvarı durumu | `[collector]` bölümünün tamamı (dinleme adresi, portlar, TLS, `unknown_senders`) |
| **E-posta** (`/settings/email`) | `[smtp]` (açık/kapalı, sunucu, port, güvenlik, kullanıcı, gönderen, zaman aşımı), SMTP parolası, sunucu sertifikası için CA dosyası (yükleme ya da Windows sertifika deposuna dönüş), uyarı e-postası ayarları (`alerts.email_*`: alıcılar, en düşük önem, saatlik sınır …); kaydetmeden önce deneme e-postası; **Test e-postası gönder** (kayıtlı ayarlarla, saatte en çok 5) | — |
| **Replika** (`/settings/replica`) | açık/kapalı, hedef (yalnızca `\\SUNUCU\paylaşım`), kullanıcı, parola; kaydederken bağlantı denenir; **Bağlantıyı test et** (kayıtlı hedefle, dakikada en çok 1) | yerel bir klasör hedefi |
| **Saklama** (`/settings/retention`) | süresi dolan günleri silme açık/kapalı, saklama süresi (en az 730 gün); silinecek gün sayısı önceden gösterilir ve yazılarak onaylanır | — |
| **Yedekleme** (`/settings/backup`) | açık/kapalı, `[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly`, `[backup] min_free_gb`; son yedeğin ayrıntıları bu sürümde sayfada gösterilmez, başarısız bir yedek `R20-backup` uyarısıdır | `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore` |
| **Değişiklik geçmişi** (`/settings/history`) | isteklerin listesi ve durumları, değişiklikleri, denetim kaydına bağlantı; **İptal et**, **Geri al**; yapılandırma sürümleri; **Tanılama paketini indir** (destek için ZIP, gizli bilgi içermez) | bir sürümü geri yüklemek (`versions restore`, sayfa komutu gösterir) |

Hiçbir sayfa şunları değiştirmez: `data_dir`, `secrets_dir`, `[database]`, `[api]`, `[ingest]`,
`[worker]`, `[seal]`, veritabanı parolası, kullanım takma ad anahtarı. Bunlar kurulumun ve
yöneticinin işidir.

**ARP süreleri** (Uygulama ayarları, 5651 sorgu ve kira zaman çizelgesi): "Bırakmadan sonra ARP'yi
yok say" (`arp.ignore_after_release_seconds`, 300 saniye) "ARP kaydı aralığı"ndan
(`arp.interval_seconds`) uzun olamaz; tarayıcı da konsol da (`arcilogger-db settings set`) bunu
bozan değişikliği reddeder. Aralığı 300 saniyenin altına indirirken iki ayarı aynı kayıtta
değiştirin. Bu kuralın dışında (elle SQL ile) yazılmış bir süreyi kira işi varsayılan olarak
(300 saniye, en çok aralık kadar) okur ve worker günlüğüne bir uyarı yazar.

**Bir kaynağı kapatmak** (`enabled = false`): güvenlik duvarı o göndericinin 514 iznini kaldırır,
sessiz kaynak uyarısı (R15) ve mühürlemenin boşluk denetimi onu artık beklemez. Kapalı kaynaktan
yine de gelen bir ileti **kaydedilir** (ham kayıt hiçbir zaman atılmaz). Kapalı kaynak lisansın
kaynak sayısında sayılmaya devam eder. **Silinen** bir kaynak da aynı biçimde kapanır; veritabanındaki
satırı geçmiş için kalır, kapalı ve artık beklenmeyen olarak: R15 ve pano onu beklemez.

**Gizli değerler** (Kamu SM müşteri numarası ve parolası, SMTP ve replika parolası) hiçbir zaman
ekrana gelmez: alanlar "Kayıtlı ✓" ve son değişiklik zamanını gösterir, **Değiştir** ile yenisi
yazılır. Son 5 dakika içinde parolanın yeniden girilmesi şunlar için istenir: Kamu SM ayarları
(kontör eşiği ve zaman aşımı dışında; müşteri numarası ve parola dahil), her SMTP, replika,
saklama ve yedekleme değişikliği, saat kaynağı ve uyarı eşiği, bir log kaynağını kapatmak ya da
silmek; oturum ve parola (`auth.*`), gizlilik (`privacy.*`), saklama süreleri ve anonim rolün
takma adlandırılmasıyla ilgili uygulama ayarları, uyarı e-postası alıcıları ve en düşük önem
derecesi; bekletilen bir değişikliğin iptali, **Geri al** ve tanılama paketi
([SECURITY.md](SECURITY.md) bölüm 7).

**Arama ve canlı akış** grubu (`search.*`, `live.*`; [ADR-0015](adr/0015-search-live-sources.md))
Arama ve Canlı Akış sayfalarının sınırlarıdır: hiçbir arama ya da akış log yüklemeyle (ingest)
yarışmasın diye her isteğin bir süresi ve her kullanıcının bir payı vardır. Değerler başlangıç
değerleridir, ölçülmüş gerçekler değildir; hiçbir değer bir sınırı ya da süreyi kapatamaz. Her
istek ayarları yeniden okur (canlı akış, açıldığında):

| Ayar | Varsayılan | Sınırlar | Anlamı |
|---|---|---|---|
| `search.max_days` | 31 | 1–92 | bir aramanın en uzun aralığı (gün, cihaz zamanı) |
| `search.page_size_max` | 500 | 50–1000 | bir isteğin en çok satırı (web arayüzü 100 ister) |
| `search.request_budget_ms` | 4000 | 500–30000 | satır aramasının süresi; dolunca o ana kadar bulunanlar taranan aralıkla gösterilir |
| `search.stats_budget_ms` | 8000 | 1000–60000 | grafik ve dağılımların süresi |
| `search.facet_sample_rows` | 100000 | 1000–1000000 | doğrudan sayılan dağılımlar için en çok eşleşme |
| `search.max_concurrent` | 3 | 1–10 | aynı anda çalışan arama istekleri; `[database] pool_size` değerinin 2 eksiğini geçmemeli |
| `search.runs_per_minute` | 30 | 1–600 | kullanıcı başına dakikada yeni arama |
| `search.export_max_rows` | 100000 | 1–1000000 | bir dışa aktarma dosyasının en çok satırı |
| `search.export_max_per_hour` | 10 | 1–100 | kullanıcı başına saatte dışa aktarma |
| `search.export_budget_seconds` | 120 | 10–900 | bir dışa aktarma dosyasının hazırlanma süresi |
| `search.saved_max_per_user` | 100 | 1–1000 | kullanıcı başına kayıtlı arama (kişisel) |
| `live.max_streams_total` | 4 | 1–20 | sunucuda aynı anda açık canlı akış |
| `live.max_streams_per_user` | 2 | 1–10 | kullanıcı başına açık canlı akış |
| `live.max_lines_per_second` | 100 | 10–500 | akış başına saniyede gösterilen satır; fazlası sayılıp bildirilir |
| `live.parse_lines_per_second` | 1000 | 50–10000 | canlı akışlar için saniyede ayrıştırılan satır |
| `live.buffer_lines` | 5000 | 500–50000 | sürdürme için sunucuda tutulan en yeni satırlar |
| `live.max_minutes` | 60 | 5–480 | bir akışın süresi (gözetimsiz ekran); Sürdür ile yeniden başlar |
| `live.backfill_lines` | 50 | 0–200 | akış açılırken hemen gösterilen son satırlar |

## 2. Nasıl çalışır?

1. Sayfa önce değişikliği gözden geçirmek için gösterir (değişiklikler, etkileri, uyarılar;
   API'nin kuru denemesi, hiçbir şey kuyruğa alınmaz). **Kaydet**'e basınca API değişikliği
   aynı kurallarla denetler (alan sınırları, yetki, gerekiyorsa parolanın yeniden girilmesi) ve
   imzalı bir istek olarak `C:\ProgramData\ARCILOGGER\admin\inbox` klasörüne bırakır. API
   yapılandırma dosyasına, gizli depoya ve sertifika klasörlerine **yazamaz**.
2. **ArciAdmin** hizmeti (`NT SERVICE\ArciAdmin`, "ARCILOGGER Yönetim (Admin)") isteğin imzasını,
   süresini ve kurallarını yeniden denetler; gerekiyorsa yeni değerlerle bağlantıyı önce dener
   (Kamu SM kontör sorgusu, deneme e-postası, replika paylaşımı, saat sunucusu: "Bağlantı
   deneniyor"), önceki sürümü saklar (`admin\versions`), değişikliği yazar ve hizmetlerin yeni
   ayarı aldığını izler. Hizmetler değişikliği yeniden başlatılmadan birkaç saniye içinde alır
   (toplayıcı ve ingest log kaynaklarını, API ve worker kendi okudukları bölümleri); zamanlanmış
   görevler bir sonraki çalışmalarında.
3. Güvenlik duvarı, Windows Saati ve Zamane istemcisinin etkinleştirilmesi **SYSTEM** hesabıyla
   çalışan `\ARCILOGGER\ApplySystem` görevinin işidir. Görev istekten hiçbir veri almaz: her
   şeyi doğrulanmış `arcilogger.toml` dosyasından ve yalnızca yöneticilerin yazabildiği
   `admin\policy.json` dosyasından kendisi çıkarır, her değeri yeniden denetler; ArciAdmin onu
   yalnızca uyandırır (olay 4101), görev ayrıca 5 dakikada bir kendiliğinden çalışır. Güvenlik
   duvarında yalnızca `ARCILOGGER Syslog UDP/TCP` kuralları (sıkı giden kipte `ARCILOGGER Out …`
   kuralları) yerinde güncellenir; başka bir kural, profil, 443, 3389 ya da 5985 değişmez.
   Kaynak eklenince 514 izni, toplayıcı yeni kaynağı tanıdıktan sonra açılır; kaynak kapanınca
   ya da silinince hemen kapanır. Windows Saati'nin eş listesini görev ilk çalışmasında da
   `[clock]` değerlerinden kurar, kurulum `-SkipTimeSetup` ile yapılmış olsa bile; saati başka bir
   yolla (ör. etki alanı) yönetilen bir sunucuda tarayıcı yönetimini `-DisableBrowserAdministration`
   ile kapatın (bölüm 6).
4. **Şimdi mühürle** (Mühürleme sayfası) ve **Kontör sorgula** de aynı kanaldan geçer: ArciAdmin
   imzalı bir görev isteği yazar, `\ARCILOGGER\Seal` görevi (olay 4110) ya da `\ARCILOGGER\Requests`
   görevi (olay 4111, LOCAL SERVICE) işi yapar. Mühürleme her zamanki gibi kendi görevinde
   kalır; tarayıcı yalnızca en eski mühürlenmemiş günü öne alır.
5. Her uygulanan değişiklik denetim kaydına yazılır (`admin.request`, `admin.applied`, …;
   güvenlik duvarı, saat ve istemci değişiklikleri `admin.system`). Değerler maskelenir; gizli bir
   değer ya da onun bir özeti hiçbir zaman yazılmaz.

## 3. Durum göstergesi ve istek durumları

Ayarlar başlığındaki gösterge ArciAdmin'in durumunu söyler: **Yönetim hizmeti: çalışıyor /
gecikiyor / çalışmıyor / kapalı** (son yanıt 30 saniyeden yeni / 2 dakikadan yeni / daha eski ya
da hiç / tarayıcı yönetimi kapalı). Hizmet çalışmıyorsa ya da tarayıcı yönetimi kapatıldıysa
(bölüm 6) makine ayarları salt okunurdur ve sayfa konsoldaki yolu gösterir; uygulama ayarları
(veritabanı), test e-postası ve kiraların yeniden oluşturulması (worker) her zaman çalışır.
Mühürleme sayfasındaki **Şimdi mühürle** ve **Kontör sorgula** da ArciAdmin üzerinden çalışır ve o
sırada kullanılamaz: konsoldaki yol Görev Zamanlayıcı'daki `\ARCILOGGER\Seal` ya da
`\ARCILOGGER\Credit` görevidir (gece çalışmaları her zamanki gibi sürer).

Bir isteğin durumu sayfada izlenir. Önemli olanlar:

| Durum | Anlamı | Ne yapılır |
|---|---|---|
| **Yazıldı, hizmetler yüklüyor** | dosya yazıldı, hizmetler yeni ayarı alıyor | bekleyin (en çok 2 dakika) |
| **Uygulandı** | bütün hizmetler yeni ayarla çalışıyor | — ; "yönetim hizmeti ayar dosyalarına artık yazamıyor" uyarısıyla (`acl_drift`) değişiklik yürürlüktedir, ancak `Set-ArciVaultAcl.ps1` çalıştırılana kadar sonraki değişiklik başarısız olur (bölüm 8) |
| **Tamamlandı** | bir işlem (mühürleme, kontör sorgusu, test) bitti; sonucu istekte | — |
| **Bekletiliyor** | korumalı bir değişiklik, geri sayım bitince uygulanır (bölüm 4); bilinen bir sürüm olmayan Zamane istemcisi ise hiçbir zaman kendiliğinden uygulanmaz: sayfa konsolda ne zamandan itibaren onaylanabileceğini (`approve-jar`) gösterir | gerekirse **İptal et** |
| **Kısmen uygulandı** | bir hizmet yeni ayarı 2 dakikada almadı ya da güvenlik duvarı güncellenemedi | Hizmetler ve **Güvenlik** sayfası; ApplySystem bir sonraki çalışmasında yeniden dener |
| **Bağlantı denemesi başarısız, hiçbir şey değişmedi** | yeni değerlerle sunucuya bağlanılamadı | sayfadaki nedeni okuyun, değeri düzeltin |
| **Uygulanmadı, önceki ayarlar geçerli** | bir adım başarısız oldu, ArciAdmin önceki sürümü geri koydu | sayfadaki nedeni okuyun, düzeltip yeniden kaydedin; uygulanmış bir değişiklik için **Geri al** |
| **Elle müdahale gerekiyor** | bir adım başarısız oldu ve önceki sürümü geri koymak da başarısız oldu (`rollback_failed`) | sayfadaki adımlar (bölüm 8); sürüm numarasını not edin |
| **Çakışma: ayarlar başka yerden değişti** | dosya bu arada elle ya da başka bir istekle değişti | sayfayı yenileyip yeniden kaydedin |
| **Reddedildi** | ArciAdmin isteği kabul etmedi (imza, süre, kural) | nedeni okuyun; imza ya da kurulum hatası `R20-admin` uyarısıdır |
| **Süresi doldu, hiçbir şey değişmedi** | ArciAdmin isteği zamanında almadı | hizmetin durumuna bakın (bölüm 8) |
| **Zaman aşımı** | hiçbir görev (Seal, Requests) isteği 15 dakikada almadı; istek geri çekildi, hiçbir şey çalışmadı (görevin aldığı bir istek, ör. uzun süren bir Seal çalışması, görev çalışabildiği sürece beklenir) | görevin son sonucuna bakın (Görev Zamanlayıcı, `\ARCILOGGER\`) |

Kısa istek numarası destek için sayfada yazar.

## 4. Bekletilen değişiklikler ve Zamane istemcisi

Çoğu değişiklik hemen uygulanır. Şunlar **korumalıdır**: yazılı onay ister, `R20-config-change`
uyarısı (e-posta ayarlıysa e-postayla) ve Windows olay günlüğü kaydı (olay 4104) üretir:

| Değişiklik | Bekleme |
|---|---|
| saklama ve imhayı açmak ya da gün sayısını düşürmek | `admin\policy.json` `hold_hours` (varsayılan 0: hemen) |
| açık replikayı kapatmak ya da hedefini değiştirmek | `hold_hours` (varsayılan 0) |
| bilinen bir sürüm olmayan bir Zamane istemcisi | konsolda `approve-jar` ile, en erken `jar_hold_hours` (varsayılan 24) saat sonra |

Bekleme süresi kurulumda `-AdminChangeHoldHours` ve `-ZamaneHoldHours` ile seçilir (bölüm 6).
Bekleyen bir değişiklik **Değişiklik geçmişi** sayfasında ya da kendi sayfasında **İptal et** ile
durdurulur.
İlk kurulum adımları beklemez (replikayı ilk kez açmak, test → üretim geçişi).

**Zamane istemcisi:** Kamu SM sihirbazında yüklenen dosya önce `zamane\web` klasöründe bekler ve
oradan **hiçbir zaman** çalışmaz. Bilinen bir sürümse ApplySystem onu `zamane\approved` klasörüne
kopyalar (olay 4107). Bilinmeyen bir sürüm ise yalnızca kaydedilir (olay 4104) ve bir yönetici
SHA-256 değerini Kamu SM sayfasındakiyle karşılaştırdıktan sonra konsolda onaylar; süre tek
başına hiçbir istemciyi çalıştırmaz. Bu sürüm hiçbir Zamane sürümünü tanımaz: tarayıcıdan
yüklenen istemci her zaman `approve-jar` bekler, en erken yüklemeden `jar_hold_hours` sonra. Bu
arada sihirbazın öteki adımları (Java, kökler, kimlik bilgileri, eşik) kaydedilebilir; ikinci bir
istemci ise ancak ilki onaylanınca ya da iptal edilince. Beklememek için istemciyi kurulumun
`-ZamaneJar` seçeneğiyle kurun ([KURULUM.md](KURULUM.md) bölüm 7). Komut:

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
& $py -I -m arcilogger_core.admin approve-jar <SHA256> --config $cfg
```

## 5. Yalnızca sunucu konsolunda kalanlar

Bunlar bilerek tarayıcıya açılmaz; ya kanıt yolunu ya da sunucunun güvenliğini doğrudan
etkiler:

- kurulum, yükseltme, geri alma, kaldırma; PostgreSQL; veritabanı geri yükleme;
- `[database]`, `[api]`, `[collector]`, `[ingest]`, `[worker]`, `[seal]`, `data_dir`,
  `secrets_dir` (`data_dir`, kurulumun `C:\ProgramData\ARCILOGGER\data-root.txt` kaydıyla aynı
  olmalıdır; farklıysa hizmetler ve görevler yapılandırmayı reddeder ve kurulum ön denetimde
  durur; veri klasörünü bilerek taşımak için kurulumu `-DataRoot` ile çalıştırın);
- Zamane dışında bir sağlayıcı, serbest adres, port, özet, kontör ifadesi, doğrulama bayrağı,
  `roots_dir`, serbest bir `java` yolu; kök ya da yasal parmak izi kaldırmak;
- `[backup] dir`, `[backup] pg_dump`, `[backup] pg_restore`; dosya yolu veren iki veritabanı
  ayarı, `devices.oui_file` ve `usage.psl_file`;
- `admin\policy.json` (kaynak ağları, beklemeler, güvenilen program klasörleri, kapatma);
- güvenlik duvarındaki yönetici adresleri, sıkı giden kip, WinRM ve korunan kurallar
  (`Set-ArciFirewall.ps1`);
- veritabanı parolası, kullanım takma ad anahtarı, `secret.key` ve yönetim anahtarlarının
  yenilenmesi; `approve-jar`; `setup-token` / `create-admin`;
- elle düzenlemeden sonra canlı yüklenmeyen bir anahtar için hizmetin yeniden başlatılması
  (bölüm 7).

## 6. Kurulumun koyduğu sınırlar (`admin\policy.json`)

Kurulum, tarayıcı yönetiminin sınırlarını `C:\ProgramData\ARCILOGGER\admin\policy.json`
dosyasına yazar; bu dosyayı yalnızca yöneticiler ve SYSTEM değiştirebilir. Değerler kurulum
parametreleriyle seçilir ve sonraki çalıştırmalarda hatırlanır ([KURULUM.md](KURULUM.md) bölüm
7.1):

| Parametre | Varsayılan | Anlamı |
|---|---|---|
| `-SourceNetwork` | `10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16` | bir log kaynağının adresi bu ağlardan birinde olmalı (en geniş /16) |
| `-AdminChangeHoldHours` | 0 | saklama ve replika değişikliklerinin bekleme süresi (0–168 saat) |
| `-ZamaneHoldHours` | 24 | bilinmeyen bir Zamane istemcisinin en erken onaylanabileceği süre (1–168 saat) |
| `-DisableBrowserAdministration` | kapalı | tarayıcı yönetimini kapatır: ArciAdmin her isteği reddeder, ApplySystem bir şey yapmaz; 5651 işlevleri (toplama, mühürleme, doğrulama) hiç etkilenmez. **Şimdi mühürle** ve **Kontör sorgula** o sırada kullanılamaz (Görev Zamanlayıcı'da `\ARCILOGGER\Seal` ya da `\ARCILOGGER\Credit` görevini başlatın); `[[sources]]` elle değişince `Set-ArciFirewall.ps1` betiğini kendiniz çalıştırın (**Kaynaklar** tablosu güvenlik duvarını o sırada **Yönetilmiyor** gösterir) |

Kurulum yapılandırmadaki açık her log kaynağını bu ağlarla karşılaştırır: dışında kalan bir
kaynak varsa hiçbir şeyi değiştirmeden durur ve verilecek `-SourceNetwork` değerini yazar
(yoksa ApplySystem o göndericiyi ilk çalışmasında 514 kuralından çıkarırdı). Sonradan elle
eklenmiş böyle bir kaynak güvenlik duvarında açıksa açık kalır, değilse açılmaz; ikisinde de
`R20-admin` uyarısı ve `source_outside_policy` kodu görünür.

Tarayıcı yönetimini yeniden açmak için kurulumu `-Upgrade` ile parametresiz yeniden çalıştırmak
yetmez (hatırlanan seçim geçerlidir); `-DisableBrowserAdministration:$false` verin. Dosya elle de
düzenlenebilir: kurulum geçerli bir dosyadaki elle yazılmış değerleri korur; geçersiz bir dosya
tarayıcı yönetimini kapatır (her okuyan için aynı anlam) ve kurulum onu uyararak varsayılanlarla
yeniden yazar.

Kurulum ayrıca görevlerin çalıştırdığı programları kaydeder: Program Files dışındaki bir `java`,
ek bağımsız değişkenli bir `[tsa] java` komutu, `pg_dump` / `pg_restore` (SHA-256 değeriyle) ve
Zamane istemcisi. Görevler başka bir programı çalıştırmaz; kaydedilen bir program değişince
kurulumu yeniden çalıştırın.

## 7. Elle düzenleme ve tarayıcı birlikte

**Bir seferde tek yol kullanın.** `arcilogger.toml` dosyası Not Defteri'yle de düzenlenebilir;
tarayıcı bunu bir sonraki kayıtta fark eder ("Çakışma") ve üzerine yazmaz. Elle yapılan
değişiklikler de canlı yüklenir: log kaynakları (toplayıcı, ingest), API ve worker'ın okuduğu
bölümler yeniden başlatma gerektirmez. Canlı yüklenmeyen bölümler (`[database]`, `[api]`,
`[collector]`, `[ingest]`, `[worker]`, `data_dir`, `secrets_dir`) için ilgili hizmeti yeniden
başlatın; sayfa bunu "Yeniden başlatma bekleyen değişiklik" diye gösterir. Elle yapılan bir
değişiklik **Değişiklik geçmişi** sayfasında listelenmez ("Ayar dosyası elle değiştirilmiş").
Tarayıcı ilk kaynak değişikliğinde bütün `[[sources]]` tablolarını işaretli bir bölüme taşır;
o tabloların içindeki yorum satırları bu sırada kaybolur (önceki dosya `admin\versions`
klasöründe kalır).

**Konsoldan gizli değer.** `arcilogger-core secret set` / `secret delete`, `secrets.json`
dosyasını yeni bir dosyayla değiştirir; yeni dosyada ArciAdmin'in yazma hakkı yoktur. Ardından
yönetici PowerShell'inde `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"`
çalıştırın (ya da kurulumu yeniden çalıştırın); yoksa tarayıcıdan yapılan bir sonraki değişiklik
"yönetim hizmeti ayar dosyalarına artık yazamıyor" uyarısıyla uygulanır (`acl_drift`, kritik
`R20-admin` uyarısı) ve ondan sonraki değişiklik, haklar onarılana kadar başarısız olur. Kurulum
kendi sakladığı değerlerden (veritabanı parolası, ilk kurulumun sonundaki Kamu SM bilgileri)
sonra bunu kendisi yapar.

## 8. Sorun giderme

```powershell
$py  = "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
$cfg = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
Get-Service ArciAdmin                                    # çalışıyor mu?
& $py -I -m arcilogger_core.admin status --config $cfg   # kalp atışı, bekleyenler, sürümler
& $py -I -m arcilogger_core.admin versions list --config $cfg
& $py -I -m arcilogger_core.admin recover --config $cfg  # yarım kalan bir uygulamayı tamamla
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'ARCILOGGER-Admin' } -MaxEvents 20
Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName ApplySystem   # güvenlik duvarını hemen eşitle
```

- **Yönetim hizmeti çalışmıyor:** `Start-Service ArciAdmin`; hizmet günlüğü
  `D:\ARCILOGGER\logs` klasöründedir. Hizmet dururken 5651 işlevleri çalışmaya devam eder; yalnızca
  tarayıcıdan ayar değişmez.
- **"Elle müdahale gerekiyor"** (`rollback_failed`): sayfadaki sürüm numarasıyla önceki sürümü
  konsolda geri koyun (`& $py -I -m arcilogger_core.admin versions restore <N> --config $cfg`;
  sayfa komutu tam yoluyla gösterir); hizmetler geri konan dosyayı kendileri yükler. Bir hizmet
  "Yeniden başlatma bekleyen değişiklik" gösteriyorsa onu yeniden başlatın.
- **Destek için:** **Değişiklik geçmişi** sayfasındaki **Tanılama paketini indir** (parolanın
  yeniden girilmesiyle): hizmetlerin durum dosyaları, son günlük satırları ve yapılandırma, gizli
  bilgiler `***` ile değiştirilmiş; ham log, kasa ve dışa aktarım yoktur.
- **`acl_drift`:** değişiklik uygulandı, ancak ArciAdmin `arcilogger.toml` ya da `secrets.json`
  dosyasına artık yazamıyor (çoğu zaman konsoldan bir `secret set` sonrası, bölüm 7): sonraki
  değişiklikten önce `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` çalıştırın.
  Bir sürümü geri yüklemeyin: bu, uygulanmış değişikliği geri alır ve hiçbir şeyi onarmaz.
- **Güvenlik duvarı güncellenemedi:** ApplySystem görevinin son sonucu ve olay 4106; kurulum
  `-SkipFirewall` ile yapıldıysa güvenlik duvarı ARCILOGGER'ın değildir (`firewall_not_managed`)
  ve kurallar elle tutulur.
- Uyarılar: `R20-admin` ("Tarayıcıdan yönetim sorunu": reddedilen bir istek, duran hizmet,
  başarısız eşitleme, geri alma, elle müdahale) ve `R20-config-change` ("Önemli yapılandırma
  değişikliği": korumalı değişiklikler) — [UYARILAR.md](UYARILAR.md) bölüm 2.

Olay kimlikleri (kaynak `ARCILOGGER-Admin`, Uygulama günlüğü): 4100/4199 hizmet başladı/duruyor,
4101 sistem eşitlemesi istendi, 4102 uygulandı, 4103 başarısız, 4104 bekletiliyor ya da istemci
kaydedildi, 4105 iptal, 4106 eşitleme sonucu, 4107 istemci onaylandı, 4110 mühürleme isteği, 4111
görev isteği, 4120 reddedilen istek, 4121 kayıtlı kimlik bilgisi değişti (yalnızca adı). Bir olay
yalnızca bir görevi uyandırır; yetki imzalı istektedir.

## 9. Sahada doğrulanacaklar

Windows CI (kurulum iş akışının uçtan uca işi, temiz bir Windows sunucuda) şunları dener:
ArciAdmin'in `NT SERVICE\ArciAdmin` olarak çalışması ve Uygulama günlüğüne yazması, 2.2'deki
haklar (API'nin yapılandırmaya, gizli depoya, `zamane` ve kasaya yazamaması; `arcilogger.toml`
değiştirildikten sonra ArciAdmin'in kendi girdisinin kalması), Caddy üzerinden bir kuruluş adı
değişikliğinin "Uygulandı" olması, tarayıcıdan eklenen bir kaynağın 2 dakika içinde iki syslog
kuralına girmesi, **Şimdi mühürle** ile Seal görevinin (LOCAL SERVICE) bir günü mühürlemesi,
**Kontör sorgula**'nın Requests görevinde çalışması ve ArciAdmin'siz bir sürüme geri dönüşte
mühürlemenin sürmesi. Sahada ayrıca:

- ArciAdmin'in `kamusm-roots` klasörüne yalnızca kendi dosyalarını ekleyip silebildiği;
- olay tetikleyicilerinin görevleri 5 dakikalık çalışmayı beklemeden uyandırdığı (Seal, Requests,
  ApplySystem);
- güvenlik duvarı kuralı yerinde güncellenirken hiçbir syslog iletisinin kaybolmadığı (gerçek
  cihazlarla);
- Kamu SM test sunucusunda kontör sorgusunun kontör harcamadığı, müşteri numarasının biçimi;
- Zamane dağıtımının biçimi (jar ya da zip) ve `NT SERVICE\ArciAdmin` altında Java'nın bulunması.

## 10. Sayfaların kullandığı API uçları

Web arayüzü yalnızca aşağıdaki uçları kullanır (hepsi `/api/v1` altında, oturum ve CSRF
korumasıyla; ayrıntılı biçim: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 16). Makine ayarını
değiştiren ya da bir işlem başlatan her uç `202` ile isteğin numarasını ve `status_url` adresini döndürür; durum
`GET /admin/requests/{id}` ile izlenir. Her hata `code`, `message_tr` ve `message_en` taşır.

| Sayfa | Uçlar |
|---|---|
| Durum göstergesi, bütün sayfalar | `GET /admin/status`, `GET /admin/config` (değerler, `etag`'ler, kilitli alanlar) |
| Genel | `POST /admin/config/general/check`, `PUT /admin/config/general` |
| Uygulama ayarları | `GET /settings`, `PATCH /settings`, `POST /settings/{key}/reset`; `POST /admin/actions/lease-rebuild` |
| Kamu SM | `POST /admin/config/tsa/check`, `PUT /admin/config/tsa`; `POST /admin/uploads?kind=…` ardından `POST /admin/config/tsa/zamane`, `POST /admin/config/tsa/roots`, `POST /admin/config/tsa/legal-roots`; `POST /admin/actions/tsa-test` |
| Kaynaklar | `POST /admin/config/sources/check`, `POST /admin/config/sources`, `PUT /admin/config/sources/{name}`, `DELETE /admin/config/sources/{name}` |
| E-posta | `POST /admin/config/smtp/check`, `PUT /admin/config/smtp`, `POST /admin/config/smtp/ca`, `DELETE /admin/config/smtp/ca`; `POST /admin/actions/test-email`; uyarı e-postası ayarları `PATCH /settings` |
| Replika | `POST /admin/config/replica/check`, `PUT /admin/config/replica`, `POST /admin/actions/replica-test` |
| Saklama | `POST /admin/config/retention/check`, `PUT /admin/config/retention` |
| Yedekleme | `POST /admin/config/backup/check`, `PUT /admin/config/backup` |
| Değişiklik geçmişi | `GET /admin/requests`, `GET /admin/requests/{id}`, `DELETE /admin/requests/{id}` (İptal et), `POST /admin/requests/{id}/revert` (Geri al), `GET /admin/config/versions`, `GET /admin/diagnostics` |
| Mühürleme | `POST /seals/{day}/seal` (Şimdi mühürle), `GET /credits`, `POST /credits/query` (Kontör sorgula) |

Uygulama ayarları ve worker işlemleri (test e-postası, kiraların yeniden oluşturulması)
veritabanından geçer ve yönetim hizmetini beklemez; diğer her şey imzalı istekle ArciAdmin'e
gider.
