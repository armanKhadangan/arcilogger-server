# Kabul testi: Windows 11 Pro 25H2 üzerinde ARCILOGGER

> Kurulum belgesi: [KURULUM.md](KURULUM.md) (özellikle bölüm 17, "Masaüstü Windows 11 Pro").
> English summary at the end.

GitHub'ın derleme sunucuları yalnızca Windows Server çalıştırır; ARCILOGGER'ın masaüstü Windows'ta
kurulumu, yükseltmesi, geri alınması ve kaldırılması orada denenemez. Bu test, masaüstü desteğini
duyurmadan önce **atılabilir** bir Windows 11 Pro 25H2 bilgisayarda ya da sanal makinede yaklaşık
bir buçuk saatte elle yapılır. Gerçek sunucuda ya da gerçek verilerle **yapmayın**: test, Windows
ayarlarını değiştirir ve sonunda ARCILOGGER'ı kaldırır.

Her adımda **beklenen** sonucu yazılana göre karşılaştırın ve sondaki tabloya "geçti / kaldı /
not" yazın. Beklenenden farklı her sonuç bir bulgudur: ekran görüntüsünü, komut çıktısını ve kurulum
günlüğünü (`C:\ARCILOGGER\logs\ARCILOGGER-*.log`) saklayın.

Tüm komutlar **yönetici olarak açılmış Windows PowerShell** penceresinde çalışır.

## 0. Hazırlık (yaklaşık 15 dakika)

1. Makine: Windows 11 Pro **25H2** (build 26200), x64, TPM 2.0 ve Güvenli Önyükleme (Hyper-V'de
   2. nesil sanal makine, sanal TPM açık), 4 çekirdek, 8–16 GB RAM, **tek disk** (yalnızca `C:`,
   en az 160 GB boş: kurulum en az 100 GB boş alan ister), ağ bağlantısı. S modu kapalı.
2. Windows güncellemeleri kurulmuş olsun; mümkünse **bekleyen bir güncelleme** bırakın (adım 9).
3. PostgreSQL 16'yı EDB yükleyicisiyle kurun: [KURULUM.md](KURULUM.md) bölüm 2; yerel ayar
   (Locale) **`C`**, veri klasörü `C:\ARCILOGGER\db`.
4. Kurulum dosyalarını bir klasöre koyun: GitHub sürümünden `ARCILOGGER-Setup-<sürüm>.exe` ve
   `SHA256SUMS.txt` (KURULUM bölüm 4.1). Yükseltme adımı için **ikinci bir paket** gerekir: bir
   sonraki sürüm ya da aynı koddan ikinci kez oluşturulmuş paket (her derlemenin parmak izi
   farklıdır).
5. Kurulumdan **önceki** değerleri kaydedin (adım 7 ve 10 bunlarla karşılaştırır):

   ```powershell
   $out = "$env:USERPROFILE\Desktop\kabul-once.txt"
   & {
     powercfg /getactivescheme
     powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
     powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE
     powercfg /a
     Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
     Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name HibernateEnabled -ErrorAction SilentlyContinue
     Get-NetAdapterPowerManagement -ErrorAction SilentlyContinue | Format-List Name, AllowComputerToTurnOffDevice
     auditpol /get /subcategory:"{0CCE921D-69AE-11D9-BED3-505054503030}"
     (Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow | Measure-Object).Count
   } *> $out
   ```

6. **Akıllı Uygulama Denetimi** (Smart App Control) durumunu not edin: Windows Güvenliği →
   Uygulama ve tarayıcı denetimi → Akıllı Uygulama Denetimi (Açık / Değerlendirme / Kapalı).

## 1. Kurulum (yaklaşık 15 dakika)

1. `Get-FileHash .\ARCILOGGER-Setup-<sürüm>.exe -Algorithm SHA256` değerini `SHA256SUMS.txt` ile
   karşılaştırın. **Beklenen:** aynı.
