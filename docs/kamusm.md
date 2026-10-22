# Kamu SM zaman damgası / Kamu SM time stamping

> Durum: **taslak (M1, MVP'de güncellendi)**. Bilinen gerçekler Kamu SM'nin kendi sayfalarından alınmıştır
> (12.10.2026). "Açık" olarak işaretli konular Kamu SM **test sunucusunda** netleştirilecek ve bu
> belge güncellenecektir.

## 1. Bilinen gerçekler

| Konu | Bilgi | Kaynak |
|---|---|---|
| Üretim sunucusu | `http://zd.kamusm.gov.tr`, port 80 (düz HTTP) | Kamu SM SSS |
| Test sunucusu | `http://tzd.kamusm.gov.tr`, port 80 — **yasal geçerliliği yok** | Kamu SM istemci sayfası |
| Güvenlik duvarı | Yalnızca `Content-Type: application/timestamp-query` kabul edilir; diğer istekler bağlantı sıfırlamasıyla düşer (tarayıcı/curl testleri çalışmaz) | Kamu SM SSS |
| Protokol | RFC 3161 uyumlu (CWA 14167-1, ETSI TS 101 861); yalnızca verinin özeti gönderilir | Kamu SM SSS |
| Kimlik doğrulama | Müşteri no + parola; standart dışı `identity` HTTP başlığı (yalnızca topluluk araçlarında tarif edilmiş) | kamusm-go (lisanssız, yalnızca davranış incelendi) |
| Resmî istemci | "Zamane" konsol istemcisi `tss-client-console-3.1.33.jar`, **Java 1.8** gerekir; `License_62_GENEL_ZD_ISTEMCI.zip` ile birlikte, SHA-256 değerleri sayfada yayımlanır | Kamu SM "Ücretsiz Zaman Damgası İstemci Yazılımı" |
| Kontör | Normal zaman damgası 1 kontör; en az 10.000 kontör satın alınır; kontörün süresi dolmaz | Kamu SM başvuru sayfası |
| Satın alma | `zdportal.kamusm.gov.tr` → "kurumsal" başvuru → sipariş → ödeme → kontör yüklenir | Kamu SM başvuru sayfası |
| Kök sertifikalar / SİL | `sertifikalar.kamusm.gov.tr` | Kamu SM |
| İletişim | 444 5 576, bilgi@kamusm.gov.tr | Kamu SM |

Konsol istemcisi komutları (Kamu SM sayfasındaki kullanım):

```text
java -jar tss-client-console-3.1.33.jar -z  <dosya> <sunucu> <port> <müşteri no> <parola> [proxy ip] [proxy port] [proxy kullanıcı] [proxy parola] [sha-256]
java -jar tss-client-console-3.1.33.jar -ZC <...aynı...>        # damga al + sunucu sertifika imzasını kontrol et
java -jar tss-client-console-3.1.33.jar -c  <dosya> <dosya>.zd   # damgayı kontrol et
java -jar tss-client-console-3.1.33.jar -CC <dosya> <dosya>.zd   # + sunucu sertifikası kontrolü
java -jar tss-client-console-3.1.33.jar -k  <sunucu> <port> <müşteri no> <parola>   # kontör sorgula
```

## 2. ARCILOGGER bunu nasıl kullanır?

- Her gün **bir** zaman damgası: günlük manifest (`manifest-YYYY-MM-DD.json`) damgalanır →
  yılda 365 kontör; 10.000 kontör yaklaşık 27 yıl yeter.
- Sağlayıcılar (`[tsa] provider`):
  - `zamane` — resmî konsol istemcisi; bu sürümde Kamu SM üretim sunucusundan
    (`zd.kamusm.gov.tr`) damga almanın **desteklenen tek yolu**. ARCILOGGER bir damgayı yalnızca
    Kamu SM üretim sunucusundan alınmışsa **ve** doğrulanan sertifika zinciri
    `[tsa] legal_root_fingerprints` listesindeki bir köke ulaşıyorsa yasal nitelikli
    (`legal_grade`) gösterir; test sunucusunun damgalarının yasal geçerliliği yoktur (Kamu SM).
    Damgaların hukuki değerlendirmesi için hukuk müşavirine danışın.
  - `rfc3161` — genel RFC 3161 istemcisi; `identity` başlığı göndermez, Kamu SM'de çalışmaz;
    yalnızca geliştirme.
  - `mock` — test/demo; kendi test sertifika otoritesi; **asla yasal değildir**. Köküne yalnızca
    `provider = "mock"` iken güvenilir: üretim sunucusunda kullanılmaz (bölüm 4 adım 1).
  - `native` (Kamu SM'ye doğrudan, `identity` başlığıyla) — henüz yok; test hesabıyla Zamane
    sonuçlarıyla karşılaştırılarak geliştirilecek ve bayrak arkasında kalacak.
- Zamane istemcisinin ekran mesajları ve çıkış kodları belgelenmemiştir. ARCILOGGER bunlara
  güvenmez: bir damga, ancak `.zd` dosyası oluşmuşsa, ayrıştırılabiliyorsa **ve kendi bağımsız
  doğrulayıcımızdan geçerse** kabul edilir (CMS imzası, ESSCertID/ESSCertIDv2, `id-kp-timeStamping`
  EKU, sabitlenmiş Kamu SM köklerine kadar zincir, dosya özeti).
- Müşteri no ve parola şifreli saklanır (`arcilogger-core secret set kamusm.customer` /
  `kamusm.password`); hiçbir log ve çıktıda görünmez (istemci çıktısında `***` ile maskelenir).
- **Kalan risk:** Zamane parolayı komut satırında alır. Windows'ta yalnızca yöneticiler başka bir
  hesabın işlem komut satırını okuyabilir. Görev, Windows'un paylaşılan LOCAL SERVICE hesabıyla
  çalışır: aynı hesapla çalışan başka bir Windows hizmeti bu komut satırını görebilir (ve
  `secret.key` ile gizli depoyu da açabilir). Görevlere ayrı bir kimlik verilmesi açık bir sahip
  kararıdır ([ADR-0007](adr/0007-windows-installer.md) bölüm 8).

## 3. Açık konular (test sunucusunda netleşecek)

| # | Soru | Neden önemli | Durum |
|---|---|---|---|
| K1 | `.zd` dosyası `TimeStampResp` mı, yalnızca `TimeStampToken` mı? | Doğrulayıcı ikisini de kabul ediyor; denetçi kılavuzundaki `openssl` komutu buna göre değişir | Açık |
| K2 | Hangi özet algoritmaları kabul ediliyor (SHA-256 kesin, SHA-512?) | `tsa.digest` ayarı | Açık |
| K3 | Hata metinleri: yanlış parola, kontör yok ("Not enough credit"), hatalı istek | Hata sınıflandırması (`zamane.py`) | Açık — şimdilik anahtar kelimeyle |
| K4 | Kontör sorgusunun çıktı biçimi | `tsa.credit_regex` (varsayılan: "kontör/credit" kelimesinden sonraki ilk sayı); yanlışsa test **yüksek sesle** başarısız olur | Açık |
| K5 | `certReq` dikkate alınıyor mu (TSA sertifikası belirtece ekleniyor mu)? | Sertifika yoksa doğrulama için ayrıca indirilmesi gerekir | Açık |
| K6 | TSA sertifikasının EKU uzantısı kritik mi? | RFC 3161 kritik olmasını ister (varsayılan: değilse hata). Kamu SM profili kritik değilse `tsa.require_critical_eku = false`: seçim her manifeste (`tsa.require_critical_eku`) mühürlenir, doğrulayıcılar o günler için uyarı verir | Açık |
| K7 | İzin verilen istek sıklığı / hız sınırı | Yeniden deneme planı | Açık |
| K8 | `identity` başlığının tam ASN.1 yapısı; PBKDF2 tuzu IV ile aynı mı? | `native` sağlayıcı | Açık |
| K9 | Çıkış kodları ve konsol çıktısı dili/kod sayfası (cp1254?) | Günlükler | Açık |
| K10 | Kök ve ara sertifikaların indirileceği kesin adresler, SİL adresleri | `tsa.roots_dir`, SİL kontrolü (henüz yok) | Açık |

## 4. Bağlantı testi (kurulumda)

1. Test kullanıcısı isteyin (aşağıdaki e-posta). Kullanıcı gelene kadar ne olacağına ilk başarılı
   mühürden **önce** karar verin ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3): zinciri canlıya geçiş
   gününde başlatmak (`[seal] first_day`) ya da test süresinde de mühürlemek; seçim sahibindir.
   Test süresinde mühürlenen günler kalıcı olarak test damgalı kalır (gün bitiminden 48 saatten
   sonra damgalananlar ayrıca kalıcı olarak "geç damgalandı"); doğrulanabilmeleri için test
   damgalarının bağlandığı kök, o günler var olduğu sürece `kamusm-roots` klasöründe kalmalıdır
   (K10, sahada doğrulayın). **Sunucuda `provider = "mock"` kullanmayın:** mock'un köküne yalnızca
   `provider = "mock"` iken güvenilir; sağlayıcı Kamu SM'ye çevrilince mock damgalı günler
   doğrulanamaz (`R20-verify`). Bir deneme sunucusunda mock kullanıldıysa deneme verisini üretime
   taşımayın ve gerçek sağlayıcıya geçtikten sonra `state\mock-tsa` klasörünü (mock'un özel
   anahtarları) silin.
2. Zamane ZIP'ini Kamu SM sayfasından indirin, sayfadaki SHA-256 ile karşılaştırın, jar dosyasını
   `C:\ProgramData\ARCILOGGER\zamane\` altına koyun. Java 8 (Eclipse Temurin 8 JRE) kurun.
3. Kamu SM kök sertifikalarını `sertifikalar.kamusm.gov.tr` adresinden indirin,
   `C:\ProgramData\ARCILOGGER\kamusm-roots\` altına koyun; SHA-256 parmak izlerini Kamu SM'nin
   yayımladığı değerlerle karşılaştırın ve kayda geçirin. Karşılaştırdığınız parmak izlerini
   `tsa.legal_root_fingerprints` listesine yazın: ARCILOGGER bir günü (rapor, mühür takvimi, kanıt
   paketi) ancak damga zinciri bu köklerden birine ulaşıyorsa "yasal sınıf" gösterir; liste boşken
   hiçbir gün öyle gösterilmez. Bu durumda Kamu SM üretim damgalı günler test damgası olarak da
   gösterilmez: rapor, mühürleme sayfası ve kanıt paketi "Kamu SM üretim damgası olarak beyan
   edilmiş; kök parmak izi tanımlı değil" der ve karşılaştırılacak parmak izini verir.
   `tsa-test` çıktısındaki `root_sha256` damganın bağlandığı kökü gösterir.
   **Bu adım `tsa-test`'ten önce gelir:**
   `tsa-test` önce damgayı alır (1 kontör), sonra onu bu köklerle doğrular; kökler yoksa kontör
   harcanmış olarak "no pinned root certificates" hatasıyla biter. Gece mühürlemesi de kökler
   olmadan çalışmaz.
4. Kimlik bilgilerini kaydedin (gizli depo anahtarını kurulum programı `secret init` ile
   oluşturur): `secret set kamusm.customer`, `secret set kamusm.password`. Komutlar yönetici
   PowerShell'inde tam yoluyla çalışır:
   `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" secret set kamusm.customer`.
5. `arcilogger-core.exe credit` → kontör okunuyor mu? (K4)
6. `arcilogger-core.exe tsa-test` → 1 KB rastgele dosya damgalanır ve doğrulanır; çıktı genTime,
   seri no, TSA adı ve sertifika geçerliliğini gösterir. Oluşan `.zd` dosyası K1/K5/K6
   sorularının cevabıdır; regresyon testleri için örnek olarak saklanır.
7. Başarılıysa üretime geçin ([KURULUM.md](KURULUM.md) bölüm 10, "Üretime geçiş"): kontör satın
   alma, üretim hesabının müşteri no ve parolasını `secret set` ile yeniden kaydetme (test
   bilgilerinin üretimde geçerli olup olmadığı doğrulanmadı), `[tsa]` içinde
   `url = "http://zd.kamusm.gov.tr"` ve Kamu SM kökünün SHA-256 parmak izini
   `legal_root_fingerprints` listesine yazma (liste boşsa hiçbir damga yasal nitelikli
   gösterilmez), ardından `Restart-Service ArciApi` — API yapılandırmayı yalnızca başlarken
   okur. Kırmızı "test damgası" uyarısı ancak yeniden başlatmadan sonra kaybolur.

### Test kullanıcısı talep e-postası (taslak)

```text
Kime: bilgi@kamusm.gov.tr
Konu: Zamane test kullanıcı talebi

Merhaba,

Kurum içi log kayıtlarımızı günlük olarak zaman damgasıyla mühürlemek için Zamane konsol
istemcisini test etmek istiyoruz. Test sunucusu (tzd.kamusm.gov.tr) için test kullanıcısı
talep ediyoruz.

Firma adı        : <...>
Vergi no         : <...>
Adres            : <...>
Sabit telefon    : <...>
İrtibat kişisi   : <...>
Cep telefonu     : <...>
İrtibat e-posta  : <...>

Saygılarımızla,
<ad soyad>
```

## 5. Bağımsız doğrulama (denetçi için)

```bash
# .zd bir TimeStampResp ise:
openssl ts -verify -in manifest-2026-10-12.json.zd -data manifest-2026-10-12.json -CAfile kamusm-kok.pem
# yalnızca TimeStampToken ise:
openssl ts -verify -token_in -in manifest-2026-10-12.json.zd -data manifest-2026-10-12.json -CAfile kamusm-kok.pem
# Kamu SM'nin kendi kontrolü:
java -jar tss-client-console-3.1.33.jar -CC manifest-2026-10-12.json manifest-2026-10-12.json.zd
```

ARCILOGGER'ın test belirteçleri `openssl ts -verify` ile otomatik testlerde de doğrulanır.

## 6. Uzun süreli geçerlilik

Zaman damgası, TSA sertifikası geçerli olduğu sürece geçerlidir. Her gün durum kaydına
`tsa_cert_not_after` yazılır. Sertifikanın bitimine 180 gün kala yenileme manifesti (etkilenen
manifestlerin ve belirteçlerin özetleri) yeni bir damgayla mühürlenecektir — **M6 kapsamında**.
