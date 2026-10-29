# Cihaz ayarları / Device setup

> Ağ yöneticisi içindir. Komutlar build specification bölüm 15'ten alınmıştır ve **FortiOS 7.6**
> ile **RouterOS 7** için yazılmıştır; bölüm 2.1'deki saat adımının ve bölüm 2.4–2.5'in FortiGate
> komutları FortiOS 7.6 CLI başvuru belgesindendir. Her komutu uygulamadan önce cihazınızdaki
> sürümün belgeleriyle doğrulayın. Bu belgede yazanların uygulandığını varsaymıyoruz. Bölüm 2.4–2.6
> Türkçe metnin ardından İngilizce de verilir (English text follows the Turkish one).

ARCILOGGER hiçbir kuruluşun ağ planını taşımaz: aşağıdaki komutlarda `<…>` yerine kendi
adreslerinizi ve arayüz adlarınızı yazın.

| Yer tutucu | Anlamı |
|---|---|
| `<arcilogger>` | ARCILOGGER sunucusunun adresi (kurulumdaki `-ServerAddress`, `[collector] bind`) |
| `<firewall>` | FortiGate'in logları gönderdiği adres (`set source-ip`) |
| `<router>` | Ana (çekirdek) MikroTik yönlendiricinin (DHCP sunucusu MikroTik ise o) logları gönderdiği adres |
| `<admin-pc>` | Yönetici PC'sinin adresi ya da ağı (kurulumdaki `-AdminAddress`) |
| `<ntp>` | Yerel saat kaynağı (kurulumdaki `-NtpServer`, `[clock] ntp_server`; genellikle `<router>`) |
| `<arayüz>` | FortiGate arayüzünün adı (`config system interface` altındaki ad; kayıtlarda `interface`, `srcintf` ya da `dstintf`) |
| `<istemci-mac>` | Bölüm 2.4 ve 2.5'teki denetimde kullanılan deneme istemcisinin MAC adresi |

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

1. **TCP'ye geçin** (tablo satır 2; bkz. bölüm 2.1 `set mode reliable`, bölüm 3
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

### 2.1 Syslog ve saat

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

**Saat (NTP).** FortiGate'in saati her oturum ve DHCP kaydını tarihler; dakikalarca bir kayma
kesin sorguları kaydırır (gerçek oturum bulunmaz, aynı portu sonra kullanan başka bir oturum
bulunabilir). FortiGate'in saatini NTP ile eşitleyin: yerel saat kaynağı için `set type custom` ve
`<ntp>`, FortiGuard'ın NTP sunucuları için yalnızca `set type fortiguard`:

```text
config system ntp
    set ntpsync enable
    set type custom         # ya da: set type fortiguard (o zaman ntpserver gerekmez)
    config ntpserver
        edit 1
            set server "<ntp>"
        next
    end
end
```

`diagnose sys ntp status` NTP sunucularının bilgisini listeler (**doğrulayın**, sonucu
[KURULUM.md](KURULUM.md) bölüm 16'ya yazın). Bir kaynağın saati 120 saniyeden fazla kayarsa
`R20-lease:source-clock` uyarısı açılır ve o anların 5651 zincirleri düşük güvenle gösterilir.

### 2.2 Web ve DNS filtre profilleri

- İnternet politikalarındaki web filtre profili (örneklerde `WEB-PROFILE`): `set log-all-url
  enable`, `set extended-log enable`.
- DNS filtre profili (örneklerde `DNS-PROFILE`): `set log-all-domain enable`.

### 2.3 Politikalar ve isteğe bağlı kayıtlar

- İnternet politikalarında `set logtraffic all` kalmalı.
- İsteğe bağlı: `config log setting` → `set fwpolicy-implicit-log enable`,
  `set local-in-deny-unicast enable`.

### 2.4 FortiGate DHCP sunucusu kayıtları (yalnız-FortiGate ağlar) / FortiGate DHCP server records (FortiGate-only networks)

Yalnızca DHCP'yi FortiGate'in dağıttığı ağlar içindir ([KURULUM.md](KURULUM.md) bölüm 0.1).
FortiGate DHCP sunucusunun olay kayıtları (DHCP Ack, DHCP Release) kira zaman çizelgesine girer.
Kayıt biçimi FortiOS belgelerinden ve tek bir topluluk örneğinden alınmıştır (varsayılan logid'ler,
`dhcp.fortigate_logids`: DHCP Ack `0100026001`; DHCP Release `0100026002`, belgedeki numaralama
kuralından türetildi); bu yüzden ilk kurulumda aşağıdaki adımlarla sahada doğrulanır ve
doğrulanana kadar raporlar bunu belirtir. Yalnızca yapılandırmada FortiGate olarak tanımlı
kaynağın (`type = "fortigate"`, `address = "<firewall>"`; bölüm 4) kayıtları kullanılır.
FortiGate'in DHCP verdiği her ağı, VLAN numarası olmasa da, Cihazlar ve Kişiler sayfasında girin;
her DHCP kapsamını tam olarak bir ağ olarak girin ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 5):
tanımlı bir ağda olmayan adreslerin zincirleri düşük güvenle gösterilir.