2. `ARCILOGGER-Setup-<sürüm>.exe` dosyasına çift tıklayın, sihirbazı geçin. Açılan PowerShell
   penceresi kurulum bilgilerini sorar ([KURULUM.md](KURULUM.md) bölüm 7.1): kuruluş adı (deneme
   için ör. `Örnek A.Ş.`), yerel saat kaynağı (ağınızın yönlendiricisi ya da Enter: yok) ve
   yönetici PC'sinin adresi (bu bilgisayara bağlanacağınız bilgisayarın adresi ya da ağı).
   Ardından postgres parolasını girin.
3. Ön denetim satırlarını okuyun. **Beklenen:**
   - `[OK]` Windows 11 Pro (build 26200, Professional); `[OK]` x64;
   - `[BİLGİ]` Windows istemci lisansı: "Windows lisans koşullarını bir Microsoft iş ortağıyla
     doğrulayın";
   - `[BİLGİ]` güç ayarları değiştirilecek, önceki değerler `power-state.json` dosyasına yazılacak;
   - `[BİLGİ]` veri klasörü otomatik seçildi: `C:\ARCILOGGER` (D: yok) ve **uyarı**: veri klasörü
     Windows ile aynı diskte;
   - sunucu adresi bulundu (bilgisayarın adresi); adres DHCP ile alındıysa **uyarı**;
   - kuruluş adı ve yönetici adresi `[OK]`; saat kaynağı boş bırakıldıysa **uyarı** (saat
     yalnızca `time.ume.tubitak.gov.tr` ile eşitlenir);
   - log kaynağı yok **uyarısı**: yeni `arcilogger.toml` hiçbir `[[sources]]` içermez, güvenlik
     duvarı 514'ü hiçbir göndericiye açmaz (adım 6'da eklenir);
   - toplayıcı bu adreste dinler (`[OK]`);
   - `[BİLGİ]` "Kurulu lisans yok; lisans durumu kurulumdan sonra geçerli [no licence installed]"
     (ARCILOGGER lisansı, [LISANS.md](LISANS.md)); `[HATA]` satırı yok.
4. Kurulumun sonunda, "Kurulum günlüğü" satırından **sonra**:
   - ilk yöneticinin kurulum anahtarı, tarayıcı adresi (`https://<adres>/setup`, bu bilgisayarda
     `https://localhost/setup`) ve tek satırlık açıklama görünür;
   - "Kamu SM müşteri numarası ve parolasını şimdi girmek ister misiniz? [E/h]" sorulur. Kamu SM
     test hesabınız yoksa `h` yazın;
   - "Kapatmak için Enter'a basın" yazar; pencere Enter'a basana kadar açık kalır.
   - Sonraki adımlar, `[[sources]]` kayıtlarının `C:\ProgramData\ARCILOGGER\arcilogger.toml`
     dosyasına eklenmesini söyler; 6. adım "Lisans [licence]: deneme süresi 14 gündür [14-day
     trial]" der, altında `ARCIREQ1-` ile başlayan istek kodunu ve arman@khadangan.com'a gönderme
     satırlarını yazar. Kodu tabloya not edin: web arayüzünde Ayarlar → Lisans'taki kodla aynı
     olmalıdır.
   - Kurulum sihirbazı "uyarılarla kuruldu" (kod 1) bildirir: tek disk, DHCP ve log kaynağı yok
     uyarıları beklenen uyarılardır.
5. Anahtarın hiçbir dosyada olmadığını denetleyin. Anahtarı komut satırına **yazmayın**:
   PowerShell (PSReadLine) komut satırlarını `ConsoleHost_history.txt` geçmiş dosyasına kaydeder
   ve anahtar o anda henüz kullanılmamıştır. `Read-Host` sorusuna yapıştırın; yanıt geçmişe
   yazılmaz ve aramaya geçmiş dosyası da girer:

   ```powershell
   $anahtar = [System.Net.NetworkCredential]::new('', (Read-Host -AsSecureString 'Kurulum anahtarı')).Password
   Select-String -Path "$env:TEMP\ARCILOGGER-*.log", 'C:\ARCILOGGER\logs\ARCILOGGER-*.log', (Get-PSReadLineOption).HistorySavePath -SimpleMatch $anahtar
   Remove-Variable anahtar
   Get-Content C:\ProgramData\ARCILOGGER\install\state.json
   ```

   **Beklenen:** `Select-String` hiçbir satır bulmaz; `state.json` içinde `server_address` ve
   `server_name` vardır, anahtar yoktur. Kamu SM bilgilerini girdiyseniz müşteri numarası da hiçbir
   günlükte geçmez ve `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" secret list`
   ikisini gösterir.

