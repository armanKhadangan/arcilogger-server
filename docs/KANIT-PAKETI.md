# Kanıt Paketi ve Mühürleme Sayfası / Evidence Packages and the Sealing Page

> ARCILOGGER, 5651 sayılı Kanun ve ilgili Yönetmelik kapsamındaki yükümlülükleri **desteklemek için
> tasarlanmıştır**; hukuki uygunluk iddiasında bulunmaz. Paketlerin bir talebe nasıl teslim
> edileceğini hukuk danışmanınızla netleştirin. English version: [below](#english).

Paketin içeriği ve bağımsız doğrulama adımları: [DENETCI-KILAVUZU.md](DENETCI-KILAVUZU.md) bölüm
4-7. Bu belge web arayüzü / API tarafını anlatır.

## 1. Kim ne yapabilir?

| İşlem | Yönetici | Denetçi | İK / Rapor İzleyici |
|---|---|---|---|
| Paketleri ve mühür takvimini görmek | ✔ | ✔ (paketler: aşağıdaki not) | — |
| Paket oluşturmak | ✔ (son 5 dk içinde parola onayı) | — (sahip kararı, `docs/roles.md`) | — |
| Paket indirmek | ✔ (parola onayı + KVKK metni) | ✔ (parola onayı + KVKK metni; aşağıdaki not) | — |
| Bir günü şimdi doğrulamak ("Doğrula") | ✔ | ✔ | — |
| "Şimdi mühürle", "Kontör sorgula" | ✔ (`seal.manage`, bölüm 4) | — | — |

**Sorgu paketlerinin görünürlüğü:** bir 5651 sorgusundan oluşturulan paket o sorgunun resmi
raporunu (talep, gerekçe, sonuç, kişi zinciri) içerir. Bu yüzden sorguların görünürlük kuralı
paketlere de uygulanır: yönetici tüm paketleri görür; denetçi yalnızca gün aralığıyla oluşturulan
paketleri ve **kendi** sorgularının paketlerini görür ve indirir. Başka bir kullanıcının
sorgusundan oluşturulan paket listede yer almaz; ayrıntı ve indirme `404` döner (sorgunun kendisi
gibi). Sorgusu silinmiş bir sorgu paketi yalnızca yöneticiye görünür. Denetim kaydını içeren bir
paket (`include_audit_log`) girdileri gerçek kullanım hedefleri ve özetleriyle içerir; bu yüzden
`usage.pseudonymize_anonymous_role` açıkken Kullanım sayfalarında takma ad gören bir okuyucuya
(Denetçi) aynı şekilde görünmez ([KULLANIM.md](KULLANIM.md) bölüm 6).

## 2. Paket oluşturma (`POST /api/v1/evidence`)

Kapsam iki yoldan biriyle seçilir:

- **5651 sorgusundan** (`lookup_id`): sorgu sonucunun dayandığı ham satırlar (FortiGate oturumu,
  DHCP/ARP kayıtları, IP çakışmasındaki diğer cihazlar ve raporun bölüm 6'da gösterdiği, kira
  zaman çizelgesine girmemiş FortiGate DHCP satırları dahil) ve sorgu günü pakete girer.
  Makam ve dosya no paketin referansı olur. `full_days` ile (varsayılan) paket, bu günlerin
  **tüm kaynaklarının** mühürlü dosyalarını tam olarak içerir (yalnızca sorgunun dayandığı
  kaynakları değil; `sources` verilirse yalnızca onlar ve alıntılanan satırların kaynakları);
  alıntılanan satırlar ayrıca `extract/` altındadır. Yalnızca dayanılan kaynakların tam
  dosyaları isteniyorsa `content: "extract"` seçilir.
- **Elle** (`from` / `to`, en fazla 31 gün; isteğe bağlı `sources` ve satır listesi `extract`).

İçerik (`content`): `full_days` — seçilen kaynakların o günlerdeki mühürlü dosyalarının tamamı
(alıntılanan satırlar ayrıca `extract/` altında); `extract` — alıntılanan satırlar ve yalnızca bu
satırların geldiği kaynakların o günlerdeki **tam** mühürlü dosyaları (alıntı, pakette tam dosyaya
karşı bayt bayt doğrulanır).

**ARCILOGGER denetim kaydı varsayılan olarak pakete girmez** (KVKK veri en aza indirme): günün
`audit.jsonl.gz` dosyası o günkü diğer bütün sorguları (makam, dosya no, sorgulanan IP/MAC) ve
personel bilgilerini içerir. `paket.json` içinde günün `excluded` listesinde `kind: "audit_log"`,
manifestteki SHA-256 değeri ve gerekçesiyle yer alır; `verify.py` bunu hata değil "pakete
alınmadı" bilgisi (`audit-log-excluded`) olarak gösterir. İç denetim için yalnızca yönetici açıkça
`include_audit_log` seçeneğiyle (komut satırında `evidence build --include-audit-log`) ekleyebilir;
`paket.json` `scope.audit_log` bunu kaydeder. API'de `include_audit_log: true` yalnızca yönetici
rolünden kabul edilir (başka bir rol paket oluşturma yetkisi alsa bile `403
audit_log_admin_only`, hiçbir iş yapılmadan); seçim paketin kapsamında
(`scope.include_audit_log`) ve iki denetim kaydında (`evidence.request`, `evidence.create`)
görünür.

Kök sertifikalar: pakete yalnızca paketteki damgaların gerçekten bağlandığı kökler girer. Bir gün
paket ve raporda ancak manifesti Kamu SM üretim sunucusunu gösteriyorsa **ve** doğrulanan damga
zinciri `tsa.legal_root_fingerprints` listesindeki bir köke ulaşıyorsa "yasal sınıf" (`legal_grade`)
sayılır; liste boşsa (varsayılan) hiçbir gün öyle gösterilmez. `paket.json` her gün için
`legal_grade`, `legal_grade_claimed` (manifestin kendi beyanı) ve `root_sha256` değerlerini yazar.
API'deki paket kaydında `legal_grade` üç değer alır: `true` (her gün yasal sınıf), `false` (test
ya da mock sağlayıcıyla damgalanmış bir gün var) ve `null` (günler Kamu SM üretim damgası beyan
ediyor ve damgalar doğrulandı, ama her kök `tsa.legal_root_fingerprints` listesinde değil — bu
bir test damgası değildir; kılavuz karşılaştırılacak kök parmak izini verir). Hiç log olmadığı
için boş geçilen günler (`unsealed_empty_days`) bir tarih aralığına, onları listeleyen mühürlü
günle birlikte alınabilir; o gün aralıkta yoksa istek `409 days_not_sealed` ile
(`details.covered_by`) reddedilir.

