# ARCILOGGER Kurulumu (Windows Server ve Windows 11 Pro)

> English: [INSTALL.md](INSTALL.md). Tasarım kararları: [ADR-0007](adr/0007-windows-installer.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; kurulumun ve
> saklama düzeninin yasal yeterliliği için hukuki inceleme önerilir.

Bu belge, boş bir Windows Server'dan ya da ARCILOGGER'a ayrılmış bir Windows 11 Pro bilgisayardan
çalışan bir ARCILOGGER'a kadar her adımı anlatır. Masaüstü Windows'a özgü konular (donanım, güç,
güncellemeler, tek disk, lisans) bölüm 17'dedir; kabul testi:
[KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md). **Kurulum için** sunucunun internete çıkması
gerekmez: her şey yönetici bilgisayarında (yönetici PC'si) hazırlanan **çevrimdışı paketten**
kurulur. **Çalışırken** ise sunucunun genel internet erişimi olmaz, ama gece mühürlemesi için Kamu
SM zaman damgası sunucusuna (`zd.kamusm.gov.tr`, testte `tzd.kamusm.gov.tr`, TCP 80; yalnızca özet
gider) ve Kamu SM sertifika sunucusuna (`sertifikalar.kamusm.gov.tr`) **sürekli** erişim gerekir
([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 1, akış 6–7). Kullanıcı bilgisayarlarına hiçbir şey
kurulmaz; ARCILOGGER'a yalnızca tarayıcıyla bağlanılır.

Kurulum bittikten sonra yapılacaklar (ilk yönetici, Kamu SM test → üretim, ilk 5651 sorgusu,
günlük kontroller): [ILK-ADIMLAR.md](ILK-ADIMLAR.md). Pilot öncesinde hukuk müşavirine verilecek
taslaklar: [aydınlatma metni](legal/aydinlatma-metni-taslak.md),
[saklama ve imha politikası](legal/saklama-imha-politikasi-taslak.md).

**Lisans:** yeni bir kurulum 14 günlük denemeyle, bütün modüller açık başlar. Kurulum programı
lisanstan bağımsız olarak her şeyi kurar; hangi ücretli modülün çalışacağına ürün çalışırken karar
verir, 5651 işlevleri her zaman çalışır. İstek kodu, lisans dosyası, modüller ve bakım süresi:
[LISANS.md](LISANS.md).

Toplam süre: yaklaşık 1–1,5 saat (Windows kurulumu hariç).

| # | Adım | Nerede |
|---|---|---|
| 1 | Diskler, ağ, saat | Sunucu |
| 2 | PostgreSQL 16 (+ isteğe bağlı TimescaleDB) | Sunucu |
| 3 | Java 8 (Kamu SM Zamane istemcisi için) | Sunucu |
| 4 | Kurulum paketini indirme (GitHub sürümü) ya da oluşturma | Yönetici PC'si (internet gerekir) |
| 5 | Zamane istemcisini Kamu SM'den indirme | Yönetici PC'si |
| 6 | Paketi sunucuya kopyalama ve doğrulama | Sunucu |
| 7 | ARCILOGGER kurulumu | Sunucu |
| 8 | Kök sertifikayı yönetici PC'sine kurma | Yönetici PC'si |
| 9 | İlk giriş (ilk yönetici) | Sunucu + tarayıcı |
| 10 | Kamu SM bağlantısı (test → üretim) | Sunucu |
| 11 | Cihaz ayarları | FortiGate, MikroTik |
| 12 | Kurulum sonrası denetim; sonra [ILK-ADIMLAR.md](ILK-ADIMLAR.md) | Sunucu |

---

## 0. Gerekenler

- **Sunucu:** ayrılmış fiziksel sunucu, Windows Server 2022 veya 2025 Standard (önerilen) ya da
  ARCILOGGER'a ayrılmış bir bilgisayarda Windows 11 Pro, Enterprise veya Education (25H2, build
  26200 ya da sonrası; bölüm 17), 4 çekirdek, 16 GB RAM, `C:` ≥ 100 GB, veri diski `D:` ≥ 1 TB
  (iki disk RAID 1 önerilir), kesintisiz güç kaynağı (UPS). Windows 10 ve Windows 11 Home
  desteklenmez: ön denetim durur (`-AllowUnsupportedWindows` ile yalnızca uyarır).
  `D:` yoksa kurulum `C:\ARCILOGGER` kullanır ve kanıtın Windows ile aynı diskte durduğunu
  uyarır (bölüm 17). Boş alan en az 100 GB olmalıdır: ham loglar ve kasa en az iki yıl (730 gün)
  saklanır ve büyür.
- **Ağ:** sunucu için, cihazların syslog gönderebildiği ağda sabit bir IP adresi (ya da DHCP
  rezervasyonu); anahtar portu o ağın VLAN'ında etiketsiz (untagged). Gerekli akışlar:
  [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 1.
- **Kuruluşa özgü değerler** (ürünle gelmez, kurulumda girilir; bölüm 7.1): kuruluş adı
  (`-SiteName`), yerel saat kaynağı (`-NtpServer`, genellikle ana yönlendirici), yönetici PC'sinin
  adresi ya da ağı (`-AdminAddress`). Kurulumdan sonra: log kaynaklarının adresleri
  (`[[sources]]`), departmanlar ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5).
- **Yönetici PC'si** (web arayüzünü ve uzak masaüstünü kullanacak bilgisayar): kurulum dosyasını
  GitHub sürümünden indirmek ya da paketi oluşturmak için internet; yalnızca paketi kendiniz
  oluşturacaksanız Git, uv, Node.js 22 + npm 11.21, isteğe bağlı Inno Setup 6.3+ (bölüm 4).
- **Kamu SM:** test kullanıcısı talebi ([kamusm.md](kamusm.md), bölüm 4'teki e-posta taslağı).
- Windows güncellemeleri kurulmuş olmalı. Genel internet erişimi (Windows Update) yalnızca bakım
  penceresinde açılır; Kamu SM akışları (zaman damgası, sertifika sunucusu), NTP ve DNS ise
  **sürekli açık** kalır ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 1).

### 0.1 Desteklenen ağ yapıları

Kurmadan önce ağınızın bu sürümün desteklediği yapıya uyduğunu denetleyin. Ağ ne olursa olsun
`[[sources]]` içindeki her cihazın logları toplanır, **değiştirilmeden** saklanır ve her gece
mühürlenir; aşağıdakiler, bir 5651 sorgusunun kişiye ve cihaza kadar ilerleyip ilerlemediğini ve
kullanım analizinin neyi gösterdiğini belirler.

- **Tek yerleşke:** bir kurulum tek bir yerleşkeyi (site) kapsar: internete bir FortiGate üzerinden
  çıkan, adresleri bir MikroTik yönlendiricinin ya da FortiGate'in DHCP sunucusunun dağıttığı
  IPv4 ağı. Aynı özel adres aralığını kullanan birden çok şube tek kurulumda desteklenmez: aynı ağ
  VLAN listesine iki kez girilemez ve iki şubedeki aynı adres tek bir adres sayılır (IP çakışması
  gibi görünür).
  İnternete başka bir hattan ya da bir VPN tünelinden çıkan bir ağ kesiminin (ör. misafir Wi-Fi)
  FortiGate kaydı yoktur: o kesimden yalnızca kira kayıtları (DHCP, `ARP-LOG`) gelir; genel IP ve
  port ile 5651 sorgusu oradaki bir cihazı bulamaz, kullanım analizi de o trafiği görmez
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 5).
- **Kişi ve cihaz eşleştirmesi (kira kanıtı):** iç IP adresinden MAC adresine, cihaza ve
  kişiye giden adım DHCP sunucusunun kira kayıtlarına dayanır:
  - **DHCP sunucusu MikroTik:** ana MikroTik yönlendiricinin kira kayıtları (`assigned` /
    `deassigned`) kullanılır; ana yönlendiricideki saatlik `ARP-LOG` betiği (isteğe bağlı,
    sahada yazılır; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 3) statik IP'li cihazları ve kira
    kayıtları arasındaki boşlukları **orta güvenle** ekler.
  - **DHCP sunucusu FortiGate (yalnız-FortiGate ağlar):** FortiGate DHCP sunucusunun olay
    kayıtları (DHCP Ack / Release) kira zaman çizelgesine girer. Kayıt biçimi FortiOS
    belgelerinden ve tek bir topluluk örneğinden alınmıştır; ilk kurulumda sahada doğrulanır
    ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4) ve doğrulanana kadar raporlar bunu
    belirtir. Yalnızca yapılandırmada FortiGate olarak tanımlı kaynakların kayıtları
    kullanılır. FortiGate'in DHCP verdiği her ağ, VLAN numarası olmasa da, Cihazlar ve Kişiler
    sayfasında tanımlanır: tanımlı bir ağda olmayan adreslerin zincirleri düşük güvenle
    gösterilir. Sonradan eklenen ya da değiştirilen bir ağ geçmiş kiralara ancak kiralar
    yeniden oluşturulunca uygulanır ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5). Statik IP'li
    cihazların kira kaydı yoktur: zincir iç IP adresinde biter; ağ geçidi FortiGate olan
    arayüzlerde, iki istemciyle sahada denetlendikten sonra, isteğe bağlı olarak FortiGate
    oturum kaydının srcmac alanı MAC adresini **kısmi kanıt** olarak, düşük güvenle verir
    ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.5).
  - **İkisi birlikte:** iki DHCP sunucusu farklı ağlara hizmet ediyorsa ikisinin kayıtları da
    kullanılır.
  - **Başka bir DHCP sunucusu (ör. Windows):** bu sürüm onun kayıtlarını okumaz; kira kaydı
    yalnızca MikroTik `ARP-LOG` ile, orta güvenle oluşur. `ARP-LOG` yoksa 5651 sorgusu
    FortiGate oturumunda ve iç IP adresinde biter (MAC, cihaz ve kişi yok), kullanım analizi de
    IP adresine göre sayar.
- **İnternet çıkışları (NAT):** FortiGate'in kaynak NAT'ı desteklenir. Her internet hattı
  Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)
  bölümünde tanımlanır: genel IP (ve değişip değişemeyeceği), FortiGate'in o hattaki NAT
  adresi, arayüzleri, NAT türü ve port koruma. Genel adres FortiGate'in kendisindeyse
  (doğrudan) genel IP + port oturumu **kesin** belirler. Servis sağlayıcının yönlendiricisi
  ikinci bir NAT yapıyorsa o hattın sorguları **aday** oturumları sıralar
  ([CORRELATION.md](CORRELATION.md) bölüm 3); genel IP başka müşterilerle paylaşılıyorsa
  (CGNAT) aday listesi doğru oturumu içermeyebilir, kesin yanıt için servis sağlayıcının NAT
  kaydı gerekir. IPv6 adresleriyle 5651 sorgusu desteklenmez: kira kayıtları (DHCP, `ARP-LOG`)
  yalnızca IPv4 adreslerini kapsar.
- **Kullanım analizi** (ücretli modül, [LISANS.md](LISANS.md)) yalnızca FortiGate kayıtlarından
  hesaplanır: süreler ve baytlar trafik kayıtlarından (`logtraffic all`; uzun oturumlar için ara
  kayıtlarla), site adları web filtresi, DNS filtresi, uygulama kontrolü ve SSL denetimi
  kayıtlarından, engellenen denemeler bunların ilk üçünden ([DEVICE-SETUP.md](DEVICE-SETUP.md)
  bölüm 2, [KULLANIM.md](KULLANIM.md)). Yalnızca özel kaynak adreslerinden (RFC 1918 blokları ve
  IPv6 ULA) gelen trafik sayılır: iç ağda başka aralıklar (ör. 100.64.0.0/10 ya da içeride
  kullanılan genel adresler) kullanılıyorsa o trafik analiz edilmez. Başka üreticilerin güvenlik
  duvarlarının kayıtları analiz edilmez.
- **Başka üreticilerin cihazları** (`[[sources]]` içinde `type = "generic"`): logları toplanır,
  değiştirilmeden saklanır, mühürlenir ve tam gün kanıt paketlerine girer; veritabanına düz log
  satırı olarak (zaman, ana bilgisayar adı, program, ileti metni) yüklenir ama alanlarına
  ayrılmaz, 5651 sorgusunda ve kullanım analizinde kullanılmaz.

**Desteklenmeyenler:** IPv6 sorguları; aynı ağa iki DHCP sunucusu (ARCILOGGER fark ederse
uyarır ve o anlardaki zincirleri düşük güvenle verir); FortiGate'in DHCP aktarıcı (relay)
olarak çalıştığı ağlarda FortiGate kira kaydı (kira kanıtı asıl DHCP sunucusundan gelmelidir);
aynı özel adres aralığını kullanan birden çok şube; FortiGate'ten geçmeyen hatlarda sorgu (hat
yalnızca "FortiGate'ten geçmiyor" olarak işaretlenir, sorgu kimseyi listelemez); bir
yönlendiricinin ya da L3 anahtarın arkasındaki ağlarda srcmac (orada o cihazın MAC adresidir;
arada yalnızca katman 2 anahtarlar varsa arayüz ancak iki istemciyle sahada denetlenince
kullanılır).

## 1. Sunucuyu hazırlama

Tüm komutlar **yönetici olarak açılmış PowerShell** penceresinde çalıştırılır.

### 1.1 Veri diski (D:)

> **Dikkat:** aşağıdaki komutlar seçilen diskteki her şeyi siler. Disk numarasını iki kez
> kontrol edin.

```powershell
Get-Disk                                   # veri diskinin numarasını bulun (örnekte 1)
Initialize-Disk -Number 1 -PartitionStyle GPT
New-Partition -DiskNumber 1 -UseMaximumSize -DriveLetter D
Format-Volume -DriveLetter D -FileSystem NTFS -NewFileSystemLabel ARCIDATA -Confirm:$false
```

`D:` harfi DVD sürücüsündeyse önce Disk Yönetimi'nden (diskmgmt.msc) DVD sürücüsünün harfini
değiştirin. Veri diski **NTFS** biçimli, sabit (iç) bir disk olmalıdır: kurulum betiği
çıkarılabilir sürücüleri (USB bellek, hafıza kartı), CD/DVD sürücülerini, ağ sürücülerini ve RAM
disklerini reddeder, USB ile bağlı bir diski uyarır ve en az 100 GB boş alan ister (kanıt iki yıl
saklanır; boyutlandırmayı doğruladıysanız `-MinFreeGB` ile düşürülebilir).

`-DataRoot` vermezseniz kurulum, sabit ve NTFS bir `D:` varsa `D:\ARCILOGGER`, yoksa
`C:\ARCILOGGER` klasörünü seçer ve bunu yeni `arcilogger.toml` dosyasına yazar. Tek diskli bir
bilgisayarda bu adımı atlayın; o durumdaki riskler ve öneriler bölüm 17'dedir.

### 1.2 Ağ ve sunucu adı

```powershell
Get-NetAdapter                             # bağdaştırıcının adı (örnekte "Ethernet")
# <...> yerine kendi ağınızın değerlerini yazın (önek uzunluğu ör. 24)
New-NetIPAddress -InterfaceAlias "Ethernet" -IPAddress <sunucu adresi> -PrefixLength <önek> -DefaultGateway <ağ geçidi>
Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses <DNS sunucusu>
Rename-Computer -NewName ARCILOGGER -Restart
```

Cihazlar logları bu adrese gönderir: adres değişirse hiçbir log kaydedilmez. Sabit IP vermek yerine
DHCP sunucusunda (ör. ana yönlendiricide) bir rezervasyon da olur. Kurulum, `-ServerAddress`
verilmezse önceki çalıştırmada hatırladığı adresi, o da yoksa varsayılan ağ geçidi olan **tek** ağ
bağdaştırıcısının adresini kullanır (adres DHCP ile alınmışsa uyarır); birden fazla bağdaştırıcıda
ağ geçidi varsa tahmin etmez ve `-ServerAddress` ister. Yeni `arcilogger.toml` bu adresi
`[collector] bind` olarak alır.

### 1.3 Saat

Saat dilimi (Türkiye, UTC+03:00) ve zaman eşitleme kurulum betiği tarafından `Set-ArciTime.ps1`
ile yapılır: Windows Time, kurulumda `-NtpServer` ile verilen yerel saat kaynağını
(`[clock] ntp_server`; genellikle ana yönlendirici) kullanır, `time.ume.tubitak.gov.tr`
(`[clock] fallback_server`) yalnızca yedektir. Yerel saat kaynağı verilmezse saat yalnızca bu
genel sunucuyla eşitlenir ve ölçülür; ön denetim ve `config-check` bunu uyarı olarak yazar
(bölüm 7.1). Sunucunun o adrese UDP 123 ile ulaşabilmesi gerekir ([DEVICE-SETUP.md](DEVICE-SETUP.md)
bölüm 1, akış 5 ve 8).