## 2. Hizmetler ve hesaplar (yaklaşık 5 dakika)

```powershell
foreach ($name in 'ArciCollector', 'ArciIngest', 'ArciApi', 'ArciWorker', 'ArciWeb') { sc.exe qc $name | Select-String 'SERVICE_START_NAME|START_TYPE' }
Get-Service Arci* | Format-Table Name, Status, StartType
secedit /export /areas USER_RIGHTS /cfg "$env:TEMP\haklar.inf" | Out-Null
Select-String -Path "$env:TEMP\haklar.inf" -Pattern 'SeServiceLogonRight'
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Format-List ProcessId, CommandLine
```

**Beklenen:** her hizmet için `SERVICE_START_NAME : NT SERVICE\<ad>`; beşi de `Running`. "Hizmet
olarak oturum aç" hakkı (`SeServiceLogonRight`) satırını tabloya kopyalayın: içinde `*S-1-5-80-0`
(`NT SERVICE\ALL SERVICES`) var mı? Bu, Windows 11'de doğrulanmamış bir sorudur (kaynaklar
çelişiyor); hizmetler çalışıyorsa hak fiilen vardır. Bir hizmet "1069" (oturum açma hatası) ile
başlamıyorsa bulgu olarak kaydedin. Dört Python hizmetinin komut satırı
`…\venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_<paket> …` olmalıdır (yalıtılmış Python,
[LISANS.md](LISANS.md) bölüm 11).

## 3. Güvenlik duvarı ve denetim (yaklaşık 5 dakika)

```powershell
Get-NetFirewallRule -Group '@FirewallAPI.dll,-25000' | Select-Object -First 1 DisplayGroup, Group
Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow | Format-Table DisplayName, Group -AutoSize
auditpol /get /subcategory:"{0CCE921D-69AE-11D9-BED3-505054503030}"
```

**Beklenen:** "Core Networking" grubu bulunur (Türkçe Windows'ta görünen adı farklı olabilir; `Group`
değeri `@FirewallAPI.dll,-25000`). Etkin gelen izin kurallarında yalnızca `ARCILOGGER` grubu ve
Core Networking vardır. "Dosya Sistemi" denetimi başarı ve hata için açıktır; kurulum günlüğünde
"auditpol başarısız" uyarısı yoktur (varsa bulgu: auditpol'un Pro'da GUID ile çalışması
doğrulanmamıştı).

## 4. Zamanlanmış görevler LOCAL SERVICE ile (yaklaşık 5 dakika)

```powershell
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Format-Table TaskName, State, @{ n = 'Hesap'; e = { $_.Principal.UserId } }
Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName ClockCheck
Start-Sleep -Seconds 60
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult
```

**Beklenen:** her görevin hesabı `LOCAL SERVICE` (ya da `S-1-5-19`); ClockCheck için
`LastTaskResult` `0`. Seal beş dakikada bir çalışır; Kamu SM kimlik bilgileri ve Zamane istemcisi
olmadan `0x1` ile durabilir ([KURULUM.md](KURULUM.md) bölüm 15, "Gece mühürlemesi olmuyor"):
o zaman `C:\ARCILOGGER\logs\core.log` nedenini yazar ve bu, bu testte beklenen sonuçtur. "Erişim
reddedildi" türünden bir hata ise bulgudur (NTFS hakları).

## 5. Akıllı Uygulama Denetimi ve arayüz (yaklaşık 5 dakika)

1. Akıllı Uygulama Denetimi durumunu yeniden okuyun. **Beklenen:** kurulum durumu değiştirmedi.
   Açıksa ve bir ARCILOGGER programını engellediyse hizmet başlamaz: Olay Görüntüleyicisi →
   Uygulamalar ve Hizmet Günlükleri → Microsoft → Windows → CodeIntegrity → Operational
   olaylarını kaydedin (**doğrulayın**: günlüğün adı).
