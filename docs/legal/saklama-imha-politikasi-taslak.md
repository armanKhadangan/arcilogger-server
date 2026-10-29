**Taslak — hukuk müşaviri onayı gerekir**

# Log saklama ve imha politikası (taslak)

> Bu belge bir **taslaktır** ve hukuki görüş değildir. ARCILOGGER'ın bu sürümde (MVP) kayıtları
> **gerçekte** nerede ve ne kadar süre tuttuğunu, neyi nasıl imha ettiğini ve neyin henüz imha
> edilmediğini anlatır; süreler ve imha aralıkları şirket ve hukuk müşaviri tarafından
> belirlenmelidir. ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır; hukuki
> uygunluk iddiasında bulunmaz. Bu bir **şablondur**: `[…]` ile işaretli yerler ARCILOGGER'ı
> kullanan şirkete özgüdür ya da karar bekler ve uydurulmamıştır (ör. `[Şirket unvanı]`,
> `[adres]`, `[VERBİS kayıt no]`). Yollar kurulumun varsayılan veri klasörüyle (`D:\ARCILOGGER`)
> yazılmıştır.
> İlgili: [aydinlatma-metni-taslak.md](aydinlatma-metni-taslak.md),
> [../DENETCI-KILAVUZU.md](../DENETCI-KILAVUZU.md) bölüm 2.
>
> English note: template of a log retention and deletion policy for legal review; it describes
> the retention ARCILOGGER implements today and lists the open decisions; the `[…]`
> placeholders are filled in by the company.

## 1. Amaç ve kapsam

[Şirket unvanı] ("şirket") tarafından ARCILOGGER sunucusunda ([sunucunun adı ve adresi])
tutulan ağ kayıtlarının, bunlardan türetilen verilerin ve ARCILOGGER'ın kendi kayıtlarının
saklanması ve imhası. Kişisel veri kategorileri: aydınlatma metni taslağı bölüm 2.

- Veri sorumlusu: [Şirket unvanı], [adres]
- VERBİS kayıt numarası: [VERBİS kayıt no] (varsa)
- Politikanın sorumlusu ve onay tarihi: [ad, unvan, tarih]

## 2. Tanımlar

