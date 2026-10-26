# Cihaz ayarları / Device setup

> Ağ yöneticisi içindir. Komutlar build specification bölüm 15'ten alınmıştır ve **FortiOS 7.6**
> ile **RouterOS 7** için yazılmıştır; her komutu uygulamadan önce cihazınızdaki sürümün
> belgeleriyle doğrulayın. Bu belgede yazanların uygulandığını varsaymıyoruz.

ARCILOGGER hiçbir kuruluşun ağ planını taşımaz: aşağıdaki komutlarda `<…>` yerine kendi
adreslerinizi ve arayüz adlarınızı yazın.

| Yer tutucu | Anlamı |
|---|---|
| `<arcilogger>` | ARCILOGGER sunucusunun adresi (kurulumdaki `-ServerAddress`, `[collector] bind`) |
| `<firewall>` | FortiGate'in logları gönderdiği adres (`set source-ip`) |
| `<router>` | Ana (çekirdek) MikroTik yönlendiricinin, yani DHCP sunucusunun logları gönderdiği adres |
| `<admin-pc>` | Yönetici PC'sinin adresi ya da ağı (kurulumdaki `-AdminAddress`) |
| `<ntp>` | Yerel saat kaynağı (kurulumdaki `-NtpServer`, `[clock] ntp_server`; genellikle `<router>`) |

Her log kaynağı `C:\ProgramData\ARCILOGGER\arcilogger.toml` içinde bir `[[sources]]` kaydıdır.
Yeni bir kurulumda **hiç kayıt yoktur** (örnek yapılandırmadaki kurgusal örnek ağın kayıtları
yorum satırıdır): güvenlik duvarınızın ve ana yönlendiricinizin kayıtlarını bölüm 4'teki gibi
ekleyin ([KURULUM.md](KURULUM.md) bölüm 7.1).

## 1. Ağ akışları (yönlendirici ve güvenlik duvarı)

| # | Kaynak | Hedef | Port | Amaç | Gerekli mi? |
|---|---|---|---|---|---|
| 1 | Tüm altyapı (FortiGate, MikroTik'ler) | `<arcilogger>` | UDP 514 | Syslog | Evet |
| 2 | FortiGate (`<firewall>`), ana yönlendirici (`<router>`) | `<arcilogger>` | **TCP 514** | Güvenilir syslog | Önerilir (bölüm 1.1) |
| 3 | Yönetici PC'si (`<admin-pc>`) | `<arcilogger>` | TCP 443 | Web arayüzü (HTTPS) | Evet |
| 4 | Yönetici PC'si (`<admin-pc>`) | `<arcilogger>` | TCP 3389 (RDP) | Sunucu yönetimi | Evet (uzak yönetim için) |
| 5 | `<arcilogger>` | `<ntp>` | UDP 123 | NTP (zaman) | Yerel saat kaynağı varsa |
| 6 | `<arcilogger>` | zd.kamusm.gov.tr, tzd.kamusm.gov.tr | TCP 80 | Kamu SM zaman damgası (yalnızca özet gider) | Evet (FortiGate üzerinden internet) |
| 7 | `<arcilogger>` | sertifikalar.kamusm.gov.tr | TCP 80/443 | Kök sertifika ve SİL | Evet |
| 8 | `<arcilogger>` | time.ume.tubitak.gov.tr | UDP 123 | Yedek NTP (`[clock] fallback_server`) | Yerel saat kaynağı yoksa evet, varsa önerilir |
| 9 | `<arcilogger>` | Replika hedefi (`[replica] target`, ör. bir NAS) | TCP 445 | Replika (ikinci kopya) | Replika açıksa |
| 10 | `<arcilogger>` | Ağın DNS sunucuları | UDP/TCP 53 | Ad çözümleme | Evet |
| 11 | `<arcilogger>` | Windows Update | TCP 443 | Güncellemeler (bakım penceresinde) | Bakım penceresinde |
| 12 | `<arcilogger>` | SMTP sunucusu | TCP 587 (ya da `[smtp] port`) | Uyarı e-postaları ([UYARILAR.md](UYARILAR.md)); rapor e-postaları (M4) | `[smtp]` ayarlanınca |

TLS'li syslog (TCP 6514) şimdilik kullanılmıyor; gerekirse ayrıca açılacak.

### 1.1 Kaynak adresi taklidi (spoofing) / Forged sender addresses

UDP syslog'da gönderen doğrulanmaz: ARCILOGGER bir mesajın hangi cihazdan geldiğini yalnızca
**gönderen IP adresinden** anlar. LAN'daki bir bilgisayar kaynak adresi `<firewall>` (FortiGate)
veya `<router>` (ana yönlendirici) olan sahte bir UDP paketi gönderebilir; ARCILOGGER onu o
cihazın ham kaydı olarak saklar, mühürler ve sorguda kullanır (ör. sahte bir DHCP `assigned`
satırı gerçek bir kirayı başka bir MAC adresine devredebilir). Mühür, sunucunun neyi aldığını
kanıtlar; satırın gerçekten o cihazdan geldiğini kanıtlamaz. Önlemler (her komutu **RouterOS 7**
belgeleriyle doğrulayın; adresleri ve arayüz adlarını sahadakilerle değiştirin):

1. **TCP'ye geçin** (tablo satır 2; bkz. bölüm 2 `set mode reliable`, bölüm 3
   `remote-protocol=tcp` destekleniyorsa): kör (blind) taklitle TCP bağlantısı kurulamaz. Geçiş
   bittiğinde `arcilogger.toml` içinde o kaynağa `transport = "tcp"` yazın ve toplayıcıyı yeniden
   başlatın. Toplayıcı o adresten gelen UDP mesajlarını **kaydetmez**, sayar
   (`collector-status.json` → `dropped_transport`) ve bir kez `wrong-transport` uyarısı verir.
   Varsayılan `transport = "any"` (UDP ve TCP).
