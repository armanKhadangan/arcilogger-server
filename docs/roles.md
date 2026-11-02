# Roller ve yetkiler / Roles and permissions

> Kaynak: yapım şartnamesi bölüm 13. Kodda tek kaynak `api/src/arcilogger_api/security.py`
> (`ROLE_PERMISSIONS`); `api/tests/test_security.py` tabloyu şartnameden bağımsız olarak yeniden
> yazar ve karşılaştırır. Açık sorular en altta — ürün sahibi karar verdikçe güncellenir.

## Özet (TR)

- Dört rol vardır: **Yönetici** (admin), **Denetçi** (auditor), **İK Raporları** (hr),
  **Rapor İzleyici** (viewer). Bir kullanıcının tek rolü olur.
- Sahibin 2026-10-20 kararıyla kullanım görme yetkisi olan **her rol** (`usage.person` ya da
  `usage.anonymous`: Yönetici, Denetçi, İK Raporları, Rapor İzleyici) kişi ve cihaz adlarını,
  MAC/IP adreslerini, ana bilgisayar adlarını ve URL'leri görür; takma adlar yalnızca
  `usage.pseudonymize_anonymous_role` ayarı açıkken ve yalnızca `usage.person` yetkisi olmayan
  roller içindir (aşağıda "Kullanım"). Denetçi 5651 sorgusu yapabilir ve mühür/kanıt paketlerini
  doğrulayıp indirebilir, ama kanıt paketi oluşturamaz ve arama sonuçlarını dışa aktaramaz.
- **İki adımlı doğrulama (TOTP)** Yönetici ve Denetçi için zorunludur (`auth.totp_required_roles`).
- Kişisel veri gören roller — bu sürümde **dört rolün hepsi** — ilk girişte ve her yeni sürümde
  bir kez **KVKK bilgilendirmesini** onaylar; her onay denetim kaydına yazılır. Onay verilmeden
  kişi düzeyinde veri döndüren uç noktalar 403 `privacy_notice_required` yanıtı verir (sunucu
  tarafında zorunlu; `require_privacy_notice`).
- Kullanıcı ekleme, rol değiştirme, parola/TOTP sıfırlama yalnızca Yönetici içindir ve son 5 dakika
  içinde parolanın yeniden girilmesini ister.
- **Lisans sayfası** (Ayarlar → Lisans: istek kodu, lisans dosyası yükleme ve kaldırma, geçmiş)
  `settings.manage` ister, yani yalnızca Yöneticidir; yükleme ve kaldırma parolanın yeniden
  girilmesini de ister. Lisans durumu (`GET /license`: durum, modüller, sınırlar; ayrıntılar
  olmadan) **oturum açmış her kullanıcıya** açıktır: bantlar ve kilitli sayfalar herkese görünür
  ([LISANS.md](LISANS.md)). Lisansta kapalı bir modül, rolü ne olursa olsun kimseye açılmaz;
  rol, modül açıkken kimin göreceğini belirler.

## Summary (EN)

Four roles, one per user. By the owner decision of 2026-10-20 every role that may see usage
(`usage.person` or `usage.anonymous`: admin, auditor, hr and viewer) sees person and device
names, MAC/IP addresses, host names and URLs; pseudonyms only with
`usage.pseudonymize_anonymous_role`, and only for roles without `usage.person`. TOTP is mandatory
for admin and auditor. Roles that see person-level data — in this release all four roles —
accept the KVKK notice once per version; the server refuses person-level endpoints until they
did (`require_privacy_notice`).
User management is admin-only and needs a re-authentication within the last five minutes.
The licence page (Settings → Licence: request code, upload and removal of the licence file,
history) needs `settings.manage` (administrators; upload and removal also a recent
re-authentication); the licence state itself (`GET /license` without its details) is open to every
signed-in user, so everybody sees the banners and the locked pages ([LICENSING.md](LICENSING.md)).
A module that is off in the licence is locked for every role; the role decides who sees it while it
is on.

## The spec 13 table

| Rol / Role | Panel | Arama | 5651 Sorgu | Kişi raporları | Anonim raporlar | Mühür/Kanıt | Ayarlar | Kullanıcılar | Denetim |
|---|---|---|---|---|---|---|---|---|---|
| Yönetici (admin) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Denetçi (auditor) | ✔ | ✔ (okuma) | ✔ | — | ✔ | ✔ (doğrula/indir) | — | — | ✔ |
| İK Raporları (hr) | ✔ (yalnızca kullanım) | — | — | ✔ | ✔ | — | — | — | — |
| Rapor İzleyici (viewer) | ✔ | — | — | — | ✔ | — | — | — | — |

