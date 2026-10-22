**Taslak — hukuk müşaviri onayı gerekir**

# Çalışan ve ağ kullanıcısı aydınlatma metni (taslak)

> Bu belge bir **taslaktır** ve hukuki görüş değildir. ARCILOGGER'ın bu sürümde **teknik olarak**
> hangi verileri işlediğini, nerede ve ne kadar süre tuttuğunu anlatır; metnin hukuki dili, hukuki
> sebepler ve haklar bölümü şirketin hukuk müşaviri tarafından yazılmalı ve onaylanmalıdır.
> ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır; hukuki uygunluk iddiasında
> bulunmaz. Bu bir **şablondur**: `[…]` ile işaretli yerler ARCILOGGER'ı kullanan şirkete özgüdür
> ve uydurulmamıştır; şirket ve hukuk müşaviri doldurur (ör. `[Şirket unvanı]`, `[adres]`,
> `[VERBİS kayıt no]`). Saklama süreleri:
> [saklama-imha-politikasi-taslak.md](saklama-imha-politikasi-taslak.md).
>
> English note: template of an employee information text (KVKK "aydınlatma metni") for legal
> review. It states only what ARCILOGGER technically processes; the `[…]` placeholders (company
> name, address, VERBİS number …), legal bases, rights and wording are for the company and its
> lawyer.

## 1. Veri sorumlusu

- Unvan: [Şirket unvanı]
- Adres: [adres]
- VERBİS kayıt numarası: [VERBİS kayıt no] (varsa)
- Başvuru ve iletişim kanalı: [e-posta / adres / KEP adresi]

## 2. Hangi kişisel veriler işlenir?

Veriler şirketin kendi ağ cihazlarının (FortiGate güvenlik duvarı, MikroTik yönlendirici, anahtar
ve erişim noktaları) ürettiği kayıtlardan gelir. **Çalışan bilgisayarlarına ve telefonlarına
yazılım ya da ajan kurulmaz**; ekran görüntüsü, tuş vuruşu, e-posta, mesaj ya da dosya içeriği
toplanmaz. Web sayfalarının içeriği kaydedilmez; ziyaret edilen **adres** kaydedilir.

| Kategori | Veriler | Kaynak |
|---|---|---|
| Ağ erişim kayıtları (5651 "erişim kayıtları") | Cihaza verilen iç IP adresi, cihazın MAC adresi, IP kullanımının başlangıç ve bitiş zamanı (DHCP kira kayıtları, saatlik ARP kayıtları), cihazın ağa bildirdiği ad (ör. `DESKTOP-…`), VLAN | MikroTik yönlendirici (DHCP sunucusu) |
| İnternet trafiği kayıtları | Oturum başına kaynak ve hedef IP/port, adres dönüşümü (NAT) adresi/portu, servis ve uygulama adı, süre, gönderilen/alınan bayt, hedef ülke | FortiGate |
| Web ve DNS kayıtları | Ziyaret edilen alan adı ve tam adres (URL, en fazla 2.000 karakter), kategori, izin/engel kararı; DNS sorgusunda sorulan ad ve dönen adresler | FortiGate web ve DNS filtresi |
| Güvenlik olayları | Saldırı tespit (IPS) ve virüs olayları, ağ cihazlarına giriş denemeleri (kullanıcı adı, IP), cihaz yapılandırma değişiklikleri | FortiGate, MikroTik |
| Cihaz ve kişi eşleştirmesi | Cihazın MAC adresi, gözlenen adları, türü; eşleştirildiği kişi (ad, departman, unvan, e-posta, sicil no) ve eşleştirmenin geçmişi (cihazın hangi tarihten hangi tarihe kime atandığı, atamayı yapan ARCILOGGER kullanıcısı) | Yönetici tarafından elle ya da CSV ile girilir; kişiye bağlanmamış cihazın departmanı VLAN'dan türetilir |
| ARCILOGGER kullanıcıları | Kullanıcı adı, görünen ad, e-posta, rol, giriş zamanları, oturumların IP adresi ve tarayıcı bilgisi, iki adımlı doğrulama kaydı, KVKK bilgilendirmesi onayı | ARCILOGGER |
| Denetim kaydı | Hangi ARCILOGGER kullanıcısının ne zaman hangi kişisel veriyi görüntülediği, sorguladığı, dışa aktardığı ya da hangi ayarı değiştirdiği | ARCILOGGER |
| 5651 talepleri | Talep eden makam, dosya/evrak no, talep tarihi, gerekçe, sorgulanan adres ve zaman, bulunan cihaz/kişi, resmi rapor (PDF) | ARCILOGGER kullanıcısının girdisi |
| İnternet kullanım özetleri (kişi, cihaz ve departman bazında, kişi adlarıyla) | Gün ve saat başına çevrimiçi süre ve **tahmini** aktif süre (mesai içi / dışı), ilk ve son etkinlik; ziyaret edilen siteler ve ana bilgisayar adları ile her birinde geçen tahmini süre, kategoriler, uygulamalar; indirilen/yüklenen veri, oturum sayısı; FortiGate'in kaydettiği URL'ler (kişi ya da cihaz ve gün başına en çok 1.000); engellenen denemeler (web, DNS, uygulama denetimi; kişi ya da cihaz ve gün başına en çok 5.000 satır); saatlik tablolar, günlük etkinlik zaman çizelgesi, 30 günlük ısı haritası, cihaz bazında döküm. Süreler bağlantı kayıtlarından hesaplanan tahminlerdir, kesin ekran süresi değildir | ARCILOGGER yukarıdaki trafik, web, DNS ve uygulama kayıtlarından türetir; kişi, cihazın o andaki atamasıyla bulunur (cihaz ve kişi eşleştirmesi) |
| Zamanlanmış kullanım raporları | Yukarıdaki özetlerden: şirket ve departman toplamları, etkinliği olan her kişi adıyla (siteler, kategoriler, engellenen denemeler, saatlik tablo), cihazlar, dikkat çeken değişiklikler; HTML e-posta, PDF ve Excel | ARCILOGGER; zamanlamaları ve alıcıları yönetici belirler |
| Sistem ve güvenlik uyarıları | Uyarı kodu, zaman, kaynak (cihaz) adı ya da gönderen IP adresi; IP çakışmasında IP ve MAC adresleri; uyarıya eklenen notlar | ARCILOGGER denetimleri (log kaynağı sessiz, mühürleme, saat, disk, yedek, IP çakışması …) |

