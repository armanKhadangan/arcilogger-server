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
çizelgesi → İnternet çıkışları (NAT)) göre arandığını "Çıkış / Exit" satırında yazar: "{Türkçe
etiket} / {İngilizce etiket} — {ad} ({açıklama}), {Türkçe tür} / {İngilizce tür}"; birden çok
çıkış "; " ile ayrılır, açıklaması olmayan bir çıkışın yalnızca adı yazılır. `unconfigured` ve
`no_egress` yanıtlarında çıkış adı yazılmaz. Etiketler ve tür adları, web'deki sorgu başlığının ve aşağıdaki okuma tablosunun da
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

Her eşleşme de kendi satırını alır: "Çıkış / Exit: {ad} ({açıklama}) · dstintf {dstintf}" (kanıt
satırının NAT adresini tanımlayan çıkış; hiçbir çıkış tanımlamıyorsa "-"). "-" yalnızca talep bir
genel IP adresi içerdiğinde görülür: iç IP, MAC ya da yalnızca hedefle yapılan bir sorguda çıkış hiç
belirlenmez ve satır "çıkış belirlenmedi (talepte genel IP yok) / exit not determined (no public IP
in the request) · dstintf {dstintf}" der. Böylece `public_unknown`, `unconfigured` ya da `fallback`
yanıtında her adayın hangi hattan çıktığı görülür. Saklanan sonuç her eşleşme için tek bir çıkış
tutar (o ana en yakın kayıt satırının). Kayıtları farklı çıkışlarda görünen bir oturumda
(`session_changed_exit`) satıra şu eklenir: yalnızca bir oturum çıkış değiştirdiyse ve bu eşleşmenin
kanıt satırları birden çok çıkış gösteriyorsa öbür çıkış "oturumun kayıtlarında ayrıca: {çıkışlar}"
/ "the session's records also show: {exits}" diye adıyla yazılır (öbür adres hiçbir çıkışa tanımlı
değilse adı yerine "hiçbir çıkışa tanımlı olmayan bir NAT adresi" / "a NAT address no exit claims");
aksi hâlde satırların kendi değerleri yazılır: "oturumun kayıtları birden çok çıkış gösteriyor: NAT
{transip} · dstintf {dstintf}; …" / "the session's records show more than one exit: …". Her kanıt
satırının çıkışı, sorgu motorunun yaptığı gibi NAT adresi ve arayüzüyle saklanan çıkışlardan
bulunur; aynı çıkışın iki arayüzü arasında geçen bir oturum çıkış değiştirmiş sayılmaz ve satırına
bu ek yazılmaz.

Yöntem bölümü her (yanıt, tür) için bir paragraf yazar, toplam on bir paragraf. Her paragraf
neyin çıkış tanımına (ayara), neyin kayıtlara dayandığını ayırır. `{searched}` sonucun saklanan
arama bilgisindeki, gerçekten aranan adreslerdir:

- `public`, doğrudan: genel IP FortiGate'in kendisindedir ve oturum kayıtlarında kaynak NAT
  adresi (transip) olarak görünür. Talepteki port FortiGate'in NAT portuyla (transport)
  karşılaştırılır; port yoksa o adresle çıkan oturumlar aday olarak sıralanır.
- `public` ya da `transip`, servis sağlayıcı NAT'ı: tanıma göre servis sağlayıcının yönlendiricisi
  FortiGate'in bu hattaki NAT adresini ({transips}) ikinci kez çevirir ve bu dönüşümün kaydı
  ARCILOGGER'da yoktur. Arama yalnızca bu çıkışın NAT adresleriyle yapıldıysa paragraf "Arama bu
  çıkışın NAT adresleriyle{ ve arayüzleriyle (…)} sınırlandı." der. Arama tanımsız adresleri de
  kapsadıysa (tanımsız adresleri kapsayan bir çıkış ya da çıkışın NAT adresi o anda görülmediği için
  genişletilen arama) bu cümlenin yerine "Arama {searched} adresleriyle{ ve arayüzleriyle (…)}
  yapıldı." yazılır. Arayüz eki yalnızca arama arayüz koşulunu koruduysa yazılır. Port koruma ya
  kullanılmamıştır ya da yalnızca sıralamada kullanılmıştır (sahada doğrulandı olarak işaretliyse).
  NAT adresi girilmemiş, tanımsız adresleri kapsayan bir çıkışta {transips} yerine "hiçbir çıkışa
  tanımlı olmayan özel ya da paylaşılan NAT adresleri" yazılır. `transip` yanıtında istenen adres
  FortiGate'in bu çıkıştaki kendi NAT adresidir ve eşleşme FortiGate kaydında transip ve transport
  ile yapılır. Kesin eşleşmede 4. bölümün "Açıklama" satırı da "Talepteki adres FortiGate'in bu
  çıkıştaki kendi NAT (WAN) adresidir; …" der: adres paylaşılan (RFC 6598) bir adres olabileceği
  için "genel" denmez. Genel adresi "değişebilir" işaretli bir çıkışın `public` yanıtında paragraf
  `egress_public_dynamic` metnini de taşır: adresin o anda bu hatta ait olduğunu yalnızca servis
  sağlayıcı doğrulayabilir.