Mühürleme sunucu saatine körü körüne güvenmez: zincirin başlangıcı mühür kayıtlarından gelir ve
geriye gitmez; eski bir güne, mühürlü bir güne ya da ileri bir tarihe ait spool dosyası
mühürlenmez, yerinde bırakılır ve bir kez kritik `R20-clock` uyarısı verir (saati ve dosyayı
inceleyin; dosyayı silmeyin). Bir gün ancak zaman damgası sağlayıcısının saati (genTime) günün
bittiğini gösterdiğinde mühürlenir; gece yarısından sonraki ilk `seal.quiet_seconds` saniyede
görev yalnızca bekler (başarısız deneme ya da uyarı sayılmaz). Hiç log olmayan art arda en fazla
`seal.max_consecutive_empty_days` gün (varsayılan 7) tek tek damgalanır; daha uzun bir dizi
damgalanmaz, bir kez kritik `R20-seal-skip` uyarısı verir ve sonraki mühürlü günün manifestinde
`unsealed_empty_days` olarak listelenir (doğrulayıcılar zinciri bu dizinin üzerinden izler).
Bu "boş geçme" kararı yalnızca sunucu saatine dayandığı için sonraki gün damgalanana kadar
geçicidir: boş geçilen güne sonradan veri gelirse, gün düzeltilmiş saate göre henüz bitmemişse
ya da boş günler dizisi artık sınırdan uzun değilse karar kendiliğinden geri alınır ve gün normal
biçimde mühürlenir (`R20-seal-skip` uyarısı, `unskip:` anahtarıyla). Sonraki gün damgalanmadan
hemen önce ve sonra boş günler yeniden denetlenir; manifest veri olan bir günü asla boş diye
listelemez. Sonraki gün damgalandıktan sonra karar kesinleşir: o güne yine de veri gelirse
dosya mühürlenmez, yerinde kalır ve kritik `R20-clock` uyarısı verir — dosyayı silmeyin, bir
kopyasını ve SHA-256 özetini alıp saat sorununu kayda geçirin; zincir o günün üzerinden geçmiş
olduğu için ARCILOGGER bu dosyayı sonradan zincire ekleyemez.
İlk kurulumda `seal.first_day` değerini girmeniz önerilir: ilk mühürden önce geriye tarihli bir
spool dosyası zincir başlangıcını hâlâ belirleyebilir. `seal.first_day` öncesine ait spool
dosyaları (örneğin canlıya geçişten önceki deneme mesajları) mühürlenmez, yerinde kalır ve
yalnızca bir kez `info` düzeyinde `R20-clock` uyarısı verir.

## 2. PostgreSQL 16

ARCILOGGER veritabanını kendisi kurmaz: PostgreSQL'i üreticinin (EDB) yükleyicisiyle siz
kurarsınız, ARCILOGGER yalnızca kendi rolünü ve veritabanını oluşturur
(neden: [ADR-0007](adr/0007-windows-installer.md) bölüm 3).

1. Yönetici PC'sinde PostgreSQL **16** Windows x64 yükleyicisini EDB sayfasından indirin:
   <https://www.enterprisedb.com/downloads/postgres-postgresql-downloads> (postgresql.org'un
   Windows sayfası da buraya yönlendirir). 16 serisinin en son sürümünü seçin.
2. Dosyanın imzasını denetleyin; `Status` değeri `Valid` olmalı ve imzalayan EnterpriseDB
   olmalıdır (**doğrulayın**: imzalayanın tam adı):

   ```powershell
   Get-AuthenticodeSignature .\postgresql-16.*-windows-x64.exe | Format-List Status, SignerCertificate
   ```

   ARCILOGGER bu yükleyicinin SHA-256 değerini kendisi doğrulayamadı (`components.json` içinde
   `null`). Paketin içine koymak isterseniz: `Build-ArciBundle.ps1 -ComponentUrl @{ postgresql =
   '<adres>' } -ComponentSha256 @{ postgresql = '<üretici sayfasında doğruladığınız SHA-256>' }`.