2. **Ana yönlendirici üzerinden gelen sahte adresleri düşürün:** `<arcilogger>`:514'e giden
   syslog'u yalnızca gönderenin kendi arayüzünden kabul edin, geri kalanını düşürün (örnek,
   doğrulayın; anahtar ve erişim noktası ağları için birer satır):

   ```text
   /ip firewall filter
   add chain=forward src-address-type=local action=drop \
       comment="ARCI: forwarded packet claiming a router address" place-before=0
   add chain=forward protocol=udp dst-address=<arcilogger> dst-port=514 \
       src-address=<firewall> in-interface=<transit> action=accept comment="ARCI syslog FW"
   add chain=forward protocol=udp dst-address=<arcilogger> dst-port=514 \
       src-address=<switch-network> in-interface=<switch-vlan> action=accept comment="ARCI syslog SW"
   add chain=forward protocol=udp dst-address=<arcilogger> dst-port=514 \
       src-address=<ap-network> in-interface=<ap-vlan> action=accept comment="ARCI syslog AP"
   add chain=forward protocol=udp dst-address=<arcilogger> dst-port=514 action=drop \
       comment="ARCI: syslog only from the sender's own interface"
   ```

   Aynı kuralları TCP 514 için de yazın. `/ip settings set rp-filter=strict` (veya `loose`)
   ayrıca düşünülebilir; ilke tabanlı bir yönlendirme kullanılıyorsa (ör. FortiGate'ten geçmeyen
   bir misafir ağı, bölüm 5) onu bozmadığını önce doğrulayın.
3. Sunucuyla **aynı ağdaki** cihazlar (ör. bir NAS) sunucuya yönlendiriciden geçmeden ulaşır;
   onlara karşı yalnızca 1. madde (TCP + `transport = "tcp"`) korur.

Sunucunun anahtar portu: sunucunun ağının VLAN'ında **etiketsiz (untagged)**.

## 2. FortiGate (FortiOS 7.6)

```text
config log syslogd setting
    set status enable
    set server "<arcilogger>"
    set mode udp            # TCP 514 açıldıktan sonra "reliable" önerilir (bkz. 1.1)
    set port 514
    set facility local7
    set format default
    set source-ip <firewall>
end
config log syslogd filter
    set forward-traffic enable
    set local-traffic enable
    set multicast-traffic enable
    set sniffer-traffic enable
    set anomaly enable
    set voip enable
end
```

- İnternet politikalarındaki web filtre profili (örneklerde `WEB-PROFILE`): `set log-all-url
  enable`, `set extended-log enable`.
- DNS filtre profili (örneklerde `DNS-PROFILE`): `set log-all-domain enable`.
- İnternet politikalarında `set logtraffic all` kalmalı.
- İsteğe bağlı: `config log setting` → `set fwpolicy-implicit-log enable`,
  `set local-in-deny-unicast enable`.

