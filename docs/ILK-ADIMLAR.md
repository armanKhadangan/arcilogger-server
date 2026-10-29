# Kurulumdan sonra ilk adımlar

> English: [FIRST-STEPS.md](FIRST-STEPS.md). Kurulum: [KURULUM.md](KURULUM.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Kayıt düzeninin ve kişisel veri işlemenin hukuki değerlendirmesi için hukuk müşavirine
> danışın.

Bu belge bu sürüm (MVP) içindir: kurulum programı bittikten sonra ilk yöneticiden ilk 5651
sorgusuna ve günlük kontrollere kadar yapılacakları sırayla anlatır. Sunucunun ayarlarının çoğu
(kuruluş adı, saat, Kamu SM, log kaynakları, e-posta, replika, saklama, yedekleme) web arayüzünün
**Ayarlar** sayfalarından da değişir: [AYARLAR.md](AYARLAR.md). Bu belgedeki komutlar konsoldaki
yoldur ve tarayıcı yönetimi kapalıyken (`-DisableBrowserAdministration`) de geçerlidir. Veritabanı yedeği ve geri
yükleme: [YEDEKLEME.md](YEDEKLEME.md). Günlük/haftalık işletim, uyarıların anlamı ve sunucu
değişimi ileride `ISLETIM.md` / `OPERATIONS.md` belgesinde (M7) toplanacaktır.

## 0. Komutlar nasıl çalıştırılır?

- Komutlar sunucuda, **yönetici olarak açılmış Windows PowerShell** penceresinde çalışır:
  yapılandırmayı (`arcilogger.toml`), gizli depo anahtarını (`secret.key`) ve denetim kaydı
  klasörünü yalnızca yöneticiler ve ARCILOGGER hizmetleri okuyabilir.
- ARCILOGGER programları `PATH` üzerinde **değildir**. Pencereyi açınca bir kez şunları yazın
  (`-InstallDir` ile başka bir klasör seçtiyseniz ilk satırı değiştirin):

  ```powershell
  $arci   = "C:\Program Files\ARCILOGGER\venv\Scripts"
  $cfg    = "C:\ProgramData\ARCILOGGER\arcilogger.toml"
  $core   = "$arci\arcilogger-core.exe"
  $api    = "$arci\arcilogger-api.exe"
  $ingest = "$arci\arcilogger-ingest.exe"
  $db     = "$arci\arcilogger-db.exe"
  ```

- Yapılandırma varsayılan olarak `C:\ProgramData\ARCILOGGER\arcilogger.toml` dosyasından okunur.
- Kuruluşa özgü değerlerin (kuruluş adı `site_name`, yerel saat kaynağı `[clock] ntp_server`)
  girilip girilmediğini denetleyin; kurulum bunları `-SiteName` ve `-NtpServer` ile alır
  ([KURULUM.md](KURULUM.md) bölüm 7.1). Log kaynağı (`[[sources]]`) yokken de uyarır: yeni bir
  kurulumda hiç yoktur, bölüm 5'te eklenir:

  ```powershell
  & $core config-check                   # HATA: çıkış kodu 1; UYARI: yalnızca bilgi
  ```

## 1. İlk yönetici

Ayrıntı: [KURULUM.md](KURULUM.md) bölüm 9.

1. Kurulum anahtarı (24 saat geçerli, tek kullanımlık, yalnızca bir kez gösterilir):

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

2. `https://<sunucu adresi>` (kurulumun sonunda yazılan web adresi) → **İlk kurulum** sayfası:
   anahtar, kullanıcı adı, görünen ad, parola.
3. Giriş yapın, iki adımlı doğrulamayı (TOTP) kurun, **10 kurtarma kodunu** sunucudan ayrı ve
   güvenli bir yerde saklayın, KVKK bilgilendirmesini onaylayın.
4. Kurtarma (bütün yöneticiler erişimini kaybettiyse):
   `& $api create-admin --config $cfg`.
5. **Lisans:** deneme süresi 14 gündür. Kurulumun sonunda yazılan istek kodunu (ya da Ayarlar →
   Lisans sayfasındakini) hemen lisans verene gönderin (bölüm 17, [LISANS.md](LISANS.md)).

## 2. Diğer kullanıcılar

**Ayarlar → Kullanıcılar ve Roller** sayfasında (yalnızca yönetici; değişiklikler son 5 dakika
içinde parolanın yeniden girilmesini ister):

- Rolü seçerek kullanıcı oluşturun (rol tablosu: [roles.md](roles.md)). Örneğin 5651 talepleri
  için bir **Denetçi** hesabı. Ekranda **bir kez** gösterilen geçici parolayı kişiye **yüz yüze**
  verin: ilk girişte değiştirilmesi zorunludur ve iki adımlı doğrulamayı ilk giren kişi kurar.
- Her kullanıcı parolasını sonradan kendisi değiştirebilir: sağ üstteki kullanıcı menüsü →
  **Parolayı değiştir** (mevcut parola istenir; diğer tarayıcı ve cihazlardaki oturumları
  kapanır, hatalı mevcut parolalar girişteki gibi hesabı kilitler).
- Yönetici ve Denetçi için TOTP zorunludur. Kişisel veri gören roller — bu sürümde dört rolün
  hepsi, rapor izleyici de (kullanımda adları görür) — ilk girişte KVKK bilgilendirmesini onaylar
  (her onay denetim kaydına yazılır).
- Yönetici olmayan kullanıcılar yalnızca **kendi** 5651 sorgularını görür; yöneticiler hepsini
  görür (sahip kararı, [ASSUMPTIONS.md](ASSUMPTIONS.md) D5). Başka bir kullanıcının sorgusundan
  oluşturulmuş kanıt paketleri de görünmez. Denetçi kanıt paketi oluşturamaz (sahip kararı, D4);
  görebildiği paketleri indirebilir.
- **Kullanıcı sınırı** (lisans): etkin kullanıcı sayısı lisanstaki sınıra ulaşınca yeni kullanıcı
  eklenemez ve pasif bir kullanıcı yeniden etkinleştirilemez (`license_limit`); var olanlar
  çalışmaya devam eder. Geçerli bir lisans yokken (deneme bitti) sınır 3'tür; deneme süresince
  sınır yoktur. Yer açmak için bir kullanıcıyı pasifleştirin ([LISANS.md](LISANS.md) bölüm 6).

## 3. Kamu SM: hesap gelmeden önce, test ve üretim

**Kamu SM kimlik bilgileri yokken** kurulumla gelen yapılandırma `provider = "zamane"` ve test
sunucusudur. `\ARCILOGGER\Seal` görevi her gece (00:10'dan sonra 5 dakikada bir) çalışır ve
kimlik bilgileri ya da kökler olmadığı için **her seferinde başarısız olur**: Görev Zamanlayıcı'da
son çalışma sonucu `0x1` görünür, `& $core status` çıktısında `pending_days` her gün büyür ve
hata `D:\ARCILOGGER\logs\core.log` dosyasına yazılır. Bu bir yapılandırma hatasıdır:
`alerts.jsonl` dosyasına gün ve neden başına **bir kez** `R20-seal-config` uyarısı yazılır (bölüm
9; e-posta ayarlıysa e-postayla da bildirilir). Ham kayıtlar bu sırada kaybolmaz; spool'da bekler.

İlk başarılı mühürden **önce** iki seçenekten birini seçin (sonradan değiştirmeyin). Seçim
**sahibin kararıdır**; hukuk müşaviriyle değerlendirin. İki seçenekte de canlıya geçiş öncesindeki
günler üretim damgası **almaz** (test sunucusunun damgalarının yasal geçerliliği yoktur — Kamu SM);
fark, o günler için bir bütünlük kanıtı olup olmamasıdır:

- **A — Zincir canlıya geçiş günü başlasın:** `arcilogger.toml` içinde `[seal]` bölümüne
  `first_day = <canlıya geçiş günü>` yazın (örnek: `first_day = 2026-11-01`) ve test süresince
  Seal görevini kapatın:

  ```powershell
  Disable-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName Seal
  # canlıya geçişte, üretim ayarları yapıldıktan sonra:
  Enable-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName Seal
  ```

  Sonuç: `first_day` öncesindeki spool günleri zincire girmez, **hiç mühürlenmez** (bütünlükleri
  hiçbir damgayla kanıtlanmaz) ve **silinmez**; spool'da olduğu gibi kalır (ne yapılacağı sahibin
  kararıdır). Veritabanına yüklenmeye devam eder. Görev yeniden açıldığında mühürleme bu
  dosyaların her biri için **bir kez** kritik bir `R20-clock` uyarısı yazar ("zincirin
  başlangıcından önceki bir güne ait"; saat atlamasına karşı genel denetim): bu seçenekte
  beklenen bir durumdur, dosyalar yerinde kalır.
- **B — Test süresinde de mühürlensin (pilot):** Seal görevi açık kalır; test kullanıcısı gelince
  bekleyen günler test sunucusuyla damgalanır. Sonuç: bu günlerin özet zinciri ve test damgası
  olur (bütünlük kanıtı), ama zincirde **kalıcı olarak** test damgalı kalırlar ve yasal nitelikli
  sayılmazlar; gün bitiminden `late_after_hours` (48) saatten sonra damgalananlar ayrıca **kalıcı
  olarak** "geç damgalandı" (`stamped_late`) olarak işaretlenir. Üretime geçişten sonraki günler
  aynı zincirde devam eder. Doğrulama yalnızca `C:\ProgramData\ARCILOGGER\kamusm-roots`
  içindeki köklere güvenir: test damgalarının bağlandığı kök (test sunucusununki üretiminkinden
  farklı olabilir — [kamusm.md](kamusm.md) K10, **sahada doğrulayın**) test damgalı günler var
  olduğu sürece bu klasörde **kalmalıdır**; kaldırılırsa o günlerin doğrulaması başarısız olur
  (`R20-verify`). Yasal nitelik yalnızca `legal_root_fingerprints` listesindeki köklerden gelir;
  test kökünü bu listeye yazmayın.

**Sunucuda `provider = "mock"` kullanmayın.** Deneme sağlayıcısı ([kamusm.md](kamusm.md) bölüm 4)
yalnızca geliştirme ve deneme sunucuları içindir: onun kök sertifikasına yalnızca
`provider = "mock"` iken güvenilir. Sağlayıcı Kamu SM'ye çevrildiği anda mock damgalı günler
**doğrulanamaz** (`R20-verify`, aylık bütün zincir denetimi başarısız); onları yeniden
doğrulanabilir kılmanın desteklenen bir yolu yoktur.

**Test hesabı ve üretime geçiş:** web arayüzünde **Ayarlar → Kamu SM** sihirbazı
([AYARLAR.md](AYARLAR.md)) ya da konsolda [KURULUM.md](KURULUM.md) bölüm 10 — sırasıyla kök
sertifikalar (parmak izi karşılaştırması), `secret set`, `credit`, `tsa-test`; üretimde kontör satın
alma, üretim kimlik bilgileri, `url = "http://zd.kamusm.gov.tr"`, `[tsa] legal_root_fingerprints`.
API yeni ayarı 2 saniye içinde kendisi yükler; Ayarlar sayfası "Yeniden başlatma bekleyen değişiklik" gösteriyorsa ya da bu sürümden önceki bir sürümde `Restart-Service ArciApi` (yeniden
yüklenmeden kırmızı "test damgası" uyarısı ve rapor damgaları eski ayarla kalır).

## 4. `arcilogger.toml` değiştiğinde

Ayarlar sayfalarından yapılan değişiklikleri hizmetler yeniden başlatılmadan alır
([AYARLAR.md](AYARLAR.md) bölüm 2). Dosyayı elle düzenlediğinizde de log kaynaklarını toplayıcı
ve ingest, kendi okudukları bölümleri API ve worker birkaç saniye içinde yükler; zamanlanmış görevler her çalıştırmada yeniden okur; güvenlik duvarını `ApplySystem`
görevi en geç 5 dakika içinde eşitler. **Bir seferde tek yol kullanın** (tarayıcı ya da elle).
Canlı yüklenmeyen bölümler (`[collector]`, `[api]`, `[ingest]`, `[worker]`, `[database]`) ve
tarayıcı yönetimi kapalıyken:

```powershell
# [[sources]] ya da [tsa] adresi/portu değiştiyse önce güvenlik duvarı:
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
# sonra hizmetler (toplayıcı yeniden başlarken UDP loglar birkaç saniye kaybolabilir):
Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
```

## 5. Cihazlar ve kaynak başına denetim

**Önce log kaynakları.** Yeni bir kurulumun `arcilogger.toml` dosyasında **hiçbir log kaynağı
yoktur**: örnek yapılandırmadaki kurgusal örnek ağın `[[sources]]` satırları yalnızca yorum
satırıdır. Bu geçerli bir durumdur, ama kaynak eklenene kadar kurulumun güvenlik duvarı 514'ü
hiçbir göndericiye açmaz ve hiçbir log kaydedilmez (`-SkipFirewall` ile her gönderici
`unknown-<ip>` olarak kaydedilir, ayrıştırıcısı içerikten seçilir); sessiz kaynak uyarısı (R15)
hiçbir kaynağı izlemez, Panel'in "Log alımı" kutusu "İzlenen kaynak yok" yazar. Log gönderen her
cihaz için, en az güvenlik duvarınız ve ana yönlendiriciniz (DHCP sunucusu MikroTik ise ya da
ARP-LOG kullanılıyorsa) için, bir `[[sources]]` kaydı ekleyin: dosyadaki örneklerin başındaki `#`
işaretini silin, adı ve adresi kendi cihazınızınkiyle değiştirin ([DEVICE-SETUP.md](DEVICE-SETUP.md)
bölüm 4). FortiGate'in DHCP kayıtları yalnızca `type = "fortigate"` ile tanımlı bir kaynaktan
gelirse kira kanıtı sayılır ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4). Sonra kurulumu
yeniden çalıştırın ya da bölüm 4'teki komutları uygulayın; `& $core config-check` artık kaynak
uyarısı vermez.

Cihaz ayarları: [DEVICE-SETUP.md](DEVICE-SETUP.md). Her cihazdan test mesajı gönderdikten sonra
**her beklenen kaynağın** bugünkü dosyası olmalıdır:

```powershell
Get-ChildItem D:\ARCILOGGER\spool\*\$(Get-Date -Format yyyy-MM-dd).log | Format-Table Directory, Length, LastWriteTime
Get-Content D:\ARCILOGGER\state\collector-status.json
& $ingest status                       # kaynak başına yüklenen gün, gecikme, son hata
```

Güvenlik duvarı 514'e yalnızca `[[sources]]` adreslerinden izin verir. Bir kaynağın dosyası
yoksa cihazın gönderdiği adres yapılandırmadakiyle aynı değildir: [DEVICE-SETUP.md](DEVICE-SETUP.md)
bölüm 4. Kira kanıtını gönderen kaynak (ana MikroTik yönlendirici: DHCP ve ARP-LOG kayıtları;
yalnız-FortiGate ağlarda FortiGate: DHCP olay kayıtları), 5651 erişim kayıtlarının özüdür ve
özellikle denetlenmeli: **Kaynaklar** sayfasının **Kira kanıtı** kutusu ondan kullanılabilir
kayıt gelip gelmediğini gösterir.

### Kişiler ve cihaz-kişi atamaları

5651 sorgusu bir kişiyi ancak cihaz **olay anında** o kişiye atanmışsa adlandırır (atama geçmişi,
[adr/0008-device-assignment-history.md](adr/0008-device-assignment-history.md)). Yönetici olarak
**Cihazlar ve Kişiler** sayfasında:

1. Departmanları ve ağı girin: ARCILOGGER hiçbir departman ya da VLAN planı getirmez, yeni bir
   kurulumda ikisi de boştur. **Departmanlar ve VLAN'lar** sekmesinde önce departmanları
   ekleyin (kişiler ve kişi CSV'si onlara bağlanır), sonra **VLAN ekle** ile kurumun her
   VLAN'ını ya da alt ağını: ağ adresi ve önek uzunluğu (ör. `10.0.20.0/24`, ana bilgisayar
   bitleri sıfır), VLAN numarası (1–4094, ağdaki numarası: MikroTik kira kayıtlarının VLAN
   numarası DHCP sunucusunun adından gelir, `dhcp.server_vlan_regex`, ör. `dhcp20` → 20,
   FortiGate kiralarınınki adresi içeren ağdan; transit ağı gibi numarasız bir ağda boş), ad ve
   isteğe bağlı olarak amaç, DHCP sunucusunun adı (MikroTik: yönlendiricideki adı, ör. `dhcp20`;
   FortiGate: DHCP sunucusunun arayüz adı; bir arayüzde birden çok DHCP sunucusu varsa, ör.
   aktarılan ağlarda, bu ad yalnızca bir ağa yazılabilir, ötekilerde boş kalır), departman,
   **Misafir ağı** işareti (FortiGate'ten değil başka bir çıkıştan çıkan misafir ağı,
   [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 5) ve not. Bir cihaz
   VLAN numarasının ya da IP adresinin bulunduğu ağın VLAN'ındadır: onun adını, kimseye
   atanmamışsa onun departmanını alır; bir ağın içindeki daha küçük bir ağ (ör. bir `/24` içinde
   `/25`) ondan önce gelir. Ağ, VLAN numarası ve DHCP sunucusu tekildir. VLAN'lar olmadan da
   toplama, mühürleme, 5651 sorgusu ve kullanım çalışır; cihazlar ancak VLAN adı ve VLAN
   departmanı almaz.

   FortiGate DHCP kullanılıyorsa FortiGate'in DHCP verdiği her ağı, VLAN numarası olmasa da,
   ekleyin: tanımlı bir ağda olmayan adreslerin FortiGate zincirleri düşük güvenle gösterilir
   (`fortigate_dhcp_no_network`). Bir ağı sonradan ekler ya da değiştirirseniz kiraları o ağın
   ilk FortiGate DHCP kaydının gününden (`R20-lease:fortigate-dhcp-subnet` uyarısının
   ayrıntılarındaki ilk gün) bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman
   çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün):
   bir ağ geçmiş kiralara ancak yeniden oluşturmadan sonra uygulanır; o zamana kadar etkilenen
   zincirler düşük güvenle gösterilir. Her DHCP kapsamını tam olarak bir ağ olarak girin: bir
   kapsamın içine daha küçük bir ağ ya da birkaç kapsamı içine alan tek bir geniş ağ girmeyin
   (ARCILOGGER bir istemcinin adres değiştirmesini ağ ağ izler; [DEVICE-SETUP.md](DEVICE-SETUP.md)
   bölüm 2.4).
2. Kişileri ekleyin ya da CSV ile içe aktarın: şablonu indirin
   (`name;department;title;email;employee_no;active;notes;usage_excluded`; yalnızca `name`
   zorunludur, `usage_excluded` evet/hayır: kişiyi kullanım raporlarının dışında bırakır), Excel'de
   **"CSV UTF-8"** türüyle kaydedin, önce **önizleyin** (hiçbir şey yazılmaz, satır satır hatalar
   görünür), hata yoksa **uygulayın** (tüm satırlar ya da hiçbiri). CSV içe aktarma Toplu içe
   aktarma modülü gerektirir; elle giriş her zaman açıktır (atama CSV'si de bu modüldedir; kişi ve
   cihaz listelerinin CSV dışa aktarması her zaman açıktır, [LISANS.md](LISANS.md)).
3. Cihazları atayın: MAC, ana bilgisayar adı, IP ya da VLAN ile arayın ("Bilinmeyen cihazlar"
   süzgeci), bir ya da birçok cihazı seçip kişiye atayın — ya da atama CSV'si
   (`mac;employee_no;person_name;valid_from;note`). Cihaz kişide **daha önceden** beri ise
   başlangıcı (`valid_from`) o tarih olarak girin: atama geçmişi sonradan geriye doğru
   değiştirilemez, yalnızca ileriye eklenir.
4. Cihaz el değiştirdiğinde **aynı gün** yeni kişiye atayın. Ayrılan kişiyi silmeyin, pasif yapın
   (geçmiş korunur) ve cihazlarını yeni sahibine atayın ya da atamayı kaldırın.

Bir kişinin birden çok cihazı olabilir (bilgisayar, telefon, telefonun değişen rastgele MAC
adresi). Her atama, içe aktarma ve CSV dışa aktarma denetim kaydına yazılır (kişi adları değil,
numaraları ve sayılar).

## 6. Veritabanındaki ayarlar

Veritabanındaki ayarlar web arayüzünde **Ayarlar → Uygulama ayarları** sayfasından ya da
konsolda `arcilogger-db settings` komutuyla okunur ve değiştirilir. Her değişiklik eski ve yeni değeriyle **zincirli denetim kaydına**
(`settings.change`) yazılır; `--by` değiştiren kişinin ARCILOGGER kullanıcı adıdır (aşağıda
örnek olarak `tolga`). Değiştirilen
bir değeri yükseltme ya da başlangıç verisi geri almaz. Bir yükseltme eski bir ayarı yenisine
çevirebilir; bu da denetim kaydına yazılır ([KURULUM.md](KURULUM.md) bölüm 13).

İnternet çıkışları (`nat.egresses`) normalde Ayarlar → Uygulama ayarları → 5651 sorgu ve kira
zaman çizelgesi → İnternet çıkışları (NAT) bölümündeki çıkış düzenleyicisiyle girilir: düzenleyici
her alanı kaydetmeden önce denetler, altındaki "Kayıtlarda görülen NAT adresleri" tablosu
FortiGate kayıtlarındaki NAT adreslerini ve arayüzlerini gösterir ([AYARLAR.md](AYARLAR.md) bölüm
1). Konsoldaki karşılığı aşağıdaki örnektir (`<genel IP>` hattın genel adresidir):

```powershell
& $db settings list
& $db settings get nat.egresses
& $db settings set lookup.tolerance_seconds 60 --by tolga
'{"hat-1": {"mode": "direct", "public": ["<genel IP>"]}}' | & $db settings set nat.egresses - --by tolga
'["<MAC 1>", "<MAC 2>"]' | & $db settings set devices.infrastructure_macs - --by tolga
```

Değer JSON'dur (ondalık sayı yok). Metin değeri JSON tırnağı olmadan yazılabilir. Liste ya da nesne,
yani çift tırnak içeren bir değer, **boruyla** verilir ve değer yerine `-` yazılır: Windows
PowerShell 5.1 programlara giden komut satırı argümanlarındaki çift tırnakları siler. Sonucu her
zaman `settings get` ile denetleyin. Ayarlar kullanıldıkları anda veritabanından okunur; hizmetleri
yeniden başlatmak gerekmez. Sahada belirlenip girilecekler ([CORRELATION.md](CORRELATION.md) bölüm
8):

| Ayar | Ne zaman |
|---|---|
| `nat.egresses` (İnternet çıkışları) | Her internet hattı için: genel IP (ve değişip değişemeyeceği), FortiGate'in o hattaki NAT adresi, arayüzleri (dstintf; iki ya da daha çok hatta zorunlu), NAT türü ve port koruma ("Korunuyor" yalnızca ölçülünce); FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da şubeye yaptığı NAT'ın adresleri "İnternet çıkışı değil" türüyle. Değerleri "Kayıtlarda görülen NAT adresleri" tablosundan okuyun ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.6); yükseltmede eski NAT ayarlarından aktarılan çıkışlar: [KURULUM.md](KURULUM.md) bölüm 13 |
| `devices.infrastructure_macs` | Ana yönlendiricinin, FortiGate'in ve anahtarların MAC adresleri (IP çakışması sayılmasınlar) |
| `lease.server_seconds` | Kira süresi bir günden (`lease.default_seconds`) farklı olan DHCP sunucuları, ör. misafir ağınınki: `{"<sunucu adı>": <saniye>}` (boruyla); ad, MikroTik'te DHCP sunucusunun adı, FortiGate'te DHCP arayüzünün adıdır. FortiGate kiraları bu süreleri yalnızca kayıttaki kira süresi kullanılamadığında kullanır (`dhcp.fortigate_use_logged_lease`) |
| `devices.oui_file` | İsteğe bağlı üretici adları: dosyayı `C:\ProgramData\ARCILOGGER\oui.csv` olarak koyun ([CORRELATION.md](CORRELATION.md) bölüm 1, "Vendor") |
| `lookup.srcmac_interfaces` | İsteğe bağlı: yalnızca aygıt tanıma açık, ağ geçidi FortiGate olan ve iki bilinen istemciyle sahada denetlenmiş arayüzler ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.5); yönlendirilmiş (L3) bir ağa bakan arayüz hiçbir zaman |
| `dhcp.fortigate_interfaces_excluded` | FortiGate DHCP kullanılıyorsa: IPsec üzerinden DHCP veren sunucuların (`server-type ipsec`) arayüzleri ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) |
| `dhcp.fortigate_logids`, `dhcp.fortigate_dhcp_msgs` | Yalnızca sahadaki doğrulama başka bir logid ya da `dhcp_msg` değeri gösterirse (ör. bırakma kaydı başka bir logid ile geliyorsa; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) |
| `dhcp.fortigate_use_logged_lease` | Açık kalır (varsayılan): FortiGate DHCP Ack kaydındaki kira süresi o kiranın süresidir |
| `dhcp.fortigate_traffic_extends` | Kapalı kalır (önerilen); yalnızca yenilemelerin kaydedilmediği sahada doğrulanırsa ve kurum, hukuk müşaviriyle, yardım metninin anlattığı yanlış kişi riskini kabul ederse açılır ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) |
| `dhcp.fortigate_format_verified` | FortiGate DHCP kayıt biçiminin sahadaki doğrulaması geçince açılır; her FortiOS yükseltmesinden sonra kapatılır, doğrulama yinelenir ve yeniden açılır ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) |

