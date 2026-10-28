# Uyum Durumu, Sistem ve ilk kurulum sihirbazı

> English: [COMPLIANCE-STATUS.md](COMPLIANCE-STATUS.md). Yetkiler: [SECURITY.md](SECURITY.md),
> [roles.md](roles.md). Uyarılar: [UYARILAR.md](UYARILAR.md). Yedekler: [YEDEKLEME.md](YEDEKLEME.md).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Kartların ve raporun sonuçlarını hukuk müşavirinizle teyit edin.

Bu belge web arayüzünün **Uyum Durumu** sayfasını (on denetim kartı: her biri neyi denetler, hangi
dosyayı ya da tabloyu okur, hangi koşulda hangi durumu gösterir, ne yapılmalı), **Uyum Durumu
Raporu**'nu (PDF), **Sistem** sayfasını ve **ilk kurulum sihirbazını** anlatır. Kartlar belgeye
"UYUM-DURUMU.md bölüm 2.{n}" diye başvurur; bölüm 2.{n}, n numaralı kartın bölümüdür.

Yollar varsayılan kurulumla verilmiştir: veri klasörü `D:\ARCILOGGER` (durum dosyaları
`D:\ARCILOGGER\state\`), yapılandırma `C:\ProgramData\ARCILOGGER\arcilogger.toml`.

## 1. Uyum Durumu sayfası nedir, ne değildir?

**Uyum Durumu** (`/compliance`) kayıt toplama, mühürleme ve yedeklemenin teknik denetimlerini tek
sayfada gösterir. On kartın her biri bir denetimdir ve dört durumdan birindedir; durum her zaman
simge **ve** metinle gösterilir, yalnızca renkle değil:

| Durum | Anlamı |
|---|---|
| **Tamam** | Denetim bir sorun bulmadı. |
| **Dikkat** | İlgilenilmesi gereken ama henüz kanıt kaybı olmayan bir durum (bekleyen bir iş, yaklaşan bir eşik, eksik bir ayar). |
| **Sorun** | Kanıtın toplanmasını, mühürlenmesini ya da korunmasını şimdi etkileyen bir durum. |
| **Bilgi yok** | Henüz veri yok ya da denetim bu kurulum için geçerli değil; gerekçesi her zaman yazılıdır. |

Sayfanın üstünde her zaman şu not durur (Uyum Durumu Raporu'nda da):

> ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Bu kartlar kayıt toplama,
> mühürleme ve yedeklemenin teknik denetimlerinin sonucudur; hukuki bir değerlendirme ya da
> uygunluk beyanı değildir. Sonuçları hukuk müşavirinizle teyit edin.

**Ne değildir?** "Uyum Durumu" sayfanın adıdır, bir hüküm değildir. On kartın hepsi **Tamam** olsa
da bu, kurumun yükümlülüklerini yerine getirdiği anlamına gelmez: kartlar yalnızca ARCILOGGER'ın
kendi kaydettiklerini denetler. Sayfa hiçbir cihaza ve Kamu SM'ye soru sormaz, hiçbir şey
indirmez, kurmaz ya da değiştirmez; yalnızca `state\` ve `admin\` klasörlerindeki dosyaları,
kasayı, veritabanını ve etkin uyarıları okur. Kontör gibi bir değer, zamanıyla birlikte gösterilen
**en son kaydedilen** değerdir. Cihazların doğru ayarlandığı, replika hedefinin değiştirilemez
olduğu gibi konular ARCILOGGER'dan denetlenemez; kartlar bunları "Sahada doğrulayın" notuyla
belirtir (bölüm 6).

**Kim ne görür?** Her iki sayfa da **5651 Temel**'e aittir: hiçbir lisans durumu onları kapatmaz.

| İşlem | Yetki | Roller |
|---|---|---|
| Uyum Durumu sayfası ve kartlar | `compliance.read` | Yönetici, Denetçi |
| Uyum Durumu Raporu (PDF) indirme | `compliance.read` | Yönetici, Denetçi |
| "Yapılandırma yedeğini aldım" (kart 10) | `settings.manage` | Yalnızca Yönetici |
| İlk kurulum sihirbazı ve bütün ayarlar | `settings.manage` | Yalnızca Yönetici |
| Sistem sayfası ve Panel'deki Disk kartı | `system.read` | Yalnızca Yönetici |

İK Raporları (`hr`) ve Rapor İzleyici (`viewer`) rolleri bu yetkilerin hiçbirine sahip değildir (sahibin kararı,
2026-10-28; [SECURITY.md](SECURITY.md)). Denetçi bütün kartları ve PDF düğmesini görür; yedek onayı
düğmesi ve sihirbaz bağlantısı ona gösterilmez. Kartın ayrıntı sayfasına giden bağlantı, kullanıcı
o sayfayı açamıyorsa gizlenir (denetçi için Kaynaklar, Güvenlik, Ayarlar ve Sistem; Mühürleme ve
Arama bağlantıları kalır); kartın kendisi her zaman gösterilir. Kartın açık uyarıları (kural, önem,
açılış zamanı) denetçiye de gösterilir, Güvenlik sayfasına bağlantısı olmadan.

**Kişisel veri yok, görüntüleme denetim kaydına yazılmaz.** Kartlarda ve raporda log öznelerine ait
hiçbir veri yoktur: istemci IP ya da MAC adresi, bir cihaza atanmış kişinin adı, cihaz ana bilgisayar
adı, gönderici adresi, replika hedefinin yolu yer almaz. Yalnızca yapılandırılmış kaynak adları,
sayılar, zamanlar, sürücü harfleri, tablo adları, web filtresi profil adları ve yapılandırılmış saat
sunucularının adresleri gösterilir. Bu yüzden sayfa bir aydınlatma metni onayı istemez ve sayfayı
açmak (Panel gibi) denetim kaydına yazılmaz. Denetim kaydına yazılanlar: raporun indirilmesi
(`compliance.report_export`, bölüm 3) ve yapılandırma yedeğinin onayı
(`compliance.config_backup_confirm`, bölüm 2.10).

**Yenileme.** Sayfa kendini 30 saniyede bir arka planda yeniler; bu otomatik yenileme oturumu açık
tutmaz (oturum boşta kalma süresi işlemeye devam eder). **Yenile** düğmesi kullanıcı etkinliği
sayılır. Sunucu hesapladığı kartları 30 saniye boyunca bütün kullanıcılar için saklar; bir değişiklik
bu yüzden en geç yaklaşık bir dakika içinde görünür. Yapılandırma yedeğinin onayı saklanan sonucu
hemen siler: kart 10 onaydan hemen sonra güncellenir. Sayfada "Güncellendi {zaman}" yazar.

**Bir kart hesaplanamazsa** **Bilgi yok** durumunda "Bu denetim şu anda yapılamadı: {hata kodu}"
gösterir (hata kodu, hatanın sınıf adıdır; ayrıntısı ArciApi hizmetinin günlüğüne yazılır). Diğer
kartlar bundan etkilenmez.

**Değerlendirme kuralı.** Her kartın bölüm 2'deki tablosu yukarıdan aşağıya okunur: **ilk tutan
satır** durumu ve gerekçeyi belirler. Hiçbir satır tutmazsa kart **Tamam**'dır. Her kart ayrıca
konusuyla ilgili etkin uyarıları (durumu `open` ya da `acknowledged`) en yeniden başlayarak en
çok beş tane listeler.

**Kartlar:**

| # | Kart | Ayrıntı sayfası | İlgili uyarılar |
|---|---|---|---|
| 1 | Log kaynakları veri gönderiyor | Kaynaklar | `R15` |
| 2 | Dünün kayıtları mühürlendi ve doğrulandı | Mühürleme | `R20-seal` (dünün) |
| 3 | Mühür zinciri bütün | Mühürleme | `R20-verify` |
| 4 | Saklama süresi | Mühürleme | `R20-retention` |
| 5 | Kamu SM kontörü ve zaman damgası sertifikası | Mühürleme | `R20-credit` |
| 6 | Sunucu saati | Sistem | `R20-clock` |
| 7 | İçerik filtresi kayıtları | Arama | `R20-filter` |
| 8 | DHCP ve ARP kayıtları (erişim kayıtları) | Kaynaklar | `R20-lease` |
| 9 | İkinci kopya (replika) | Mühürleme | `R20-replica` |
| 10 | Yapılandırma ve veritabanı yedekleri | Ayarlar | `R20-backup` |

Sayfanın üstündeki özet "{toplam} denetimden {tamam} tanesi tamam" der. Bir kart **Tamam** değilse
ve kullanıcı yöneticiyse sayfada **İlk kurulum sihirbazını aç** bağlantısı da görünür (bölüm 5).

## 2. Kartlar

Tablolardaki gerekçeler kartın gösterdiği metinlerdir; `{…}` yerine kart değeri yazar ("—": değer
yok). Uyarı kodlarının anlamı ve eşikleri: [UYARILAR.md](UYARILAR.md) bölüm 2.

### 2.1 Kart 1 — Log kaynakları veri gönderiyor

**Ne denetler?** İzlenen her log kaynağının kalp atışı süresi içinde mesaj gönderip göndermediğini.
İzlenen kaynak, R15'in izlediği kaynaktır: etkin, beklenen ve yapılandırılmış (bilinmeyen bir
gönderici değil) bir kaynak (Ayarlar → Kaynaklar).

**Neyi okur?** Veritabanındaki `sources` tablosu (izlenen kaynaklar, kaynağın kalp atışı süresi
`[[sources]] heartbeat_minutes`, varsayılan 15 dakika, ve içe aktarmanın son yüklediği zaman),
toplayıcının durum dosyası `state\collector-status.json` (son alınan mesaj) ve etkin `R15:<kaynak>`
uyarıları. Bir kaynağın en yeni mesajı bu ikisinin yenisidir; hesap Panel'in "Log alımı" kutusuyla
aynıdır.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | Bir kaynak için etkin `R15` uyarısı var | "{n} kaynak kalp atışı süresinden uzun süredir veri göndermiyor: {adlar}." |
| 2 | Dikkat | İzlenen bir kaynak kalp atışı süresinden uzun süredir sessiz, ama henüz `R15` uyarısı yok | "{adlar} kalp atışı süresinden uzun süredir sessiz; uyarı başlangıç bekleme süresi içinde." |
| 3 | Dikkat | İzlenen kaynak yok | "İzlenen log kaynağı yok. Kaynakları Ayarlar → Kaynaklar'da tanımlayın." |
| 4 | Tamam | — | "İzlenen {n} kaynağın tümü kalp atışı süresi içinde veri gönderdi." |

Satır 2, R15'in bekleme süreleri içindedir: worker başladıktan ya da kaynağın kaydı oluştuktan sonra
`alerts.r15_startup_grace_minutes` (15 dakika) boyunca uyarı açılmaz; hiç mesaj göndermemiş bir
kaynak kaydı oluştuktan `alerts.r15_never_seen_minutes` (60 dakika) sonra uyarır.

Bilgiler: izlenen kaynak sayısı, veri gönderen, sessiz kaynakların adları, kalp atışı süresi
("kaynağa göre, varsayılan 15 dk").

**Ne yapmalı?**

- Sessiz kaynak (satır 1-2): cihaz açık ve ağa bağlı mı, syslog ayarı ARCILOGGER'ı gösteriyor mu
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 4, komutları sahada doğrulayın); toplayıcı çalışıyor mu
  (Sistem → Hizmetler). Kaynaklar sayfası kaynağın son kaydını ve kayıt boşluklarını gösterir.
  Kaynak yeniden gönderince `R15` kendiliğinden çözülür ve kart düzelir.
- Kaynak bilerek kapatılmışsa ya da artık beklenmiyorsa Ayarlar → Kaynaklar'da kapatın; kapalı
  kaynak izlenmez ([AYARLAR.md](AYARLAR.md) bölüm 1).
- İzlenen kaynak yok (satır 3): güvenlik duvarını ve ana yönlendiriciyi Ayarlar → Kaynaklar'da
  ekleyin (bölüm 5, sihirbazın "Log kaynakları" adımı).

### 2.2 Kart 2 — Dünün kayıtları mühürlendi ve doğrulandı

**Ne denetler?** Dünkü günün (Türkiye saatiyle) mühürlenip doğrulandığını ve damganın Kamu SM üretim
sunucusundan alındığını.

**Neyi okur?** Mühürleme sayfasının takviminin dün için gösterdiği gün durumunu (aynı hesap: kasa,
`state\seal\<gün>.json`, `state\verify\` altındaki doğrulama sonuçları ve Mühürleme sayfasından
istenen doğrulamalar); `alerts.seal_overdue_hour` (varsayılan 9, yani 09:00); `[tsa]` ayarının Kamu
SM üretim sunucusunu gösterip göstermediğini. Dünün `R20-seal` uyarısı kartta listelenir; kartın
durumunu gün durumu belirler.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | Gün "Başarısız" | "Dünün ({gün}) mühürlenmesi ya da doğrulaması başarısız: {ileti}" |
| 2 | Sorun | Gün "Mühürlenmeyi bekliyor" ve saat bugün `alerts.seal_overdue_hour`'ı geçti | "Dün ({gün}) saat {saat}:00'a kadar mühürlenmedi." |
| 3 | Sorun | Gün için ham kayıt yok ve en az bir izlenen kaynak var | "Dün ({gün}) için ham kayıt yok; kaynaklar veri göndermemiş olabilir." |
| 4 | Dikkat | Gün "Mühürlenmeyi bekliyor", gecikme saatinden önce | "Dünün ({gün}) mühürlenmesi bekleniyor (Seal görevi 00:10'dan sonra çalışır)." |
| 5 | Dikkat | Gün mühürlendi, henüz doğrulanmadı | "Dün ({gün}) mühürlendi, henüz doğrulanmadı (VerifyDaily 01:00'de çalışır)." |
| 6 | Dikkat | Gün geç damgalandı | "Dün ({gün}) geç damgalandı." |
| 7 | Dikkat | Gün mühürlendi ve doğrulandı, ama `[tsa]` Kamu SM üretim sunucusunu göstermiyor | "Dün mühürlendi ve doğrulandı, ancak damga Kamu SM üretim sunucusundan değil." |
| 8 | Bilgi yok | Gün için kayıt yok ve izlenen kaynak yok | "Dün için kayıt yok; henüz izlenen kaynak tanımlanmadı." |
| 9 | Tamam | Gün mühürlendi ve doğrulandı | "Dün ({gün}) mühürlendi ve doğrulandı." |

`{ileti}`, Mühürleme sayfasının gün için gösterdiği hata iletisidir ("—": ileti yok).
Bilgiler: gün, durum (Mühürleme sayfasındaki etiketiyle), mühürlenme zamanı, doğrulama ("{zaman},
başarılı|başarısız" ya da "—"), damga sunucusu (bölüm 2.5'teki adlarla).

**Ne yapmalı?**

- Başarısız ya da gecikmiş (satır 1-2): Mühürleme sayfasında günün ayrıntısına ve
  `D:\ARCILOGGER\logs\core.log` dosyasına bakın; Güvenlik sayfasındaki `R20-seal` ve
  `R20-seal-config` uyarıları nedeni söyler (Kamu SM'ye erişim, kontör, kimlik bilgileri,
  kök sertifikalar: [kamusm.md](kamusm.md), [UYARILAR.md](UYARILAR.md) bölüm 2). Seal görevi
  5 dakikada bir yeniden dener; Mühürleme sayfasındaki **Şimdi mühürle** de kullanılabilir.
- Ham kayıt yok (satır 3): kaynaklar dün veri göndermemiş olabilir; kart 1'e ve Kaynaklar sayfasına
  bakın.
- Bekliyor ya da doğrulanmadı (satır 4-5): sabah bu olağandır; Seal görevi 00:10'dan sonra,
  VerifyDaily 01:00'de çalışır. Öğleden sonra hâlâ böyleyse Sistem → Zamanlanmış görevler'de
  görevlerin son kanıtına bakın (bölüm 4.6).
- Geç damgalandı (satır 6): Mühürleme sayfasında günün ayrıntısına bakın ve sunucunun ya da Kamu SM
  bağlantısının o gece neden aksadığını inceleyin. Kart yalnızca dünü değerlendirir: sonraki günler
  zamanında mühürlenince düzelir.
- Üretim sunucusu değil (satır 7): kart 5'e bakın (bölüm 2.5).

### 2.3 Kart 3 — Mühür zinciri bütün

**Ne denetler?** Dünle biten son 30 günün mühürlü günlerinin doğrulandığını ve aylık tam zincir
denetiminin başarılı olduğunu.

**Neyi okur?** Mühürleme sayfasının son 30 gün için gösterdiği gün durumları; tam zincir
denetiminin sonucu `state\verify\last-full.json` (`at`, `ok`; VerifyMonthly görevi, ayın 1'i 04:00);
kasadaki ilk mühürlü gün; etkin `R20-verify` uyarısı. Mühürlü bir gün, son 35 gün içinde başarılı bir
doğrulaması varsa "doğrulanmış" sayılır. Doğrulama görevleri değiştirilmedi: VerifyDaily her gün son
2 günü, VerifyWeekly pazar günleri son 90 günü, VerifyMonthly her ay bütün zinciri doğrular; kart bu
sonuçları değerlendirir.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | 30 günden biri "Başarısız" | "Son 30 günde {n} gün başarısız: {günler}." |
| 2 | Sorun | `last-full.json` `"ok": false` diyor ya da okunamıyor, veya etkin bir `R20-verify` uyarısı var | "Tam zincir denetimi ({zaman}) başarısız." |
| 3 | Dikkat | Mühürlü bir gün son 35 günde doğrulanmadı | "Son 30 günde {n} mühürlü gün son 35 günde doğrulanmadı." |
| 4 | Dikkat | Dünden eski bir gün hâlâ mühürlenmeyi bekliyor | "Son 30 günde {n} gün henüz mühürlenmedi: {günler}." |
| 5 | Dikkat | Son tam zincir denetimi 35 günden eski; ya da hiç çalışmamış ve ilk mühürlü gün en az 35 gün önce | "Son tam zincir denetimi {zaman}; 35 günden eski." — hiç çalışmamışsa: "Tam zincir denetimi hiç çalışmadı." |
| 6 | Bilgi yok | 30 gün içinde mühürlü gün yok | "Son 30 günde mühürlü gün yok." |
| 7 | Tamam | — | "Son 30 günün mühürlü {n} günü doğrulandı; tam zincir denetimi {zaman} tarihinde başarılı." — ilk tam denetimden önce: "… tam zincir denetimi her ayın 1'inde çalışır, henüz çalışmadı." |

Okunamayan bir `last-full.json` (bozuk ya da izin verilmeyen dosya) başarısız bir tam denetim
sayılır (satır 2, zaman "—"); "denetlenemedi" diye geçiştirilmez. Geç damgalanmış, verisi olmayan,
silinmiş ve süren günler durumu değiştirmez; yalnızca sayılır (geç damgalanmış ama son 35 günde
doğrulanmış bir gün "doğrulanmış" sayılır). Bilgiler: doğrulanmış, doğrulanmamış, başarısız ve "veri
yok ya da silinmiş" gün sayıları, son tam zincir denetimi ("{zaman}, başarılı|başarısız"),
doğrulama görevleri ("günlük son 2 gün, haftalık son 90 gün, aylık tüm zincir").

**Ne yapmalı?**

- Başarısız gün ya da başarısız tam denetim (satır 1-2): aynı gün inceleyin. Mühürleme sayfasında
  günü açın, **Doğrula** ile yeniden doğrulayın; ham kayıtları ve kasayı **silmeyin, değiştirmeyin**.
  Bağımsız doğrulama: [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md). `R20-verify`, gün yeniden
  doğrulanınca kendiliğinden çözülür. Dosya okunamıyorsa Windows haklarına bakın (bölüm 6).
- Doğrulanmamış gün (satır 3): VerifyWeekly ve VerifyMonthly görevleri çalışıyor mu (Sistem →
  Zamanlanmış görevler)? Mühürleme sayfasındaki **Doğrula** ile hemen doğrulayabilirsiniz.
- Bekleyen gün (satır 4): kart 2'deki gibi Seal görevine ve `R20-seal` uyarılarına bakın.
- Tam denetim eski ya da hiç çalışmamış (satır 5): VerifyMonthly görevini denetleyin; yönetici olarak
  açılmış PowerShell'de elle başlatabilirsiniz (bütün zinciri okur, uzun sürebilir):

  ```powershell
  Start-ScheduledTask -TaskPath \ARCILOGGER\ -TaskName VerifyMonthly
  ```

### 2.4 Kart 4 — Saklama süresi

**Ne denetler?** Kasanın en az 730 günlük mühürlü kaydı tuttuğunu (kurulum yeterince eskiyse) ve
saklama kapalıyken gereğinden eski günlerin kasada birikmediğini.

**Neyi okur?** `[retention] enabled` (varsayılan `false`) ve `[retention] days` (varsayılan 760,
en az 730); kasadaki en eski mühürlü gün; saklama görevinin silme kayıtları (`vault\deletions`);
etkin `R20-retention` uyarıları. "İlk mühürlü gün", kasadaki en eski gün ile saklamanın sildiği en
eski günden hangisi daha eskiyse odur.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | `info` dışında etkin bir `R20-retention` uyarısı var | "Saklama görevinin açık bir uyarısı var." |
| 2 | Sorun | İlk mühürlü gün en az 730 gün önce, ama kasadaki en eski mühürlü gün 730 günden yeni | "Kurulum 730 günden eski, ancak en eski mühürlü gün {en eski} ({yaş} gün önce)." |
| 3 | Dikkat | Saklama kapalı ve kasada `[retention] days`'ten eski mühürlü gün var | "Saklama kapalı ve {gün} günden eski {n} mühürlü gün kasada duruyor (en eskisi {en eski}). Kişisel verilerin gereğinden uzun saklanmaması için saklamayı açın." |
| 4 | Bilgi yok | Mühürlü gün yok | "Henüz mühürlü gün yok." |
| 5 | Tamam | — | En eski gün en az 730 gün önceyse: "En eski mühürlü gün {en eski} ({yaş} gün önce); 730 günlük süre kasada." — değilse: "… kurulum 730 günden genç." |

Kart, uyarıları tekilleştirme anahtarıyla değil **kuralıyla** okur: saklama görevinin uyarıları
anahtarsız aktarılır ve her reddedilen çalışma ayrı bir uyarıdır. Tamamlanan bir imhanın `info`
düzeyindeki notu kartı etkilemez. Kart yalnızca mühürlü günleri değerlendirir; veritabanı
tablolarının saklama süresi (`db.retention_days`) bu kartın konusu değildir. Bilgiler: en eski
mühürlü gün, saklama ("açık, {gün} gün" ya da "kapalı"), saklama ile silinen gün sayısı.

**Ne yapmalı?**

- Saklama uyarısı (satır 1): imha tutanağını ve `D:\ARCILOGGER\logs\core.log` dosyasını inceleyin;
  reddedilen ya da yarım kalan çalışmanın nedenini giderin, imha bir sonraki çalışmada yeniden
  denenir. Bu uyarılar kendiliğinden çözülmez: nedeni not olarak ekleyip Güvenlik sayfasında kapatın.
- Kasada saklama süresi içindeki günler eksik (satır 2): 730 günden genç, saklanması gereken
  günler kasadan silinmiş ya da kaybolmuş. Silme kayıtlarını, Güvenlik sayfasındaki imha uyarılarını ve denetim kaydını inceleyin;
  replika açıksa o günlerin ikinci kopyası replika hedefindedir. Eksik dönemi hukuk müşavirinizle
  değerlendirin.
- Saklama kapalı (satır 3): kişisel verilerin gereğinden uzun saklanmaması için Ayarlar → Saklama'da
  saklamayı açın; silinecek gün sayısı önceden gösterilir ve yazılarak onaylanır
  ([AYARLAR.md](AYARLAR.md) bölüm 1). Saklama süresini hukuk müşavirinizle belirleyin (en az 730 gün).

### 2.5 Kart 5 — Kamu SM kontörü ve zaman damgası sertifikası

**Ne denetler?** Damgaların Kamu SM üretim sunucusundan alındığını, kontörün yettiğini ve en yeni
damganın TSA sertifikasının geçerli olduğunu.

**Neyi okur?** `[tsa]` ayarları (sağlayıcı ve sunucu: Zamane istemcisi ve Kamu SM üretim sunucusu mu);
kontör geçmişi `state\credit.jsonl` (Credit görevi her gün 06:00, ayrıca Mühürleme sayfasındaki
**Kontör sorgula**); `[tsa] credit_alert_threshold` (varsayılan 500); son 30 günün kullanımından
tahmini gün sayısı (Panel'deki hesap); en yeni mühürlü günün durum dosyasındaki
`tsa_cert_not_after` (`state\seal\<gün>.json`). `R20-credit` uyarısı kartta listelenir; durumu
aşağıdaki koşullar belirler.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | Damgalar Kamu SM üretim sunucusundan alınmıyor (deneme, RFC 3161 ya da Kamu SM test sunucusu) | "Damgalar Kamu SM üretim sunucusundan alınmıyor (sağlayıcı: {sağlayıcı}). Kamu SM bağlantısını Ayarlar → Kamu SM'de tamamlayın." |
| 2 | Sorun | En son bilinen kontör 0 ya da daha az | "Kamu SM kontörü bitti." |
| 3 | Sorun | En yeni damganın TSA sertifikasının süresi dolmuş | "Son damganın TSA sertifikasının süresi {tarih} tarihinde doldu." |
| 4 | Dikkat | Kontör eşiğe eşit ya da altında | "Kalan kontör {kontör}; eşik {eşik}. Tahmini {gün} gün yeter." |
| 5 | Dikkat | Hiç kontör okuması yok ya da son okuma iki günden eski | "Kontör bilinmiyor ya da son okuma ({zaman}) iki günden eski." |
| 6 | Dikkat | Sertifikanın bitmesine 180 gün ya da daha az var | "Son damganın TSA sertifikası {tarih} tarihinde doluyor ({gün} gün kaldı)." |
| 7 | Bilgi yok | Henüz mühürlü gün yok | "Henüz Kamu SM ile damgalanmış gün yok." |
| 8 | Tamam | — | "Kalan kontör {kontör} (eşik {eşik}); son damganın TSA sertifikası {gün} gün daha geçerli." |

Satır 2 kontör günlüğündeki eksi bir okumayı da "bitti" sayar. Yeni bir kurulum, Kamu SM üretim
bağlantısı tamamlanana kadar bu kartta **Sorun** gösterir (satır 1); aynı durumda kart 2 yalnızca
**Dikkat** gösterir (bölüm 2.2 satır 7). "İki gün", günlük Credit görevinin iki kez kaçırılmasıdır;
180 gün, şartnamenin öngördüğü süredir. Damga sunucusu adları: "Kamu SM üretim", "Kamu SM test",
"Deneme (mock)", "RFC 3161". Bilgiler: damga sunucusu, kalan kontör, tahmini gün, eşik, son okuma,
TSA sertifikası bitişi, kalan gün.

**Sahada doğrulayın** (kartın notu): "Kontör değeri Kamu SM istemcisinin çıktısından okunur; okuma
biçimi sahada doğrulanmalıdır." ([kamusm.md](kamusm.md) bölüm 3, K4; bölüm 6.)

**Ne yapmalı?**

- Üretim sunucusu değil (satır 1): Ayarlar → Kamu SM sihirbazını tamamlayın: test sunucusuyla
  deneme, kök sertifika parmak izlerinin Kamu SM'nin yayımladığı değerlerle karşılaştırılması, sonra
  **Üretime geç** ([kamusm.md](kamusm.md), [KURULUM.md](KURULUM.md) bölüm 10).
- Kontör bitti ya da azaldı (satır 2, 4): kontör satın alın ([kamusm.md](kamusm.md) bölüm 1).
  Kontör biterse günler mühürlenemez; kart 2 ve `R20-seal` bunu gösterir.
- Kontör bilinmiyor (satır 5): Mühürleme sayfasında **Kontör sorgula** (`seal.manage`) ile okuyun;
  Credit görevinin son kanıtına Sistem → Zamanlanmış görevler'de bakın. Okuma hep başarısızsa okuma
  biçimini sahada doğrulayın (bölüm 6).
- Sertifika doldu ya da doluyor (satır 3, 6): Mühürleme sayfasında son mühürlü günü ve Seal görevini
  denetleyin. Uzun süreli geçerlilik ve yenileme manifesti: [kamusm.md](kamusm.md) bölüm 6; ne
  yapılacağını hukuk müşavirinizle değerlendirin.

### 2.6 Kart 6 — Sunucu saati

**Ne denetler?** Sunucu saatinin saat sunucusundan sınırdan az saptığını ve ölçümün güncel olduğunu.

**Neyi okur?** En yeni saat ölçümü `state\clock\<gün>.jsonl` (bugünün, yoksa dünün dosyası;
ClockCheck görevi saatte bir); `[clock] ntp_server` (yerel saat kaynağı, varsayılan boş),
`[clock] fallback_server` (yedek sunucu, varsayılan `time.ume.tubitak.gov.tr`), `[clock]
alert_offset_ms` (sınır, varsayılan 1000 ms); etkin `R20-clock` uyarıları (`R20-clock` ile başlayan
bütün anahtarlar).

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | Son 3 saatte bir ölçüm var ve sapmanın mutlak değeri sınıra eşit ya da büyük | "Saat farkı {fark} ms; sınır {sınır} ms." |
| 2 | Sorun | Son 3 saatteki ölçüm yanıt alamadı, ya da etkin bir `R20-clock` uyarısı var | "Saat sunucusuna ulaşılamadı ya da saat uyarısı açık ({sunucu})." |
| 3 | Dikkat | Hiç ölçüm yok ya da en yenisi 3 saatten eski | "Son saat ölçümü {zaman}; 3 saatten eski (ClockCheck saatte bir çalışır)." — hiç yoksa: "Henüz saat ölçümü yok." |
| 4 | Dikkat | `[clock] ntp_server` boş | "Yerel saat kaynağı ayarlanmamış; saat yalnızca yedek sunucuyla ({yedek}) eşitleniyor." |
| 5 | Tamam | — | "Saat farkı {fark} ms ({sunucu}, {zaman}); sınır {sınır} ms." |

Kart sınıra **eşit** bir sapmayı da sorun sayar ("1 saniyeden az"); Panel'in saat kutusu eşit değeri
hâlâ kabul eder. Bilgiler: son ölçüm, sunucu, fark, sınır, yerel saat kaynağı ("ayarlı" ya da "yok").

**Ne yapmalı?**

- Sapma ya da ulaşılamayan sunucu (satır 1-2): Windows Saat hizmetini ve saat sunucusunu denetleyin
  ([KURULUM.md](KURULUM.md) bölüm 1.3); Sistem → Saat bloğu son ölçümleri ve ApplySystem'in Windows
  Saati'ne uyguladığı sunucuları gösterir. Eşik içindeki sonraki ölçüm uyarıyı çözer. Mühürlemenin
  gördüğü bir saat atlaması ya da mühürlenmeyecek bir spool dosyası için açılan `R20-clock:<anahtar>`
  uyarıları kendiliğinden çözülmez: uyarıda adı geçen dosyayı silmeden inceleyin, nedeni not olarak
  ekleyip kapatın ([UYARILAR.md](UYARILAR.md) bölüm 2). Kart o zamana kadar **Sorun** gösterir.
- Ölçüm eski (satır 3): ClockCheck görevinin son kanıtına Sistem → Zamanlanmış görevler'de bakın.
- Yerel saat kaynağı yok (satır 4): Ayarlar → Genel'de yerel saat kaynağını (genellikle ana
  yönlendirici) girin; kaydetmeden önce denenir ([AYARLAR.md](AYARLAR.md) bölüm 1).

### 2.7 Kart 7 — İçerik filtresi kayıtları

**Ne denetler?** FortiGate'in web filtresi ve DNS filtresi kayıtlarının, kayıt beklenen günlerde
gelmeye devam ettiğini. Yalnızca etkin, yapılandırılmış (bilinmeyen bir gönderici olmayan) ve türü
`fortigate` olan bir kaynak varsa geçerlidir; diğer üreticilerin içerik filtresi kayıtları bu kartın
konusu değildir.

**Neyi okur?**

- `search_counts` tablosundaki saatlik sayımlar (`fg_web`, `fg_dns`; FortiGate kaynakları, son
  8 gün): bugünkü ve son 7 günün web ve DNS filtresi kayıt sayısı;
- `fg_web` ve `fg_dns` tablolarındaki en yeni kayıt zamanı (son 8 gün);
- web filtresi profillerinin adları: son 24 saatin en yeni 10.000 `fg_web` satırından;
- ayarlar (Ayarlar → Uygulama ayarları → Uyarılar ve e-posta):

| Ayar | Varsayılan | Sınırlar | Anlamı |
|---|---|---|---|
| `alerts.filter_days` | `[1,2,3,4,5,6,7]` (haftanın her günü) | 1–7 (1 = Pazartesi, 7 = Pazar); boş olabilir | İçerik filtresi kaydı beklenen ISO hafta günleri |
| `alerts.filter_skip_holidays` | `true` (açık) | — | Açıkken `usage.holidays` listesindeki günlerde kayıt beklenmez; kapalıyken resmî tatiller de beklenen gün sayılır |
| `alerts.filter_quiet_hours` | `24` | 6–168 | Beklenen günlerde kayıtsız geçebilecek en uzun süre (saat) |

**Beklenen gün:** hafta günü `alerts.filter_days` içinde olan ve (`alerts.filter_skip_holidays`
açıkken) `usage.holidays` listesinde olmayan gün. Varsayılan olarak içerik filtresi kaydı **haftanın
her günü** beklenir, `usage.holidays` listesindeki günler hariç (sahibin kararı, 2026-10-28); ikisi
de değiştirilebilir. `usage.holidays` (Ayarlar → Uygulama ayarları → Kullanım analizi → Tatiller)
yalnızca sabit tarihli resmî tatillerle gelir ("AA-GG" her yıl, "YYYY-AA-GG" bir kez); dinî
bayramların tarihlerini her yıl yönetici girer.

**Pencere:** son `alerts.filter_quiet_hours` saat. Kayıt, pencerenin dokunduğu her gün beklenen
bir günse **ve** pencerede ne web ne de DNS filtresi kaydı geldiyse eksik sayılır; iki türden biri
yeter.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Bilgi yok | Etkin, yapılandırılmış bir FortiGate kaynağı yok | "FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor." |
| 2 | Sorun | Pencerede hiç kayıt yok ve pencerenin dokunduğu her gün beklenen gün | "Beklenen günlerde son {saat} saattir web ya da DNS filtresi kaydı gelmedi (son kayıt: {son})." |
| 3 | Dikkat | Pencerede hiç kayıt yok, ama pencere beklenmeyen bir güne dokunuyor | "Son {saat} saatte içerik filtresi kaydı yok; bu süre beklenmeyen bir gün içeriyor (son kayıt: {son})." |
| 4 | Dikkat | Bir tür (web ya da DNS) son 7 günde geldi ama pencerede gelmedi; diğeri geliyor | "{tür} filtresi kaydı {saat} saattir gelmiyor; diğer tür geliyor." |
| 5 | Tamam | — | "Son kayıt {son}; bugün {web} web, {dns} DNS filtresi kaydı." |

`{son}` son 8 günde kayıt yoksa "—" olur. Örnek: varsayılan ayarlarla 1 Ocak (`01-01`) beklenmeyen
bir gündür; 1 Ocak'a dokunan 24 saatlik bir pencerede kayıt gelmezse kart **Sorun** değil
**Dikkat** gösterir. Bilgiler: son kayıt, bugünkü web ve DNS filtresi kayıt sayısı, son 7 gün
("13.10: 1234 · 12.10: …"), web filtresi profilleri (en çok 10 ad), DNS filtresi profili ("FortiGate
DNS filtresi kayıtlarından profil adı okunmuyor (sahada doğrula)."), beklenen günler (varsayılan
"Pzt, Sal, Çar, Per, Cum, Cmt, Paz"), resmî tatiller ("beklenmez" ya da "beklenen gün sayılır").

**Uyarı:** aynı koşul (satır 2) `R20-filter` uyarısını açar (önem yüksek, tek anahtar
`R20-filter:none`); yeni bir içerik filtresi kaydı gelince kendiliğinden çözülür
([UYARILAR.md](UYARILAR.md) bölüm 2).

**Sahada doğrulayın** (kartın notu): "Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını
kaydettiği ve ARCILOGGER'a gönderdiği sahada doğrulanmalıdır."

**Ne yapmalı?**

1. Kartın bilgilerinde son kaydı ve beklenen günleri görün; Arama sayfasında web ve DNS filtresi
   kayıtlarını arayın.
2. Güvenlik duvarında web ve DNS filtresi kullanan politikalarda kayıt tutmanın açık olduğunu ve
   kayıtların ARCILOGGER'a gönderildiğini denetleyin ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2;
   sahada doğrulayın). Kart 1 kaynağın kendisinin sessiz olup olmadığını gösterir.
3. Kurum bu günlerde çalışmıyorsa (ör. hafta sonu kapalıysa ve o günlerde hiç trafik yoksa) beklenen
   günleri ve tatil ayarını Ayarlar → Uygulama ayarları → Uyarılar ve e-posta'da düzeltin; dinî
   bayramları `usage.holidays`'e girin.
4. Yeni bir içerik filtresi kaydı gelince uyarı kendiliğinden kapanır ve kart düzelir.

### 2.8 Kart 8 — DHCP ve ARP kayıtları (erişim kayıtları)

**Ne denetler?** IP adreslerini cihazlara bağlayan kira kanıtının (DHCP ve ARP kayıtları) son 24
saatte geldiğini ve açık bir kira kanıtı sorunu olmadığını. Kart yapılandırılmış kira kanıtını izler,
"DHCP ve ARP" kelimesi kelimesine değil: yalnızca FortiGate kullanan bir sitede ARP kaydı yoktur.

**Neyi okur?** `search_counts` tablosunda son 24 saatin `mt_dhcp` (MikroTik DHCP), `mt_arp` (MikroTik
ARP) ve `fg_dhcp` (FortiGate DHCP) satırları, yalnızca izlenen kaynaklardan; etkin `R20-lease:`
uyarıları (kira kanıtı denetimleri: neden başına ayrı bir uyarı).

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Bilgi yok | İzlenen kaynak yok | "İzlenen kaynak yok; erişim kaydı beklenmiyor." |
| 2 | Sorun | Önemi yüksek ya da kritik olan etkin bir `R20-lease` uyarısı var | "{n} kira kanıtı sorunu açık." |
| 3 | Dikkat | Başka önemde etkin bir `R20-lease` uyarısı var | "{n} kira kanıtı sorunu açık." |
| 4 | Dikkat | Son 24 saatte üç tablonun hiçbirinde satır yok | "Son 24 saatte DHCP ya da ARP kaydı gelmedi." |
| 5 | Tamam | — | "Son 24 saatte {tablolar} kayıtları geldi; açık kira kanıtı uyarısı yok." |

Bilgiler: her tablo için "{n} satır (24 saat)"; açık kira kanıtı sorunları: uyarıların başlıkları
(neden, sayılar ve yapılandırılmış ağlar; tekilleştirme anahtarları hiçbir zaman), en çok 5 tane.

**Ne yapmalı?**

- Kira kanıtı sorunu (satır 2-3): Güvenlik sayfasında uyarıyı açın; nedeni ve ne yapılacağı uyarının
  ayrıntısındadır (Kaynaklar → Kira kanıtı; [UYARILAR.md](UYARILAR.md) bölüm 2, `R20-lease`).
- Kayıt yok (satır 4): DHCP sunucusunun (ana yönlendirici ya da FortiGate) kaynak olarak tanımlı ve
  sessiz olmadığını (kart 1), cihazda DHCP ve ARP kayıtlarının gönderildiğini denetleyin
  ([DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 3: MikroTik DHCP ve saatlik `ARP-LOG` satırları;
  bölüm 4: kaynak ve test; komutları sahada doğrulayın).

### 2.9 Kart 9 — İkinci kopya (replika)

**Ne denetler?** Mühürlü günlerin ikinci kopyasının replika hedefine alındığını ve denetlendiğini.

**Neyi okur?** `[replica] enabled` (varsayılan `false`); dünle biten son 7 günün mühürlü günleri
için replika durum dosyaları `state\replica\<gün>.json` (kopyanın durumu ve denetlenme zamanı);
`alerts.replica_stale_hours` (varsayılan 48, bilgi olarak); etkin `R20-replica` uyarıları
(`R20-replica` ile başlayan bütün anahtarlar). Replika hedefinin yolu hiçbir zaman gösterilmez.

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | Replika kapalı | "Replika kapalı: mühürlü günlerin ikinci kopyası alınmıyor." |
| 2 | Sorun | Etkin bir `R20-replica` uyarısı var | "Replika uyarısı açık: kopya gecikti ya da kopyalama başarısız." |
| 3 | Dikkat | 7 günün mühürlü bir gününün denetlenmiş kopyası yok | "{n} mühürlü gün henüz kopyalanmadı (en eskisi {gün})." |
| 4 | Bilgi yok | 7 günde mühürlü gün yok | "Son 7 günde mühürlü gün yok." |
| 5 | Tamam | — | "Son 7 günün mühürlü {n} günü replika hedefine kopyalandı ve denetlendi." |

Replika varsayılan olarak kapalıdır; bu yüzden yeni bir kurulum bu kartta **Sorun** gösterir.
Replika görevi saatte bir (00:40'tan itibaren) çalışır: dünün kopyası sabah kısa bir süre bekleyebilir
(satır 3). Bilgiler: replika ("açık" ya da "kapalı"), son kopya, bekleyen gün, gecikme eşiği (saat).

**Sahada doğrulayın** (kartın notu): "Replika hedefinin değiştirilemez (WORM) olduğu ARCILOGGER'dan
denetlenemez; sahada doğrulayın."

**Ne yapmalı?**

- Replika kapalı (satır 1): Ayarlar → Replika'da hedefi (`\\SUNUCU\paylaşım`), kullanıcıyı ve parolayı
  girip açın; kaydederken bağlantı denenir ([KURULUM.md](KURULUM.md) bölüm 10, "Replika").
- Uyarı ya da bekleyen gün (satır 2-3): hedefe erişimi denetleyin (Ayarlar → Replika → **Bağlantıyı
  test et**, ya da sunucuda):

  ```powershell
  & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-core.exe" replica --test
  ```

  Başarılı bir kopya uyarıyı kendiliğinden kapatır. "Replika farklı" (kritik) uyarısı kendiliğinden
  kapanmaz: farkı inceleyin, sonucu not olarak ekleyip kapatın ([UYARILAR.md](UYARILAR.md) bölüm 2).

### 2.10 Kart 10 — Yapılandırma ve veritabanı yedekleri

**Ne denetler?** Gece veritabanı yedeğinin açık ve güncel olduğunu ve yapılandırma dosyalarının
çevrim dışı kopyasının, yapılandırmanın şimdiki hâli için onaylandığını.

**Neyi okur?** `state\backup\last.json` (Backup görevi her gün 01:40: sonuç, bitiş zamanı, boyut);
`[backup] enabled` (varsayılan `true`); `alerts.backup_stale_hours` (36) ve
`alerts.backup_missing_hours` (48); kurulumun yaşı (veritabanındaki ilk worker işi kaydı,
`job_runs`); denetim kaydındaki en yeni `compliance.config_backup_confirm` girdisi ve onun SHA-256
değeri ile `arcilogger.toml` dosyasının şimdiki SHA-256 değeri; etkin `R20-backup` uyarıları
(`R20-backup` ile başlayan bütün anahtarlar: `:missing`, `:prune`, `:clock` dahil).

| Sıra | Durum | Koşul | Gerekçe |
|---|---|---|---|
| 1 | Sorun | `[backup] enabled = false` | "Gece veritabanı yedeği kapalı." |
| 2 | Sorun | Etkin bir `R20-backup` uyarısı var, ya da `last.json` `"ok": false` diyor ya da okunamıyor | "Son veritabanı yedeği başarısız ya da yedek uyarısı açık." |
| 3 | Bilgi yok | `last.json` yok ve kurulum `alerts.backup_missing_hours`'tan genç | "İlk veritabanı yedeği henüz alınmadı (Backup görevi 01:40'ta çalışır)." |
| 4 | Dikkat | Son başarılı yedek `alerts.backup_stale_hours`'tan eski (ya da hiç başarılı yedek yok) | "Son başarılı veritabanı yedeği {zaman}; {saat} saatten eski." |
| 5 | Dikkat | Yapılandırma kopyası hiç onaylanmadı | "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3)." |
| 6 | Dikkat | Son onay, yapılandırmanın başka bir SHA-256 değeri içindi | "Yapılandırma son onaydan ({zaman}) sonra değişti; yeni bir çevrimdışı kopya alın." |
| 7 | Tamam | — | "Son veritabanı yedeği {zaman} ({boyut}); yapılandırma kopyası {onay} tarihinde onaylandı." |

Okunamayan bir `last.json` (bozuk ya da izin verilmeyen dosya) başarısız bir yedek sayılır (satır 2),
worker'ın yedek denetimi gibi. Kart, onayı kimin verdiğini göstermez (denetim kaydı gösterir).
Bilgiler: son veritabanı yedeği, boyut, gece yedeği ("açık" ya da "kapalı"), yapılandırma kopyası
onayı (zaman ya da "hiç").

**"Yapılandırma yedeğini aldım" düğmesi.** Kart 10'da, yalnızca yöneticiye (`settings.manage`)
görünür. Onay penceresi şunu söyler:

> arcilogger.toml, secret.key ve Caddy CA klasörünün (özel anahtarıyla) şifreli, çevrim dışı ve
> yedeklerden ayrı saklanan bir ortama kopyalandığını onaylıyorsunuz (secrets.json isteğe bağlı;
> YEDEKLEME.md bölüm 3). Onay, yapılandırmanın şimdiki SHA-256 değeriyle denetim kaydına yazılır.

Onaylayınca sunucu `arcilogger.toml` dosyasının şimdiki SHA-256 değerini okur ve denetim kaydına
`compliance.config_backup_confirm` girdisini bu değerle yazar ("Yapılandırma yedeği onaylandı");
kart hemen güncellenir. Onay tekrarlanabilir (her seferinde yeni bir girdi). Dosya okunamazsa onay
kaydedilmez: "Yapılandırma dosyası okunamadı; yedek onayı kaydedilmedi." (409
`config_backup_unreadable`).

Kart yalnızca `arcilogger.toml` dosyasının değiştiğini görür: Ayarlar'dan yapılan ve bu dosyaya
yazılan her değişiklik (ör. Genel, Kaynaklar, Kamu SM, Replika, Yedekleme) kartı **Dikkat**'e
çevirir; veritabanındaki uygulama ayarları çevirmez. `secret.key` ya da Caddy CA yenilendiğinde kart
bunu görmez: o zaman da yeni bir çevrim dışı kopya alıp yeniden onaylayın. Düğme yalnızca sizin
yaptığınızı kaydeder; kopyayı ARCILOGGER almaz ve denetleyemez.

**Ne yapmalı?**

- Yedek kapalı (satır 1): Ayarlar → Yedekleme'de açın; veritabanını başka bir ürün yedekliyorsa
  bu kart **Sorun** göstermeye devam eder ([YEDEKLEME.md](YEDEKLEME.md) bölüm 2).
- Başarısız yedek ya da uyarı (satır 2): `D:\ARCILOGGER\state\backup\last.json` ve
  `D:\ARCILOGGER\logs\backup.log` dosyalarına ve Güvenlik sayfasındaki `R20-backup` uyarısına bakın
  ([YEDEKLEME.md](YEDEKLEME.md) bölüm 2 ve 7).
- Eski yedek (satır 4): Backup görevinin son kanıtına Sistem → Zamanlanmış görevler'de bakın.
- Onay yok ya da yapılandırma değişti (satır 5-6): [YEDEKLEME.md](YEDEKLEME.md) bölüm 3'teki gibi
  `arcilogger.toml`, `secret.key` ve Caddy CA klasörünü (özel anahtarıyla) şifreli, çevrim dışı ve
  yedeklerin kopyalarından ayrı saklanan bir ortama kopyalayın (`secrets.json` isteğe bağlı), sonra
  **Yapılandırma yedeğini aldım**'a basın.

## 3. Uyum Durumu Raporu (PDF)

Uyum Durumu sayfasındaki **Uyum Durumu Raporu (PDF)** düğmesi kartların o anki durumunu A4 bir PDF
olarak indirir. Yönetici ve denetçi indirebilir (`compliance.read`).

- **İstek üzerine, saklanmadan:** her indirmede kartlar yeniden hesaplanır (sayfanın 30 saniyelik
  sonucu kullanılmaz). Rapor sunucuda saklanmaz, zamanlanmış olarak üretilmez ve e-postayla
  gönderilmez; Raporlar sayfasının bir rapor türü değildir.
- **Zaman damgası yok:** rapor Kamu SM ile damgalanmaz (kontör harcamaz). Kanıtı denetim kaydındaki
  SHA-256 değeridir.
- **Denetim kaydı:** rapor gönderilmeden önce `compliance.report_export` ("Uyum Durumu Raporu
  indirildi") girdisi yazılır: dosyanın SHA-256 değeri ve her durumdaki kart sayısı (tamam, dikkat,
  sorun, bilgi yok). Yanıtın `X-Arci-Sha256` başlığı aynı değeri taşır. Dosya adı
  `ARCILOGGER-uyum-durumu-YYYYAAGG-SSDD.pdf`'dir (Türkiye saati).
- **İçerik, bu sırayla:** başlık ("Uyum Durumu Raporu / Compliance status report"); kurum adı
  ("Kurum / Site: …", ayarlanmamışsa "—"); "Oluşturulma / Generated: {zaman} (Europe/Istanbul)";
  "Oluşturan / Exported by: {görünen ad} ({kullanıcı adı})": raporu indiren ARCILOGGER hesabı;
  özet satırı ("{toplam} denetimden {tamam} tanesi tamam" ve İngilizcesi); bölüm 1'deki not
  (Türkçe ve İngilizce); sonra her kart için: "{numara}. {Türkçe başlık} / {İngilizce başlık}",
  köşeli parantez içinde metin olarak durum (**[TAMAM] / [OK]**, **[DİKKAT] / [ATTENTION]**,
  **[SORUN] / [PROBLEM]**, **[BİLGİ YOK] / [NO DATA]**), Türkçe ve İngilizce gerekçe, iki sütunlu
  bilgi tablosu (Türkçe etiket ve değer), varsa sahada doğrulama notu, açık uyarılar ("kural, önem,
  açılış zamanı") ve denetim zamanı.
- **Her sayfada:** üstte başlık ve kurum adı; altta ARCILOGGER sürümü ve oluşturulma zamanı,
  "Sayfa n / N" ve 5651 raporundaki not: "ARCILOGGER, 5651 yükümlülüklerini desteklemek için
  tasarlanmıştır; bu rapor hukuki uygunluk beyanı değildir."
- **Kişisel veri:** kartlar gibi raporda da log öznelerine ait veri yoktur. Tek istisna raporu
  indiren hesabın görünen adı ve kullanıcı adıdır.

Rapor teknik denetimlerin bir dökümüdür; hukuki bir değerlendirme değildir. Raporu bir denetime
sunmadan önce metinlerini hukuk müşavirinizle gözden geçirin.

## 4. Sistem sayfası

**Sistem** (`/system`) sunucunun kaynaklarını, hizmetlerini, zamanlanmış görevlerini ve saatini
gösterir; yalnızca yöneticiye açıktır (`system.read`) ve yalnızca okur. Görüntüleme denetim kaydına
yazılmaz. Sayfa 30 saniyede bir arka planda yenilenir. Hizmet ve görev durumları Windows Hizmet
Denetim Yöneticisi'ne ya da Görev Zamanlayıcı'ya sorulmaz: ARCILOGGER'ın zaten okuyabildiği durum
dosyalarından, kanıt dosyalarından ve veritabanından çıkarılır. Bloklar bu sıradadır:

### 4.1 Kaynak kullanımı

İşlemci (%), bellek (kullanılan ve toplam) ve çalışma süresi, Windows API'siyle okunur (ek program
yok). İşlemci kullanımı iki okuma arasındaki farktır: ilk istekte "ölçülüyor" yazar, sonraki
isteklerde son okumadan bu yana geçen sürenin kullanımı görünür. Bir değer okunamazsa "bu platformda
okunamıyor" yazar.

### 4.2 Diskler ve disk tahmini

Veri diski ve (başka bir sürücüyse) sistem diski, sürücü harfleriyle: toplam, kullanılan ve boş
alan, kullanım yüzdesi metin olarak ve %80 (uyarı, `alerts.disk_used_percent`) ile %95 (kritik)
işaretli bir ölçek; `R20-disk` uyarısıyla aynı sürücüler ve eşikler.

**Disk geçmişi:** worker'ın `system.sample` işi her gün 00:30'dan sonra her sürücünün toplam ve
kullanılan bayt sayısını `D:\ARCILOGGER\state\system-history.json` dosyasına yazar (gün başına bir
kayıt, aynı günün kaydı yenilenir, en yeni 120 gün tutulur). Dosyada sürücü etiketleri ve bayt
sayılarından başka bir şey yoktur. Bozuk bir dosya bu örnekle yeniden başlatılır; tahmin o zaman yedi
yeni örnek bekler.

**Tahmin:** sürücü başına en yeni 30 günlük örneğe düz bir çizgi uydurulur (en küçük kareler).

| Durum | Ne zaman | Gösterilen metin |
|---|---|---|
| Yetersiz | 7'den az örnek | "Tahmin için en az 7 günlük ölçüm gerekiyor ({örnek}/7)." |
| Artmıyor | Eğim sıfır ya da eksi | "Son 30 günde disk kullanımı artmadı." |
| Tahmin | Eğim artı | "Bu hızla %{uyarı} eşiğine {uyarı tarihi}, dolmaya {dolma tarihi} (~{gün} gün)." |

- Disk **şu anda** uyarı eşiğinin üstündeyse uyarı tarihi verilmez; bunu çizgi değil o anki ölçüm
  (yoksa en yeni örnek) belirler. Ölçüm eşiğe henüz varmamışken çizgi geçmişte eşiği geçmişse uyarı
  tarihi "bugün" olur.
- 100 yıldan (36.500 gün) uzaktaki bir tarih verilmez: günde birkaç bayt büyüyen bir disk "tahmin"
  durumunda kalır, ama tarihsiz.
- Düz bir çizgi bilerek basit bir modeldir: haftalar sonrası için kaba bir yol göstericidir.

Disk dolarsa ham loglar yazılamaz (`R20-spool`): yer açmak için ham kayıtları ve kasayı **silmeyin**;
diski büyütün ya da başka dosyaları taşıyın ([UYARILAR.md](UYARILAR.md) bölüm 2, `R20-disk`).

### 4.3 Veritabanı

Veritabanının toplam boyutu ve en büyük 10 tablo (bölümleriyle birlikte; TimescaleDB kullanılıyorsa
hiper tabloların boyutu). Değerler 5 dakika saklanır ve ölçüm zamanıyla gösterilir; ölçülemezse
boş kalır.

### 4.4 Toplayıcı ve İçe aktarma

İki blok, toplayıcının ve içe aktarmanın (ingest) durum dosyalarından (`state\collector-status.json`,
`state\ingest-status.json`): dosya 2 dakikadan yeniyse "çalışıyor", eskiyse "güncel değil", yoksa
"yok".

- **Toplayıcı:** yapılandırılmış her kaynak için mesaj ve bayt sayısı ve son alım zamanı; yapılandırmada
  olmayan göndericiler yalnızca sayılır (gönderici sayısı, mesaj, bayt), adresleri gösterilmez
  (bunlar Kaynaklar sayfasında **Bilinmeyen** süzgecindedir); bilinmeyen gönderici ve taşıma yolu
  nedeniyle atılan mesajlar, yazma hataları ve hatanın başlangıcı, kapatılan yarım satırlar, TCP
  bağlantıları ve çerçeveleme hataları.
- **İçe aktarma:** hizmetin durumu, veritabanı bağlantısı, yapılandırılmış ve etkin ayrıştırma
  süreci (parse worker) sayısı, saniyede satır; kaynak başına gecikme (bayt ve saniye), saniyede satır, bitmemiş gün sayısı
  ve bir hata olup olmadığı (hatanın metni gösterilmez); yapılandırmada olmayan kaynaklar yalnızca
  sayılır.

Bir ağ kaynağının üyeleri kaynağın adı altında toplanır. Yalnızca durum dosyasında kaydı olan
yapılandırılmış kaynaklar listelenir: henüz hiç göndermemiş bir kaynağın satırı yoktur (Kaynaklar
sayfası her yapılandırılmış kaynağı listeler).

### 4.5 Hizmetler

- **ArciCollector, ArciIngest, ArciWorker:** durum dosyaları 2 dakikadan yeniyse "çalışıyor", eskiyse
  "güncel değil", dosya yoksa "yok". **ArciApi** her zaman "çalışıyor"dur (sayfayı o yanıtlar).
- **Yönetim hizmeti (ArciAdmin):** kalp atışına göre "tamam", "güncel değil", "yok" ya da tarayıcıdan
  yönetim kapalıysa "kapalı".
- **ApplySystem:** son sonucu (`admin\system\status.json`): "tamam", "başarısız" (kodu ile), "hiç
  çalışmadı" ya da tarayıcıdan yönetim kapalıysa "kapalı".

### 4.6 Zamanlanmış görevler

`deploy\windows\tasks\` içindeki her görev için bir satır. Durum görevin **kanıt dosyasından**
çıkarılır (yollar veri klasörüne göredir): "tamam"; "gecikti" (kanıt, sınırdan eski); "yok" (henüz
kanıt yok); "kullanılmıyor" (görev bu yapılandırmada iş yapmaz); "izlenemiyor" (ArciApi'nin
okuyabileceği bir kanıt yok). Sınırlar görevin çalışma sıklığı artı bir paydır; ayar değildir.

| Görev | Çalışma | Kanıt | Gecikti | Kullanılmıyor |
|---|---|---|---|---|
| Seal | Her gün 00:10'dan sonra 5 dakikada bir | `status` değeri `deleted` olmayan en yeni `state\seal\<YYYY-AA-GG>.json` dosyasının değişme zamanı (Saklama'nın sildiği günlerin yeniden yazdığı durumlar sayılmaz) | 26 saat | — |
| VerifyDaily | Her gün 01:00 | `state\verify\last-last-1.json` ve `last-last-2.json` dosyalarının yenisindeki `at` | 26 saat | — |
| VerifyWeekly | Pazar 03:00 | `state\verify\last-last-<N>.json` (N ≥ 3) dosyalarının en yeni `at` değeri | 8 gün | — |
| VerifyMonthly | Ayın 1'i 04:00 | `state\verify\last-full.json` içindeki `at` | 32 gün | — |
| Replica | Saatte bir, 00:40'tan | son 3 günün `state\replica\<gün>.json` dosyalarındaki en yeni `verified_at` | 26 saat | `[replica] enabled = false` |
| Retention | Her gün 02:00 | okunabilir kanıt yok → "izlenemiyor" | — | `[retention] enabled = false` |
| Credit | Her gün 06:00 | `state\credit.jsonl` içindeki en yeni `time` | 26 saat | `[tsa] provider` `zamane` değil |
| ClockCheck | Saatte bir, 00:05'ten | bugünün ve dünün `state\clock\<gün>.jsonl` dosyalarındaki en yeni `time` | 2 saat | — |
| Backup | Her gün 01:40 | `state\backup\last.json` içindeki `finished` | 26 saat | `[backup] enabled = false` |
| Requests | Olayla ve 5 dakikada bir | ArciApi `state\requests` klasörünü okuyamaz → "izlenemiyor" | — | — |
| ApplySystem | Olayla ve 5 dakikada bir | `admin\system\status.json` içindeki `at` | 15 dakika | tarayıcıdan yönetim kapalı |

Doğrulama görevlerinin dosya adları komutun kendisinden gelir: `verify --last 2` `last-last-2.json`,
`verify --last 90` `last-last-90.json` yazar; zincirde istenenden az mühürlü gün varsa ad doğrulanan
gün sayısını taşır (`last-last-<gün>.json`, örneğin `last-last-30.json`); yalnızca `verify --all`
`last-full.json` yazar.
Görevin Görev Zamanlayıcı'daki son sonucunu sunucuda görmek için:

```powershell
Get-ScheduledTask -TaskPath \ARCILOGGER\ | Get-ScheduledTaskInfo | Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime
```

### 4.7 Worker işleri

Worker'ın her işi için: çalışma sıklığı ("Her {n} saniyede/dakikada/saatte bir" ya da "Her gün
{SS:DD}'den sonra"), durum ("hiç çalışmadı", "çalışıyor" (`job_runs` tablosunda bitmemiş bir çalışması var),
"başarısız" (art arda en az bir hata ya da son çalışması başarısız; veritabanına ulaşılamadığında
hata sayılmadan da), "atlandı", "tamam"), son durum, son başlangıç ve bitiş, süre (son 7 günün en yeni bitmiş
çalışmasından), art arda hata sayısı ve bir sonraki çalışma. Kaynak `state\worker-status.json` ve
veritabanındaki `job_runs` tablosudur. Hatanın metni gösterilmez: `D:\ARCILOGGER\logs\worker.log`
dosyasına bakın; art arda 3 hata `R20-job` uyarısını açar.

### 4.8 Saat

Yerel saat kaynağı (`[clock] ntp_server`), yedek sunucu (`[clock] fallback_server`), uyarı sınırı
(`[clock] alert_offset_ms`), ApplySystem'in Windows Saati'ne uyguladığı sunucular ve zamanı
(ApplySystem yoksa boş) ve son 24 saatin saat ölçümleri, en yeniden başlayarak: zaman, sunucu,
yanıt alınıp alınmadığı, sapma ve gecikme (ms), stratum. Kart 6 bu ölçümlerin en yenisini
değerlendirir.

### 4.9 Sürüm ve güncelleme

Ürün adı, API sürümü, çekirdek sürümü, yayın tarihi ve lisansın bakım süresi (Ayarlar → Lisans'taki
bilgi). Sayfa şunu da yazar: "Yükseltme için yeni çevrimdışı paketi sunucuya kopyalayıp KURULUM.md
bölüm 13'teki adımları izleyin. ARCILOGGER güncelleme indirmez." ([KURULUM.md](KURULUM.md)
bölüm 13.)

### 4.10 Panel'deki Disk kartı

Panel'de, yalnızca yöneticiye (`system.read`), bir **Disk** kartı her sürücünün kullanım yüzdesini
simge ve metinle ve tahmini tek satırda gösterir; **Sistem sayfasını aç** bağlantısı bu sayfaya
götürür.

## 5. İlk kurulum sihirbazı

**İlk kurulum sihirbazı** (`/settings/setup`) kurulumdan sonra yapılması gerekenleri sırayla gösteren
bir denetim listesidir; yalnızca yöneticiye açıktır (`settings.manage`). Kendi formu yoktur: her adım
ilgili **Ayarlar** sayfasını açar, değişiklikler oradaki gibi kaydedilir ve denetlenir
([AYARLAR.md](AYARLAR.md)). Sihirbaz hiçbir şey saklamaz: her adımın durumu sunucunun yapılandırmasından
ve istek geçmişinden çıkarılır.

**Nereden açılır?** Ayarlar ana sayfasındaki **İlk kurulum sihirbazı** kartından ve Uyum Durumu
sayfasındaki **İlk kurulum sihirbazını aç** bağlantısından (bir kart **Tamam** değilken). İlk girişten
sonra kendiliğinden açılmaz. Hesap oluşturma sayfası (`/setup`) üçüncü adım olarak "Kurulum
sihirbazı — Girişten sonra, Ayarlar'da" gösterir.

| Adım | Tamam sayılır, eğer | Açtığı sayfa |
|---|---|---|
| Yönetici ve iki adımlı doğrulama | her zaman (kullanıcı oturum açmıştır) | — |
| Site ayarları | `site_name` ya da `clock.*` ile ilgili hata düzeyinde bir yapılandırma sorunu yok | Ayarlar → Genel |
| Log kaynakları | en az bir etkin, yapılandırılmış kaynak var | Ayarlar → Kaynaklar |
| Kamu SM: test sunucusu | istek geçmişinde başarılı bir "Damga testi" var ya da üretim sunucusu seçili | Ayarlar → Kamu SM (test adımı) |
| Kamu SM: üretim | üretim sunucusu seçili ve mühürleme hazır | Ayarlar → Kamu SM (üretim adımı) |
| Replika | replika açık | Ayarlar → Replika |
| E-posta | SMTP sunucusu ve gönderen ayarlı | Ayarlar → E-posta |
| Özet | — | — |

- Sihirbaz açılınca **Tamam** olmayan ilk adım seçilir. Her adım ne için olduğunu, durumunu
  ("Tamam" / "Eksik") ve neyin eksik olduğunu yazar; **Ayarlar'da aç** ilgili sayfayı açar,
  **Bu adımı şimdilik atla** sonraki adıma geçer (adım "Eksik" kalır).
- Sihirbazdan açılan Ayarlar sayfasındaki geri dönüş bağlantısı **Kurulum sihirbazına dön** olur.
  Bu durum yalnızca o tarayıcı sekmesinde hatırlanır; sihirbaz sayfası ya da Ayarlar ana sayfası onu
  sonlandırır.
- **Log kaynakları** adımı her etkin kaynağın güvenlik duvarı durumuna göre bir not gösterir:
  ARCILOGGER'ın güvenlik duvarı kuralı 514 numaralı bağlantı noktasını etkin kaynakların adreslerine
  kendisi açar; güvenlik duvarı ARCILOGGER tarafından yönetilmiyorsa bağlantı noktasını elle açın
  ([KURULUM.md](KURULUM.md) bölüm 12). Cihaz ayarları için "DEVICE-SETUP.md bölüm 1 ve 4 (komutlar
  sahada doğrulanmalıdır)" der ([DEVICE-SETUP.md](DEVICE-SETUP.md)); web arayüzünde cihaz komutu ve
  önceden doldurulmuş bir cihaz listesi yoktur. Her yapılandırılmış kaynak için veri gelip gelmediği
  gösterilir.
- **Kamu SM** adımları şunu hatırlatır: "Test sunucusunun damgalarının yasal geçerliliği yoktur
  (Kamu SM); üretime geçmeden önce kök sertifika parmak izlerini Kamu SM'nin yayımladığı değerlerle
  karşılaştırın (sahada doğrula)." ([kamusm.md](kamusm.md).)
- **Özet:** her adım ve durumu, yapılandırma sorunlarının tamamı (her düzeyde, Türkçe ve
  İngilizce), "Kurulum sonrası denetim listesi: KURULUM.md bölüm 12" ve Uyum Durumu sayfasına bağlantı.

## 6. Sahada doğrulanacaklar

ARCILOGGER'ın kendi başına denetleyemediği ve kurulumda bir kez (ve cihazlar ya da sunucu değişince
yeniden) yerinde doğrulanması gereken konular:

| Konu | Neden sahada? | Nerede |
|---|---|---|
| DNS filtresi profili | FortiGate DNS filtresi kayıtlarında profil adı okunmuyor; DNS filtresi kayıtlarının tablosunda profil sütunu yok. Kart 7 yalnızca web filtresi profillerini listeler. Hangi DNS filtresi profilinin hangi politikada kullanıldığını cihazda doğrulayın. | Kart 7; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2 |
| Güvenlik duvarının filtre kaydı | Kart 7 yalnızca gelen kayıtları görür. Web ve DNS filtresi kullanan her politikanın bu olayları kaydettiğini ve ARCILOGGER'a gönderdiğini cihazda doğrulayın; kurum belirli günlerde hiç trafik üretmiyorsa beklenen günleri buna göre ayarlayın (bölüm 2.7). | Kart 7; [DEVICE-SETUP.md](DEVICE-SETUP.md) bölüm 2 (komutları sahada doğrulayın) |
| Kontör biçimi | Kontör, Zamane istemcisinin çıktısından `[tsa] credit_regex` ile okunur; çıktının biçimi Kamu SM'de doğrulanmadı (K4). Test sunucusunda **Kontör sorgula** ile okunan değeri Kamu SM'nin gösterdiğiyle karşılaştırın. | Kart 5; [kamusm.md](kamusm.md) bölüm 3 ve 4 |
| WORM (replika hedefi) | ARCILOGGER kopyayı alır ve denetler, ama hedefin değiştirilemez (WORM) olup olmadığını göremez. Hedef cihazın WORM ayarını ve saklama süresini yerinde doğrulayın. | Kart 9; [KURULUM.md](KURULUM.md) bölüm 10, "Replika" |
| Windows hakları | Sayfalar durum dosyalarını kurulumun `NT SERVICE\ArciApi` hesabına verdiği okuma hakkıyla okur; işlemci, bellek ve çalışma süresi aynı hesapla Windows API'sinden okunur. Requests görevinin kanıtı bilerek okunamaz ("izlenemiyor"). Okunamayan bir `last.json` ya da `last-full.json` kart 10'u ya da kart 3'ü **Sorun**'a çevirir. İlk geceden sonra Sistem sayfasında değerlerin göründüğünü ("bu platformda okunamıyor" yazmadığını) ve görevlerin kanıtının "yok" olmadığını denetleyin; haklar bozulduysa yönetici PowerShell'inde `& "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` çalıştırın. | Bölüm 4; [YEDEKLEME.md](YEDEKLEME.md) bölüm 7 |
| Hukuki değerlendirme | Kartların seçimi, eşikleri ve raporun metinleri teknik varsayımlardır. 5651 yükümlülüklerini nasıl karşıladığınızı ve raporu nasıl kullanacağınızı hukuk müşavirinizle teyit edin. | Bölüm 1 ve 3 |