## 3. MikroTik (RouterOS 7)

Ana (çekirdek) yönlendirici, yani DHCP sunucusu:

```text
/system logging action
add name=arcilogger target=remote remote=<arcilogger> remote-port=514 \
    remote-log-format=syslog syslog-time-format=iso8601 syslog-facility=daemon
# Doğrulayın: add-topics-string / konu metninin mesaja eklenmesi seçeneğinin adı
/system logging
add topics=dhcp action=arcilogger
add topics=firewall action=arcilogger
add topics=account action=arcilogger
add topics=critical action=arcilogger
add topics=error action=arcilogger
add topics=warning action=arcilogger
add topics=script action=arcilogger
add topics=system action=arcilogger        # yapılandırma değişiklikleri
```

Diğer anahtarlar ve erişim noktaları: aynı `arcilogger` eylemi; konular
`account, critical, error, warning, interface, system, script`.

**Kalp atışı** (her MikroTik'te; "log kaynağı sessiz" kuralı R15 için):

```text
/system scheduler
add name=arci-heartbeat interval=5m \
    on-event=":log info (\"ARCI-HEARTBEAT \" . [/system identity get name])"
```

**Saatlik `ARP-LOG` satırları** (statik IP'li cihazlar ve DHCP kiraları arasındaki boşluklar için
5651 kanıtı): ana yönlendiricide zamanlanmış bir betik her saat ARP tablosunun her satırı için
`ARP-LOG <ip> <mac> <arayüz>` biçiminde bir log satırı yazmalıdır (`:log info`, konu `script`;
biçim: [PARSERS.md](PARSERS.md)). Betik ürünle gelmez: sahada yazılıp ilk yakalamayla
**doğrulanmalıdır** (bölüm 4; geçersiz satırlar `mikrotik.arp` ayrıştırma hatası olarak sayılır).
Aralığı `arp.interval_seconds` ayarıyla (varsayılan 3600 saniye) aynı olmalıdır.

DHCP sunucusunun adı VLAN numarasını taşıyorsa (`dhcp<VLAN numarası>`, ör. `dhcp42`) ARCILOGGER
bir kiranın VLAN'ını bu addan bulur (`dhcp.server_vlan_regex`, varsayılan `^dhcp(\d+)$`; farklı
bir adlandırma için bu ayarı değiştirin); yoksa VLAN listesindeki alt ağlardan.

Erişim noktaları adreslerini DHCP'den alıyorsa ARCILOGGER'a tek tek değil **ağ** olarak tanıtılır
(`[[sources]]` içinde `address = "<ap-network>/24"` gibi bir ağ) ve her AP için ayrı dosya tutulur
(`<kaynak adı>-<ip>`, ör. `ap-<ip>`). Sabit adres (DHCP rezervasyonu) önerilir.

## 4. Log kaynakları ve test / Log sources and test

**Log kaynaklarını ekleyin.** Yeni bir kurulumda `arcilogger.toml` **hiçbir log kaynağı** içermez:
ilk kurulum, kuruluşta olmayan cihazları (belgelerin kurgusal örnek ağını) yapılandırmasın diye
örnek yapılandırmanın `[[sources]]` satırları yorum satırıdır. Bu geçerli bir durumdur, ama kaynak
eklenene kadar kurulumun güvenlik duvarı 514'ü hiçbir göndericiye açmaz ve hiçbir log kaydedilmez;
sessiz kaynak uyarısı (R15) hiçbir kaynağı izlemez, Panel'in "Log alımı" kutusu "İzlenen kaynak
yok" yazar. Kurulumun ön denetimi, sonundaki sonraki adımlar ve `arcilogger-core config-check` bunu
uyarı olarak yazar. Log gönderen her cihaz için, en az güvenlik duvarı ve ana yönlendirici (DHCP
sunucusu) için, bir `[[sources]]` kaydı ekleyin; `address` cihazın logları **gönderdiği** adrestir
(bulmak için aşağıya bakın):

```toml
[[sources]]
name = "fortigate"          # küçük harf, rakam ve "-": spool klasörünün adı
type = "fortigate"          # "fortigate" | "mikrotik" | "generic": ayrıştırıcıyı seçer
address = "<firewall>"
heartbeat_minutes = 10      # bu süre mesajsız kalırsa R15 uyarısı (varsayılan 15)

[[sources]]
name = "router"
type = "mikrotik"
address = "<router>"

[[sources]]
name = "ap"                 # adresini DHCP'den alan erişim noktaları: her biri "ap-<ip>" olur
type = "mikrotik"
address = "<ap-network>/24"
```