- `fallback`, doğrudan çıkış ("Doğrudan hat doğrulanamadı"): istenen adres o anın çevresinde
  FortiGate kayıtlarında NAT adresi olarak görünmüyor ya da yalnızca bir kısmında görünüyor;
  kayıtlar o sırada özel ya da paylaşılan başka kaynak NAT adreslerini de ({seen}, en çok 5)
  gösteriyor. Bu nedenle kesin eşleşme yapılamadı ve {searched} adresleriyle çıkan oturumlar aday
  olarak sıralandı. Servis sağlayıcının yönlendiricisinin o sırada ikinci bir NAT yapmış
  **olabileceği** yazılır, bir olgu olarak değil. "kayıtlar o sırada bu hatta özel ya da
  paylaşılan başka kaynak NAT adresleri ({seen}) de gösteriyor" cümlesindeki "bu hatta" / "on this
  line" yalnızca çıkışın arayüzleri tanımlıysa yazılır: arayüz olmadan adresin hangi hatta
  görüldüğü bilinemez, cümle o zaman yalnızca FortiGate kayıtlarından söz eder.
- `fallback`, servis sağlayıcı NAT'ı çıkışının NAT adresi ("NAT adresi doğrulanamadı"): aynı
  açıklama; köprü modu cümlesi ve "Doğrudan olarak tanımlayın" önerisi yoktur.
- `fallback` eki: arama başka bir çıkışın adreslerine de girdiyse bir ek yazılır. Doğrulanamayan
  çıkışın arayüzü tanımlı değilse: "Aranan adreslerin bir kısmı {other} çıkışına aittir: bu çıkış
  yükseltmede eski ayarlardan aktarıldı ve arayüz tanımlanmadığı için aynı hat sayıldı. Liste o
  hattın kullanıcılarını da içerebilir; her hattın arayüzleri girilince bu olmaz." Arayüzü
  tanımlıysa (öbür çıkış onunla bir arayüzü paylaşıyor): "Aranan adreslerin bir kısmı {other}
  çıkışına aittir: bu çıkış {exit} çıkışıyla aynı arayüzü ({interfaces}) paylaştığı için aynı hat
  sayıldı. Liste o hattın kullanıcılarını da içerebilir."
- `observed` ("Kayıtlarda görülen adres"): adres hiçbir çıkışta tanımlı değil, ama o anın
  çevresinde (±15 dakika) FortiGate kayıtlarında kaynak NAT adresi olarak görünüyor
  (`egress_observed_not_configured`). Adres FortiGate'ten geçmeyen bir çıkışa tanımlıysa metin
  `egress_not_logged_but_seen` olur. Eşleşme FortiGate kaydında yapılır; adresin o tarihte kuruma
  ait olduğunu servis sağlayıcı doğrular.
- `public_unknown`: adres hiçbir çıkışın genel adresi değil. Arama, genel adresi tanımlanmamış
  ya da değişebilen çıkışlarda ({exits}) yapıldı ve bu çıkışlarda servis sağlayıcının ikinci bir
  NAT yaptığı varsayıldı. Adres bu hatlardan birine ait değilse liste ilgisiz kullanıcıları
  gösterebilir. Arama tanımsız adresleri de kapsadıysa ardından "Arama {searched} adresleriyle{ ve
  arayüzleriyle (…)} yapıldı." gelir. Arayüz eki yalnızca aranan her çıkış arayüz koşulunu
  koruduysa yazılır: arayüz tanımlanmamış bir çıkış (örneğin tanımsız adresleri kapsayan çıkış)
  ya da arayüzleri kayıtlarda görülmediği için koşulu kaldırılan bir çıkış bütün arayüzlerde
  arandığından ek yazılmaz.
