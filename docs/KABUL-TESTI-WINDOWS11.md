# Kabul testi: Windows 11 Pro 25H2 üzerinde ARCILOGGER

> Kurulum belgesi: [KURULUM.md](KURULUM.md) (özellikle bölüm 17, "Masaüstü Windows 11 Pro").
> English summary at the end.

GitHub'ın derleme sunucuları yalnızca Windows Server çalıştırır; ARCILOGGER'ın masaüstü Windows'ta
kurulumu, yükseltmesi, geri alınması ve kaldırılması orada denenemez. Bu test, masaüstü desteğini
duyurmadan önce **atılabilir** bir Windows 11 Pro 25H2 bilgisayarda ya da sanal makinede yaklaşık
bir saatte elle yapılır. Gerçek sunucuda ya da gerçek verilerle **yapmayın**: test, Windows
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
   açılır; `https://localhost/setup` sayfasında anahtarla ilk yönetici oluşturulur.

## 6. Syslog alımı (yaklaşık 5 dakika)

Yeni kurulumda `C:\ProgramData\ARCILOGGER\arcilogger.toml` hiçbir `[[sources]]` içermez ve
güvenlik duvarı 514'ü hiçbir göndericiye açmaz. Mesajı göndereceğiniz test bilgisayarının adresini
bir kaynak olarak ekleyin (`name = "kabul"`, `type = "generic"`, `address = "<test bilgisayarının
adresi>"`; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4), güvenlik duvarını yeniden uygulayın ve
dört hizmeti yeniden başlatın (KURULUM bölüm 12). Hizmetler yapılandırmayı yalnızca başlarken
okur: yalnızca toplayıcı yeniden başlatılırsa alım (ArciIngest) yeni kaynağı tanımaz, sessiz kaynak
uyarısı (R15) onu izlemez.

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
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

A manual acceptance test of about one hour on a **throw-away** Windows 11 Pro 25H2 PC or VM (TPM
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
`[[sources]]` entry, the firewall applied again and all four services restarted, since they read
the configuration only at start (step 6; before, 514 is closed); the power settings after the
installation and the recorded previous values (step 7); an upgrade that uses the remembered
address and asks no question, then a rollback (step 8); a forced update restart — services back,
messages sent during the restart missing and not reported (step 9); removal, with the power
settings restored and data, configuration and database kept (step 10). Fill in the result table
and answer the open questions (the default "Log on as a service" right, auditpol on Pro, turning
Smart App Control on again, the network adapter power command).
