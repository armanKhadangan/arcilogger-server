# Resmi Rapor (5651 sorgu raporu) / Official lookup report

> ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Rapor bir hukuki uygunluk
> beyanı değildir; metninin ve kullanımının hukuk müşaviri tarafından incelenmesi önerilir.

## Türkçe

**Ne zaman üretilir?** "5651 Sorgu" sayfasında bir sorgu çalıştırıldıktan sonra "Resmi Rapor
(PDF)" düğmesiyle (`GET /api/v1/lookup/{id}/report.pdf`). Rapor **bir kez** üretilir ve
`exports/reports/ARCILOGGER-5651-rapor-<no>.pdf` olarak saklanır; sonraki her indirmede aynı dosya
verilir, böylece SHA-256 özeti değişmez. Her üretim (`lookup.report`) ve her indirme
(`lookup.report_download`) denetim kaydına yazılır. Raporu sorguyu yapan kişi ve yöneticiler
alabilir; denetçi yalnızca kendi sorgularının raporunu görür.

**İçerik:**

1. Rapor bilgileri — sorgu no, kurum (`arcilogger.toml` içindeki `site_name`; ayarlanmamışsa
   "–" yazılır ve ad giriş cümlesinden ve altbilgiden çıkarılır), sorguyu yapan, sorgu ve rapor
   zamanı, ARCILOGGER sürümü.
2. Talep bilgileri — makam, dosya/evrak no, talep tarihi, gerekçe (sorgudan önce zorunlu).
3. Sorgu ölçütleri — zaman (Europe/Istanbul ve UTC; girilen metin ve saat diliminin nasıl
   yorumlandığı), genel IP/port, protokol, hedef, tolerans, iç IP, MAC.
4. Yöntem — (1) FortiGate oturum kayıtlarının kaynak NAT bilgisi ve (2) "DHCP sunucusunun
   kayıtlarından (MikroTik DHCP, FortiGate DHCP olayları) ve saatlik ARP-LOG kayıtlarından kurulan
   IP–MAC zaman çizelgesi"; genel IP sorgusunda **çıkış satırı** ve yanıt türüne göre bir **yöntem
   paragrafı**; gerektiğinde **çift NAT** paragrafı ve kesin sonuç için gerekenler — "sonuç bir
   aday listesidir" cümlesi yalnızca aday varken yazılır, sonuç yoksa "aday oturum da bulunamadı"
   yazılır; aday puanının anlamı; FortiGate DHCP kayıt biçimi notu; sorgu motorunun açıklamaları;
   veri penceresi (ayrıntılar aşağıda, "İnternet çıkışları ve yöntem").