1. **Doğrulayın:** arayüz bir DHCP aktarıcısı (relay) değildir ve ona bir DHCP sunucusu hizmet
   verir:

   ```text
   config system interface
       edit <arayüz>
           get             # dhcp-relay-service disable olmalı
   end
   config system dhcp server
       show                # set interface <arayüz> ve set status enable içeren bir kayıt olmalı
   end
   ```

   FortiGate'in DHCP aktarıcı olarak çalıştığı ağlarda FortiGate kira kaydı desteklenmez: kira
   kanıtı asıl DHCP sunucusundan gelmelidir.
2. IPsec üzerinden DHCP veren sunucuların (`server-type ipsec`; DHCP over IPsec, VPN havuzları)
   arayüzlerini Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi bölümündeki
   `dhcp.fortigate_interfaces_excluded` ayarına ekleyin: bu kiralar tünel kapandıktan
   `ipsec-lease-hold` saniye sonra biter, kayıttaki kira süresi bu bitişi göstermez.
3. Olay kayıtları açık olmalıdır (varsayılanlar):

   ```text
   config log eventfilter
       set event enable
       set system enable
   end
   ```

4. Syslog filtresi bilgi düzeyindeki kayıtları göndermelidir (varsayılan); olay kayıtlarını
   dışarıda bırakan serbest biçimli (free-style) bir filtre olmamalıdır:

   ```text
   config log syslogd filter
       set severity information
   end
   ```