Örnek yapılandırmanın satırlarını kullanıyorsanız başlarındaki `#` işaretini silin (`# ` ile
başlayanlar açıklamadır) ve adları ve adresleri kendi cihazlarınızınkiyle değiştirin. Ardından
kurulumu yeniden çalıştırın ya da güvenlik duvarını yeniden uygulayıp hizmetleri yeniden başlatın
(bu bölümün sonundaki komutlar). Kaynak yokken `-SkipFirewall` ile kurulmuş bir sunucu her
göndericiyi `unknown-<ip>` olarak kaydeder ve ayrıştırıcısını içerikten seçer (aşağıda).
Kurulumdan sonra kaynaklar web arayüzünde **Ayarlar → Kaynaklar** sayfasından eklenir,
düzenlenir, kapatılır ya da silinir; toplayıcı, ingest ve güvenlik duvarı değişikliği
kendiliğinden izler, yeniden başlatma gerekmez ([AYARLAR.md](AYARLAR.md)).

Her kaynağın adresi kurulumun izin verdiği ağlardan birinde olmalıdır (`-SourceNetwork`,
varsayılan özel ağlar; en geniş ağ /16). Bu ağların
dışında bir kaynağı olan yapılandırmayla kurulum ve yükseltme, hiçbir şeyi değiştirmeden ön
denetimde durur ve verilecek `-SourceNetwork` değerini yazar (ör. başka bir yerleşkenin genel
adresi ya da CGNAT adresi). Sonradan elle eklenmiş böyle bir kaynağa `ApplySystem` 514'ü
açmaz; güvenlik duvarında zaten açık olanı açık bırakır. İkisinde de `R20-admin` uyarısı ve
`source_outside_policy` kodu görünür.