5. Sonuçlar — kesin eşleşmeler veya adaylar; her biri için oturum, NAT, zaman farkı, hedef
   eşleşmesi ve tam atıf zinciri (iç IP → IP–MAC kaydı → cihaz → kişi/departman, güven
   düzeyleri; IP çakışmasında diğer olası zincirler, hepsi "düşük" güvenle). Kişi bağlantısının
   idari bir atama olduğu belirtilir; kişi, atama geçmişine göre **olay anında** geçerli olan
   atamadır ve aralığıyla yazılır ("atama: 01.10.2026 09:00:00 tarihinden itibaren"), departmanı
   kişinin bugünkü departmanıdır (departman geçmişi tutulmaz). Olay anında atanmamış bir cihazda
   kişi yazılmaz; atama geçmişi hiç olmayan bir cihazda sorgu anındaki atama gösterilir ve bunun
   olay anının kanıtı olmadığı yazılır (ADR-0008). Hedef eşleşmesinin
   zayıf türleri ayrı yazılır ("hedef IP eşleşti, port farklı", "yalnızca adres eşleşti …
   paylaşılan CDN adresi olabilir"). Yalnızca alan adı verilen bir kesin (NAT) sorguda alan
   adı FortiGate DNS/web/uygulama kayıtlarında görülmediyse oturum yine listelenir, ancak hedef
   "hedef doğrulanmadı" olarak yazılır; kayıt eksikliği başka bir hedefe gidildiği şeklinde
   yorumlanmaz. Her eşleşme çıkışını ve `dstintf` değerini yazar; yapılandırılmamış bir
   göndericinin kayıtlarına dayanan eşleşme "UYARI / Warning" satırı alır; srcmac'e dayanan bir
   adım "kısmi kanıt" olarak, FortiGate DHCP kirası kira süresi ve son DHCP kanıtıyla yazılır
   (aşağıda, "FortiGate DHCP, srcmac ve tanımsız göndericiler").
6. Kanıt listesi — her kanıt (K1, K2 …) için veritabanı satırı, kaynak dosya, satır numarası,
   bayt konumu, ham satırın kendisi ve SHA-256 özeti; ham dosyaların rapor anındaki özetleri
   (mühürlü günlerde manifestle karşılaştırma); her mühürlü gün için manifest ve zaman damgası
   dosyalarının özetleri, seri no, damga zamanı, TSA, sağlayıcının Kamu SM üretim sunucusu olup
   olmadığı (pozitif ifade yalnızca doğrulama geçtiyse ve damga zinciri `tsa.legal_root_fingerprints`
   listesindeki bir köke ulaşıyorsa; manifesti üretim sunucusunu gösteren ama kökü listede olmayan
   gün "yasal sınıf olarak teyit edilmedi" diye nötr yazılır, test/mock damgası sayılmaz; "YASAL
   GEÇERLİ DEĞİL" yalnızca üretim sunucusu olmayan sağlayıcılar için, "test/geliştirme" yalnızca
   test (mock) damgaları için; "yasal geçerli" ifadesi kullanılmaz) ve
   **rapor anında yapılan doğrulamanın sonucu** (zincirin ilk günü, `python -m arcilogger_core
   verify` ile aynı kuralla denetlenir). Ham dosyası hâlâ spool klasöründe olan günler "henüz
   mühürlenmedi" olarak, spool dosyasının rapor anındaki özetiyle listelenir (gün rapordan en az
   iki gün önce kapandıysa "mühürleme gecikmiş" uyarısıyla). Ham dosyası kasada olduğu hâlde
   manifesti veya zaman damgası bulunmayan günler kırmızı **"MÜHÜR BULUNAMADI"** olarak yazılır —
   asla "henüz mühürlenmedi" olarak değil. 72 KiB'den uzun ham satırlar raporda kısaltılır ve
   bu açıkça belirtilir; satırın SHA-256 özeti ve uzunluğu her zaman satırın tamamınındır. Kira
   zaman çizelgesine girmemiş bir FortiGate DHCP kaydı da gerektiğinde burada, eşleştirme
   olmadığını söyleyen notuyla gösterilir; ham satırlar aygıt tanıma alanlarıyla olduğu gibi
   yazılır (aşağıda).
7. Raporun doğrulanması — özetin nasıl kontrol edileceği ve (açıksa) zaman damgası.
8. Yasal not. Her sayfada "Sayfa n / N", sorgu no, sürüm ve ifade kuralı vardır.

**İnternet çıkışları ve yöntem (bölüm 4 ve 5):** genel IP ile yapılan bir sorgunun 4. bölümü,
istenen adresin hangi internet çıkışına (Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman
çizelgesi → İnternet çıkışları (NAT)) göre arandığını bir satırda yazar: "Çıkış / Exit: {yanıt
etiketi} — {ad} ({açıklama}), {tür}". `unconfigured` ve `no_egress` yanıtlarında çıkış adı
yazılmaz. Etiketler ve tür adları, web'deki sorgu başlığının ve aşağıdaki okuma tablosunun da
kullandığı tek bir Türkçe/İngilizce tablodan gelir:

- `public` "Genel IP" / "Public IP"; `transip` "NAT adresi" / "NAT address";
- `fallback`, doğrudan bir çıkışta "Doğrudan hat doğrulanamadı" / "Direct line not confirmed";
  servis sağlayıcı NAT'ı çıkışının NAT adresinde (`transip`) "NAT adresi doğrulanamadı" / "NAT
  address not confirmed";
- `observed` "Kayıtlarda görülen adres" / "Address seen in the logs"; `public_unknown` "Genel
  adresi bilinmeyen hatlar" / "Lines with unknown public address"; `unconfigured` "Çıkış tanımlı
  değil" / "No exits defined"; `no_egress` "Çıkış yok" / "No exit";
- türler: "Doğrudan" / "Direct", "Servis sağlayıcı NAT'ı" / "ISP NAT", "FortiGate'ten geçmiyor" /
  "Does not pass the FortiGate", "İnternet çıkışı değil" / "Not an internet exit".

Her eşleşme de kendi satırını alır: "Çıkış / Exit: {ad} ({açıklama}) · dstintf {dstintf}"
(kanıt satırının NAT adresini tanımlayan çıkış; hiçbir çıkış tanımlamıyorsa "-"). Kayıtları farklı
çıkışlarda görünen bir oturum (`session_changed_exit`) iki çıkışı da yazar. Böylece
`public_unknown`, `unconfigured` ya da `fallback` yanıtında her adayın hangi hattan çıktığı
görülür.

