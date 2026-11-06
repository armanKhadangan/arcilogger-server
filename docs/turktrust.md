# TÜRKTRUST zaman damgası / TÜRKTRUST time stamping

> Durum: **taslak (Wave 5)**. TÜRKTRUST hakkında bilinenler TÜRKTRUST'ın e-postasından ve ürün
> sahibinin 2.11.2026 tarihli kararından gelir. **"Sahada doğrula"** ile işaretli konular sahada,
> TÜRKTRUST hesabıyla netleştirilecek ve bu belge güncellenecektir. English: [below](#english).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Sağlayıcılar, yasal nitelik, yeniden damgalama ve iptal listeleri:
> [ZAMAN-DAMGASI.md](ZAMAN-DAMGASI.md). Kamu SM için: [kamusm.md](kamusm.md).

TÜRKTRUST, Kamu SM'nin yanında ikinci zaman damgası sağlayıcısıdır (`[tsa] provider = "turktrust"`).
ARCILOGGER ona RFC 3161 ile, kullanıcı adı ve parolayla (HTTP Basic kimlik doğrulaması) bağlanır;
Java'ya, Zamane istemcisine ya da TÜRKTRUST'ın ücretli Spira kitaplığına gerek yoktur. Kamu SM'de
olduğu gibi sunucudan yalnızca özet (hash) çıkar; log satırı hiçbir zaman gönderilmez.

## 1. Bilinenler ve yasal nitelik

| Konu | Bilgi | Kaynak |
|---|---|---|
| Üretim sunucusu | `zd.turktrust.com.tr`; adresin **yolu** bilinmiyor, varsayılan `https://zd.turktrust.com.tr/` (T1) | TÜRKTRUST e-postası |
| Portlar | 80 ve 443; ARCILOGGER varsayılan olarak HTTPS (443) kullanır, düz HTTP (80) yalnızca açık bir yönetici seçimiyle (T5) | TÜRKTRUST e-postası |
| Protokol | RFC 3161, HTTP Basic kimlik doğrulaması (kullanıcı adı + parola); TÜRKTRUST'ın yanlış kimlik bilgisine cevabı **sahada doğrula** (T2, bölüm 3 adım 7) | Ürün kararı (D12) |
| Kontör sorgusu | **Yok.** Bakiye TÜRKTRUST'ın kendi portalındadır | TÜRKTRUST |
| Portal adresi, düşük bakiye e-postası | **TÜRKTRUST'a sorun** | — |
| Kök sertifikalar ve iptal listeleri | TÜRKTRUST'ın "kök sertifikaları ve iptal listeleri" sayfası; kesin adresler **sahada doğrula** (T4) | TÜRKTRUST |
| Başvuru, fiyat, sözleşme, deneme hesabı | **TÜRKTRUST'a sorun** | — |

**Yasal nitelik (legal grade).** TÜRKTRUST üretim sunucusunun (`zd.turktrust.com.tr`) damgaları,
**ürün sahibinin kararıyla** (2.11.2026) Kamu SM üretim damgaları gibi yasal nitelikli sayılır. Bu
bir ürün sabitidir (`TURKTRUST_LEGAL_GRADE`); hiçbir site ayarı başka bir sağlayıcıyı yasal
nitelikli yapamaz. Kamu SM ile **aynı kök sertifika denetimi** geçerlidir. ARCILOGGER bir günü
ancak şu üçü birlikte doğruysa yasal nitelikli gösterir:

1. damga yasal nitelik iddia eden bir üretim sağlayıcısından alınmıştır (Kamu SM üretim sunucusu
   ya da `zd.turktrust.com.tr` üzerindeki `turktrust`);
2. damga doğrulanır;
3. damganın sertifika zinciri, sitenin `[tsa] legal_root_fingerprints` listesine yazdığı bir köke
   ulaşır (TÜRKTRUST kökü, Ayarlar'da "yasal" onayıyla yüklenmiş; bölüm 5).

Başka bir adresteki `turktrust` damgaları, `rfc3161`, `mock` ve test sunucularının damgaları hiçbir
zaman yasal nitelikli gösterilmez; uyarı şeridi bunu `provider_not_legal` olarak bildirir.

**Hukuki inceleme önerilir.** TÜRKTRUST damgalarını yasal nitelikli saymak ürün sahibinin kararıdır,
bir hukuki görüş değildir. Kuruluşunuzun hukuk müşavirine danışın. TÜRKTRUST'tan zaman
damgalarının hukuki niteliğine ilişkin **yazılı beyanını** isteyin ve bu beyanı sitenin kayıtlarıyla
(kurulum ve denetim kayıtları) birlikte saklayın.

ARCILOGGER'da TÜRKTRUST'ın yeri:

- Her gün bir damga (günlük manifest), Kamu SM'deki gibi. Yeniden damgalama (`Renew` görevi) günde
  en çok bir damga daha alır. TÜRKTRUST üretim damgasıyla yapılan bir yeniden damgalama,
  [ZAMAN-DAMGASI.md](ZAMAN-DAMGASI.md) bölüm 3.3'teki koşullarla sayılır: damga doğrulanır, zinciri
  `[tsa] legal_root_fingerprints` listesindeki bir köke ulaşır ve kendi sertifikası yenilenmesi
  gereken duruma gelmemiştir. Test sunucusuyla yapılanlar hiçbir zaman sayılmaz.
- Kullanıcı adı ve parola gizli depoda şifreli saklanır (`turktrust.username`,
  `turktrust.password`), `arcilogger.toml` dosyasına hiçbir zaman yazılmaz. Parola hiçbir hata
  metninde, günlükte ya da istemci çıktısında görünmez.
- HTTPS bağlantısının sunucu sertifikası Windows'un güven deposuyla doğrulanır; bu doğrulama
  kapatılamaz.
- Hata türleri: HTTP 401 ya da 403 bir yapılandırma hatasıdır (yanlış kullanıcı adı ya da parola,
  yeniden denenmez); HTTP 5xx ve ağ hataları "sunucuya ulaşılamıyor" sayılır ve mühürleme planına
  göre yeniden denenir; durum metninde kontör kelimesi geçen bir ret "kontör yok" sayılır (T2).
- Kontör kartı "Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin."
  der; `Credit` görevi TÜRKTRUST için sorgu yapmaz ve `R20-credit` uyarısı TÜRKTRUST için hiç
  verilmez. Bakiyeyi TÜRKTRUST'ın portalından izleyin.
- Vekil sunucu (proxy): `[network] https_proxy` doluysa (`http://host:port`, kimlik bilgisi almaz)
  TÜRKTRUST bağlantısı ve iptal listesi indirme onun üzerinden gider.

## 2. "Sahada doğrula" listesi

Aşağıdaki varsayımları depo doğrulamaz. Her biri ayarlanabilir ve varsayım bozulursa yüksek sesle
başarısız olan bir testi vardır. Sahadaki sonuçlarını bölüm 3'teki denetim listesine yazın ve ürün
ekibine iletin.

| # | Varsayım | Ayar | Durum |
|---|---|---|---|
| T1 | TSA adresi: sunucu `zd.turktrust.com.tr` (TÜRKTRUST e-postası), **yolu bilinmiyor**; varsayılan `https://zd.turktrust.com.tr/` | `[tsa] url` | Sahada doğrula |
| T2 | Yanlış kimlik bilgisine TÜRKTRUST HTTP 401 ile cevap verir. Bakiye bittiğinde ne cevap verdiği bilinmiyor: durum metninde kontör kelimesi geçen bir ret "kontör yok" sayılır | `[tsa] credit_regex` durum metni için de kullanılır | Sahada doğrula |
| T3 | TÜRKTRUST TSA sertifikasının genişletilmiş anahtar kullanımı (EKU) uzantısı kritik mi? Varsayılan: kritik değilse doğrulama hatası (Kamu SM K6 ile aynı düzen) | `[tsa] require_critical_eku` | Sahada doğrula |
| T4 | TÜRKTRUST iptal listelerinin (SİL) sunucusu, "kök sertifikaları ve iptal listeleri" sayfasından | `[tsa] crl_urls`; sıkı giden güvenlik duvarı bu adreslerin sunucularını açar | Sahada doğrula |
| T5 | Port 80 (düz HTTP). TÜRKTRUST e-postası 80 ve 443'ü adlandırır; varsayılan HTTPS'tir ve sahadaki damga testi bunu doğrular | `[tsa] allow_plain_http` (varsayılan kapalı) | Sahada doğrula |
| K10 | Kamu SM iptal listesi adresleri ([kamusm.md](kamusm.md) bölüm 3) | `[tsa] crl_urls`, boşsa sertifikalardaki SİL dağıtım noktaları | Sahada doğrula |

ARCILOGGER'ın kodunda hiçbir iptal listesi adresi yoktur: adresler `[tsa] crl_urls` listesinden ya da
sabitlenmiş köklerin ve son damgaların sertifikalarındaki SİL dağıtım noktalarından (yalnızca
http/https) gelir.

## 3. Sahada elle denetim listesi

Bu liste TÜRKTRUST hesabıyla (ürün sahibinin deneme hesabı ya da sitenin hesabı) sahada elle
yapılır; hiçbir otomatik test gerçek TÜRKTRUST sunucusuna bağlanmaz. Her adımın sonucunu, tarihini
ve yapanın adını sitenin kayıtlarına yazın. Ayrıntılar bölüm 4–8'dedir.

1. **Hesap bilgileri** (bölüm 4): kullanıcı adı, parola, TSA adresinin yolu dahil tam hali (T1),
   kullanılacak port (T5) ve hesabın deneme mi üretim mi olduğu TÜRKTRUST'tan yazılı olarak alındı.
2. **Yazılı beyan:** TÜRKTRUST'ın damgalarının hukuki niteliğine ilişkin yazılı beyanı alındı ve
   sitenin kayıtlarına konuldu; hukuk müşavirine iletildi.
3. **Kök sertifikalar** (bölüm 5): TÜRKTRUST kökü indirildi; SHA-256 parmak izi TÜRKTRUST'ın
   verdiği değerle karşılaştırıldı ve kayda geçirildi; Ayarlar → Zaman damgası'nda "TÜRKTRUST kök
   sertifikası" olarak **yasal** onayıyla yüklendi. Kamu SM kökleri ve parmak izleri yerinde kaldı.
4. **Kimlik bilgileri** (bölüm 6): sunucu seçimi "TÜRKTRUST" (HTTPS); kullanıcı adı ve parola
   kaydedildi, alanlar "Kayıtlı ✓" gösteriyor.
5. **Damga testi (HTTPS):** Ayarlar'daki damga testi ya da `& $core tsa-test` başarılı. Bu, T1'in
   yolunu ve T5'in HTTPS tarafını doğrular. Çıktıdaki genTime, seri no, TSA adı, sertifika
   geçerliliği ve `root_sha256` kayda geçirildi; `root_sha256` adım 3'teki parmak iziyle aynı,
   `legal_grade_claimed` ve `legal_grade` doğru (`true`). Oluşan `.zd` dosyası saklandı.
6. **T3 (EKU):** damga testi EKU yüzünden başarısız olduysa, `tsa.require_critical_eku = false`
   ancak TÜRKTRUST'ın sertifika profilinin EKU'yu kritik işaretlemediği doğrulandıktan sonra
   yapıldı. Bu seçim her manifeste mühürlenir; doğrulayıcılar o günler için uyarı verir.
7. **T2 (hatalar):** parola bilerek yanlış girildi; damga testi yeniden denenmeyen bir kimlik
   bilgisi hatasıyla bitti ve hata metninde parola görünmedi. Doğru parola yeniden kaydedildi.
   Bakiye bittiğinde TÜRKTRUST'ın hangi durum metniyle cevap verdiği TÜRKTRUST'a soruldu (ya da
   bakiyesi biten bir deneme hesabıyla görüldü) ve kayda geçirildi; metinde kontör kelimesi
   geçmiyorsa ürün ekibine iletildi.
8. **T4 (SİL):** TÜRKTRUST'ın iptal listesi adresleri sayfasından alındı, sertifikalardaki SİL
   dağıtım noktalarıyla karşılaştırıldı; gerekiyorsa `[tsa] crl_urls` listesine yazıldı.
   `& $core crl fetch` çıkış kodu 0, `& $core crl status` TÜRKTRUST listesini gösteriyor.
9. **Güvenlik duvarı ve vekil sunucu** (bölüm 8): sıkı giden modda (`-StrictOutbound`) ya da vekil
   sunucuyla damga testi ve iptal listesi indirme yeniden denendi ve başarılı.
10. **Kontör kartı:** Mühürleme sayfasındaki kart kontör sorgusu olmadığını söylüyor; `R20-credit`
    yok. Portal adresi ve düşük bakiye e-postası TÜRKTRUST'a soruldu, sonuç kayda geçirildi.
11. **Yasal durum:** uyarı şeridinde `provider_not_legal`, `legal_root_missing` ve
    `credentials_in_clear` yok; `& $core status` çıktısında `legal_grade` doğru. İlk gece
    mühürlemesinden sonra Mühürleme sayfası günü yasal nitelikli gösteriyor.
12. **Düz HTTP (yalnızca HTTPS sahada çalışmıyorsa):** düz HTTP sunucu seçimi (`turktrust-http`)
    onay metni yazılarak yapıldı, uyarı şeridi `credentials_in_clear` gösteriyor; bunun nedeni kayda
    geçirildi. HTTPS çalışıyorsa bu adım yapılmaz.
13. **Sonuçlar:** T1–T5 için sahada görülenler ürün ekibine iletildi; varsayılanlar ve bu belge buna
    göre güncellenir.

Bu listedeki komutlar yönetici PowerShell'inde, aşağıdaki `$core` ile çalışır:

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core tsa-test        # bir damga alır ve doğrular
& $core status          # legal_grade
& $core crl fetch       # iptal listelerini indirir ve doğrular
& $core crl status      # indirilen listeler
```

Damga testi gerçek bir damga alır; TÜRKTRUST'ın bunu nasıl ücretlendirdiğini TÜRKTRUST'a sorun.

## 4. Hesap açma

Bu belge yalnızca ARCILOGGER'ın neye ihtiyaç duyduğunu söyler. Başvurunun nasıl yapıldığı, hangi
belgelerin istendiği, fiyat, sözleşme ve bakiye yükleme: **TÜRKTRUST'a sorun.**

1. TÜRKTRUST'a kurumsal zaman damgası hesabı için başvurun. RFC 3161 ile, kullanıcı adı ve
   parolayla (HTTP Basic), HTTPS üzerinden damga almak istediğinizi belirtin. Spira kitaplığına
   ihtiyacınız olmadığını söyleyin.
2. TÜRKTRUST'tan **yazılı olarak** isteyin:
   - kullanıcı adı ve parola (parolayı e-postayla değil, TÜRKTRUST'ın önerdiği güvenli yolla);
   - TSA adresinin yolu dahil tam hali (T1) ve HTTPS'in (443) desteklendiği (T5);
   - kök ve gerekiyorsa ara sertifikaların indirileceği sayfa ve SHA-256 parmak izleri (bölüm 5);
   - iptal listelerinin adresleri (T4);
   - yanlış kimlik bilgisinde ve bakiye bittiğinde sunucunun cevabı (T2);
   - TSA sertifikasında EKU'nun kritik olup olmadığı (T3);
   - bakiyenin izlendiği portal ve isteğe bağlı düşük bakiye e-postası;
   - deneme hesabı verilip verilmediği ve deneme damgalarının hangi sunucudan geldiği;
   - damgalarının hukuki niteliğine ilişkin yazılı beyanı (bölüm 1).
3. Kullanıcı adını ve parolayı yalnızca Ayarlar'a (bölüm 6) girin; e-postada, biletlerde ya da
   paylaşılan belgelerde saklamayın.

**Deneme hesabı.** ARCILOGGER bir TÜRKTRUST damgasının deneme mi üretim mi olduğunu sunucu adından
anlar. Deneme hesabının damgaları da `zd.turktrust.com.tr` üzerinden geliyorsa ARCILOGGER onları
üretim damgalarından ayıramaz. Deneme süresinde mühürlemeye ne olacağına ilk başarılı mühürden
**önce** karar verin ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3): örneğin zinciri üretim hesabına
geçtiğiniz gün başlatmak (`[seal] first_day`). Seçim sahibindir.

### TÜRKTRUST'a talep e-postası (taslak)

```text
Konu: RFC 3161 zaman damgası hesabı talebi

Merhaba,

Kurum içi log kayıtlarımızı günlük olarak zaman damgasıyla mühürlemek için RFC 3161 zaman
damgası hizmetinizi kullanmak istiyoruz. Bağlantı kullanıcı adı ve parolayla (HTTP Basic),
HTTPS üzerinden olacaktır; Spira kitaplığına ihtiyacımız yoktur. Aşağıdakileri yazılı olarak
iletmenizi rica ederiz:

- TSA adresinin yolu dahil tam hali ve desteklenen portlar
- kök ve ara sertifikaların indirileceği sayfa ve SHA-256 parmak izleri
- iptal listelerinin adresleri
- yanlış kimlik bilgisinde ve bakiye bittiğinde sunucunun cevabı
- TSA sertifikasında genişletilmiş anahtar kullanımının kritik olup olmadığı
- bakiyenin izlendiği portal ve düşük bakiye e-postası
- deneme hesabı verilip verilmediği
- zaman damgalarınızın hukuki niteliğine ilişkin yazılı beyanınız

Kuruluş adı      : <...>
Vergi no         : <...>
Adres            : <...>
İrtibat kişisi   : <...>
İrtibat e-posta  : <...>

Saygılarımızla,
<ad soyad>
```

## 5. Kök sertifikalar ve parmak izleri

Kökler damga testinden **önce** gelir: test önce damgayı alır, sonra onu sabitlenmiş köklerle
doğrular; kökler yoksa damga alınmış olarak "no pinned root certificates" hatasıyla biter. Gece
mühürlemesi de kökler olmadan çalışmaz.

1. TÜRKTRUST'ın kök (ve gerekiyorsa ara) sertifikalarını TÜRKTRUST'ın "kök sertifikaları ve iptal
   listeleri" sayfasından indirin. Sayfanın adresi: **sahada doğrula** (TÜRKTRUST'a sorun).
2. Her dosyanın SHA-256 parmak izini TÜRKTRUST'ın yayımladığı ya da yazılı verdiği değerle
   karşılaştırın ve kayda geçirin (dosya adı, parmak izi, tarih, karşılaştıran):

   ```powershell
   Get-FileHash .\turktrust-kok.der -Algorithm SHA256
   ```

   `Get-FileHash` dosyanın özetini verir; DER biçimli bir sertifikada bu, sertifikanın SHA-256
   parmak izidir. PEM dosyasını önce DER'e çevirin: `certutil -decode turktrust-kok.pem
   $env:TEMP\turktrust-kok.der`, sonra `Get-FileHash $env:TEMP\turktrust-kok.der -Algorithm
   SHA256`. Windows sertifika penceresindeki "Parmak izi" SHA-1'dir, karşılaştırmaya uygun
   değildir. Değerler eşleşmiyorsa kökü yüklemeyin ve TÜRKTRUST'a sorun.
3. **Ayarlar → Zaman damgası**'nda kökü yükleyin. Sağlayıcı TÜRKTRUST iken form "TÜRKTRUST kök
   sertifikası" der. Yalnızca adım 2'de karşılaştırdığınız kökü **yasal** onayıyla yükleyin: parmak
   izi `[tsa] legal_root_fingerprints` listesine eklenir. Konsoldaki yol Kamu SM ile aynıdır: dosyayı
   `C:\ProgramData\ARCILOGGER\kamusm-roots\` klasörüne koyun (klasörün adı tarihseldir; bütün
   sağlayıcıların kökleri buradadır) ve parmak izini `arcilogger.toml` içindeki `[tsa]` bölümünde
   `legal_root_fingerprints` listesine yazın ([KURULUM.md](KURULUM.md) bölüm 10).
4. Damga testinden (bölüm 3 adım 5) sonra çıktıdaki `root_sha256` değerinin kayda geçirdiğiniz
   parmak iziyle aynı olduğunu denetleyin.

Kamu SM'den TÜRKTRUST'a geçerken Kamu SM köklerini ve parmak izlerini **silmeyin**: Kamu SM ile
damgalanmış eski günler onlarla doğrulanır ve yasal nitelikli kalır. Liste boşsa ya da TÜRKTRUST
kökü listede değilse hiçbir TÜRKTRUST günü yasal nitelikli gösterilmez (`legal_root_missing`).

## 6. Kullanıcı adı ve parola (Ayarlar)

1. **Ayarlar → Zaman damgası**'nı açın (Yönetici rolü). Sunucu seçiminde **TÜRKTRUST**'u seçin:
   sağlayıcı `turktrust`, adres `https://zd.turktrust.com.tr/`, port 443 olur. Adresin yolu T1'dir
   (sahada doğrula): TÜRKTRUST başka bir yol verdiyse tarayıcı formu onu alamaz; sunucu konsolunda
   `C:\ProgramData\ARCILOGGER\arcilogger.toml` içindeki `[tsa] url` değerine yazın (aşağıdaki
   örnek), sonra damga testini çalıştırın.
2. **Kullanıcı adı** ve **Parola** alanlarına TÜRKTRUST'ın verdiği değerleri yazın. Form "sahada
   doğrula" notunu gösterir. Kaydederken parolanızı yeniden girmeniz istenir (son 5 dakika,
   [AYARLAR.md](AYARLAR.md) bölüm 1). Değerler gizli depoya `turktrust.username` ve
   `turktrust.password` adlarıyla şifreli yazılır; alanlar bundan sonra "Kayıtlı ✓" gösterir,
   **Değiştir** ile yenisi yazılır.
3. Damga testini çalıştırın. İki değer de kayıtlı değilse test reddedilir (`tsa_not_ready`, eksik:
   `turktrust.username`, `turktrust.password`).

Konsoldaki yol (yönetici PowerShell'i; değerler ekranda görünmez):

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core secret set turktrust.username
& $core secret set turktrust.password
& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"   # sonra: yönetim hizmetinin hakkı
```

ve `C:\ProgramData\ARCILOGGER\arcilogger.toml` içinde:

```toml
[tsa]
provider = "turktrust"
url = "https://zd.turktrust.com.tr/"   # T1: yolu sahada doğrula
port = 443
```

Kurulum programı TÜRKTRUST kimlik bilgilerini konsolda sormaz ve hiçbir günlüğe yazmaz; kurulumun
son adımları bu bölümü ve bölüm 5'i gösterir. Konsoldan her `secret set` için
`Set-ArciVaultAcl.ps1` gereklidir ([KURULUM.md](KURULUM.md) bölüm 10).

Kullanıcı adını ya da parolayı `[tsa] url` içine yazmayın (`https://kullanıcı:parola@…`): böyle bir
adres reddedilir ve hata metninde tekrarlanmaz. **Kayıtlı hesabı silmek** bu sürümde yalnızca
konsoldan yapılır; tarayıcıdaki **Sil** yalnızca Kamu SM müşteri numarasını ve parolasını siler:

```powershell
& $core secret delete turktrust.username
& $core secret delete turktrust.password
```

**Düz HTTP (port 80).** HTTPS sahada çalışmıyorsa (T5) ve yalnızca o zaman: düz HTTP sunucu
seçimi (`turktrust-http`: `http://zd.turktrust.com.tr/`, port 80). Bu seçim `[tsa] allow_plain_http`
ayarını açar, yeniden kimlik doğrulama ister ve onay metni yazılarak yapılır; form, parolanın bu
durumda şifresiz gideceğini söyler. `[tsa] allow_plain_http` kapalıyken `turktrust` için `http://`
ile başlayan bir adres reddedilir: parola açık metin olarak giderdi. Sunucu da onaysız bir düz HTTP
geçişini reddeder (`tsa_plain_http_confirm_required`); form o zaman onay penceresini sunucunun
metniyle yeniden açar ve onay metninin yeniden yazılmasını ister. Düz HTTP kullanıldıkça uyarı
şeridi şunu gösterir (`credentials_in_clear`, uyarı; bir tarayıcıda 24 saat gizlenebilir):
"TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve parola ağda açık metin olarak
gider. Mümkünse HTTPS'e dönün."

## 7. Bakiye ve hatalar

- **Bakiye:** TÜRKTRUST kontör sorgusu sunmaz. Bakiyeyi TÜRKTRUST'ın portalından izleyin; portalın
  adresi ve isteğe bağlı düşük bakiye e-postası: **TÜRKTRUST'a sorun.** ARCILOGGER bakiyenin
  azaldığını önceden bildiremez; bakiye bittiğinde mühürleme başarısız olur ve `R20-seal`
  uyarısı verilir ([UYARILAR.md](UYARILAR.md)).
- **Yanlış kullanıcı adı ya da parola** (HTTP 401 ya da 403): yapılandırma hatası, yeniden
  denenmez. Değerleri Ayarlar'da düzeltin.
- **Yanlış adres** (yönlendirme 3xx, ya da 408 ve 429 dışında bir 4xx): yapılandırma hatası,
  yeniden denenmez. Yönlendirme izlenmez; metin durum kodunu verir ve `[tsa] url` ayarını gösterir
  (adresin yolu T1'dir, sahada doğrulayın), hiçbir zaman kimlik bilgisini.
- **Sunucuya ulaşılamıyor** (HTTP 408, 429, 5xx, ağ hatası): mühürleme kendi planına göre yeniden
  dener. Sıkı giden güvenlik duvarını ve vekil sunucuyu denetleyin (bölüm 8).
- **Bakiye yok** (T2): durum metninde kontör kelimesi geçen bir ret. TÜRKTRUST'ın gerçek cevabı
  sahada doğrulanacaktır.

## 8. Güvenlik duvarı, vekil sunucu ve iptal listeleri

- **Sıkı giden mod** (`Set-ArciFirewall.ps1 -StrictOutbound`): sağlayıcı `turktrust` iken betik
  `ARCILOGGER Out TURKTRUST` kuralıyla `[tsa] url` adresinin sunucusuna TCP 443'ü (adres kendi
  portunu veriyorsa onu; `[tsa] allow_plain_http` ile `http://` adreste 80'i) açar; ayrıca
  `ARCILOGGER Out CRL` kuralıyla `[tsa] crl_urls` adreslerinin sunucularına TCP 80 ve 443'ü açar.
  Betik yalnızca bu listedeki adresleri açar: sıkı giden modda TÜRKTRUST'ın iptal listesi
  adreslerini (T4) `[tsa] crl_urls` listesine yazın, yoksa sertifikalardaki SİL dağıtım
  noktalarına ulaşılamaz. Sağlayıcı `turktrust` iken `ARCILOGGER Out Kamu SM` kuralı yalnızca Kamu
  SM'nin iki sunucusunu, port 80'de tutar.
- **Vekil sunucu:** `[network] https_proxy` doluysa (`http://host:port`) bu iki kuralın yerine
  `ARCILOGGER Out Proxy` kuralıyla yalnızca vekil sunucunun adresi ve portu açılır. Vekil sunucu
  bu sürümde kimlik bilgisi almaz.
- **Tarayıcıdan yapılan değişiklikler:** Ayarlar → Zaman damgası ya da Genel'de bir değişiklikten
  sonra yönetim hizmeti bu kuralları aynı ad ve portlarla günceller, eksik olanı oluşturur; hiçbir
  kuralı kaldırmaz. Ayarı kaldırılan bir kural, kurulumun güvenlik duvarı adımı yeniden çalışana
  kadar kalır ([ZAMAN-DAMGASI.md](ZAMAN-DAMGASI.md) bölüm 1).
- **İptal listeleri:** `CrlFetch` görevi her gün `[tsa] crl_urls` listesini, liste boşsa
  sertifikalardaki SİL dağıtım noktalarını indirir ve sabitlenmiş bir sertifikayla doğrulanan
  listeleri değiştirmeden saklar. TÜRKTRUST'ın SİL sunucusu T4'tür: sahada doğrulayın.

---

## English

> Status: **draft (Wave 5)**. What is known about TÜRKTRUST comes from TÜRKTRUST's e-mail and from
> the product owner's decision of 2 November 2026. Items marked **"sahada doğrula"** (verify on
> site) will be settled on site with a TÜRKTRUST account, and this document will be updated.
>
> ARCILOGGER is **designed to support 5651 obligations**; it makes no claim of legal compliance.
> Providers, legal grade, re-stamping and revocation lists: [TIME-STAMPING.md](TIME-STAMPING.md).
> For Kamu SM: [kamusm.md](kamusm.md).

TÜRKTRUST is the second time-stamp provider next to Kamu SM (`[tsa] provider = "turktrust"`).
ARCILOGGER connects to it with RFC 3161, using a username and a password (HTTP Basic
authentication); it needs no Java, no Zamane client and no paid TÜRKTRUST Spira library. As with
Kamu SM, only a hash leaves the server; a log line is never sent.

### 1. What is known, and legal grade

| Topic | Information | Source |
|---|---|---|
| Production server | `zd.turktrust.com.tr`; the **path** of the URL is unknown, default `https://zd.turktrust.com.tr/` (T1) | TÜRKTRUST's e-mail |
| Ports | 80 and 443; ARCILOGGER uses HTTPS (443) by default, plain HTTP (80) only by an explicit administrator choice (T5) | TÜRKTRUST's e-mail |
| Protocol | RFC 3161, HTTP Basic authentication (username + password); TÜRKTRUST's answer to wrong credentials **sahada doğrula** (T2, section 3 step 7) | Product decision (D12) |
| Credit query | **None.** The balance is in TÜRKTRUST's own portal | TÜRKTRUST |
| Portal address, low-balance e-mail | **Ask TÜRKTRUST** | — |
| Root certificates and revocation lists | TÜRKTRUST's "kök sertifikaları ve iptal listeleri" page; exact addresses **sahada doğrula** (T4) | TÜRKTRUST |
| Application, price, contract, trial account | **Ask TÜRKTRUST** | — |

**Legal grade.** Stamps of the TÜRKTRUST production server (`zd.turktrust.com.tr`) count as legal
grade, like Kamu SM production stamps, **by the product owner's decision** (2 November 2026). This
is a product constant (`TURKTRUST_LEGAL_GRADE`); no site setting can make any other provider legal
grade. The **same root-certificate check** as for Kamu SM applies. ARCILOGGER shows a day as legal
grade only when all three hold:

1. the stamp comes from a production provider that claims legal grade (the Kamu SM production
   server, or `turktrust` on `zd.turktrust.com.tr`);
2. the stamp verifies;
3. the stamp's certificate chain ends at a root the site listed in `[tsa] legal_root_fingerprints`
   (the TÜRKTRUST root, uploaded in Settings with the "legal" confirmation; section 5).

`turktrust` stamps from any other address, and stamps of `rfc3161`, `mock` and test servers, are
never shown as legal grade; the banner reports this as `provider_not_legal`.

**Legal review is recommended.** Counting TÜRKTRUST stamps as legal grade is the product owner's
decision, not a legal opinion. Ask your organisation's legal counsel. Ask TÜRKTRUST for its
**written statement** on the legal standing of its time stamps, and keep it with the site's records
(installation and audit records).

TÜRKTRUST in ARCILOGGER:

- One stamp a day (the daily manifest), as with Kamu SM. Re-stamping (the `Renew` task) takes at
  most one more stamp a day. A re-stamping with a TÜRKTRUST production stamp counts under the
  conditions of [TIME-STAMPING.md](TIME-STAMPING.md) section 3.3: its token verifies, chains to a
  root listed in `[tsa] legal_root_fingerprints`, and its own certificate is not due. Re-stamping with
  a test server never counts.
- The username and the password are stored encrypted in the secret store (`turktrust.username`,
  `turktrust.password`) and never written into `arcilogger.toml`. The password never appears in an
  error text, a log or client output.
- The server certificate of the HTTPS connection is verified with the Windows trust store; this
  check cannot be turned off.
- Error kinds: HTTP 401 or 403 is a configuration error (wrong username or password, not retried);
  HTTP 5xx and network errors count as "server unavailable" and are retried on the sealing
  schedule; a rejection whose status text contains a credit word counts as "no credit" (T2).
- The credit card says "This provider offers no credit query; watch the balance in the provider's
  portal."; the `Credit` task makes no query for TÜRKTRUST, and the `R20-credit` alert is never
  raised for it. Watch the balance in TÜRKTRUST's portal.
- Proxy: when `[network] https_proxy` is set (`http://host:port`, no credentials), the TÜRKTRUST
  connection and the revocation-list download go through it.

### 2. The "sahada doğrula" list

The repository does not confirm the assumptions below. Each is configurable and has a test that
fails loudly if the assumption breaks. Write the on-site results into the checklist of section 3
and pass them to the product team.

| # | Assumption | Setting | State |
|---|---|---|---|
| T1 | The TSA URL: host `zd.turktrust.com.tr` (TÜRKTRUST's e-mail), **path unknown**; default `https://zd.turktrust.com.tr/` | `[tsa] url` | Sahada doğrula |
| T2 | TÜRKTRUST answers HTTP 401 for wrong credentials. Its answer for an empty balance is unknown: a rejection whose status text contains a credit word counts as "no credit" | `[tsa] credit_regex` is reused for the status text | Sahada doğrula |
| T3 | Does TÜRKTRUST's TSA certificate mark the extended key usage (EKU) critical? Default: a verification error if it does not (same mechanism as Kamu SM K6) | `[tsa] require_critical_eku` | Sahada doğrula |
| T4 | TÜRKTRUST's revocation-list (CRL) host, from its "kök sertifikaları ve iptal listeleri" page | `[tsa] crl_urls`; the strict outbound firewall opens their hosts | Sahada doğrula |
| T5 | Port 80 (plain HTTP). TÜRKTRUST's e-mail names ports 80 and 443; HTTPS is the default, and the stamp test on site confirms it | `[tsa] allow_plain_http` (default off) | Sahada doğrula |
| K10 | Kamu SM's CRL addresses ([kamusm.md](kamusm.md) section 3) | `[tsa] crl_urls`, otherwise the certificates' CRL distribution points | Sahada doğrula |

ARCILOGGER's code holds no revocation-list address: the addresses come from `[tsa] crl_urls`, or from
the CRL distribution points (http/https only) of the pinned roots and of the newest tokens'
certificates.

### 3. Manual on-site checklist

This list is worked through by hand on site with a TÜRKTRUST account (the product owner's trial
account or the site's account); no automated test connects to the real TÜRKTRUST server. Write the
result of each step, its date and who did it into the site's records. Details are in sections 4–8.

1. **Account details** (section 4): the username, the password, the full TSA URL including its path
   (T1), the port to use (T5) and whether the account is a trial or a production account were
   received from TÜRKTRUST in writing.
2. **Written statement:** TÜRKTRUST's written statement on the legal standing of its stamps was
   received and put into the site's records; it was passed to legal counsel.
3. **Root certificates** (section 5): the TÜRKTRUST root was downloaded; its SHA-256 fingerprint
   was compared with the value TÜRKTRUST gives and recorded; it was uploaded under Settings → Time
   stamping as "TÜRKTRUST kök sertifikası" with the **legal** confirmation. The Kamu SM roots and
   fingerprints stayed in place.
4. **Credentials** (section 6): the server choice is "TÜRKTRUST" (HTTPS); the username and the
   password were saved, and the fields show "Stored ✓".
5. **Stamp test (HTTPS):** the stamp test in Settings, or `& $core tsa-test`, succeeds. This
   confirms the path of T1 and the HTTPS side of T5. The genTime, serial number, TSA name,
   certificate validity and `root_sha256` of the output were recorded; `root_sha256` equals the
   fingerprint of step 3, and `legal_grade_claimed` and `legal_grade` are `true`. The resulting
   `.zd` file was kept.
6. **T3 (EKU):** if the stamp test failed because of the EKU, `tsa.require_critical_eku = false` was
   set only after TÜRKTRUST's certificate profile was confirmed to mark the EKU non-critical. The
   choice is sealed into every manifest; the verifiers warn for those days.
7. **T2 (errors):** a wrong password was entered on purpose; the stamp test ended with a credentials
   error that is not retried, and the error text did not show the password. The right password was
   saved again. Which status text TÜRKTRUST answers with when the balance is empty was asked of
   TÜRKTRUST (or seen with a trial account whose balance ran out) and recorded; if the text holds no
   credit word, it was passed to the product team.
8. **T4 (CRL):** TÜRKTRUST's revocation-list addresses were taken from its page and compared with
   the CRL distribution points in the certificates; where needed they were written into
   `[tsa] crl_urls`. `& $core crl fetch` exits with 0, and `& $core crl status` shows the TÜRKTRUST
   list.
9. **Firewall and proxy** (section 8): the stamp test and the revocation-list download were repeated
   in strict outbound mode (`-StrictOutbound`) or through the proxy, and succeed.
10. **Credit card:** the card on the Sealing page says there is no credit query; there is no
    `R20-credit`. The portal address and the low-balance e-mail were asked of TÜRKTRUST, and the
    answer was recorded.
11. **Legal status:** the banner shows no `provider_not_legal`, `legal_root_missing` or
    `credentials_in_clear`; `& $core status` shows `legal_grade` true. After the first night's
    sealing, the Sealing page shows the day as legal grade.
12. **Plain HTTP (only if HTTPS does not work on site):** the plain-HTTP server choice
    (`turktrust-http`) was made with the typed confirmation, the banner shows
    `credentials_in_clear`, and the reason was recorded. If HTTPS works, this step is not done.
13. **Results:** what was seen on site for T1–T5 was passed to the product team; the defaults and
    this document are updated accordingly.

The commands of this list run in an administrator PowerShell with the `$core` below:

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core tsa-test        # takes one stamp and verifies it
& $core status          # legal_grade
& $core crl fetch       # downloads and verifies the revocation lists
& $core crl status      # the downloaded lists
```

The stamp test takes a real stamp; how TÜRKTRUST charges for it: ask TÜRKTRUST.

### 4. Opening the account

This document says only what ARCILOGGER needs. How to apply, which documents are asked for, the
price, the contract and topping up the balance: **ask TÜRKTRUST.**

1. Apply to TÜRKTRUST for a corporate time-stamp account. Say that you want stamps over RFC 3161,
   with a username and a password (HTTP Basic), over HTTPS. Say that you do not need the Spira
   library.
2. Ask TÜRKTRUST **in writing** for:
   - the username and the password (the password not by e-mail, but by the secure way TÜRKTRUST
     recommends);
   - the full TSA URL including its path (T1), and that HTTPS (443) is supported (T5);
   - the page to download the root and, where needed, intermediate certificates from, and their
     SHA-256 fingerprints (section 5);
   - the revocation-list addresses (T4);
   - the server's answer for wrong credentials and for an empty balance (T2);
   - whether the TSA certificate marks the EKU critical (T3);
   - the portal where the balance is watched, and the optional low-balance e-mail;
   - whether a trial account is offered, and which server trial stamps come from;
   - its written statement on the legal standing of its stamps (section 1).
3. Enter the username and the password only in Settings (section 6); do not keep them in e-mails,
   tickets or shared documents.

**Trial account.** ARCILOGGER tells a TÜRKTRUST trial stamp from a production stamp by the server
name. If the trial account's stamps also come from `zd.turktrust.com.tr`, ARCILOGGER cannot tell
them from production stamps. Decide what happens to sealing during the trial **before** the first
successful seal ([FIRST-STEPS.md](FIRST-STEPS.md) section 3): for example, start the chain on the
day you move to the production account (`[seal] first_day`). The choice is the owner's.

The request e-mail to TÜRKTRUST is the Turkish draft in section 4 of the Turkish part; send it in
Turkish.

### 5. Root certificates and fingerprints

The roots come **before** the stamp test: the test takes the stamp first and then verifies it with
the pinned roots; without roots it ends, with the stamp already taken, with the error "no pinned
root certificates". The nightly sealing does not work without roots either.

1. Download TÜRKTRUST's root (and, where needed, intermediate) certificates from TÜRKTRUST's "kök
   sertifikaları ve iptal listeleri" page. The page's address: **sahada doğrula** (ask TÜRKTRUST).
2. Compare each file's SHA-256 fingerprint with the value TÜRKTRUST publishes or gives in writing,
   and record it (file name, fingerprint, date, who compared):

   ```powershell
   Get-FileHash .\turktrust-root.der -Algorithm SHA256
   ```

   `Get-FileHash` gives the file's hash; for a DER certificate that is the certificate's SHA-256
   fingerprint. Convert a PEM file to DER first: `certutil -decode turktrust-root.pem
   $env:TEMP\turktrust-root.der`, then `Get-FileHash $env:TEMP\turktrust-root.der -Algorithm
   SHA256`. The "Thumbprint" in the Windows certificate window is SHA-1 and is not suitable for the
   comparison. If the values do not match, do not upload the root, and ask TÜRKTRUST.
3. Upload the root under **Settings → Time stamping**. While the provider is TÜRKTRUST the form says
   "TÜRKTRUST kök sertifikası". Upload with the **legal** confirmation only the root you compared in
   step 2: its fingerprint is added to `[tsa] legal_root_fingerprints`. The console way is the same as
   for Kamu SM: put the file into `C:\ProgramData\ARCILOGGER\kamusm-roots\` (the folder's name is
   historical; the roots of every provider live there) and write the fingerprint into the
   `legal_root_fingerprints` list in the `[tsa]` section of `arcilogger.toml`
   ([INSTALL.md](INSTALL.md) section 10).
4. After the stamp test (section 3, step 5), check that `root_sha256` in the output equals the
   fingerprint you recorded.

When moving from Kamu SM to TÜRKTRUST, do **not** delete the Kamu SM roots and fingerprints: the
older days stamped by Kamu SM verify with them and stay legal grade. With the list empty, or without
the TÜRKTRUST root in it, no TÜRKTRUST day is shown as legal grade (`legal_root_missing`).

### 6. Username and password (Settings)

1. Open **Settings → Time stamping** (Administrator role). Choose **TÜRKTRUST** as the server: the
   provider becomes `turktrust`, the URL `https://zd.turktrust.com.tr/`, the port 443. The path of
   the URL is T1 (sahada doğrula): if TÜRKTRUST gave another path, the browser form cannot take it;
   write it into `[tsa] url` in `C:\ProgramData\ARCILOGGER\arcilogger.toml` on the server console
   (example below), then run the stamp test.
2. Enter the values TÜRKTRUST gave into the **Username** and **Password** fields. The form shows the
   "sahada doğrula" note. Saving asks for your password again (last 5 minutes,
   [SETTINGS.md](SETTINGS.md) section 1). The values are written encrypted into the secret store
   under the names `turktrust.username` and `turktrust.password`; the fields then show
   "Stored ✓", and **Change** writes a new value.
3. Run the stamp test. Unless both values are stored, the test is refused (`tsa_not_ready`,
   missing: `turktrust.username`, `turktrust.password`).

The console way (administrator PowerShell; the values are not shown on screen):

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
& $core secret set turktrust.username
& $core secret set turktrust.password
& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"   # then: the service's right
```

and in `C:\ProgramData\ARCILOGGER\arcilogger.toml`:

```toml
[tsa]
provider = "turktrust"
url = "https://zd.turktrust.com.tr/"   # T1: verify the path on site
port = 443
```

The installer never asks for the TÜRKTRUST credentials at the console and never writes them into a
log; its next steps point to this section and to section 5. Every `secret set` at the console needs
`Set-ArciVaultAcl.ps1` afterwards ([INSTALL.md](INSTALL.md) section 10).

Do not write the username or password into `[tsa] url` (`https://user:password@…`): such a URL is
refused, and the error text does not repeat it. In this release the stored account is **cleared on
the console only**; the browser's **Sil** (Delete) clears only the Kamu SM customer number and
password:

```powershell
& $core secret delete turktrust.username
& $core secret delete turktrust.password
```

**Plain HTTP (port 80).** Only if HTTPS does not work on site (T5): the plain-HTTP server
choice (`turktrust-http`: `http://zd.turktrust.com.tr/`, port 80). This choice turns on
`[tsa] allow_plain_http` and asks for re-authentication and a typed confirmation; the form says that
the password then travels unencrypted. While `[tsa] allow_plain_http` is off, an `http://` URL is
refused for `turktrust`: the password would travel in clear text. The server, too, refuses a
plain-HTTP switch without the confirmation (`tsa_plain_http_confirm_required`); the form then opens
the confirmation again with the server's text and asks for the typed phrase again. As long as plain
HTTP is used, the
banner shows (`credentials_in_clear`, a warning; it can be hidden for 24 hours per browser): "The
TÜRKTRUST connection uses unencrypted HTTP; the username and password cross the network in clear
text. Switch back to HTTPS if you can."

### 7. Balance and errors

- **Balance:** TÜRKTRUST offers no credit query. Watch the balance in TÜRKTRUST's portal; the
  portal's address and the optional low-balance e-mail: **ask TÜRKTRUST.** ARCILOGGER cannot warn
  before the balance runs low; when it is empty, sealing fails and raises the `R20-seal` alert
  ([ALERTS.md](ALERTS.md)).
- **Wrong username or password** (HTTP 401 or 403): a configuration error, not retried. Correct the
  values in Settings.
- **Wrong URL** (a 3xx redirect, or a 4xx other than 408 and 429): a configuration error, not
  retried. Redirects are not followed; the text gives the status code and points to `[tsa] url`
  (the URL path is T1, verify it on site), never to the credentials.
- **Server unavailable** (HTTP 408, 429, 5xx, network error): sealing retries on its own schedule.
  Check the strict outbound firewall and the proxy (section 8).
- **No credit** (T2): a rejection whose status text contains a credit word. TÜRKTRUST's real answer
  is to be verified on site.

### 8. Firewall, proxy and revocation lists

- **Strict outbound mode** (`Set-ArciFirewall.ps1 -StrictOutbound`): while the provider is
  `turktrust`, the script's `ARCILOGGER Out TURKTRUST` rule opens TCP 443 to the host of
  `[tsa] url` (the URL's own port if it names one; 80 for an `http://` URL with
  `[tsa] allow_plain_http`); its `ARCILOGGER Out CRL` rule opens TCP 80 and 443 to the hosts of
  `[tsa] crl_urls`. The script opens only the addresses of that list: in strict outbound mode,
  write TÜRKTRUST's revocation-list addresses (T4) into `[tsa] crl_urls`, or the CRL distribution
  points in the certificates cannot be reached. While the provider is `turktrust`, the
  `ARCILOGGER Out Kamu SM` rule keeps only Kamu SM's two servers, on port 80.
- **Proxy:** when `[network] https_proxy` is set (`http://host:port`), the `ARCILOGGER Out Proxy`
  rule opens only the proxy's host and port, in place of those two rules. The proxy takes no
  credentials in this release.
- **Changes in the browser:** after a change under Ayarlar → Zaman damgası or Genel, the management
  service updates these rules with the same names and ports and creates one that is missing; it
  removes none. A rule whose setting went away stays until the installer's firewall step runs
  again ([TIME-STAMPING.md](TIME-STAMPING.md) section 1).
- **Revocation lists:** the `CrlFetch` task downloads `[tsa] crl_urls` every day, or the certificates'
  CRL distribution points when the list is empty, and stores the lists that verify with a pinned
  certificate byte for byte. TÜRKTRUST's CRL host is T4: verify it on site.