Kural motoruyla tanımlanacak güvenlik kuralları sonraki bir sürümde planlanmaktadır; devreye
alınmadan önce metin güncellenmelidir.

## 3. İşleme amaçları

- 5651 sayılı Kanun ve İnternet Toplu Kullanım Sağlayıcıları Hakkında Yönetmelik kapsamındaki
  yükümlülükleri desteklemek: erişim kayıtlarını şirketin kendi sisteminde saklamak, bütünlüklerini
  günlük zaman damgasıyla göstermek ve yetkili makamların taleplerini yanıtlamak.
- Şirket ağının ve bilgi sistemlerinin güvenliği.
- ARCILOGGER'ın kendi güvenliği: kimin hangi veriye eriştiğinin denetlenmesi.
- İnternet kullanımının kişi, cihaz ve departman bazında raporlanması (tahmini kullanım
  özetleri ve zamanlanmış raporlar). [Şirket ve hukuk müşaviri: bu raporlamanın amacı.]

[Hukuk müşaviri: amaçların KVKK m. 4 ilkelerine göre ifadesi; başka amaçla kullanılmayacağı.]

## 4. Hukuki sebepler

[Hukuk müşaviri belirler (KVKK m. 5 / m. 6). ARCILOGGER ekibi hukuki sebep önermez.]

## 5. Kimler erişebilir?

- Erişim rol tabanlıdır ([../roles.md](../roles.md)): **Yönetici**, **Denetçi** (5651 sorguları ve
  mühür doğrulama), **İK Raporları** ve **Rapor İzleyici**. Kişi düzeyinde veri yalnızca bu
  yetkiye sahip rollere gösterilir.
- **İnternet kullanım özetleri:** ARCILOGGER'ın varsayılan rol tanımlarında kullanım görme yetkisi
  olan **dört rolün hepsi** (Yönetici, Denetçi, İK Raporları, Rapor İzleyici) Kullanım sayfasında
  kişi ve cihaz adlarını, MAC/IP adreslerini, ana bilgisayar adlarını, URL'leri, engellenen
  denemeleri ve kişi bazındaki zaman çizelgelerini görür ve CSV/Excel olarak dışa aktarabilir.
  İsteğe bağlı bir ayar (`usage.pseudonymize_anonymous_role`, varsayılan kapalı) Denetçi ve Rapor
  İzleyici'ye ad yerine takma ad ("Kullanıcı-7F3A") gösterir. Yönetici bir kişiyi kişi düzeyindeki
  görünümlerden ve raporlardan çıkarabilir (`persons.usage_excluded`; trafiği şirket ve departman
  toplamlarında sayılmaya devam eder).
- **Zamanlanmış kullanım raporlarını** yalnızca Yönetici oluşturur ve alıcılarını belirler
  (`reports.manage`); raporlar varsayılan olarak kişi düzeyinde ve adlarladır. Alıcılar
  ARCILOGGER kullanıcısı olmak zorunda değildir (e-posta adresleri, bölüm 6).