Yöntem bölümü her (yanıt, tür) için bir paragraf yazar, toplam on bir paragraf. Her paragraf
neyin çıkış tanımına (ayara), neyin kayıtlara dayandığını ayırır. `{searched}` sonucun saklanan
arama bilgisindeki, gerçekten aranan adreslerdir:

- `public`, doğrudan: genel IP FortiGate'in kendisindedir ve oturum kayıtlarında kaynak NAT
  adresi (transip) olarak görünür. Talepteki port FortiGate'in NAT portuyla (transport)
  karşılaştırılır; port yoksa o adresle çıkan oturumlar aday olarak sıralanır.
- `public` ya da `transip`, servis sağlayıcı NAT'ı: tanıma göre servis sağlayıcının yönlendiricisi
  FortiGate'in bu hattaki NAT adresini ({transips}) ikinci kez çevirir ve bu dönüşümün kaydı
  ARCILOGGER'da yoktur. Arama bu çıkışın NAT adresleriyle (arayüzleri tanımlıysa arayüzleriyle
  de) sınırlanır. Port koruma ya kullanılmamıştır ya da yalnızca sıralamada kullanılmıştır
  (sahada doğrulandı olarak işaretliyse). NAT adresi girilmemiş, tanımsız adresleri kapsayan bir
  çıkışta {transips} yerine "hiçbir çıkışa tanımlı olmayan özel ya da paylaşılan NAT adresleri"
  yazılır. `transip` yanıtında istenen adres FortiGate'in bu çıkıştaki kendi NAT adresidir ve
  eşleşme FortiGate kaydında transip ve transport ile yapılır. Genel adresi "değişebilir"
  işaretli bir çıkışın `public` yanıtında paragraf `egress_public_dynamic` metnini de taşır:
  adresin o anda bu hatta ait olduğunu yalnızca servis sağlayıcı doğrulayabilir.
- `fallback`, doğrudan çıkış ("Doğrudan hat doğrulanamadı"): istenen adres o anın çevresinde
  FortiGate kayıtlarında NAT adresi olarak görünmüyor ya da yalnızca bir kısmında görünüyor;
  kayıtlar o sırada özel ya da paylaşılan başka kaynak NAT adreslerini de ({seen}, en çok 5)
  gösteriyor. Bu nedenle kesin eşleşme yapılamadı ve {searched} adresleriyle çıkan oturumlar aday
  olarak sıralandı. Servis sağlayıcının yönlendiricisinin o sırada ikinci bir NAT yapmış
  **olabileceği** yazılır, bir olgu olarak değil. Çıkışın arayüzleri tanımlıysa {seen} "bu
  hatta", tanımlı değilse "FortiGate kayıtlarında" görülmüş olarak yazılır: arayüz olmadan
  adresin hangi hatta görüldüğü bilinemez.
- `fallback`, servis sağlayıcı NAT'ı çıkışının NAT adresi ("NAT adresi doğrulanamadı"): aynı
  açıklama; köprü modu cümlesi ve "Doğrudan olarak tanımlayın" önerisi yoktur.
- `fallback` eki: arama başka bir çıkışın adreslerine de girdiyse "Aranan adreslerin bir kısmı
  {other} çıkışına aittir" ile başlayan bir ek yazılır. Ek, o çıkışın neden aynı hat sayıldığını
  söyler: yükseltmede eski ayarlardan arayüzsüz aktarıldığı için ya da bu hatla bir arayüzü
  paylaştığı için. Liste o hattın kullanıcılarını da içerebilir.
- `observed` ("Kayıtlarda görülen adres"): adres hiçbir çıkışta tanımlı değil, ama o anın
  çevresinde (±15 dakika) FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor
  (`egress_observed_not_configured`). Adres FortiGate'ten geçmeyen bir çıkışa tanımlıysa metin
  `egress_not_logged_but_seen` olur. Eşleşme FortiGate kaydında yapılır; adresin o tarihte kuruma
  ait olduğunu servis sağlayıcı doğrular.
- `public_unknown`: adres hiçbir çıkışın genel adresi değil. Arama, genel adresi tanımlanmamış
  ya da değişebilen çıkışlarda ({exits}) yapıldı ve bu çıkışlarda servis sağlayıcının ikinci bir
  NAT yaptığı varsayıldı. Adres bu hatlardan birine ait değilse liste ilgisiz kullanıcıları
  gösterebilir.