2. Kök sertifikayı bu bilgisayara kurun ve arayüzü açın ([KURULUM.md](KURULUM.md) bölüm 17,
   "Arayüzü bu bilgisayarda açmak"). **Beklenen:** `https://localhost` sertifika uyarısı olmadan
   açılır; `https://localhost/setup` sayfasında anahtarla ilk yönetici oluşturulur. Sayfanın adım
   listesinde üçüncü adım olarak **Kurulum sihirbazı** ("Girişten sonra, Ayarlar'da") görünür
   (sihirbazın kendisi: adım 6a).

## 6. Syslog alımı (yaklaşık 5 dakika)

Yeni kurulumda `C:\ProgramData\ARCILOGGER\arcilogger.toml` hiçbir `[[sources]]` içermez ve
güvenlik duvarı 514'ü hiçbir göndericiye açmaz. Mesajı göndereceğiniz test bilgisayarının adresini
bir kaynak olarak ekleyin (`name = "kabul"`, `type = "generic"`, `address = "<test bilgisayarının
adresi>"`): tarayıcıda **Ayarlar → Kaynaklar** sayfasından ya da konsolda `arcilogger.toml`
dosyasına bir `[[sources]]` tablosu olarak ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4). Toplayıcı
ve alım (ArciIngest) yeni kaynağı yeniden başlatmadan birkaç saniyede yükler, sessiz kaynak uyarısı
(R15) onu izler; `ApplySystem` görevi 514'ü toplayıcı kaynağı tanıyınca açar (elle değişiklikte en
geç 5 dakika). Hizmetleri yeniden başlatmak gerekmez. Tarayıcıdan yönetim kapalıysa güvenlik
duvarını kendiniz uygulayın (KURULUM bölüm 12):

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
```

`config-check` artık kaynak uyarısı vermez. Sonra o bilgisayardan bir UDP mesajı gönderin:

```powershell
$udp = New-Object System.Net.Sockets.UdpClient
$bytes = [System.Text.Encoding]::ASCII.GetBytes('<134>kabul-testi-1')
[void]$udp.Send($bytes, $bytes.Length, '<ARCILOGGER adresi>', 514)
$udp.Close()
```

**Beklenen:** kaynak eklenmeden önce mesaj hiçbir yere yazılmaz (514 kapalı); ekledikten sonra
birkaç saniye içinde `C:\ARCILOGGER\spool\kabul\<gün>.log` dosyasında `kabul-testi-1` satırı
görünür.

## 6a. Uyum Durumu, Sistem ve ilk kurulum sihirbazı (yaklaşık 20 dakika)

Sayfaların ne gösterdiği: [UYUM-DURUMU.md](UYUM-DURUMU.md). Uyum Durumu sunucunun verisi
çoğaldıkça değişir; bu adım kartların doğru durumu değil, sayfaların, raporun, onayın ve
yetkilerin çalıştığını sınar. Uyum Durumu'nun yanıtı 30 saniye saklanır: bir değişiklikten sonra en
geç 30 saniye bekleyip **Yenile**'ye basın.

1. İlk yöneticiyle oturum açın ve menüden **Uyum Durumu** sayfasını açın. **Beklenen:** üstte
   "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır." ile başlayan not; "…
   denetimden … tanesi tamam" özet satırı; "Güncellendi" saati; 1'den 10'a on kart. Her kartta
   numara, başlık, simgesi ve metniyle durum (**Tamam**, **Dikkat**, **Sorun** ya da **Bilgi yok**;
   durum hiçbir yerde yalnızca renkle gösterilmez), neden, ayrıntılar ve "Belge: UYUM-DURUMU.md
   bölüm 2.n" satırı vardır. Bu yeni kurulumda en az şunlar görünür: kart 5 **Sorun**
   ("Damgalar Kamu SM üretim sunucusundan alınmıyor (sağlayıcı: Kamu SM test) …"), kart 7
   **Bilgi yok** ("FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor."), kart 9
   **Sorun** ("Replika kapalı: …"). Tamam olmayan kart bulunduğu için "İlk kurulum sihirbazını aç"
   bağlantısı görünür. Diğer kartların durumunu ve nedenini tabloya yazın: kurulumun saatine ve
   adım 6'daki kaynağa bağlıdırlar.
2. **Uyum Durumu Raporu (PDF)** düğmesine basın. **Beklenen:** `ARCILOGGER-uyum-durumu-<YYYYAAGG-SSDD>.pdf`
   iner. Raporda başlık Türkçe ve İngilizce, kuruluş adı (`Örnek A.Ş.`), oluşturulma zamanı,
   "Oluşturan / Exported by: <görünen ad> (<kullanıcı adı>)", özet satırı, not ve on kart vardır;
   her kartın durumu metinle yazılır ("[TAMAM] / [OK]", "[SORUN] / [PROBLEM]" …); her sayfanın
   altında "Sayfa n / N" yazar. Raporda, raporu indiren hesabın görünen adı ve kullanıcı
   adı dışında kişi adı, istemci adresi ya da cihaz adı geçmez. **Denetim Kaydı** sayfasında "Uyum Durumu Raporu indirildi"
   (`compliance.report_export`) girdisi vardır; ayrıntısındaki `sha256` değeri dosyanınkiyle
   aynıdır (büyük/küçük harf dışında):

   ```powershell
   Get-FileHash "$env:USERPROFILE\Downloads\ARCILOGGER-uyum-durumu-*.pdf" -Algorithm SHA256
   ```

3. Yapılandırma yedeği onayı ([YEDEKLEME.md](YEDEKLEME.md) bölüm 3). Önce bir veritabanı yedeği
   alın:

   ```powershell
   Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName Backup
   Start-Sleep -Seconds 120
   Get-Content C:\ARCILOGGER\state\backup\last.json
   ```

   **Beklenen:** `last.json` içinde `"ok": true`; kart 10 **Dikkat**, "Yapılandırma dosyalarının
   çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3)." Kart 10'daki **Yapılandırma
   yedeğini aldım** düğmesine basın (bu atılabilir makinede kopyayı gerçekten almanız gerekmez;
   gerçek sunucuda kopyayı almadan onaylamayın). **Beklenen:** onay penceresi
   `compliance.confirmBackupBody` metnini gösterir ("arcilogger.toml, secret.key ve Caddy CA
   klasörünün (özel anahtarıyla) şifreli, çevrim dışı ve yedeklerden ayrı saklanan bir ortama
   kopyalandığını onaylıyorsunuz …"); onaydan sonra kart 10 **Tamam**, nedeninde son yedeğin
   zamanı ve boyutu ile onayın zamanı vardır; kart onaylayanın adını göstermez. Denetim Kaydı'nda
   "Yapılandırma yedeği onaylandı" (`compliance.config_backup_confirm`) girdisinin `config_sha256`
   değeri, büyük/küçük harf dışında, şununla aynıdır:

   ```powershell
   Get-FileHash C:\ProgramData\ARCILOGGER\arcilogger.toml -Algorithm SHA256
   ```

4. **Ayarlar → Yedekleme** sayfasında `[backup] keep_daily` değerini 14'ten 15'e çevirin (parola
   yeniden sorulur) ve değişiklik etkin olunca Uyum Durumu'nu yenileyin. **Beklenen:** kart 10
   **Dikkat**, "Yapılandırma son onaydan (…) sonra değişti; yeni bir çevrimdışı kopya alın."
   Değeri 14'e geri alın ve yeniden onaylayın: denetim kaydında ikinci bir onay girdisi oluşur.
5. **Ayarlar → Kullanıcılar ve Roller** sayfasında `denetci` adlı, rolü denetçi olan bir kullanıcı
   oluşturun; başka bir tarayıcı penceresinde (gizli pencere) onunla oturum açın (iki adımlı
   doğrulama denetçi için zorunludur). **Beklenen:** menüde Uyum Durumu vardır, Sistem yoktur;
   kartlar ve **Uyum Durumu Raporu (PDF)** düğmesi görünür; **Yapılandırma yedeğini aldım** düğmesi
   ve "İlk kurulum sihirbazını aç" bağlantısı görünmez; kartların Kaynaklar, Güvenlik, Ayarlar ve
   Sistem bağlantıları gizlidir, Mühürleme ve Arama bağlantıları görünür; `https://localhost/system`
   adresi Sistem verisini göstermez. Denetçinin indirdiği rapor "Oluşturan / Exported by" satırında
   denetçinin adını taşır ve denetim kaydına onun adıyla `compliance.report_export` olarak yazılır.