- `unconfigured`: internet çıkışı tanımlanmamış. Servis sağlayıcının ikinci bir NAT yaptığı
  varsayıldı ve FortiGate'in özel ya da paylaşılan bir NAT adresiyle çıkan bütün kaynak NAT
  oturumları arandı. Genel bir NAT adresiyle çıkan oturumlar FortiGate'in kendi adresinden
  çıktıkları için aranmadı.
- `not_logged`: adres FortiGate'ten geçmeyen bir çıkışa ait ve ARCILOGGER'da bu hattın oturum
  kaydı yok. Rapor sonuç içermez; kayıt ancak hattı yöneten cihazdan alınabilir.
- `transip`, internet çıkışı değil: adres, çıkış tanımına göre FortiGate'in iç ağlara (DMZ, başka
  bir VLAN, VPN ya da şube) yaptığı kaynak NAT'ın adresidir. Oturum aranmadı; rapor sonuç
  içermez.
- `no_egress`: "{address} kurumun tanımlı internet çıkışlarından hiçbirine ait değil ve o sırada
  yapılandırılmış FortiGate kaynaklarının kayıtlarında da görünmüyor; bu nedenle oturum
  aranmadı." Yapılandırılmamış bir göndericinin (örneğin yapılandırılmamış bir HA üyesinin)
  kayıtları adres kontrolünde okunmaz. Adres
  kurumun bir hattına aitse çıkış tanımlanıp sorgu yeniden çalıştırılmalıdır. Ardından "Sorgu
  anında tanımlı çıkışlar: {configured}." gelir: her çıkış "ad (açıklama): tür, genel adresler"
  biçimindedir. Bunlar güvenlik duvarının kendi adresleridir, kişisel veri değildir. Liste
  sorgunun saklanan sonucundan gelir, bugünkü ayardan değil.

Tanımsız adresler de arandıysa `{searched}` "… ve hiçbir çıkışa tanımlı olmayan bütün özel ya da
paylaşılan kaynak NAT adresleri ({an} çevresinde görülenler: …)" / "(seen around {at}: …)" diye
biter. {an} istenen andır ve raporun zaman biçimiyle yazılır (`egress_transip_not_seen` notunun
yazdığı gibi). Böylece listede görülmeyen bir tanımsız adresten gelen bir aday da yazılı aramanın
içinde kalır.

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
- `fallback`: paragrafın yerini yöntem paragrafı tutar. Saklanan çift NAT açıklamaları
  (`double_nat`, `double_nat_no_candidates`) da bu paragrafın yanında ayrıca listelenmez.

`fallback` dışındaki biçimler CGNAT cümlesini ekler: "Genel adres başka müşterilerle
paylaşılıyorsa (CGNAT) aday listesi doğru oturumu içermeyebilir; kesin yanıt için servis
sağlayıcının NAT kaydı gerekir." Sonra sonuç cümlesi ve öneri gelir. Kesin eşleşme için ya (a)
servis sağlayıcının ilgili zamana ait NAT kaydı (genel port → FortiGate çıkış portu) gerekir ya da
(b) servis sağlayıcı yönlendiricisinin köprü (bridge) moduna alınması ve hattın İnternet
çıkışlarında 'Doğrudan' olarak tanımlanması. Bu paragrafın başlığı "Çift NAT / Double NAT"tır.
`fallback` ise "Kesin eşleşme için / For an exact answer" başlığı altında sonuç cümlesini ve
yalnızca (a)'yı yazar: "Kesin eşleşme için (a) servis sağlayıcının ilgili zamana ait NAT kaydı
(genel port → FortiGate çıkış portu) gerekir."; doğrulanamayan çıkış doğrudansa "gerekir"den önce
"ya da hattın o tarihte köprü modunda olduğunun servis sağlayıcıdan doğrulanması" eklenir. "Doğrudan olarak tanımlayın" önerisi zaten doğrudan olan bir çıkış ve bir `transip`
fallback için hiç yazılmaz. Rapor, hattın servis sağlayıcı yönlendiricisinin arkasında olduğunu
hiçbir zaman bir olgu olarak yazmaz. Biçim cümlesi ve CGNAT cümlesi yalnızca talep bir genel IP
adresi içerdiğinde yazılır. İç IP, MAC ya da yalnızca hedefle yapılan bir sorguda çift NAT
paragrafı sonuç cümlesi ve öneriyle sınırlıdır.