- `unconfigured`: internet çıkışı tanımlanmamış. Servis sağlayıcının ikinci bir NAT yaptığı
  varsayıldı ve FortiGate'in özel ya da paylaşılan bir NAT adresiyle çıkan bütün kaynak NAT
  oturumları arandı. Genel bir NAT adresiyle çıkan oturumlar FortiGate'in kendi adresinden
  çıktıkları için aranmadı.
- `not_logged`: adres FortiGate'ten geçmeyen bir çıkışa ait ve ARCILOGGER'da bu hattın oturum
  kaydı yok. Rapor sonuç içermez; kayıt ancak hattı yöneten cihazdan alınabilir.
- `transip`, internet çıkışı değil: adres, çıkış tanımına göre FortiGate'in iç ağlara (DMZ, başka
  bir VLAN, VPN ya da şube) yaptığı kaynak NAT'ın adresidir. Oturum aranmadı; rapor sonuç
  içermez.
- `no_egress`: adres kurumun tanımlı internet çıkışlarından hiçbirine ait değil ve o sırada
  yapılandırılmış kaynakların FortiGate kayıtlarında da görünmüyor; oturum aranmadı. Adres
  kurumun bir hattına aitse çıkış tanımlanıp sorgu yeniden çalıştırılmalıdır. Ardından "Sorgu
  anında tanımlı çıkışlar: {configured}." gelir: her çıkış "ad (açıklama): tür, genel adresler"
  biçimindedir. Bunlar güvenlik duvarının kendi adresleridir, kişisel veri değildir. Liste
  sorgunun saklanan sonucundan gelir, bugünkü ayardan değil.

Tanımsız adresler de arandıysa `{searched}` "… ve hiçbir çıkışa tanımlı olmayan bütün özel ya da
paylaşılan kaynak NAT adresleri (T çevresinde görülenler: …)" diye biter. T istenen andır. Böylece
listede görülmeyen bir tanımsız adresten gelen bir aday da yazılı aramanın içinde kalır.

`no_egress`, `not_logged` ve internet çıkışı olmayan bir NAT adresi hiç oturum aramaz. Bu yüzden 4.
ve 5. bölüm, kayıtlara dayalı bir "kimse yok" gibi okunacak "Ölçütlere uyan kayıt bulunamadı"
yerine şunları yazar:

- "Oturum aranmadı: istenen adres {address} tanımlı internet çıkışlarından hiçbirine ait değil
  (bölüm 4)."
- "Oturum aranmadı: istenen adres {address} FortiGate'ten geçmeyen {exit} çıkışına ait (bölüm
  4)."
- "Oturum aranmadı: istenen adres {address} internet çıkışı olmayan {exit} tanımına ait (bölüm
  4)."

Yöntem bölümü ayrıca şu notları, varsa, metinleriyle yazar:

- `source_clock_offset`: kanıt gösterilen bir kaynağın saati o anda kaymıştı (sonuç boşsa aranan
  bir kaynağın). Zincirler düşük güvenle gösterilir. Sınır, kira araması ve iç IP sorgusu için
  120 saniyedir; yalnızca oturum araması için ölçülen bir kaynakta, sorgu toleransı daha büyükse
  toleranstır.
- `egress_internal`: internet çıkışı olmayan NAT adresi.
- `egress_changed_after_event`: olaydan sonra tanımı değişen her çıkış, değişiklik zamanı, kim
  tarafından (ARCILOGGER'ın kendi değişikliklerinde "yükseltme (eski NAT ayarlarından aktarıldı)"
  ya da "varsayılan değer") ve çıkışın bugün ne tuttuğu, silindiyse "silindi". Not denetim
  kaydına bakmadan okunabilir; önceki tanımlar kurumun denetim kaydındadır (`settings.change`).

Bu notların hepsi ve FortiGate kira notları saklanan sorgu sonucundan gelir, rapor anındaki
ayardan değil: rapor ilk indirmede üretilir ve ayar o arada değişmiş olabilir. Metni bir yöntem
paragrafının içinde yazılan açıklamalar (`egress_observed_not_configured`,
`egress_not_logged_but_seen`, `egress_public_dynamic`) açıklama listesinde bir daha yazılmaz;
diğer her açıklama listede kalır.

**Çift NAT paragrafı (bölüm 4):** aday ya da sonuçsuz bir yanıtta sorgu motoru çift NAT
belirlediyse yazılır; `transip` yanıtında **hiç yazılmaz**. Orada istenen adres FortiGate'in kendi
NAT adresidir, eşleşme transip ve transport ile yapılır ve "genel port kesin eşlenemez" cümlesi
yöntem paragrafıyla çelişirdi. Üç biçimi vardır:

- tanımlı servis sağlayıcı NAT'ı (`public` yanıtı): "Çift NAT: {exit} çıkışı servis sağlayıcı
  NAT'ı olarak tanımlı; servis sağlayıcının yönlendiricisi FortiGate'in adresini ikinci kez
  çevirir. …";
- varsayılan (`unconfigured`, `public_unknown`): "Çift NAT varsayımı: bu adres için servis
  sağlayıcının ikinci bir NAT yaptığı varsayıldı; …";
- `fallback`: paragrafın yerini yöntem paragrafı tutar. Saklanan çift NAT açıklamaları da bu
  paragrafın yanında ayrıca listelenmez.

`fallback` dışındaki biçimler CGNAT cümlesini ekler: "Genel adres başka müşterilerle
paylaşılıyorsa (CGNAT) aday listesi doğru oturumu içermeyebilir; kesin yanıt için servis
sağlayıcının NAT kaydı gerekir." Sonra sonuç cümlesi ve öneri gelir. Kesin eşleşme için ya (a)
servis sağlayıcının ilgili zamana ait NAT kaydı (genel port → FortiGate çıkış portu) gerekir ya da
(b) servis sağlayıcı yönlendiricisinin köprü (bridge) moduna alınması ve hattın İnternet
çıkışlarında 'Doğrudan' olarak tanımlanması. `fallback` yalnızca (a)'yı yazar; doğrulanamayan çıkış
doğrudansa "ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması"
ekiyle. "Doğrudan olarak tanımlayın" önerisi zaten doğrudan olan bir çıkış ve bir `transip`
fallback için hiç yazılmaz. Rapor, hattın servis sağlayıcı yönlendiricisinin arkasında olduğunu
hiçbir zaman bir olgu olarak yazmaz. Biçim cümlesi ve CGNAT cümlesi yalnızca talep bir genel IP
adresi içerdiğinde yazılır. İç IP, MAC ya da yalnızca hedefle yapılan bir sorguda çift NAT
paragrafı sonuç cümlesi ve öneriyle sınırlıdır.

**FortiGate DHCP, srcmac ve tanımsız göndericiler (bölüm 4–6):**

- **Kira satırı.** FortiGate DHCP kirasının satırı kaynağını "FortiGate DHCP kaydı / FortiGate
  DHCP record" olarak adlandırır ve şunu ekler: "kira süresi (FortiGate kaydı): {lease} sn · son
  DHCP kanıtı: {last}". İkisi de kiranın istenen anda ya da ondan önceki **kendi son DHCP
  kaydından** gelir. Kirayı bitiren satır ya da sonraki bir yenileme kullanılmaz. Kayıt süre
  yazmadıysa "ayarlardaki süre" yazılır. Bu ekler yalnızca kaynağı FortiGate DHCP olan kiralarda
  bulunur; MikroTik kiralarının satırı değişmez.
- **Düşük güven notları.** Bir zincir aşağıdaki nedenlerden biriyle düşük güvendeyse o nedenin
  metni kira satırının yanında yazılır:
  - `lease_renewal_not_seen`: istenen an, kiranın son kaydından kira süresinin yarısından fazla
    sonra ve arada yenileme kaydı yok;
  - `fortigate_dhcp_not_converted_since_lease`: kiranın son kaydından sonra o adres ya da kiranın
    MAC adresi için, kira zaman çizelgesinin bu kiraya uygulamadığı bir FortiGate DHCP kaydı var.
    O kayıt başka bir istemciye ait olabileceği için gösterilmez;
  - `fortigate_dhcp_no_network`: adres tanımlı hiçbir ağda değil.

  Kendi notu olmadan düşük güvende görünen bir FortiGate zinciri, ardından kiraların yeniden
  oluşturulmadığı bir geri almadan kalmış olabilir ([KURULUM.md](KURULUM.md) bölüm 13). Kiralar
  yeniden oluşturulup sorgu yeniden çalıştırılmalıdır.
- **Kayıt biçimi notu.** Yöntem bölümünde bir kez yazılır. Biçim doğrulanmadıysa: "FortiGate DHCP
  kayıt biçimi bu kurulumda sahada doğrulanmadı; kira kayıtları FortiOS belgelerindeki biçime göre
  okundu." Doğrulandıysa sorgu anında saklanan metin yazılır: kimin, hangi tarihte "sahada
  doğrulandı olarak işaretlendi" dediği. İkisi birlikte yazılmaz ve zincirlerin yanında
  tekrarlanmaz. Not sorgu anındaki durumu gösterir: doğrulanmadan yapılan bir sorgunun raporu,
  ayar sonradan açılıp rapor ilk kez o zaman indirilse de "sahada doğrulanmadı" der.