3. Yükleyiciyi sunucuya kopyalayıp **yönetici olarak** çalıştırın:
   - Bileşenler: *PostgreSQL Server* ve *Command Line Tools* (psql, pg_dump gerekli). *pgAdmin*
     isteğe bağlı, *Stack Builder* gerekmez.
   - Veri klasörü: `D:\ARCILOGGER\db` (`D:` olmayan bir bilgisayarda `C:\ARCILOGGER\db`)
   - Parola: postgres süper kullanıcısı için güçlü bir parola. **Parola yöneticisinde saklayın,
     sunucuya yazmayın.** ARCILOGGER bu parolayı kurulumda bir kez sorar ve hiçbir yere kaydetmez.
   - Port: `5432`.
   - Yerel ayar (Locale): listeden **`C`** seçin, varsayılanı (*[Default locale]*) değil.
     Varsayılan, Windows'un yerel ayarıdır; Türkçe Windows'ta bu adı bir Windows güncellemesi daha
     önce değiştirdi (`Turkish_Turkey` → `Turkish_Türkiye`) ve o adla oluşturulmuş PostgreSQL
     kümeleri ardından başlamadı (PostgreSQL BUG #18196). `C` yerel ayarının adı değişmez.
     ARCILOGGER kendi veritabanını, kümenin ayarı ne olursa olsun, `C` yerel ayarıyla oluşturur;
     var olan bir veritabanını değiştirmez.
4. Yalnızca yerelden bağlantı kabul etsin: `D:\ARCILOGGER\db\postgresql.conf` dosyasında
   `listen_addresses = 'localhost'` yapın ve hizmeti yeniden başlatın. Güvenlik duvarı betiği
   ayrıca ARCILOGGER kuralları dışındaki tüm gelen izin kurallarını (EDB'nin 5432 kuralı dahil)
   kapatır.

   ```powershell
   Restart-Service postgresql-x64-16
   Get-Service postgresql-x64-16              # Status: Running
   ```
5. **Virüs koruması (Microsoft Defender).** Windows Server'da Defender'ın gerçek zamanlı taraması
   varsayılan olarak açıktır. PostgreSQL topluluk raporları (posta listeleri, PostgreSQL wiki'nin
   Windows sayfası) bir tarayıcının dosyayı tuttuğu anda WAL ve veri dosyalarında
   "could not rename/open file … Permission denied" hataları anlatır; öneri veri klasörünün ve
   sunucu sürecinin taramadan çıkarılmasıdır. ARCILOGGER kurulumu bu ayarı **yapmaz**; siz
   yaparsınız (**doğrulayın**: yol ve süreç adı, kurulu PostgreSQL 16 ile):

   ```powershell
   Get-MpComputerStatus | Format-List AMServiceEnabled, RealTimeProtectionEnabled
   Add-MpPreference -ExclusionPath 'D:\ARCILOGGER\db'
   Add-MpPreference -ExclusionProcess 'C:\Program Files\PostgreSQL\16\bin\postgres.exe'
   (Get-MpPreference).ExclusionPath; (Get-MpPreference).ExclusionProcess
   ```

   - Başka bir virüs koruma ya da EDR ürünü kullanılıyorsa aynı iki istisnayı onun konsolunda
     tanımlayın.
   - İstisnaları dar tutun: yalnızca veritabanı klasörü ve PostgreSQL süreci (klasörün NTFS
     hakları bölüm 16'da sahada doğrulanır).
   - `spool` ve `vault` varsayılan olarak taranmaya devam eder: toplayıcı günün dosyasını açık
     tutar; mühürleme ve ingest bir tarama yüzünden başarısız olursa kendiliğinden yeniden dener.
     Bunların da istisnaya alınması ya da yalnızca zamanlanmış taramayla taranması **sahibin
     kararıdır**.
   - Defender istisnaları kasanın denetim (SACL) kaydını değiştirmez; değişikliklerin kanıtı yine
     özetler, zaman damgaları ve Windows Güvenlik günlüğüdür.

### 2.1 TimescaleDB (isteğe bağlı)

ARCILOGGER düz PostgreSQL ile çalışır. TimescaleDB büyük log tablolarını sıkıştırır; ancak
sıkıştırma ve saklama ilkeleri **Timescale License (TSL)** altındadır (OSI onaylı açık kaynak
değil). Kullanmadan önce sahibin onayı gerekir; onay yoksa yalnızca Apache-2.0 kısmı
kullanılabilir.

1. Lisans kararı verildiyse paketi `Build-ArciBundle.ps1 -IncludeTimescaleDB` ile oluşturun:
   `operator-installers\timescaledb\timescaledb-postgresql-16-windows-amd64.zip` pakete eklenir
   (sürüm 2.30.2, SHA-256 `components.json` içinde). Bu seçenek olmadan TimescaleDB pakete
   konmaz.
2. Zip'i sunucuda açın, `timescaledb\setup.exe` dosyasını yönetici olarak çalıştırın ve
   sorulan PostgreSQL yollarını gösterin (**doğrulayın**: kurulu PostgreSQL 16 alt sürümüyle).
3. TSL onaylanmadıysa `postgresql.conf` içine `timescaledb.license = 'apache'` ekleyin. Her
   durumda telemetriyi kapatan satırı da ekleyin: `timescaledb.telemetry_level = off`
   (ARCILOGGER hiçbir yere veri göndermez; kurulum betiği bunu ayrıca veritabanı düzeyinde,
   eklentiden önce, ve eklentiyi açtığında `ALTER SYSTEM` ile sunucu genelinde kapatır). Gece
   yedeği (`pg_dump`) her chunk'ı tek işlemde kilitler ve chunk sayısı her gün artar:
   `max_locks_per_transaction = 256` satırını da ekleyin (varsayılan 64; **doğrulayın**,
   [YEDEKLEME.md](YEDEKLEME.md) bölüm 7).
4. `Restart-Service postgresql-x64-16`. ARCILOGGER kurulumu eklentiyi kendi veritabanında açar.
   TimescaleDB'yi ARCILOGGER'dan **sonra** kurarsanız:
   `& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -d arcilogger -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"`

## 3. Java 8 (Kamu SM Zamane istemcisi)

Zamane konsol istemcisi Java 1.8 ister. Paket, Eclipse Temurin 8 JRE yükleyicisini doğrulanmış
olarak içerir (`operator-installers\temurin8-jre\`). Sabit bir klasöre kurun ki
`arcilogger.toml` içindeki yol Java güncellemelerinden sonra da geçerli kalsın:

```powershell
msiexec /i C:\Kurulum\ARCILOGGER-0.1.0\operator-installers\temurin8-jre\OpenJDK8U-jre_x64_windows_hotspot_8u502b07.msi INSTALLDIR="C:\Program Files\Eclipse Adoptium\jre-8\" /qn /norestart /l*v C:\Windows\Temp\temurin8.log
& "C:\Program Files\Eclipse Adoptium\jre-8\bin\java.exe" -version
```

(**Doğrulayın:** MSI'ın `INSTALLDIR` özelliği. Yükleyici varsayılan olarak
`C:\Program Files\Eclipse Adoptium\jre-8.0.502.7-hotspot\` klasörünü kullanır; o klasöre
kurduysanız `arcilogger.toml` içinde `[tsa] java` yolunu ona göre değiştirin.)

## 4. Kurulum paketini indirme ya da oluşturma (yönetici PC'si)

### 4.1 GitHub sürümünden indirme (önerilen)

Depo bir `v<sürüm>` etiketi aldığında GitHub Actions (`.github/workflows/installer.yml`, "Release
build" işi, kurulum testleri ve uçtan uca kurulum başarılı olduktan sonra) paketi oluşturur ve bir
**taslak** GitHub sürümü (release) hazırlar; yayımlamadan önce
iş günlüğünü ve değerleri kontrol eden kişi yayımlar. Sürümü çıkaran kişi etiketlenen
commit'te `core/src/arcilogger_core/__init__.py` içindeki `__version__` ile `RELEASE_DATE`
değerlerini birlikte günceller: `RELEASE_DATE` commit gününden 14 günden fazla önceyse ya da
ondan sonraysa iş sürümü durdurur (lisansın bakım tarihi bu tarihle karşılaştırılır; kontrol
listesi [ADR-0007](adr/0007-windows-installer.md), A1). Sürümde şu dosyalar vardır:

| Dosya | İçerik |
|---|---|
| `ARCILOGGER-Setup-<sürüm>.exe` | Tek dosyalık kurulum (çift tıklanır; bölüm 7) |
| `ARCILOGGER-<sürüm>.zip` | Aynı paket klasör olarak (`ARCILOGGER-<sürüm>\`, betikle kurulum için) |
| `ARCILOGGER-<sürüm>.fingerprint.txt` | Paket parmak izi (paketin `SHA256SUMS` dosyasının SHA-256 değeri) |
| `ARCILOGGER-<sürüm>.spdx.json` | Yazılım malzeme listesi (SBOM, SPDX) |
| `SHA256SUMS.txt` | Yukarıdaki dosyaların SHA-256 değerleri |

Bu yolda yönetici PC'sinde Git, uv, Node.js ya da Inno Setup gerekmez. İndirdikten sonra
özetleri denetleyin; değerler hem `SHA256SUMS.txt` dosyasında hem sürüm notunda hem de işin
özetinde (Actions → çalıştırma → *Summary*) yazar:

```powershell
Get-FileHash .\ARCILOGGER-Setup-0.1.0.exe, .\ARCILOGGER-0.1.0.zip -Algorithm SHA256 | Format-List Hash, Path
Get-Content .\SHA256SUMS.txt
```

(Linux ya da Git Bash'te: `sha256sum -c SHA256SUMS.txt`.) Değerler aynı değilse dosyayı
**kullanmayın**. Paket parmak izini (`.fingerprint.txt`) not edin: kurulum betiği sunucuda aynı
değeri yazar (bölüm 6). Bu yolda güven, paketi GitHub'ın sunucusunda etiketli koddan oluşturan
derleme işine kayar ([ADR-0007](adr/0007-windows-installer.md), "Amendment 2026-10-19").

### 4.2 Paketi kendiniz oluşturma

Bir kez gerekenler: Git for Windows, uv (<https://docs.astral.sh/uv/> → Windows kurulumu),
Node.js 22 LTS ve ardından `npm install --global npm@11.21.0`, isteğe bağlı Inno Setup 6.3 veya
üstü (tek dosyalık `ARCILOGGER-Setup.exe` için).

```powershell
git clone <ARCILOGGER deposunun adresi> arcilogger
cd arcilogger
powershell -ExecutionPolicy Bypass -File deploy\windows\installer\Build-ArciBundle.ps1
```

Betik şunları yapar ve herhangi bir özet (SHA-256) tutmazsa **durur**:

- `components.json` içindeki bileşenleri indirir ve doğrular: Python 3.12, WinSW 2.12.0, Caddy;
  çevrimdışı kurulum için ayrıca Temurin 8 JRE (`-SkipOperatorInstallers` ile atlanır) ve yalnızca
  `-IncludeTimescaleDB` verilirse TimescaleDB. Lisans metinlerini de (sürüme sabitlenmiş adresten,
  SHA-256 denetimli) `licenses\` klasörüne koyar; kurulum bunları `THIRD-PARTY-NOTICES.txt` ile
  birlikte program klasörüne kopyalar.
- ARCILOGGER paketlerini derler, `uv.lock` içindeki tüm bağımlılıkları Windows / Python 3.12 için
  özetleriyle (hash) indirir, web arayüzünü derler.
- `dist\ARCILOGGER-<sürüm>\` klasörünü, `bundle-manifest.json` ve `SHA256SUMS` dosyalarını
  oluşturur ve **paket parmak izini** (SHA256SUMS dosyasının SHA-256 değeri) yazdırır; aynı değer
  `dist\ARCILOGGER-<sürüm>.fingerprint.txt` içindedir. Bu değeri not edin.
- Inno Setup kuruluysa `dist\ARCILOGGER-Setup-<sürüm>.exe` dosyasını da oluşturur (`-RequireInno`
  ile Inno Setup yoksa durur).

## 5. Zamane istemcisi (Kamu SM)

Kamu SM'nin "Ücretsiz Zaman Damgası İstemci Yazılımı" sayfasından ZIP'i indirin, sayfada
yayımlanan **SHA-256** değerini not edin ve `tss-client-console-3.1.33.jar` dosyasını çıkarın
([kamusm.md](kamusm.md) bölüm 4). Jar dosyası ARCILOGGER deposuna ve paketine **konmaz**.

## 6. Paketi sunucuya kopyalama ve doğrulama

Klasörü USB bellekle veya paylaşımla sunucuya kopyalayın, örneğin `C:\Kurulum\ARCILOGGER-0.1.0`
(**`D:\ARCILOGGER` içine değil**). Jar dosyasını da `C:\Kurulum\` içine koyun.

```powershell
Get-ChildItem -Recurse C:\Kurulum | Unblock-File       # "internetten indirildi" işaretini kaldırır
Get-FileHash C:\Kurulum\ARCILOGGER-0.1.0\SHA256SUMS -Algorithm SHA256
```

Çıkan değer, bölüm 4'te not ettiğiniz **paket parmak izi** ile aynı olmalıdır. Kurulum betiği
ayrıca paketteki her dosyanın özetini denetler; tek bir dosya bile değişmişse hiçbir şey kurmaz.
Yalnızca `ARCILOGGER-Setup-<sürüm>.exe` kopyalıyorsanız sunucuda da
`Get-FileHash .\ARCILOGGER-Setup-<sürüm>.exe -Algorithm SHA256` değerini `SHA256SUMS.txt` ile
karşılaştırın; kurulum penceresi paket parmak izini yeniden yazar.

## 7. ARCILOGGER kurulumu

Önce **deneme** (hiçbir şey değişmez; ön denetimleri ve planı gösterir). Kuruluşa özgü
değerleri (bölüm 7.1) burada da verin; verilmezse deneme onları sorar:

```powershell
cd C:\Kurulum\ARCILOGGER-0.1.0
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -DryRun `
    -SiteName "<kuruluş adı>" -NtpServer <yerel saat kaynağı> -AdminAddress <yönetici PC'si>
```

`[HATA]` satırı varsa giderin (ör. PostgreSQL çalışmıyor, 514/443 portu başka bir programda,
veri sürücüsü NTFS değil, sunucu adresi bu bilgisayarın değil). Ardından gerçek kurulum:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 `
    -SiteName "<kuruluş adı>" -NtpServer <yerel saat kaynağı> -AdminAddress <yönetici PC'si> `
    -ZamaneJar C:\Kurulum\tss-client-console-3.1.33.jar `
    -ZamaneJarSha256 <Kamu SM sayfasındaki SHA-256>
```

`ARCILOGGER-Setup-<sürüm>.exe` kullanıyorsanız çift tıklayın: aynı betik `-Auto` ile bir PowerShell
penceresinde çalışır (kurulu değilse kurar, başka bir sürüm kuruluysa yükseltir) ve pencere siz
**Enter**'a basana kadar açık kalır. Kurulum dosyası Zamane istemcisini kurmaz; sonradan
`C:\ProgramData\ARCILOGGER\zamane\` içine elle kopyalanan bir jar da çalışmaz: görevler yalnızca
kurulumun kaydettiği, bilinen bir sürüm olan ya da konsolda onaylanan istemciyi çalıştırır, bu
yüzden mühürleme `executable` nedeniyle durur. İstemciyi paketteki betikle ekleyin:
`powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -ZamaneJar
C:\Kurulum\tss-client-console-3.1.33.jar -ZamaneJarSha256 <Kamu SM sayfasındaki SHA-256>` (SHA-256
değerini karşılaştırır, dosyayı kopyalar ve kaydeder; `[tsa] zamane_jar` bu dosyayı göstermeli)
ya da **Ayarlar → Kamu SM** sayfasından yükleyin: bu sürüm hiçbir Zamane sürümünü tanımadığı için
yüklenen istemci konsolda `approve-jar` bekler, en erken `jar_hold_hours` sonra (varsayılan 24,
bölüm 7.2).

Kurulum `.exe` dosyası iki lisans seçeneğini de betiğe iletir; yönetici komut isteminden ya da
PowerShell'den verin (büyük/küçük harf fark etmez, `-` ile de yazılabilir):

```powershell
.\ARCILOGGER-Setup-<sürüm>.exe /LICENSEFILE="C:\Kurulum\ARCI-L-<numara>.lic"
.\ARCILOGGER-Setup-<sürüm>.exe /ALLOWUNCOVEREDUPGRADE
```

`/LICENSEFILE="<dosya>"` betiğe `-LicenseFile "<tam yol>"` olarak gider (göreli bir yol önce
geçerli klasörde, sonra kurulum `.exe` dosyasının klasöründe aranır; en güvenlisi tam yoldur).
Dosya yoksa kurulum hiçbir şey kopyalamadan bunu söyleyip kapanır. `/ALLOWUNCOVEREDUPGRADE`
betiğe `-AllowUncoveredUpgrade` olarak gider (bölüm 13). İkisi birlikte de verilebilir.
Seçenek verilmezse kurulum `.exe` dosyası betiği eskisi gibi yalnızca `-Auto` ile çalıştırır.

Kurulum sırasında **PostgreSQL süper kullanıcı (postgres) parolası** bir kez ve verilmemiş kuruluş
değerleri (bölüm 7.1) sorulur. İlk kurulumun sonunda, kurulum günlüğü kapandıktan sonra ve yalnızca
pencerenin başında biri varsa:

- İlk yöneticinin **kurulum anahtarı** pencerede gösterilir (bölüm 9). Kurulum programı anahtar
  satırını PowerShell'in çıktı akışlarını atlayarak doğrudan konsol penceresine yazar: kurulum
  günlüğünde ve planda yoktur; sizin ya da bir Grup İlkesinin başlattığı PowerShell kaydı veya
  yönlendirmesi (`Start-Transcript`, PowerShell transcription, `*>`, `Tee-Object`) da onu kaydetmez.
  Konsol penceresinin kendisini kaydeden bir araç onu yine görür: o zaman bölüm 9'daki komutla yeni
  anahtar alın, önceki geçersiz olur. Yöneticisi olan, korunmuş bir veritabanı üzerine yeniden
  kurulumda (bölüm 14'ten sonra) anahtar üretilmez ve kurulum programı bunu yazar.
- "Kamu SM müşteri numarası ve parolasını şimdi girmek ister misiniz? [E/h]" sorulur. `E` (ya da
  Enter) ile müşteri numarası (görünür) ve parola (görünmez) doğrudan şifreli gizli depoya gider
  (`secret set`, standart girdi üzerinden; komut satırına ve günlüğe yazılmaz); `h` ile atlanır,
  sonra bölüm 10'daki komutlarla girilir. Kimlik bilgilerinden biri zaten kayıtlıysa sorulmaz
  (eksik olanı bölüm 10'daki komutla girin).

`powershell -NonInteractive`, CI ya da çıktısı bir dosyaya yönlendirilmiş bir çalıştırmada ikisi de
olmaz; anahtar için bölüm 9'daki komut kullanılır.

| Parametre | Varsayılan | Açıklama |
|---|---|---|
| `-ServerAddress` | hatırlanan değer; yoksa varsayılan ağ geçidi olan tek bağdaştırıcının adresi | Sunucu adresi (cihazların log gönderdiği adres, sertifika ve web adresi; yeni `arcilogger.toml` içinde `[collector] bind`). Verilen değer `C:\ProgramData\ARCILOGGER\install\state.json` içinde hatırlanır |
| `-ServerName` | hatırlanan değer; yoksa `arcilogger.internal` | Ek web adı (hatırlanır) |
| `-SiteName` | — (yok; ilk kurulumda gerekli) | Kuruluş adı (`site_name`); bölüm 7.1 |
| `-NtpServer` | — (yok; boş: yalnızca yedek saat sunucusu) | Yerel saat kaynağı (`[clock] ntp_server`); bölüm 7.1 |
| `-AdminAddress` | — (yok; hatırlanan değer) | 443 ve 3389'a yalnızca bu adres(ler) ya da ağ(lar) bağlanabilir (ör. `10.0.0.10`, `10.0.0.0/24`); bir kez verilen değer hatırlanır; bölüm 7.1 |
| `-DataRoot` | sabit NTFS `D:` varsa `D:\ARCILOGGER`, yoksa `C:\ARCILOGGER` | Veri klasörü (yalnızca ilk kurulumda; sonra `arcilogger.toml` belirler) |
| `-ZamaneJar`, `-ZamaneJarSha256` | — | Zamane istemcisi ve Kamu SM'nin yayımladığı özet |
| `-LicenseFile` | — (yok) | Kurulacak lisans dosyası (`.lic`, [LISANS.md](LISANS.md) bölüm 4). Ön denetimde Python olmadan okunur (dosya var, en çok 65.536 bayt, JSON; lisans no, bakım ve geçerlilik tarihi); okunamazsa kurulum hiçbir şey değiştirmeden durur (`2`). Şema kurulduktan hemen sonra `arcilogger-db license install` ile yüklenir; ürün dosyayı reddederse ya da veritabanına ulaşılamazsa uyarı verilir, kurulum sürer ve `1` ile biter. `-SkipDatabase` ile yüklenmez, sonra çalıştırılacak komut yazılır |
| `-AllowUncoveredUpgrade` | kapalı | Lisansın bakım süresinin kapsamadığı bir sürümü yine de kurar: sürüm yalnızca 5651 Temel ile çalışır, ücretli modüller sürümü kapsayan bir lisans yüklenene kadar kapalıdır (bölüm 13). `-LicenseFile` ve `-AllowUncoveredUpgrade` yalnızca kurulumda ve yükseltmede kullanılır (`-Uninstall` ve `-Rollback` ile `2`) |
| `-StrictOutbound` | kapalı | Giden trafiği de kısıtlar (bölüm 12); seçim hatırlanır, kapatmak için `-StrictOutbound:$false` |
| `-SourceNetwork` | RFC 1918 özel ağları | Tarayıcıdan eklenen bir log kaynağının adresinin bulunması gereken ağlar (bölüm 7.2); virgülle birden çok; hatırlanır |
| `-AdminChangeHoldHours` | `0` | Saklama ve replika değişikliklerinin tarayıcıda bekleme süresi, 0–168 saat (bölüm 7.2); hatırlanır |
| `-ZamaneHoldHours` | `24` | Bilinmeyen bir Zamane istemcisinin en erken onaylanabileceği süre, 1–168 saat (bölüm 7.2); hatırlanır |
| `-DisableBrowserAdministration` | kapalı | Sunucu ayarlarının tarayıcıdan değiştirilmesini kapatır (bölüm 7.2); 5651 işlevleri etkilenmez; hatırlanır, açmak için `-DisableBrowserAdministration:$false` |
| `-SkipFirewall`, `-SkipTimeSetup`, `-SkipDatabase` | — | İlgili adımı atlar (`-SkipTimeSetup` yalnızca kurulumun saat adımını atlar: tarayıcı yönetimi açıkken `ApplySystem` Windows Saati'ni `[clock]` değerlerinden kurar; [AYARLAR.md](AYARLAR.md) bölüm 2) |
| `-SkipPower` | — | Masaüstü Windows'ta güç ayarlarına dokunmaz (bölüm 17) |
| `-AllowUnsupportedWindows` | — | Windows 10 ve Windows 11 Home'da durmak yerine uyarır (desteklenmez) |
| `-DryRun` / `-WhatIf` | — | Yalnızca plan; `-PlanPath plan.json` ile JSON olarak yazar |
| `-HealthWaitSeconds` | `90` | Sağlık denetiminin API ve Caddy'yi bekleme süresi |

Betiğin yaptıkları (sırasıyla):

1. Paket özetleri, ön denetim: Windows sürümü ve baskısı (Windows Server 2022/2025 ve Windows 11
   Pro/Enterprise/Education desteklenir; Windows 10 ve Home'da durur; ARM64'te uyarır; masaüstü
   Windows'ta lisans koşullarını doğrulama notu), yönetici hakkı, .NET 4.6.1+, veri sürücüsü
   (sabit disk, NTFS, boş alan; USB diskte ve Windows'un diskinde uyarı), PostgreSQL hizmeti,
   514/443 portları, sunucu adresi ve toplayıcının dinleyeceği adres (`[collector] bind` bu
   bilgisayarın bir adresi ya da `0.0.0.0` değilse **durur** ve nasıl düzeltileceğini yazar),
   kuruluş adı ve saat kaynağı (bölüm 7.1).
   Güvenlik duvarı adımının reddedebileceği her şey de burada, hiçbir şey değişmeden denetlenir:
   `-AdminAddress` (verilmiş ya da hatırlanan bir değer olmalı) ve biçimi, mevcut `arcilogger.toml`
   içindeki `[[sources]]` adresleri ve güvenlik duvarının keseceği açık bir uzak masaüstü oturumu.
   Hiç log kaynağı (`[[sources]]`) yoksa (yeni bir kurulumda hiç yoktur) ön denetim durmaz, uyarır
   (bölüm 7.1). Lisansın bakım kuralı (`preflight.license`): paketin yayın tarihi
   (`bundle-manifest.json`, `release_date`) `-LicenseFile` dosyasının ya da kurulu lisansın bakım
   tarihinden sonraysa kurulum **durur** (`2`; `-AllowUncoveredUpgrade` ile uyarır ve sürer;
   bölüm 13, [LISANS.md](LISANS.md) bölüm 7). Lisans yoksa bu yalnızca bilgidir.
2. Klasörler: `C:\Program Files\ARCILOGGER` (program), `C:\ProgramData\ARCILOGGER`
   (yapılandırma, gizli depo, Kamu SM kökleri, SMTP CA'ları, Zamane, Caddy CA, tarayıcı yönetiminin
   `admin\` klasörü), veri klasörü (`D:\ARCILOGGER` ya da `C:\ARCILOGGER`: spool, vault, exports,
   state, logs, backups).
3. Python 3.12 → sanal ortam → bağımlılıklar **çevrimdışı ve özet denetimli** (`pip --no-index
   --require-hashes`) → ARCILOGGER, web arayüzü, Caddy, WinSW.
4. `arcilogger.toml` yoksa örnekten oluşturulur: `data_dir` veri klasörü, `[collector] bind`
   sunucu adresi, `site_name` kuruluş adı, `[clock] ntp_server` yerel saat kaynağı olur; örnek
   **hiçbir log kaynağı** tanımlamaz, kurgusal örnek ağın `[[sources]]` satırları yorum satırıdır
   (bölüm 7.1). Varsa **dokunulmaz**; yalnızca verilen `-SiteName` / `-NtpServer` satırı değişir.
   Veri klasörü `C:\ProgramData\ARCILOGGER\data-root.txt` dosyasına yazılır: hizmetler ve görevler
   yapılandırmanın `data_dir` değerini bununla karşılaştırır, tarayıcıdan yapılan hiçbir değişiklik
   veri klasörünü taşıyamaz. Görevlerin çalıştırdığı programlar kaydedilir (bölüm 7.2). Gizli depo
   anahtarı (`secret init`) ve boş `secrets.json`; tarayıcı yönetiminin anahtarları (`keys init`,
   var olan bir anahtar değişmez), `admin\policy.json` ve Windows olay günlüğü kaynağı
   `ARCILOGGER-Admin`.
5. NTFS izinleri: her ARCILOGGER hizmeti yalnızca ihtiyacı olan klasöre yazabilir (tablo:
   [ADR-0007](adr/0007-windows-installer.md) bölüm 4). Toplayıcı spool'a yazabilir ama ham log
   dosyalarını **silemez ve yeniden adlandıramaz** (açtığı dosyaya yazabilir; içeriği gece mührü
   kanıtlar). Denetim kaydı spool'u (`spool\_audit`) ve mühürleme durumu (`state\seal`,
   `state\replica`, `state\verify`, `state\clock`) ayrı korunur: toplayıcı onlara hiç erişemez,
   yalnızca zamanlanmış görevler yazar. Kasaya (vault) ARCILOGGER'dan yalnızca zamanlanmış görevler
   (mühürleme, saklama) yazar ve kasadaki her yazma/silme girişimi Windows Güvenlik günlüğüne
   düşer. Gece veritabanı yedeklerinin klasörüne (`backups\scheduled`) yalnızca zamanlanmış
   görevler yazar; hizmetler onu okuyamaz. Yönetim hizmeti ArciAdmin yalnızca
   `arcilogger.toml` ve `secrets.json` dosyalarına (dosyaya özel izinle) ve kendi klasörlerine
   yazar, `kamusm-roots` ve `smtp-ca` klasörlerine yalnızca dosya ekleyebilir; API yalnızca
   `admin\inbox` klasörüne istek bırakır ([SECURITY.md](SECURITY.md) bölüm 14). **Sınır:**
   zamanlanmış görevler Windows'un paylaşılan
   LOCAL SERVICE hesabıyla çalışır; aynı hesapla çalışan Windows hizmetleri de bu hakları (spool,
   kasa ve `state` yazma,
   `secret.key` okuma) taşır. Görevlere ayrı bir kimlik verilmesi sahip kararı ve sunucuda
   doğrulama bekleyen açık konudur ([ADR-0007](adr/0007-windows-installer.md) bölüm 8).
6. Veritabanı: rastgele bir parola üretilir, şifreli gizli depoya yazılır
   (`database.password`), PostgreSQL'e yalnızca parolanın SCRAM özeti gider; `arcilogger` rolü ve
   veritabanı (UTF8, yerel ayar `C`; yalnızca yoksa) oluşturulur.
7. Windows hizmetleri (otomatik başlar, hata olursa yeniden başlar, günlükler veri klasörünün
   `logs` alt klasöründe döner): `ArciCollector`, `ArciIngest`, `ArciApi`, `ArciWorker`,
   `ArciWeb` ve yönetim hizmeti `ArciAdmin` ("ARCILOGGER Yönetim (Admin)"). Her biri kendi sanal
   hesabıyla (`NT SERVICE\<ad>`) çalışır. Veritabanı kullanmayan `ArciCollector` ve `ArciWeb` hemen
   başlatılır; diğerleri şema kurulduktan sonra, `ArciAdmin` en son (durdurulurken ilk o durur). Python
   hizmetleri sanal ortamın Python'unu **yalıtılmış** çalıştırır
   (`venv\Scripts\python.exe -I -X utf8 -u -m arcilogger_<paket> …`): hiçbir ortam değişkeni
   (`PYTHONPATH`, `PYTHONSTARTUP` gibi; makinenin ya da hizmetin kendi değişkenleri) onlara başka
   bir modül yükletemez; `ARCILOGGER_CONFIG` geçerli kalır.
8. Saat (`Set-ArciTime.ps1`), masaüstü Windows'ta güç ayarları (`Set-ArciPower.ps1`, bölüm 17),
   güvenlik duvarı (`Set-ArciFirewall.ps1`, bölüm 12), zamanlanmış görevler
   (`Register-ArciTasks.ps1`: mühürleme, doğrulama, replika, saklama, kontör, saat denetimi ve gece
   01:40 veritabanı yedeği, [YEDEKLEME.md](YEDEKLEME.md); tarayıcıdan gelen görev istekleri için
   `Requests` (LOCAL SERVICE) ve güvenlik duvarı ile saati yapılandırmaya uyduran `ApplySystem`
   (SYSTEM); sizin kapattığınız bir görev kapalı kalır).
   Görevler de Python'u yalıtılmış çalıştırır (`python.exe -I -m arcilogger_core …`,
   `-I -m arcilogger_db backup run`).
9. Veritabanı şeması `arcilogger-db upgrade` ile kurulur — **son değişiklik** budur: kendi
   nedeniyle başarısız olabilecek her adım ondan önce çalışır. İlk kurulumda deneme süresi burada
   başlar. `-LicenseFile` verildiyse lisans dosyası hemen ardından yüklenir (adım
   `license.install`; reddedilirse yalnızca uyarı).
10. Sağlık denetimi: hizmetler, `http://<[api] host:port>/api/v1/health` (varsayılan
    `127.0.0.1:8000`; Caddy'nin `/api` yönlendirmesi de bu değeri izler) ve Caddy üzerinden
    `https://localhost/api/v1/health` (90 saniyeye kadar beklenir). Sonunda web adresi, kök
    sertifikanın yolu ve parmak izleri ile sonraki adımlar yazılır. Sonraki adımların 6.'sı
    lisanstır: lisans kuruluysa lisans sahibi, yoksa lisans verene e-postayla gönderilecek
    **istek kodu** (alınamazsa sonra çalıştırılacak komut; [LISANS.md](LISANS.md) bölüm 4).

Çıkış kodları: `0` tamam, `1` uyarılarla tamam (sağlık denetimini okuyun; ürünün reddettiği ya da
yüklenemeyen bir `-LicenseFile` da), `2` hiçbir şey değiştirilmeden durdu (deneme çalıştırmasında:
gerçek çalıştırma ön denetimde dururdu; lisansın bakım kuralı, okunamayan bir `-LicenseFile` ve
yayın tarihi olmayan bir paket de), `3` bir değişiklik sırasında hata oldu. `3` durumunda betik ne
yaptığını ve elle yapılması gerekeni yazar (ayrıca `C:\ProgramData\ARCILOGGER\install\state.json`,
`last_failure`). Hatayı giderip betiği (ya da `ARCILOGGER-Setup.exe`'yi) **yeniden çalıştırın**:
program dosyaları kopyalanırken yarıda kalan bir ilk kurulum (`INSTALL-IN-PROGRESS.json` işareti)
baştan yapılır, sonraki adımlarda kalan bir kurulum onarılır; `-Force` gerekmez. Kurulum günlüğü
önce `%TEMP%\ARCILOGGER-<kip>-*.log` olarak yazılır; veri klasörü oluştuysa bir kopyası onun `logs`
alt klasörüne konur (`D:\ARCILOGGER\logs\` ya da `C:\ARCILOGGER\logs\`). Pencerenin sonunda günlüğün
yolu yazılır.

> Paketinde bulunmayan bir program için hizmet kurulmaz ve uyarı verilir; veritabanı paketi
> (`arcilogger-db`) yoksa veritabanı adımları atlanır. Bu sürümün paketinde altı hizmetin de
> programı vardır.

ARCILOGGER programları `PATH` üzerinde **değildir**: komutları her zaman tam yoluyla çalıştırın
(`C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-*.exe`; `-InstallDir` ile başka bir klasör
seçtiyseniz o klasörün `venv\Scripts` alt klasörü). Yapılandırma ve gizli depo anahtarını yalnızca
yöneticiler ve ARCILOGGER hizmetleri okuyabildiği için bu komutlar **yönetici olarak açılmış**
Windows PowerShell'de çalışır.

### 7.1 Kuruluşa özgü değerler

ARCILOGGER hiçbir kuruluşun bilgisini taşımaz: kuruluş adı, saat kaynağı ve yönetici PC'sinin
adresi için **varsayılan değer yoktur**. Kurulum bunları parametrelerden alır; eksik olanı, konsolun
başında biri varsa (kurulum `.exe`'sinin penceresi, yönetici PowerShell'i) sorar, yoksa ön denetimde
durur ya da (saat kaynağı için) uyarır. `-NonInteractive`, CI ya da yönlendirilmiş çıktıyla bir şey
sorulmaz.

| Değer | Parametre | Nereye yazılır | Verilmezse |
|---|---|---|---|
| Kuruluş adı (raporlar, e-postalar, kanıt paketleri, web arayüzünün kenar çubuğu) | `-SiteName "<kuruluş adı>"` | `site_name` | İlk kurulumda sorulur; konsol yoksa ön denetim durur. Yükseltmede yapılandırmadaki ad kalır |
| Yerel saat kaynağı (genellikle ana yönlendirici ya da yerel bir NTP sunucusu) | `-NtpServer <adres ya da ad>` | `[clock] ntp_server` | İlk kurulumda sorulur (Enter: yok). Boş kalırsa Windows Time ve saatlik saat denetimi yalnızca `[clock] fallback_server` (`time.ume.tubitak.gov.tr`) kullanır ve ön denetim uyarır. Yükseltmede yapılandırmadaki değer kalır |
| Yönetici PC'sinin adresi ya da ağı (ör. `10.0.0.10`, `10.0.0.0/24`) | `-AdminAddress` | `install\firewall-state.json` (güvenlik duvarı: 443 ve 3389 yalnızca ona açılır) | Hatırlanan değer; o da yoksa sorulur, konsol yoksa ön denetim durur (`-SkipFirewall` ile gerekmez) |

`-SiteName` ve `-NtpServer` yeni `arcilogger.toml` dosyasına yazılır. Yükseltmede ya da var olan bir
yapılandırmayla verilirlerse dosyada yalnızca o satır değişir; önceki dosya
`arcilogger.toml.before-<zaman>` olarak saklanır. Kurulumdan sonra eksikleri denetlemek için:

```powershell
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" config-check
```

`HATA [ERROR]` satırları (kuruluş adı yok; `[clock] ntp_server` ile `[clock] fallback_server`
ikisi de boş) çıkış kodu `1` verir; `UYARI [WARNING]` (yerel saat kaynağı yok; log kaynağı,
`[[sources]]`, yok) çıkış kodunu değiştirmez; `--json` aynı sonucu JSON olarak yazar. Kuruluş adı
boşken ARCILOGGER çalışır, ama raporlar, e-postalar ve kanıt paketleri adsız oluşturulur.

Ürünle **hiçbir ağ planı gelmez.** Yeni `arcilogger.toml` **hiçbir log kaynağı** içermez: örnek
yapılandırmadaki kurgusal örnek ağın `[[sources]]` satırları yalnızca yorum satırıdır, ilk kurulum
kuruluşta olmayan bir cihazı yapılandırmaz (hiç mesaj göndermeyecek cihazlar için sessiz kaynak
uyarısı ya da adresi tutan bir göndericiye yanlış ayrıştırıcı olmaz). Bu geçerli bir durumdur, ama
kaynak eklenene kadar güvenlik duvarı 514'ü hiçbir göndericiye açmaz ve hiçbir log kaydedilmez
(`-SkipFirewall` ile her gönderici `unknown-<ip>` olarak kaydedilir, ayrıştırıcısı içerikten
seçilir); sessiz kaynak uyarısı (R15) hiçbir kaynağı izlemez. Ön denetim, kurulumun sonundaki
sonraki adımlar ve `config-check` bunu uyarı olarak yazar (kurulum uyarıyla, çıkış kodu `1` ile
biter). Güvenlik duvarınızın, ana yönlendiricinizin (DHCP sunucusu) ve log gönderen diğer
cihazlarınızın `[[sources]]` kayıtlarını ekleyin (dosyadaki örneklerin başındaki `#` işaretini
silip adları ve adresleri değiştirerek; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4) ve kurulumu
yeniden çalıştırın (ya da `Set-ArciFirewall.ps1`'i çalıştırıp hizmetleri yeniden başlatın, bölüm
12); güvenlik duvarı 514'ü yalnızca onlara açar (bölüm 11). Kurulumdan sonra kaynaklar web
arayüzünde **Ayarlar → Kaynaklar** sayfasından da eklenir: güvenlik duvarı kendiliğinden izler,
yeniden başlatma gerekmez ([AYARLAR.md](AYARLAR.md)). Yükseltme var olan `arcilogger.toml`
dosyasını ve kaynaklarını korur (yalnızca verilen `-SiteName` / `-NtpServer` satırı değişir).
Veritabanında VLAN listesi ve departmanlar yeni bir kurulumda boştur: ikisi de Cihazlar ve
Kişiler sayfasının **Departmanlar ve VLAN'lar** sekmesinde girilir ([ILK-ADIMLAR.md](ILK-ADIMLAR.md)
bölüm 5 ve 12).

### 7.2 Tarayıcıdan yönetim

Kurulumdan sonra sunucunun ayarları web arayüzünün **Ayarlar** sayfalarından değişir: kuruluş adı,
saat kaynakları, Kamu SM (test → üretim sihirbazı), log kaynakları, e-posta, replika, saklama,
yedekleme ([AYARLAR.md](AYARLAR.md)). API yapılandırmayı yazamaz; imzalı istekleri yönetim
hizmeti `ArciAdmin` uygular, güvenlik duvarını ve Windows Saati'ni SYSTEM görevi `ApplySystem`
yapılandırmadan kendisi türetir. Bu bölümdeki komutlar konsoldaki yol olarak geçerli kalır.

Kurulum sınırları yalnızca yöneticilerin değiştirebildiği
`C:\ProgramData\ARCILOGGER\admin\policy.json` dosyasına yazar ve sonraki çalıştırmalarda
hatırlar:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade `
    -SourceNetwork 172.16.0.0/12 -AdminChangeHoldHours 24 -ZamaneHoldHours 48
# tarayıcı yönetimini kapatmak (5651 işlevleri etkilenmez):
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -DisableBrowserAdministration
```

Elle düzenlenmiş geçerli bir `policy.json` dosyasındaki değerler korunur; geçersiz bir dosya
tarayıcı yönetimini kapatır ve kurulum onu uyararak yeniden yazar.

Kurulum yapılandırmadaki açık her log kaynağının adresini bu ağlarla karşılaştırır. Dışında kalan
bir kaynak varsa (ör. başka bir yerleşkenin genel ya da CGNAT adresi, /16'dan geniş bir ağ)
hiçbir şey değiştirmeden durur ve verilecek `-SourceNetwork` değerini yazar: aksi hâlde
`ApplySystem` ilk çalışmasında, tarayıcıda hiçbir değişiklik yokken, o göndericiyi 514
kuralından çıkarırdı. Tarayıcı yönetimi kapalıyken ya da `-SkipFirewall` ile bu bir uyarıdır.

**Veri klasörü kaydı:** kurulum veri klasörünü `C:\ProgramData\ARCILOGGER\data-root.txt`
dosyasına yazar (yalnızca yöneticiler değiştirebilir; her çalıştırmada silinip yeniden
oluşturulur). Hizmetler ve görevler `data_dir` değeri bu kayıttan farklı bir yapılandırmayı
reddeder. Sonraki her çalıştırma veri klasörünü bu kayıttan alır: `arcilogger.toml` içindeki
`data_dir` kayıttan farklıysa ya da kayıt okunamıyorsa kurulum hiçbir şey değiştirmeden durur ve
iki değeri yazar. Veri klasörünü bilerek taşıdıysanız (veriler kopyalandıktan sonra) kurulumu
`-DataRoot <yeni klasör>` ile çalıştırın; kayıt yalnızca o zaman değişir.

**Görevlerin çalıştırdığı programlar:** görevler yalnızca güvenilen yerlerdeki programları
çalıştırır (`C:\Program Files`, `C:\Program Files (x86)`, sistemin `PATH` üzerindeki `java`).
Kurulum, Program Files dışındaki bir `java`, ek bağımsız değişkenli bir `[tsa] java` komutu ve
`[backup] pg_dump` / `[backup] pg_restore` yollarını SHA-256 değerleriyle `policy.json` dosyasına
kaydeder ve her biri için uyarır; program değişince kurulumu yeniden çalıştırın. Kaydedilecek
programın klasörüne yöneticiler ve SYSTEM dışında yazabilen biri varsa kurulum hiçbir şey
değiştirmeden durur ve programı Program Files altına taşımanızı ister. Zamane istemcisi yalnızca
`C:\ProgramData\ARCILOGGER\zamane\` klasöründen çalışır: `[tsa] zamane_jar` başka bir yeri
gösteriyorsa kurulum dosyayı oraya kopyalar, `zamane_jar` ayarını kopyaya çevirir (önceki
yapılandırma `arcilogger.toml.before-<zaman>` olarak kalır) ve SHA-256 değerini
`zamane\zamane-jar.sha256` dosyasına ekler.

## 8. Kök sertifikayı yönetici PC'sine kurma

Caddy, ARCILOGGER'ın HTTPS sertifikalarını kendi iç sertifika otoritesiyle (CA) üretir. Tarayıcının
güvenmesi için bu CA'nın **kök sertifikası** yönetici PC'sine bir kez kurulur (geçerlilik süresi
yaklaşık 10 yıl; ara ve uç sertifikaları Caddy kendisi yeniler).

1. Sunucudaki `C:\ProgramData\ARCILOGGER\caddy-root.crt` dosyasını yönetici PC'sine kopyalayın
   (yalnızca açık sertifika; özel anahtar sunucudan çıkmaz).
2. Yönetici PC'sinde özetini, kurulumun sonunda yazılan değerle karşılaştırın:

   ```powershell
   Get-FileHash .\caddy-root.crt -Algorithm SHA256
   ```

3. Yönetici PowerShell'de kurun (Edge ve Chrome Windows deposunu kullanır):

   ```powershell
   Import-Certificate -FilePath .\caddy-root.crt -CertStoreLocation Cert:\LocalMachine\Root
   ```

   Ya da dosyaya çift tıklayın → *Sertifika Yükle* → *Yerel Makine* → *Tüm sertifikaları
   aşağıdaki depolama alanına yerleştir* → **Güvenilen Kök Sertifika Yetkilileri**.
4. Firefox kendi deposunu kullanır: *Ayarlar → Gizlilik ve Güvenlik → Sertifikaları Göster →
   Yetkililer → İçe Aktar*, ya da `about:config` içinde `security.enterprise_roots.enabled = true`.
5. `arcilogger.internal` adını kullanmak için yönetici PC'sinin
   `C:\Windows\System32\drivers\etc\hosts` dosyasına `<sunucu adresi> arcilogger.internal` satırını
   ekleyin ya da ağın DNS sunucusunda statik bir kayıt oluşturun (MikroTik DNS kullanılıyorsa
   **doğrulayın**: `/ip dns static add name=arcilogger.internal address=<sunucu adresi>`).

## 9. İlk giriş

Kurulumdan sonra hiçbir kullanıcı yoktur; ilk **yönetici**, sunucu konsolunda üretilen tek
kullanımlık bir **kurulum anahtarıyla** web arayüzünden oluşturulur. İlk kurulumun penceresi bu
anahtarı, kurulum günlüğü kapandıktan sonra kendisi gösterir (bölüm 7); o zaman adım 2'den devam
edin. Anahtarı kaybettiyseniz ya da kurulumu betikle, yönlendirilmiş çıktıyla çalıştırdıysanız:

1. Sunucuda Başlat menüsünden **Windows PowerShell'i yönetici olarak** açın (yapılandırmayı, gizli
   depo anahtarını ve denetim kaydı klasörünü yalnızca yöneticiler ve ARCILOGGER hizmetleri
   okuyabilir) ve çalıştırın:

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

   (`-InstallDir` ile başka bir program klasörü seçtiyseniz yolu ona göre değiştirin; kurulumun
   sonunda yazılan komut doğru yolu gösterir.) Komut anahtarı **yalnızca bir kez** ekrana yazar.
   Anahtar **24 saat** geçerlidir ve **bir kez** kullanılır; komutu yeniden çalıştırmak yeni bir
   anahtar üretir ve öncekini geçersiz kılar. Bir yönetici hesabı varsa komut anahtar üretmez.
   Çıkış kodları: `0` tamam, `2` kullanım ya da yapılandırma hatası, `3` veritabanına
   ulaşılamadı, `4` reddedildi: bir yönetici hesabı zaten var (o hesapla giriş yapın ya da adım
   4). Komut, çalıştıran Windows hesabını denetim kaydına yazar.
2. Tarayıcıda `https://<sunucu adresi>` adresini açın (kurulumun sonunda yazılan web adresi;
   adres çubuğunda uyarı çıkıyorsa bölüm 8;
   sunucunun kendisinde `https://localhost`, kök sertifika için bölüm 17).
   Henüz yönetici olmadığı için **İlk kurulum** sayfası açılır: anahtarı, kullanıcı adını, görünen
   adı ve parolayı (en az 12 karakter; kurallar [SECURITY.md](SECURITY.md) bölüm 2) girin.
3. Yeni hesapla giriş yapın. **İki adımlı doğrulama (TOTP)** yönetici için zorunludur ve ilk girişte
   kurulur: QR kodu bir doğrulama uygulamasıyla okutun, ekranda bir kez gösterilen **10 kurtarma
   kodunu** güvenli bir yerde (sunucuda değil) saklayın. Ardından **KVKK bilgilendirmesini**
   onaylayın.
4. **Kurtarma** (bütün yöneticiler telefonlarını ve kurtarma kodlarını kaybettiyse): aynı programla
   konsoldan yeni bir yönetici oluşturulur:

   ```powershell
   & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" create-admin --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
   ```

Diğer kullanıcılar (denetçi vb.) ve sonraki adımlar: [ILK-ADIMLAR.md](ILK-ADIMLAR.md).

## 10. Kamu SM bağlantısı (test → üretim)

Web arayüzünde **Ayarlar → Kamu SM** sihirbazı aşağıdaki adımları aynı sırayla yapar (Java,
Zamane istemcisi, kök sertifikalar ve parmak izleri, müşteri bilgileri ve kontör sorgusu, damga
testi, üretime geçiş; [AYARLAR.md](AYARLAR.md)). Konsoldaki yol:

Kamu SM'den test kullanıcısı geldiğinde ([kamusm.md](kamusm.md) bölüm 4) adımları **bu sırayla**
yapın. `tsa-test` önce damgayı alır (1 kontör), sonra onu sabitlenmiş köklerle doğrular: kökler
yoksa test kontör harcadıktan sonra "no pinned root certificates" hatasıyla biter. Gece
mühürlemesi (`\ARCILOGGER\Seal` görevi) de kökler olmadan hiçbir günü mühürleyemez.

1. **Kök sertifikalar:** Kamu SM kök (ve gerekiyorsa ara) sertifikalarını
   `sertifikalar.kamusm.gov.tr` adresinden indirip `C:\ProgramData\ARCILOGGER\kamusm-roots\`
   klasörüne koyun (kesin indirme adresleri henüz açık konu: [kamusm.md](kamusm.md) K10 —
   **doğrulayın**). Her dosyanın parmak izini Kamu SM'nin yayımladığı değerle karşılaştırın ve
   kayda geçirin:

   ```powershell
   Get-ChildItem C:\ProgramData\ARCILOGGER\kamusm-roots | Get-FileHash -Algorithm SHA256
   ```

   (`Get-FileHash` dosyanın özetini verir; DER biçimli bir sertifikada bu, sertifikanın SHA-256
   parmak izidir. PEM dosyasını önce kökler klasörünün dışında DER'e çevirin:
   `certutil -decode kok.pem $env:TEMP\kok.der`, sonra `Get-FileHash $env:TEMP\kok.der -Algorithm SHA256`. Windows sertifika penceresindeki "Parmak izi"
   SHA-1'dir, karşılaştırmaya uygun değildir.)
2. **Kimlik bilgileri** (ekranda görünmez, şifreli saklanır). İlk kurulumun sonunda girdiyseniz
   (bölüm 7) `& $core secret list` (aşağıdaki `$core` ile) ikisini de gösterir; bu adımı atlayın:

   ```powershell
   $core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
   & $core secret set kamusm.customer       # müşteri numarası
   & $core secret set kamusm.password       # parola
   & "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"   # sonra: yönetim hizmetinin hakkı
   ```

   Konsoldan her `secret set` / `secret delete`, `secrets.json` dosyasını yeni bir dosyayla
   değiştirir; yeni dosyada yönetim hizmetinin (`ArciAdmin`) yazma hakkı yoktur. Ardından
   `Set-ArciVaultAcl.ps1` betiğini (ya da kurulumu) yeniden çalıştırın; yoksa tarayıcıdan yapılan
   bir sonraki değişiklik "yönetim hizmeti ayar dosyalarına artık yazamıyor" uyarısıyla uygulanır
   (`acl_drift`, kritik `R20-admin` uyarısı) ve ondan sonraki değişiklik, betik çalıştırılana
   kadar başarısız olur. İlk kurulumun sonunda girilen değerlerden sonra kurulum bunu kendisi
   yapar.
3. **Kontör:** `& $core credit` — kontör okunuyor mu? (çıktı biçimi açık konu K4)
4. **Bağlantı testi:** `& $core tsa-test` — 1 KB rastgele dosyayı damgalar ve doğrular (1 kontör).

Test sunucusunun damgalarının yasal geçerliliği yoktur; test damgalı günler arayüzde işaretlenir.
Test sırasında Seal görevinin günleri test damgasıyla mühürlemesini isteyip istemediğinize
**önceden** karar verin ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3: `[seal] first_day`).

### Üretime geçiş

1. `zdportal.kamusm.gov.tr` üzerinden kurumsal başvuru ve en az 10.000 kontör satın alma
   ([kamusm.md](kamusm.md) bölüm 1).
2. Üretim hesabının müşteri numarası ve parolasını `secret set kamusm.customer` /
   `secret set kamusm.password` ile yeniden kaydedin (test kullanıcısı ayrı bir taleple verilir;
   test bilgilerinin üretimde geçerli olup olmadığı **doğrulanmadı**).
3. `C:\ProgramData\ARCILOGGER\arcilogger.toml` içinde `[tsa]` bölümünde
   `url = "http://zd.kamusm.gov.tr"` yapın. ARCILOGGER'ın bir damgayı "yasal nitelikli" (legal
   grade) göstermesi için damganın sertifika zinciri, aynı bölümde `legal_root_fingerprints`
   listesine yazdığınız bir Kamu SM kökünün SHA-256 parmak izine ulaşmalıdır (adım 1'de kayda
   geçirdiğiniz değer); liste boşsa hiçbir damga yasal nitelikli gösterilmez.
4. API değişen yapılandırmayı 2 saniye içinde kendisi yükler; kırmızı "test damgası" uyarısı ve
   rapor damgaları yeni ayarı izler (zamanlanmış görevler her çalıştırmada yeniden okur). Bu
   sürümden önceki bir sürümde ya da Ayarlar sayfası "Yeniden başlatma bekleyen değişiklik" gösteriyorsa: `Restart-Service ArciApi`.
5. `& $core credit` ve `& $core status` (`legal_grade`) ile denetleyin; Mühürleme sayfasında kırmızı
   uyarı kalkmış olmalıdır.

ARCILOGGER yalnızca Kamu SM üretim sunucusundan alınan ve zinciri `legal_root_fingerprints`
listesindeki bir köke ulaşan damgaları yasal nitelikli olarak işaretler; test sunucusunun
damgalarının yasal geçerliliği yoktur (Kamu SM). Damgaların ve kayıt düzeninin
hukuki değerlendirmesi için hukuk müşavirine danışın.

Kimlik bilgisi değişiklikleri (`secret set` / `secret delete`) değerleri olmadan
`D:\ARCILOGGER\state\seal\secret-changes.jsonl` dosyasına yazılır; worker bunları 15 dakika
içinde denetim kaydına `settings.secret_change` olarak aktarır. Kayıt yazılamazsa komut çıkış
kodu 1 ile uyarır.

### Replika (ör. bir NAS'ta WORM paylaşımı)

İkinci kopyanın hedefi (`[replica] target`) bir NAS'ta ya da başka bir sunucuda bir SMB
paylaşımıdır; mümkünse WORM (bir kez yaz) özelliği açık bir klasör. Replica görevi
`LOCAL SERVICE` hesabıyla çalışır; bu hesap ağa anonim bağlanır. Yalnızca replika kullanıcısına
açık bir paylaşım için depolama cihazında bu klasörde yalnızca okuma-yazma hakkı olan bir
kullanıcı oluşturun ve (`<NAS>` yerine cihazın adını yazın):

```powershell
# arcilogger.toml: [replica] enabled = true, target = '\\<NAS>\arcilogger-worm',
#                  user = '<NAS>\arcireplica'
& $core secret set replica.password      # parola (ekranda görünmez)
& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"   # ArciAdmin'in secrets.json hakkı (bölüm 10)
& $core replica --test                   # bağlan, deneme dosyası yaz/oku/sil
```

ARCILOGGER paylaşıma her çalışmada bu kullanıcıyla sürücü harfi olmadan bağlanır
(`WNetAddConnection2W`, profile kaydedilmez) ve işi bitince bağlantıyı kapatır; parola komut
satırına ya da günlüklere yazılmaz. Kopyalar önce `<target>\.arcilogger-staging\`
altına yazılır ve özeti tutunca gün klasörüne taşınır; WORM paylaşımın yeni yazılmış bir dosyanın
yeniden adlandırılmasına izin verdiği sahada **doğrulanmalıdır**. Yarım kalmış eski bir kopya
"yüksek" uyarı verir ve tamamlanır; içeriği farklı bir dosya "kritik" uyarı verir ve asla üzerine
yazılmaz.

## 11. Cihaz ayarları

FortiGate ve MikroTik ayarları için: [DEVICE-SETUP.md](DEVICE-SETUP.md). İlk test mesajından sonra
**her beklenen kaynak için** `D:\ARCILOGGER\spool\<kaynak>\<gün>.log` dosyası oluşmalıdır
(kaynak başına denetim: [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4). Güvenlik duvarı 514'e
yalnızca `[[sources]]` adreslerinden izin verir: adresi yanlış yazılmış bir cihazın kayıtları
sunucuya **hiç ulaşmaz**; hiç `[[sources]]` yokken (yeni bir kurulum, bölüm 7.1) 514 kapalıdır.

## 12. Kurulum sonrası denetim listesi

```powershell
Get-Service Arci* | Format-Table Name, Status, StartType
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Format-Table TaskName, State
Get-NetFirewallRule -Group ARCILOGGER | Format-Table DisplayName, Direction, Enabled
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" status
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" license status --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
```

Son komut lisans durumunu, açık modülleri ve sınırları yazar (deneme, kalan gün; [LISANS.md](LISANS.md)
bölüm 5).

Güvenlik duvarı (`Set-ArciFirewall.ps1`):

- **Gelen:** varsayılan eylem *Engelle*. İzin yalnızca: UDP/TCP 514 `[[sources]]` adreslerinden
  (hiç `[[sources]]` yoksa 514 için kural oluşturulmaz, 514 kapalı kalır ve betik bunu yazar),
  TCP 443 ve TCP/UDP 3389 `-AdminAddress` adreslerinden. Bunların dışındaki **tüm etkin gelen izin
  kuralları kapatılır** ve kaydedilir — Windows'un "Uzak Masaüstü", "Windows Uzaktan Yönetimi
  (HTTP-In)" (TCP 5985, Windows Server'da varsayılan olarak açık), "Dosya ve Yazıcı Paylaşımı"
  kuralları ve başka programların eklediği kurallar dahil. Yalnızca "Core Networking" grubu
  (DHCP, IPv6 komşu bulma, ICMP hata iletileri) açık kalır. PowerShell uzaktan yönetimi
  gerekiyorsa `-AllowWinRM` (TCP 5985, yalnızca yönetici PC'sinden); gelen bağlantı bekleyen bir
  izleme/yedekleme aracı varsa kuralını `-KeepRule '<kural adı>'` ile koruyun.
- **Giden (isteğe bağlı):** `C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1
  -StrictOutbound`. Varsayılan eylem *Engelle* olur; izin yalnızca NTP, DNS, Kamu SM (zd/tzd ve
  `[tsa]` adresi, `[tsa] port`), Kamu SM sertifika sunucusu, SMTP ve replika hedefine verilir;
  Windows'un diğer etkin giden izin kuralları da ("Core Networking" dışındakiler) kapatılır.
  Windows Update için bakım sırasında `-StrictOutbound:$false` ile kapatın.
- **SMTP (uyarı e-postası), yalnızca sıkı giden modda:** `-StrictOutbound` açıksa ve
  `arcilogger.toml` dosyasının `[smtp]` bölümünde `enabled = true` ve `host` doluysa betik her
  çalıştığında (yükseltmede de) bu sunucuya, bölümün `port` değeri (varsayılan 587) için giden
  kuralı kendisi oluşturur. Ad olarak yazılmış bir `host` çalıştırma anında IP adreslerine çözülür:
  sağlayıcının adresleri değişirse betiği yeniden çalıştırın. Başka bir posta sunucusu gerekiyorsa
  `-SmtpServer <sunucu:port>` ayrı bir kural ekler (o da yalnızca sıkı giden modda) ve hatırlanır.
  Varsayılan modda giden trafiğe izin verilir ve SMTP kuralı oluşturulmaz. Hatırlanan `-SmtpServer`
  değerini unutturmak için boş değeri `-Command` ile verin: Windows PowerShell 5.1 bir programa boş
  bir bağımsız değişken iletmez; `-File` ile betik `-SmtpServer` değerini hiç almaz ve durur.

  ```powershell
  powershell -ExecutionPolicy Bypass -Command "& 'C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1' -SmtpServer ''"
  ```
- Seçimler (`-StrictOutbound`, `-AdminAddress`, `-AllowWinRM`, `-KeepRule`, `-SmtpServer`)
  `C:\ProgramData\ARCILOGGER\install\firewall-state.json` dosyasında **hatırlanır**: yükseltme
  bunları sessizce değiştirmez. Değiştirmek için parametreyi yeniden verin.
- `[[sources]]` değişiklikleri (Ayarlar → Kaynaklar ya da elle) toplayıcı ve ingest tarafından 5
  saniye içinde yüklenir; `ApplySystem` görevi syslog kurallarını yerinde günceller (yalnızca
  `ARCILOGGER Syslog UDP/TCP` ve sıkı giden kipte `ARCILOGGER Out …` kuralları; başka kural, profil,
  443, 3389, 5985 değişmez), elle düzenlemeden sonra en geç 5 dakika içinde. Tarayıcı yönetimi
  kapalıysa (`-DisableBrowserAdministration`), `[tsa]` adresi/portu ya da yönetici adresleri
  değiştiyse betiği kendiniz çalıştırın. `[collector]`, `[api]`, `[ingest]`, `[worker]` ya da
  `[database]` değiştiyse hizmetleri yeniden başlatın (toplayıcı dururken gelen UDP loglar birkaç
  saniye kaybolabilir):

  ```powershell
  powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
  Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
  ```
- Kaldırma (`-Remove`, kaldırmada otomatik): ARCILOGGER kuralları silinir, kapatılan kurallar ve
  profil ayarları ilk çalıştırmadan önceki hâline döner.

## 13. Yükseltme

1. Yönetici PC'sinde yeni paketi indirin ya da oluşturun (bölüm 4), sunucuya kopyalayın ve parmak
   izini denetleyin (bölüm 6). `ARCILOGGER-Setup-<sürüm>.exe` kurulu sürümü kendisi yükseltir;
   sunucu adresi, web adı, yönetici adresleri ve güvenlik duvarı seçimleri hatırlandığı, kuruluş
   adı ve saat kaynağı yapılandırmadan okunduğu için yeniden verilmez (değiştirmek için
   `-SiteName` / `-NtpServer`, bölüm 7.1).
2. Bakım penceresinde (toplayıcı yalnızca program dosyaları ve izinler değiştirilirken durur; bu
   sürede UDP ile gelen loglar kaybolur, UDP yeniden göndermez):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -DryRun
   powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade
   ```

**Lisans ve bakım** ([LISANS.md](LISANS.md) bölüm 7 ve 8): yükseltme, hiçbir şey değiştirmeden
önce yeni paketin yayın tarihini kurulu lisansın (veri klasöründeki `state\license-status.json`)
ya da `-LicenseFile` ile verilen dosyanın bakım tarihiyle karşılaştırır; sürüm kapsanmıyorsa `2`
ile durur ve bunu Türkçe ve İngilizce yazar. Bakımı yenileyip gelen lisans dosyasını yükseltmeyle
birlikte verin:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade -LicenseFile C:\Kurulum\ARCI-L-<numara>.lic
```

Ya da sürümü `-AllowUncoveredUpgrade` ile yine de kurun: yalnızca 5651 Temel ile çalışır,
ücretli modüller sürümü kapsayan bir lisans yüklenene kadar kapalıdır (5651 yükümlülüğü için
gereken bir düzeltme böylece her zaman kurulabilir). Yalnızca kurulum `.exe` dosyası varsa
ikisi de onunla verilir (bölüm 7):

```powershell
.\ARCILOGGER-Setup-<sürüm>.exe /LICENSEFILE="C:\Kurulum\ARCI-L-<numara>.lic"
.\ARCILOGGER-Setup-<sürüm>.exe /ALLOWUNCOVEREDUPGRADE
```

Kurulu paketi yeniden çalıştırmak (onarım)
yalnızca uyarır. Lisansı olmayan bir kurulumun kasasında 14 günden eski mühürlü günler varsa ön
denetim, deneme süresinin yükseltmeden hemen sonra dolmuş sayılacağını **uyarır** (durmaz):
ücretli modüller kapanır, 3'ten fazla etkin kullanıcı varsa yeni kullanıcı eklenemez, 5651
işlevleri etkilenmez. Lisanslamadan önceki bir sürümden yükseltmede ne yapılacağı:
[LISANS.md](LISANS.md) bölüm 8. Kurulum programının kuralı yol göstericidir; asıl denetimi çalışan
ürün yapar (`version_not_covered`).

Yükseltme önce veritabanını yedekler (`D:\ARCILOGGER\backups\db\*.dump`; yedek alınamazsa
hiçbir şey değiştirmeden durur), zamanlanmış görevleri duraklatır, hizmetleri durdurur, eski
program klasörünü `C:\Program Files\ARCILOGGER.previous` olarak saklar, yeni sürümü kurar,
veritabanı kullanmayan toplayıcıyı ve web sunucusunu hemen yeniden başlatır, **en son** şemayı
yükseltir ve diğer hizmetleri ondan sonra başlatır (yönetim hizmeti `ArciAdmin` ilk durur, en son
başlar). Yapılandırma, gizli depo ve veriler yerinde kalır. Veritabanı yedeğinden hemen sonra
`arcilogger.toml` dosyası `arcilogger.toml.before-upgrade-<UTC zamanı>` olarak kopyalanır ve adı
`install\state.json` dosyasında hatırlanır (`config_before_upgrade`); görevlerin çalıştırdığı
programlar kaydedilir (bölüm 7.2), bir program güvenilmeyen bir klasördeyse yükseltme hiçbir şey
değiştirmeden durur. Yükseltme öncesi yedekler (`backups\db`) gece yedeklerinden (`backups\scheduled`,
[YEDEKLEME.md](YEDEKLEME.md)) ayrıdır ve kendiliğinden silinmez; eskilerini elle silin. Yükseltme
çalışan görevlerin bitmesini bekler: gece yedeği (01:40) sürerken başlatmayın.

Bir adım başarısız olursa (çıkış kodu `3`):

- **Şema yükseltmesinden önce** (klasör taşınamadı, program kurulamadı, güvenlik duvarı ya da
  görev hatası): eski sürüm kendiliğinden geri getirilir ve başlatılır, duraklatılan görevler
  yeniden açılır. Yarım kalan yeni sürüm `C:\Program Files\ARCILOGGER.failed-<zaman>` olarak
  kenara konur (inceleme için; kaldırmada silinir).
- **Şema yükseltmesi sırasında:** şema yarım kalmış olabilir; eski program onunla çalıştırılmaz.
  Eski program klasörü geri gelir, ancak yalnızca `ArciCollector`, `ArciWeb` ve zamanlanmış kanıt
  görevleri çalışır — bunların hiçbiri veritabanı kullanmaz, syslog kaybı olmaz, mühürleme sürer.
  `ArciIngest`, `ArciApi`, `ArciWorker` elle başlatmaya alınır ve durur. Betik, yükseltme öncesi
  yedeği geri yükleyecek komutu tam olarak yazar:

  ```powershell
  powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Rollback -RestoreDatabase -DatabaseDump "D:\ARCILOGGER\backups\db\<yedek>.dump"
  ```

  Ya da hatayı giderip yükseltmeyi yeniden çalıştırın.
- Kendiliğinden yapılamayan her şey "ELLE YAPILMASI GEREKENLER" başlığıyla yazılır.

**Geri alma:**

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Rollback
# veritabanını da yükseltme öncesi yedeğe döndürmek için:
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Rollback -RestoreDatabase
```

`ARCILOGGER.previous` yoksa `-Rollback -RestoreDatabase` yalnızca veritabanını geri yükler
(toplayıcı çalışmaya devam eder; geri yükleme tek işlemdir, başarısız olursa veritabanı olduğu gibi
kalır).

Geri alma (ve başarısız bir yükseltmenin kendiliğinden geri dönüşü), önceki sürümün hizmetlerini
başlatmadan önce yapılandırmayı **o sürümün Python'uyla** yükler. Tarayıcıdan yapılmış bir
değişikliği (ör. kapatılmış bir log kaynağı, `[[sources]] enabled = false`) önceki sürüm
tanımıyorsa yükseltme öncesi kopya geri konur, reddedilen dosya
`arcilogger.toml.refused-<zaman>` olarak yanında kalır ve betik "Tarayıcıdan yapılan sonraki
değişiklikler geri alındı; denetim kaydında duruyorlar" uyarısını yazar. Kopya yoksa geri alma `3`
ile durur ve dosyanın elle düzeltilmesini ister. Tarayıcı yönetiminden önceki bir sürüme dönüşte
`ArciAdmin` hizmeti kaldırılır; mühürleme ve diğer görevler sürer.

`ApplySystem` görevi yeni sürümle birlikte kalkar, ama güvenlik duvarının 514 kuralını ve Windows
Saati'ni yeni yapılandırmaya göre bırakmıştır. Bu yüzden geri alma, hizmetleri başlatmadan önce
geri gelen sürümün kendi `Set-ArciFirewall.ps1` (durum dosyasında hatırlanan seçimlerle) ve
`Set-ArciTime.ps1` betiklerini çalıştırır (`system.restore` adımı; başarısız bir yükseltmenin
geri dönüşünde, yapılandırma geri konduysa): yükseltme öncesi kopyada yeniden açık görünen ya da
geri gelen bir kaynağın logları yine kabul edilir. Bu adım başarısız olursa geri alma yine
tamamlanır, çıkış kodu `1` olur ve betik elle çalıştırılacak komutu yazar:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
```

**Arama'lı sürüme yükseltme (şema revizyonu `0016_search`).** Şema yükseltmesi web filtresi
tablosu `fg_web` üzerinde yeni bir indeks oluşturur: `ix_fg_web_rhost_time` ("bu alan adı ve alt
alan adları" araması). Geliştirme makinesinde (4 vCPU, varsayılan `maintenance_work_mem` değeri
64 MB olan PostgreSQL 16, aylık bölümler) **10 milyon `fg_web` satırı başına yaklaşık 15 saniye**
sürdü (13–15 sn, indeks 553 MiB; 2026-10-26 ölçümü, makinede başka işler de çalışırken ve 3,6 GiB
tablo bellekteyken: diskten okunan bir tabloda ya da yavaş bir diskte daha uzun sürer). Günde 1
milyon log satırında, bunların yaklaşık %18'i web filtresi satırıyken, 400 gün yaklaşık 72 milyon
`fg_web` satırı eder: yaklaşık 2 dakika, belleğe sığmıyorsa daha uzun; günde 5 milyon satırda beş
katı. Şema yükseltilirken ingest durur; toplayıcı spool'a yazmayı sürdürür, hiçbir şey kaybolmaz
ve ingest ardından yetişir ([INGEST.md](INGEST.md)). Yükseltmeden sonra worker işi
`search.counts_backfill`, yükseltmeden önce yüklenmiş günleri Arama'nın özet tablosuna 10
dakikada bir, en yeni gün önce sayar: bir dakikadan sonra yeni dosyaya başlamaz; bir dosyanın
yeniden sayımı milyon satır başına yaklaşık 9 sn sürer (geliştirme makinesi) ve worker'ın öteki
işleri onu bekler. Bir gün sayılana kadar Arama o günün istatistiklerini doğrudan sayar (daha
yavaş, sonuç aynı). Sayılmayı bekleyenler:

```powershell
& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" search-counts status --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"
```

**Aynı anda çok kişi arama yapıyorsa.** Aramalar, canlı akışlar, raporlar ve web arayüzünün bütün
sayfaları API'nin veritabanı bağlantılarını paylaşır (`[database] pool_size`, varsayılan 5).
Arama'yı aynı anda birkaç kişinin kullandığı bir kurumda `arcilogger.toml` içinde `pool_size`
10'a, `search.max_concurrent` ayarı da onunla birlikte yükseltilir (en çok `pool_size` eksi 2;
Ayarlar → Arama ve canlı akış, [AYARLAR.md](AYARLAR.md)), ardından hizmetler yeniden başlatılır
(bölüm 12).

**İnternet çıkışlı sürüme yükseltme (şema revizyonları `0017_fortigate_dhcp`,
`0018_nat_egresses`).** Şema yükseltmesi FortiGate DHCP kayıtları için yeni bir tablo ve
`fg_traffic` tablosuna bir sütun ekler (büyük tablolarda bile hızlıdır: sütun boş eklenir).
Eski NAT ayarları tek bir yeni ayara, **İnternet çıkışları**na çevrilir: `nat.public_ips`
adresleri "Genel IP (yükseltmede aktarıldı)" adlı doğrudan bir çıkış, `nat.wan_transip`
"FortiGate WAN adresi (yükseltmede aktarıldı)" adlı bir servis sağlayıcı NAT'ı çıkışı olur
(adres genel IP'lerden biriyse ayrı çıkış yapılmaz; `nat.public_ips` içinde özel ya da
paylaşılan bir adres, ör. 100.64.x.x, varsa o da bu çıkışın NAT adresi olur), genel adresi
olmayan hatlar için, "FortiGate WAN adresi" çıkışı yapılmadıysa ve bir genel IP ya da
`lookup.port_hint` aktarıldıysa, "Genel adresi tanımlanmamış hatlar" adlı bir çıkış eklenir;
`lookup.port_hint` "FortiGate WAN adresi" çıkışının, o yoksa bu çıkışın port koruma değerine
geçer. Dönüşüm denetim kaydına `settings.change` olarak yazılır (`details.migration =
"0018_nat_egresses"`; atılan geçersiz, IPv6 ya da ayrılmış değerler `details.dropped` içinde).
Dönüşüm geçerli bir tanım veremezse çıkışlar boş kalır ve `R20-nat:conversion-failed` uyarısı
açılır.

**Yükseltmeden sonra:**

- Her hattın genel adresini ve arayüzlerini (dstintf) Ayarlar → Uygulama ayarları → 5651
  sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT) bölümünde girin. Aktarılan
  "FortiGate WAN adresi" ya da "Genel adresi tanımlanmamış hatlar" çıkışı olan her kurulumda,
  bu girilene kadar hiçbir çıkışa tanımlı olmayan bir adres için yapılan her sorgu ve rapor
  `egress_public_unknown` uyarısını taşır; aktarılan çıkışlar arayüz girilene ya da
  değiştirilene kadar eski NAT davranışını korur, ancak sonradan eklenen bir çıkışın NAT
  adresleri bunun dışındadır. Hat hâlâ servis sağlayıcı yönlendiricisinin arkasındaysa ve
  FortiGate'in o hattaki NAT adresini (ör. 100.64.x.x) yeni bir çıkış olarak eklerseniz,
  hattın genel adresini doğrudan çıkıştan bu çıkışa taşıyın ve arayüzlerini girin; aksi hâlde
  doğrudan çıkışın sorguları "Doğrudan hat doğrulanamadı" uyarısıyla yanıtlanabilir.
- Aktarılan "FortiGate WAN adresi" çıkışının NAT adresi hattın genel adresiyse (FortiGate genel
  adresi kendisi tutuyorsa) çıkışın türünü **Doğrudan** yapın; editör bunu uyarır.
- Bütün hatlar tanımlandıktan sonra "Genel adresi tanımlanmamış hatlar" çıkışını silin: o
  kaldıkça hiçbir hatta ait olmayan bir genel IP sorulduğunda ilgisiz oturumlar aday olarak
  listelenir.
- Bir değişiklik: hiçbir çıkışa tanımlı olmayan bir adres o anda FortiGate kayıtlarında kaynak
  NAT adresi olarak görünüyorsa sorgu artık aday listesi yerine o adresle kesin eşleşme yapar
  ve bunu uyarıyla belirtir (`egress_observed_not_configured`).
- FortiGate DHCP kullanılıyorsa [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4'teki doğrulamayı
  yapın ve sonucu bölüm 16'ya yazın; her FortiOS yükseltmesinden sonra doğrulamayı yineleyin.
  Yükseltmeden önceki anlar için 5651 sorguları iç IP adresinde durur: o zamana ait FortiGate
  DHCP kayıtları kira zaman çizelgesine dönüştürülmez, sorgu bunu belirtir ve iç IP sorgusunda
  ya da kesin eşleşmede kaydı kanıt olarak gösterir (`fortigate_dhcp_not_converted`). Daha eski
  bir kiraya (MikroTik ya da FortiGate) dayanan bir zincirde, kiranın son DHCP kaydından sonra o
  adres ya da o kiranın MAC adresi için böyle bir kayıt varsa (ör. yükseltmeden önce
  MikroTik'ten FortiGate DHCP'ye geçen bir istemci başka bir adres aldıysa) zincir düşük güvenle
  ve nedeniyle gösterilir (`fortigate_dhcp_not_converted_since_lease`). **Yükseltmeden sonra
  da** her istemci için yeni sürümde ilk DHCP Ack kaydı gelene kadar zincir kurulmaz: bu, kira
  süresinin yarısına kadar sürebilir (FortiOS varsayılanı 7 günlük kirada 3,5 gün; istemci
  yenilemeyi kirasının yarısında yapar). Yükseltmeyi bu boşluğu bilerek planlayın; FortiGate
  DHCP kullanmayan kurulumlar bundan etkilenmez.
- Yükseltmeden önce (ya da bir geri alma sırasında önceki sürümce) yüklenen FortiGate oturum
  kayıtlarının srcmac alanı 5651 sorgusunda kullanılmaz; o anlar için srcmac ile kısmi zincir
  kurulmaz.
- Genel bir NAT adresi hiçbir çıkışa tanımlı değilse (ör. bir IP havuzu) artık çift NAT
  belirtisi sayılmaz ve aday aramasına alınmaz: FortiGate'in kendi adresidir, sorulursa
  kayıtlarda görülen adres olarak yanıtlanır. FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da
  şubeye yaptığı NAT'ın adreslerini "İnternet çıkışı değil" türüyle girin.

**Geri alma:** eski NAT satırları bu sürümde okunmadan saklanır; `-Rollback` şema geri dönüşü
yapmaz, önceki sürüm bu satırları, yani **yükseltme öncesi** NAT değerlerini okur. Yükseltmeden
sonra İnternet çıkışlarında bir değişiklik yaptıysanız, geri döndükten sonra önceki sürümün
Ayarlar → Uygulama ayarları bölümündeki `nat.public_ips` ve `nat.wan_transip` değerlerini bu
sürümde yaptığınız değişiklikle karşılaştırıp elle girin (değişiklikler denetim kaydında
`settings.change` olarak durur); bunun için veritabanını geri yüklemeyin: `-RestoreDatabase`
yükseltmeden sonraki bütün kayıtları (denetim, sorgu, rapor, uyarı, cihaz ve kişi atamaları)
siler. `-Rollback` çalıştırmadan önce bu sürümde yapılan sorguların resmi raporlarını (PDF)
indirin: bir rapor ilk indirildiğinde bir kez oluşturulur ve önceki sürüm, bu sürümün eklediği
satırları (tanımsız gönderici uyarısı, "Kısmi kanıt" etiketi, çıkış ve FortiGate kira satırları)
yazmaz. FortiGate DHCP kullanan bir kurulumda önceki sürüm FortiGate DHCP kayıtlarını okuyamaz:
bir FortiGate kirasını, adres bırakıldıktan ya da başka bir istemciye verildikten sonra da açık
tutabilir. Bu yüzden `-Rollback`, program değiştirilmeden önce, bütün FortiGate kiralarını
**düşük güvene** indirir; kaç kira olduğunu yazar ve kurulum günlüğüne kaydeder (yalnızca
türetilmiş kira satırları değişir; ham kayıtlara ve mühürlere dokunulmaz; `-RestoreDatabase`
ile bu adım yapılmaz). Önceki sürümde FortiGate DHCP'ye dayanan bütün zincirler bu nedenle
düşük güvenle görünür. Önceki sürüm mevcut FortiGate kiralarını kendi ayarlardaki sürelerine
göre (kayıttaki kira süresine bakmadan, varsayılanlarla kiranın son DHCP kaydından yaklaşık 26
saat sonra; bu an geçmişte de olabilir) kapatır, geri dönüş günü ya da önceki gün başlayanları
(ve geç gelen kayıtlar için yeniden oluşturduğu günlerde başlayanları) kendi yeniden
oluşturmasında siler ve yenisini oluşturmaz; MikroTik kiraları önceki sürümün kendi
kurallarıyla, yükseltmeden önceki gibi sürer. Geri dönüşten sonraki anlar için FortiGate DHCP
adreslerinin 5651 sorgularını yeniden yükseltmeden sonra yapın. Yeniden yükseltildiğinde
aradaki günlerin FortiGate DHCP kayıtları kira zaman çizelgesine girmez ve sorgular bunu
belirtir: zincir kurulamayan bir anda, o anı kapsayan en yeni DHCP kaydı böyle bir kayıtsa
`fortigate_dhcp_not_converted` ile; bir DHCP kirasına dayanan zincirde, kiranın son DHCP
kaydından sonra o adres ya da o kiranın MAC adresi için böyle bir kayıt varsa zincir düşük
güvenle ve nedeniyle gösterilir (`fortigate_dhcp_not_converted_since_lease`).

`-Rollback` FortiGate kiralarını, geri dönülen sürüm FortiGate DHCP kayıtlarını okusa da
düşük güvene indirir: FortiGate DHCP okuyan iki sürüm arasındaki bir geri almadan sonra da
FortiGate DHCP zincirleri bir sonraki `arcilogger-db upgrade` çalışmasına kadar düşük güvenle
görünür. O yükseltme (ör. yeniden yükseltme) onları geri getirir (aşağıda). Bu düşük güven
hiçbir zaman yanlış bir kişiyi göstermez; zinciri yalnızca gereğinden düşük gösterir.

**Yeniden yükseltme:** veritabanı şeması geri alınmadığı için dönüşüm yeniden çalışmaz ve eski
NAT ayarları yeniden çevrilmez. Geri dönüş sırasında önceki sürümde `nat.public_ips`,
`nat.wan_transip` ya da `lookup.port_hint` değiştirildiyse `R20-nat:legacy-changed` uyarısı
değişen ayarları adlarıyla gösterir: değişikliği İnternet çıkışları bölümüne elle işleyin;
uyarı çıkışlar kaydedilince kapanır. Yeniden yükseltme, önceki sürümün değiştirmediği FortiGate
kiralarının güven düzeyini geri getirir ve kaç kiranın geri geldiğini, kaçının düşük kaldığını
ve kiraların yeniden oluşturulacağı günleri yazar; düşük kalanlar önceki sürümün kapattığı ya
da yeniden hesapladığı kiralardır. FortiGate DHCP kullanan bir kurulumda yeniden yükseltmeden
sonra, geri dönüş gününden en uzun kira süresi kadar önceki günden (7 günlük kirada 7 gün
önce) yeniden yükseltme gününe kadar kiraları Ayarlar → Uygulama ayarları → 5651 sorgu ve kira
zaman çizelgesi → Kiraları yeniden oluştur bölümünde yeniden oluşturun (bir istekte en çok 31
gün): önceki sürümün erken kapattığı, sildiği ya da düşük güvende bıraktığı FortiGate kiraları
ancak böyle FortiGate'in kendi bitişine ve güven düzeyine göre yeniden kurulur; yapılmazsa o
aralıktaki anlar için sorgu nedensiz bir "kira yok" verebilir ya da zinciri düşük güvenle
gösterir. Yeniden kurulan bu kiralar geri dönüş aralığında, kiranın son DHCP kaydından sonra
önceki sürümün o adres ya da MAC adresi için yüklediği bir FortiGate DHCP kaydı varsa düşük
güvenle ve nedeniyle gösterilir (`fortigate_dhcp_not_converted_since_lease`): adres o arada
bırakılmış, başka bir cihaza verilmiş ya da MAC adresi başka bir adres almış olabilir.

## 14. Kaldırma

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Uninstall
```

Betik önce zamanlanmış görevleri duraklatır ve çalışan bir görevin (ör. mühürleme, aylık
doğrulama) bitmesini bekler (`-TaskWaitMinutes`, varsayılan 30): görevler program klasöründeki
Python'u kullanır. O sürede bitmeyen bir görev varsa **hiçbir şey kaldırmadan** durur (çıkış kodu
`2`, görevler yeniden etkinleşir); daha sonra yeniden çalıştırın. Ardından hizmetler (`ArciAdmin` dahil), zamanlanmış
görevler, olay günlüğü kaynağı `ARCILOGGER-Admin` ve güvenlik duvarı kuralları silinir (kapatılan
Windows kuralları ve profil ayarları geri gelir; yazılmış olaylar günlükte kalır); masaüstü Windows'ta güç ayarları kurulumdan önceki değerlerine döner
(`C:\ProgramData\ARCILOGGER\install\power-state.json`). Saat dilimi, zaman eşitleme ayarları ve
"Dosya Sistemi" denetim ilkesi geri alınmaz. Program klasöründe `installer\` en son silinir: Windows bir dosyayı silmeyi reddederse
betik yerinde kalır ve kaldırma yeniden çalıştırılarak tamamlanır. Yalnızca kurulumun oluşturduğu program klasörleri silinir:
`C:\Program Files\ARCILOGGER`, `.previous`, `.failed-*`, `.rolled-back-*` — içinde kurulumun
`VERSION.json` ya da `INSTALL-IN-PROGRESS.json` dosyası olanlar. Başka bir klasör (ör. yanlış
yazılmış bir `-InstallDir`), sürücü kökü ya da Windows klasörü verilirse betik **hiçbir şey
silmeden** durur. **Ham loglar, kasa, dışa aktarımlar, yedekler (`D:\ARCILOGGER`), yapılandırma,
gizli depo ve tarayıcı yönetiminin `admin\` klasörü (`C:\ProgramData\ARCILOGGER`) ve veritabanı
silinmez.** Saklanan kayıtlar yasal
delildir; silinmeleri yalnızca saklama/imha işiyle ve imha tutanağıyla olur.

## 15. Sorun giderme

| Belirti | Çözüm |
|---|---|
| "betik çalıştırılamaz" (execution policy) | `powershell -ExecutionPolicy Bypass -File ...` ve `Unblock-File` (bölüm 6) |
| `[HATA] PostgreSQL 16 bulunamadı` | Bölüm 2; hizmet adı farklıysa `-PgServiceName`, psql başka yerdeyse `-PgBinDir` |
| `[collector] bind = "…" bu bilgisayarın adresi değil` ya da `-ServerAddress … bu bilgisayarın adresi değil` | Toplayıcı o adreste dinleyemezdi, hiç log gelmezdi. İletideki adreslerden birini `arcilogger.toml` içinde `[collector] bind` olarak yazın (ya da `0.0.0.0`) veya kurulumu doğru `-ServerAddress` ile yeniden çalıştırın (bölüm 1.2) |
| `Sunucu adresi kendiliğinden seçilemedi` / `bulunamadı` | Birden fazla ağ bağdaştırıcısında (ikinci kart, VPN, sanal anahtar) ağ geçidi var ya da hiçbirinde yok: `-ServerAddress <adres>` verin; değer hatırlanır |
| `Adres DHCP ile alınmış` (uyarı) | Bilgisayara sabit IP verin ya da DHCP sunucusunda (ör. ana yönlendiricide) rezervasyon yapın; adres değişirse loglar gelmez |
| `Kuruluş adı gerekli` / `Kuruluş adı yok` | `-SiteName "<kuruluş adı>"` verin ya da kurulumu konsoldan çalıştırıp adı girin (bölüm 7.1); yükseltmede `arcilogger.toml` içindeki `site_name` boşsa da durur |
| `Yerel saat kaynağı ayarlanmamış` (uyarı) | Saat yalnızca `[clock] fallback_server` ile eşitlenir: ana yönlendiricinin ya da yerel bir NTP sunucusunun adresini `-NtpServer` ile verin (bölüm 7.1) |
| `Yönetici PC'sinin adresi gerekli` | `-AdminAddress <adres ya da ağ>` verin ya da kurulumu konsoldan çalıştırıp girin; değer hatırlanır (bölüm 7.1) |
| `Windows 10 desteklenmiyor` / `Windows 11 Home desteklenmiyor` | Windows 11 Pro/Enterprise/Education ya da Windows Server kullanın (bölüm 17); yalnızca denemek için `-AllowUnsupportedWindows` |
| `çıkarılabilir bir sürücü`, `CD/DVD sürücüsü`, `ağ sürücüsü`, `RAM diski` | Kanıt yerel, sabit bir NTFS diskte durur: `-DataRoot` ile başka bir disk seçin (bölüm 1.1) |
| `boş alan … < 100 GB` | Kanıt en az iki yıl saklanır ve büyür: daha büyük bir disk seçin; boyutlandırmayı doğruladıysanız `-MinFreeGB` |
| `UDP 514 kullanımda` | Başka bir syslog sunucusu çalışıyor; durdurun veya kaldırın |
| `TCP 443 kullanımda` | IIS veya başka bir web sunucusu; durdurun ya da 443'ü bırakın |
| `TCP 8000 kullanımda` (API portu) | Başka bir program `[api] port` değerini kullanıyor: onu durdurun ya da `arcilogger.toml` içinde `[api] port` değerini değiştirip kurulumu yeniden çalıştırın (Caddy'nin yönlendirmesi, sağlık denetimi ve ön denetim bu değeri izler) |
| Kaldırma `2` ile bitti: `Görev hâlâ çalışıyor` | Bir kanıt görevi (mühürleme, doğrulama) sürüyor; hiçbir şey kaldırılmadı. Görev bitince yeniden çalıştırın ya da `-TaskWaitMinutes` değerini artırın |
| `... silinmek üzere işaretli` | Hizmetler (services.msc) penceresini kapatıp betiği yeniden çalıştırın |
| `Açık uzak masaüstü oturumu ... keserdi` (ön denetim) | Kurulumu `-AdminAddress` adreslerinden birinden bağlanarak yapın ya da bağlandığınız adresi/ağı `-AdminAddress` ile ekleyin (`-Force` oturumu keser) |
| `-KeepRuleGroup hiçbir kurala uymuyor` | "Core Networking" grubunun adı farklı: `Get-NetFirewallRule -DisplayGroup '<ad>' \| Select-Object -First 1 Group, DisplayGroup` ile bulup `Set-ArciFirewall.ps1 -KeepRuleGroup '<Group değeri>'` |
| Bir izleme/yedekleme aracına dışarıdan bağlanılamıyor | Güvenlik duvarı ARCILOGGER dışındaki gelen kuralları kapattı: `Set-ArciFirewall.ps1 -KeepRule '<kural adı>'` (hatırlanır) |
| Yükseltme `3` ile bitti | Ekrandaki açıklama ve `C:\ProgramData\ARCILOGGER\install\state.json` (`last_failure`, `pending_database_restore`); bölüm 13 |
| `Bu sürüm (…) lisansınızın bakım süresinden (…) sonra yayımlandı; hiçbir şey değiştirilmedi` | Paket lisansın bakım tarihinden sonra yayımlanmış: bakımı yenileyip yeni lisans dosyasını `-LicenseFile` ile verin ya da sürümü yalnızca 5651 Temel ile kurmak için `-AllowUncoveredUpgrade` (bölüm 13, [LISANS.md](LISANS.md) bölüm 7) |
| `Lisans dosyası okunamadı` | `-LicenseFile` yolunu, dosyanın boyutunu (en çok 65.536 bayt) ve içeriğini denetleyin: lisans verenden gelen, değiştirilmemiş `.lic`; hiçbir şey değiştirilmedi |
| `Lisans dosyası yüklenmedi` (uyarı) | Ürün dosyayı reddetti (nedeni iletide: başka sunucu, bozuk dosya, bakım …) ya da veritabanına ulaşılamadı; kurulum sürdü ve `1` ile bitti. Nedeni giderip dosyayı web arayüzünden (Ayarlar → Lisans) ya da iletideki komutla yükleyin ([LISANS.md](LISANS.md) bölüm 4.3 ve 13) |
| `Paket yayın tarihi yok` | Paketin `bundle-manifest.json` dosyasında `release_date` yok: paket eski ya da bozuk, yeniden indirin ya da oluşturun (bölüm 4) |
| `6. Lisans [licence]: istek kodu alınamadı` | İletideki `license request-code` komutunu sonra yönetici PowerShell'inde çalıştırın ya da web arayüzünde Ayarlar → Lisans'ı açın; `license_server_id_unavailable`: sunucu kimliği okunamadı (bölüm 16) |
| Hizmet başlamıyor | `D:\ARCILOGGER\logs\<Hizmet>.out.log`, `.err.log`, `.wrapper.log` |
| Tarayıcı sertifika uyarısı | Bölüm 8; doğru adres mi (kurulumun yazdığı `https://<sunucu adresi>`) |
| Veritabanı parolası kayboldu | `Install-Arcilogger.ps1 -ResetDatabasePassword` yeni parola üretir ve rolü günceller |
| `arcilogger-api` / `arcilogger-core` "tanınmıyor" (not recognized) | Programlar `PATH` üzerinde değildir: tam yolla çalıştırın, ör. `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"` (bölüm 9) |
| `setup-token` ya da `secret set` "erişim reddedildi" / gizli anahtar okunamadı | PowerShell'i **yönetici olarak** açın (bölüm 9) |
| Gece mühürlemesi olmuyor, `status` çıktısında `pending_days` büyüyor | Görev Zamanlayıcı'da `\ARCILOGGER\Seal` görevinin son çalışma sonucu ve `D:\ARCILOGGER\logs\core.log`. Yapılandırma hataları — Kamu SM kimlik bilgileri yok, kökler (`kamusm-roots`) yok, `[tsa] zamane_jar` ayarlı değil (bölüm 10) — görevi mühürleme başlamadan durdurur: sonuç `0x1`, hata her çalışmada `core.log` dosyasında ve gün ve neden başına bir kez `D:\ARCILOGGER\state\alerts.jsonl` içinde `R20-seal-config`. Başlamış bir deneme başarısız olursa (ör. Kamu SM'ye erişim yok) `R20-seal` ([UYARILAR.md](UYARILAR.md)) |
| PostgreSQL günlüğünde (`D:\ARCILOGGER\db\log`) "could not rename file" / "Permission denied" ya da mühürleme sürekli yeniden deniyor | Virüs koruması istisnaları (bölüm 2 adım 5): `Get-MpPreference`; tespitler için `Get-WinEvent -LogName 'Microsoft-Windows-Windows Defender/Operational'` (olay numaraları **doğrulayın**). Defender `D:\ARCILOGGER` altında bir dosyayı karantinaya aldıysa kayda geçirin ve bir sonraki mühürleme ya da doğrulamadan önce geri yükleyin |
| `... 'Microsoft.PowerShell.Security' ... could not be loaded` (`ConvertTo-SecureString`, `Get-Acl`) | Windows PowerShell'i, bir PowerShell 7 (pwsh) penceresinden başlatılmış başka bir program (cmd.exe, bir kurulum `.exe`'si, bir araç) başlatmış ve PowerShell 7'nin modül klasörlerini devralmış (pwsh bu klasörleri yalnızca kendi başlattığı `powershell`'den çıkarır; about_PSModulePath). ARCILOGGER betikleri bu klasörleri kendileri çıkarır; kendi komutlarınız için Başlat menüsünden "Windows PowerShell"i yönetici olarak açın |
| Kurulum `1` koduyla hemen bitti, hiçbir şey kurulmadı ya da yükseltilmedi | PowerShell, betik ilk satırından önce durduğunda da (sözdizimi ya da parametre hatası) `1` döndürür. Betiği yönetici konsolundan çalıştırın (bölüm 7, yükseltmede bölüm 13 ve `-Upgrade`); kırmızı hata ekranda kalır. Betik ilk satırına ulaştıysa kurulum `.exe`'sinin penceresi de Enter'a basana kadar açık kalır; ulaşmadıysa kapanır. Kurulum `.exe`'si, `0` ya da `1` kodundan sonra `C:\Program Files\ARCILOGGER\VERSION.json` taşıdığı paketin parmak izini göstermiyorsa hata bildirir — ilk kurulumda da yükseltmede de |

## 16. Sahada doğrulanacaklar

- WinSW hizmetlerinin `NT SERVICE\<ad>` sanal hesabıyla başlaması (`sc.exe qc ArciCollector`);
  "Hizmet olarak oturum aç" hakkının `NT SERVICE\ALL SERVICES` için varsayılan olarak bulunması.
- EDB yükleyicisinin `D:\ARCILOGGER\db` klasöründe verdiği haklar (kurulum, PostgreSQL hizmet
  hesabının tam yetkisini ayrıca korur) ve `pg_hba.conf` içinde `127.0.0.1/32 scram-sha-256`.
- `auditpol` "Dosya Sistemi" alt kategorisinin GUID ile açılması (`auditpol /get /category:*`).
- Temurin MSI `INSTALLDIR` özelliği; TimescaleDB `setup.exe`'nin kurulu PostgreSQL 16 sürümüyle
  çalışması ve geri yüklemede `timescaledb_pre_restore()` / `timescaledb_post_restore()`; Inno
  Setup'ta `Turkish.isl` dil dosyası.
- Güvenlik duvarındaki "Core Networking" grubunun kaynak adı:
  `Get-NetFirewallRule -DisplayGroup 'Core Networking' | Select-Object -First 1 Group` →
  `@FirewallAPI.dll,-25000` beklenir (Türkçe Windows'ta görünen ad farklı olabilir; betik hem
  kaynak adını hem görünen adı kabul eder). Uygulamadan sonra
  `Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow` yalnızca ARCILOGGER ve Core
  Networking kurallarını göstermelidir.
- `arcilogger-api serve` komutunun 127.0.0.1:8000'de dinlemesi; `setup-token` komutunun yönetici
  PowerShell'inde tam yoluyla çalışıp anahtar yazması (bölüm 9).
- Python hizmetlerinin yalıtılmış Python'la başlaması (`Get-CimInstance Win32_Process -Filter
  "Name = 'python.exe'" | Format-List CommandLine`: `python.exe -I -X utf8 -u -m arcilogger_api
  serve …`; `sc.exe qc` hizmet sarmalayıcısını gösterir) ve sunucu kimliğinin ArciApi hizmetinin
  sanal hesabıyla ve yönetici PowerShell'inde aynı okunması (Ayarlar → Lisans'taki sunucu kimliği
  `arcilogger-db license status` çıktısındakiyle aynı olmalı; [LISANS.md](LISANS.md) bölüm 4.1).
- Microsoft Defender'ın durumu ve bölüm 2 adım 5'teki iki istisnanın kurulumdan sonra yerinde
  olması (`Get-MpPreference`); `spool` ve `vault` için sahibin kararı (istisna mı, zamanlanmış
  tarama mı).
- Gece veritabanı yedeği: ilk gecenin `D:\ARCILOGGER\state\backup\last.json` sonucu, yedeğin
  süresi ve boyutu, ilk geri yükleme tatbikatı ([YEDEKLEME.md](YEDEKLEME.md) bölüm 6 ve 8).
- Arama'lı sürüme yükseltmede `ix_fg_web_rhost_time` indeksiyle şema yükseltmesinin gerçek
  veritabanında ne kadar sürdüğü (bölüm 13) ve ilk günlerden sonra
  `arcilogger-db search-counts verify` sonucu: özet tablosu satırlarla aynı olmalı; küçük harfle
  saklanmayan ad sayıları (`fg_dns.qname`, `fg_web.hostname`) 0 olmalı.
- Tarayıcı yönetimi ([AYARLAR.md](AYARLAR.md) bölüm 9): `arcilogger.toml` değiştirildikten sonra
  ArciAdmin'in dosyaya özel izninin yerinde kalması (`Get-Acl`), ArciAdmin'in `kamusm-roots`
  klasörüne yalnızca kendi dosyalarını ekleyip silebilmesi, `ARCILOGGER-Admin` olaylarının
  `Requests`, `Seal` ve `ApplySystem` görevlerini uyandırması (`Get-WinEvent`, Görev Zamanlayıcı
  geçmişi) ve güvenlik duvarı kuralı yerinde güncellenirken syslog iletisi kaybolmaması.
- Masaüstü Windows 11 Pro'da: yukarıdakilerin hepsi ve güç ayarları, Akıllı Uygulama Denetimi,
  zorunlu bir güncelleme yeniden başlatması — adım adım:
  [KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md).

**FortiGate DHCP, srcmac ve internet çıkışları.** Aşağıdaki varsayımlar FortiOS belgelerine
dayanır ya da kurumun ağına bağlıdır; sahada doğrulanır (V numaraları bu listenin kendi
numaralarıdır; aynı varsayımlar [CORRELATION.md](CORRELATION.md) bölüm 8'de konularına göre
anlatılır). Her satırın sonucunu ve
denetimin tarihini yazın. Denetimleri **her FortiOS yükseltmesinden sonra** yineleyin (kayıt
alanları sürümler arasında değişebilir) ve yeni sonucu tarihiyle ekleyin; eskisini silmeyin.
Bir sonuç beklenenden farklıysa [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4'teki dallara göre
ayarı düzeltin. FortiGate DHCP kullanılmıyorsa V1–V6, V14 ve V17 satırlarına "kullanılmıyor"
yazın.

| # | Doğrulanacak | Nasıl | Yazılacak sonuç | Sonuç | Tarih |
|---|---|---|---|---|---|
| V1 | FortiGate DHCP Ack kaydının (logid `0100026001`) syslog'daki gerçek biçimi: `ip`, `mac`, `dhcp_msg`, `lease`, `interface`, `hostname` alanları, tırnaklar, MAC adresinin yazımı; `hostname` alanının olmaması ya da boş olması | [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2.4 adım 5: Kaynaklar → Kira kanıtı kartı ve deneme 5651 Sorgu; geçince `dhcp.fortigate_format_verified` açılır (adım 6) | geçti / geçmedi; düzeltilen ayar | | |
| V2 | İstemcinin adresi bırakması (RELEASE) kaydediliyor mu; logid'i (beklenen `0100026002`) ve `dhcp_msg` değeri | DEVICE-SETUP.md bölüm 2.4 adım 5 (`ipconfig /release`, sonra `ipconfig /renew`) ve dalları; başka bir logid `R20-lease:fortigate-refused` uyarısında "tanınmayan logid" olarak görünür | RELEASE kaydediliyor: evet / hayır; logid; `dhcp_msg` | | |
| V3 | Yenilemeler (istemci bağlı kalırken kira süresinin yarısında) ikinci bir DHCP Ack kaydı veriyor mu | DEVICE-SETUP.md bölüm 2.4 adım 5, yenileme denetimi | yenilemeler kaydediliyor: evet / hayır; hayırsa kurumun DEVICE-SETUP.md bölüm 2.4 adım 5'teki seçimi (olduğu gibi bırakmak ya da `dhcp.fortigate_traffic_extends` ayarını açmak) ve seçimin tarihi | | |
| V4 | `lease` alanı saniye cinsinden mi; süresiz kira (`lease-time` `0`) nasıl yazılıyor | Arama'da (`logid:0100026001`) ham satırdaki `lease` değeri FortiGate'teki `lease-time` ile karşılaştırılır (DEVICE-SETUP.md bölüm 2.4 adım 7); `R20-lease:fortigate-lease` ve `R20-lease:fortigate-lease-unlimited` uyarıları | `lease` = `lease-time`: evet / hayır | | |
| V5 | DHCP olay kayıtları DEVICE-SETUP.md'deki ayarlarla (önem düzeyi `information`, olayları dışlayan süzgeç yok) syslog'a ulaşıyor mu | DEVICE-SETUP.md bölüm 2.4 adım 3–4; Kaynaklar → Kira kanıtı kartının durumu (`ok`, `none`, `refused`) | kartın durumu; `none` ya da `refused` ise yapılan düzeltme | | |
| V6 | FortiGate'in DHCP aktarıcı (relay) olduğu arayüzlerden FortiGate DHCP kaydı gelmediği; `server-type ipsec` sunucularının (DHCP over IPsec, VPN havuzları) arayüzlerinin hariç tutulduğu; bir DHCPINFORM yanıtının DHCP Ack olarak yazılıp yazılmadığı (`dhcp_msg` değeri: V14); FortiGate bir arayüzde aktarılan (relay) birden çok ağa hizmet veriyorsa aktarılan bir istemcinin DHCP Ack kaydının hangi `interface` ve adresi taşıdığı | DEVICE-SETUP.md bölüm 2.4 adım 1, 2 ve 9; `dhcp.fortigate_interfaces_excluded`; `R20-lease:fortigate-dhcp-subnet` uyarısı | aktarılan ağlara tek bir arayüzde hizmet veriliyor: evet / hayır; evetse kayıttaki `interface` ve adresin istemcinin kendi ağından olup olmadığı; hariç tutulan arayüzler | | |
| V8 | srcmac yalnızca aygıt tanıma açık arayüzlerde gelir ve doğrudan bağlı istemcilerin kendi MAC adresidir; arada katman 2 anahtar varsa ancak iki istemci denetimi bunu gösterirse; birimin hangi aygıt tanıma alanlarını doldurduğu | DEVICE-SETUP.md bölüm 2.5: arayüzde iki bilinen istemciyle, "Kayıtlarda görülen NAT adresleri" panelinin (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi) srcmac arayüzleri tablosunda `max_ips_per_mac` 1 (IPv4 kaynaklar) ve Arama'da (`srcmac:`) iki MAC adresi istemcilerin kendisi (bir anahtarın ya da yönlendiricinin değil) | arayüz başına: denetim geçti / geçmedi, `lookup.srcmac_interfaces` listesine alındı mı; birimin doldurduğu alanlar | | |
| V11 | Her hattın servis sağlayıcı yönlendiricisi kaynak portu koruyor mu | DEVICE-SETUP.md bölüm 2.6: port koruma ölçümü | hat başına `preserved` (ölçüldü) ya da `unknown` | | |
| V14 | Her eşlenmiş logid altında FortiOS'un yazdığı `dhcp_msg` değerleri | Arama'da ham satırlar (`logid:<logid>`); `R20-lease:fortigate-refused` uyarısı "beklenmeyen dhcp_msg" nedeniyle görülen değerleri yazar; `dhcp.fortigate_dhcp_msgs` | logid başına görülen `dhcp_msg` değerleri | | |
| V15 | Her hattın genel IP adresi sabit mi | DEVICE-SETUP.md bölüm 2.6: servis sağlayıcıya sorulur; değişebilecek adresli hat `public_dynamic` olarak işaretlenir (servis sağlayıcı NAT'ı çıkışında; değişen adresli doğrudan hattı ARCILOGGER kayıtlardan bulur, DEVICE-SETUP.md bölüm 2.6) | hat başına sabit / değişebilir; servis sağlayıcının yanıtı | | |
| V16 | FortiGate DHCP kayıtları kullanılabilir bir cihaz zamanı taşıyor ve FortiGate'in saati dakikalar içinde doğru: FortiGate NTP ile eşitleniyor | DEVICE-SETUP.md bölüm 2.1 (NTP adımı); FortiGate'te `diagnose sys ntp status` NTP sunucularının bilgisini listeler; `R20-lease:source-clock` uyarısı açık olmamalı | FortiGate NTP ile eşitleniyor: evet / hayır; `diagnose sys ntp status` çıktısındaki NTP sunucusu | | |
| V17 | FortiOS bir DHCPDECLINE kaydı yazıyor mu ya da bir adresi vermeden önce denetliyor mu; bir istemci bir sunucu arayüzü ve ağı için tek bir kira tutar | Ayrı bir deneme yoktur: statik cihazlar DHCP aralığının dışında tutulur (DEVICE-SETUP.md bölüm 2.4 adım 8); `R20-lease:fortigate-decline:<ağ>` uyarısı | uyarı açıldı mı; açıldıysa ağ ve yapılan düzeltme (adres ayrıldı ya da cihaz taşındı) | | |

## 17. Masaüstü Windows 11 Pro

ARCILOGGER, ARCILOGGER'a **ayrılmış** ve sürekli açık bir Windows 11 Pro (ya da Enterprise,
Education) bilgisayarda da çalışır; hiçbir adımı Windows Server gerektirmez. Bu bölüm, masaüstü
Windows'un sunucudan farklı olan yanlarını anlatır. Duyurmadan önce kabul testini yapın:
[KABUL-TESTI-WINDOWS11.md](KABUL-TESTI-WINDOWS11.md).

**Lisans.** Windows istemci lisans koşulları, cihazın sunucu olarak kullanımını sınırlayan bir madde
içerir (Windows 11 OEM koşulları, Nisan 2024, madde 2(c)(v); Windows 10 koşullarında da aynı
madde vardır). ARCILOGGER'ın bu kullanımının o maddeye nasıl düştüğü belirlenmedi: Windows lisans
koşullarını bir Microsoft iş ortağıyla doğrulayın. Kurulum ön denetimi masaüstü Windows'ta bunu
bir bilgi satırıyla hatırlatır; bu belge bir sonuca varmaz.

**Donanım ve Windows.**

- Windows 11 donanım gereksinimleri: TPM 2.0, UEFI ve **Güvenli Önyükleme (Secure Boot)**,
  desteklenen bir işlemci (Microsoft'un Windows 11 belirtimleri). Windows 10'da kalmış eski ofis
  bilgisayarları bunları karşılamayabilir.
- x64 işlemci. ARM64 bilgisayarlarda ARCILOGGER'ın x64 programları öykünmeyle çalışır; bu
  denenmedi, ön denetim uyarır.
- Windows 11 **25H2 (build 26200)** ya da sonrası. Daha eski bir Windows 11 yapısında ön denetim
  uyarır: o yapı güncelleme almıyor olabilir, Microsoft'un destek tarihlerini kontrol edin. Windows
  10 ve Windows 11 Home desteklenmez (ön denetim durur).
- **S modu kapalı** olmalıdır: S modunda yalnızca Microsoft Store uygulamaları çalışır, kurulum
  `.exe`'si ve PowerShell betiği çalışmaz (ön denetim bunu göremez). S modundan çıkış Ayarlar →
  Sistem → Etkinleştirme altındadır ve geri alınamaz (**doğrulayın**).
- **Akıllı Uygulama Denetimi (Smart App Control):** Microsoft, açık (zorunlu) kipte imzasız kodun
  engellendiğini belgeler. ARCILOGGER paketindeki `python.exe`, `python312.dll`, `caddy.exe` ve
  WinSW imzasızdır; EDB'nin `postgres.exe` ve `initdb.exe` dosyalarının da imzasız olduğu
  bildiriliyor. Kurulumdan önce durumuna bakın: Windows
  Güvenliği → Uygulama ve tarayıcı denetimi → Akıllı Uygulama Denetimi. Açıksa kapatmak sahibin
  kararıdır; Microsoft'un belgesine göre yeniden açmak yalnızca Windows'u temiz kurarak olur
  (başka bir kaynak aksini söylüyor: **doğrulayın**). Kabul testi gerçek davranışı kaydeder.

**Güç ayarları (kurulum yapar).** Bir masaüstü bilgisayar uykuya geçer ya da hazırda bekler; o
sırada UDP ile gelen syslog kaybolur ve ARCILOGGER'da hiçbir kaydı kalmaz. Masaüstü Windows'ta
kurulum `Set-ArciPower.ps1` ile şunları yapar ve önceki değerleri
`C:\ProgramData\ARCILOGGER\install\power-state.json` dosyasına yazar; kaldırma (bölüm 14) onları
geri yükler. `-SkipPower` ile hiçbirine dokunmaz.

- Etkin güç planında prize takılıyken (AC) "Uyku" ve "Hazırda beklet" süreleri: hiçbir zaman.
  Pil (DC) değerleri değişmez.
- Hazırda bekletme kapalı (`powercfg /hibernate off`); bu, Hızlı Başlatma'yı (Fast Startup) da
  kapatır. Ayrıca `HiberbootEnabled = 0`: hazırda bekletme yeniden açılsa da Hızlı Başlatma kapalı
  kalır.
- Fiziksel ağ bağdaştırıcılarında, sürücü destekliyorsa "Güç tasarrufu için bilgisayar bu aygıtı
  kapatabilir" kapalı; bağdaştırıcı ya da Windows yeniden başlayınca geçerli olur (açık bir uzak
  masaüstü oturumu kesilmez). Komut bu Windows'ta yoksa betik bunu yazar; o zaman Aygıt
  Yöneticisi'nde elle kaldırın.
- Elle: BIOS/UEFI'de "elektrik geri gelince aç" (AC power recovery) ayarı ve bir UPS.

**Windows Update.** Güncellemeler kurulmalıdır, ama yeniden başlatma sırasında gelen UDP syslog
kaybolur (ARCILOGGER yalnızca tek adresli, beklenen bir kaynağın 60 dakikadan uzun sessizliğini
manifestte boşluk olarak gösterir; daha kısa bir kesintinin kaydı yoktur). Windows Pro'da
güncellemeleri "indir ve bildir" olarak ayarlayın: Yerel Grup İlkesi Düzenleyicisi (`gpedit.msc`)
→ Bilgisayar Yapılandırması →
Yönetim Şablonları → Windows Bileşenleri → Windows Update → Son kullanıcı deneyimini yönetin →
"Otomatik Güncelleştirmeleri Yapılandır" → **3 – Otomatik indir ve yükleme için bildir**
(**doğrulayın**: menü adları). Yeniden başlatmayı bakım penceresinde, elle yapın. Yeniden
başlatmaları gündüz etkin saatlere **zorlamayın**: o saatlerde log trafiği en yüksektir ve kayıp en
büyük olur. Genel internet erişimi yalnızca bakım penceresinde açılır (bölüm 0).

**BitLocker.** Windows 11 cihaz şifrelemesi açıksa kurtarma anahtarını bu bilgisayarda değil,
ayrı ve güvenli bir yerde (kurumun parola kasası, kilitli dolapta bir çıktı) saklayın. Anakart,
TPM ya da BIOS değişikliğinden sonra kurtarma anahtarı olmadan disk açılmaz: ham loglar, kasa ve
veritabanı birlikte erişilemez olur.

**Tek disk.** `D:` olmayan bir bilgisayarda veri klasörü `C:\ARCILOGGER` olur ve ön denetim uyarır:
ham loglar, kasa, veritabanı, gece veritabanı yedekleri (`C:\ARCILOGGER\backups\scheduled`,
[YEDEKLEME.md](YEDEKLEME.md)), yükseltme öncesi yedekler (`C:\ARCILOGGER\backups\db`) ve gizli depo
anahtarı (`C:\ProgramData\ARCILOGGER\secret.key`) Windows ile aynı diskte durur; tek bir disk
arızası hepsini birlikte kaybettirir. Mümkünse ikinci bir iç disk ekleyip `D:` olarak biçimlendirin
(bölüm 1.1) ya da `-DataRoot` ile başka bir sabit disk verin. En azından replikayı (ör. bir NAS'a)
açın (bölüm 10, "Replika") ve gece yedeklerini sunucu dışına kopyalayın
([YEDEKLEME.md](YEDEKLEME.md) bölüm 4); yedeklerin nereye gideceği sahibin kararıdır. Windows'un
özellik güncellemeleri de bu diskte yer ister: boş alanı düzenli denetleyin
([ILK-ADIMLAR.md](ILK-ADIMLAR.md)). Daha sonra bir Windows Server'a taşıma yordamı henüz yazılmadı.

**Arayüzü bu bilgisayarda açmak.** Caddy, Windows'un sertifika deposuna dokunmaz. Tarayıcıyı
ARCILOGGER bilgisayarının kendisinde açacaksanız kök sertifikayı burada da kurun ve
`https://localhost` adresini kullanın (sertifika `localhost` adını da kapsar):

```powershell
Get-FileHash C:\ProgramData\ARCILOGGER\caddy-root.crt -Algorithm SHA256   # kurulumun yazdığı değer
Import-Certificate -FilePath C:\ProgramData\ARCILOGGER\caddy-root.crt -CertStoreLocation Cert:\LocalMachine\Root
```