## Permissions per role (as implemented)

| Permission | admin | auditor | hr | viewer | Meaning / column |
|---|---|---|---|---|---|
| `dashboard.view` | ✔ | ✔ | — | ✔ | Panel (all widgets) |
| `dashboard.usage` | ✔ | ✔ | ✔ | ✔ | Panel, usage widgets only (hr: "usage only") |
| `search.read` | ✔ | ✔ | — | — | Arama and live tail, read |
| `search.export` | ✔ | — | — | — | Export search results (auditor: "read") |
| `lookup.run` | ✔ | ✔ | — | — | 5651 lookup incl. the PDF report |
| `usage.person` | ✔ | — | ✔ | — | Person-level usage reports (always with names) |
| `usage.anonymous` | ✔ | ✔ | ✔ | ✔ | Usage reports with names (owner decision 2026-10-20); pseudonyms instead only with `usage.pseudonymize_anonymous_role` for roles without `usage.person` (the spec 13 column "Anonim raporlar") |
| `seal.read`, `seal.verify` | ✔ | ✔ | — | — | Seal calendar, manifests, tokens, verification |
| `seal.manage` | ✔ | — | — | — | "Şimdi mühürle" |
| `evidence.read`, `evidence.download` | ✔ | ✔ | — | — | List and download evidence packages (non-admins: manual packages and those of their own lookups; none with the audit log for a reader who sees usage pseudonyms, "Kullanım" below) |
| `evidence.create` | ✔ | — | — | — | Create evidence packages (owner decision: not for the auditor) |
| `settings.manage` | ✔ | — | — | — | Ayarlar |
| `users.manage` | ✔ | — | — | — | Kullanıcılar (this package: `/api/v1/users`) |
| `audit.read` | ✔ | ✔ | — | — | Denetim kaydı (`/api/v1/audit`, `/api/v1/audit/verify`) |
| `sources.*`, `devices.*`, `alerts.*`, `reports.manage`, `compliance.read`, `system.read` | ✔ | — | — | — | Pages the spec table does not list: admin only until decided |

Independent of the role:

| Rule | Where |
|---|---|
| Second factor (TOTP) required | roles in `auth.totp_required_roles` (default `admin`, `auditor`); others may enrol voluntarily |
| KVKK privacy notice required | roles with `usage.person`, `usage.anonymous`, `lookup.run` or `search.read` — all four roles (admin, auditor, hr, viewer); enforced on every person-level endpoint with `arcilogger_api.auth.privacy.require_privacy_notice` (403 `privacy_notice_required`) |
| Recent re-authentication (5 min) | creating users, changing role/active state/name/e-mail, password and TOTP resets, new recovery codes, voluntary TOTP enrolment, **evidence package creation** (`evidence.create`) and **download** (`evidence.download`, together with the KVKK notice), **search export** (`search.export`, together with the KVKK notice); later: credentials and retention settings (`require_recent_auth`) |
| Own lookups only | administrators see every 5651 lookup; every other role with `lookup.run` (the auditor) sees only its own — another user's lookup answers 404, and evidence packages built from it are hidden as well (owner decision, ASSUMPTIONS D5; `routers.lookup.sees_all_lookups`) |
| Everything blocked while `must_change_password` | except `/auth/me`, `/auth/password`, `/auth/logout` |

## Endpoints of the authentication package

| Endpoint | Access |
|---|---|
| `POST /auth/login`, `GET /auth/setup/status`, `POST /auth/setup/admin` | no session (rate-limited) |
| `POST /auth/totp`, `GET /auth/totp/setup`, `POST /auth/totp/confirm`, `GET /auth/me`, `POST /auth/logout` | also a session that still waits for the second factor (TOTP setup/confirm from a full session: recent re-authentication) |
| `POST /auth/password` | signed in, also while `must_change_password` |
| `POST /auth/reauth`, `POST /auth/privacy-notice`, `POST /auth/recovery-codes` | signed in (recovery codes: recent re-authentication) |
| `GET/POST/PATCH /users…` | `users.manage` (changes: recent re-authentication; unlock: no) |
| `GET /audit` | `audit.read` + accepted KVKK notice (entries hold lookup queries with IP/MAC and result ids) |
| `GET /audit/verify` | `audit.read` (counts and hashes only) |

`api/tests/test_auth_security.py` lists every route of these routers with its access rule and
fails when a new route is added without one.

## Lisans / Licence (`/api/v1/license`)