`dhcp.fortigate_*` ayarlarından birini (`dhcp.fortigate_format_verified` dışında: o yalnızca sorgu
ve rapor metnini değiştirir) ya da bir FortiGate kaynağını (Ayarlar → Kaynaklar) değiştirdikten
sonra kiraları etkilenen ilk günden bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman
çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün):
değişiklik geçmiş günlere ancak yeniden oluşturmadan sonra uygulanır. **Kanıt ekleyen bir
değişiklikten** sonra (bir logid ya da `dhcp_msg` değeri eşlendi, bir FortiGate kaynağı eklendi) o
zamana kadar o günlerin anları için 5651 sorgusu zincir kurmadan yanıt verebilir (güvenli taraf).
**Kanıtı kaldıran ya da daraltan bir değişiklikten** sonra (bir arayüz
`dhcp.fortigate_interfaces_excluded` listesine eklendi, `dhcp.fortigate_traffic_extends` ya da
`dhcp.fortigate_use_logged_lease` kapatıldı, bir logid ya da `dhcp_msg` değeri çıkarıldı, bir
FortiGate kaynağı kaldırıldı ya da türü değiştirildi) eski ayarlarla oluşturulan kiralar yeniden
oluşturmaya kadar eski güven düzeyleriyle kalır; bir an hâlâ önceki cihazı gösterebilir. Bu durumda
kiraları **hemen**, etkilenen ilk günden yeniden oluşturun.

