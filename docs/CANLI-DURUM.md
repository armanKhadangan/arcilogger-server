# Canlı Durum: internet hattı ve şu an kim ne kadar veri kullanıyor

> English: [LIVE-STATUS.md](LIVE-STATUS.md). Kullanım raporları: [KULLANIM.md](KULLANIM.md).
> Cihaz ayarları (SNMPv3): [DEVICE-SETUP.md](DEVICE-SETUP.md). Lisans: [LISANS.md](LISANS.md).
>
> ARCILOGGER **5651 yükümlülüklerini desteklemek için tasarlanmıştır**. Canlı Durum bir
> 5651 işlevi değildir: kişi düzeyindeki tablo bir kullanım analizi aracıdır. Çalışanların
> internet kullanımının canlı izlenmesi kişisel veri işlemedir; aydınlatma metninde anılması ve
> hukuki inceleme önerilir ([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)).

**Canlı Durum** sayfası (`/activity`, menüde Canlı Akış'ın altında) iki bölümden oluşur:

1. **İnternet hattı:** her WAN hattının şu anki indirme ve yükleme hızı (Mbps), son 60 dakikanın
   grafiği ve hattın kapasitesi biliniyorsa kullanılan yüzdesi. Değerler, cihazın arayüz
   sayaçlarından salt okunur SNMPv3 ile okunur (bölüm 3).
2. **Şu an:** son 1, 5 ya da 15 dakikada kimin ne kadar veri indirip yüklediği; kişi, cihaz ya da
   IP başına bir satır, en çok indirenden başlayarak, ve şirketin dakika dakika son 60 dakikası.
   Değerler FortiGate trafik kayıtlarından, Kullanım raporlarıyla aynı kurallarla hesaplanır
   (bölüm 2).

Sayfa açık ve görünür olduğu sürece 10 saniyede bir yenilenir; arka plandaki bir sekme sormaz. Her
bölümü yalnızca onu görmeye yetkili roller görür (bölüm 4).

## 1. Ne gösterir, ne göstermez?

- **Tahmindir.** Bayt sayıları cihazların trafik kayıtlarından gelir; Kullanım sayfasındaki gibi
  hiçbir değer kesin ölçüm değildir.
- **MikroTik bayt saymaz.** MikroTik kayıtları bayt sayısı taşımaz: MikroTik'ten geçen trafik
  "Şu an" tablosunda yer almaz. Sayfa bunu yazar. Bir MikroTik'in hattı SNMP açıksa yine de
  "İnternet hattı" bölümünde görünür.
- **Uzun bağlantılar gecikmeyle görünür.** FortiGate bir oturumun baytlarını oturum sürerken ara
  kayıtlarla (interim) ve kapanınca son kayıtla bildirir. Ara kayıtların ne sıklıkla geldiği
  cihaz ayarına bağlıdır ve **sahada doğrulanır** (X-B1). Bir ara kayıttaki fark kendi dakikasında
  sayılır; son kayıt yalnızca ara kayıtların saymadığını ekler. Sayfa en yeni trafik kaydının
  zamanını gösterir: "Uzun bağlantılar FortiGate ara kayıtları geldikçe görünür".
- **İnternet hattı bölümü kişisel veri taşımaz:** yalnızca hat adı, hız, durum ve zamanlar.

## 2. "Şu an" tablosu

| Sütun | Anlamı |
|---|---|
| Kişi / cihaz / IP | Kullanım raporlarındaki özne kuralları: cihaz bir kişiye atanmışsa kişi, değilse cihaz, o da bilinmiyorsa IP adresi |
| İndirme, yükleme | Pencere boyunca bayt ve ortalama hız (bayt × 8 / pencerenin saniyesi) |
| Oturumlar | Pencerede başlayan oturumlar |
| En çok siteler, uygulamalar | En çok 3'er; arka plan trafiği siteler arasında sayılmaz. Her dakikanın en büyük 5'inden toplanır, bu yüzden yaklaşıktır |
| Son görülme | Öznenin pencerede trafiği olan en yeni dakikasının sonu (en çok en yeni trafik kaydı) |

- **Pencere:** 1, 5 ya da 15 dakika (varsayılan 5). Başka bir değer 422 `activity_window_invalid`
  ile reddedilir.
- En çok **200 satır** gösterilir; fazlası olduğunda sayfa bunu söyler. Toplamlar ve dakika
  grafiği her zaman bütün trafiği kapsar.
- **Kullanım analizinden çıkarılan kişiler** (`usage_excluded`) satır olarak görünmez; trafikleri
  şirket toplamında sayılır.
- **Takma adlar:** `usage.pseudonymize_anonymous_role` açıkken `usage.person` yetkisi olmayan
  roller adlar yerine takma adlar görür, Kullanım sayfasındakiyle aynı kuralla.
- Hesap Kullanım raporlarının kendi kodudur: bir günün dakikaları toplanınca o günün kullanım
  özetiyle aynı baytlar çıkar. Mesai saatleri ve molalar ([KULLANIM.md](KULLANIM.md)) her dakikaya
  o günün kendi saatleriyle uygulanır.
- **Rakamlar nereden gelir:** worker son 60 dakikanın bir özetini (`state/activity.json`) tutar;
  yeni trafik kayıtlarını her 10 saniyede kendi iş parçacığında sayar (`activity.tick`,
  [CORRELATION.md](CORRELATION.md)); sayfa bu özeti okur. Özette özne anahtarları ve dakika başına
  baytlar vardır; ad, URL ya da kayıt satırı yoktur. Adlar ve takma adlar sayfa sorduğunda eklenir.
- **Worker başladıktan sonra** önce son bir saati yeniden okur; bitirene kadar ve özet 60
  saniyeden eskiyse (worker durmuşsa) sayfa rakamların eksik olduğunu söyler (`warming_up`).
- Veritabanına zamanından 5 dakikadan geç ulaşan bir trafik kaydı tabloya eklenmez; kullanım
  raporları onu sayar.

## 3. İnternet hattı (SNMPv3)

Her log kaynağının **Kaynaklar** sayfasında (Ayarlar → Kaynaklar → kaynak → SNMP) bir SNMP bölümü
vardır. Açıldığında ARCILOGGER cihazın WAN arayüzlerinin sayaçlarını kendisi okur:

| Ayar (`[sources.snmp]`) | Varsayılan | Sınırlar | Anlamı |
|---|---|---|---|
| `enabled` | kapalı | — | Bu kaynağın hattı okunsun mu |
| `port` | 161 | 1–65535 | Cihazın SNMP portu (UDP) |
| `user` | — | — | SNMPv3 kullanıcı adı (gizli değildir) |
| `auth` | `sha256` | `sha1`, `sha256`, `sha512` | Kimlik doğrulama protokolü |
| `priv` | `aes128` | `aes128`, `aes256` | Şifreleme protokolü (`aes256` **sahada doğrula**) |
| `interfaces` | — | 1–4 arayüz | WAN hatlarının arayüz adları (`ifName`), cihazın bildirdiği gibi yazılır; yanlış bir ad kaydederken reddedilir ve ret iletisi cihazın arayüzlerini listeler |
| `capacity_mbps` | — | isteğe bağlı | Arayüz başına indirme/yükleme kapasitesi (Mbps); yüzde göstermek için |
| `interval_seconds` | 15 | 10–60 | Okuma aralığı |

- **Kaydederken bağlantı denenir.** SNMP açılırken, değiştirilirken ya da anahtarları yazılırken
  ARCILOGGER, hiçbir şey kaydetmeden önce cihaza bağlanır ve her arayüzün adını, durumunu ve
  hızını okur. Başarısız olursa değişiklik uygulanmaz ve nedeni gösterilir (`snmp_timeout`,
  `snmp_auth_failed`, `snmp_no_such_interface`, `snmp_no_hc_counters`). `snmp_no_such_interface`
  iletisi cihazın bildirdiği arayüzleri (ad, durum, Mbit/s) listeler. Listeyi kaydetmeden önce
  getiren ayrı bir düğme sonraki bir sürümde gelecektir.
- **Yalnızca SNMPv3, kimlik doğrulamalı ve şifreli (authPriv).** SNMP v1/v2c kabul edilmez:
  topluluk adı (community) ağda açık metin gider.
- **Hedef kaynağın kendi adresidir.** Adresi bir ağ olan kaynakta SNMP açılamaz
  (`snmp_source_is_network`).
- **Anahtarlar gizli depodadır:** `snmp.<kaynak>.auth_key` ve `snmp.<kaynak>.priv_key` (8–64
  karakter). TOML dosyasına, loglara, uyarılara ve API yanıtlarına hiçbir zaman yazılmaz. Eksik
  bir anahtar `snmp_secret_missing` verir.
- **Yön:** WAN arayüzünde gelen (In) indirme, giden (Out) yüklemedir; her cihazda cihazın kendi
  arayüz grafiğiyle karşılaştırın (**sahada doğrula**, X-B2).
- **Yalnızca 64 bit sayaçlar** (`ifHCInOctets`, `ifHCOutOctets`). Bunları vermeyen cihaz
  `snmp_no_hc_counters` durumunu alır. Sayaç ya da cihazın çalışma süresi geri giderse (yeniden
  başlatma) o okuma yalnızca sonraki hızın başlangıcı olur.
- **Durumlar:** `ok`, `timeout`, `auth_failed`, `no_such_interface`, `snmp_no_hc_counters`,
  `disabled`.
- **Uyarı `R20-snmp`** (düşük): bir kaynaktan 10 dakika boyunca iyi bir okuma gelmezse açılır,
  sonraki iyi okumayla kendiliğinden kapanır ([UYARILAR.md](UYARILAR.md)).
- **Saklama:** veritabanı tablosu yoktur. Worker son 60 dakikayı tam çözünürlükte, son 24 saati
  1 dakikalık ortalamalarla `D:\ARCILOGGER\state\wan-rates.json` dosyasında tutar.
- **Ağ:** sunucu yalnızca SNMP'si açık kaynakların adresine ve portuna UDP gönderir; gelen bağlantı
  yoktur. Sıkı giden trafikte güvenlik duvarı kaynak başına bir `ARCILOGGER SNMP <kaynak>` kuralı
  açar ([KURULUM.md](KURULUM.md) bölüm 12). Bir kaynağın SNMP'si kapatılınca kuralı, öteki giden
  kurallar gibi, kurulumun güvenlik duvarı adımı yeniden çalışana kadar kalır.
- **Cihaz tarafı:** salt okunur bir SNMPv3 kullanıcısı, yalnızca ARCILOGGER sunucusunun adresine
  yanıt veren, bir LAN arayüzünde; **hiçbir zaman WAN arayüzünde**
  ([DEVICE-SETUP.md](DEVICE-SETUP.md), "SNMPv3").

## 4. Kim neyi görür?

| Bölüm | Yetki | Roller | KVKK bilgilendirmesi | Denetim kaydı | Lisans |
|---|---|---|---|---|---|
| İnternet hattı | `dashboard.view` ya da `system.read` | Yönetici, Denetçi, Rapor İzleyici | gerekmez (kişisel veri yok) | yazılmaz | 5651 Temel |
| Şu an | `usage.person` ya da `usage.anonymous` | Yönetici, Denetçi, İK Raporları, Rapor İzleyici | onaylanmış olmalı | `activity.view` (bölüm 5) | **Kullanım analizi** modülü |

Kullanım analizi modülü kapalıyken "Şu an" bölümü kilitlidir, internet hattı çalışmaya devam eder
([LISANS.md](LISANS.md)). Panel'deki **İnternet hattı** kartı, hiçbir kaynakta SNMP açık değilken
gizlidir; yöneticiler onun yerine Kaynaklar'a bir bağlantı görür.

## 5. Denetim

"Şu an" tablosunun görüntülenmesi denetim kaydına `activity.view` olarak yazılır (hedef
`activity:devices`; ayrıntı: pencere ve satır sayısı, **ad yok**). Sayfa 10 saniyede bir sorduğu
için kayıt bir kullanıcının ilk isteğinde, sonra kullanıcı sormaya devam ettikçe en çok 15 dakikada
bir yazılır.

## 6. Sahada doğrulanacaklar

| No | Ne | Nasıl |
|---|---|---|
| X-B1 | FortiGate'in ara trafik kayıtlarını ne sıklıkla gönderdiği (uzun oturumların tablodaki gecikmesi) | Uzun bir indirme sırasında satırın ne zaman güncellendiğine bakın |
| X-B2 | WAN arayüzünde In/Out'un indirme/yükleme olduğu | Cihazın kendi arayüz grafiğiyle karşılaştırın |
| X-B3 | Cihazın kabul ettiği SNMPv3 protokolleri (eski yazılımlar `sha1` isteyebilir ya da `aes256` sunmayabilir) | Kaydederken yapılan bağlantı denemesi (`snmp_auth_failed`) |
| X-B4 | Cihazın WAN hatları için bildirdiği arayüz adları (`ifName`) | Cihazın kendi arayüz listesi; yanlış bir adın ret iletisi de listeyi gösterir. Hiçbir ad varsayılmaz |