| Endpoint | Access |
|---|---|
| `GET /license` | any signed-in user (no KVKK notice: no person data; not audited); `details` (licence id, server ids, request code, time source, history) only with `settings.manage` |
| `POST /license`, `DELETE /license` | `settings.manage` + recent re-authentication (audited: `license.install`, `license.remove`, `license.refuse`) |

Lisansta kapalı bir modülün uç noktaları (`/usage`, `/reports`, `/imports`) oturum denetiminden
sonra (oturum yoksa 401), uç noktanın yetki denetiminden önce 403 `module_not_licensed` verir; yetki
tablosu değişmez. / The endpoints of a module that is off in the licence (`/usage`, `/reports`,
`/imports`) answer 403 `module_not_licensed` once the user is signed in (401 without a session),
before the endpoint's own permission check; the permission table does not change
([LICENSING.md](LICENSING.md) section 2.2).

## Cihazlar ve Kişiler / Devices and persons — who sees persons

Kişi kayıtları (Cihazlar ve Kişiler sayfasının kişi listesi; e-posta, sicil no, unvan, notlar),
cihaz listesi ve atama geçmişi (kim, hangi tarihten hangi tarihe, atamayı yapan) kişisel veridir.
Cihazlar ve Kişiler sayfasında bunları bugün yalnızca **Yönetici** görür ve değiştirir
(`devices.read`, `devices.manage`); kişi adı döndüren her uç nokta ayrıca KVKK bilgilendirmesinin
onayını ister. Hangi cihazın hangi kişiye bağlandığını başka roller de görür: `audit.read`
(Yönetici, Denetçi) Denetim Kaydı sayfasında Cihazlar ve Kişiler sayfasından yapılan her atama
değişikliğini görür (`device.assign`: cihazın MAC adresi, önceki ve yeni kişinin numarası,
atamanın başlangıcı, değişikliği yapan kullanıcı; CSV ile içe aktarılan atamalar yalnızca
sayılarla yazılır) ve bu numaraları Kullanım sayfasındaki ve 5651 sorgu sonuçlarındaki kişilerle
eşleştirebilir (Denetçi, takma ad ayarı açıkken yalnızca 5651 sorgu sonuçlarındakilerle);
kullanım görme yetkisi olan her rol (`usage.person` ya da `usage.anonymous`: dört rolün hepsi)
Kullanım sayfasının Kişi sekmesinde dönemde kullanımı olan kişileri (ad, departman) ve her kişinin
dönemde kullandığı cihazları (cihaz adı, MAC, IP) görür (aşağıda "Kullanım"); kişi düzeyindeki
(varsayılan) zamanlanmış raporların Excel dosyası (Cihazlar sayfası) alıcılarına her cihazı
dönemde bağlandığı kişilerle verir; `lookup.run` (Yönetici, Denetçi) bir 5651 sorgusunun
sonucunda (zincirin kişi adımı) o andaki atamayı görür: kişinin adı, departmanı, unvanı ve
atamanın başlangıcı ile bitişi.

