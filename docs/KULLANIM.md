# Kullanım raporları — yöntem, ayarlar, yeniden hesaplama

> English: [USAGE.md](USAGE.md). Karar kaydı: [adr/0011-usage-analytics.md](adr/0011-usage-analytics.md).
> API sözleşmesi: [MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 12.
>
> **Lisans:** kullanım analizi **Kullanım analizi** modülünü gerektirir ([LISANS.md](LISANS.md)); modül kapalıyken Kullanım sayfası, paneldeki kullanım bölümleri, `/usage` uç noktaları ve `usage rebuild` komutu kilitlidir, 5651 işlevleri etkilenmez.

ARCILOGGER kişi, cihaz, departman ve şirket için günlük internet kullanımını **bağlantı
kayıtlarından tahmin eder**: çevrimiçi süre, aktif kullanım süresi, site ve kategori başına süre,
indirilen/yüklenen veri, oturumlar, engellenen denemeler, ilk/son etkinlik, saatlik ısı haritası,
uygulamalar ve mesai dışı kullanım. **Bu sayıların hiçbiri kesin değildir**; arayüz, raporlar ve
Excel dosyaları her süreyi "tahmini" olarak işaretler: *"Tahmini değer: bağlantı kayıtlarından
hesaplanır; arka planda açık sekmeler de sayılabilir."*

Varsayılan olarak (sahibin 2026-10-20 kararı) raporlar ayrıntılı ve **adlı**dır
([ASSUMPTIONS.md](ASSUMPTIONS.md), "Owner 2026-10-20"); bir kuruluşun çalışanlarının kullanımını
bu biçimde işleyip işleyemeyeceği (onay, aydınlatma metni) o kuruluşun hukuki sorusudur. Takma
adlar ve kişilerin çıkarılması birer ayardır (bölüm 6). ARCILOGGER 5651 yükümlülüklerini
desteklemek için tasarlanmıştır; kişisel veri işleme kuralları için hukuk müşavirine danışın
([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)).

## 1. Ölçümler

| Ölçüm | Tanım |
|---|---|
| **Çevrimiçi süre** | Kişinin/cihazın en az bir bağlantısının açık olduğu 1 dakikalık dilimler × 60 sn (arka plan trafiği dahil) |
| **Aktif kullanım süresi (tahmini)** | Arka plan dışı trafiği dilimde en az `usage.active_bytes_threshold` (2048 bayt) olan dilimler × 60 sn |
| **Site başına süre (tahmini)** | Her aktif dilimin 60 saniyesi, o dilimdeki sitelere arka plan dışı baytları oranında bölünür: site süreleri toplamı aktif süreye eşittir. **Ham süre**: sitenin herhangi bir trafiği olan her dilim için 60 sn (bölünmez; ayrıntı için) |
| **Kategori başına süre** | Site sürelerinin FortiGuard kategorisine göre toplamı; kategorisi bilinmeyen siteler **Bilinmiyor** |
| **İndirilen / Yüklenen** | `rcvdbyte` / `sentbyte`: ara kayıtların farkları + son kaydın kalanı, hiçbir zaman iki kez sayılmaz (arka plan dahil) |
| **Oturum sayısı** | Ayrı FortiGate oturumları; bir oturum her kişi/cihaz için bir kez, ilk dakikasının bulunduğu bölümde (mesai/mesai dışı) sayılır; gece yarısını geçen oturum iki günde de sayılır |
| **Engellenen denemeler** | Web filtresinde engellenen (`blocked`), DNS filtresinde engellenen (`block`) ve uygulama kontrolünde engellenen (`block`, `reset`) her kayıt, kategorisiyle; kişinin cihaz dökümünde her cihaz kendi denemelerinin tamamını sayar. O gün yalnızca engellenen denemeleri olan bir cihaz da dökümde (ve raporun Cihazlar sayfasında) yer alır; IP'si ve VLAN'ı bu denemelerinkidir |
| **İlk / son etkinlik** | Günün ilk aktif dakikasının başı / son aktif dakikasının sonu |
| **Saatlik ısı haritası** | Gün × saat, renk = saatteki aktif dakika (seçilen günden geriye 30 gün) |
| **Uygulamalar** | FortiGate `app`/`appcat` alanlarına göre süre ve bayt |
| **Mesai dışı kullanım** | Mesai saatleri dışındaki (ve tatillerdeki) aktif süre |
| **URL'ler** | Web filtresinin kaydettiği URL'ler (`fg_web.url`), kişi ve gün başına en yeni `usage.urls_per_day` (1000) tanesi; sertifika denetimiyle HTTPS'te çoğunlukla yalnızca `/` görünür |

"Aktif kişi" sayısı, aktif süresi olan konuların (kişi, yoksa cihaz, yoksa IP) sayısıdır.

## 2. Yöntem

### 2.1 Kim? (konu)

Trafik, **o anda** cihazın atandığı kişiye aittir (atama geçmişi, [adr/0008-device-assignment-history.md](adr/0008-device-assignment-history.md));
cihaz kimseye atanmamışsa cihaza (DHCP/ARP kira kaydındaki MAC), kira kaydı yoksa yalnızca IP
adresine. Kural 5651 sorgusunun kuralıyla **aynı koddur**: IP + an → kira → cihaz → o anki kişi.
Öğlen başka bir kişiye atanan cihazın öğleden önceki trafiği eski, sonraki trafiği yeni kişinindir
(dakika dilimi, ilk anına göre).

**Departman:** kişinin (bugünkü) departmanı; kişinin departmanı yoksa ya da konu bir cihaz/IP ise
en çok çevrimiçi dakikanın geçtiği VLAN'ın departmanı (VLAN'a bağlı departman, yoksa VLAN'ın adı;
o gün yalnızca engellenen denemeleri ya da URL'leri olan bir konu bu olayların VLAN'ını alır);
hiçbiri yoksa **Bilinmiyor**. Departman geçmişi tutulmaz.