- **srcmac.** Kira kaydı olmayan bir iç IP için MAC adresi, listelenen bir arayüzde FortiGate
  oturum kaydının srcmac alanından gelebilir. Satır şöyledir: "MAC adresi (kısmi kanıt):
  FortiGate oturum kaydının srcmac alanı, {interface} arayüzü; FortiGate'in aygıt tanıma
  bilgisine göre kaynak IP adresi o anda bu MAC adresiyle ilişkilendirilmişti; kira kaydı yok".
  Satır MAC adresinin oturumu gönderdiğini söylemez; güven düşüktür. srcmac kira kaydındaki MAC
  adresinden farklıysa kira zinciri ve srcmac'e göre olası zincir iki MAC adresiyle birlikte
  yazılır. Olası zincir "Olası diğer zincir (srcmac, kısmi kanıt)" başlığı altındadır; ikisi de
  düşük güvenlidir. Zinciri hiç kurulamayan bir eşleşme "Zincir kurulamadı: o anda bu iç IP adresi
  için kira kaydı yok." yazar.
- **Kira zaman çizelgesine girmemiş FortiGate DHCP kaydı.** Bazı FortiGate DHCP kayıtları kira
  kanıtına çevrilmemiştir: yükseltmeden önce yüklenmiş, alanları okunamamış ya da bir geri
  almadan sonra önceki sürümün yüklediği kayıtlar. Bir iç IP sorgusunda ya da kesin eşleşmede,
  o anı kapsayan böyle bir kayıt (IP başına en çok bir tane, andan önceki) 6. bölümde her kanıt
  gibi ham satırı ve mühür bilgisiyle gösterilir. Not şudur: "kira zaman çizelgesine girmedi;
  zincir kurulmadı, güven düzeyi verilmez". Kayıt bir eşleştirme değildir. Aday raporu yalnızca
  `fortigate_dhcp_not_converted` metnini IP adresleriyle yazar; böyle bir satır göstermez. Bu
  kayıtlar en az 7 gün geriye aranır, en uzun kira penceresi daha uzunsa o kadar. Daha eski
  bir kayıt adlandırılmaz.
- **Tanımsız gönderici.** Kayıtları yapılandırmada tanımlı olmayan bir göndericiden gelen bir
  eşleşme, kanıtla uyuşmayan bir eşleşmedeki gibi bir "UYARI / Warning" satırı alır: "Bu
  eşleşmenin kayıtları yapılandırmada tanımlı olmayan bir göndericiden geldi: {kaynaklar}; kurumun
  FortiGate'inden geldiği doğrulanmadan kanıt olarak kullanılmamalıdır." Aynı rapordaki diğer
  eşleşmeler bu uyarıyı almaz. Bir göndericinin adresi kayıtlar alındıktan sonra FortiGate kaynağı
  olarak tanımlandıysa, bu kayıtlara dayanan zincir ya da eşleşme `source_configured_later`
  metnini taşır ("gönderici sonradan yapılandırıldı").
- **Aygıt tanıma alanları ham satırlarda.** FortiGate'te aygıt tanıması açık bir arayüzün oturum
  satırları istemcinin MAC adresini (`srcmac`), cihaz adını (`srcname`), işletim sistemini
  (`osname`), donanım bilgisini, cihaz türünü ve pasif öğrenilen kullanıcı adını (`unauthuser`)
  taşır. Böyle bir satır kanıt olarak gösterildiğinde bu alanlar olduğu gibi yazılır, başka
  kişilerin aday oturumlarında da. `lookup.srcmac_interfaces` ayarı bunu değiştirmez. Ham
  satırlar hiçbir zaman değiştirilmez.

