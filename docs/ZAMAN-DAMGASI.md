# Zaman damgası: sağlayıcılar, yasal nitelik, yeniden damgalama ve iptal listeleri

> English: [TIME-STAMPING.md](TIME-STAMPING.md). Kamu SM: [kamusm.md](kamusm.md). TÜRKTRUST:
> [turktrust.md](turktrust.md). Ayarlar: [AYARLAR.md](AYARLAR.md). Denetçi:
> [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Bu belgedeki "yasal nitelikli" ifadesi ARCILOGGER'ın teknik bir işaretidir (bölüm 2),
> hukuki bir değerlendirme değildir. Damgaların ve kayıt düzeninin hukuki değerlendirmesi için
> hukuk müşavirinize danışın.

Bu belge ARCILOGGER'ın zaman damgasıyla ilgili her şeyini tek yerde anlatır: hangi sağlayıcıların
seçilebildiğini (bölüm 1), bir günün ne zaman "yasal nitelikli" gösterildiğini (bölüm 2), TSA
sertifikası sona ermeden önce yeniden damgalamayı (bölüm 3), sertifika iptal listelerini (SİL,
bölüm 4), her sayfanın üstündeki yasal geçerlilik uyarı şeridini ve nedenlerini (bölüm 5), Panel,
Mühürleme sayfası ve e-posta uyarılarını (bölüm 6), ilgili ayarları (bölüm 7) ve sahada
doğrulanacakları (bölüm 8). Uyarı şeridinin metinleri bu belgeye "ZAMAN-DAMGASI.md bölüm N" diye
başvurur; her nedenin kendi bölümü vardır (bölüm 5.2–5.9).

Yollar varsayılan kurulumla verilmiştir: veri klasörü `D:\ARCILOGGER` (kasa `D:\ARCILOGGER\vault\`,
durum dosyaları `D:\ARCILOGGER\state\`), yapılandırma `C:\ProgramData\ARCILOGGER\arcilogger.toml`.
Konsol komutları yönetici PowerShell'inde tam yoluyla çalışır:

```powershell
$core = "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe"
```

## 1. Sağlayıcılar

Sağlayıcı `[tsa] provider` ile, sunucu `[tsa] url` ile seçilir. Web arayüzünde bu seçim
**Ayarlar → Zaman damgası** sayfasındadır (yönetici, parolanın yeniden girilmesiyle):

| `[tsa] provider` | Sunucu | Yasal nitelik iddiası | Kimlik bilgisi | Kontör sorgusu |
|---|---|---|---|---|
| `zamane` | Kamu SM üretim, `zd.kamusm.gov.tr` | **evet** | `kamusm.customer`, `kamusm.password` | var (Zamane istemcisi) |
| `zamane` | Kamu SM test, `tzd.kamusm.gov.tr` | hayır — test sunucusunun damgalarının yasal geçerliliği yoktur (Kamu SM) | aynı (test kullanıcısı) | var |
| `turktrust` | TÜRKTRUST üretim, `zd.turktrust.com.tr` (HTTPS, port 443) | **evet**, ürün sahibinin kararıyla (bölüm 2.2) | `turktrust.username`, `turktrust.password` | **yok** |
| `turktrust` | başka herhangi bir sunucu | hayır | aynı | yok |
| `rfc3161` | genel bir RFC 3161 sunucusu (yalnızca geliştirme) | hayır, hiçbir zaman | yok | yok |
| `mock` | ARCILOGGER'ın kendi deneme sertifika otoritesi | hayır, hiçbir zaman | yok | yok |

- **Kamu SM (`zamane`):** resmî Zamane konsol istemcisiyle çalışır. Hesap, kontör, test ve üretime
  geçiş: [kamusm.md](kamusm.md) ve [KURULUM.md](KURULUM.md) bölüm 10.
- **TÜRKTRUST (`turktrust`):** HTTP Basic kimlik doğrulamalı RFC 3161. Varsayılan ve önerilen yol
  HTTPS'tir; TLS sertifikası işletim sisteminin güven deposuyla doğrulanır ve bu doğrulama
  kapatılamaz. Düz HTTP (port 80) yalnızca bir yönetici `[tsa] allow_plain_http` ayarını açıkça
  açtığında kullanılabilir (onay metni yazılarak); o zaman kullanıcı adı ve parola ağda şifresiz
  gider ve şerit `credentials_in_clear` uyarısını gösterir (bölüm 5.3). Kullanıcı adı ve parola TOML
  dosyasında değil, gizli depoda saklanır: Ayarlar → Zaman damgası'nda girilir (ya da konsolda
  `secret set turktrust.username` / `secret set turktrust.password`); parola hiçbir log, hata
  metni ya da çıktıda görünmez. Kullanıcı adı ya da parola taşıyan bir `[tsa] url`
  (`https://kullanıcı:parola@…`) reddedilir ve hata metninde tekrarlanmaz. Kayıtlı TÜRKTRUST
  hesabını **silmek bu sürümde yalnızca konsoldan** yapılır: `secret delete turktrust.username` ve
  `secret delete turktrust.password` (tarayıcıdaki **Sil** yalnızca Kamu SM müşteri numarasını ve
  parolasını siler). Hesap açma, kök sertifikalar ve elle denetim listesi:
  [turktrust.md](turktrust.md).
- **TÜRKTRUST'ın yanıtları:** 401 ve 403 kimlik bilgisi hatasıdır (yeniden denenmez). Bir
  yönlendirme (3xx) izlenmez; 3xx ve 408 ile 429 dışındaki her 4xx bir yapılandırma hatasıdır,
  yeniden denenmez ve metni durum kodunu verip `[tsa] url` ayarını gösterir (adres yolu T1,
  **sahada doğrula**), hiçbir zaman kimlik bilgisini. 408, 429, 5xx ve ağ hataları "sunucuya
  ulaşılamıyor" sayılır ve yeniden denenir.
- **TÜRKTRUST kontör sorgusu sunmaz.** Credit görevi TÜRKTRUST için kontör okumaz, kontör kartı
  şu metni gösterir ve `R20-credit` uyarısı verilmez:
  "Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin."
  Bakiyenin TÜRKTRUST portalında nasıl izleneceğini ve bakiye azaldığında isteğe bağlı bir e-posta
  alınıp alınamayacağını **TÜRKTRUST'a sorun**.
- **`rfc3161`** kimlik bilgisi göndermez; Kamu SM'de çalışmaz, yalnızca geliştirme içindir.
- **`mock`** yalnızca geliştirme ve deneme sunucuları içindir: köküne yalnızca `provider = "mock"`
  iken güvenilir. Sunucuda kullanmayın; sağlayıcı değişince mock damgalı günler doğrulanamaz
  (`R20-verify`; [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3).

**Ne gönderilir?** Yalnızca özetler. Bir günün damgası günlük manifestin özetini, bir yeniden
damgalama yalnızca özetlerden oluşan yenileme kaydının SHA-256 özetini gönderir (bölüm 3). Hiçbir
log satırı sunucudan çıkmaz.

**Vekil sunucu (proxy):** `[network] https_proxy` (`http://host:port`, varsayılan boş) ayarlanırsa
SİL indirme ile `rfc3161` ve `turktrust` sağlayıcıları bu vekil üzerinden bağlanır. Vekil bu
sürümde kimlik bilgisi almaz. Zamane konsol istemcisi bu ayarı **kullanmaz**: istemcinin vekili
nasıl aldığı bilinmiyor (**sahada doğrula**).

**Sıkı giden trafik** (`Set-ArciFirewall.ps1 -StrictOutbound`): güvenlik duvarı şu giden kuralları
açar (hepsi TCP):

| Kural | Ne zaman | Adres ve port |
|---|---|---|
| `ARCILOGGER Out TURKTRUST` | sağlayıcı `turktrust` ve vekil yok | `[tsa] url` adresinin sunucusu; port adresin kendi portu, yoksa 80 ya da 443 olmayan `[tsa] port`, yoksa 443 (`[tsa] allow_plain_http` ile `http://` adreste 80) |
| `ARCILOGGER Out CRL` | `[tsa] crl_urls` dolu ve vekil yok | her SİL adresinin sunucusu, 80 ve 443 (listeler imzalıdır, düz HTTP onlar için güvenlidir) |
| `ARCILOGGER Out Proxy` | `[network] https_proxy` ayarlı | vekilin adresi ve portu; TÜRKTRUST ve SİL kurallarının yerine |
| `ARCILOGGER Out Kamu SM` | her zaman | Kamu SM'nin iki sunucusu; sağlayıcı `turktrust` iken yalnızca port 80 (Zamane istemcisi vekil kullanmaz, bu kural vekil ayarlıyken de kalır) |

Tarayıcıdan yapılan bir değişiklikten sonra (Ayarlar → Zaman damgası ya da Genel) yönetim hizmeti
(ApplySystem) bu kuralları aynı adlar ve portlarla günceller, eksik olanı oluşturur. Hiçbir giden
kuralı kaldırmaz: ayarı kaldırılan bir `TURKTRUST`, `CRL` ya da `Proxy` kuralı, kurulumun güvenlik
duvarı adımı (`Set-ArciFirewall.ps1`) yeniden çalışana kadar kalır. Kamu SM için açılan adresler:
[KURULUM.md](KURULUM.md).

## 2. "Yasal nitelikli" ne demek?

### 2.1 Üç koşul

ARCILOGGER bir günü (5651 raporunda, mühür takviminde, Mühürleme sayfasında, kanıt paketinde)
ancak **üç koşulun üçü de** sağlanıyorsa "yasal nitelikli" (legal grade) gösterir:

1. **İddia:** damga bir üretim sağlayıcısından alındığını beyan eder. Bu sürümde iki üretim
   sağlayıcısı vardır: `zd.kamusm.gov.tr` üzerinde `zamane` ve `zd.turktrust.com.tr` üzerinde
   `turktrust`. İddia manifestin `tsa` bloğuna (`legal_grade`) mühürlenir.
2. **Doğrulama:** damga (belirteç) doğrulanır: imza, manifest özeti, `id-kp-timeStamping`
   kullanımı, damga anında geçerli sertifikalarla köke kadar zincir.
3. **Kök:** zincir, sitenin `[tsa] legal_root_fingerprints` listesine yazdığı bir kökün SHA-256
   parmak izine ulaşır. Liste boşsa hiçbir gün yasal nitelikli gösterilmez (bölüm 5.4).

Dördüncü bir yol yoktur. Üretim sağlayıcılarının listesi bir ürün sabitidir: hiçbir site ayarı
başka bir sağlayıcıyı ya da başka bir sunucuyu "yasal nitelikli" yapamaz. Kök listesi ise sitenin
kendi kararıdır: kök, sağlayıcının yayımladığı parmak iziyle karşılaştırılıp kayda geçirildikten
sonra Ayarlar → Zaman damgası'ndaki kök yüklemesiyle, "yasal" onayıyla eklenir.

### 2.2 TÜRKTRUST

TÜRKTRUST'ın üretim sunucusundan (`zd.turktrust.com.tr`) alınan damgalar, **ürün sahibinin
kararıyla** (2026-11-02) Kamu SM'ninkilerle aynı biçimde yasal nitelikli sayılır. Karar bir ürün
sabitidir (`TURKTRUST_LEGAL_GRADE`); kök denetimi Kamu SM'deki ile aynıdır: TÜRKTRUST ile
damgalanmış bir gün ancak damgası doğrulanır ve zinciri `[tsa] legal_root_fingerprints`
listesindeki bir TÜRKTRUST köküne ulaşırsa yasal nitelikli gösterilir (kök yüklenirken metin
"TÜRKTRUST kök sertifikası" der).

TÜRKTRUST'ı seçmeden önce:

- seçimi **hukuk müşavirinizle** değerlendirin;
- TÜRKTRUST'ın, damgalarının niteliği hakkındaki **yazılı beyanını** isteyin ve sitenin kayıtlarıyla
  birlikte saklayın.

### 2.3 Yasal nitelikli olmayan damgalar

Deneme (`mock`), `rfc3161` ve test sunucularının damgaları yasal nitelikli **değildir**; o
sağlayıcılar seçiliyken şerit `provider_not_legal` gösterir (bölüm 5.2). Daha önce damgalanmış
günler sağlayıcı değişince değişmez: mühürlenmiş veri hiçbir zaman yeniden yazılmaz. `[tsa]
require_critical_eku` seçimi (TSA sertifikasının EKU uzantısı kritik mi; Kamu SM için K6,
TÜRKTRUST için T3, **sahada doğrula**) her manifeste mühürlenir.

## 3. Yeniden damgalama

### 3.1 Neden ve ne zaman?

Bir zaman damgası, alındığı anda geçerli olan TSA sertifikasıyla imzalanır; sertifikanın bir bitiş
tarihi (`notAfter`) vardır. Yeniden damgalama, sertifika sona ermeden önce eski damgaların
özetlerini yeni bir damgayla mühürler; böylece eski damgaların o tarihte var olduğu, hâlâ geçerli
bir sertifikayla gösterilebilir. Yeniden damgalama destekleyici bir kanıttır: bir damganın
alındığı an için geçerli olup olmadığını değiştirmez. Günün doğrulama sonucu aynı kalır.

**Ne yenilenir?**

- gün damgaları (`vault\YYYY\MM\DD\manifest-<gün>.json.zd`);
- imha tutanaklarının damgaları (`vault\deletions\*.zd`);
- saklanan resmî raporların damgaları (`exports\reports\*.pdf.zd`);
- daha önceki yenileme kayıtlarının damgaları.

Yalnızca dışa aktarılmış paketlerin içinde bulunan rapor damgaları yenilenmez (sunucudaki
`exports\reports\` kopyaları yenilenir). Özet algoritmasının yenilenmesi (ham dosyaları yeni bir
algoritmayla yeniden özetlemek) bu sürümde yoktur.

**Ne zaman?** Bir damga, TSA sertifikasının bitişine `[tsa] renew_before_days` günden (varsayılan
180, 30–730) az kaldığında ve onu kapsayan, sayılan bir yenileme yoksa **yenilenmesi gereken**
(`due`) olur. `Renew` görevi her gün `arcilogger-core renew` komutunu çalıştırır. Bir çalışma en
çok **bir** yenileme kaydı yazar ve o an yenilenmesi gereken bütün damgaları bu tek kayda koyar:
günde en çok bir damga, kontör kullanımı öngörülebilir kalır. Yenilenecek bir şey yoksa damga
alınmaz.

### 3.2 Yenileme kaydı

Her yenileme iki dosyadır: `vault\renewals\<YYYY-MM-DD>-<sıra>.json` (kanonik JSON, şema
`arcilogger.renewal/1`) ve onun damgası `<aynı ad>.json.zd`. Sıra numarası her tarih için 1'den
başlar. Kayıt yalnızca özetler içerir:

```json
{
  "schema": "arcilogger.renewal/1",
  "created_at": "2031-05-04T03:10:00.000+03:00",
  "system": {"name": "ARCILOGGER", "version": "0.5.0", "site": "<kuruluş adı>"},
  "tsa": {"provider": "zamane", "legal_grade": true, "digest": "sha256"},
  "reason": "due",
  "tokens": [
    {
      "kind": "day",
      "day": "2029-06-01",
      "path": "2029/06/01/manifest-2029-06-01.json.zd",
      "token_sha256": "<64 hex>",
      "manifest_sha256": "<64 hex>",
      "cert_not_after": "2031-10-01T00:00:00Z"
    },
    {
      "kind": "renewal",
      "path": "renewals/2029-01-03-1.json.zd",
      "token_sha256": "<64 hex>",
      "record_sha256": "<64 hex>",
      "cert_not_after": "2031-07-01T00:00:00Z",
      "protects": ["<64 hex: eski yenilemenin koruduğu gün manifest/damga özetleri>"]
    }
  ]
}
```

- **`kind`:** `day`, `deletion`, `report` ya da `renewal`. `report` için `path`, `exports\`
  altındadır (ör. `exports/reports/ARCILOGGER-5651-rapor-<id>.pdf.zd`) ve `record_sha256` PDF'in
  SHA-256 özetidir; `deletion` ve `renewal` için `record_sha256` kaydın özetidir.
- **`tsa` bloğu** manifestinkiyle aynı alanları taşır; `legal_grade` sağlayıcının **iddiasıdır**.
  Yenilemenin sayılıp sayılmadığı bölüm 3.3'teki kurala göre belirlenir.
- **`reason`:** `due`; sağlayıcının iddiası yasal nitelikli değilse kayıt yine yazılır, ama
  `reason: "test"` olur ve hiçbir zaman sayılmaz.
- **Biçim:** `tokens` `path`'e göre sıralıdır; özetler küçük harfli onaltılıktır; zamanlar
  ISO 8601'dir (`created_at` manifestlerdeki gibi site saatiyle, `cert_not_after` UTC).
- **Yenilemenin yenilemesi:** bir yenileme damgasının kendi sertifikası sona yaklaşınca sonraki
  yenileme o damganın özetini **ve** onun koruduğu bütün gün özetlerini (`protects`) yeniden
  listeler. Böylece bir günün kanıt paketi, zincirin tamamını aynı özet eşleşmesiyle alır.
- **Dosyalar:** kayıt yazılır, damgalanır, sonra iki dosya da mühürlü dosyalar gibi kilitlenir.
  Kayıt yazıldıktan sonra damga tamamlanamazsa kayıt silinir: damgası olmayan kayıt henüz kanıt
  değildir. Yeniden damgalama hiçbir gün klasörüne, manifeste ya da damgaya dokunmaz; yalnızca
  `vault\renewals\` altına yeni dosya ekler.

### 3.3 Hangi yenileme sayılır?

Bir yenileme ancak şu üç koşulda **sayılır**:

- kendi damgası doğrulanır;
- yasal nitelik iddiası olan bir sağlayıcıyla alınmıştır ve zinciri `[tsa] legal_root_fingerprints`
  listesindeki bir köke ulaşır (bölüm 2.1);
- kendi sertifikası yenilenmesi gereken duruma gelmemiştir (ya da o da sayılan bir yenilemeyle
  kapsanmıştır).

`mock`, `rfc3161` ya da bir test sunucusuyla yapılan yenileme yazılır ama **hiçbir zaman
sayılmaz**: deneme ya da test amaçlı bir yenileme, yenilenmesi gereken bir üretim damgasını
gizleyemez.

### 3.4 Damga başına durum

Her yenilenebilir damga için durum `state\renewal\renewal.json` dosyasındadır. Bu klasör korumalıdır:
yalnızca `Renew` görevi (LOCAL SERVICE) ve yöneticiler yazar; API ve worker yalnızca okur.
Yükseltmeden sonraki ilk sayılan çalıştırmaya kadar önceki sürümün `state\renewal.json` dosyası
okunur; ondan yalnızca art arda başarısızlık sayısı alınır, dosya yerinde kalır ve sonra okunmaz.

| Durum | Koşul | Düzey |
|---|---|---|
| `not_due` | Sertifikanın bitişine `[tsa] renew_before_days` günden çok var | — |
| `due` | Bitişe `[tsa] renew_before_days` günden az kaldı ve sayılan yenileme yok | uyarı |
| `covered` | Sayılan bir yenileme bu damganın özetini listeliyor, sertifika bitmeden damgalanmış ve kendi durumu `not_due` ya da `covered` | — |
| `late` | Sayılan yenileme, eski sertifikanın süresi dolduktan **sonra** damgalanmış | uyarı |
| `uncovered` | Sertifikanın süresi doldu ve sayılan yenileme yok | uyarı |

`uncovered` bile bir hata değil, uyarıdır: damga alındığı an için geçerli kalır; yeniden damgalama
uzun süreli kanıt için önerilir. Süresi dolmuş bir damga da yenilenir; o zaman durumu `late` olur.

### 3.5 Komut satırı

```powershell
& $core renew --dry-run      # yenilenmesi gerekenleri gösterir; damga almaz, kayıt yazmaz
& $core renew                # yenilenmesi gerekenleri tek kayıtta yeniden damgalar
& $core renew --json         # sonucu JSON olarak verir
```

| Çıkış kodu | Anlamı |
|---|---|
| 0 | Yenilendi ya da yenilenmesi gereken yoktu |
| 1 | Yenilendi, uyarılarla (bazı damgalar `uncovered`) |
| 2 | Yapılandırma reddetti (sağlayıcı kimlik bilgisi yok, kök yok) |
| 3 | Zaman damgası sunucusu ya da çalıştırma başarısız oldu, veya başka bir çalıştırma kilidi tutuyor (yeniden denenebilir) |

Kilidi tutan bir çalıştırma dışındaki her çalıştırma `state\renewal\renewal.json` dosyasını yazar,
başarısız ya da reddedilen bir çalıştırma da (`last_run`, `failures_in_a_row`: `R20-renewal`
bunları okur). `--dry-run` durumları hesaplayıp dosyaya yazar ama damga almaz, yenileme kaydı
yazmaz ve `last_run` değerini değiştirmez; yükseltmeden sonra yalnızca önceki sürümün dosyası
varken hiçbir şey yazmaz. `Renew` görevi aynı komuttur; son çalışmasının sonucu
Sistem → Zamanlanmış görevler'de görünür ([UYUM-DURUMU.md](UYUM-DURUMU.md) bölüm 4.6).

### 3.6 Bağımsız doğrulayıcı ve kanıt paketi

Kanıt paketi, bir günün manifest ya da damga özetini (ve paketlenen bir raporun damga özetini)
içeren yenileme kayıtlarını `vault/renewals/` altında taşır; üç kat derin bir yenileme zinciri de
böylece pakete girer. Bağımsız doğrulayıcı (`verify.py`; sürümü raporun başında yazar) paketteki her gün
damgası için bir bulgu ekler. Sonuç yalnızca paketten, doğrulamanın yapıldığı an ("şimdi") esas
alınarak hesaplanır; sunucudaki doğrulayıcı aynı kodları verir:

| Düzey | Kod | Metin (TR) | Metin (EN) |
|---|---|---|---|
| BİLGİ | `renewal-not-due` | Yeniden damgalama henüz gerekmiyor (sertifika {date} tarihine kadar geçerli). | Re-stamping is not due yet (certificate valid until {date}). |
| OK | `renewal-covered` | Bu damga, sertifikası sona ermeden yeniden damgalanmış: {renewal}. | Re-stamped before its certificate expired: {renewal}. |
| UYARI | `renewal-due` | Damga sertifikası {date} tarihinde sona eriyor; yeniden damgalama bekleniyor. | The stamp certificate expires on {date}; re-stamping is due. |
| UYARI | `renewal-late` | Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({date}). | Re-stamped after the old certificate had expired ({date}). |
| UYARI | `renewal-uncovered` | Damga sertifikasının süresi {date} tarihinde doldu ve yeniden damgalama yok; damga, alındığı an için geçerlidir. | The stamp certificate expired on {date} with no re-stamping; the stamp is valid for the moment it was made. |

`renewal-late` satırındaki tarih yeniden damgalamanın zamanıdır (yenileme damgasının genTime değeri),
öteki satırlardaki tarih sertifikanın bitişidir.

Yenileme damgasının kendisinin denetimi (`renewal-ok`) ve kapsadığı günler (`renewal-covers`)
önceki sürümlerdeki gibi raporlanır. Doğrulayıcı çıktısının tamamı:
[DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) bölüm 7.

## 4. İptal listeleri (SİL)

### 4.1 İndirme

`CrlFetch` görevi her gün `arcilogger-core crl fetch` komutunu çalıştırır. Adresler:

- `[tsa] crl_urls` doluysa yalnızca bu liste (en çok 20 http(s) adresi);
- boşsa sabitlenmiş kökler klasöründeki (`[tsa] roots_dir`) sertifikaların ve her sağlayıcının en
  yeni damgasındaki sertifikaların SİL dağıtım noktaları (yalnızca `http` ve `https`).

Programın içinde **hiçbir SİL adresi yazılı değildir**: Kamu SM'nin SİL adresleri bilinmiyor
([kamusm.md](kamusm.md) K10), TÜRKTRUST'ınki TÜRKTRUST'ın "kök sertifikaları ve iptal listeleri"
sayfasından alınır (T4); ikisi de **sahada doğrula**. Sıkı giden trafikte güvenlik duvarı
`[tsa] crl_urls` adreslerinin sunucularını açar; bu yüzden sıkı giden trafik kullanan bir sitede
SİL adreslerini `[tsa] crl_urls` listesine yazın.

Her liste `[tsa] timeout_seconds` süresiyle, en çok 20 MB olarak ve varsa `[network] https_proxy`
üzerinden indirilir. İş yalnızca indirir; hiçbir şey göndermez. İmzası sabitlenmiş köklerden ya da
damgalarda görülen ara sertifikalardan biriyle doğrulanan liste bayt bayt şu adla saklanır:

```text
D:\ARCILOGGER\state\crl\<veren-sha256-ilk-12>-<thisUpdate, YYYYMMDDTHHMMSSZ>.crl
```

ve `state\crl\index.json` dosyasında tarif edilir (şema `arcilogger.crl-index/1`: adres, dosya,
veren, `this_update`, `next_update`, SHA-256, indirme zamanı). Doğrulanamayan liste saklanmaz;
başarısızlığı dizine yazılır:

| Hata | Anlamı |
|---|---|
| `unreachable` | Adrese ulaşılamadı |
| `too_large` | Liste 20 MB'tan büyük |
| `not_a_crl` | İndirilen dosya bir SİL değil |
| `bad_signature` | Listenin imzası doğrulanmadı |
| `unknown_issuer` | Listeyi verenin sertifikası sabitlenmiş kökler ve damgalardaki ara sertifikalar arasında yok |

İş **hiçbir dosyayı silmez**: bir sertifika otoritesi süresi dolmuş bir sertifikayı sonraki
listelerden çıkarabilir, eski listeler bu yüzden değerini korur. `crl status` saklanan listelerin
sayısını ve boyutunu da gösterir.

```powershell
& $core crl fetch            # bütün adresleri indirir ve doğrular
& $core crl fetch --json     # sonucu JSON olarak verir
& $core crl status           # dizini (saklanan listeler, başarısızlıklar) gösterir
```

| Çıkış kodu (`crl fetch`) | Anlamı |
|---|---|
| 0 | Her adres indirildi ve doğrulandı |
| 1 | Bazıları başarısız (dizine yazıldı) |
| 2 | Bilinen hiçbir adres yok |

### 4.2 Doğrulama

Sunucudaki doğrulayıcı saklanan listelerle iptal durumunu denetler; kodlar ve düzeyler bağımsız
doğrulayıcınınkilerle aynıdır:

| Kod | Düzey | Anlamı |
|---|---|---|
| `crl-revoked` | HATA | Bir sertifika damgadan önce iptal edilmiş; günün denetimi başarısız olur (bölüm 5.8) |
| `crl-revoked-later`, `crl-hold` | UYARI | Sertifika damgadan sonra iptal edilmiş ya da askıya alınmış; incelenmeli |
| `crl-none`, `crl-stale` | BİLGİ | Listeler bu sertifikayı ya da damga anını kapsamıyor; iptal durumu bilinmiyor |
| `crl-ok` | OK | SİL denetimi geçti |

Kanıt paketi her listeyi değil, yalnızca gerekenleri taşır: her veren için en yeni liste ve
paketlenen her günün damga anından sonraki (`thisUpdate` ≥ damga anı) ilk liste. Hiç liste yoksa
paket bunu belirten notu yazar. Bağımsız doğrulayıcı çevrim içi denetim yapmaz (OCSP de yoktur);
yalnızca paketteki listelere bakar ([DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) bölüm 6 ve 7).

## 5. Yasal geçerlilik uyarı şeridi

### 5.1 Şerit nasıl çalışır?

Oturum açmış **her kullanıcı** (her rol) her sayfanın üstünde, lisans şeridinin altında yasal
geçerlilik şeridini görür. Şerit `GET /legal-status` yanıtından beslenir. Bu durum tek bir sunucu
işlevinde hesaplanır; aynı kaynak Panel'in engelleyici kartını, Mühürleme sayfasını ve e-posta
uyarılarını da besler (bölüm 6).

- Düzey **tehlike** (kırmızı) ya da **uyarı** (sarı-turuncu) olur, her zaman simge ve metinle.
  Hiçbir neden yoksa şerit görünmez.
- Şerit en kötü nedeni gösterir; başka nedenler varsa "ve N uyarı daha" yazar ve tıklanınca listeyi
  açar.
- **Tehlike kapatılamaz.** Bir uyarı, o tarayıcıda 24 saat gizlenebilir.
- Şerit 5 dakikada bir yenilenir. Neden, koşula göre Mühürleme sayfasına ya da Ayarlar → Zaman
  damgası'na bağlantı verebilir; kullanıcının açma yetkisi olmayan sayfanın bağlantısı gösterilmez.
- `GET /legal-status` oturum açmış her kullanıcıya açıktır, denetim kaydına yazılmaz, log
  sahipleri hakkında hiçbir veri taşımaz ve 60 saniye önbellekte tutulur. Sağlayıcıyı adıyla verir:
  "Kamu SM", "TÜRKTRUST", "Test sunucusu", "Deneme (mock)".

Nedenler, metinleri ve bölümleri (şeridin metni ilgili bölüme "ZAMAN-DAMGASI.md bölüm N" diye
başvurarak biter; metinlerdeki `{…}` alanları o anki değerlerle doldurulur):

| Neden | Düzey | Bölüm |
|---|---|---|
| `provider_not_legal` | tehlike | 5.2 |
| `credentials_in_clear` | uyarı | 5.3 |
| `legal_root_missing` | tehlike | 5.4 |
| `unstamped_days` | tehlike | 5.5 |
| `renewal_due` | uyarı | 5.6 |
| `renewal_uncovered` | uyarı | 5.7 |
| `certificate_revoked` | tehlike | 5.8 |
| `crl_stale` | uyarı | 5.9 |

Liste önce tehlikeleri, sonra uyarıları gösterir; her grup koda göre sıralıdır.

### 5.2 `provider_not_legal` — deneme ya da test sağlayıcısı (tehlike)

> Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu damgaların yasal geçerliliği
> yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim sunucusunu seçin.

**Neden?** Sağlayıcı `mock`, `rfc3161` ya da bir test sunucusu (bölüm 1). Mühürleme sayfasındaki
kırmızı uyarı aynı metni gösterir.

**Ne yapmalı?** Bir üretim sağlayıcısına geçin: Kamu SM ([kamusm.md](kamusm.md),
[KURULUM.md](KURULUM.md) bölüm 10) ya da TÜRKTRUST ([turktrust.md](turktrust.md); önce bölüm 2.2).
Daha önce deneme ya da test sağlayıcısıyla damgalanmış günler değişmez.

### 5.3 `credentials_in_clear` — TÜRKTRUST düz HTTP ile (uyarı)

> TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve parola ağda açık metin olarak
> gider. Mümkünse HTTPS'e dönün.

**Neden?** Sağlayıcı `turktrust` ve `[tsa] allow_plain_http` açık (Ayarlar → Zaman damgası'nda
TÜRKTRUST'ın düz HTTP seçimi). Damgalar alınır; ama HTTP Basic kimlik bilgileri şifrelenmeden gider.

**Ne yapmalı?** Ayarlar → Zaman damgası'nda HTTPS'li TÜRKTRUST seçimine dönün
(`https://zd.turktrust.com.tr/`, port 443) ve `[tsa] allow_plain_http` ayarını kapatın. HTTPS
çalışmıyorsa nedenini (güvenlik duvarı, vekil, adres yolu T1) sahada doğrulayın
([turktrust.md](turktrust.md)). Parola bir süre açık gittiyse değiştirilmesini değerlendirin.

### 5.4 `legal_root_missing` — kök parmak izi yok (tehlike)

> Kök sertifika parmak izi tanımlı değil; hiçbir gün yasal nitelikli gösterilemez.

**Neden?** Sağlayıcının iddiası yasal nitelikli (Kamu SM ya da TÜRKTRUST üretim), ama `[tsa]
legal_root_fingerprints` boş. Üç koşulun üçüncüsü hiçbir zaman sağlanamaz (bölüm 2.1).

**Ne yapmalı?** Sağlayıcının kök sertifikasını indirin, SHA-256 parmak izini sağlayıcının
yayımladığı değerle karşılaştırıp kayda geçirin ve Ayarlar → Zaman damgası'nda kökü "yasal"
onayıyla ekleyin. Kamu SM: [kamusm.md](kamusm.md) bölüm 4 adım 3; TÜRKTRUST:
[turktrust.md](turktrust.md) bölüm 5.

### 5.5 `unstamped_days` — damgalanmamış kapanmış günler (tehlike)

> {count} kapanmış gün {hours} saatten uzun süredir damgalanmadı.

**Neden?** Kapanmış bir gün, gün bitiminden `[seal] late_after_hours` (varsayılan 48) saatten uzun
süredir damgasız. Panel'in üstünde engelleyici kart da görünür (bölüm 6).

**Ne yapmalı?** Mühürleme sayfasında bekleyen günleri ve son hatayı, Sistem → Zamanlanmış
görevler'de `Seal` görevini denetleyin; `R20-seal` ve `R20-seal-config` uyarılarına bakın.
Olası nedenler: kimlik bilgisi ya da kök eksik, kontör ya da bakiye bitmiş, sunucuya ulaşılamıyor
(güvenlik duvarı, vekil). Bu süreden sonra damgalanan günler kalıcı olarak "geç damgalandı" diye
işaretlenir ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 3).

### 5.6 `renewal_due` — yeniden damgalama bekleniyor (uyarı)

> {count} damganın sertifikası {days} gün içinde sona eriyor; yeniden damgalama bekleniyor.

**Neden?** `due` durumunda damgalar var (bölüm 3.4); `{days}`, `[tsa] renew_before_days` değeridir.

**Ne yapmalı?** `Renew` görevi bunu her gün kendisi yapar. Uyarı birkaç gün sürüyorsa Sistem →
Zamanlanmış görevler'de `Renew` görevinin son sonucuna bakın, `& $core renew --dry-run` ile neyin
yenileneceğini görün ve gerekirse `& $core renew` çalıştırın (bölüm 3.5). Sağlayıcı yasal
nitelikli değilse yapılan yenilemeler sayılmaz (bölüm 3.3): önce bölüm 5.2.

### 5.7 `renewal_uncovered` — sertifika yeniden damgalanmadan sona erdi (uyarı)

> {count} damganın sertifikası yeniden damgalanmadan sona erdi; damgalar geçerliliğini korur, ancak
> uzun süreli kanıt için yeniden damgalama önerilir.

**Neden?** `uncovered` durumunda damgalar var (bölüm 3.4).

**Ne yapmalı?** `& $core renew` ile şimdi yeniden damgalayın; bu damgalar o zaman `late` olur
(bölüm 3.4). Yenilemenin neden zamanında yapılmadığını (`Renew` görevi, kimlik bilgisi, bakiye)
bulun. Bu günlerin kanıt değerini hukuk müşavirinizle değerlendirin.

### 5.8 `certificate_revoked` — sertifika iptal listesinde (tehlike)

> Zaman damgası sertifikası iptal listesinde: {name}. Zaman damgası sağlayıcısıyla görüşün;
> hukuki inceleme önerilir.

**Neden?** Kullanılan bir TSA sertifikası, saklanan ve doğrulanmış bir iptal listesinde damgadan
önce iptal edilmiş görünüyor (`crl-revoked`, bölüm 4.2). İlgili günlerin doğrulaması başarısız olur.

**Ne yapmalı?** Damgayı veren sağlayıcıyla görüşün (Kamu SM ya da TÜRKTRUST); hukuki inceleme
isteyin. Hiçbir dosyayı silmeyin ya da değiştirmeyin: mühürlü veri, damgalar ve iptal listeleri
kanıttır. `& $core crl status` hangi listenin bu sonucu verdiğini gösterir.

### 5.9 `crl_stale` — güncel iptal listesi yok (uyarı)

> {days} günden yeni doğrulanmış bir iptal listesi (SİL) yok.

**Neden?** Kullanılan bir sertifikayı veren için `[tsa] crl_max_age_days` (varsayılan 7) günden yeni,
doğrulanmış bir liste yok.

**Ne yapmalı?** `& $core crl fetch` çalıştırın ve `& $core crl status` ile başarısızlıklara
bakın (bölüm 4.1): `unreachable` için güvenlik duvarı, vekil ve adres; `unknown_issuer` ya da
`bad_signature` için sabitlenmiş kökler. SİL adresleri bilinmiyorsa `[tsa] crl_urls` listesine yazın
(K10, T4: **sahada doğrula**).

## 6. Panel, Mühürleme sayfası ve e-posta uyarıları

- **Panel:** kapanmış bir gün `[seal] late_after_hours` saatten uzun süredir damgasızsa Panel'in en
  üstünde engelleyici bir kart görünür: en eski damgasız gün ve kaç saat geciktiği. Kart, gün
  damgalanınca kalkar.
- **Mühürleme sayfası:** deneme ya da test sağlayıcısı uyarısı (bölüm 5.2) şeritle aynı metni
  gösterir. Bir günün ayrıntısı o günün yeniden damgalama durumunu (bölüm 3.4: durum, sertifika
  bitişi, sayılan yenileme) ve iptal denetiminin sonucunu (bölüm 4.2) da gösterir.
- **E-posta uyarıları** (her biri tek uyarı, tekrar anahtarı `<kod>:none`; e-postalar kişi adı ve
  log satırı içermez). Kodların tamamı ve ne yapılacağı: [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 9.

| Uyarı | Önem | Ne zaman açılır? |
|---|---|---|
| `R20-renewal` | yüksek | `due` ya da `uncovered` bir damga varsa; ayrıca `Renew` görevi art arda iki kez başarısız olduysa |
| `R20-cert` | kritik / yüksek | Bir sertifika iptal listesinde (`crl-revoked`, kritik) ya da 30 gün içinde sona eriyor ve yenilemeyle kapsanmamış (yüksek) |
| `R20-crl` | orta | Kullanılan bir sertifikayı veren için `[tsa] crl_max_age_days` günden yeni doğrulanmış liste yok |

Yeniden damgalama, SİL indirme, uyarı şeridi ve TÜRKTRUST sağlayıcısı **5651 Temel** işlevleridir:
hiçbir lisans durumu onları kapatmaz ([LISANS.md](LISANS.md)).

## 7. Ayarlar

Bu sürümün yeni ayarları (`arcilogger.toml`):

| Ayar | Tür, varsayılan | Sınırlar | Anlamı | Tarayıcıdan |
|---|---|---|---|---|
| `[tsa] renew_before_days` | tamsayı, 180 | 30–730 | Sertifikanın bitişine kaç gün kala yeniden damgalanır (bölüm 3.1) | Ayarlar → Zaman damgası (parolanın yeniden girilmesiyle) |
| `[tsa] crl_urls` | http(s) adres listesi, `[]` | en çok 20 | SİL adresleri; boşsa sertifikalardaki dağıtım noktaları (bölüm 4.1) | Ayarlar → Zaman damgası (parolanın yeniden girilmesiyle) |
| `[tsa] crl_max_age_days` | tamsayı, 7 | 1–60 | Bir liste kaç günden eskiyse `crl_stale` ve `R20-crl` (bölüm 5.9) | Ayarlar → Zaman damgası (parolanın yeniden girilmesiyle) |
| `[network] https_proxy` | metin, `""` | `http://host:port` ya da boş | Giden vekil: SİL indirme, `rfc3161`, `turktrust` (bölüm 1) | Ayarlar → Genel (parolanın yeniden girilmesiyle) |
| `[tsa] allow_plain_http` | mantıksal, false | yalnızca `turktrust` için anlamlı | TÜRKTRUST'a düz HTTP (port 80); `credentials_in_clear` (bölüm 5.3) | yalnızca TÜRKTRUST'ın düz HTTP seçimiyle, onay metni yazılarak |

Sağlayıcı `turktrust` iken `[tsa] url` `https://` ile başlamalıdır; `[tsa] allow_plain_http`
kapalıyken `http://` bir adres yapılandırma hatası verir (parola açık metin olarak giderdi).
İlgili mevcut ayarlar: `[tsa] provider`, `[tsa] url`, `[tsa] roots_dir`,
`[tsa] legal_root_fingerprints`, `[tsa] require_critical_eku`, `[tsa] timeout_seconds`,
`[tsa] credit_regex` (TÜRKTRUST'ın ret metninde de kontör sözcüğü aranır, T2),
`[seal] late_after_hours`. Gizli değerler:
`turktrust.username` (en çok 128 karakter) ve `turktrust.password` (en çok 256 karakter). Ayarlar
sayfalarının tamamı: [AYARLAR.md](AYARLAR.md).

## 8. Sahada doğrulanacaklar

Depoda doğrulanamayan her davranış ayarlanabilir ve varsayımı bozulursa yüksek sesle başarısız olan
bir testle korunur. Ayrıntılar ve elle denetim listesi: [turktrust.md](turktrust.md) bölüm 2 ve
3, [kamusm.md](kamusm.md) bölüm 3.

| No | Varsayım | Nereden ayarlanır? |
|---|---|---|
| T1 | TÜRKTRUST'ın TSA adresi `https://zd.turktrust.com.tr/` (8.11.2026'da doğrulandı: kök yol damga verir) | `[tsa] url` |
| T2 | Yanlış parola: HTTP 200 ve metinsiz bir ret (8.11.2026'da doğrulandı; kimlik bilgisi hatası olarak bildirilir). Bakiye bittiğinde ne yanıt verdiği bilinmiyor: durum metninde kontör sözcüğü geçen bir ret "kontör yok" sayılır | `[tsa] credit_regex` |
| T3 | TÜRKTRUST'ın TSA sertifikası EKU uzantısını kritik işaretler (8.11.2026'da doğrulandı) | `[tsa] require_critical_eku` |
| T4 | TÜRKTRUST'ın SİL'i: `http://www.turktrust.com.tr/sil/TURKTRUST_Kok_SIL_h7.crl` (8.11.2026'da doğrulandı) | `[tsa] crl_urls`; güvenlik duvarı bu sunucuları açar |
| T5 | HTTPS çalışır (8.11.2026'da doğrulandı); düz HTTP gerekmez | `[tsa] allow_plain_http` (varsayılan kapalı) |
| K10 | Kamu SM'nin SİL adresleri | `[tsa] crl_urls`, boşsa sertifikalardaki dağıtım noktaları |
| — | Zamane konsol istemcisinin vekil sunucuyu nasıl aldığı | bu sürümde yok: Zamane `[network] https_proxy` kullanmaz |