5. **Denetim, bir deneme istemcisiyle.** İstemcide `ipconfig /release`, ardından
   `ipconfig /renew` çalıştırın (ya da istemcinin bağlantısını kesip yeniden bağlayın) ve saati
   not edin. ARCILOGGER şu ikisini birlikte gösteriyorsa denetim **geçer**:
   - a) **Kaynaklar** sayfasındaki **Kira kanıtı** kutusu bir FortiGate kirası gösterir: durum
     `ok`, FortiGate DHCP güncel kira sayısı 0'dan büyük, kullanılmayan kayıt 0 (hariç tutulan
     arayüzlerin kayıtları bu sayılara girmez). Sayı geçerli ayarlara hemen uyar; yalnızca alma
     zamanı ya da kullanılamayan MAC adresi nedeniyle kullanılmayan kayıtlar 24 saatlik
     pencereden çıkana kadar sayılır.
   - b) **5651 Sorgu** sayfasında [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 8'deki gibi girilen bir
     deneme sorgusu (makam "DENEME", gerekçe "FortiGate DHCP sahada doğrulama"), İç ağ → İç IP
     alanında istemcinin IP adresi ve not edilen saatle, istemcinin MAC adresini ve cihazını
     verir ("kayıt biçimi sahada doğrulanmadı" notuyla).

   İkisi birlikte biçimi uçtan uca kanıtlar. Canlı Akış ve Arama bir FortiGate DHCP kaydını ve
   sıradan bir FortiGate olayını aynı türde (`type:event`) gösterir, ikisini ayıramaz; bu yüzden
   denetimin parçası değildir. Ham satırlar Arama'da `type:event mac:<istemci-mac>` ya da
   `logid:<logid>` ile açılır. Dallar:
   - Bırakma (release) satırı görünmüyor (Arama'da `type:event mac:<istemci-mac>`,
     `ipconfig /release` saatinde): istemcilerin bırakmaları kaydedilmiyor; kiralar süreleri
     dolunca biter, bu kabul edilebilir. Sonucu [KURULUM.md](KURULUM.md) bölüm 16'ya yazın ve Ack
     biçimiyle devam edin.
   - Bırakma satırı başka bir logid ile görünüyor: o logid'i `deassigned` anlamıyla
     `dhcp.fortigate_logids` ayarına ekleyin ve o günün kiralarını yeniden oluşturun (Ayarlar →
     Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden oluştur); sonucu
     bölüm 16'ya yazın.
   - Kutu FortiGate kirası göstermiyor ya da kullanılmayan kayıt gösteriyor: ham satırı Arama'da
     açın (`logid:<logid>`), logid'ini `dhcp.fortigate_logids`, `dhcp_msg` değerini
     `dhcp.fortigate_dhcp_msgs` ile karşılaştırın, `R20-lease:fortigate-unconverted` ve
     `R20-lease:fortigate-refused` uyarılarına bakın, ayarı düzeltin, kiraları uyarının
     adlandırdığı günden (`first_day`) bugüne yeniden oluşturun (Kiraları yeniden oluştur, bir
     istekte en çok 31 gün) ve denetimi yineleyin.

   **Doğrulayın** ayrıca, istemci bağlı kalırken bir yenilemenin ikinci bir Ack satırı verdiğini
   (kira süresinin yarısını bekleyin ya da bir deneme kapsamında kısa bir `lease-time`
   ayarlayın) ve sonucu bölüm 16'ya yazın:
   - İkinci bir Ack görünüyor: yenilemeler kaydediliyor; başka bir şey gerekmez.
   - İkinci bir Ack görünmüyor: yenilemeler kaydedilmiyor. O zaman sürekli açık her istemcinin
     zinciri kirası bitince biter (`no_lease`) ve kiranın yarısından sonra düşük güvenle
     gösterilir. Kurum (hukuk müşaviriyle) iki yol arasında seçer: bunu olduğu gibi bırakmak
     (hiçbir zaman yanlış kişiyi göstermez) ya da `dhcp.fortigate_traffic_extends` ayarını
     açmak; ayarın yardım metni riski söyler: trafik o zaman kirayı açık tutar ve aynı adresi
     kullanan başka bir cihaz (havuzdaki statik bir cihaz) eski kira sahibi olarak gösterilir.
     Seçimi ve tarihini bölüm 16'ya yazın.
6. Denetim geçince `dhcp.fortigate_format_verified` ayarını açın (raporlar artık kimin, ne zaman
   işaretlediğini yazar). Adım 5–6'yı **her FortiOS yükseltmesinden sonra** yineleyin (alan
   listesi sürümler arasında değişir): ayarı kapatın, denetleyin, yeniden açın. Bir satırın kurum
   dışında paylaşılması gerekirse (ör. destek için) önce MAC adresini, ana bilgisayar adını
   (`hostname`), IP adresini, `devid` ve `devname` alanlarını maskeleyin; satırı hiçbir zaman
   maskesiz göndermeyin.
7. DHCP sunucusunun `lease-time` değeri (varsayılan 604800 saniye, 7 gün; `0` süresizdir ve
   yüksek önemli `R20-lease:fortigate-lease-unlimited` uyarısını verir) her kiranın süresi olarak
   kullanılır (`dhcp.fortigate_use_logged_lease`). Trafik FortiGate kiralarını,
   `dhcp.fortigate_traffic_extends` açılmadıkça uzatmaz.