### 2.2 Hangi site? (sırasıyla)

1. Aynı `sessionid` (ve kaynak IP) ile web filtresi kaydındaki ana bilgisayar adı (`fg_web.hostname`);
2. aynı `sessionid` ile uygulama kontrolü / SSL kaydındaki ana bilgisayar adı (`fg_app.hostname`, SNI);
3. aynı kaynak IP'nin, oturum başlamadan en çok `usage.dns_window_seconds` (600 sn) önce aldığı ve
   hedef IP'yi içeren **en yeni DNS yanıtı** (`fg_dns`);
4. FortiGuard hizmet adı (`dstinetsvc`, ör. `Google-Gmail`) — "hizmet" olarak gösterilir;
5. yalnızca hedef IP adresi. (Şartname 10.2'deki "ters DNS" yapılmaz: sunucu kendi DNS sorgusu
   göndermez, U3.)

Gece yarısını geçen uzun bir oturum ertesi gün de aynı adla görünür: 1-3. adımlar, oturum günün
okunan kayıtlarından (gece yarısından `usage.session_overlap_seconds` önce başlar) daha erken
başlamış olsa da oturumun başlangıcına kadar geriye bakar; arka plan kuralları da aynı kalır.

Ana bilgisayar adı **kayıt edilebilir alan adına** indirgenir (`www.youtube.com` → `youtube.com`,
`haber.bbc.co.uk` → `bbc.co.uk`); tam ad ayrıntıda kalır. Bunun için **Genel Sonek Listesi**
(Public Suffix List) kullanılır: `publicsuffixlist` paketinin içinde gelir, **internetten
indirilmez**. Daha yeni bir liste sunucuya konup `usage.psl_file` ayarıyla gösterilebilir (dosya
yoksa ya da okunamazsa paketteki liste kullanılır, iş kaydı nedenini yazar). Varsayılan olarak
yalnızca ICANN bölümü sayılır (`usage.psl_private_suffixes`): aksi hâlde `cloudfront.net` gibi bir
CDN binlerce "site"ye bölünürdü.

**Kategori:** web filtresinin `catdesc` değeri alan adı başına önbelleğe alınır
(`domain_categories`, en yeni günün en sık kategorisi); web filtresi kategorisi yoksa DNS
filtresininki; hiçbiri yoksa **Bilinmiyor** (İngilizce arayüzde "Unknown" yazar; API'deki ve
adresteki adı her dilde `Bilinmiyor`).