`password: true` paketi AES-256 ile şifreler. Parola **yalnızca bu isteğin cevabında bir kez**
gösterilir; veritabanına, denetim kaydına ve sistem kayıtlarına yazılmaz. Kaybolursa paket
açılamaz — yeniden oluşturun.

İstek hemen `202` ile döner; paket arka planda hazırlanır (`queued` → `building` → `ready` /
`failed`). Bir API sürecinde aynı anda tek paket hazırlanır. Kasa yalnızca okunur; paket
`D:\ARCILOGGER\exports\` klasörüne yazılır. Mühürlenmemiş gün, değişmiş bir kasa dosyası ya da
ARCILOGGER'ın kendi doğrulayıcısından geçmeyen paket verilmez (`failed` ve hata metni).

Denetim kaydı: `evidence.request` (kapsam, alıntı satır sayısı ve özeti, parolalı mı) ve paket
bitince `evidence.create` (paket no, ZIP'in, `paket.json`'ın ve `verify.py`'nin SHA-256 özetleri
ya da hata). Bir gün ancak Seal görevi onu tamamladıysa pakete girer: zaman damgası kasada olsa
bile görev günü bitiremediyse (ör. dosyalar kilitlenemedi) istek `409 days_not_sealed` alır.

### Sınırlar (veri diski korunur)

Paketler ham kayıtlar, kasa ve durum dosyalarıyla **aynı diske** yazılır; disk dolarsa toplayıcı
kayıt yazamaz (UDP syslog kaybolur) ve mühürleme durur. Bu yüzden istek, kuyruğa alınmadan önce
reddedilir:

| Durum | Cevap | Ayar (varsayılan) |
|---|---|---|
| Aynı kullanıcı, aynı kapsam, önceki paket henüz bitmedi (çift tıklama) | `409 evidence_duplicate` | — |
| Kullanıcının bitmemiş paketi çok | `429 rate_limited` | `evidence.max_unfinished_per_user` (2) |
| Tüm kullanıcıların bitmemiş paketi çok | `429 rate_limited` | `evidence.max_unfinished_total` (5) |
| Son bir saatte çok istek | `429 rate_limited` | `evidence.max_requests_per_hour` (10) |
| Paket ve bekleyen paketlerden sonra boş yer yedeğin altına iner | `507 disk_space_low` | `evidence.min_free_percent` (10), `evidence.min_free_gb` (20) |
| `exports\` klasörü sınırı aşılır | `507 exports_quota_exceeded` | `evidence.exports_max_gb` (100) |

Paket boyutu manifestlerden **tahmin edilir** (seçilen dosyaların boyutu + sabit pay; mühürlü
dosyalar ZIP'e olduğu gibi girdiği için gerçek boyuta yakın, normalde biraz üstünde) ve paket
bilgisinde `scope.estimated_bytes` olarak görünür. Boş yer, paket yazılmaya başlamadan hemen önce
bir kez daha kontrol edilir; yetmezse paket `failed` olur. Varsayılanlar ölçülmüş değerler değil,
başlangıç değerleridir: disk boyutuna ve beklenen taleplere göre sahada ayarlayın
(yönetici PowerShell'inde tam yolla:
`& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set evidence.min_free_gb 50 --by AD`;
doğrulanır ve denetim kaydına `settings.change` olarak yazılır).

## 3. İndirme ve saklama

`GET /api/v1/evidence/{id}/download` (son 5 dk içinde parola onayı ve KVKK metni gerekir): ZIP
ek olarak iner (`Content-Disposition: attachment`, önbelleğe alınmaz, `Repr-Digest` başlığında
SHA-256). Her indirme `evidence_downloads` tablosuna ve denetim kaydına (`evidence.download`)
yazılır, sayaç artar. Kısmi indirme (`Range`) desteklenmez (`Accept-Ranges: none`): her cevap
dosyanın tamamıdır, böylece sayaç ve denetim kaydı her zaman tam bir kopyayı gösterir. Kesilen
bir indirme baştan yapılır ve yeni bir indirme olarak kaydedilir.

Paketler `evidence.keep_days` ayarı kadar (varsayılan **30 gün**) saklanır. Worker her gece
03:00'ten sonra süresi dolan paketleri siler (`evidence.purge`, denetim kaydına yazılır); süresi
dolan ya da dosyası olmayan paket için indirme `410` döner. Kanıtın kendisi kasada kalır; paket
gerektiğinde yeniden oluşturulur.

**Alıcıya iletilecekler:** ZIP (parolalıysa parola ayrı bir yoldan) ve paket ayrıntısındaki
`verify.py` SHA-256 değeri (`verifier_sha256`) — teslim tutanağına yazın; alıcı doğrulayıcının
değiştirilmediğini bununla kontrol eder (DENETCI-KILAVUZU bölüm 5).

## 4. Mühürleme sayfası (`/api/v1/seals`, `/api/v1/credits`)

- **Takvim** (`GET /seals/calendar?month=YYYY-MM`): her gün için `verified` (mühürlü ve son 35
  günde doğrulandı), `sealed` (yakın zamanda doğrulanmadı), `late` (geç damgalandı), `pending`,
  `failed` (mesajıyla), `open` (bugün), `future`, `no_data`, `deleted` (saklama süresi doldu).
  Bilgiler kasadan ve zamanlanmış görevlerin durum dosyalarından okunur, veritabanından değil.
  Zaman damgası alınmış ama Seal görevinin tamamlayamadığı gün (durum dosyasında hata) `failed`
  olarak hatasıyla gösterilir. Zaman damgası olmayan ya da günleri hâlâ kasada duran bir imha
  tutanağı (başarısız imha çalışması) günü `deleted` yapmaz.
- **Gün ayrıntısı** (`GET /seals/{gün}`): manifestteki dosyalar, boyutlar, satırlar ve özetler,
  önceki güne bağlantı (kasadaki hâliyle hâlâ uyuşuyor mu), kesintiler, saat bilgisi, zaman damgası
  (genTime, seri no, TSA, politika, sertifika geçerliliği, sağlayıcı, `legal_grade`, anlık damga
  kontrolü), son doğrulama ve replika durumu. Manifest ve damga indirilebilir (`seal.download`).
  Damga üç ayrı bilgiyle gösterilir: `legal_grade` (doğrulanan zincir
  `tsa.legal_root_fingerprints` listesindeki bir köke ulaşıyor), `legal_grade_claimed` (manifest
  Kamu SM üretim sunucusunu gösteriyor) ve `test_stamp` (ARCILOGGER'ın test/mock damgası).
  Manifesti üretim sunucusunu gösteren ama kökü listede olmayan (ya da damgası doğrulanamayan)
  gün "Kamu SM üretim (kök tanımlı değil)" etiketi ve bilgi notuyla gösterilir, test/mock damgası
  sayılmaz; kırmızı "yasal geçerliliği yok" uyarısı test/mock ve üretim sunucusu olmayan
  sağlayıcılar içindir (üretim beyanı taşıyan bir test damgası da dahil). Doğrulama ve damga kontrolü hataları
  Türkçe (`errors_tr`, `warnings_tr`) gösterilir; çekirdeğin İngilizce metni teknik ayrıntı olarak
  kalır.
- **Doğrula** (`POST /seals/{gün}/verify`): zamanlanmış doğrulamayla aynı kontrol (varsayılan
  derin: dosyalar açılıp yeniden özetlenir), ayrı bir süreçte, **300 sn** sınırıyla. Sonuç
  `seal_verifications` tablosuna yazılır (API `state\verify` klasörüne yazamaz) ve denetlenir
  (`seal.verify`); takvim zamanlanmış ve anlık doğrulamanın yenisini gösterir. Süre aşımı ya da
  doğrulama sürecinin hiç başlatılamaması (`error`) sonuçsuz sayılır, yine kaydedilir ve
  denetlenir, günü kırmızıya çevirmez.
- **Şimdi mühürle** (`POST /seals/{gün}/seal`, `seal.manage`, `202`): web hizmetinin kasaya
  yazma izni yoktur (ADR-0007); istek yönetim hizmeti ArciAdmin üzerinden imzalı bir görev isteği
  olur ve `\ARCILOGGER\Seal` görevi sıradaki (en eski) mühürlenmemiş günü bir kez mühürlemeyi
  dener (ADR-0014; gece yarısından sonraki bekleme, boş gün kuralı ve gün sırası atlanmaz). Görev
  her gece 00:10'dan itibaren 5 dakikada bir de çalışır ve başarısız denemeleri tekrarlar.
  Tarayıcıdan yönetim kapalıyken ya da ArciAdmin yanıt vermiyorken düğme kullanılamaz; o zaman
  görevi Görev Zamanlayıcı'dan çalıştırın.
- **Kontör** (`GET /credits`): `\ARCILOGGER\Credit` görevinin günlük sorgu geçmişi ve uyarı
  eşiği. **Kontör sorgula** (`POST /credits/query`, `seal.manage`, dakikada en çok 1) sorguyu
  ArciAdmin üzerinden `\ARCILOGGER\Requests` görevine bir kez yaptırır; yanıt aynı geçmişe
  yazılır.
- **Denetim kaydı:** zamanlanmış mühürleme ve imha veritabanı olmadan çalışır; worker her 15
  dakikada bir sonuçlarını denetim kaydına aktarır — her olay **bir kez**:
  `seal.run` mühürlenen her gün (`sealed`) ve her başarısız deneme (`failed`; `state\alerts.jsonl`
  içindeki `R20-seal` uyarılarından, durum dosyasında üzerine yazılmış denemeler dahil);
  `retention.run` zaman damgalı ve günlerini gerçekten silmiş her imha tutanağı (`deleted`),
  reddedilen her imha çalışması (`refused`, `R20-retention` uyarısı) ve bir gün sonra hâlâ
  tamamlanmamış tutanak (`incomplete`: damgası yok ya da günler kasada duruyor; silme olarak
  **sayılmaz**).

<a id="english"></a>

## English

ARCILOGGER is *designed to support* Law 5651 obligations; it does not claim legal compliance.

- **Who:** administrators create packages (re-authentication within 5 minutes); administrators
  and auditors list, download (re-authentication + accepted KVKK notice) and verify days. The
  auditor does not create packages (owner decision). A package of a 5651 lookup carries that
  lookup's report, so the lookup visibility rule applies: non-admins see and download only
  packages chosen by days and packages of **their own** lookups; another user's lookup package
  is not listed and answers `404` (as the lookup itself does). A package with the audit log
  (`include_audit_log`) holds the entries with their real usage targets and hashes, so it is
  hidden in the same way from a reader who sees pseudonyms on the Kullanım pages (the auditor
  while `usage.pseudonymize_anonymous_role` is on; [USAGE.md](USAGE.md) section 6).
- **Create** (`POST /api/v1/evidence`): scope from a 5651 lookup (`lookup_id`: the raw lines its
  result cites, the FortiGate DHCP lines outside the lease timeline that its report shows in
  section 6 included, and the lookup day; with `full_days`, the default, the package holds the complete
  sealed files of **all** sources of those days — not only the cited ones — unless `sources`
  narrows them, plus the cited lines under `extract/`) or explicit (`from`/`to`, at most 31 days, optional `sources`
  and `extract` lines). `content`: `full_days` (all sealed files of the chosen sources) or
  `extract` (the cited lines plus the complete sealed files of their sources on those days).
  ARCILOGGER's own audit log (`audit.jsonl.gz`) is **left out by default** (KVKK data
  minimisation: it holds every other lookup of the day and staff data); `paket.json` lists it in
  the day's `excluded` with `kind: "audit_log"` and its sealed SHA-256, and `verify.py` reports it
  as "not included" (`audit-log-excluded`), not as a failure. Only an administrator can add it
  explicitly (`include_audit_log`; command line `evidence build --include-audit-log`), recorded in
  `scope.audit_log`. The API accepts `include_audit_log: true` from the administrator role only
  (any other role gets `403 audit_log_admin_only` before any work, even if it is ever allowed to
  create packages); the choice is shown in the package scope (`scope.include_audit_log`) and in
  both audit entries (`evidence.request`, `evidence.create`). Only the roots the package's tokens chain to are bundled; a day counts as
  legal grade only when its manifest claims the Kamu SM production server **and** the verified
  chain reaches a root listed in `tsa.legal_root_fingerprints` (empty by default: no day is).
  The package row's `legal_grade` is `true` (every day legal grade), `false` (a test or mock
  stamped day) or `null` (Kamu SM production stamps that verified, but not every root is listed
  — not a test stamp). Days skipped for having no log (`unsealed_empty_days`) can be packaged
  with the sealed day that lists them; without it the request is refused with `409
  days_not_sealed` and `details.covered_by`.
  `password: true` encrypts the ZIP (AES-256); the generated password is in that response only
  and is never stored, logged or audited. The request returns `202`; one background thread per
  API process builds packages one at a time into `exports\`, reading the vault only. Audit:
  `evidence.request`, then `evidence.create` with the SHA-256 of the ZIP, `paket.json` and
  `verify.py` (or the error). Only days the Seal task completed can be packaged.
- **Limits** (the packages share the data disk with the spool and the vault): a duplicate of an
  unfinished request of the same user → `409 evidence_duplicate`; more than
  `evidence.max_unfinished_per_user` (2) / `evidence.max_unfinished_total` (5) unfinished builds
  or `evidence.max_requests_per_hour` (10) → `429 rate_limited` with `Retry-After`; free space
  after this and every waiting package below `evidence.min_free_percent` (10 %) of the disk, at
  least `evidence.min_free_gb` (20 GiB) → `507 disk_space_low`; `exports\` above
  `evidence.exports_max_gb` (100 GiB) → `507 exports_quota_exceeded`. The package size is
  estimated from the manifests (`scope.estimated_bytes`); the builder checks the free space
  again just before writing. The defaults are starting values to adjust on site.
- **Download** (`GET /evidence/{id}/download`): attachment, no caching, `Repr-Digest` header;
  byte ranges are not served (`Accept-Ranges: none`, always the whole file); every download is
  recorded, audited and counted. Packages are kept `evidence.keep_days`
  (30 days); the worker's nightly `evidence.purge` deletes expired ones (audited); afterwards
  the download answers `410`. Give the recipient the `verifier_sha256` on a separate channel.
- **Sealing page:** calendar states (`verified`, `sealed`, `late`, `pending`, `failed`, `open`,
  `future`, `no_data`, `deleted`) from the vault and the task state files; day detail with the
  exact manifest values, the chain link, the token (three facts kept apart: `legal_grade`
  confirmed by a root of `tsa.legal_root_fingerprints`, `legal_grade_claimed` from the manifest,
  `test_stamp` for the mock TSA — a production day whose root is not listed gets the "Kamu SM
  production (root not configured)" badge and an info notice, never the red test/mock one; verification and token-check errors
  in Turkish as `errors_tr`/`warnings_tr`) and the replica; manifest/token downloads
  (audited); "Verify now" in a child process with a 300 s limit, stored in `seal_verifications`
  (the API may not write `state\verify`); "Seal now" (`POST /seals/{day}/seal`, `seal.manage`,
  `202`) becomes a signed task request through the administration service ArciAdmin, and the
  `\ARCILOGGER\Seal` task tries the oldest unsealed day once (ADR-0014); `/credits` shows the Kamu
  SM credit history, and "Query credit" (`POST /credits/query`, at most once a minute) has the
  `\ARCILOGGER\Requests` task query it once. While browser administration is off or ArciAdmin
  does not answer, run the `\ARCILOGGER\Seal` or `\ARCILOGGER\Credit` task in Task Scheduler.
- **Sealing states:** a day whose token is in the vault but which the Seal task did not finish
  (an error in its state file) is `failed` with that error; a deletion record without a time
  stamp, or whose days are still in the vault, does not make a day `deleted`. A verification
  process that cannot start is stored and audited as an inconclusive `error`.
- **Audit import:** every 15 minutes the worker copies scheduled sealing (`seal.run`: each sealed
  day and every failed attempt, from the state files and the `R20-seal` alerts) and retention
  (`retention.run`: `deleted` for a stamped record whose days are gone, `refused` for each
  `R20-retention` refusal, `incomplete` for a record still incomplete after a day — never counted
  as a deletion) into the audit log, each exactly once.
