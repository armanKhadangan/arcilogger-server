# Kullanım raporları (zamanlanmış e-posta, PDF, Excel)

> English: [USAGE-REPORTS.md](USAGE-REPORTS.md). Karar:
> [adr/0012-scheduled-usage-reports.md](adr/0012-scheduled-usage-reports.md).
>
> **Lisans:** zamanlanmış raporlar **Zamanlanmış kullanım raporları** modülünü (o da Kullanım analizi modülünü) gerektirir ([LISANS.md](LISANS.md)); modül kapalıyken Raporlar sayfası kilitlidir, raporlar üretilmez ve gönderilmez, `R20-report` açılmaz; modül kapanmadan önce ve kapandığında lisans uyarısı `R20-license` yöneticilere haber verir ([UYARILAR.md](UYARILAR.md)).
>
> ARCILOGGER, **5651 yükümlülüklerini desteklemek için tasarlanmıştır**; hukuki uygunluk iddiasında
> bulunmaz. Kişi düzeyindeki raporların kullanımı için hukuk müşavirinin görüşünü alın.

Bu belge zamanlanmış kullanım raporlarını anlatır: ne zaman çalışırlar, neyi kapsarlar, içlerinde
ne vardır, nasıl gönderilirler, dosyalar nerede saklanır ve kurulum nasıl denenir.

**Kullanım sayıları tahminidir:** bağlantı kayıtlarından hesaplanır; arka planda açık sekmeler de
sayılabilir. Her rapor (e-posta, PDF, Excel) bu notu yazar; süreler hiçbir zaman kesin değer
olarak sunulmaz.

## 1. Zamanlamalar

Kurulumla dört zamanlama gelir. Hepsi **kapalıdır** (alıcı yok); alıcıları girip açın:

| Zamanlama | Tür | Ne zaman | Kapsadığı dönem |
|---|---|---|---|
| Gün sonu raporu | `end_of_day` | her gün 18:30 | o gün 00:00 – çalışma anı |
| Tam gün raporu | `full_day` | her gün 07:00 | önceki gün (00:00 – 24:00) |
| Haftalık rapor | `weekly` | pazartesi 07:15 | önceki ISO haftası (pazartesi – pazar) |
| Aylık rapor | `monthly` | ayın 1'i 07:30 | önceki takvim ayı |

- Saatler **Türkiye saatidir** (Europe/Istanbul). Türkiye 2016'dan beri yaz saati
  uygulamadığından 07:00 her gün 04:00 UTC'dir.
- Haftalık raporun günü (`weekday`, 1 = pazartesi … 7 = pazar) ve aylık raporun günü
  (`monthday`, 1–28: her ayda vardır) değiştirilebilir; dönem değişmez (önceki hafta / ay).
- Gün sonu raporu **00:00'a kurulamaz**: kapsadığı dönem (o gün 00:00 – çalışma anı) boş olurdu.
  00:01–23:59 arası bir saat seçin; önceki günün tamamı için Tam gün raporunu kullanın.
- Her zamanlamanın kendi alıcıları, biçimleri (HTML e-posta, PDF, Excel), **kişi düzeyi**
  (`person_level`, varsayılan açık: adlarla) ve **takma ad** (`pseudonymize`, varsayılan kapalı)
  seçeneği vardır. Varsayılan olarak (sahibin 2026-10-20 kararı) raporlar adlarla ve ayrıntılıdır;
  bunun çalışanların onayına uyup uymadığına kuruluş, hukuk müşaviriyle karar verir. Kişi
  düzeyindeki raporlardan çıkarılması istenen kişi, Cihazlar ve Kişiler'de kişinin
  **Kullanım raporlarından hariç tut** kutusuyla çıkarılır (`persons.usage_excluded`,
  [KULLANIM.md](KULLANIM.md) bölüm 6).
- **Sunucu kapalıyken kaçan raporlar:** worker yeniden başlayınca her zamanlama **bir kez**
  çalışır, kaçan en son zaman için (örneğin beş gün kapalı kalınca yalnızca dünün tam gün raporu
  gider, beş rapor gitmez). `reports.catch_up` `false` yapılırsa 30 dakikadan fazla geciken
  rapor hiç gönderilmez; sonraki zamanı beklenir. Worker kapalıyken zamanlamanın adını,
  alıcılarını ya da biçimlerini değiştirmek kaçan raporu düşürmez; türü, saati, günü ya da
  açık/kapalı durumunu değiştirmek bir sonraki zamanı yeniden hesaplar.