8. Statik cihazları DHCP aralığının dışında tutun (ya da adreslerini FortiGate'te ayırın):
   havuzun içindeki statik bir cihaz istemcilerin adresi reddetmesine yol açar
   (`R20-lease:fortigate-decline`) ve bir adrese iki cihaz koyabilir.
9. **Doğrulayın**, FortiGate yönlendiricilerin arkasındaki ağlara DHCP aktarımıyla hizmet
   veriyorsa (bir arayüzde birden çok DHCP sunucusu): aktarılan ağdaki bir istemcinin DHCP Ack
   satırı bir `interface` ve kendi ağından bir adres taşır; ARCILOGGER bir istemci için arayüz
   başına değil ağ başına bir adres tutar. Sonucu bölüm 16'ya yazın.

**Ayar değişikliğinden sonra.** `dhcp.fortigate_*` ayarlarından birini
(`dhcp.fortigate_format_verified` dışında: o yalnızca sorgu ve rapor metnini değiştirir) ya da
bir FortiGate kaynağını (Ayarlar → Kaynaklar) değiştirdikten sonra kiraları etkilenen ilk günden
bugüne Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → Kiraları yeniden
oluştur bölümünde yeniden oluşturun (bir istekte en çok 31 gün): değişiklik geçmiş günlere ancak
yeniden oluşturmadan sonra uygulanır. O zamana kadar o günlerin anları için 5651 sorgusu zincir
kurmadan yanıt verebilir (güvenli taraf: yanlış kişi gösterilmez).

**English.** For networks whose DHCP server is the FortiGate only ([INSTALL.md](INSTALL.md)
section 0.1). The FortiGate DHCP server's event records (DHCP Ack, DHCP Release) feed the lease
timeline. Their format comes from the FortiOS documentation and one community sample (default
logids, `dhcp.fortigate_logids`: DHCP Ack `0100026001`; DHCP Release `0100026002`, derived from
the documented numbering rule); it is therefore verified on site at the first installation with
the steps below, and until then reports say so. Only records of the source configured as a
FortiGate (`type = "fortigate"`, `address = "<firewall>"`; section 4) are used. Enter every
network the FortiGate serves by DHCP on the Cihazlar ve Kişiler (Devices and people) page, even
without a VLAN number, and each DHCP scope as exactly one network
([FIRST-STEPS.md](FIRST-STEPS.md) section 5): chains of addresses outside the entered networks
are shown with low confidence.

1. **Verify on site** that the interface is not a DHCP relay and that a DHCP server serves it:
   `config system interface` → `edit <arayüz>` → `get` shows `dhcp-relay-service disable`;
   `config system dhcp server` → `show` has an entry with `set interface <arayüz>` and
   `set status enable` (commands above). FortiGate leases are not supported where the FortiGate
   is a DHCP relay: lease evidence must come from the actual DHCP server.
2. Add the interfaces of DHCP-over-IPsec servers (`server-type ipsec`, VPN pools) to
   `dhcp.fortigate_interfaces_excluded` in Settings → Application settings → 5651 lookup and
   lease timeline: those leases end `ipsec-lease-hold` seconds after the tunnel goes down, and
   the lease time in the record does not show that end.
3. Event records must be on (the defaults): `config log eventfilter` → `set event enable`,
   `set system enable`.
4. The syslog filter must send records of level information (the default): `config log syslogd
   filter` → `set severity information`; no free-style filter may exclude event records.
5. **The check, with one test client.** On the client run `ipconfig /release`, then
   `ipconfig /renew` (or disconnect and reconnect the client), and note the time. The check
   **passes** when ARCILOGGER shows both of these:
   - a) the **Kira kanıtı** (Lease evidence) box on the **Sources** page shows a FortiGate lease:
     state `ok`, FortiGate DHCP current leases above 0, refused records 0 (records of excluded
     interfaces are not in these counts). The count follows the current settings at once; only
     records refused for the receive time or an unusable MAC address stay counted until they
     leave the 24-hour window.
   - b) a trial lookup on the **5651 lookup** page, entered as [FIRST-STEPS.md](FIRST-STEPS.md)
     section 8 describes (authority "DENEME", reason "FortiGate DHCP sahada doğrulama"), with
     the client's IP address in Internal network → Internal IP and the noted time, gives the
     client's MAC address and device (with the note "record format not verified on site").

   Together they prove the format end to end. Live tail and Search show a FortiGate DHCP record
   and a plain FortiGate event under the same kind (`type:event`), so they cannot tell the two
   apart and are not part of the check. Raw lines are opened in Search with
   `type:event mac:<istemci-mac>` or `logid:<logid>`. Branches:
   - no release line appears (Search `type:event mac:<istemci-mac>` at the time of
     `ipconfig /release`): clients' releases are not logged; leases then end when their time
     runs out, which is acceptable. Record it in [INSTALL.md](INSTALL.md) section 16 and go on
     with the Ack format;
   - a release line appears under another logid: add that logid with the meaning `deassigned`
     to `dhcp.fortigate_logids` and rebuild the leases of the day (Settings → Application
     settings → 5651 lookup and lease timeline → Rebuild the leases); record it in section 16;
   - the box shows no FortiGate lease, or refused records: open the raw line in Search
     (`logid:<logid>`), compare its logid with `dhcp.fortigate_logids` and its `dhcp_msg` with
     `dhcp.fortigate_dhcp_msgs`, look at the alerts `R20-lease:fortigate-unconverted` and
     `R20-lease:fortigate-refused`, correct the setting, rebuild the leases from the day the
     alert names (`first_day`) to today (Rebuild the leases, at most 31 days per request), and
     repeat the check.

   **Verify on site** as well that a renewal while the client stays connected gives a second
   Ack line (wait for half the lease time, or set a short `lease-time` on a test scope), and
   record the outcome in section 16:
   - a second Ack appears: renewals are logged; nothing else to do;
   - no second Ack appears: renewals are not logged. Then every always-on client's chain ends at
     its lease end (`no_lease`) and is shown with low confidence from half the lease on. The
     organisation (with its legal counsel) chooses between keeping that, which never names a
     wrong person, and switching on `dhcp.fortigate_traffic_extends`, whose help text states the
     risk: traffic then keeps a lease open, and another device using the same address (a static
     device in the pool) is named as the former holder. Record the choice and its date in
     section 16.