### 2.3 Baytlar dakikalara nasıl dağıtılır?

Bir oturumun kayıtları zaman sırasıyla okunur. **Ara kayıt** (`0000000020`, `sentdelta`/`rcvddelta`
alanlı) kendi farkını `[zaman − durationdelta, zaman]` aralığına ekler; diğer her kayıt (son kayıt)
toplam sayaçlarının o ana kadar sayılanı aştığı kadarını (**kalan**) önceki kayıttan kendi zamanına
kadar ekler. Böylece ara ve son kayıtlar asla iki kez sayılmaz; kaybolan bir ara kaydın baytları
son kayıtla gelir. Okunan ilk kayıt bir ara kayıtsa, toplam sayaçlarının kendi farkını aşan
kısmı (o kaydın aralığından önceki baytlar) oturumun başlangıcından o aralığa kadar eklenir: ilk
ara kaydı kaybolan bir oturumun baytları da sayılır, gece yarısından önce başlamış uzun bir
oturumun önceki güne düşen kısmı önceki günde kalır. Her artış aralığına **eşit** dağıtılır; güne
düşen kısmı o güne yazılır.
Oturum `[en erken (zaman − süre), son kayıt]` boyunca açıktır (çevrimiçi).

Gün, İstanbul saatiyle 00:00-24:00'tır. Gece yarısını geçen oturumlar için gün bittikten sonra
`usage.session_overlap_seconds` (30 dk) içinde gelen kayıtlar da önceki güne sayılır.

### 2.4 Arka plan trafiği

Arka plan trafiği **çevrimiçi süreye ve veri hacmine** sayılır, **aktif süreye sayılmaz** (spec 10.3):

- ana bilgisayar adı `usage.background_domains` kalıplarından birine uyan oturumlar (işletim sistemi
  güncellemeleri ve telemetri, Apple push, iCloud, Google API'leri, sertifika denetimleri OCSP/CRL,
  FortiGuard, MikroTik güncelleme sunucuları; kuruluşun diğer ürünlerinin, ör. IP santralinin ya da
  antivirüsün güncelleme ve bulut adlarını yönetici ekler); `*.ornek.com` kalıbı `ornek.com`'un
  kendisine de uyar, büyük/küçük harf fark etmez;
- `usage.background_ports` portlarına giden oturumlar (DNS 53, NTP 123, DNS over TLS 853);
- toplamı `usage.background_session_bytes` (2048 bayt) altındaki oturumlar;
- uygulama kategorisi `usage.background_appcats` içinde olan (`Update`, `Network.Service`)
  **ve ana bilgisayar adı bilinmeyen** oturumlar. (FortiOS 7.6 sıradan HTTPS gezinmesini
  `SSL_TLSv1.3.PQC` / `Network.Service` olarak loglayabilir, sahada doğrulanmalı; ad biliniyorsa
  ad kuralları karar verir — U4.)

Antivirüs güncellemeleri üreticiye göre değişir: kullandığınız ürünün güncelleme alan adlarını
listeye ekleyin. `*.googleapis.com` şartnamedeki varsayılandır; YouTube uygulaması gibi
uygulamaların trafiğini de gizleyebilir — gerekirse listeden çıkarın.

### 2.5 Mesai saatleri ve tatiller

`usage.work_hours` (varsayılan Pazartesi-Cuma 08:00-18:00) dışındaki ve `usage.holidays`
günlerindeki her dakika **mesai dışıdır**. ARCILOGGER yalnızca **sabit tarihli resmî tatilleri**
getirir: 1 Ocak, 23 Nisan, 1 Mayıs, 19 Mayıs, 15 Temmuz, 30 Ağustos, 29 Ekim. **Dini bayramların
(Ramazan ve Kurban Bayramı) tarihleri her yıl değişir; yönetici her yıl `YYYY-AA-GG` olarak
girmelidir.** Yarım günler (arife günleri, 28 Ekim öğleden sonra) bu sürümde ifade edilemez: gün
ya tamamen tatildir ya değildir.