**Ham satır karşılaştırması (bölüm 4 ve 6):** sorgunun sonucu veritabanı satırlarından
(`fg_traffic`, `mt_dhcp`, `mt_arp`, `leases`) gelir; veritabanı türetilmiş veridir, kanıt ham
kayıt satırıdır. Rapor oluşturulurken her kanıt satırı ingest hizmetinin kullandığı ayrıştırıcıyla
yeniden okunur ve raporda gösterilen değerlerle karşılaştırılır: oturumun kaynak/hedef adres ve
portları, protokol, FortiGate oturum no, NAT adresi/portu; IP-MAC kaydının IP ve MAC adresi
(IP'yi başka bir cihaza veren kapanış satırında yalnızca IP); her satırda kayıt türü ve zamanı.
Bir fark varsa rapor bunu kırmızı uyarıyla yazar (bölüm 4 ve 5'in başında; farklar bölüm 6'da
ilgili kanıtın altında, "raporda X, ham satırda Y"); o sonuç **"kesin eşleşme" olarak değil
"kanıtla uyuşmayan sonuç"** olarak gösterilir ve zincire güven düzeyi verilmez. Karşılaştırma
veritabanının kayıtlar alındıktan sonra değişmesini (elle düzeltme, hatalı yeniden hesaplama,
müdahale, ayrıştırıcı sürümü değişikliği) ortaya çıkarır; ayrıştırıcının kendi hatasını
ortaya **çıkaramaz** (aynı ayrıştırıcı aynı hatayı yineler) ve cihazın kişiye bağlanması bir
log satırına dayanmaz, idari bir atamadır. 5651 Sorgu sayfası veritabanı sonucunu gösterir;
karşılaştırma resmi rapordadır.

**Raporun özeti nasıl doğrulanır?** PDF kendi özetini içeremez. Özet ARCILOGGER'da sorgu
kaydında (`report.sha256`), indirme yanıtının `X-Arci-Sha256` başlığında ve denetim kaydında
(`lookup.report`) bulunur. Dosyanın özetini yeniden hesaplayıp karşılaştırın:

```
Windows:      certutil -hashfile ARCILOGGER-5651-rapor-17.pdf SHA256
Linux/macOS:  sha256sum ARCILOGGER-5651-rapor-17.pdf
```

**İsteğe bağlı zaman damgası:** `lookup.report_timestamp` ayarı açıksa (varsayılan kapalı;
yönetici PowerShell'inde
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set lookup.report_timestamp true --by AD`) PDF'in
özeti, `arcilogger.toml` içindeki `[tsa]` sağlayıcısıyla RFC 3161 zaman damgasına gönderilir
(Kamu SM ile rapor başına **1 kontör**). Damga `….pdf.zd` olarak PDF'in yanında saklanır ve
`GET /api/v1/lookup/{id}/report.pdf.zd` ile indirilir. PDF, sağlayıcıyı ve sağlayıcı Kamu SM üretim
sunucusu değilse damganın **yasal geçerli olmadığını** açıkça yazar. Doğrulama, mühürlü günlerle
aynı yöntemle ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) Yöntem B ve C1; istemci sürümü ve
çıktı biçimi orada ve [kamusm.md](kamusm.md)'de):

```text
java -jar tss-client-console-3.1.33.jar -CC ARCILOGGER-5651-rapor-17.pdf ARCILOGGER-5651-rapor-17.pdf.zd
openssl ts -verify -in ARCILOGGER-5651-rapor-17.pdf.zd -data ARCILOGGER-5651-rapor-17.pdf \
    -CAfile kokler.pem -attime <genTime>      # yalın belirteçse -token_in
```

Damga alınamazsa rapor yine verilir; durum `failed` ve hata metni sorgu kaydında ve denetim
kaydında görülür (otomatik yeniden deneme yoktur).

**Rapor üretilemezse hiçbir şey saklanmaz:** Kanıt dosyalarından biri (ham dosya, manifest,
zaman damgası) var olduğu hâlde şu anda okunamıyorsa — API hizmetinin klasör izni yoksa 503
`report_access_denied`, başka bir okuma hatasında 503 `report_evidence_unreadable` —, dayandığı
bir gün tam o sırada mühürleniyorsa (503 `report_seal_in_progress`; rapor sırasında mühürlenen
gün yeniden okunur ve hiçbir zaman "mühür bulunamadı" olarak kaydedilmez) veya PDF
dizilemezse (500 `report_render_failed`) rapor kaydedilmez; sorun giderildikten sonra aynı istek
eksiksiz raporu üretir. Geçici bir sunucu sorunu, bir kez üretilip hep aynı verilen rapora
"dosya okunamadı" olarak işlenmez. Kanıtın kendisine ait durumlar (bozuk sıkıştırılmış veri,
mühürden sonra değişmiş dosya, kaybolmuş mühür) ise raporda açıkça yazılır.

**Yeniden üretme yok:** Saklanan dosya eksik veya özeti farklıysa ARCILOGGER raporu yeniden
üretmez (409 `report_missing` / `report_damaged`), çünkü verilen özet geçersiz kalırdı ve yeni
bir rapor kasayı *bugünkü* hâliyle okurdu. Raporun dayandığı girdiler (`lookup_reports.inputs`)
saklanır; aynı girdiler aynı ARCILOGGER, reportlab ve yazı tipi sürümleriyle **bayt bayt aynı**
PDF'i verir (inceleme için).

## English (short)

The official report is rendered once per lookup, stored under `exports/reports/` and served
unchanged, so its SHA-256 ("ARCILOGGER signature digest", kept in `lookup_reports.sha256`, the
`X-Arci-Sha256` header and the audit entry `lookup.report`) stays valid. It contains the
organisation (`site_name`; a dash while it is not set, and the name is left out of the opening
sentence and the footer), the request details, the query as entered and as interpreted, the method
(with the double-NAT explanation and what would make the answer exact), the results with full
attribution chains (the person is the assignment valid at the moment of the event, printed with its
interval; the department is the person's current one; ADR-0008), the evidence (raw lines with file,
line number, byte offset and hash; file hashes; for every sealed day the manifest and token hashes,
serial, genTime, TSA, the provider row and a fresh verification (the provider row keeps three facts
apart, decision 5: a production day is named positively only when its chain reaches a root listed in
`tsa.legal_root_fingerprints`; a production day whose root is not listed is "not confirmed as legal
grade", never a test stamp; "YASAL GEÇERLİ DEĞİL" only for other providers, "test/geliştirme" only
for mock stamps); days not sealed yet with the spool file's hash at report time, flagged when
sealing is overdue; days whose files are in the vault without manifest or token as **seal not
found**, in red), how to verify the report, and the legal note; every page is numbered. Nothing is
stored when a cited file exists but cannot be read now (503 `report_access_denied` /
`report_evidence_unreadable`) or the layout fails (500 `report_render_failed`); the next request
tries again. Every cited raw line is parsed again with the ingest parser and compared with what the
report shows from the database (session addresses, ports, protocol, session number, NAT; IP and MAC
of an IP-MAC record; record type and time): a difference is printed in red, and that result is shown
as "contradicted by its evidence", never as an exact match or with a confidence. The check finds a
database changed after ingest, not a defect of the parser itself. With `lookup.report_timestamp` the
PDF's hash is time-stamped (1 kontör), the `.zd` is stored next to it and the PDF names the provider
and warns when it is not the Kamu SM production server (it never states that a stamp is legally
valid). Verify with `certutil -hashfile … SHA256` / `sha256sum`, and the token with Zamane `-CC` or
`openssl ts -verify`.

## Verify on site / open items

- Zamane from the API service: the stamp runs the Java client as `NT SERVICE\ArciApi`; the
  service needs read access to `secret.key` (it has it, ADR-0007), Java and the jar, and write
  access to `exports\reports` (it has it). Test once with the Kamu SM **test** server.
- The mock provider keeps its keys in `state\mock-tsa`, which the API may only read (ADR-0007):
  the sealing task (or `arcilogger-core tsa-test`) must create them first; a report stamp from the
  API does not count down the mock's credit counter (it cannot write there). The mock is never
  legal, and its root is trusted only while `tsa.provider` is `mock`.
- The API service reads the raw spool of days not sealed yet: the installer's ACL plan
  (`ArciInstall.psm1` `Get-ArciAclPlan`, ADR-0007) grants `NT SERVICE\ArciApi` read
  (ReadAndExecute, no write or delete) on `<data>\spool`; re-running the installer or
  `Set-ArciVaultAcl.ps1` gives existing installations the right. On site: as ArciApi, view a
  raw line of today and make a report that cites today.
- Reading cost of a report: each cited raw file is read once (hash + lines); a sealed day is
  decompressed up to the last cited line and `verify_day` re-hashes the day's sealed files —
  roughly 1–4 s per GB on the server (measure on site with a full day).
- Cost of `GET /raw` (measured on the development machine; measure on site): the first view of
  a late line reads the file up to it — spool about 0.45 s per 500 MB, a sealed `.log.gz` about
  1.1–2 s per 500 MB of uncompressed log (so up to ~20 s for a late line of a 5 GB day). It
  leaves checkpoints in memory (spool: line starts every 4 MiB, 32 files; sealed files: a copy
  of the decompressor every 64 MiB, ~40 KB each, 8 files), so every later view in that file
  costs well under a second (measured 0.07 s for 500 MB). Views of one sealed file wait for each
  other and at most two sealed files are decompressed at once, which bounds the CPU a user can
  take; there is no per-user rate limit yet.
- `GET …/report.pdf` creates the report on first use (a GET with a side effect; cookies are
  SameSite=Strict, so another site cannot trigger it). Lookups and reports are not rate-limited
  yet (spec 13 "rate limits on … exports").
- Stored reports are kept with the lookup (no retention job for `exports/reports` yet); they
  contain person-level data — listed as an open decision in the draft retention/deletion policy
  for legal review ([legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md)).
- The report wording (Turkish and English) needs legal review before the first official use.