## 7. Bu sürümün sınırları

- **Departman geçmişi tutulmaz:** 5651 sonucundaki kişi olay anındaki atamadır, departmanı ise
  kişinin bugünkü departmanıdır (rapor bunu belirtir). Geçmişteki bir atama arayüzden
  düzeltilemez (bölüm 5).
- Menüde yalnızca **Uyum Durumu ve Sistem** henüz yer tutucudur. Çalışan sayfalar: Panel,
  Kaynaklar ve Canlı Akış (bölüm 9), Arama, 5651 Sorgu, Kullanım (bölüm 16), Güvenlik, Mühürleme,
  Kanıt Paketleri, Cihazlar ve Kişiler (bölüm 12), Raporlar (Panel, Güvenlik ve Raporlar: bölüm
  15), Denetim Kaydı, Ayarlar (Kullanıcılar ve Roller, Lisans: bölüm 17).
- **Ücretli modüller** (lisans, [LISANS.md](LISANS.md)): Kullanım analizi (Kullanım sayfası,
  paneldeki kullanım bölümleri), Zamanlanmış kullanım raporları (Raporlar sayfası) ve Toplu içe
  aktarma (CSV). Deneme süresince hepsi açıktır; bir modül kapalıyken sayfası kilitli görünür.
  Geri kalan her şey 5651 Temel'dir ve hiçbir lisans durumunda kapanmaz.