```powershell
'["01-01", "04-23", "05-01", "05-19", "07-15", "08-30", "10-29", "2027-03-20", "2027-03-21"]' | & $db settings set usage.holidays - --by tolga
'{"days": [1, 2, 3, 4, 5], "start": "08:30", "end": "18:00"}' | & $db settings set usage.work_hours - --by tolga
```

(Örnekteki 2027 tarihleri yer tutucudur, gerçek tarih değildir; o yılın tarihlerini resmî takvimden alın.)

## 3. Neden her şey tahmindir — bilinen kör noktalar

- Kayıtlar **bağlantıları** gösterir, ekrana bakan kişiyi göstermez: açık bırakılmış ve arka planda
  veri çeken bir sekme aktif sayılabilir; okunup veri çekmeyen bir sayfa sayılmaz.
- **QUIC / HTTP3** (UDP 443) ve web filtresinin kaydetmediği diğer trafik yalnızca DNS yanıtıyla ya
  da FortiGuard hizmet adıyla adlandırılabilir; o da yoksa yalnızca IP adresi görünür.
- **DNS over HTTPS** (tarayıcının şifreli DNS'i) ad bilgisini gizler: oturum IP adresi ya da hizmet
  adıyla görünür.
- **FortiGate'ten geçmeyen** cihazlar ve trafik görünmez: başka bir çıkışı olan bir misafir Wi-Fi
  (ayrı bir hat ya da VPN tüneli; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 5), yerel ağ içi trafik
  (dosya paylaşımı, yazıcı), mobil veri.
- Aynı cihazı kullanan birden çok kişi ya da arkasında NAT olan cihazlar kişilere ayrılamaz.
- Rastgele MAC adresli telefonlar her yeni MAC'te yeni bir cihazdır: aynı kişiye atanmalıdır.
- Ara kayıt aralığı FortiGate ayarıdır (sahada doğrulanacak); ara kaydı olmayan ve gece
  yarısından 30 dakikadan uzun süre sonra biten bir oturumun gece yarısından önceki kısmı önceki
  günün son hesabında eksik kalır.

## 4. Ayarlar

Hepsi veritabanı ayarıdır (`& $db settings set …`, her değişiklik denetim kaydına yazılır).
Değişen bir ayar **son `usage.auto_rebuild_days` güne kendiliğinden** uygulanır (bölüm 5); daha
eski günler için elle yeniden hesaplayın.

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `usage.active_bytes_threshold` | 2048 | Dakikanın aktif sayılması için arka plan dışı bayt (1 - 1.000.000.000) |
| `usage.background_session_bytes` | 2048 | Toplamı bundan az olan oturum arka plandır |
| `usage.dns_window_seconds` | 600 | DNS yanıtı bu kadar eskiyse hâlâ oturumu adlandırır (0 - 86400) |
| `usage.session_overlap_seconds` | 1800 | Gün bittikten sonra hâlâ o güne sayılan kayıtlar (gece yarısını geçen oturumlar) |
| `usage.background_domains` | spec 10.3 listesi | Arka plan ana bilgisayar kalıpları (`*`, `?`) |
| `usage.background_appcats` | `["Update", "Network.Service"]` | Adı bilinmeyen oturumlarda arka plan uygulama kategorileri |
| `usage.background_ports` | `[53, 123, 853]` | Arka plan hedef portları |
| `usage.work_hours` | Pzt-Cum 08:00-18:00 | `{"days": [1..7], "start": "SS:DD", "end": "SS:DD"}` (bitiş hariç) |
| `usage.holidays` | 7 sabit resmî tatil | `AA-GG` her yıl, `YYYY-AA-GG` tek sefer |
| `usage.psl_file` | `""` | Daha yeni `public_suffix_list.dat` dosyasının yolu (boş: paketteki) |
| `usage.psl_private_suffixes` | `false` | Genel Sonek Listesi'nin özel bölümü de sayılsın mı |
| `usage.auto_rebuild_days` | 7 | Kendiliğinden yeniden hesaplanan geçmiş gün sayısı (0 - 62) |
| `usage.retention_days` | 400 | Bu kadar eski kullanım özetleri silinir (loglar `db.retention_days`) |
| `usage.urls_per_day` | 1000 | Kişi ve gün başına saklanan en yeni URL sayısı |
| `usage.blocked_per_day` | 5000 | Kişi ve gün başına saklanan engellenen deneme kaydı (kişi ve cihaz başına günlük sayaçlar her zaman tamdır) |
| `usage.pseudonymize_anonymous_role` | `false` | Açıksa yalnızca `usage.anonymous` yetkili görüntüleyiciler takma ad görür |
| `usage.change_min_percent` | 50 | Raporlarda "dikkat çeken değişiklik": 7 günlük ortalamaya göre en az bu kadar % |
| `usage.change_min_seconds` | 3600 | … ve sürelerde en az bu kadar saniye (bir tarafta) |
| `usage.change_min_bytes` | 1073741824 | … ve veri hacminde en az bu kadar bayt (1 GiB) |

## 5. Hesaplama ve yeniden hesaplama

Çalışan işler (`ArciWorker` hizmeti, [CORRELATION.md](CORRELATION.md) bölüm 4):

| İş | Zaman | Ne yapar |
|---|---|---|
| `usage.today` | 5 dakikada bir | Bugünü o ana kadar yüklenen kayıtlardan yeniden hesaplar (`partial`) |
| `usage.daily` | her gün 00:45 sonrası | Dünü kesin (`final`) olarak yeniden hesaplar |
| `usage.reattribute` | 10 dakikada bir | Son `usage.auto_rebuild_days` günden, hesaplandığı girdileri değişenleri yeniden hesaplar: bir cihaz bir kişiye (yeniden) atandı ya da kira zaman çizelgesi değişti, geç gelen kayıtlar yüklendi (gün hesaplanırken yüklenenler de), `usage.*` ayarları değişti; eksik ya da hâlâ `partial` günler de |
| `usage.requests` | 30 saniyede bir | Web arayüzünden istenen yeniden hesaplamalar (`POST /api/v1/usage/rebuild`) |
| `usage.retention` | her gün 03:30 sonrası | `usage.retention_days` günden eski özetleri siler |

**Cihazları kişilere bağladıktan sonra:** son 7 gün kendiliğinden, en geç 10 dakika içinde
güncellenir. Atama daha eskiyse (ör. geçmişe dönük bir zimmet) o günleri elle hesaplatın — web
arayüzünden ya da sunucuda (yönetici PowerShell, [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 0):

```powershell
& "$arci\arcilogger-worker.exe" usage rebuild --from 2026-10-01 --to 2026-10-19 --config $cfg
```

Her gün kendi işleminde hesaplanır (okuyan sayfa eski ya da yeni günü görür, karışığını görmez);
komut her gün için bir satır yazar ve `job_runs` tablosuna bir `usage.rebuild` kaydı ekler. Çıkış
kodları: 0 tamam, 1 bir gün başarısız, 2 yanlış tarih/yapılandırma, 3 veritabanına bağlanılamadı.
Yeniden hesaplama günün loglarıyla yapılır: web arayüzü (API), veritabanındaki en eski logun
gününden (hiç log yoksa bugünden) önce başlayan bir aralığı reddeder (422
`usage_rebuild_no_logs`, yanıt en erken günü söyler); logu olmayan bir gün boş hesaplanır ve
özetlerinin yerine boş bir gün yazılırdı, o günlerin özetleri olduğu gibi kalır (sunucudaki
`usage rebuild` komutu bu denetimi yapmaz). Veritabanının logları tutma süresi
`db.retention_days` (400 gün) ayarıdır; bu sürümde veritabanından log silinmez
([DATA-MODEL.md](DATA-MODEL.md)).

## 6. Gizlilik ve denetim

- **Adlar varsayılan olarak görünür** (sahibin kararı): kullanım görme yetkisi olan her rol
  (`usage.person` ya da `usage.anonymous`) kişi ve cihaz adlarını, MAC/IP adreslerini, ana
  bilgisayar adlarını ve URL'leri görür. `dashboard.usage` tek başına yalnızca şirket toplamlarını
  görür.
- Kişi düzeyinde veri döndüren her uç nokta **KVKK bilgilendirmesinin onaylanmasını** ister
  (`require_privacy_notice`). Adları gören her rol onaylar: bu sürümde dört rolün hepsi
  (yönetici, denetçi, İK, rapor izleyici; [roles.md](roles.md)).
- **Denetim kaydı** (adlar değil, numaralar ve sayılar): bir kişinin/cihazın ayrıntısının
  görüntülenmesi (`usage.view`, spec 8.5), her dışa aktarma (`usage.export`: görünüm, biçim,
  dönem, satır sayısı, kullanılan süzgeçler), yeniden hesaplama isteği (`usage.rebuild`), takma ad
  eşlemesinin gösterilmesi (`usage.pseudonyms`). Bir ayrıntının görüntülenmesinin ve tek bir
  kişinin/cihazın dışa aktarılmasının hedefi, görüntüleyen takma ad görse de gerçek kişi ya da
  cihazdır (`usage:person:12`): "bu kişiye kim baktı?" sorusu takma adlı görüntülemeleri de
  bulur. Kayıt hiç değiştirilmez; ama Kullanım sayfalarında takma ad gören bir okuyucu (ayar
  açıkken Denetçi) **Denetim Kaydı**'nda bu hedefi kişinin/cihazın takma ad belirteciyle görür
  (`usage:p:3f2a…`, `usage:d:…`; yanıtta `target_masked: true`, anahtar yokken `usage:masked`).
  Hedef süzgeci de gösterilen değerle karşılaştırılır. Girdinin özeti gerçek hedefi kapsar ve
  özetlerle bir tahmin sınanabilirdi; bu yüzden böyle bir okuyucu hiçbir girdinin özetini görmez
  (`prev_hash` ve `hash` boş, `/audit/verify` yanıtında `last_hash` boş); zincir sunucuda yine
  doğrulanır. Yönetici ve adları gören okuyucular gerçek hedefi ve özetleri görür.
- **Takma adlar (isteğe bağlı):** `usage.pseudonymize_anonymous_role` açıldığında, `usage.person`
  yetkisi olmayan `usage.anonymous` görüntüleyiciler `Kullanıcı-7F3A` / `Cihaz-1A2B` görür (MAC/IP
  gizli). Takma ad, gizli depodaki `usage.pseudonym_key` anahtarıyla HMAC-SHA-256'dır; her yerde
  ve her gün aynıdır. Anahtarı bir kez kaydedin (uzun, rastgele bir metin; değiştirirseniz bütün
  takma adlar değişir):

  ```powershell
  & $core secret set usage.pseudonym_key
  ```

  Anahtar yokken takma ad istenirse API `409 pseudonym_key_missing` yanıtı verir. Eşlemeyi
  (takma ad → kişi) yalnızca yönetici görür.

  Takma adlar Denetçi için zayıf bir korumadır: Denetçi `search.read` ve `lookup.run` yetkileriyle
  ham kayıtlardan ve 5651 sorgularından IP ve MAC adreslerine, kişilere ulaşabilir. Günün mühür
  manifesti (Mühürleme) günün son denetim girdisinin özetini taşır: bir günde az sayıda kullanım
  görüntülemesi olduysa bir tahmin hâlâ onunla sınanabilir. Denetim kaydını içeren kanıt paketleri
  (yalnızca yönetici ekleyebilir) girdileri gerçek hedefleri ve özetleriyle içerir; ayar açıkken
  Kullanım sayfalarında takma ad gören bir okuyucunun listesinde görünmez, ayrıntısı ve indirmesi
  404 yanıtı verir.
- **Kişiyi kişi düzeyindeki raporların dışında bırakmak:** Cihazlar ve Kişiler → Kişiler → kişinin
  İşlemler menüsü → **Düzenle** → **Kullanım raporlarından hariç tut** (yeni kişide "Kişi ekle"
  formunda da var; `persons.usage_excluded`). Bu kişi kişi listelerinde, ayrıntılarda, site/kategori
  kullanıcı listelerinde ve raporların kişi tablolarında görünmez; trafiği şirket ve departman
  toplamlarında sayılmaya devam eder. Ayar okunduğu anda geçerlidir (geçmiş günler de; yeniden
  hesaplama gerekmez; önceden oluşturulmuş ve gönderilmiş rapor dosyaları değişmez) ve geri
  alınabilir. Kişi listesinde adın yanında **Kullanım: hariç** işareti görünür. Değişiklik eski ve
  yeni değeriyle denetim kaydına yazılır (`person.update`); toplu değişiklik için kişi CSV'sinin
  isteğe bağlı `usage_excluded` sütunu (evet/hayır) kullanılır. Yalnızca `devices.manage` yetkisi
  olan kullanıcı (varsayılan rollerde Yönetici) değiştirebilir. 5651 sorguları, resmi raporları ve
  kanıt paketleri bu ayardan etkilenmez: kişi orada her zaman görünür.
- Dışa aktarılan dosyalar (CSV, Excel) kişisel veri içerir: yalnızca gerektiği kadar saklayın.

## 7. Web arayüzü ve API

Sayfalar (Kullanım: Şirket / Departman / Kişi / Cihaz / Site / Kategori) `/api/v1/usage` uç
noktalarını kullanır ([MVP-CONTRACTS.md](MVP-CONTRACTS.md) bölüm 12): dönem (`from`, `to`, gün),
saat süzgeci (`hours` = `all` / `work` / `off`), departman süzgeci (`department`), arama, sıralama,
sayfalama (`limit` 1-100.000, `offset`; arayüz bir listenin tamamını tek istekte okur), CSV ve
Excel dışa aktarma (Excel'de her bölüm ayrı sayfa ve bir "Yöntem" sayfası). Her yanıt
`estimated: true` ve `computed_until` (özetlerin işlendiği son an) taşır.

Bir sitenin ya da kategorinin **kullanıcıları** yalnızca o site/kategorideki değerleriyle görünür:
süre, mesai dışı süre, indirilen/yüklenen veri, oturum; sitede ayrıca ham süre. Özetler bir
kategorinin çevrimiçi dakikalarını, site başına engellenen denemeleri ve ilk/son etkinliği
tutmadığından bu sütunlar yoktur. Ayrıntı ekranı en çok süresi olan 200 kullanıcıyı, kategoride
100 siteyi ve sitede 50 ana bilgisayar adını gösterir; kısaltılmış bir kullanıcı ya da site
listesi toplam sayıyı söyler. Dışa aktarılan dosyada hepsi vardır.

## 8. Performans

Geliştirme makinesinde (4 çekirdek paylaşımlı, PostgreSQL 16, TimescaleDB yok) yük üreteciyle bir
Çarşamba, 50 kişi (100 kullanıcı cihazı + altyapı):

| Yük | Satır | Tam günün hesaplanması (hesap + yazma) |
|---|---|---|
| Varsayılan (kişi başı saatte 30 gezinme oturumu) | 92.674 satır; 24.309 trafik, 16.185 web, 32.370 DNS, 15.866 uygulama | **1,7 sn** (tekrar: 2,1 sn) |
| Beş kat yoğun | 450.182 satır; 123.097 trafik, 81.191 web, 162.382 DNS, 79.568 uygulama | **6,0 sn** (tekrar: 5,8 sn) |

Bütçe (`worker/tests/test_usage_performance.py`, `ARCILOGGER_USAGE_PERF=heavy` ile yoğun yük):
30 sn ve 120 sn. Şartnamenin yoğun günü (günde 5 milyon mesaj, ~1,4 milyon trafik satırı) için
doğrusal tahmin: gün sonunda hesap başına bir dakikadan biraz fazla.

## 9. Sahada doğrulanacaklar

- FortiGate ara kayıt aralığı ve ara kayıtların `sentdelta`/`rcvddelta` taşıdığı (biçim örneklerinde
  var).
- Web filtresinin `action` değerleri (`blocked`), DNS filtresinin (`block`) ve uygulama kontrolünün
  (`block`, `reset`) engelleme değerleri.
- Arka plan listesindeki FortiGuard ve MikroTik ana bilgisayar adları (`*.fortiguard.net`,
  `*.fortinet.net`, `upgrade.mikrotik.com`, `download.mikrotik.com`); kuruluşun diğer ürünlerinin
  (IP santrali, antivirüs …) güncelleme ve bulut adlarını ekleyin.
- Mesai saatleri ve o yılın dini bayram tarihleri.