**Bir kaynağı kapatmak.** `[[sources]] enabled = false` (Ayarlar → Kaynaklar'daki anahtar) bir
kaynağı silmeden kapatır: güvenlik duvarı o göndericinin 514 iznini kaldırır, sessiz kaynak
uyarısı (R15) ve mühürlemenin boşluk denetimi onu artık beklemez. Kapalı kaynaktan yine de gelen
bir ileti kaydedilir (ham kayıt hiçbir zaman atılmaz). Kaynak lisansın kaynak sayısında sayılmaya
devam eder. Yalnızca `false` yazılır; anahtar yoksa kaynak açıktır:

```toml
[[sources]]
name = "eski-switch"
type = "generic"
address = "<switch>"
enabled = false             # kapalı: 514 izni yok, R15 beklemez
```

Her cihazdan bir test mesajı gönderin:

- FortiGate: `diagnose log test`
- MikroTik: `:log info "ARCI-TEST"`

Sunucuda (web arayüzündeki "Canlı Akış" ve "Kaynaklar" sayfaları bu sürümde yer tutucudur),
**her beklenen kaynağın** bugünkü dosyası olmalı ve `collector-status.json` içindeki `sources`
listesinde görünmelidir:

```powershell
Get-ChildItem D:\ARCILOGGER\spool\*\$(Get-Date -Format yyyy-MM-dd).log | Format-Table Directory, Length, LastWriteTime
Get-Content D:\ARCILOGGER\spool\fortigate\$(Get-Date -Format yyyy-MM-dd).log -Tail 5
Get-Content D:\ARCILOGGER\state\collector-status.json
```

**Yapılandırmada olmayan göndericiler.** Kurulumun Windows güvenlik duvarı
(`Set-ArciFirewall.ps1`) UDP/TCP 514'e **yalnızca** `arcilogger.toml` içindeki `[[sources]]`
adreslerinden izin verir. Başka bir adresten gelen mesajı Windows, ARCILOGGER görmeden düşürür:
`unknown-<ip>` klasörü oluşmaz, "yeni log kaynağı" uyarısı çıkmaz, `collector-status.json`
içinde sayılmaz. `D:\ARCILOGGER\spool\unknown-<ip>\` ve bu uyarı yalnızca güvenlik duvarının
geçirdiği göndericiler içindir (ör. `-SkipFirewall` ile kurulmuş bir sunucuda). Bir `[[sources]]`
ağının (ör. erişim noktalarının ağı) içindeki her adres o kaynağın `<ad>-<ip>` dosyasına yazılır.

Bir kaynağın dosyası yoksa cihaz yapılandırmadakinden başka bir adresten gönderiyordur. Bu
özellikle **ana yönlendirici** için önemlidir: DHCP ve ARP-LOG kayıtları, yani 5651 erişim
kayıtlarının özü, ondan gelir. Birden çok VLAN arayüzü olan bir yönlendirici logları hangi
arayüzünün adresinden gönderiyorsa `[[sources]]` içinde o adres olmalıdır. Gerçek adresi bulmak
için:

- Gönderen adresi yönlendiricide sabitleyin (**doğrulayın**: RouterOS 7'de özelliğin adı):
  `/system logging action set arcilogger src-address=<router>`
- ya da Windows güvenlik duvarının düşürülen paket günlüğünü kısa süreliğine açın, bir test mesajı
  gönderin, günlükte 514'e gelen adresi bulun ve günlüğü yeniden kapatın:

  ```powershell
  Set-NetFirewallProfile -All -LogBlocked True
  Select-String ' 514 ' "$env:windir\System32\LogFiles\Firewall\pfirewall.log" | Select-Object -Last 20
  Set-NetFirewallProfile -All -LogBlocked False
  ```

  (**Doğrulayın:** günlük dosyasının yolu `Get-NetFirewallProfile | Format-List Name, LogFileName`
  ile görünür.)

`[[sources]]` elle değiştirildiğinde toplayıcı ve ingest kaynakları 5 saniye içinde yükler,
`ApplySystem` görevi güvenlik duvarını en geç 5 dakika içinde eşitler. Tarayıcı yönetimi
kapalıysa (`-DisableBrowserAdministration`) güvenlik duvarını kendiniz uygulayın; `[collector]`
bölümü değiştiyse hizmetleri yeniden başlatın:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciFirewall.ps1"
Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker
```

**Sahte UDP göndericisine karşı.** UDP'de gönderen adresi yerel ağda taklit edilebilir; güvenlik
duvarının adres listesi bunu engellemez. Bir kaynağın hangi taşıma yoluyla (UDP/TCP) kabul
edileceği `[[sources]]` içindeki `transport` anahtarıyla sınırlanır (`"any"` varsayılan, `"udp"`,
`"tcp"`): izin verilmeyen yoldan gelen mesaj yazılmaz, sayılır ve bir kez uyarı üretir. TCP 514
açıldıktan sonra (bölüm 1, akış 2) FortiGate (`set mode reliable`) ve ana yönlendirici için TCP ve
`transport = "tcp"` önerilir (yönlendiricide TCP desteğini **doğrulayın**: RouterOS 7 uzak günlük
eylemi).

İlk gerçek MikroTik yakalamasında ayrıştırma hatası olmamalıdır (`& $ingest status`,
[ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5; `R20-parse` uyarısı; [PARSERS.md](PARSERS.md)):
ayrıştırıcılar depodaki kurgusal biçim örnekleri ve sentetik örneklerle sınanır, sizin cihazınızın
sürümü ve ayarları farklı olabilir.

## 5. FortiGate'ten geçmeyen ağlar (ör. misafir ağı)

Bazı ağlarda misafir Wi-Fi (ya da başka bir ağ) internete FortiGate'ten değil başka bir çıkıştan
çıkar: ayrı bir internet hattı ya da bir VPN tüneli. Bu bir yapılandırma seçeneğidir ve
ARCILOGGER'ın görebildiğini değiştirir:

- O ağın trafiği için **FortiGate kaydı yoktur**; ARCILOGGER yalnızca yönlendiricinin DHCP ve
  ARP-LOG kayıtlarını görür. 5651 sorgusu bu ağdaki bir cihazı genel IP ve port üzerinden bulamaz;
  kullanım raporlarında bu trafik görünmez.
- VLAN listesinde misafir ağı olarak işaretlenmiş (`vlans.guest`) bir VLAN'daki adres için 5651
  sonucu bunu açıklar (`guest_vlan`) ve misafirler aday listesine girmez
  ([CORRELATION.md](CORRELATION.md)).
- Misafir DHCP sunucusunun kira süresi kısa ise `lease.server_seconds` ayarına girin
  ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 6).
- Böyle bir ağın 5651 kapsamında nasıl ele alınacağı (ayrı kayıt, kimlik doğrulama) kuruluşun ve
  hukuk müşavirinin kararıdır.