| Terim | Anlamı |
|---|---|
| Ham kayıt (spool) | Ağ cihazından gelen her mesajın değiştirilmeden yazıldığı günlük dosya (`D:\ARCILOGGER\spool\<kaynak>\<gün>.log`) |
| Kasa (vault) | Mühürlenmiş günler: sıkıştırılmış ham kayıtlar, ARCILOGGER denetim kaydının o günkü dosyası, manifest ve zaman damgası (`D:\ARCILOGGER\vault\YYYY\MM\DD\`) |
| Mühür | Günün dosyalarının SHA-256 özetleri, önceki güne zincir ve Kamu SM zaman damgası |
| İmha tutanağı | Silinen günlerin listesi ve özetleri; zaman damgasıyla mühürlenir (`vault\deletions\`) |
| Zincir çapası | Son silinen günün özetleri; kalan zincir bununla doğrulanabilir kalır |

## 3. Saklama yerleri ve süreler (bugünkü davranış)

| Veri | Yer | Bugün ne olur? | Karar bekleyen |
|---|---|---|---|
| Ham kayıt (bugünkü ve mühürlenmemiş günler) | `D:\ARCILOGGER\spool\` | Gün mühürlenip kasadaki kopya doğrulanınca spool'dan silinir. `[seal] first_day` öncesindeki günler mühürlenmez ve silinmez | `first_day` öncesindeki günlerin akıbeti |
| Mühürlü günler (ham kayıt + günün denetim kaydı dosyası + manifest + damga) | `D:\ARCILOGGER\vault\` | Varsayılan **760 gün**; ayar **730 günün altına indirilemez** (`[retention] days`). İmha işi **varsayılan olarak kapalıdır** (`[retention] enabled = false`): kapalıyken hiçbir gün silinmez | İmhanın ne zaman açılacağı; 730 mı 760 mı |
| İmha tutanakları ve zincir çapası | `vault\deletions\` | Süresiz (silinen günlerin özetlerini ve zinciri korur; kişisel veri değil, özet içerir) | — |
| İkinci kopya | [İkinci kopyanın yeri, ör. bir NAS'ta WORM klasörü] (`[replica] target`; planlanan) | Bu sürümde **kapalı** (`[replica] enabled = false`); açıldığında kasa ile aynı süre | WORM saklama süresinin kasayla uyumu |
| Kanıt paketleri | `D:\ARCILOGGER\exports\` | **30 gün** (`evidence.keep_days`), worker her gece 03:00'ten sonra siler; denetim kaydına yazılır | Süre uygun mu |
| Resmi 5651 raporları (PDF ve damgası) | `D:\ARCILOGGER\exports\reports\` | **Süre sınırı yok**: sorguyla birlikte kalır, otomatik silinmez | Saklama süresi |
| Kullanım raporları (HTML, PDF, Excel; kişi adlarıyla) | `D:\ARCILOGGER\exports\usage-reports\` | **400 gün** (`reports.keep_days`), worker her gece 03:20'den sonra siler; çalıştırma kaydı (alıcılar, dosya özetleri) veritabanında kalır. E-postayla gönderilen kopyalar alıcıların posta kutularındadır, ARCILOGGER onları silemez | Süre ve alıcılar |
| 5651 sorguları (talep bilgileri, sorgu, sonuç) | Veritabanı, `lookups` | **Süre sınırı yok** | Saklama süresi |
| Ayrıştırılmış kayıtlar (trafik, web, DNS, MikroTik ve FortiGate DHCP kayıtları, ARP …; aygıt tanıma açık FortiGate arayüzlerinde oturum kayıtlarının aygıt tanıma alanları: kaynak MAC adresi, cihaz adı, işletim sistemi, donanım üreticisi ve sürümü, cihaz türü, FortiGate'in pasif olarak öğrendiği kullanıcı adı) | Veritabanı (PostgreSQL) | Hedef **400 gün** (`db.retention_days`), ancak bu sürümde bu tablolar için **veritabanı imha işi yoktur**: ayrıştırılmış kayıtlardan hiçbir şey silinmez. Veriler kasadaki ham kayıtlardan yeniden üretilebilir. FortiGate DHCP kayıtları ve aygıt tanıma alanları aynı süreye tabidir; ham satırlar değiştirilmez (alanlar ham satırın parçasıdır ve kasada onunla birlikte saklanır) ve bu alanlar ham satırlarla birlikte 5651 raporlarına ve kanıt paketlerine girer (başka kişilerin aday oturumları dahil) | İmha işinin devreye alınması (sonraki sürüm) |
| Kullanım özetleri (kişi düzeyinde, adlarla: siteler, ana bilgisayar adları, URL'ler, engellenen denemeler, saatlik satırlar, cihaz dökümü) | Veritabanı: `usage_days`, `usage_daily`, `usage_device_daily`, `usage_domain_daily`, `usage_app_daily`, `usage_hourly`, `usage_blocked`, `usage_urls` | **400 gün** (`usage.retention_days`); worker her gece 03:30'dan sonra daha eski günleri siler (`usage.retention`). Silme yalnızca işin çalışma kaydına (`job_runs`) yazılır, **imha tutanağı tutulmaz**. Ayrıştırılmış kayıtlar veritabanında durdukça yeniden hesaplanabilir | Süre; imha tutanağı gerekip gerekmediği |
| ARCILOGGER denetim kaydı | Veritabanı `audit_log` + günlük dosyası kasada | Veritabanındaki kayıt **süresiz** (silinmesi teknik olarak engellenir); günlük dosyası mühürlenir ve kasadaki günle birlikte imha edilir | Veritabanındaki kaydın süresi |
| Oturum kayıtları (IP, tarayıcı) | Veritabanı `user_sessions` | Süresi biten oturumlar silinmez (temizleme işi yok) | Süre |
| Kurulum yedekleri | `D:\ARCILOGGER\backups\db\` | Her yükseltmede veritabanı yedeği alınır; otomatik silinmez | Süre ve şifreleme |
| Hizmet günlükleri | `D:\ARCILOGGER\logs\` | Hizmet başına 10 MB'lık 8 dosya; eskisi üzerine yazılır | — |

## 4. İmha yöntemi (kasa)

İmha yalnızca saklama işiyle yapılır (`\ARCILOGGER\Retention` görevi, `arcilogger-core retention`)
ve `[retention] enabled = true` gerektirir:

1. Süresi dolan her gün silinmeden önce yeniden doğrulanır; doğrulanamayan bir gün hiçbir zaman
   silinmez (kritik uyarı üretilir).
2. Silinecek günlerin listesi, manifest ve damga özetleri bir **imha tutanağına** yazılır ve tutanak
   Kamu SM zaman damgasıyla mühürlenir; damga doğrulanmadan hiçbir gün silinmez.
3. Günlerin dosyaları silinir, **zincir çapası** güncellenir; tutanak ertesi günün manifestine
   eklenir.
4. Çalışma ARCILOGGER denetim kaydına `retention.run` olarak aktarılır; reddedilen bir çalışma
   uyarı (`R20-retention`) üretir.

Mühürlü bir günden tek bir kişinin satırları silinemez (bütünlük kanıtı bozulur); imha günün
tamamı için yapılır.

## 5. Teknik ve idari tedbirler (bugün)

- Kayıtlar şirketin sunucusunda tutulur; ARCILOGGER bulut hizmeti kullanmaz ve kayıtları kendisi
  yurt dışına aktarmaz. **İstisna e-postadır:** zamanlanmış kullanım raporları (kişi adlarıyla;
  HTML e-posta, PDF ve Excel ekleri) ve uyarı e-postaları yöneticinin ayarladığı SMTP sunucusu
  üzerinden yöneticinin girdiği alıcılara gider; e-postayla giden kopyaları ARCILOGGER silemez
  (bölüm 3; aydınlatma metni bölüm 6). Uyarı e-postaları kişi adı ve log satırı içermez,
  gerektiğinde her uyarı için bir IP adresi içerir (bir e-posta birden çok uyarıyı
  listeleyebilir). Kamu SM'ye yalnızca SHA-256 özetleri gider. Sunucudan çıkan
  diğer kopyalar (dışa aktarılan ve indirilen dosyalar, yetkili makama teslim edilebilen 5651
  raporu ve kanıt paketi): aydınlatma metni bölüm 6.
- Rol tabanlı erişim, iki adımlı doğrulama (yönetici ve denetçi), KVKK bilgilendirmesi onayı,
  kişisel veri görüntüleme ve dışa aktarmalarının zincirli denetim kaydı.
- Kasa: salt okunur dosyalar, NTFS erişim kısıtları, Windows nesne erişim denetimi (SACL), günlük
  özet zinciri ve zaman damgası; değişiklik **tespit edilir** (bir yönetici dosya silebilir, ama
  fark edilmeden değiştiremez).
- Gizli bilgiler (veritabanı parolası, Kamu SM bilgileri, TOTP anahtarları) şifreli saklanır.
- Kanıt paketleri isteğe bağlı olarak AES-256 ile şifrelenir; parola saklanmaz.

## 6. Şirket ve hukuk müşaviri için açık kararlar

1. Kasa imhasının ne zaman açılacağı ve süre (730 / 760 gün).
2. Veritabanındaki ayrıştırılmış kayıtların süresi (hedef 400 gün) — imha işi sonraki sürümde.
   Kullanım özetleri ise bugün 400 gün sonra kendiliğinden silinir (`usage.retention_days`):
   bu süre ve silmenin bir imha tutanağıyla kaydedilip kaydedilmeyeceği.
3. 5651 sorguları ve resmi raporların saklama süresi (`exports\reports`).
4. Veritabanındaki denetim kaydının saklama süresi.
5. Oturum kayıtları, kurulum yedekleri, kanıt paketleri (30 gün) ve kullanım raporlarının (400 gün)
   süreleri.
6. Periyodik imha aralığı ve imha kayıtlarının tutulma şekli: [hukuk müşaviri].
7. Yedeklerin (veritabanı, `secret.key`) şifrelenmesi ve şirket dışında saklanması
   ([../ASSUMPTIONS.md](../ASSUMPTIONS.md) A6).
8. Web arayüzündeki KVKK bilgilendirmesinin bu politikaya uygun hale getirilmesi.
9. E-posta sağlayıcısının şirket dışında ya da yurt dışında bir hizmet olup olmadığı; e-postayla
   giden kullanım raporlarının (kişi adlarıyla) ve uyarı e-postalarının buna göre
   değerlendirilmesi ve alıcıların posta kutularındaki kopyaların süresi (bölüm 5; aydınlatma
   metni bölüm 9, soru 7).

## 7. Sürüm

| Sürüm | Tarih | Değişiklik |
|---|---|---|
| taslak 1 | 2026-10-13 | ARCILOGGER MVP'nin teknik davranışından hazırlandı; hukuk müşaviri onayı bekliyor |
| taslak 2 | 2026-10-20 | Kullanım özetleri (veritabanı) ve kullanım raporu dosyaları eklendi; bölüm 5 e-posta istisnasını aydınlatma metniyle aynı biçimde anlatır, bölüm 6'ya e-posta sağlayıcısı sorusu eklendi; hukuk müşaviri onayı bekliyor |
| taslak 3 | 2026-10-20 | Şablon: şirkete özgü bilgiler (unvan, adres, VERBİS kayıt no, sunucu, ikinci kopyanın yeri) `[…]` yer tutucularıyla; hukuk müşaviri onayı bekliyor |
| taslak 4 | 2026-10-29 | Ayrıştırılmış kayıtlar satırına FortiGate DHCP kayıtları ve oturum kayıtlarının aygıt tanıma alanları eklendi (aynı süre; ham satırlarla birlikte 5651 raporlarına ve kanıt paketlerine girer); hukuk müşaviri onayı bekliyor |