6. Yöneticiyle **Sistem** sayfasını açın. **Beklenen:** sırasıyla Kaynak kullanımı (işlemci ilk
   açılışta "ölçülüyor", **Yenile**'den sonra bir yüzde; bellek; çalışma süresi), Diskler, Veritabanı,
   Toplayıcı, İçe aktarma, Hizmetler, Zamanlanmış görevler, Worker işleri, Saat, Sürüm ve
   güncelleme bölümleri. Tek diskli bu makinede tek bir disk göstergesi (`C:`) vardır; değer metin
   olarak yazılır, %80 ve %95 işaretleri görünür; tahmin satırı "Tahmin için en az 7 günlük ölçüm
   gerekiyor (…/7)." der. Toplayıcı bölümünde `kabul` kaynağı adıyla görünür; tanınmayan
   göndericiler yalnızca sayılır, adresleri yazılmaz. Zamanlanmış görevler tablosunda ClockCheck'in
   adım 4'teki ve Backup'ın bu adımdaki çalışması görünür. Sürüm bölümünde "ARCILOGGER güncelleme
   indirmez." cümlesi vardır. **Panel**'de "Disk" kartı aynı yüzdeyi ve "Sistem sayfasını aç"
   bağlantısını gösterir. Disk örneği dosyasını okuyun:

   ```powershell
   Get-Content C:\ARCILOGGER\state\system-history.json
   ```

   **Beklenen:** `"schema": 1` ve `days` içinde bugünün tarihiyle bir örnek (diskin etiketi,
   toplam ve kullanılan bayt); başka bir şey yoktur.
7. Yöneticiyle **Ayarlar** sayfasındaki **İlk kurulum sihirbazı** kartını açın
   (`https://localhost/settings/setup`). **Beklenen:** solda adım listesi; her adımda simgesi ve
   metniyle **Tamam** ya da **Eksik**; açılan adım, tamamlanmamış ilk adımdır. "Yönetici ve iki
   adımlı doğrulama" ve "Log kaynakları" (adım 6'daki kaynak) **Tamam**; "Kamu SM: test sunucusu",
   "Kamu SM: üretim", "Replika" ve "E-posta" **Eksik**. Log kaynakları adımı güvenlik duvarı notunu
   ve "Cihaz ayarları: DEVICE-SETUP.md bölüm 1 ve 4 (komutlar sahada doğrulanmalıdır)." satırını
   gösterir. Bir adımda **Ayarlar'da aç** ilgili Ayarlar sayfasını açar ve sayfanın dönüş bağlantısı
   "Kurulum sihirbazına dön" olur; **Bu adımı şimdilik atla** sonraki adıma geçer, atlanan adım
   **Eksik** kalır. **Özet** adımı her adımın durumunu, yapılandırma sorunlarını, "Kurulum sonrası
   denetim listesi: KURULUM.md bölüm 12" satırını ve Uyum Durumu bağlantısını gösterir. Denetçi
   `/settings/setup` adresini açamaz.

## 7. Güç ayarları (yaklaşık 3 dakika)

Hazırlık adım 5'teki komutları bu kez `kabul-sonra.txt` dosyasına yazdırın ve karşılaştırın.
**Beklenen:**

- `STANDBYIDLE` ve `HIBERNATEIDLE` için "Current AC Power Setting Index" `0x00000000`; DC değerleri
  değişmedi;
- `powercfg /a`: hazırda bekletme ve Hızlı Başlatma kullanılamıyor;
- `HiberbootEnabled` `0`;
- ağ bağdaştırıcılarında `AllowComputerToTurnOffDevice` `Disabled` (bağdaştırıcı ya da Windows
  yeniden başladıktan sonra) ya da sürücü desteklemiyorsa `Unsupported`; komut yoksa kurulum
  günlüğünde Aygıt Yöneticisi'ni gösteren satır;
- `C:\ProgramData\ARCILOGGER\install\power-state.json` içinde `previous` altında **kurulum öncesi**
  değerler.

## 8. Yükseltme ve geri alma (yaklaşık 10 dakika)

1. İkinci paketin `ARCILOGGER-Setup-<sürüm>.exe` dosyasını çalıştırın. **Beklenen:** "yükseltme
   yapılacak" yazar; sunucu adresi sorulmaz ve ön denetimde "hatırlanan" olarak görünür; önce
   veritabanı yedeği alınır (`C:\ARCILOGGER\backups\db\*.dump`); kurulum anahtarı ve Kamu SM
   sorusu **gelmez** (yalnızca ilk kurulumda); pencere Enter bekler. `power-state.json` içindeki
   `previous` değerleri değişmedi (hâlâ kurulum öncesi değerler).
2. Geri alın:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Rollback
   Get-Content "C:\Program Files\ARCILOGGER\VERSION.json"
   ```

   **Beklenen:** çıkış kodu `0` ya da `1`; `VERSION.json` ilk sürümü gösterir; hizmetler çalışır;
   adım 6'daki mesaj yeniden gönderildiğinde spool'a yazılır.

## 9. Zorunlu güncelleme yeniden başlatması (yaklaşık 10 dakika)

1. Saati not edin. Ayarlar → Windows Update → bekleyen güncellemeyi kurup **Şimdi yeniden
   başlat** (bekleyen güncelleme yoksa benzetim için `shutdown /r /t 0`; tabloya hangisini
   yaptığınızı yazın). Yeniden başlatma sürerken adım 6'daki mesajı `kabul-testi-kayip` metniyle
   birkaç kez göndermeyi deneyin.
2. Windows açılınca, oturum açmadan önce ve sonra:

   ```powershell
   Get-Service Arci* | Format-Table Name, Status
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" status
   Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult
   ```

**Beklenen:** tam bir açılış (Hızlı Başlatma kapalı); `ArciCollector` oturum açmadan da çalışır,
diğer hizmetler gecikmeli otomatik başlangıçla birkaç dakika içinde `Running` olur; yeniden
başlatmadan **sonra** gönderilen mesaj spool'a yazılır. Yeniden başlatma **sırasında** gönderilen
`kabul-testi-kayip` mesajları spool'da **yoktur** ve ARCILOGGER bu boşluğu bir uyarıyla
göstermez: UDP yeniden göndermez, manifest yalnızca tek adresli, beklenen bir kaynağın 60
dakikadan uzun sessizliğini boşluk olarak listeler. Bunu tabloya "beklenen davranış" olarak
yazın; boşluğun süresini (son mesaj ile ilk mesaj arası) not edin.

## 10. Kaldırma (yaklaşık 5 dakika)

Ayarlar → Uygulamalar → Yüklü uygulamalar → ARCILOGGER → Kaldır (aynı betiği `-Uninstall` ile
çalıştırır). Ardından hazırlık adım 5'teki komutları `kabul-kaldirma.txt` dosyasına yazdırın.

**Beklenen:**

- `Get-Service Arci*`, `Get-ScheduledTask -TaskPath \ARCILOGGER\` ve
  `Get-NetFirewallRule -Group ARCILOGGER` boş;
- güç ayarları `kabul-once.txt` ile aynı (etkin plan, AC süreleri, hazırda bekletme,
  `HiberbootEnabled`, bağdaştırıcılar); `power-state.json` silinmiş;
- etkin gelen izin kurallarının sayısı kurulum öncesindeki gibi;
- `C:\ARCILOGGER` (ham loglar, kasa, yedekler), `C:\ProgramData\ARCILOGGER` (yapılandırma, gizli
  depo) ve veritabanı **yerinde**;
- geri alınmayanlar (bilinen sınır): saat dilimi, zaman eşitleme ayarları ve "Dosya Sistemi"
  denetim ilkesi.

## Sonuç tablosu

| # | Adım | Sonuç (geçti / kaldı / not) |
|---|---|---|
| 1 | Kurulum, ön denetim satırları, anahtar yalnızca ekranda, Enter beklemesi | |
| 2 | `NT SERVICE\<ad>` hesapları; `SeServiceLogonRight` satırı; `python.exe -I` komut satırları | |
| 3 | Core Networking, gelen kurallar, auditpol | |
| 4 | Görevler LOCAL SERVICE, sonuçlar | |
| 5 | Akıllı Uygulama Denetimi durumu; arayüz bu bilgisayarda | |
| 6 | Syslog alımı | |
| 6a | Uyum Durumu (on kart, PDF ve SHA-256'sı, yedek onayı ve değişen yapılandırma), denetçinin görünümü, Sistem ve `system-history.json`, Panel disk kartı, ilk kurulum sihirbazı | |
| 7 | Güç ayarları sonra / önce | |
| 8 | Yükseltme (hatırlanan adres), geri alma | |
| 9 | Güncelleme yeniden başlatması, boşluğun süresi | |
| 10 | Kaldırma, güç ayarlarının geri gelmesi | |

Açık sorular (sahada yanıtlanacak): Windows 11'de `NT SERVICE\ALL SERVICES` için "Hizmet olarak
oturum aç" hakkı varsayılan mı; `auditpol` "Dosya Sistemi" GUID'iyle Pro'da çalışıyor mu; Akıllı
Uygulama Denetimi kapatıldıktan sonra yeniden açılabiliyor mu; ağ bağdaştırıcısı güç yönetimi
komutu (`Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice`) bu Windows'ta var mı.

---

## English summary

A manual acceptance test of about an hour and a half on a **throw-away** Windows 11 Pro 25H2 PC or VM (TPM
2.0, Secure Boot, one disk, S mode off), because GitHub-hosted runners only run Windows Server.
Never on the real server or with real data. Record the power, audit and firewall settings first
(step 0), then: install with `ARCILOGGER-Setup-<version>.exe` from the GitHub release after
checking its SHA-256 (step 1: expected preflight lines — Windows 11 Pro ok, the licence note,
`C:\ARCILOGGER` chosen with the system-disk warning, the server address detected, the organisation
name, time source and admin address asked in the window (INSTALL.md section 7.1), a warning that
the new configuration has no log source; the setup token
shown on screen only after the log is closed and found in no file, the PSReadLine history
included, checked without typing the token on a command line; the Kamu SM question; the
window waits for Enter; the preflight's "no licence installed" information and, as step 6 of the
next steps, the request code of the 14-day trial, [LICENSING.md](LICENSING.md)); services under
`NT SERVICE\<name>` (`sc.exe qc`), the "Log on as a service" line and the Python services'
command lines `python.exe -I -X utf8 -u -m arcilogger_<package> …` (step 2); Core Networking, inbound rules, `auditpol` (step 3); scheduled tasks as
LOCAL SERVICE (step 4); Smart App Control unchanged and the interface on the PC itself with the
root certificate (step 5); a syslog message reaching the spool once the test PC is added as a
`[[sources]]` entry (on Ayarlar → Kaynaklar or by hand), with no service restart: the collector
and ingest load it live and ApplySystem opens 514 once the collector knows it (step 6; before, 514
is closed; with the browser administration off, run `Set-ArciFirewall.ps1` yourself); the
Uyum Durumu page, its PDF report, the configuration backup confirmation, the auditor's view, the
Sistem page and the first-run wizard (step 6a: the notice "designed to support 5651 obligations",
ten cards with their state as icon and text, cards 5 and 9 Problem and card 7 No data on a fresh
installation; the PDF's SHA-256 equal to the `sha256` of its `compliance.report_export` audit
entry; card 10 Attention before the confirmation, OK after it, Attention again once a backup
setting changes `arcilogger.toml`, with `config_sha256` equal to the file's SHA-256; the auditor
sees the cards and the PDF button but no confirmation button, no wizard link and no Sistem; one
disk meter with the 7-sample forecast note and today's sample in `state\system-history.json`;
the wizard's steps marked Tamam or Eksik, "Ayarlar'da aç" leading back with "Kurulum sihirbazına
dön", a skipped step staying Eksik); the power settings after the
installation and the recorded previous values (step 7); an upgrade that uses the remembered
address and asks no question, then a rollback (step 8); a forced update restart — services back,
messages sent during the restart missing and not reported (step 9); removal, with the power
settings restored and data, configuration and database kept (step 10). Fill in the result table
and answer the open questions (the default "Log on as a service" right, auditpol on Pro, turning
Smart App Control on again, the network adapter power command).