- **Kişi kayıtlarını** (kişi listesi; e-posta, sicil no, unvan ve notlarla), cihaz listesini ve
  **atama geçmişini** (cihazın hangi tarihten hangi tarihe kime atandığı, atamayı yapan) Cihazlar
  ve Kişiler sayfasında yalnızca Yönetici görür ve değiştirir. Hangi cihazın hangi kişiye
  bağlandığını başka roller de görür: denetim kaydını okuyan roller (`audit.read`: Yönetici,
  Denetçi) Denetim Kaydı sayfasında Cihazlar ve Kişiler sayfasından yapılan her atama
  değişikliğini görür (`device.assign`: cihazın MAC adresi, önceki ve yeni kişinin numarası —
  ARCILOGGER'daki kayıt numarası, sicil no değil —, atamanın başlangıcı, değişikliği yapan
  kullanıcı; CSV ile içe aktarılan atamalar yalnızca sayılarla yazılır) ve bu numaraları Kullanım
  sayfasındaki ve 5651 sorgu sonuçlarındaki kişilerle eşleştirebilir (Denetçi, takma ad ayarı
  açıkken yalnızca 5651 sorgu sonuçlarındakilerle); Kullanım sayfası kullanım görme yetkisi olan
  her role (dört rolün hepsi) dönemde kullanımı olan kişileri (ad, departman) ve **her kişinin
  dönemde kullandığı cihazları** (cihaz adı, MAC, IP) gösterir (takma ad ayarı açıkken Denetçi ve
  Rapor İzleyici'ye takma adla, MAC/IP olmadan); kişi düzeyindeki (varsayılan) zamanlanmış
  raporların Excel dosyası (Cihazlar sayfası) alıcılarına her cihazı dönemde bağlandığı kişilerle
  birlikte verir; 5651 sorgusu yapan roller (Yönetici, Denetçi) sorgu sonucunda o andaki atamayı
  (kişinin adı, departmanı ve unvanı, atamanın başlangıcı ve bitişi) görür.
- Kişisel veri gören her kullanıcı — bu sürümde dört rolün hepsi — ilk girişte ve metnin her yeni
  sürümünde bir KVKK bilgilendirmesini onaylar; her onay, her 5651 sorgusu, bir kişinin ya da
  cihazın kullanım ayrıntısının her görüntülenmesi, her dışa aktarma, rapor zamanlamalarındaki her
  değişiklik, her rapor ve kanıt paketi indirmesi **değiştirilemez (zincirli) denetim kaydına**
  yazılır.
- Yönetici olmayan kullanıcılar yalnızca kendi 5651 sorgularını görür.
- Şirket dışından erişim yoktur: sunucu yalnızca şirketin yönetici bilgisayarından tarayıcıyla
  kullanılır. Zamanlanmış kullanım raporları açılırsa kişi adlarıyla e-postayla sunucunun dışına
  çıkar (bölüm 6).

## 6. Aktarım

- Kayıtlar **şirketin kendi sunucusunda** tutulur; ARCILOGGER bulut hizmeti kullanmaz ve
  kayıtları kendisi **yurt dışına aktarmaz**. İstisna e-postadır (aşağıda).
- **Zamanlanmış kullanım raporları** (kişi adlarıyla; HTML e-posta, PDF ve Excel ekleri)
  yöneticinin ayarladığı SMTP sunucusu üzerinden, yöneticinin girdiği alıcılara e-postayla
  gönderilir (kurulumla gelen zamanlamalar alıcı girilip açılana kadar kapalıdır). E-postalar ve
  ekleri alıcıların posta kutularında ve e-posta sağlayıcısında kalır; **ARCILOGGER bu kopyaları
  silemez**. Sunucudaki rapor dosyalarının süresi: bölüm 7.
  [Hukuk müşaviri: alıcılar; e-posta sağlayıcısı şirket dışında ya da yurt dışındaysa aktarımın
  değerlendirilmesi; eklerin korunması.]
- **Uyarı e-postaları** kişi adı, IP–kişi eşleşmesi, MAC adresi ya da log satırı içermez; kaynak
  adları, günler, eşikler ve sayılar ile gerektiğinde her uyarı için bir IP adresi içerir (bir
  e-posta birden çok uyarıyı listeleyebilir): yeni bir log göndericisinin IP adresi, taşıma yolu
  izin verilmeyen bir mesajın gönderici adresi (yapılandırılmış bir log kaynağına ait bir adres;
  mesaj bu adresi taklit eden bir göndericiden de gelmiş olabilir) ya da IP çakışmasında çakışan
  iç IP adresi. Bir ağ tanımındaki ya da yapılandırılmamış bir göndericinin kaynak adı da IP
  adresini içerir (ör. `ap-<IP adresi>`, `unknown-<IP adresi>`) ([../UYARILAR.md](../UYARILAR.md)
  bölüm 5).
- Kullanım sayfasından dışa aktarılan CSV/Excel dosyaları ve indirilen raporlar, onları indiren
  ARCILOGGER kullanıcısının bilgisayarına iner; her dışa aktarma ve indirme denetim kaydına
  yazılır.
- Günlük mühürleme için TÜBİTAK BİLGEM Kamu SM zaman damgası sunucusuna **yalnızca SHA-256
  özetleri** gönderilir; kayıtların kendisi gönderilmez.
- İkinci kopyanın şirketin kendi depolama cihazında ([ikinci kopyanın yeri, ör. bir NAS])
  tutulması planlanmaktadır; bu sürümde varsayılan olarak kapalıdır.
- Yetkili makamların talebi üzerine 5651 raporu ve kanıt paketi teslim edilebilir. Kanıt paketi,
  bağımsız doğrulama için ilgili günlerin mühürlü dosyalarını **tamamen** içerir; bu nedenle
  talep konusu dışındaki kişilerin kayıtları da pakette bulunur. ARCILOGGER'ın kendi denetim
  kaydı varsayılan olarak pakete girmez.
  [Hukuk müşaviri: aktarımın hukuki sebebi, paketin teslim yöntemi (parolalı paket, teslim
  tutanağı).]

## 7. Saklama süresi

Ayrıntı ve bugünkü teknik durum: [saklama-imha-politikasi-taslak.md](saklama-imha-politikasi-taslak.md).
Özet: mühürlü ham kayıtlar en az 730 gün (varsayılan 760 gün) saklanır; süresi dolan günlerin
imhası zaman damgalı bir imha tutanağıyla yapılır ve bu imha işi **varsayılan olarak kapalıdır**.
Veritabanındaki **kullanım özetleri** 400 gün (`usage.retention_days`) sonra worker tarafından her
gece silinir (bu silme için imha tutanağı tutulmaz, yalnızca işin çalışma kaydı); sunucudaki
**kullanım raporu dosyaları** 400 gün (`reports.keep_days`), kanıt paketleri 30 gün sonra silinir;
e-postayla gönderilen kopyalar alıcılarda kalır. Veritabanındaki ayrıştırılmış kayıtlar, 5651
sorguları, resmi raporlar ve denetim kaydı için bu sürümde otomatik imha yoktur (politika
taslağındaki açık kararlar).

## 8. İlgili kişinin hakları

[Hukuk müşaviri: KVKK m. 11 hakları ve başvuru usulü; başvuru kanalı bölüm 1'de.]

Teknik not: bir kişiye ait kayıtlar mühürlü günlük dosyaların içindedir. Mühürlü bir dosyadan tek
bir kişinin satırlarını silmek, o günün bütünlük kanıtını (özet ve zaman damgası) bozar; mühürlü
kayıtlar yalnızca saklama süresi dolunca, günün tamamı olarak imha edilir. Bu durumun haklarla
ilişkisi hukuk müşavirince değerlendirilmelidir.

## 9. Hukuk müşavirine açık sorular

1. Hukuki sebepler ve aydınlatmanın çalışanlara nasıl yapılacağı (imza, intranet, işe giriş).
2. Misafir Wi-Fi kullanıcıları için ayrı bir metin gerekip gerekmediği (misafirler bu sürümde
   anonimdir; kimlik doğrulama modülü planlanmaktadır).
3. Tam URL ve DNS sorgularının saklanmasının ölçülülüğü.
4. Kanıt paketlerinin içeriği (tam günlük dosyalar) ve teslim yöntemi.
5. Web arayüzündeki KVKK bilgilendirmesinin metni (şu an "yurt dışına aktarılmaz ve saklama
   süresi sonunda imha edilir" der; e-postayla giden raporlar ve imha işleri bu politikaya göre
   netleşince metin ve sürümü güncellenmelidir).
6. Kişi düzeyindeki kullanım verisinin (adlar, URL'ler, engellenen denemeler, saatlik zaman
   çizelgeleri) ölçülülüğü, hangi rollerin göreceği ve şirketin çalışanlardan aldığı onayların
   (varsa) kapsamı. ARCILOGGER bu verileri varsayılan olarak adlarla gösterir; takma ad ayarı
   ve kişi düzeyinin kapatılması bölüm 5'tedir.
7. Zamanlanmış raporların alıcıları ve e-posta sağlayıcısı (şirket dışında ya da yurt dışında bir
   hizmet olup olmadığı).