6. When the check passes, switch on `dhcp.fortigate_format_verified` (reports then name who
   marked it and when). Repeat steps 5–6 after **every FortiOS upgrade** (the field list changes
   between releases): switch the setting off, check, switch it on again. If a line has to be
   shared outside the organisation (e.g. for support), mask the MAC address, the host name
   (`hostname`), the IP address, `devid` and `devname` first; never send it unmasked.
7. The DHCP server's `lease-time` (default 604800 seconds, 7 days; `0` is unlimited and gives the
   high alert `R20-lease:fortigate-lease-unlimited`) is used as each lease's time
   (`dhcp.fortigate_use_logged_lease`). Traffic does not extend FortiGate leases unless
   `dhcp.fortigate_traffic_extends` is switched on.
8. Keep static devices out of the DHCP range (or reserve their addresses on the FortiGate): a
   static device inside the pool makes clients decline the address
   (`R20-lease:fortigate-decline`) and can put two devices on one address.
9. **Verify on site**, if the FortiGate serves networks behind routers through DHCP relay
   (several DHCP servers on one interface), that the DHCP Ack line of a client in a relayed
   network carries an `interface` and an address of its own network; ARCILOGGER keeps one
   address per client and network, not per interface. Record it in section 16.

**After a settings change.** After changing any `dhcp.fortigate_*` setting (except
`dhcp.fortigate_format_verified`, which only changes the lookup and report texts) or a FortiGate
source (Settings → Sources), rebuild the leases from the first affected day to today in
Settings → Application settings → 5651 lookup and lease timeline → Rebuild the leases (at most 31
days per request): the change applies to past days only after the rebuild. Until then a 5651
lookup for moments of those days may answer without a chain (the safe side: no wrong person is
named).

### 2.5 srcmac ve aygıt tanıma (isteğe bağlı; ağ geçidi FortiGate olan ağlar) / srcmac and device identification (optional; networks whose gateway is the FortiGate)

Statik IP'li cihazların kira kaydı yoktur. FortiGate'in VLAN'ın ağ geçidi olduğu arayüzlerde
FortiGate oturum kaydının `srcmac` alanı, kirası olmayan bir oturumda MAC adresini, cihazı ve
kişiyi **kısmi kanıt** olarak, düşük güvenle verebilir; ARCILOGGER bu alandan hiçbir zaman kira
oluşturmaz. Bunun için arayüzde aygıt tanıma açılır:

```text
config system interface
    edit <arayüz>
        set device-identification enable
    next
end
```