- **Uyarılar Güvenlik sayfasındadır** (bu sürümde yalnızca Yönetici): liste, durum ve önem
  süzgeçleri, ayrıntı ve adım adım ne yapılacağı, durum (Yeni, İnceleniyor, Kapandı, Yanlış
  alarm) ve notlar; Panel en yeni açık uyarıları gösterir (bölüm 15, [UYARILAR.md](UYARILAR.md)
  bölüm 8). Bir sorunla uğraşırken uyarıyı **İnceleniyor** yapın: sorun sürerken kapatılan bir
  uyarının yerine denetim bir sonraki çalışmasında yeni bir uyarı açar. Denetimlerin çözemediği
  uyarıları (ör. `R20-spool`, replika farklı) inceledikten sonra nedeni not olarak ekleyip elle
  kapatın. Kural motoru (YAML kuralları), kural düzenleyici ve webhook'lar yoktur; sunucuda
  `arcilogger-worker alerts list --open` da çalışır, e-posta `[smtp]` ayarlanınca gider.
- **Mühürlemenin yapılandırma hataları** (Kamu SM kimlik bilgileri yok, `kamusm-roots` klasöründe
  kök yok, `[tsa] zamane_jar` ayarlı değil ya da Java veya Zamane istemcisi güvenilir değil:
  `executable` nedeni, ör. kurulumdan sonra `zamane\` içine elle kopyalanan bir jar; bu jar ancak
  kurulum `-ZamaneJar` ile kaydedince ya da Ayarlar → Kamu SM'den yüklenip onaylanınca çalışır,
  [KURULUM.md](KURULUM.md) bölüm 7) Seal görevini mühürlemeye başlamadan durdurur: görevin son
  çalışma sonucu `0x1`, hata her çalışmada `D:\ARCILOGGER\logs\core.log` dosyasına,
  `R20-seal-config` uyarısı gün ve neden başına bir kez yazılır.
- **Şimdi mühürle** ve **Kontör sorgula** Mühürleme sayfasındadır (`seal.manage`): "Şimdi
  mühürle" sıradaki (en eski) günü `\ARCILOGGER\Seal` görevine bir kez mühürletir, "Kontör
  sorgula" Kamu SM'yi `\ARCILOGGER\Requests` görevine bir kez sorgulatır. İkisi de yönetim
  hizmeti ArciAdmin üzerinden çalışır; tarayıcıdan yönetim kapalıyken ya da ArciAdmin yanıt
  vermiyorken Görev Zamanlayıcı'da `\ARCILOGGER\Seal` ya da `\ARCILOGGER\Credit` görevini
  çalıştırın.
- Veritabanı yedekleri sunucunun kendi `D:` diskinde durur; sunucu dışına (harici disk, bir
  NAS) kopyalama elle yapılır (bölüm 10).

## 8. İlk 5651 sorgusu (deneme)

1. Bilinen bir bilgisayardan (IP ve MAC adresini bildiğiniz) bir siteye girin; saati ve hedefi
   (alan adı ya da IP ve port) not edin.
2. Birkaç dakika sonra **5651 Sorgu** sayfasında talep bilgilerini girin. Her sorgu, talep
   bilgileriyle birlikte **kalıcı olarak** saklanır ve denetim kaydına yazılır: denemeyi açıkça
   belirtin (örneğin makam "DENEME", gerekçe "Kurulum denemesi").
3. Not ettiğiniz zamanı ve hedefi girin. Sonuçta bilgisayarın IP'si, MAC adresi, DHCP kira kaydı ve
   kanıt satırları görünmelidir.
4. **Resmi rapor (PDF)** indirin; bir kanıt satırının ham satırını açın.
5. İsterseniz bu sorgudan bir **kanıt paketi** oluşturun (yönetici) ve başka bir bilgisayarda
   `verify.py` ile doğrulayın ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) bölüm 5). Paket ancak
   mühürlenmiş günler için oluşturulabilir (ertesi gün).

## 9. Günlük ve haftalık kontroller

Her gün:

- **Panel**: log alımı, bugünkü ayrıştırma hataları, dünkü mühür, kontör, saat sapması ve açık
  uyarılar bir bakışta. **Güvenlik** sayfası: Yeni uyarıları inceleyin, ilgilendiklerinizi
  İnceleniyor yapın.
- **Kaynaklar** sayfası (yönetici): her beklenen kaynak "Etkin" mi? "Sessiz" ya da "Hiç mesaj
  gelmedi" görünen bir kaynağın cihazını ve ağ bağlantısını denetleyin ("Sessiz (izlenmiyor)":
  kaynak sessiz kalabilir, R15 onu izlemez). Bugünkü satır sayısı, son bir saatin mesaj/dk eğrisi
  ve ayrıştırma hatası oranı da buradadır; bir satır kaynağın son 48 saatini, son 14 gününü,
  kayıt boşluklarını ve ayrıştırma hatalarını açar. **Bilinmeyen** süzgeci yapılandırmada olmayan
  göndericileri gösterir: tanıdığınız bir cihazsa **Kaynak olarak tanımla**, Ayarlar →
  Kaynaklar'da ekleme penceresini onun adresiyle doldurur (adı gözden geçirip kaydedin; eski
  `unknown-<ip>` klasörü ve kayıtları yerinde kalır). Kaynaklar sayfası yalnızca okur ve lisanstaki
  log kaynağı sayısını gösterir; sınırın üzerindeki kaynakların kayıtları da toplanır ve
  mühürlenir.
- **Canlı Akış** (yönetici ve denetçi): bir cihazın satırları gerçekten geliyor mu, ingest
  beklemeden görmek için. Kaynağı ya da "Bilinmeyen göndericiler" çipini seçip **Başlat**'a basın;
  yeni bir cihazın ilk denemesi [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4'tedir. Her akış
  süzgeciyle denetim kaydına yazılır; boşta kalan ekranın oturumu her sayfadaki gibi kapanır.
- **Mühürleme** sayfası: dün "doğrulandı" mı? Kırmızı "test damgası" uyarısı var mı?
- Sunucuda:

  ```powershell
  & $core status                         # pending_days, last_sealed_day, legal_grade, credit
  Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
  Get-Service Arci* | Format-Table Name, Status
  & "$arci\arcilogger-worker.exe" alerts list --open   # açık uyarılar (alerts.jsonl dahil)
  Get-Content D:\ARCILOGGER\state\alerts.jsonl -Tail 20
  Get-Content D:\ARCILOGGER\state\backup\last.json   # gece yedeği: "ok": true ve bugünün tarihi
  Get-Volume D | Format-Table DriveLetter, SizeRemaining, Size
  ```

- Uyarılar (`alerts.jsonl` ve worker denetimleri; eşikler, ayarlar ve e-posta:
  [UYARILAR.md](UYARILAR.md)):

  | Kod | Anlamı |
  |---|---|
  | `R15` | Log kaynağı sessiz: bir kaynaktan kalp atışı süresinden (`[[sources]] heartbeat_minutes`; örnek yapılandırmanın yorum satırı hâlindeki kaynaklarında güvenlik duvarı 10, ana yönlendirici 15 dk; kaynak yoksa R15 de yok) uzun süredir mesaj yok; kaynak yeniden gönderince kendiliğinden çözülür |
  | `R20-seal` | Bir mühürleme denemesi başarısız (Kamu SM'ye ulaşılamadı, damga doğrulanmadı, …) ya da kapanmış gün ertesi sabah 09:00'da hâlâ mühürlenmemiş; gün yeniden denenir |
  | `R20-seal-config` | Seal görevi mühürlemeye hiç başlayamadı: Kamu SM kimlik bilgileri, `kamusm-roots` kökleri ya da `[tsa] zamane_jar` eksik (gün ve neden başına bir kez; bölüm 3) |
  | `R20-seal-skip` | Art arda `[seal] max_consecutive_empty_days` (7) günden uzun **hiç log olmayan** bir dönem: bu günler tek tek damgalanmadı, sonraki mühürlü günün manifestinde boşluk (`gaps`) olarak listelenir. Toplayıcı durmuş, sunucu kapalı kalmış ya da saat ileri atlamış olabilir |
  | `R20-clock` | Saat sapması (`clock-check`) ya da mühürlemenin gördüğü bir saat atlaması (zincir başlangıcından önceki, mühürlenmiş ya da gelecek bir güne ait spool dosyası; geri giden saat). Mühürleme o dosyalara dokunmaz |
  | `R20-verify` | Doğrulama hatası (haftalık/aylık doğrulama) |
  | `R20-replica` | İkinci kopya hatası |
  | `R20-credit` | Kontör eşiğin altında ya da okunamadı |
  | `R20-backup` | Gece veritabanı yedeği alınamadı ya da başlatılmadı (`high`: boş alan yetersiz, `pg_dump` bulunamadı ya da sunucudan eski, veritabanına bağlanılamadı, yedek denetimden geçmedi). Yedek alındı ama: eski yedekler silinemedi (`R20-backup:prune`, `medium`; o yedek bunu yeniden bildirmezse sonraki başarılı yedekten bir saat sonra çözülür), `last.json` yazılamadı (`R20-backup`, `high`) ya da adı klasördeki bir yedekten eski, saat geri alınmış (`R20-backup:clock`, `medium`); bu ikisi sonraki başarılı bir yedekle çözülür. Ayrıntı: `state\backup\last.json`, `logs\backup.log` ([YEDEKLEME.md](YEDEKLEME.md) bölüm 7) |
  | `R20-retention` | İmha çalışması reddedildi ya da yarım kaldı; tamamlanan bir imha da bilgi (`info`) düzeyinde, imha tutanağının adıyla yazılır |
  | `R20-spool` | Ham log diske yazılamıyor (dolu disk, donanım); düzelince kaç mesajın kaybolduğu ikinci bir uyarıyla bildirilir |
  | `R20-spool-torn` | Elektrik kesintisi, çökme ya da dolu disk yüzünden yarım kalmış son satır bir satır sonuyla kapatıldı (baytlar değiştirilmez; yarım satır tek bir bozuk satır olarak kalır) |
  | `R20-disk` | Veri ya da sistem diski %80'den fazla dolu (`alerts.disk_used_percent`) |
  | `R20-parse` | Bir kaynağın son bir saatteki satırlarının %1'inden fazlası ayrıştırılamadı |
  | `R20-job` | Bir worker işi art arda 3 kez başarısız (`logs\worker.log`) |
  | `R20-backup` | Veritabanı yedeği başarısız, 36 saatten eski ya da kurulumdan 48 saat sonra hâlâ yok |
  | `R20-email` | Uyarı e-postası gönderilemedi (`[smtp]`, `smtp.password`, güvenlik duvarı); kendisi e-postayla gitmez |
  | `R20-report` | Zamanlanmış bir kullanım raporu üretilemedi ya da bir alıcıya gönderilemedi (Raporlar sayfasındaki çalıştırma geçmişi, `[smtp]`; bölüm 14 ve 15). Zamanlanmış kullanım raporları modülü lisansta kapalıyken açılmaz; modül kapanınca kendiliğinden çözülür |
  | `R20-license` | Ücretli modüller kapanıyor ya da kapalı: deneme süresi 3 gün içinde, süreli bir lisans 14 gün içinde bitiyor ya da lisans durumu yüzünden ücretli modüller kapalı (deneme bitti, lisans doğrulanamıyor ya da başka sunucu için, süresi doldu, sürüm bakım kapsamında değil); 5651 işlevleri çalışmaya devam eder. Ayarlar → Lisans ([LISANS.md](LISANS.md)) |
  | `R20-license-limit` | Kullanıcı (yeni kullanıcı eklenemez) ya da log kaynağı (toplanmaya ve mühürlenmeye devam eder) sayısı lisans sınırını aşıyor |
  | `R20-license-maintenance` | Bakım süresi 30 gün içinde bitiyor ya da bitti (`info`: yalnızca yükseltmeler ve destek, lisans geçerli kalır) |
  | `R20-admin` | Tarayıcıdan yönetim sorunu: yönetim hizmeti (ArciAdmin) 2 dakikadan uzun süredir yanıt vermiyor, imzası ya da kurulumu tutmayan bir istek reddedildi (olası kurcalama), bir değişiklik geri alındı, elle müdahale gerekiyor, dosya izinleri değişmiş (konsoldan bir `secret set` sonrası: `Set-ArciVaultAcl.ps1`), `ApplySystem` güvenlik duvarını, saati veya Zamane istemcisini eşitleyemedi ya da politika ağlarının dışında bir log kaynağı buldu. 5651 işlevleri sürer; [AYARLAR.md](AYARLAR.md) bölüm 8 |
  | `R20-config-change` | Önemli yapılandırma değişikliği (korumalı bir ayar): saklama açıldı ya da gün sayısı düştü, replika kapandı ya da hedefi değişti, bir log kaynağı kapatıldı ya da silindi, üretimden teste dönüldü, Kamu SM kimlik bilgileri değişti, bir kök ya da yasal parmak izi eklendi, bir Zamane istemcisi yüklendi ya da onaylandı (yüksek); SMTP ya da yedekleme kapatıldı (orta). Değişikliği bilinen bir yönetici yapmadıysa **Değişiklik geçmişi** sayfasında bekleyeni **İptal et**, uygulanmışı **Geri al** ile geri alın ve denetim kaydına bakın |
  | `R20-lease` | Kira kanıtı sorunu: kira kanıtı (FortiGate DHCP, MikroTik DHCP ya da ARP-LOG) kesildi, FortiGate DHCP kayıtları kullanılmıyor ya da çevrilemedi, yenilemeler görünmüyor, aynı ağda iki DHCP kaynağı, bir kaynağın saati kayık ya da bir ayar sahada doğrulanmadı; neden ve ne yapılacağı uyarının ayrıntısındadır (Kaynaklar → Kira kanıtı; [UYARILAR.md](UYARILAR.md) bölüm 2) |
  | `R20-nat` | İnternet çıkışı tanımı sorunu: eski NAT ayarları aktarılamadı, çıkışlar ayarı okunamıyor, geri almada eski bir NAT ayarı değişti ya da hiçbir çıkışa tanımlı olmayan bir NAT adresi kullanılıyor (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)) |
  | `new-log-source` | Yapılandırmada olmayan bir gönderici (güvenlik duvarı izin verdiyse) |
  | `wrong-transport` | Bir kaynaktan izin verilmeyen taşıma yoluyla mesaj geldi (`[[sources]] transport`); taklit edilmiş bir UDP gönderimi ya da yanlış cihaz ayarı olabilir, mesaj kaydedilmedi |

  `R15`, `R20-seal`, `R20-seal-config`, `R20-seal-skip`, `R20-clock`, `R20-verify`, `R20-spool`
  ve `R20-disk` uyarıları **aynı gün** incelenmelidir. Seal görevinin `LastTaskResult` değerine
  ve `pending_days`'e de her gün bakın; ayrıntı `D:\ARCILOGGER\logs\core.log`. E-posta
  ayarlıysa ([UYARILAR.md](UYARILAR.md) bölüm 4) yüksek ve kritik uyarılar e-postayla da gelir;
  e-posta gelmiyorsa `R20-email` uyarısına bakın.
- Durum dosyaları: `D:\ARCILOGGER\state\collector-status.json`, `ingest-status.json`,
  `worker-status.json`.

Her hafta: Görev Zamanlayıcı'da `VerifyWeekly` sonucu; kontör (`& $core credit`); diskteki boş
alan. Her ay: `VerifyMonthly` (bütün zincir) sonucu.

## 10. Yedekler

Ayrıntı, sunucu dışına kopyalama ve geri yükleme: [YEDEKLEME.md](YEDEKLEME.md).

- **Veritabanı**: kullanıcılar, 5651 sorguları, saklanan raporların kayıtları, kişi ve cihaz
  bilgileri ve denetim kaydı ham kayıtlardan yeniden üretilemez. `\ARCILOGGER\Backup` görevi
  her gece 01:40'ta veritabanını `D:\ARCILOGGER\backups\scheduled\` klasörüne yedekler
  (`pg_dump`; son 14 gün, 8 hafta ve 12 ayın birer yedeği kalır) ve sonucu
  `D:\ARCILOGGER\state\backup\last.json` dosyasına yazar; başarısız bir yedek `R20-backup`
  uyarısı üretir. Yükseltme öncesinde kurulum programı ayrıca `D:\ARCILOGGER\backups\db\` içine
  yedek alır. Elle yedek ve denetim:

  ```powershell
  & $db backup run
  & $db backup list
  & $db backup verify
  ```

- Yedekler veritabanıyla **aynı diskte** durur: haftada bir harici bir diske ya da bir NAS'ta ayrı
  bir paylaşıma kopyalayın ([YEDEKLEME.md](YEDEKLEME.md) bölüm 4) ve **üç ayda bir** bir geri
  yükleme tatbikatı yapın (bölüm 6). Her kopyanın SHA-256'sını sunucu dışında kaydedin
  (`SHA256.txt`, bölüm 4): geri yükleme yedekteki SQL'i PostgreSQL süper kullanıcısı olarak
  çalıştırır ve bu özetle denetlenir (`--sha256`). Geri yükleme her zaman yeni bir veritabanına
  yapılır (`& $db backup restore`); canlı veritabanına dokunulmaz.
- **`C:\ProgramData\ARCILOGGER\secret.key`**: veritabanındaki TOTP gizli anahtarları ve şifreli
  kimlik bilgileri onunla çözülür. Kaybolursa her kullanıcının TOTP'yi yeniden kurması ve kimlik
  bilgilerinin yeniden girilmesi gerekir. Anahtarı veritabanı yedeğiyle **aynı yerde ve şifresiz**
  saklamayın: `secret.key`, `arcilogger.toml` ve Caddy CA klasörünü (`caddy\`) kurulumdan sonra
  bir kez şifreli, çevrim dışı ve yedeklerden ayrı bir ortama kopyalayın
  ([YEDEKLEME.md](YEDEKLEME.md) bölüm 3).
- **Kasa** (`D:\ARCILOGGER\vault`): ikinci kopya (`[replica]`) varsayılan olarak **kapalıdır**;
  hedefi (ör. bir NAS'ta WORM klasörü, `[replica] target`) doğrulanınca açın
  ([KURULUM.md](KURULUM.md) bölüm 10, "Replika"; [ASSUMPTIONS.md](ASSUMPTIONS.md) A6).

## 11. Hukuki ön hazırlık

Pilot öncesinde şu taslakları hukuk müşavirine verin ([ASSUMPTIONS.md](ASSUMPTIONS.md) A7):

- [legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md) — çalışan aydınlatma metni
- [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md) — log saklama
  ve imha politikası

## 12. Cihazlar ve Kişiler sayfası

Ne zaman ne yapılacağı bölüm 5'tedir ("Kişiler ve cihaz-kişi atamaları"); bu bölüm sayfanın
kendisini anlatır. Sayfayı bu sürümde yalnızca yönetici açar; kişi adları gösterildiği için önce
KVKK bilgilendirmesi onaylanmalıdır. Cihazlar elle girilmez, DHCP ve ARP kayıtlarından gelir.
Her değişiklik, içe ve dışa aktarma denetim kaydına yazılır.

- **Cihazlar:** arama MAC adresinde (ayraçsız da: `f01faf`), ana bilgisayar adında, son IP'de ve
  cihaz adında yapılır. Filtreler (tür, VLAN, rastgele MAC, altyapı) ve **Bilinmeyen cihazlar**
  (sahibi olmayan, altyapı dışı) adres çubuğunda kalır, sayfa yer imine eklenebilir; arama metni
  adrese yazılmaz. **CSV indir** gösterilen listenin tüm sayfalarını indirir. Cihazları işaretleyip
  **Kişiye ata** / **Atamayı kaldır** kullanın; geçmişe dönük bir başlangıç, cihazın mevcut
  atamasının başlangıcından ya da son atamasının bitişinden önce olamaz. Sunucu reddederse
  pencerede nedeni görünür ve hiçbir cihaz değişmez.
- **Cihaz ayrıntısı** (MAC adresine tıklayın): ad, tür, **Altyapı cihazı** işareti, etiketler,
  notlar; atama geçmişi ve son kira kayıtları. Geçmiş yalnızca eklenerek tutulur: bir kayıt
  düzeltilmez ya da silinmez, yeni atama öncekini kapatır. **Rastgele MAC** uyarısı olan cihaz
  çoğunlukla telefondur; aynı telefonun her MAC adresini aynı kişiye atayın.
- **Kişiler:** arama ad, e-posta, sicil no ve unvanda yapılır. Kişi silinmez, **Pasifleştir**ilir:
  pasif bir kişiye cihaz atanamaz, geçmişi ve geçmiş 5651 sonuçlarındaki bağlantısı korunur, şu
  anki cihazları atanmış kalır (gerekirse Cihazlar sekmesinden kaldırın).
- **Departmanlar ve VLAN'lar:** departman ekleme ve yeniden adlandırma; VLAN'ları ve alt ağları
  ekleme (**VLAN ekle**), düzenleme ve silme (satırın **İşlemler** menüsü) ve her VLAN için
  departman seçimi (tablodaki seçim kutusu). Henüz VLAN yoksa sayfa "Henüz VLAN yok: ağınızı
  ekleyin" der. Kimseye atanmamış cihaz VLAN'ının departmanını alır, VLAN'ın departmanı yoksa
  departmanı da yoktur. Düzenlemede yalnızca değişen alanlar gönderilir; ağı düzeltmek
  VLAN'ın adını, departmanını ve işaretini korur. Silme bir onay ister: o ağdaki cihazlar VLAN
  adını ve VLAN'ın departmanını kaybeder, kullanım sayfalarında da; ham kayıtlar, kira kayıtları
  ve kaydedilmiş 5651 sonuçları değişmez. Değişiklik bir departman değişikliği gibi hemen
  geçerlidir: cihaz listesi ve sonraki 5651 sorguları yeni tabloyu kullanır, kullanım sayfaları
  VLAN'ın departmanını VLAN numarasıyla, okurken alır. FortiGate DHCP kiralarına ise eklenen ya da
  değiştirilen bir ağ ancak kiralar yeniden oluşturulunca uygulanır (bölüm 5). Kullanımın VLAN
  numarası adresi içeren VLAN'ınkidir; adres hiçbir VLAN'ın ağında değilse kira kaydınınki (kayıt
  oluşurken DHCP sunucusunun adından ya da o zamanki ağdan). Bu numara değişirse (numarası değişen,
  yeni eklenen ya da ağı değişen VLAN; bir VLAN'ın ağından çıkan adreslerde — silinen ya da taşınan
  VLAN — yalnızca kira kaydı olmayan saatler) son günlerin kullanımı kendiliğinden yeniden
  hesaplanır (`usage.reattribute`, `usage.auto_rebuild_days`); daha eski günler için `usage rebuild`
  (bölüm 13).
- **İçe/Dışa aktar:** listelerin tamamı CSV olarak (Excel için `;` ayraçlı, zamanlar yerel saatle,
  evet/hayır); dosyalar kişisel veri içerir, yalnızca gerektiği kadar saklayın. Dışa aktarılan
  kişi listesi güncelleme olarak geri yüklenir: `id` sütunu her satırın kişisini belirtir, sicil
  numarası olmayan kişininkini de. İçe aktarmada **Kontrol et (deneme)** hiçbir şey yazmaz ve
  satır satır hataları gösterir; **Uygula** ya tüm satırları ya hiçbirini yazar. Kontrolden sonra
  veriler değiştiyse (ör. kişi pasifleşti) uygulama reddedilir, sayfa yeni hataları gösterir:
  düzeltip yeniden kontrol edin. Atama dosyası yeni cihaz eklemez. İçe aktarma Toplu içe aktarma
  (CSV) modülünü gerektirir (kapalıyken bu bölüm kilitlidir); dışa aktarma her zaman açıktır.

5651 sonucunda kişi adımı, sorgulanan anda geçerli atamayı gösterir (ör. "Atama: 01.10.2026
09:00:00 itibarıyla"); atama geçmişinden önce kaydedilmiş sorgularda bu satır yoktur.

## 13. Kullanım raporları: sayılar ne anlama gelir?

Kullanım sayfaları ve raporları kişi, cihaz, departman ve site başına günlük kullanımı
**bağlantı kayıtlarından tahmin eder** (yöntem, ayarlar ve kör noktalar: [KULLANIM.md](KULLANIM.md)).
Bu sayfalar Kullanım analizi modülünü gerektirir ([LISANS.md](LISANS.md)); modül kapalıyken
`usage rebuild` komutu da reddedilir.

- **Çevrimiçi süre**: bir bağlantının açık olduğu dakikalar. **Aktif süre (tahmini)**: arka plan
  dışı trafiği dakikada en az 2 KB olan dakikalar; site süreleri bu dakikaların baytlara göre
  bölünmesidir ve toplamı aktif süreye eşittir. Arka planda açık bir sekme aktif sayılabilir; hiçbir
  sayı kesin değildir.
- Kullanım, cihazın **o anda** atandığı kişiye yazılır (5651 sorgusuyla aynı kural). Kimseye
  atanmamış cihazlar "cihaz", kira kaydı olmayan adresler "IP" olarak görünür.
- Adlar varsayılan olarak görünür (sahibin 2026-10-20 kararı); bir kişi, Kişiler sekmesinde
  kişinin **Kullanım raporlarından hariç tut** kutusuyla (`usage_excluded`) kişi düzeyindeki
  raporların dışında bırakılabilir; 5651 sorguları bundan etkilenmez. Ayrıntı görüntüleme ve dışa
  aktarma denetim kaydına yazılır.
- Dini bayram tarihlerini her yıl `usage.holidays` ayarına girin (yalnızca sabit tarihli resmî
  tatiller hazır gelir; [KULLANIM.md](KULLANIM.md) bölüm 2.5).

**Cihazları kişilere bağladıktan sonra** (bölüm 5) son 7 günün kullanımı en geç 10 dakikada
kendiliğinden yeni kişiye geçer. Daha eski günler için (geriye dönük bir zimmet gibi) o günleri
yeniden hesaplatın:

```powershell
& "$arci\arcilogger-worker.exe" usage rebuild --from 2026-10-01 --to 2026-10-19 --config $cfg
```

## 14. Kullanım raporları (zamanlanmış e-posta)

Ayrıntı: [KULLANIM-RAPORLARI.md](KULLANIM-RAPORLARI.md). Raporlar Zamanlanmış kullanım
raporları modülünü gerektirir ([LISANS.md](LISANS.md)); modül kapalıyken raporlar gönderilmez.

- Kurulumla dört zamanlama gelir — **Gün sonu raporu** (her gün 18:30, günün başından o ana),
  **Tam gün raporu** (her gün 07:00, önceki gün), **Haftalık rapor** (pazartesi 07:15, önceki
  hafta), **Aylık rapor** (ayın 1'i 07:30, önceki ay) — hepsi alıcısız ve **kapalı**. Raporlar
  sahibin kararıyla kişi düzeyinde ve adlarladır; bir zamanlamada kişi düzeyi kapatılabilir ya da
  takma ad seçilebilir.
- Önce uyarı e-postasını kurup deneyin (bölüm 9 ve [UYARILAR.md](UYARILAR.md) bölüm 4): rapor
  e-postası aynı `[smtp]` bağlantısını kullanır. Sonra Raporlar sayfasında zamanlamaya alıcıları
  girip etkinleştirin; **önizleme** ile içeriği, **şimdi çalıştır** ile gönderimi deneyin.
- Her alıcıya ayrı bir e-posta gider: HTML rapor, ekinde PDF ve Excel (toplam 15 MB'ı aşarsa
  ekler yerine indirme bağlantısı, `reports.max_attachment_mb`). Dosyalar
  `D:\ARCILOGGER\exports\usage-reports\` altında 400 gün kalır (`reports.keep_days`); kasaya
  girmez.
- Kullanım süreleri tahminidir (bağlantı kayıtlarından); raporlar bunu her zaman yazar.
  Gönderilemeyen bir rapor `R20-report` uyarısı açar.

## 15. Panel, Güvenlik ve Raporlar sayfaları

Her sayfa ve her bölüm yalnızca görme yetkisi olana görünür; sunucu her istekte yetkiyi yeniden
denetler. Kullanım rakamları bağlantı kayıtlarından hesaplanan **tahminlerdir** (bilgi simgesi).

- **Panel** (başlangıç sayfası): log alımı (aktif kaynaklar, son 60 dakikanın mesaj/dk eğrisi,
  sessiz kaynaklar), dünkü mühür (durum, saat, seri, doğrulama), Kamu SM kontörü ve ne kadar
  yeteceği, saat sapması (son ölçüm 3 saatten eskiyse durum "Bilinmiyor"); bugünkü şirket
  kullanımı (aktif kullanıcı, aktif süre, indirilen ve yüklenen, saatlik grafik ve tablosu), açık
  güvenlik uyarıları (önem derecesine göre sayılar ve en yeni beşi), en çok vakit geçirilen
  siteler, kategoriler (en büyük altısı aktif süredeki paylarıyla, gerisi "Diğer"; ortada günün
  aktif süresi) ve son 30 günün mühür takvimi. Rolün göremediği bölüm hiç gösterilmez (İK
  yalnızca kullanım bölümlerini görür). **Ayrıştırma hataları** bölümü bugün okunamayan satırların
  binde oranını, sayılarını ve en sık kalıpları gösterir; bir kalıbın örnek satırı ham haliyle
  açılır, "Aramada göster" o kaynağın o kalıptaki satırlarını Arama'da açar (toplayıcı biçiminde
  olmayan satırlar veritabanına yüklenmediği için yalnızca örnek satırı vardır). Bölümün altındaki
  bağlantı ilgili sayfayı, takvimdeki
  bir gün o günün mühür ayrıntısını, bir uyarı Güvenlik sayfasında o uyarıyı açar. Canlı
  bölümler 30 saniyede, kullanım 5 dakikada bir kendiliğinden yenilenir; sekme arka plandayken
  yenileme durur ve bu yenilemeler oturumu açık tutmaz.
- **Güvenlik:** üstte **Güvenlik Özeti** (önem derecesine göre açık uyarılar, inceleniyor, son 30
  günde ortalama kapanma süresi), altta uyarı listesi: durum, önem (düğmeler), kural (ör. `R15`),
  zaman aralığı ve metin araması (başlık, kural, kaynak); süzgeçler ve açık uyarı adres çubuğunda
  kalır, arama metni kalmaz. Bir uyarıya tıklayınca özet, ayrıntılar, **Ne yapmalı?** (arayüz
  dilinde), geçmiş (sistemin kendiliğinden yaptığı değişiklikler nedeniyle birlikte) ve gönderilen
  e-postalar (alıcı adresleri kısaltılmış) açılır. Uyarı yönetimi yetkisi olan kullanıcı durumu
  değiştirir — **Yeni**, **İnceleniyor**, **Kapandı**, **Yanlış alarm**; sayfa yalnızca izin
  verilen değişiklikleri sunar — ve not ekler; her değişiklik uyarının geçmişine ve denetim
  kaydına yazılır. Sorun sürerken kapatılan bir uyarı için denetim bir sonraki çalışmada **yeni**
  bir uyarı açar: üzerinde çalışırken **İnceleniyor**'u kullanın. Kapalı bir uyarı, aynı sorun
  için etkin başka bir uyarı yoksa **Yeniden aç**ılır; varsa sayfa o uyarıya bağlantı verir. Uyarı
  bu arada değiştiyse (başka bir kullanıcı ya da sistem kapattıysa) sayfa bunu söyler ve güncel
  hâlini yükler.
- **Raporlar** (yalnızca yönetici): zamanlanmış raporlar — gün sonu (18:30, günün 00:00'ından
  itibaren), tam gün (07:00, önceki gün), haftalık (pazartesi 07:15, önceki hafta), aylık (ayın
  1'i 07:30, önceki ay). **Yeni zamanlama** / **Düzenle** penceresinde tür, gün ve saat, alıcılar
  (adresi yazıp Enter'a basın; virgüllü bir liste yapıştırılabilir), biçimler (e-posta gövdesi,
  PDF, Excel) ve ayrıntı seçilir; pencere bir sonraki çalışmayı ve kapsadığı dönemi gösterir.
  Raporlar varsayılan olarak **kişi düzeyinde ayrıntı (adlarla)** içerir, takma ad seçeneği
  kapalıdır ([ASSUMPTIONS.md](ASSUMPTIONS.md), sahip kararı); alıcıları buna göre seçin. Alıcısı
  olmayan bir zamanlama etkinleştirilemez. **Şimdi çalıştır** raporu kendi dönemiyle (gün sonu:
  bugün şu ana kadar; tam gün: dün; önceki hafta ya da ay) ya da seçilen en fazla 92 günlük bir
  dönemle hazırlar, e-posta isteğe bağlıdır (sunucuda e-posta kapalıysa sayfa bunu söyler); **Önizle** raporu kaydetmeden ve
  göndermeden gösterir (PDF yeni sekmede, e-posta sayfa içinde). **Çalışma geçmişi** her alıcının
  teslim durumunu ve dosyaları gösterir; dosya indirmek denetim kaydına yazılır. Bir zamanlama
  silinince eski çalışmaları ve dosyaları kalır. Rapor e-postasındaki bağlantı o çalışmayı
  doğrudan açar.

## 16. Kullanım sayfası

Bu sayfa Kullanım analizi modülünü gerektirir ([LISANS.md](LISANS.md)); modül kapalıyken sayfa
ve paneldeki kullanım bölümleri kilitli görünür. **Kullanım** sayfası (`usage.person` veya
`usage.anonymous`; bu sürümde her rolde var) şirketin
internet kullanımını şirket, departman, kişi, cihaz, site ve kategori bazında gösterir. Sahibin
20.10.2026 kararıyla kişi ve cihaz adları varsayılan olarak görünür; **Şirket** dışındaki her
sekme için önce KVKK bilgilendirmesi onaylanmalıdır. Tüm süreler **tahminidir** (ⓘ "tahmini"):
bağlantı kayıtlarından 1 dakikalık dilimlerle hesaplanır, kesin ekran süresi değildir, arka
planda açık sekmeler de sayılabilir (yöntem: [KULLANIM.md](KULLANIM.md)). Her görünümün
üstündeki satır verilerin ne zamana kadar işlendiğini söyler ("Veriler 14:35'e kadar işlendi";
biten bir gün için "Veriler 12.10.2026 sonuna kadar işlendi").

- **Dönem ve saatler:** Bugün (varsayılan), Dün, Bu hafta, Geçen hafta, Bu ay, Geçen ay ya da
  takvimden özel aralık (haftalar pazartesi başlar); ‹ › önceki/sonraki dönem. **Tüm saatler /
  Mesai içi / Mesai dışı** sunucudaki mesai ayarına göredir (varsayılan Pazartesi–Cuma
  08:00–18:00). Sekme, dönem, saatler ve açılan ayrıntı adres çubuğunda kalır: sayfa yer imine
  eklenebilir, tarayıcının Geri düğmesi bir önceki görünüme döner. Tablolardaki arama metni adrese
  yazılmaz.
- **Şirket:** özet kutuları (aktif kullanıcı, çevrimiçi ve aktif süre, mesai dışı, indirilen,
  yüklenen, oturum, engellenen), saatlik indirilen/yüklenen ve aktif kullanıcı grafikleri (her
  grafiğin tablo görünümü vardır), en çok vakit geçirilen siteler, kategoriler (en büyük onu;
  halka grafikte en büyük altısı ve "Diğer", ortada dönemin aktif süresi), uygulamalar ve
  departmanlar (ne kişinin ne de VLAN'ın departmanı olan trafik "Bilinmiyor" altındadır). Bir
  satıra tıklamak ayrıntısını açar.
- **Departman:** departmanlar → bir departmanın kişileri, VLAN'ındaki kimseye atanmamış cihazlar
  ve MAC'i bilinmeyen IP adresleri tüm ölçümlerle; siteleri ve kategorileri.
- **Kişi:** tüm kişiler. **Cihaz:** kimseye atanmamış cihazlar ve MAC'i bilinmeyen IP adresleri —
  bir kişiye atanmış cihazın trafiği o kişiye sayılır (payı kişinin **Cihazlar** bölümündedir).
  Arama ad, MAC, IP adresi ya da ana bilgisayar adını yazıldığı gibi bulur (Türkçe harflerle,
  ör. "ayşe"). → ayrıntı: özet (cihazın MAC, IP ve ana bilgisayar adı); tek bir gün seçiliyken
  günün zaman çizelgesi (aktif dakikalar kategoriye göre renkli, üzerine gelince saat ve en çok
  kullanılan site); bir dönem için günlere göre tablo ve toplam; saatlik grafikler; son 30 günün
  saatlik ısı haritası; **Siteler** (bir siteye tıklayınca ana bilgisayar adları ve URL'ler),
  **URL'ler** (web filtresinin kaydettiği adresler, engellenenler işaretli, aranabilir),
  Kategoriler, Uygulamalar, Engellenenler (tümü) ve kişinin Cihazları.
- **Site / Kategori:** tüm siteler (alan adıyla arama, kategori filtresi; güncelleme gibi arka
  plan trafiği **arka plan** olarak işaretlidir: çevrimiçi süreye girer, aktif süreye hiç
  girmez) → ana bilgisayar adları ve kimin ne kadar kullandığı (o sitedeki süresi); kategoriler →
  siteleri ve kullanıcıları.
- **Dışa aktar** (tabloların sağında): gösterilen görünüm ve dönem. **CSV** görünümün ana
  tablosunu, **Excel** dosyası her bölümü ayrı bir sayfada ve **Yöntem** sayfasını (yöntem notu)
  içerir; görünümün ana tablosu olmayan bir tabloda yalnızca Excel sunulur. Dosyalar kişisel veri
  içerebilir, yalnızca gerektiği kadar saklayın; her dışa aktarma denetim kaydına yazılır.
  Zamanlanmış PDF raporlar Raporlar sayfasındadır (bölüm 15).
- **Yeniden hesapla** (yalnızca yönetici, sağ üstteki ⋯ menüsü): seçilen günlerin kullanım
  özetleri trafik kayıtlarından baştan hesaplanır — örneğin cihazlar kişilere atandıktan sonra.
  İlerleme pencerede görünür, bitince bütün görünümler yenilenir. Ham kayıtlar değişmez.
  Veritabanındaki en eski logdan önceki günler hesaplanamaz; sunucu bu durumda en erken günü
  söyler ve o günlerin özetleri olduğu gibi kalır.

İsteğe bağlı `usage.pseudonymize_anonymous_role` ayarı açılırsa (varsayılan kapalı) yalnızca
`usage.anonymous` yetkisi olan roller ad yerine takma ad görür ("Kullanıcı-7F3A", **anonim**
rozeti). Takma adlar sunucuda bir anahtar ister: kaydedilene kadar bu roller listeler yerine
sunucunun açıklamasını görür; uzun, rastgele bir metni `arcilogger-core secret set
usage.pseudonym_key` ile kaydedin (komut metni sorar; bir metin dosyasında saklamayın). Denetçi
Denetim Kaydı'nda da kişileri ve cihazları takma adlarıyla görür; ama arama ve 5651 sorgusu
yetkileriyle adreslere ulaşabildiği için takma adlar Denetçi için zayıf bir korumadır
([KULLANIM.md](KULLANIM.md) bölüm 6).

## 17. Lisans

Ayrıntı: [LISANS.md](LISANS.md) (modüller, durumlar, sınırlar, bakım süresi, sunucu değişikliği).

1. Kurulumdan sonra ARCILOGGER **14 gün** bütün modüllerle çalışır; her sayfada "Deneme sürümü —
   N gün kaldı." bandı görünür. Deneme bitince ya da lisans sorunluyken yalnızca ücretli modüller
   (Kullanım analizi, Zamanlanmış kullanım raporları, Toplu içe aktarma (CSV)) kapanır; 5651
   işlevleri (log toplama, mühürleme, 5651 sorgusu, kanıt paketleri, uyarılar, denetim kaydı,
   kullanıcılar) her durumda çalışır.
2. Kuruluş adının (`site_name`) girildiğini denetleyin (bölüm 0): istek koduna o girer.
3. İstek kodunu alın: kurulumun sonundaki 6. adım, web arayüzünde **Ayarlar → Lisans →
   Etkinleştirme** (Kopyala / İndir (.txt)) ya da:

   ```powershell
   & $db license request-code --config $cfg
   ```

4. Kodu **arman@khadangan.com** adresine e-postayla gönderin; istediğiniz modülleri, log kaynağı
   ve kullanıcı sayısını yazın. Sunucu dışarıya hiçbir zaman bağlanmaz.
5. Gelen `.lic` dosyasını **Ayarlar → Lisans → Lisans dosyası**'ndan yükleyin (parola yeniden
   sorulur) ya da:

   ```powershell
   & $db license verify "C:\Kurulum\ARCI-L-<numara>.lic" --config $cfg
   & $db license install "C:\Kurulum\ARCI-L-<numara>.lic" --config $cfg
   ```

6. Durumu denetleyin: lisans sayfası ya da `& $db license status --config $cfg`. Web arayüzü ve
   worker yeni durumu yeniden başlatma olmadan en geç bir dakikada görür.

Geçerli bir lisans yokken (deneme bitti, geçersiz dosya) etkin kullanıcı sayısı 3'e ulaşınca
yeni kullanıcı eklenemez; var olan kullanıcılar çalışmaya devam eder (bölüm 2). Log kaynağı sınırı yumuşaktır: sınırın üstündeki kaynakların logları da toplanır ve mühürlenir.
Sunucu değişince (yeni donanım, Windows'un yeniden kurulması) yeni istek kodu gönderin. Bakım
süresi yükseltmeleri sınırlar: [KURULUM.md](KURULUM.md) bölüm 13.