- Zamanlamalar web arayüzünün **Raporlar** sayfasından (ya da `/api/v1/reports`, yetki
  `reports.manage`: yönetici) düzenlenir. Her değişiklik denetim kaydına yazılır
  (`report.schedule.create/update/delete`; alıcı adresleri değil, yalnızca sayıları).

## 2. Raporda ne var

Bütün biçimler aynı içeriği gösterir (aynı belgeden üretilir). Kuruluş adı (`site_name`)
e-postanın konusunda, özet cümlelerinde, HTML başlığında, PDF kapağında ve altbilgisinde ve
Excel'in Özet sayfasında ("Site" satırı) yer alır; ayarlanmamışsa bu yerlerden çıkarılır (boş
parantez ya da yarım cümle kalmaz; [KURULUM.md](KURULUM.md) bölüm 7.1).

- **Şirket geneli:** aktif kullanıcı, toplam çevrimiçi ve aktif süre, mesai dışı aktif süre,
  indirilen / yüklenen veri, oturum, engellenen deneme; en çok kullanılan siteler ve kategoriler
  (e-postada 10, PDF ve Excel'de 20).
- **Departmanlar:** aktif kullanıcı, aktif süre, indirme, yükleme, engellenen deneme.
  Departmanı olmayan kişiler, cihazlar ve IP adresleri her biçimde aynı adla, **Bilinmiyor**
  satırında toplanır.
- **Kişiler — hepsi** (etkinliği olan her kişi, adlarıyla): departman, ilk / son etkinlik
  (birden çok günü kapsayan raporda tarihleriyle), çevrimiçi ve aktif süre, indirme / yükleme,
  oturum, en çok kullandığı siteler (süreleriyle) ve kategoriler, engellenen deneme, mesai dışı
  süre. Aynı adı taşıyan iki kişinin (ya da bir kişiyle aynı adı taşıyan bir cihazın) ayrıntıları
  birbirine karışmaz.
- **Dikkat çeken değişiklikler:** 7 günlük ortalamaya göre büyük farklar (ör. "Video ve müzik
  akışı %180 üzerinde"; eşikler kullanım motorunun ayarlarıdır).
- **Dönemin güvenlik uyarıları:** zaman, önem, kural, durum ve uyarı kataloğundaki ad ve tek
  cümle ([UYARILAR.md](UYARILAR.md)). Bir uyarının kendi başlığı ve ayrıntıları (hata metinleri,
  log alanları) rapora **girmez**; hiçbir ham log satırı rapora girmez.
- **Önceki günün mühürü:** mühürlendi (zaman, seri no) / henüz mühürlenmedi / başarısız / boş gün;
  test damgası (mock) ise "yasal geçerli değil" uyarısı.
- **Veri kalitesi:** verilerin hesaplandığı son an, kişiye bağlanamayan süre, notlar.

Biçimler:

- **HTML e-posta:** Outlook ve Gmail'de doğru görünmesi için yalnızca tablolar ve satır içi
  stiller; resim, yazı tipi, betik ya da dışarıdan yüklenen hiçbir şey yoktur. Tek bağlantı
  ARCILOGGER web adresidir (`alerts.web_base_url`; boşsa `https://<[collector] bind>`). Önce
  Türkçe, başlığın altında bir satırlık İngilizce özet. Çok kişili bir raporun e-postası uzundur
  (200 kişi yaklaşık 500 KB); Gmail ~100 KB'tan uzun iletiyi kırpar ("İletinin tamamını
  görüntüle"). Tüm ayrıntı PDF ve Excel'dedir.
- **PDF:** kapak (özet, yöntem notu, şirket, departmanlar, değişiklikler, uyarılar, mühür),
  ardından her departman **yeni bir sayfada** kişileriyle, her kişinin ayrıntısı (en çok 20 site
  süreleri ve kategorileriyle, kategoriler, engellenen denemeler — PDF kişi başına en çok 100
  deneme listeler, raporun listelediklerinin hepsi Excel'dedir; sınırlar aşağıda —, ilk / son
  etkinlik, mesai dışı süre). Her sayfada "Sayfa n / N", oluşturma zamanı ve yöntem notu. Türkçe
  harfler gömülü DejaVu yazı tipiyle aranabilir ve kopyalanabilir.
- **Excel:** sayfalar **Özet, Departmanlar, Kişiler, Kişi-Site** (her kişi × site), **Kişi-Saat**
  (her kişi × saat), **Engellenenler** (dönemin engellenen denemeleri, en eskiden başlayarak),
  **Cihazlar** (trafiği ya da engellenen denemesi olan her cihaz, kişisi olmayanlar da). Süreler Excel **zaman değeridir**
  (`[h]:mm:ss`, toplanır ve sıralanır), veri miktarları **bayt sayısıdır** (`#,##0`); hiçbir
  sayı metin değildir. Başlık satırı sabit ve filtrelidir. `=`, `+`, `-`, `@` ile başlayan
  metinlerin başına `'` eklenir: hiçbir hücre formül olarak açılmaz. Excel dosyasının tutamadığı
  denetim karakterleri (ör. bir cihazın DHCP ana bilgisayar adında) görünür biçimde `\xNN`
  olarak yazılır. Zamanlar Türkiye saatiyle yazılır.

**Engellenen denemelerin sınırları:** kullanım motoru her kişi, cihaz ya da IP adresi için günde
en çok `usage.blocked_per_day` (5000) deneme kaydı saklar, en yenilerini; günlük sayacı ise
hepsini sayar. Tam günleri kapsayan raporlar (tam gün, haftalık, aylık) bu yüzden her denemeyi
sayar, sınırı aşan bir günün ise yalnızca saklanan kayıtlarını listeler. Günün bir bölümünü
kapsayan raporlar (gün sonu raporu; önizlemede ya da elle çalıştırmada saatle verilen bir dönem)
yalnızca saklanan kayıtları sayabilir: sınırı aşan bir günde gerçek sayı daha yüksek olabilir.
Böyle bir gün bir sayıyı ya da listeyi eksik bıraktığında veri kalitesi notları bunu yazar.
Ayrıca bir rapor en çok 50 000 deneme listeler (en eskiden başlayarak); bu sınırla kesilen liste
bunu Engellenenler sayfasının son satırında ve veri kalitesi notlarında yazar, kişi ve departman
sayıları listelenmeyenleri de sayar.

Bir zamanlama `person_level` kapalıysa rapor yalnızca şirket ve departman toplamlarını içerir;
`pseudonymize` açıksa kişiler takma adlarla (`Kullanıcı-7F3A`) gösterilir. Takma adlar gizli
depodaki `usage.pseudonym_key` anahtarını ister ([KULLANIM.md](KULLANIM.md) bölüm 6); anahtar
yokken takma adlı bir zamanlama kaydedilemez, önizlenemez ve elle çalıştırılamaz: API
`409 pseudonym_key_missing` ile anahtarın nasıl kaydedileceğini söyler.

## 3. Gönderim

- Gönderen worker'dır (`reports.run_due`, dakikada bir): zamanı gelen raporu üretir, dosyaları
  saklar ve **her alıcıya ayrı bir e-posta** gönderir (`To` yalnızca o alıcı; kimse diğer
  alıcıları görmez, reddedilen bir adres yalnızca kendisini etkiler).
- SMTP bağlantısı uyarı e-postasıyla aynıdır: `[smtp]` ve gizli depodaki `smtp.password`
  ([UYARILAR.md](UYARILAR.md) bölüm 4). `[smtp] enabled = false` iken rapor yine üretilir ve
  saklanır, ama teslimler başarısız sayılır (`R20-report`).
- **Ekler:** PDF ve Excel e-postaya eklenir. İkisi birlikte `reports.max_attachment_mb`
  (varsayılan 15 MB; 1 MB = 1 048 576 bayt) sınırını aşarsa e-posta eksiz gider ve Raporlar
  sayfasındaki indirmeye bağlantı verir (`https://<sunucu>/reports?run=<no>`).
- **Sınır eklerin ham boyutudur, e-postanınki değil:** ekler e-postada base64 ile kodlanır ve
  yaklaşık **1,37 kat** büyür; HTML gövdesi de eklenir (200 kişi yaklaşık 500 KB). 15 MB ekli bir
  e-posta bu yüzden yaklaşık 22 MB olur. Posta sunucusu iletiyi boyutu yüzünden reddederse (kalıcı
  5xx) o teslim hemen başarısız olur ve `R20-report` açılır; indirme bağlantısına geçilmez. Ayarı
  sunucunun ileti boyutu sınırının **en çok üçte ikisine** getirin (ör. 20 MB sınır için 13,
  25 MB için 16).
- **Yeniden deneme:** geçici hatalar (bağlantı, zaman aşımı, TLS, kimlik doğrulama, 4xx) 1, 5,
  15, 60 ve 180 dakika sonra yeniden denenir; 6 denemeden sonra ya da kalıcı bir 5xx retinde o
  alıcının teslimi **başarısız** olur ve yerel **`R20-report`** uyarısı (orta) açılır. Aynı
  alıcıya sonraki rapor gidince (ya da adres bütün zamanlamalardan çıkarılınca) uyarı kendiliğinden
  çözülür. Sunucu ile gönderim arasında bir çökme olursa o e-posta en çok bir kez daha gider.
- **Çalıştırma durumu:** `queued` (sırada) → `running` (üretiliyor / gönderiliyor, yeniden deneme
  bekleniyor) → `sent` (bitti: her e-posta gitti ya da gönderim istenmedi), `partial` (bazı
  alıcılara gitmedi) ya da `failed` (rapor üretilemedi ya da hiçbir e-posta gitmedi). Rapor
  üretilemezse (ör. kullanım motoru hata verdi) `R20-report` açılır; sonraki başarılı rapor
  çözer.

## 4. Dosyalar nerede, ne kadar kalır

- `D:\ARCILOGGER\exports\usage-reports\<çalıştırma no>\` — PDF, Excel ve HTML dosyası; her
  dosyanın boyutu ve SHA-256 özeti veritabanında (`report_runs.files`) kayıtlıdır. Raporlar
  sayfasından indirme bu özetle denetlenir (değişmiş dosya verilmez) ve denetim kaydına yazılır
  (`report.download`). Klasörde ayrıca gönderilen e-postanın kendisi (`mail.html`, `mail.txt`)
  durur, böylece yeniden deneme aynı e-postayı gönderir.
- Kullanım raporları **kanıt değildir** ve kasaya (`vault`) asla girmez.
- `reports.keep_days` (varsayılan **400** gün) sonra dosyalar `reports.purge` işiyle (her gece
  03:20'den sonra) silinir; çalıştırma kaydı ve dosyaların özetleri kalır, indirme "süresi doldu"
  (410) yanıtı verir.
- Veritabanı yedekleri zamanlamaları ve çalıştırma kayıtlarını içerir; dosyaların kendisi
  `exports` klasöründedir ([YEDEKLEME.md](YEDEKLEME.md)).

## 5. Ayarlar

Değiştirme: [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 6 (`& $db settings set …`).

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `reports.keep_days` | 400 | Rapor dosyalarının saklandığı gün sayısı (en az 1) |
| `reports.max_attachment_mb` | 15 | Eklerin kodlanmamış toplam boyutunun sınırı (MB = 1 048 576 bayt, 1–100); üstünde e-posta indirme bağlantısıyla gider. E-postanın kendisi yaklaşık 1,37 kat büyüktür: posta sunucusu sınırının en çok üçte ikisi (bölüm 3) |
| `reports.catch_up` | `true` | Sunucu kapalıyken kaçan raporu bir kez, son zaman için gönder |
| `alerts.web_base_url` | boş | E-postadaki bağlantının adresi (uyarı e-postasıyla ortak) |

## 6. Kurulum ve deneme

1. Uyarı e-postasını kurun ve deneyin ([UYARILAR.md](UYARILAR.md) bölüm 4,
   `arcilogger-worker alerts test-email --to <adres>`). Rapor e-postası aynı bağlantıyı kullanır.
2. **Raporlar** sayfasında "Tam gün raporu"nu açın: alıcıları girin, biçimleri seçin,
   **Etkin** yapın. Alıcı olmadan bir zamanlama etkinleştirilemez.
3. **Önizleme:** sayfadaki önizleme (ya da
   `GET /api/v1/reports/preview?kind=full_day&format=pdf`) raporu şimdi üretir, saklamaz ve
   göndermez; içeriği gözden geçirin.
4. **Şimdi çalıştır:** bir zamanlamanın "Şimdi çalıştır" düğmesi (`POST
   /api/v1/reports/schedules/<no>/run`) bir çalıştırmayı sıraya koyar; worker en geç bir dakika
   içinde üretir ve gönderir. Yalnızca dosya için `send: false` kullanın; belirli bir dönem için
   `from` / `to` verin (en çok 92 gün; saat dilimi yoksa Türkiye saati).
5. Çalıştırma geçmişinde durumu ve her alıcının teslimini izleyin; e-postayı ve ekleri bir alıcı
   olarak açıp denetleyin. Sorun varsa uyarılarda `R20-report`e ve
   `D:\ARCILOGGER\logs\worker.log` dosyasına bakın.

## 7. Sahada doğrulanacaklar

- Şirketin posta sunucusunun ileti boyutu sınırı (`reports.max_attachment_mb` bu sınırın en çok
  üçte ikisi olmalı, bölüm 3).
- Raporların gönderileceği adresler ve kişi düzeyindeki raporu kimlerin alacağı (KVKK, hukuk
  müşaviri görüşü).
- Büyük bir aylık raporun (çok kişi) üretim süresi ve boyutu; worker diğer işlerini bu sürece
  bekletir (geliştirme makinesinde 200 kişilik bir ay: PDF ve Excel birlikte yaklaşık 30 saniye,
  PDF ~0,8 MB, Excel ~1,6 MB).
