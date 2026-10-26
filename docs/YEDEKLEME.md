# Veritabanı yedeği ve geri yükleme

> English: [BACKUP.md](BACKUP.md). Karar ve gerekçeler: [ADR-0009](adr/0009-database-backup.md).
> İlk adımlar: [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 10.

ARCILOGGER veritabanını her gece kendisi yedekler. Bu belge neyin yedeklendiğini ve neyin
yedeklenmediğini, yedeğin nasıl denetlendiğini, sunucu dışına nasıl kopyalandığını ve bir yedeğin
nasıl geri yüklendiğini anlatır. Komutlar sunucuda, **yönetici olarak açılmış** Windows
PowerShell'de çalışır; programlar `PATH` üzerinde değildir ([ILK-ADIMLAR.md](ILK-ADIMLAR.md)
bölüm 0):

```powershell
$arci   = "C:\Program Files\ARCILOGGER\venv\Scripts"
$core   = "$arci\arcilogger-core.exe"
$ingest = "$arci\arcilogger-ingest.exe"
$db     = "$arci\arcilogger-db.exe"
```

## 1. Ne yedekleniyor, ne yedeklenmiyor?

| Ne | Nerede | Yedeği |
|---|---|---|
| Veritabanı: kullanıcılar ve roller, 5651 sorguları ve talep bilgileri, saklanan rapor ve kanıt paketi kayıtları, değiştirilen ayarlar, kişiler, cihaz adları ve notları, uyarı durumları, aydınlatma metni onayları, denetim kaydı, ayrıştırılmış log satırları | PostgreSQL (`D:\ARCILOGGER\db`) | **Her gece 01:40**, `D:\ARCILOGGER\backups\scheduled\` (bu belge) |
| Yükseltme öncesi veritabanı yedeği | `D:\ARCILOGGER\backups\db\` | Kurulum programı, her yükseltmeden önce; kendiliğinden silinmez |
| Ham loglar ve kasa (mühürlü günler) | `D:\ARCILOGGER\spool`, `D:\ARCILOGGER\vault` | Kanıt dosyalarıdır, veritabanı yedeğine **girmez**; ikinci kopyası replikadır (`[replica]`, ör. bir NAS'ta WORM klasörü; varsayılan olarak kapalı, [ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 10) |
| Kanıt paketleri, saklanan resmi raporlar (PDF) | `D:\ARCILOGGER\exports` | Yedeğe girmez (paketler 30 gün tutulur; raporlar silinmez) |
| `secret.key`, `secrets.json`, `arcilogger.toml`, Caddy CA (`caddy\`), `kamusm-roots`, Zamane | `C:\ProgramData\ARCILOGGER` | Yedeğe girmez: **elle, bir kez, ayrı** bir ortama (bölüm 3) |

**Ham loglardan yeniden üretilebilen kısım:** ayrıştırılmış log tabloları ve kira (DHCP/ARP)
zaman çizelgesi. Geri yüklenen veritabanında ingest, yedekten sonra gelen logları spool'dan ve
kasadan yeniden yükler; veritabanı saklama süresi (`db.retention_days`, varsayılan 400 gün)
içindeki mühürlü günler kendiliğinden yüklenir, daha eskiler yüklenmez ("arşivden yeniden yükle"
sonraki bir özelliktir). Kullanıcılar, 5651 sorguları, kişi ve cihaz bilgileri, ayarlar ve
veritabanındaki denetim kaydı **yalnızca** veritabanında ve onun yedeklerinde vardır.

**Yedeğin içinde ne var?** Kullanıcıların parola özetleri (Argon2id), `secret.key` ile
şifrelenmiş TOTP gizli anahtarları, kurtarma kodlarının ve oturumların özetleri ve kişisel veriler
(kişiler, cihazlar, sorgular, denetim kaydı, log satırları). Kayıtlı kimlik bilgileri (veritabanı
parolası, Kamu SM müşteri numarası ve parolası, replika parolası) veritabanında **değildir**;
`secrets.json` içindedir. `secret.key` olmadan yedekteki TOTP anahtarları çözülemez. Yedek
dosyalarını kanıt kadar özenle saklayın: kişisel veri içerirler (KVKK).

## 2. Zamanlama, saklama, kontrol

- Görev `\ARCILOGGER\Backup`, her gün 01:40 (Türkiye saati), LOCAL SERVICE hesabıyla; sunucu o
  saatte kapalıysa açıldıktan sonra çalışır. `pg_dump -Fc` ile, ARCILOGGER'ın kendi veritabanı
  rolüyle (parola şifreli gizli depodan, komut satırına yazılmadan).
- Dosya: `D:\ARCILOGGER\backups\scheduled\arcilogger-YYYYAAGG-SSDDss.dump` ve yanında
  `….dump.json` (SHA-256, boyut, sürümler, şema sürümü). Yedek geçici bir adla yazılır,
  `pg_restore --list` ile denetlenir, sonra adlandırılır.
- Saklama: son **14** günün, son **8** haftanın ve son **12** ayın her birinin en yeni yedeği
  (`[backup] keep_daily`, `keep_weekly`, `keep_monthly`); en yeni yedek ve son başarılı
  çalışmanın yedeği (`last.json`'un gösterdiği) her zaman kalır — geri alınmış bir saat yeni bir
  yedeğe klasördekilerden eski bir ad verir (`R20-backup:clock`, `medium`). Silme yalnızca bu
  klasördeki ARCILOGGER yedek adlarına dokunur; `backups\db` içindeki yükseltme yedeklerine ve
  başka dosyalara dokunmaz.
- Disk: yaklaşık 30 yedek kadar yer. İlk haftalardan sonra `& $db backup list` toplamına bakın.
  Boş alan `[backup] min_free_gb` (10) **artı en yeni yedeğin boyutu** kadar değilse yedek
  başlamaz (ham loglar aynı diske yazılır).
- Klasör izinleri: yalnızca zamanlanmış görev yazar; ARCILOGGER hizmetleri (API, worker, …)
  yedekleri okuyamaz. Sınır: LOCAL SERVICE hesabıyla çalışan her Windows hizmeti görevlerin
  haklarını taşır ([ADR-0007](adr/0007-windows-installer.md) bölüm 8): yedekleri okuyabilir ve
  **bir yedeği yan dosyasıyla birlikte değiştirebilir**. Bu yüzden yan dosya denetimi yalnızca
  kazara bozulmayı bulur; `pg_restore` ise yedekteki SQL'i PostgreSQL süper kullanıcısının
  haklarıyla çalıştırır: SHA-256'sını sunucu dışında kaydettiğiniz bir kopyayı geri yükleyin
  (bölüm 4 ve 5).

Ayarlar (`C:\ProgramData\ARCILOGGER\arcilogger.toml`, `[backup]` bölümü; yoksa varsayılanlar):
`[backup] enabled` (true), `[backup] dir` ("backups/scheduled", `data_dir`'e göre),
`[backup] keep_daily` (14), `[backup] keep_weekly` (8), `[backup] keep_monthly` (12),
`[backup] pg_dump` ve `[backup] pg_restore` (boş: PostgreSQL hizmetinin `bin` klasörü, sonra en
yeni `C:\Program Files\PostgreSQL\<sürüm>\bin`, sonra `PATH`), `[backup] min_free_gb` (10).
`dir` değiştirilirse yeni klasörün izinlerini yönetici kendisi verir (kurulum programı yalnızca
varsayılan klasöre izin verir). `enabled = false` (ör. veritabanını başka bir ürün yedekliyorsa)
görevi hiçbir şey yapmaz hâle getirir ve worker'ın `R20-backup` uyarılarını kapatır (worker
yapılandırmayı 1 dakika içinde yeniden yükler; bu sürümden önceki bir sürümde
`Restart-Service ArciWorker`; [UYARILAR.md](UYARILAR.md)).

**Tarayıcıdan:** web arayüzünde **Ayarlar → Yedekleme** sayfası `[backup] enabled`,
`[backup] keep_daily`, `[backup] keep_weekly`, `[backup] keep_monthly` ve `[backup] min_free_gb`
değerlerini değiştirir ve son yedeğin sonucunu (`state\backup\last.json`) gösterir. Yedeklemeyi
kapatmak parolanın yeniden girilmesini ister ve `R20-config-change` (orta) uyarısı üretir.
`[backup] dir`, `[backup] pg_dump` ve `[backup] pg_restore` yalnızca konsoldan değişir: görev
yalnızca güvenilen yerlerdeki programları çalıştırır, Program Files dışındaki bir `pg_dump` ya da
`pg_restore` yolu kurulumda SHA-256 değeriyle kaydedilir ([AYARLAR.md](AYARLAR.md) bölüm 6). Geri
yükleme ve tatbikat (bölüm 5 ve 6) her zaman konsoldadır.

**Her gün** ([ILK-ADIMLAR.md](ILK-ADIMLAR.md) bölüm 9):

```powershell
Get-Content D:\ARCILOGGER\state\backup\last.json
& $db backup list
```

`last.json` içinde `"ok": true` ve bugünün tarihi (`finished`) olmalıdır. Başarısız ya da
reddedilen bir yedek `"ok": false` ve İngilizce kısa bir hata (`error`) yazar, ayrıca
`alerts.jsonl` dosyasına `R20-backup` (`high`) uyarısı düşer. Ayrıntı:
`D:\ARCILOGGER\logs\backup.log`. Görevin son sonucu `0x1` ise yedek **ya da saklama** ilgi
bekliyor demektir: `"ok": false` ise yedek alınamamıştır; `"ok": true` ve bugünün tarihiyse yedek
alınmış ve denetlenmiştir, yalnızca eski yedekler silinememiştir (`R20-backup:prune`,
`medium`); tarih daha eskiyse yedek alınmış ama `last.json` yazılamamış olabilir (`R20-backup`,
`high`) — hangisi olduğunu `backup.log` gösterir. `R20-backup` ve `R20-backup:clock` sonraki
başarılı bir yedekle çözülür; `R20-backup:prune`, o yedek bunu yeniden bildirmediyse bir saat
sonra çözülür.

**Elle yedek ve doğrulama:**

```powershell
& $db backup run                       # şimdi bir yedek (görevle aynı iş)
& $db backup verify                    # en yeni yedek: boyut, SHA-256, pg_restore --list
& $db backup verify --all --deep       # bütün yedekler, verinin tamamı okunur (uzun sürer)
& $db backup prune --dry-run           # saklama kuralı neyi silerdi
```

## 3. Çevrim dışı kopya: `secret.key`, Caddy CA, `arcilogger.toml`

Bunlar yedeklerin yanına **hiçbir zaman kendiliğinden kopyalanmaz**: yedek + `secret.key` TOTP
anahtarlarını, `secret.key` + `secrets.json` Kamu SM kimlik bilgilerini ve veritabanı parolasını
açar; Caddy CA'nın özel anahtarı sunucuyu taklit etmeye yeter. Kurulumdan sonra **bir kez** ve
her değişiklikten sonra (anahtar yenilendi, yapılandırma değişti) şifreli, çevrim dışı bir ortama
(ör. BitLocker To Go ile şifrelenmiş bir USB bellek — **doğrulayın**) kopyalayın; bu ortamı
yedeklerin kopyalarından **ayrı** bir yerde (kasa/dolap) saklayın:

```powershell
$usb = "E:\ARCILOGGER-anahtarlar-$(Get-Date -Format yyyyMMdd)"
New-Item -ItemType Directory -Path $usb | Out-Null
Copy-Item C:\ProgramData\ARCILOGGER\secret.key, C:\ProgramData\ARCILOGGER\arcilogger.toml -Destination $usb
Copy-Item C:\ProgramData\ARCILOGGER\caddy -Destination $usb -Recurse
Get-FileHash $usb\secret.key, $usb\arcilogger.toml -Algorithm SHA256
```

`secrets.json` (şifreli kimlik bilgileri) aynı şifreli ortama konabilir; konmazsa yeni sunucuda
Kamu SM kimlik bilgileri yeniden girilir (`& $core secret set …`). Özetleri kâğıda yazıp ortamla
birlikte saklayın.

## 4. Yedekleri sunucu dışına kopyalama

Yedekler veritabanıyla **aynı diskte** durur: yanlış bir işleme, başarısız bir yükseltmeye ya da
mantıksal bir bozulmaya karşı korur, diskin ya da sunucunun kaybına karşı korumaz. Haftada bir (ve
önemli bir değişiklikten önce) dosyaları sidecar'larıyla birlikte harici bir diske ya da bir NAS'ta
**ayrı** bir paylaşıma kopyalayın (WORM klasörü kasanın replikasına aittir):

```powershell
# Harici disk (F:):
robocopy D:\ARCILOGGER\backups\scheduled F:\ARCILOGGER-veritabani *.dump *.dump.json /XO /R:1 /W:5
# NAS'ta ayrı bir paylaşım (<NAS> cihazın adı; adı ve hakları sahada belirlenecek):
robocopy D:\ARCILOGGER\backups\scheduled \\<NAS>\arcilogger-db-yedek *.dump *.dump.json /XO /R:1 /W:5
# Kopyayı denetleyin (yan dosyadaki SHA-256 ile):
& $db backup verify (Get-ChildItem F:\ARCILOGGER-veritabani\*.dump | Sort-Object Name | Select-Object -Last 1).FullName
# Her yeni kopyanın SHA-256'sını bir kez, kopyaların yanına (sunucu dışında) kaydedin:
$liste = "F:\ARCILOGGER-veritabani\SHA256.txt"
Get-ChildItem F:\ARCILOGGER-veritabani\*.dump | Where-Object { -not ((Test-Path $liste) -and (Select-String -Path $liste -SimpleMatch -Quiet -Pattern $_.Name)) } | Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash)  $(Split-Path $_.Path -Leaf)" } | Add-Content $liste
```

Geri yükleme `SHA256.txt` ile denetlenir (`--sha256`, bölüm 5): sunucudaki yan dosya yedekle
birlikte değiştirilebilir (bölüm 2). Bir yedek yazıldıktan sonra hiç değişmez; bir kez kaydedilen
satır geçerli kalır. Aynı adın sonraki bir `Get-FileHash` değeri farklıysa o dosyayı geri
yüklemeyin. `SHA256.txt`'yi yalnızca yöneticilerin yazabildiği yerde tutun (NAS paylaşımında da
aynısını yapın).

Kopyalar da kişisel veri içerir: şifreli bir disk ya da yalnızca yöneticilerin erişebildiği bir
paylaşım kullanın. Dışarıdaki eski kopyaları siz silersiniz (ARCILOGGER yalnızca kendi klasörünü
temizler). Kopyalamanın otomatikleştirilmesi sahibin kararıdır
([ADR-0009](adr/0009-database-backup.md), açık konular).

## 5. Geri yükleme

Geri yükleme **her zaman yeni bir veritabanına** yapılır: canlı veritabanı silinmez, üzerine
yazılmaz. Var olan bir ada, yapılandırmadaki (canlı) veritabanına, yan dosyasındaki SHA-256'ya
uymayan ya da `--sha256` ile verilen SHA-256'dan farklı bir yedeğe geri yükleme, hiçbir şey
değiştirilmeden reddedilir. PostgreSQL süper kullanıcısının (`postgres`) parolası sorulur; komut
satırına yazılmaz.

**Hangi dosya?** `pg_restore` yedekteki SQL'i süper kullanıcının haklarıyla çalıştırır;
`backups\scheduled` içindeki bir yedek ise yan dosyasıyla birlikte LOCAL SERVICE hesabıyla
çalışan herhangi bir işlem tarafından değiştirilebilir (bölüm 2): yan dosya denetimi yalnızca
kazara bozulmayı bulur. Mümkün olan her durumda sunucu dışındaki bir kopyayı, `SHA256.txt`
içinde onun için kaydedilmiş SHA-256 ile geri yükleyin (bölüm 4). Böyle bir kaydı olmayan bir
yedek (dün geceki, henüz kopyalanmamış) yalnızca kazara bozulmaya karşı denetlenir; komut bunu
çıktısında söyler.

1. Yedeği seçin ve denetleyin:

   ```powershell
   & $db backup list
   $dosya = "F:\ARCILOGGER-veritabani\arcilogger-20261019-014002.dump"
   $ozet  = "<SHA256.txt içinde bu dosya için kayıtlı SHA-256>"
   & $db backup verify --deep $dosya
   ```

2. Yeni bir veritabanına geri yükleyin (ad: küçük harf, rakam ve `_`):

   ```powershell
   & $db backup restore --file $dosya --sha256 $ozet --target-db arcilogger_20261019
   ```

   `backups\scheduled` içindeki bir yedek ya da yükseltme öncesi bir yedek (`backups\db\*.dump`;
   yan dosyası yoktur, yalnızca `pg_restore --list` denetlenir) aynı komutla, özet kaydedilmediyse
   `--sha256` olmadan geri yüklenir. Yeni veritabanı, kurulum programınınki gibi `C` yerel
   ayarını alır (PostgreSQL kümesinin varsayılanını değil). TimescaleDB varsa
   `timescaledb_pre_restore()` / `timescaledb_post_restore()` kendiliğinden çalışır. Geri yükleme
   komutları sırayla çalıştırır ve ilk hatada durur: yeni veritabanı o zaman **yarımdır** —
   komutun yazdığı `DROP DATABASE` ile silin ve (aynı ya da başka bir adla) yeniden deneyin.

3. Komut, ARCILOGGER'ı yeni veritabanına nasıl geçireceğinizi yazar:

   ```powershell
   Stop-Service ArciWorker, ArciApi, ArciIngest
   notepad C:\ProgramData\ARCILOGGER\arcilogger.toml   # [database] name = "arcilogger_20261019"
   & $db upgrade                                         # yalnızca komut "şema eski" dediyse
   Start-Service ArciIngest, ArciApi, ArciWorker
   ```

   Toplayıcı ve mühürleme veritabanı kullanmaz; bu sırada çalışmaya devam eder. Hizmetler
   başladıktan sonra web arayüzünde oturum açın, **Denetim Kaydı** ve **5651 Sorgu** sayfalarını
   deneyin; `& $ingest status` yedekten sonraki günlerin yüklenişini gösterir.
4. Geri dönmek için `[database] name` değerini eski adına çevirip hizmetleri yeniden başlatın.
   Eski veritabanını yeni olan kabul edilmeden **silmeyin**; silmek sahibin kararıdır
   (`& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE arcilogger"`
   — **doğrulayın**: ad ve PostgreSQL yolu).