- Yalnızca `lookup.srcmac_interfaces` ayarında listelenecek arayüzlerde açın: FortiGate'in
  VLAN'ın ağ geçidi olduğu arayüzler; yönlendirilmiş (L3) bir ağda hiçbir zaman (orada srcmac
  herkes için yönlendiricinin ya da L3 anahtarın MAC adresidir). Arada katman 2 anahtarlar varsa:
  Fortinet'e göre "if there is a switch in between, the user may see the MAC address entry of the
  switch", bir Fortinet çalışanının yanıtına göre de aygıt tanıma "when the endpoints have
  layer-2 connectivity up to the FortiGate" düzgün çalışır; böyle bir arayüz ancak aşağıdaki iki
  istemcili denetimden sonra listelenir.
- **Ne ekler:** o arayüzün her oturum kaydı istemcinin MAC adresini (`srcmac`, `mastersrcmac`,
  `srcmacvendor`) ve FortiGate algıladıkça cihaz adını (`srcname`), işletim sistemini (`osname`,
  `srcfamily`, `srcswversion`), donanım üreticisini ve sürümünü (`srchwvendor`,
  `srchwversion`), cihaz türünü (`devtype`) ve pasif olarak öğrenilen kullanıcı adlarını
  (`unauthuser`, `unauthusersource`) taşır; cihazınızın hangilerini doldurduğunu **doğrulayın**.
  ARCILOGGER bunları oturum kaydıyla birlikte, değiştirmeden, saklama süresi boyunca saklar.
- **Nerede görünür:** ham satırlarda ve Arama'da (`search.read` yetkisi); listelenen
  arayüzlerde 5651 sonuçlarında kısmi kanıt olarak; ve `lookup.srcmac_interfaces` ne derse
  desin, o arayüzün bir oturumunu kanıt olarak gösteren **her 5651 raporunun ve kanıt paketinin
  ham satır kanıtında**, çift NAT raporundaki başka kişilerin aday oturumları da dahil.
- Açmak, misafir ağlarında olduğu gibi (bölüm 5), kurumun hukuk müşaviriyle vereceği bir
  karardır; aydınlatma metni taslağında
  ([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)) karşılık gelen satırlar
  vardır. **Doğrulayın:** açtıktan sonra cihazın yükünü kendi izleme ekranlarında denetleyin.
- **Doğrulayın**, bir arayüzü `lookup.srcmac_interfaces` ayarına eklemeden önce: arayüzde iki
  bilinen istemciyle, Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi →
  İnternet çıkışları (NAT) altındaki "Kayıtlarda görülen NAT adresleri" panelinin srcmac tablosu
  (son bir saat) o arayüz için `max_ips_per_mac` 1 gösterir (IPv4 kaynaklar; çift yığınlı bir
  istemcinin IPv6 adresleri sayılmaz) ve Arama'da `srcmac:<istemci-mac>` iki istemcinin her
  birinin kendi MAC adresiyle (bir anahtarın ya da yönlendiricinin değil) kendi oturumlarını
  bulur. Arayüz panelin "srcmac için ekle" düğmesiyle ya da ayarın kendisinden eklenir.
- Yönlendirilmiş ağlar hiçbir zaman listelenmez (listedeki bir arayüzde paylaşılan bir MAC adresi
  `R20-lease:srcmac-shared` uyarısını verir).

**English.** Static-IP devices have no lease. On interfaces where the FortiGate is the VLAN's
gateway, the `srcmac` field of the FortiGate session record can give the MAC address, the device
and the person of a session without a lease as **partial evidence**, with low confidence;
ARCILOGGER never creates a lease from it. For this, device identification is switched on on the
interface (`config system interface` → `edit <arayüz>` → `set device-identification enable`,
commands above).

- Switch it on only on interfaces that will be listed in `lookup.srcmac_interfaces`: interfaces
  where the FortiGate is the VLAN's gateway; never on a routed (L3) segment (there srcmac is the
  router's or L3 switch's MAC address for everyone). With layer-2 switches in between: Fortinet
  says "if there is a switch in between, the user may see the MAC address entry of the switch",
  and a Fortinet staff answer says device identification works properly "when the endpoints have
  layer-2 connectivity up to the FortiGate"; such an interface is listed only after the
  two-client check below.