The person registry (the persons list of the devices page, with e-mail, staff number, title and
notes), the device list and the assignment history (who, from when to when, assigned by whom) are
personal data. On the devices page only administrators see and change them today (`devices.read`,
`devices.manage`, not in the spec 13 table: open question 5 below); every endpoint that returns a
person's name also needs the accepted KVKK notice. Other roles still see which device was
attributed to which person: `audit.read` (admin, auditor) sees every assignment change on the audit
log page that was made on the devices page (`device.assign`: the device's MAC address, the previous
and the new person's number, the assignment's start, the user who made it; an applied assignment
CSV is logged with counts only) and can match these numbers with the persons of the usage page and
of 5651 results (the auditor, with pseudonyms on, only with those of 5651 results); every role
that may see usage (`usage.person` or `usage.anonymous`: all four roles) sees on the usage page's
person tab the persons with usage in the period (name, department) and the devices each one used
in the period (device name, MAC, IP) ("Kullanım / Usage" below); a person-level (the default)
scheduled report's Excel file (sheet Cihazlar) gives its recipients every device with the persons
it was attributed to in the period; `lookup.run` (admin, auditor) shows in a 5651 result (the
chain's person step) the assignment valid at that moment: the person's name, department and title
and the assignment's start and end.

**FortiGate DHCP, srcmac ve aygıt tanıma (ADR-0016).** Yeni bir izin yoktur. FortiGate DHCP
kayıtlarındaki ana bilgisayar adları ve MAC adresleri (5651 sonuçlarındaki kira adımı, Arama'daki
fg_dhcp satırları, cihaz listesi) mevcut izinlerle okunur: 5651 sonuçlarında `lookup.run`, Arama'da `search.read`, Cihazlar ve Kişiler
sayfasında `devices.read`. `lookup.run` (Yönetici, Denetçi) ile bir 5651 sonucu, kira kaydı
olmayan bir oturumda cihazı ve kişiyi `lookup.srcmac_interfaces` ayarındaki arayüzlerde
FortiGate oturum kaydının srcmac alanından da gösterebilir ("kısmi kanıt", düşük güven). Ham
satırlar olduğu gibi basılır (D21): FortiGate'in aygıt tanıma özelliği açık bir arayüzde her
oturum satırı istemcinin MAC adresini, cihaz adını, işletim sistemini, donanım üreticisini ve
sürümünü, cihaz türünü ve pasif olarak öğrenilen kullanıcı adlarını taşır; 5651 raporu ve kanıt
paketi bu satırları, başka kişilerin aday oturumları da dahil ve `lookup.srcmac_interfaces` boş
olsa da, değiştirmeden basar. `search.read` (Yönetici, Denetçi) aynı alanları Arama'daki ham
satırlarda ve `srcmac:` alanıyla görür. 5651 sorgusu, raporu, ham satır görünümü ve Arama KVKK
bilgilendirmesinin onayını ister ve yanıt vermeden önce denetim kaydına yazılır (`lookup.run`,
`lookup.view`, `lookup.report`, `raw.view`, `search.run`).

**FortiGate DHCP, srcmac and device identification (ADR-0016).** There is no new permission.
FortiGate DHCP host names and MAC addresses (the lease step of 5651 results, fg_dhcp rows in
Search, the device list) are read under the existing permissions: `lookup.run` in 5651 results, `search.read` in Search, `devices.read` on
the devices page. With `lookup.run` (admin, auditor) a 5651 result may also name the device and
the person of a session without a lease through the srcmac field of the FortiGate session record
on the interfaces of `lookup.srcmac_interfaces` ("kısmi kanıt", partial evidence, low
confidence). Raw lines are printed as they are (D21): on an interface with FortiGate device
identification on, every session line carries the client's MAC address, device name, operating
system, hardware vendor and version, device type and passively learned user names; the 5651
report and the evidence package print these lines unchanged, other people's candidate sessions
included and also while `lookup.srcmac_interfaces` is empty. `search.read` (admin, auditor) sees
the same fields in the raw lines of Search and through the field `srcmac:`. The 5651 lookup, its
report, the raw-line view and Search need the accepted KVKK notice and are audited before they
answer (`lookup.run`, `lookup.view`, `lookup.report`, `raw.view`, `search.run`).

| Endpoint | Access |
|---|---|
| `GET /devices`, `/devices/{mac}`, `/devices/export.csv`, `GET /persons…` | `devices.read` + KVKK notice (they name current owners) |
| `PATCH /devices/{mac}`, `POST /devices/assign`, `POST/PATCH /persons…`, `POST /imports/{kind}` | `devices.manage` + KVKK notice |
| `GET /departments`, `GET /vlans`, `GET /imports/{kind}/template.csv` | `devices.read` (no person data) |
| `POST/PATCH /departments…`, `POST/PATCH/DELETE /vlans` | `devices.manage` |

Changes, applied imports and CSV exports are audited with ids, field names and counts, never
with names, e-mails or notes — department and VLAN names included (a VLAN's entry keeps its
subnet, VLAN id, department id and guest flag; docs/MVP-CONTRACTS.md section 11); reading the
lists is not audited (like the lookup list).
`api/tests/test_devices_api.py` lists every route of the area with its rule and checks the
refusal for every other role.

## Kullanım / Usage (`/api/v1/usage`)

Sahibin 2026-10-20 kararı ([ASSUMPTIONS.md](ASSUMPTIONS.md) "Owner 2026-10-20"): kullanım görme
yetkisi olan her rol (`usage.person` ya da `usage.anonymous`: yönetici, denetçi, İK, rapor
izleyici) kişi ve cihaz adlarını görür; takma adlar yalnızca `usage.pseudonymize_anonymous_role`
açıkken, `usage.person` yetkisi olmayan izleyiciler içindir. Bu yüzden bu rollerin hepsi, rapor
izleyici de, kişi düzeyindeki uç noktalardan önce KVKK bilgilendirmesini onaylar.

Owner decision of 2026-10-20: every role that may see usage sees names; pseudonyms only with
`usage.pseudonymize_anonymous_role`, for viewers without `usage.person` ([USAGE.md](USAGE.md)
section 6, docs/MVP-CONTRACTS.md section 12). Therefore all of these roles, the viewer too,
accept the KVKK notice before the person-level endpoints answer ([ASSUMPTIONS.md](ASSUMPTIONS.md)
U17).

| Endpoint | Access |
|---|---|
| `GET /usage/company` | `dashboard.usage` or `usage.person` or `usage.anonymous` (company and department totals) |
| `GET /usage/subjects…`, `/usage/sites…`, `/usage/categories…`, `/usage/export.*` | `usage.person` or `usage.anonymous` + KVKK notice (`view=company` exports like `/usage/company`) |
| `POST /usage/rebuild`, `GET /usage/rebuild/{job_id}` | `settings.manage` |
| `GET /usage/pseudonyms` | `users.manage` (administrators) + KVKK notice |

Viewing one subject (`usage.view`), exports (`usage.export`), rebuild requests (`usage.rebuild`)
and the pseudonym mapping (`usage.pseudonyms`) are audited with ids and counts, never names (the
target is the real subject, e.g. `usage:person:12`, also when the viewer sees pseudonyms).

Bir ayrıntı görüntülemesinin (`usage.view`) ve tek kişinin/cihazın dışa aktarılmasının denetim
kaydındaki hedefi, görüntüleyen takma ad görse de gerçek kişi ya da cihazdır (`usage:person:12`).
Kayıt değiştirilmez; ama Kullanım sayfalarında takma ad gören biri (ayar açıkken Denetçi)
`GET /audit` yanıtında bu hedefi takma ad belirteciyle görür (`usage:p:…`, `target_masked: true`;
anahtar yokken `usage:masked`); özet gerçek hedefi kapsadığı için ona hiçbir girdinin özeti
gönderilmez (`prev_hash`, `hash` ve `/audit/verify` yanıtındaki `last_hash` boş). Denetim kaydını
içeren kanıt paketleri (yalnızca yönetici ekler) girdileri olduğu gibi içerir; bu okuyucunun
listesinde görünmez ve 404 yanıtı verir. Yönetici ve adları görenler gerçek hedefi ve özetleri
görür. Takma adlar Denetçi için yine de zayıftır: `search.read` ve `lookup.run` ile ham
kayıtlardan ve 5651 sorgularından adreslere ve kişilere ulaşabilir; günün mühür manifesti günün
son girdisinin özetini taşır, az kullanım görüntülemesi olan bir günde bir tahmin onunla
sınanabilir.

The entry is not changed, but a reader who sees pseudonyms on the Kullanım pages (the auditor
while the setting is on) gets this target in `GET /audit` as the pseudonym token (`usage:p:…`,
`target_masked: true`; `usage:masked` without the key); as the hash covers the real target, it
gets no entry's hashes (`prev_hash`, `hash` and the `last_hash` of `/audit/verify` empty).
Evidence packages with the audit log (added by an administrator only) hold the entries as they
are; they are left out of this reader's list and answer 404. Administrators and readers who see
names get the real target and the hashes. Pseudonyms remain weak for the auditor: with
`search.read` and `lookup.run` it reaches addresses and persons through the raw logs and 5651
lookups; the day's seal manifest carries the hash of the day's last entry, so on a day with few
usage views a guess can be tested against it.
`api/tests/test_usage_api.py` and `api/tests/test_evidence_report.py` cover the rules.

## Güvenlik ve Panel / Security page and dashboard — who sees what

Uyarıları bugün yalnızca **Yönetici** görür ve durumlarını değiştirir (`alerts.read`,
`alerts.manage`; şartname 13 tablosunda sütunu yok). Panel (`dashboard.view`) her kutu için ayrıca
kendi iznini ister; izni olmayanın kutusu boş gelir: Denetçi log alımı, dünkü mühür, kontör ve saat
kutularını görür, uyarı kutusunu görmez; Rapor İzleyici yalnızca log alımını görür; İK
Raporları'nın Panel'i yalnızca kullanım kutularıdır (`/usage`).

Alerts are administrator-only today (`alerts.read`, `alerts.manage`; not in the spec 13
table). The dashboard (`dashboard.view`) needs each box's own permission as well; a box the user
may not see is `null`:

| Endpoint / box | Access | admin | auditor | hr | viewer |
|---|---|---|---|---|---|
| `GET /alerts`, `/alerts/summary`, `/alerts/{id}` | `alerts.read` | ✔ | — | — | — |
| `POST /alerts/{id}/state`, `/alerts/{id}/notes` | `alerts.manage` | ✔ | — | — | — |
| `GET /dashboard/summary` | `dashboard.view` | ✔ | ✔ | — (403) | ✔ |
| box `ingest` (log intake) | `sources.read` or `dashboard.view` | ✔ | ✔ | — | ✔ |
| boxes `seal`, `credit` | `seal.read` | ✔ | ✔ | — | — |
| box `clock` | `system.read` or `seal.read` | ✔ | ✔ | — | — |
| box `alerts` | `alerts.read` | ✔ | — | — | — |

Every change of an alert's state is audited as `alert.state` (from/to, rule), every note as
`alert.note` with its length only — a note is free text and may name people, so its text never
enters the sealed audit log (docs/MVP-CONTRACTS.md section 14). `api/tests/test_alerts_api.py`
lists every route of the area and checks each role against `ROLE_PERMISSIONS`;
`api/tests/test_dashboard_api.py` checks the boxes per role.

## Ayarlar / Settings — sunucu yönetimi ve uygulama ayarları / browser administration

Sunucu yapılandırması (`/admin/...`, ADR-0014) ve uygulama ayarları (`/settings`) bugün yalnızca
**Yönetici**dedir (`settings.manage`, `sources.manage`, `seal.manage`; şartname 13 tablosunda
"Ayarlar" yalnızca yöneticiye açıktır). Kimlik bilgisi, saklama süresi, saat sunucusu ve güvenlik
ayarlarının değişikliği ayrıca son 5 dakika içinde parolanın yeniden girilmesini ister. Hiçbir
lisans durumu bu uç noktaları kapatmaz. "Şimdi mühürle" ve "Kontör sorgula" `seal.manage` ister;
bu izni olan kişi kendi başlattığı isteği izleyebilir. Çıkış düzenleyicisinin altındaki
"Kayıtlarda görülen NAT adresleri" tablosu (`GET /settings/fortigate/observed`) yalnızca
Yöneticiye açıktır (`settings.manage`); yalnızca sayıları ve güvenlik duvarının kendi NAT
adreslerini, arayüz adlarını gösterir, kişisel veri içermez ve denetim kaydına yazılmaz.

The machine configuration (`/admin/...`, ADR-0014) and the database settings (`/settings`) are
administrator-only today (`settings.manage`, `sources.manage`, `seal.manage`; "Ayarlar" is
administrator-only in the spec 13 table). Changes of credentials, retention, the time servers
and security settings also need a re-authentication within the last 5 minutes. No licence state
closes these endpoints. "Şimdi mühürle" and "Kontör sorgula" need `seal.manage`; a user with it
may follow the request they started. The "NAT addresses seen in the logs" table under the exit
editor (`GET /settings/fortigate/observed`) is administrator-only (`settings.manage`); it shows
counts, the firewall's own NAT addresses and interface names only, holds no personal data and is
not audited.

| Endpoint | Access | admin | auditor | hr | viewer |
|---|---|---|---|---|---|
| `GET /admin/config`, `/admin/status`, `/admin/requests…` | `settings.manage` | ✔ | — | — | — |
| `POST /admin/config/<section>/check`, `PUT /admin/config/general` | `settings.manage` (clock: + re-auth) | ✔ | — | — | — |
| `PUT /admin/config/tsa`, `POST /admin/uploads`, `POST /admin/config/tsa/roots`, `/legal-roots`, `/zamane` | `settings.manage` (+ re-auth; not for the credit threshold and time limit) | ✔ | — | — | — |
| `POST /admin/config/sources[/check]`, `PUT` / `DELETE /admin/config/sources/{name}` | `sources.manage` (switch-off and removal: + re-auth) | ✔ | — | — | — |
| `PUT /admin/config/smtp`, `/replica`, `/retention`, `/backup` (and their checks), `POST` / `DELETE /admin/config/smtp/ca` | `settings.manage` (+ re-auth) | ✔ | — | — | — |
| `POST /admin/actions/tsa-test`, `/replica-test`, `/test-email`, `/lease-rebuild` | `settings.manage` | ✔ | — | — | — |
| `GET /admin/requests/{id}` of one's own seal now / credit query | `seal.manage` | ✔ | — | — | — |
| `DELETE /admin/requests/{id}`, `POST /admin/requests/{id}/revert`, `GET /admin/config/versions` | `settings.manage` (cancel of a held change and revert: + re-auth) | ✔ | — | — | — |
| `GET /admin/diagnostics` | `settings.manage` + re-auth | ✔ | — | — | — |
| `POST /seals/{day}/seal`, `POST /credits/query` | `seal.manage` | ✔ | — | — | — |
| `GET /settings`, `PATCH /settings`, `POST /settings/{key}/reset` | `settings.manage` (+ re-auth for `REAUTH_KEYS`) | ✔ | — | — | — |
| `GET /settings/fortigate/observed` | `settings.manage` (counts and the firewall's own NAT addresses only; not audited; 504 `fortigate_observed_timeout`) | ✔ | — | — | — |

Every request is audited `admin.request` when it is queued and its outcome is imported from the
agent's journal (`admin.applied`, `admin.failed`, …); a refused change is audited
`admin.refused` with its codes only; a setting change is `settings.change` with the user's id
and address (docs/MVP-CONTRACTS.md section 16). `api/tests/test_admin_config.py`,
`test_admin_kamusm.py`, `test_admin_sources.py`, `test_admin_sections.py`,
`test_admin_actions.py`, `test_admin_history.py`, `test_settings_api.py` and
`test_settings_api_observed.py` (the observed endpoint) check the roles;
`tests/test_admin_end_to_end.py` runs the whole channel on Linux.

## Arama, Canlı Akış ve Kaynaklar / Search, live tail and sources

Arama ve Canlı Akış kişisel veri gösterir: **Yönetici** ve **Denetçi** (`search.read`) KVKK
bilgilendirmesini onayladıktan sonra arar, sayfaları ve istatistikleri görür, kayıt ayrıntısını
açar, kendi kayıtlı aramalarını tutar ve canlı akışı izler. Dışa aktarma yalnızca **Yönetici**
içindir (`search.export`) ve son 5 dakika içinde parolanın yeniden girilmesini ister; "Kanıt
paketine aktar" `evidence.create` ister (Yönetici). Kaynaklar sayfası `sources.read` ister
(Yönetici); sayfadaki "Son satırlar" ham satır gösterdiği için ayrıca `search.read` ya da
`lookup.run` ve KVKK onayı ister (ham satır görünümünün kuralı). Kaynaklar sayfasındaki "Kira
kanıtı" kutusu (`GET /sources` yanıtının `lease_evidence` alanı) da `sources.read` ister
(Yönetici); yalnızca sayıları, zamanları, arayüz adlarını ve kayıt biçiminin durumunu taşır
(istemci MAC adresi, IP adresi ya da ana bilgisayar adı yok) ve denetim kaydına yazılmaz; `none`
durumu yalnızca yöneticinin gördüğü uyarılardan (`alerts.read`) okunur. İK ve Rapor
İzleyici bu sayfaları açamaz. Hiçbir lisans durumu bunları kapatmaz (5651 Temel).

Search and the live tail show personal data: **administrators** and **auditors**
(`search.read`) search, see pages and statistics, open a record's details, keep their own saved
searches and watch the live tail once they accepted the KVKK notice. Export is
**administrator**-only (`search.export`) and needs a re-authentication within the last
5 minutes; "Kanıt paketine aktar" (make an evidence package from the lines) needs `evidence.create`
(administrators). The sources page needs `sources.read` (administrators); its "Son satırlar"
(latest lines) also needs `search.read` or `lookup.run` and the KVKK notice, because it shows raw
lines (the rule of the raw line view). The "Kira kanıtı" / "Lease evidence" card of the sources
page (`lease_evidence` of `GET /sources`) needs `sources.read` too (administrators); it holds
counts, times, interface names and the format state only (no client MAC, IP address or host
name) and is not audited; its `none` state is read from the alerts, which only administrators
see (`alerts.read`). HR and viewers cannot open these pages. No licence state closes them
(5651 Core).

| Endpoint | Access | admin | auditor | hr | viewer |
|---|---|---|---|---|---|
| `POST /search`, `POST /search/page`, `POST /search/stats`, `GET /search/event` | `search.read` + KVKK | ✔ | ✔ | — | — |
| `POST /search/parse`, `GET /search/fields` | `search.read` (no database rows) | ✔ | ✔ | — | — |
| `GET` / `POST /search/saved`, `PUT` / `DELETE /search/saved/{id}` | `search.read` + KVKK (own saved searches only; another user's id: 404) | ✔ | ✔ | — | — |
| `POST /search/export` | `search.read` + `search.export` + KVKK + re-auth (5 min) | ✔ | — | — | — |
| `POST /search/refs` | `search.read` + `evidence.create` + KVKK | ✔ | — | — | — |
| `POST /live/streams`, `GET` / `DELETE /live/streams/{id}`, `GET /live/status` | `search.read` (+ KVKK to open a stream; a stream belongs to its user and session) | ✔ | ✔ | — | — |
| `GET /sources`, `GET /sources/{name}` | `sources.read` (parse error texts only with `search.read` or `lookup.run` + KVKK; the `lease_evidence` card: counts only, no client MAC or IP, not audited) | ✔ | — | — | — |
| `GET /sources/{name}/tail` | `sources.read` + (`search.read` or `lookup.run`) + KVKK | ✔ | — | — | — |
| Panel box `parse_failures` | `sources.read` or `dashboard.view` | ✔ | ✔ | — | ✔ |
| Raw line drawer on these pages (`GET /raw`) | `search.read` or `lookup.run` + KVKK | ✔ | ✔ | — | — |

Audited: `search.run`, `search.event`, `search.refs`, `search.export`, `search.saved_*`,
`live.open`, `live.close`, `raw.tail`, `raw.view`; not audited: the next pages and the
statistics of an audited search, the query check and the field list ([SEARCH.md](SEARCH.md)
section 11), and the sources list with its `lease_evidence` card. `api/tests/test_search_permissions.py` runs every search endpoint with every role,
without the notice, without a session, with a pending password change and in every licence
state.

## Kararlar / Decisions

- **Denetçi ve kanıt paketi** (karar 2026-10-12, [ASSUMPTIONS.md](ASSUMPTIONS.md) D4): şartname 13
  denetçiye "Mühür/Kanıt ✔ (doğrula/indir)" verir, 9.5 ve 12.3 ise "Kanıt Paketi" düğmesini 5651
  sorgu sayfasına koyar. Denetçi kanıt paketi **oluşturamaz**; web arayüzü düğmeyi yalnızca
  `evidence.create` ile gösterir. Denetçinin kendi sorguları için oluşturma izni istenirse
  değiştirilecek.
- **Sorguların görünürlüğü** (ASSUMPTIONS D5): yönetici olmayan roller yalnızca kendi 5651
  sorgularını ve bunlardan oluşturulan kanıt paketlerini görür.

## Açık sorular / Open questions (owner decision needed)

1. **Denetçi ve kanıt paketi:** karar verildi (2026-10-12) — yukarıdaki "Kararlar" bölümü
   ([ASSUMPTIONS.md](ASSUMPTIONS.md) D4).
2. **İK için TOTP:** İK kişi bazlı tarama raporlarını görür; şartname TOTP'yi yalnızca yönetici ve
   denetçi için zorunlu tutar. Öneri: `auth.totp_required_roles` ayarına `hr` eklensin
   (yönetici PowerShell'inde; kurulum programı `venv\Scripts` klasörünü PATH'e eklemediği için tam
   yol gerekir: `'["admin","auditor","hr"]' | & "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-db.exe" settings set
   auth.totp_required_roles - --by AD`). Ayar yalnızca rol ekler: `admin` ve `auditor` listeden
   çıkarılamaz (komut bunu reddeder).
3. **KVKK bilgilendirmesi kimler için?** Şartname 3.3 "her yönetici kullanıcıya" der. Uygulama,
   kişi düzeyinde veri gören tüm rollere gösterir; sahibin 2026-10-20 kararından beri rapor
   izleyici de adları gördüğü için bu dört rolün hepsidir (yönetici, denetçi, İK, rapor izleyici).
   Metnin kendisi (web arayüzü, i18n) hukuk müşaviri onayından geçmelidir; sürüm
   `privacy.notice_version` ayarıdır.
4. **Rapor İzleyici ve tam panel:** şartname panel için ✔ verir; panelde güvenlik uyarıları ve mühür
   durumu da var. İzleyici bunları görsün mü, yoksa yalnızca kullanım kutularını mı?
5. **Tabloda olmayan sayfalar** (Kaynaklar, Cihazlar ve Kişiler, Güvenlik, Raporlar, Uyum Durumu,
   Sistem): şimdilik yalnızca yönetici. Denetçi örneğin "Uyum Durumu"nu görmeli mi?
6. **Geçici parola süresi:** yöneticinin oluşturduğu geçici parolanın bir son kullanma süresi yok
   (ilk girişte değiştirilmesi zorunlu). Süre sınırı (ör. 72 saat) istenir mi?
7. **Denetim kaydını okumak** kendisi denetlenmez (her sayfa açılışında kayıt üretirdi). İstenirse
   `audit.view` olarak eklenebilir.