**FortiGate DHCP, srcmac ve tanımsız göndericiler (bölüm 4–6):**

- **Kira satırı.** FortiGate DHCP kirasının satırı şunu ekler: "kaynak: FortiGate DHCP kaydı ·
  kira süresi (FortiGate kaydı): {lease} sn · son DHCP kanıtı: {last}", altında İngilizcesiyle
  ("source: FortiGate DHCP record · lease time (FortiGate record): {lease} s · last DHCP evidence:
  {last}"). Süre de son DHCP kanıtı da kiranın istenen anda ya da ondan önceki **kendi son DHCP
  kaydından** gelir. Kirayı bitiren satır ya da sonraki bir yenileme kullanılmaz. Kayıt süre
  yazmadıysa "ayarlardaki süre" yazılır. Bu ekler yalnızca kaynağı FortiGate DHCP olan kiralarda
  bulunur; MikroTik kiralarının satırı değişmez. Bölüm 6'da bu kayıt türü "FortiGate DHCP kaydı /
  FortiGate DHCP record", kira zaman çizelgesine girmemiş satır "FortiGate olay kaydı / FortiGate
  event record" diye adlandırılır; eski kayıt türlerinin adları eskisi gibi yalnızca Türkçedir.
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
  oturum kaydının srcmac alanından gelebilir. Satırın adı "MAC adresi / MAC address"tir; değeri
  MAC adresinden sonra şudur: "MAC adresi (kısmi kanıt):
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
(`fg_traffic`, `fg_dhcp`, `mt_dhcp`, `mt_arp`, `leases`) gelir; veritabanı türetilmiş veridir,
kanıt ham kayıt satırıdır. Rapor oluşturulurken her kanıt satırı ingest hizmetinin kullandığı
ayrıştırıcıyla yeniden okunur ve raporda gösterilen değerlerle karşılaştırılır:

- oturumun kaynak/hedef adres ve portları, protokol, FortiGate oturum no, NAT adresi/portu;
- srcmac'e dayanan bir zincirde, gösterilen oturum satırının kaynak IP adresi (`srcip`) ve
  kaynak MAC adresi (`srcmac`);
- IP-MAC kaydının (MikroTik DHCP, FortiGate DHCP, ARP-LOG) IP ve MAC adresi;
- her satırda kayıt türü ve zamanı.

Bir kirayı bitiren DHCP satırı (MikroTik ya da FortiGate) kirayla paylaştığı alanı ve yazdığı IP
adresini taşır. Kiranın IP adresini taşıyorsa yalnızca IP karşılaştırılır: adresi başka bir cihaz
aldı ya da aynı adres ve MAC öbür DHCP kaynağının kaydıyla sürdü. Başka bir IP adresi taşıyorsa
satırın yazdığı o IP adresi ve kiranın MAC adresi karşılaştırılır: aynı MAC adresi başka bir adres
aldı. Böylece doğru bir zincir yanlış bir "uyuşmuyor" uyarısı almaz; veritabanında üçüncü bir adrese
değiştirilmiş bir satır ise "uyuşmuyor" diye yazılır. Bu üç durumun hepsinde kira satırının bitiş
nedeni "sonraki bir DHCP atamasıyla kapandı" / "closed by a later DHCP assignment" diye yazılır:
kirayı bitiren kayıt başka bir cihazın, aynı MAC adresinin başka bir adresinin ya da öbür DHCP
kaynağının kaydı olabilir; rapor bunlardan birini olgu olarak yazmaz. Kira zaman çizelgesine
girmemiş FortiGate DHCP satırları (bölüm 6'da notlu) karşılaştırılmaz.
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

**Yükseltmeden önce yapılmış sorgular:** rapor ilk indirmede üretilir. İnternet çıkışlı sürüme
yükseltmeden önce yapılmış bir sorgunun saklanan sonucunda çıkış, `dstintf` ve kira kaynağı bilgisi
yoktur. Böyle bir sorgunun raporu 4. bölümde ve eşleşmelerinde çıkış satırı, yanıt türüne göre
yöntem paragrafı ve FortiGate kira ekleri olmadan yazılır. Çift NAT belirlenmiş bir yanıt, talep
bir genel IP adresi içeriyorsa, "Çift NAT varsayımı" biçimini CGNAT cümlesiyle alır. Servis
sağlayıcı modemini bir olgu olarak anlatan eski cümle yeni üretilen hiçbir raporda yazılmaz; daha
önce saklanmış raporlar ise baytlarını korur. Geri almadan (`-Rollback`) önce, yeni sürümde yapılan sorguların
raporlarını indirin: önceki sürüm, ilk indirmede raporu kendi biçimiyle üretir. Bu biçimde çıkış
satırları, tanımsız gönderici uyarısı, "kısmi kanıt" etiketi ve FortiGate kira satırı yoktur
([KURULUM.md](KURULUM.md) bölüm 13).

**Raporun okunması (okuma tablosu):** denetçi PDF'i okur. Bu tablo, PDF'te görülen her çıkış
etiketi ve yöntem bölümünde ya da bir zincirin yanında yazılan her açıklama için üç şeyi ayırır:
kayıtta kesin olan, kurumun ayarlarına dayanan ve varsayılan. İlk sütun PDF'in kendi etiketlerini
ve arkalarındaki açıklama anahtarlarını (`…`) kullanır. Her hücre önce Türkçe, eğik çizgiden sonra
İngilizce yazılır.

| PDF'te görülen / What the PDF shows | Kesin olan (kayıtta) / Exact (in the records) | Ayarlara dayanan / Configured | Varsayılan / Assumed |
|---|---|---|---|
| Çıkış "Genel IP", doğrudan, kesin eşleşme / exit "Public IP", direct, exact | FortiGate oturum kaydı: transip ve transport / the FortiGate session record: transip and transport | genel IP'nin bu hatta ait olduğu / that the public IP belongs to this line | — |
| Çıkış "Genel IP" ya da "NAT adresi", servis sağlayıcı NAT'ı / ISP NAT | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın ikinci NAT yaptığı, genel adresi / that the line does a second NAT, its public address | genel port → FortiGate portu eşlemesi yoktur; liste adaydır / no public port → FortiGate port mapping; the list is candidates |
| "Genel adresi değişebilir" uyarısı / "public address may change" (`egress_public_dynamic`) | FortiGate oturumları ve NAT adresleri / the FortiGate sessions and NAT addresses | hattın genel adresi ve değişebilir işaretlendiği / the line's public address and that it is marked as changing | adresin o anda bu hatta ait olduğu; yalnızca servis sağlayıcı doğrulayabilir / that the address was this line's at that moment; only the ISP can confirm it |
| "Doğrudan hat doğrulanamadı" (fallback) | o anın çevresinde istenen adresin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü ve o hatta özel ya da paylaşılan başka bir NAT adresinin de göründüğü / that around the moment the requested address did not appear in the records, or only part of the time, and another, private or shared NAT address did on that line | hattın tanımı / the line's definition | o anda ikinci NAT yapılmış olabileceği; liste başka bir hattın kullanıcılarını içerebilir (paragraf söyler) / that a second NAT may have happened; the list may hold another line's users (the paragraph says so) |
| "NAT adresi doğrulanamadı" (fallback of an ISP NAT address) | o anın çevresinde istenen NAT adresinin kayıtta görünmediği ya da yalnızca bir kısmında göründüğü / that around the moment the requested NAT address did not appear in the records, or only part of the time | hattın tanımı ve NAT adresi / the line's definition and NAT address | listenin bu hattın o anki oturumlarını içerdiği; başka adreslerle çıkan oturumlar da aday olabilir / that the list holds this line's sessions of that moment; sessions that left with other addresses may be candidates too |
| "Kayıtlarda görülen adres" (observed) | FortiGate'in o adresi kaynak NAT adresi olarak kullandığı / that the FortiGate used the address as source NAT | — | adresin o tarihte kuruma ait olduğu (servis sağlayıcıdan doğrulanır) / that the address belonged to the organisation then (confirmed with the ISP) |
| "Genel adresi bilinmeyen hatlar", "Çıkış tanımlı değil" | özel ya da paylaşılan NAT adresiyle çıkan FortiGate oturumları / the FortiGate sessions that left with a private or shared NAT address | — | ikinci NAT; liste ilgisiz kullanıcılar içerebilir / a second NAT; the list may hold unrelated users |
| "Kısmi kanıt: srcmac" (`srcmac_only`) | FortiGate'in aygıt tanımasının kaynak IP'yi bu MAC ile ilişkilendirdiği / that the FortiGate's device identification associated the source IP with this MAC | arayüzün listelendiği / that the interface is listed | MAC'in oturumu gönderdiği kanıtlanmaz; güven düşük / not proof that the MAC sent the session; low confidence |
| Düşük güvenli srcmac çakışması / low srcmac conflict (`srcmac_mismatch`) | iki farklı MAC kaydı / two different MAC records | — | o anda iki olası cihaz vardır; ikisi de gösterilir / there are two possible devices; both are shown |
| "Kayıt biçimi sahada doğrulanmadı" / "verified by … on …" (`fortigate_format_unverified`, `fortigate_format_verified`) | FortiGate DHCP kaydı / the FortiGate DHCP record | biçimin doğru okunduğu (doğrulandıysa kim, ne zaman; sorgu anındaki durum) / that the format is read correctly (if verified, by whom and when; as at the time of the lookup) | doğrulanmadıysa biçim FortiOS belgelerine dayanır / if not verified, the format rests on the FortiOS documents |
| "Kira zaman çizelgesine girmedi; zincir kurulmadı" (`fortigate_dhcp_not_converted`) | ham FortiGate DHCP kaydı ve mührü (iç IP sorgusu ve kesin eşleşme) / the raw FortiGate DHCP line and its seal (internal lookup and exact match) | — | zincir kurulmadı ve güven düzeyi verilmez; kayıt yalnızca o anı kapsayan bir kaydın var olduğunu gösterir, bir eşleştirme değildir / no chain was built and no confidence is given; the line only shows that a record covering the moment exists, it is not an attribution |
| "Kira yenilemesi görülmedi" (güven düşük) / no renewal seen (low) (`lease_renewal_not_seen`) | FortiGate DHCP Ack kaydı ve zamanı (kiranın istenen andan önceki kendi son kaydı) / the FortiGate DHCP Ack and its time (the lease's own last record before the moment) | kira süresi (kayıttan ya da ayardan) / the lease time (logged or configured) | Ack sahibinin adresi istenen anda hâlâ kullandığı; kayıtlar bunu gösteremez, güven bu yüzden düşük / that the Ack's holder still used the address at the requested moment; the records cannot show it, hence low confidence |
| "Kiradan sonra uygulanmamış DHCP kaydı" (güven düşük) / DHCP record after the lease not applied to it (low) (`fortigate_dhcp_not_converted_since_lease`) | DHCP kira kaydı (kiranın istenen andan önceki kendi son kaydı) ve ondan sonra o adres ya da kiranın MAC adresi için kira zaman çizelgesinin bu kiraya uygulamadığı bir FortiGate DHCP kaydının var olduğu (kayıt gösterilmez) / the DHCP lease record (the lease's own last record before the moment) and that a FortiGate DHCP record of that address or of the lease's MAC address after it was not applied to this lease by the lease timeline (the record is not shown) | kira süresi / the lease time | kira sahibinin adresi istenen anda hâlâ kullandığı; sonraki kayıt bir bırakma, başka bir cihaza verilme ya da MAC adresinin başka bir adres alması olabilir, güven bu yüzden düşük / that the holder still used the address at the moment; the later record may be a release, a hand-over to another device or the MAC taking another address, hence low confidence |
| "Tanımlı bir ağda değil" (güven düşük) / not in a configured network (low) (`fortigate_dhcp_no_network`) | FortiGate DHCP kira kaydı / the FortiGate DHCP lease record | kurumun tanımlı ağları (adres hiçbirinde değil) / the organisation's configured networks (the address is in none) | kira sahibinin adresi o anda hâlâ kullandığı; adresin reddedilip başka bir istemciye geçtiği yalnızca tanımlı ağlarda anlaşılır, güven bu yüzden düşük / that the holder still used the address then; a declined address is only detected in configured networks, hence low confidence |
| "Aynı ağda iki DHCP kaynağı" (güven düşük) / two DHCP sources on one network (low) (`dhcp_sources_overlap`) | o ağda o anın çevresinde iki DHCP kaynağının kayıtları / the records of two DHCP sources in that network around the moment | ağın (VLAN alt ağının) tanımı (tanımlı bir ağda olmayan adreste adresin kendisi) / the network's (VLAN subnet's) definition (for an address in no configured network, the address itself) | zincirin dayandığı kaydı yazan sunucunun o anda adresi gerçekten verdiği; kayıtlar iki sunucudan hangisinin geçerli olduğunu gösteremez / that the server whose record the chain rests on really gave the address then; the records cannot show which of the two applied |
| "Gönderici sonradan yapılandırıldı" / sender configured later (`source_configured_later`) | kayıtların o göndericiden geldiği / that the records came from that sender | göndericinin adresinin sonradan bir FortiGate kaynağı olarak tanımlandığı / that the sender's address was configured as a FortiGate source later | kayıtların alındıkları sırada da kurumun FortiGate'inden geldiği / that the records came from the organisation's FortiGate when they arrived as well |
| "Kaynak saati kayık" (güven düşük) / source clock off (low) (`source_clock_offset`) | kayıtlardaki zaman ile alma zamanı arasındaki ölçülen fark / the offset measured between the record times and the receive times | sınır: 120 saniye; yalnızca oturum araması için ölçülen bir kaynakta sorgu toleransı daha büyükse tolerans / the limit: 120 seconds; the lookup tolerance when larger, for a source measured only for the session search | doğru oturumun kaymış zaman aralığında olduğu; bulunan oturum başka biri olabilir / that the right session lies in the shifted window; the session found may be someone else's |
| "Oturum aranmadı" (Çıkış yok / no exit) | — | sorgu anındaki çıkış listesi (PDF'te yazılı) ve hatların türü / the exit list at the time of the lookup (printed in the PDF) and the lines' types | çıkış listesinin olay tarihinde de tam olduğu / that the exit list was complete on the date of the event too |
| "Oturum aranmadı" (FortiGate'ten geçmeyen hat / line not passing the FortiGate) | — | hattın FortiGate'ten geçmediği / that the line does not pass the FortiGate | hattın olay tarihinde de FortiGate'ten geçmediği / that it did not pass it on the date of the event either |
| "Oturum aranmadı" (internet çıkışı olmayan NAT adresi / not an internet exit) (`egress_internal`) | — | adresin FortiGate'in iç ağlara yaptığı NAT'ın adresi olarak tanımlandığı / that the address is defined as the FortiGate's NAT towards internal networks | adresin olay tarihinde de bir internet çıkışı olmadığı / that it was not an internet exit on the date of the event either |
| "Tanımsız gönderici" uyarılı eşleşme / match with the unconfigured-sender warning (`session_from_unknown_source`) | yapılandırılmamış bir göndericinin oturum kaydı / a session record of an unconfigured sender | — | kaydın kurumun FortiGate'inden geldiği doğrulanmadı; kanıt olarak kullanılmamalıdır / not confirmed that the record came from the organisation's FortiGate; not to be used as evidence |
| "Çıkış tanımı olaydan sonra değişti" / exit changed after the event (`egress_changed_after_event`) | — | bugünkü tanım (PDF'te yazılı); önceki tanımlar kurumun denetim kaydında / today's definition (printed in the PDF); earlier definitions in the organisation's audit log | o tarihteki tanımın aynı olduğu / that the definition was the same on that date |

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
report shows from the database (session addresses, ports, protocol, session number, NAT; `srcip` and
`srcmac` of a srcmac chain's session line; IP and MAC of an IP-MAC record, MikroTik or FortiGate
DHCP or ARP-LOG, where the line that ended a lease claims the field it shares with the lease and the
IP it names; record type and time): a difference is printed in red, and that result is shown as
"contradicted by its evidence", never as an exact match or with a confidence. The check finds a
database changed after ingest, not a defect of the parser itself. With `lookup.report_timestamp` the
PDF's hash is time-stamped (1 kontör), the `.zd` is stored next to it and the PDF names the provider
and warns when it is not the Kamu SM production server (it never states that a stamp is legally
valid). Verify with `certutil -hashfile … SHA256` / `sha256sum`, and the token with Zamane `-CC` or
`openssl ts -verify`.

**Internet exits, FortiGate DHCP and srcmac (Wave 3).** Section 4 of a public-IP report has an
exit row, "Çıkış / Exit": "{label TR} / {label EN} — {name} ({label}), {mode TR} / {mode EN}"
(several exits joined with "; "), with the labels of one
TR/EN table that the web and the reading table share ("Public IP", "NAT address", "Direct line not
confirmed", "NAT address not confirmed", "Address seen in the logs", "Lines with unknown public
address", "No exits defined", "No exit"; modes "Direct", "ISP NAT", "Does not pass the FortiGate",
"Not an internet exit"). The method section prints one paragraph per (resolution, mode), eleven in
all, each separating what the exit definition states from what the logs show; the `no_egress`
paragraph speaks of "the records of the configured FortiGate sources" and lists the exits defined
at the time of the lookup. For a `transip` answer the mode row calls the requested address the
FortiGate's own NAT (WAN) address on that exit, never a public one. The ISP NAT paragraph says "The
search was limited to this exit's NAT addresses" only when it was; when the search also covered the
addresses no exit claims (a catch-all exit, or the widened search) it says "The search covered
{searched}" instead, as the `public_unknown` paragraph then does (the unclaimed addresses "seen
around {at}", the requested moment); there "on the interfaces (…)" is printed only when every exit
searched kept its interface condition (an exit without interfaces, or one whose interfaces were not
seen in the logs, was searched on every interface). The direct `fallback` paragraph says "on this
line" only for an exit with interfaces, and its addition names the other exit as converted without
interfaces or as sharing an interface. The double-NAT paragraph is never printed for resolution
`transip`. It reads "Double NAT" for a configured ISP NAT exit and "Double NAT assumed" for
`unconfigured` and `public_unknown`, with the CGNAT sentence; for a `fallback` the method paragraph
stands in for it, and "For an exact answer" prints the result sentence and advice (a) alone (with
the bridge-mode check for a failed direct exit). The variant sentence and the CGNAT sentence appear
only when the request names a public IP. The report never states as a fact that a line sits behind
an ISP router, and never advises defining a line as Direct when it already is, or for a `transip`
fallback. Method notes (clock offset, changing public address, internal NAT, exits changed after the
event, the FortiGate DHCP format note) come from the stored outcome, never from today's settings.

Every match prints its exit and `dstintf` ("-" when no exit claims the NAT address, which happens
only when a public IP was requested; without one, "exit not determined (no public IP in the
request)"; for a session that changed exit, the other exit when the stored outcome can tell it, an
unclaimed one as "a NAT address no exit claims", else the NAT address and `dstintf` of each of its
cited lines; each line's exit is found from its NAT address and interface as the lookup engine does,
so a session that moved between two interfaces of one exit did not change exit); a match whose
records came from an unconfigured sender gets a "UYARI / Warning" row. `no_egress`, `not_logged` and
an address that is not an internet exit print "No session was searched: …" instead of the generic
no-match text. A srcmac step, the row "MAC adresi / MAC address", reads "MAC address (partial
evidence)" and never says that the MAC sent the session; a srcmac conflict prints both MACs, the
alternative under "Possible other chain (srcmac, partial evidence)". A FortiGate DHCP lease row adds
its source, lease time and last DHCP evidence, both from the lease's own last record at or before
the moment (section 6 labels such lines "FortiGate DHCP kaydı / FortiGate DHCP record" and
unconverted ones "FortiGate olay kaydı / FortiGate event record"). A lease ended by a later DHCP
record, whether another device took the address, the lease's MAC took another address or the other
DHCP source recorded the same pair, reads "sonraki bir DHCP atamasıyla kapandı" / "closed by a later
DHCP assignment"; a `low` chain prints its reason beside it (`lease_renewal_not_seen`,
`fortigate_dhcp_not_converted_since_lease`, `fortigate_dhcp_no_network`). An unconverted FortiGate
DHCP line is cited in section 6 only for an internal lookup or an exact match, marked "not in the
lease timeline; no chain built; no confidence given"; a candidate report carries the text alone. Raw
session lines of an interface with FortiGate device identification show `srcmac`, the device name,
OS and passively learned user names as logged, other people's candidate sessions included.

A lookup made before the upgrade to this version is printed, on its first download, without exit
rows, per-resolution paragraphs or FortiGate lease additions; a double-NAT answer to a public-IP
request reads "Double NAT assumed" with the CGNAT sentence. Reports already stored keep their bytes.
Download the reports of lookups made in the new version before a `-Rollback`. The reading table
(exact in the records / configured / assumed) is bilingual and sits in the Turkish part above.

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
- The FortiGate DHCP record format comes from the FortiOS documentation and is verified on site
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) section 2.4, again after every FortiOS upgrade). Until
  `dhcp.fortigate_format_verified` is switched on, reports say "sahada doğrulanmadı"; a PDF keeps
  the state at the time of its lookup.
- The Wave 3 wording (the exit and method paragraphs, srcmac as partial evidence, the
  device-identification fields that raw lines carry, also in other people's candidate sessions)
  needs the same legal review, together with the matching rows of the privacy-notice draft
  ([legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md)).
- Unconverted FortiGate DHCP lines are looked for over 7 days before the moment (longer only when
  the longest lease window is longer), not the 100 days the plan named, to keep the lookup within
  its time budget: an older such line is not named. An index on the event lines' address would
  restore the longer window (an open question of the Wave 3 contract).