- **What it adds:** every session record of that interface then carries the client's MAC address
  (`srcmac`, `mastersrcmac`, `srcmacvendor`) and, as the FortiGate detects them, the device name
  (`srcname`), operating system (`osname`, `srcfamily`, `srcswversion`), hardware vendor and
  version (`srchwvendor`, `srchwversion`), device type (`devtype`) and passively learned user
  names (`unauthuser`, `unauthusersource`); **verify on site** which of them your unit fills.
  ARCILOGGER keeps them unchanged with the session record for the whole retention period.
- **Where it appears:** in raw lines and Search (`search.read` permission); for listed interfaces
  in 5651 results as partial evidence; and, whatever `lookup.srcmac_interfaces` says, in the
  **raw-line evidence of every 5651 report and evidence package** that cites a session of that
  interface, the candidate sessions of other people in a double-NAT report included.
- Switching it on is the organisation's decision, with its legal counsel, as for guest networks
  (section 5); the privacy notice draft
  ([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)) has the matching rows.
  **Verify on site:** after switching it on, check the unit's load in its own monitoring.
- **Verify on site** before listing an interface in `lookup.srcmac_interfaces`: with two known
  clients on it, the srcmac table (last hour) of the "Kayıtlarda görülen NAT adresleri" (NAT
  addresses seen in the logs) panel under Settings → Application settings → 5651 lookup and lease
  timeline → Internet exits (NAT) shows `max_ips_per_mac` 1 for that interface (IPv4 sources; a
  dual-stack client's IPv6 addresses are not counted), and `srcmac:<istemci-mac>` in Search finds
  each client's own sessions with its own MAC address (not a switch's or a router's). The
  interface is added with the panel's "Use for srcmac" button or in the setting itself.
- Routed segments are never listed (a shared MAC address on a listed interface raises the alert
  `R20-lease:srcmac-shared`).

### 2.6 Birden çok internet hattı / Several internet lines

Her internet hattı Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet
çıkışları (NAT) bölümünde bir çıkış olarak tanımlanır ([KURULUM.md](KURULUM.md) bölüm 0.1).

- Her hattın `transip` (FortiGate'in o hattaki kaynak NAT adresi) ve `dstintf` (çıkış arayüzü)
  değerlerini aynı bölümdeki "Kayıtlarda görülen NAT adresleri" tablosunda ya da Arama'da
  `natip:` ve `interface:` ile okuyun.
- Her hattın `dstintf` adlarını çıkışın arayüzleri olarak girin: **iki ya da daha çok hatta
  zorunludur** (girilmezse sorgu bir oturumun hangi hattan çıktığını ayıramaz ve düzenleyici
  uyarır). Servis sağlayıcı yönlendiricisinin arkasındaki bir FortiGate adresi (RFC 1918 ya da
  100.64.x.x) bir servis sağlayıcı NAT'ı çıkışının NAT adresidir, hiçbir zaman genel adres
  değildir.
- Her hat için servis sağlayıcıya genel IP adresinin sabit olup olmadığını sorun; adresi
  değişebilen bir hattı "Genel IP değişebilir (dinamik)" (`public_dynamic`, servis sağlayıcı
  NAT'ı) olarak işaretleyin. Adresi değişen doğrudan bir hattı ARCILOGGER kayıtlardan bulur.
- Çıkışları girin.
- Port korumayı her hat için yalnızca bir ölçümle girin, yoksa "Bilinmiyor" (`unknown`) kalsın:
  bir istemciden o hat üzerinden kurumun yönettiği bir sunucuya bağlantı açın (ör. kendi web
  sunucusuna `curl`), sunucunun kaydettiği kaynak portu Arama'daki oturumun `transport`
  değeriyle (`natip:<transip>`) karşılaştırın; birkaç denemede aynıysa "Korunuyor (sahada
  doğrulandı)" (`preserved`).
- FortiGate'ten geçmeyen misafir hatlarını "FortiGate'ten geçmiyor" (`not_logged`) olarak
  işaretleyin (bölüm 5).
- FortiGate'in DMZ'ye, başka bir VLAN'a, VPN'e ya da şubeye yaptığı kaynak NAT'ın adreslerini
  (NAT'ı açık politikalar; tabloda o DMZ'nin, VLAN'ın ya da tünelin `dstintf` değeriyle
  görünürler) "İnternet çıkışı değil" (`internal`) türüyle girin: o zamana kadar tanımsız bir hat
  sayılırlar (`R20-nat:unmapped-transip`, `egress_unmapped_seen`) ve sahipsiz adaylar olarak
  aranırlar.

**English.** Each internet line is defined as an exit in Settings → Application settings → 5651
lookup and lease timeline → Internet exits (NAT) ([INSTALL.md](INSTALL.md) section 0.1).

- Read each line's `transip` (the FortiGate's source-NAT address on that line) and `dstintf` (the
  outgoing interface) in the "Kayıtlarda görülen NAT adresleri" (NAT addresses seen in the logs)
  table of the same section, or in Search with `natip:` and `interface:`.
- Enter each line's `dstintf` names as the exit's interfaces: **required with two or more lines**
  (without them a lookup cannot tell by which line a session left, and the editor warns). A
  FortiGate address behind an ISP router (RFC 1918 or 100.64.x.x) is the NAT address of an ISP
  NAT exit, never a public address.
- Ask the ISP for each line whether its public IP address is static; mark a line whose address may
  change as "Public IP may change (dynamic)" (`public_dynamic`, ISP NAT). ARCILOGGER finds a
  direct line with a changing address from the logs.
- Enter the exits.
- Enter port preservation per line only by a measurement, otherwise leave it "Unknown"
  (`unknown`): from a client, open a connection over that line to a server the organisation
  controls (e.g. `curl` to its own web server) and compare the source port that server logs with
  the session's `transport` in Search (`natip:<transip>`); equal on several tries → "Kept
  (verified on site)" (`preserved`).
- Mark guest lines that bypass the FortiGate as "Does not pass the FortiGate" (`not_logged`;
  section 5).
- Enter the FortiGate's source-NAT addresses towards a DMZ, another VLAN, a VPN or a branch
  (policies with NAT switched on; they appear in the table with the `dstintf` of that DMZ, VLAN or
  tunnel) as exits of the type "Not an internet exit" (`internal`): until then they count as an
  unconfigured line (`R20-nat:unmapped-transip`, `egress_unmapped_seen`) and as unclaimed
  candidates.

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

**Test mesajı ve Canlı Akış.** Web arayüzünde **Canlı Akış** sayfasını açın, filtrede cihazın
kaynağını seçin (yapılandırmada olmayan göndericiler için "Bilinmeyen göndericiler" çipini; bu
göndericiler yalnızca güvenlik duvarı onları geçiriyorsa gelir, aşağıya bakın) ya da sorgu
kutusuna `sender:<cihaz-ip>` yazın ve **Başlat**'a basın. Sonra her cihazdan bir test mesajı
gönderin:

- FortiGate: `diagnose log test`
- MikroTik: `:log info "ARCI-TEST"`

Satır birkaç saniye içinde listede görünür. Canlı Akış satırları veritabanından değil ham kayıt
klasöründen (spool) okur: ingest geride kalsa ya da dursa da toplayıcının yazdığı her satır
görünür ve satıra tıklayınca ham kaydı (bayt bayt, SHA-256 ile) açılır. **Kaynaklar** sayfası
cihazın satırını "Etkin" gösterir (son mesaj heartbeat süresi içinde). Hiçbir satır gelmiyorsa
cihaz başka bir adresten gönderiyordur ya da güvenlik duvarı göndericiyi geçirmiyordur (aşağıda).

Web arayüzüne ulaşılamıyorsa aynı denetim sunucuda yapılır: **her beklenen kaynağın** bugünkü
dosyası olmalı ve `collector-status.json` içindeki `sources` listesinde görünmelidir:

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
kayıtlarının özü, ondan gelir (DHCP'yi FortiGate dağıtıyorsa güvenlik duvarı için de:
bölüm 2.4). Birden çok VLAN arayüzü olan bir yönlendirici logları hangi arayüzünün adresinden
gönderiyorsa `[[sources]]` içinde o adres olmalıdır. Gerçek adresi bulmak için:

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
açıldıktan sonra (bölüm 1, akış 2) FortiGate (`set mode reliable`, bölüm 2.1) ve ana yönlendirici
için TCP ve `transport = "tcp"` önerilir (yönlendiricide TCP desteğini **doğrulayın**: RouterOS 7
uzak günlük eylemi).

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