**Denetim kaydı ve geri yükleme:** eski bir yedeğe dönmek, veritabanındaki denetim kaydını yedeğin
anına geri sarar. Yedekten sonraki kayıtlar kasadaki mühürlü günlük denetim dosyalarında ve
bugünkü `spool\_audit` dosyasında kalır; geri yüklenen zincir yedeğin son kaydından devam eder.
Her geri yüklemeyi (tarih, dosya, neden, kim) yazılı bir tutanakla kaydedin.

**Yeni bir sunucuya (felaket sonrası):** (1) PostgreSQL'i ve ARCILOGGER'ın önkoşullarını
[KURULUM.md](KURULUM.md)'ye göre kurun; (2) çevrim dışı ortamdan `arcilogger.toml`, `secret.key`
(ve varsa `secrets.json`) dosyalarını `C:\ProgramData\ARCILOGGER\` klasörüne, `caddy\`
klasörünü de aynı yere kopyalayın — kurulum programı var olan anahtarı ve yapılandırmayı korur;
(3) kasayı replikadan `D:\ARCILOGGER\vault` klasörüne geri getirin; (4) kurulum programını
çalıştırın; (5) en yeni yedeği bölüm 5'e göre yeni bir veritabanına geri yükleyip ARCILOGGER'ı
ona geçirin. Bu sıra **sahada doğrulanacaktır**; ilk tatbikatta deneyin.

## 6. Geri yükleme tatbikatı (üç ayda bir)

Bir yedek ancak geri yüklenebildiğinde yedektir. **Üç ayda bir** sunucu dışındaki en yeni kopyayı
(bölüm 4; böylece kopya da denenmiş olur) bir deneme veritabanına geri yükleyin, birkaç tabloyu
sayın, deneme veritabanını silin ve sonucu (tarih, dosya, süre, sayılar) yazın:

```powershell
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"   # doğrulayın: kurulu sürüm
$son  = (Get-ChildItem F:\ARCILOGGER-veritabani\*.dump | Sort-Object Name | Select-Object -Last 1).FullName
$ozet = "<SHA256.txt içinde onun için kayıtlı SHA-256>"
& $db backup restore --file $son --sha256 $ozet --target-db arcilogger_tatbikat
& $psql -h 127.0.0.1 -U postgres -d arcilogger_tatbikat -c "SELECT count(*) FROM users" -c "SELECT count(*) FROM lookups" -c "SELECT count(*) FROM audit_log"
& $psql -h 127.0.0.1 -U postgres -d postgres -c "DROP DATABASE arcilogger_tatbikat"
```

Sayıları aynı sorgularla canlı veritabanındakilerle karşılaştırın (canlıda yedekten sonra eklenen
kayıtlar kadar fazla olabilir). Tatbikat canlı veritabanına dokunmaz; hizmetleri durdurmak
gerekmez.

## 7. Sorun giderme

| Belirti (`last.json` `error`, `backup.log`) | Ne yapılır |
|---|---|
| `not enough free space …` | `D:` diskinde yer açın ya da `[backup] min_free_gb` / saklama sayılarını gözden geçirin; ham loglar aynı diske yazılır |
| `pg_dump not found …` | `[backup] pg_dump` ve `[backup] pg_restore` değerlerine tam yolu yazın (ör. `C:/Program Files/PostgreSQL/16/bin/pg_dump.exe`) |
| `… older than the server …` | Sunucunun kendi `bin` klasöründeki `pg_dump.exe`'yi `[backup] pg_dump` ile gösterin |
| `password authentication failed …` | Veritabanı parolası gizli depoyla uyuşmuyor: kurulum programını `-ResetDatabasePassword` ile yeniden çalıştırın |
| `connection … refused` / `timeout` | PostgreSQL hizmeti çalışıyor mu (`Get-Service postgresql*`)? |
| `Permission denied` / `Access is denied` (`backups\scheduled`, `state\backup`) | İzinleri yeniden uygulayın: `powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\scripts\Set-ArciVaultAcl.ps1"` |
| `retention failed: …` (`backup.log`, `R20-backup:prune`) | Yeni yedek alınmıştır (`"ok": true`); eski yedekler silinemedi (`backups\scheduled` izinleri, robocopy ya da virüs tarayıcısının açık tuttuğu bir dosya). Nedeni giderin; sonraki çalışma onları siler |
| `out of shared memory` … `max_locks_per_transaction` (`pg_dump`) | `pg_dump` her tabloyu tek bir işlem içinde kilitler; TimescaleDB'de her chunk bir tablodur (sayıları her gün artar). PostgreSQL'in kilit tablosu bağlantı (`max_connections`) başına yaklaşık `max_locks_per_transaction` (varsayılan 64) kilit tutar. `postgres` olarak: `ALTER SYSTEM SET max_locks_per_transaction = 256;`, ARCILOGGER hizmetlerini durdurup PostgreSQL hizmetini yeniden başlatın — değeri tablo sayısıyla **doğrulayın** (`SELECT count(*) FROM pg_class WHERE relkind IN ('r', 'p')`). Geri yükleme komutları tek tek çalıştırır, buna ihtiyaç duymaz |
| `… differs from --sha256 …` (geri yükleme) | Bu dosyayı geri yüklemeyin: özeti kaydedilen dosya değildir. Başka bir kopya alın ve dosyanın nasıl değiştiğini araştırın (bölüm 2) |
| Görev `0x1`, `last.json` güncel değil | `logs\backup.log` dosyasına bakın; `& $db backup run` ile elle deneyin |

## 8. Sahada doğrulanacaklar

- TimescaleDB kurulu bir veritabanında `pg_dump`'ın ARCILOGGER rolüyle (süper kullanıcı olmadan)
  çalışması; geri yüklemede `timescaledb_pre_restore()` / `timescaledb_post_restore()`.
- TimescaleDB ile: tablo sayısının (chunk'lar ve sıkıştırılmış chunk'lar)
  `max_locks_per_transaction` × `max_connections` ile karşılaştırılması, yılda bir (bölüm 7);
  gerçek veriyle bir geri yükleme tatbikatı.
- LOCAL SERVICE'in geçici klasöründe parola dosyasının yalnızca o hesaba açık olması
  (`C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp`).
- Gerçek veriyle yedeğin süresi (görev 4 saatte durdurulur) ve boyutu; saklama sayılarının diske
  sığması.
- Harici disk ya da NAS paylaşımına kopyalama; BitLocker To Go; felaket sonrası kurulum sırası.
