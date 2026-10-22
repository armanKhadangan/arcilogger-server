# ARCILOGGER güvenliği / Security of ARCILOGGER

> Kapsam: kimlik doğrulama, oturumlar, CSRF, hesap kilitleme, kullanıcı yönetimi ve denetim kaydı
> (şartname 3.3, 8.5, 13, 16, 17). Rol tablosu: [roles.md](roles.md). Kod:
> `api/src/arcilogger_api/{security,audit}.py`, `api/src/arcilogger_api/auth/`,
> `api/src/arcilogger_api/routers/{auth,users,audit}.py`.
> ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır; KVKK ve 5651 açısından
> uygunluk değerlendirmesi için hukuk müşaviri görüşü alınmalıdır.

## Özet (TR)

- Parolalar **Argon2id** ile saklanır (RFC 9106'nın ikinci önerisi: 64 MiB, 3 tur, 4 şerit).
  En az 12 karakter; yaygın/sızdırılmış parolalar (pakete gömülü liste, `P@ssw0rd` gibi
  harf-rakam oyunları dahil), "şifre", "yönetici", takım, şehir ve yaygın ad gibi Türkçe kelimelere
  rakam eklenmiş parolalar (`Galatasaray1905`), tekrarlı/ardışık karakterler, kullanıcı adı, ad,
  şirket ve ürün adı kabul edilmez.
- Yönetici ve denetçi için **iki adımlı doğrulama (TOTP)** zorunludur; ilk girişte QR kodla kurulur,
  10 tek kullanımlık **kurtarma kodu** bir kez gösterilir. TOTP gizli anahtarı veritabanında
  **şifreli** durur (`secret.key`). Aynı kod ikinci kez kabul edilmez.
- 5 hatalı denemede hesap **15 dakika kilitlenir**; başarılı bir giriş olmadan tekrarlanan her
  kilitte süre iki katına çıkar (en fazla 24 saat). Ayrıca adres başına deneme sınırı vardır.
  Bilinmeyen kullanıcı ile yanlış parola **aynı yanıtı** alır.
- Oturum çerezi HttpOnly + SameSite=Strict + Secure; veritabanında yalnızca SHA-256 özeti tutulur.
  15 dakika hareketsizlik veya 12 saat sonunda oturum kapanır. TOTP adımından ve parola
  değişikliğinden sonra **yeni oturum anahtarı** verilir; parola değişince diğer oturumlar kapanır.
- Her değiştirici istek **CSRF** anahtarı ister. Hassas işlemler (kullanıcı yönetimi, isteğe bağlı
  TOTP kurulumu, kanıt paketi oluşturma ve indirme; ileride kimlik bilgisi ve saklama ayarları)
  son 5 dakika içinde **parolanın yeniden girilmesini** ister.
- Kişisel veri gören roller **KVKK bilgilendirmesini** onaylamadan kişi düzeyinde veri döndüren
  uç noktalara erişemez (sunucu tarafında zorunlu, `require_privacy_notice`).
- Giriş, hatalı giriş, kilitleme, TOTP hataları, çıkış, parola ve kullanıcı değişiklikleri
  **zincirlenmiş denetim kaydına** yazılır; parolalar, kodlar ve gizli anahtarlar asla yazılmaz.
- İlk yönetici, sunucu konsolunda (yönetici PowerShell'i) üretilen **tek kullanımlık kurulum
  anahtarı** ile oluşturulur:
  `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"`
  (program `PATH` üzerinde değildir; [KURULUM.md](KURULUM.md) bölüm 9). Kurtarma için aynı
  programın `create-admin` komutu.
- **Lisans** ([LISANS.md](LISANS.md)): depoda gizli bilgi yoktur — kodda yalnızca lisans verenin
  açık anahtarları ve iptal edilmiş lisans numaraları bulunur; imzalama anahtarı lisans verende,
  çevrimdışıdır. Lisans hiçbir zaman dışarıya bağlanmaz, telemetri yoktur. Hizmetler ve görevler
  Python'u yalıtılmış çalıştırır (`python -I`). Lisanslamanın çevrimdışı koruyamadıkları
  (sunucu kimliğinin taklidi, sıfırdan kurulum, kararlı bir yöneticinin kodu değiştirmesi …)
  açıkça yazılıdır (bölüm 13; [LISANS.md](LISANS.md) bölüm 11).

## Summary (EN)

Argon2id passwords with a length and blocklist policy (leetspeak and Turkish base words
included); TOTP (mandatory for admin and auditor) with replay protection, encrypted secrets and
hashed single-use recovery codes; progressive per-account lockout plus per-IP rate limits and
uniform failure responses; hashed, rotated, revocable session tokens in strict cookies; CSRF on
every mutation; re-authentication for sensitive actions; the KVKK notice enforced by the server
on person-level endpoints; every security event in the hash-chained audit log, never with a
secret; a one-time console token for the first administrator. Offline licensing (section 13):
only public keys and revoked licence ids in the code, no telemetry, services and tasks on isolated
Python (`python -I`), and a plain list of what an offline licence check cannot prevent.

## 1. Sign-in flow

All endpoints are under `/api/v1/auth`. Every answer of the flow contains `next`, which tells the
web app what to show:

| `next` | Meaning | Next call |
|---|---|---|
| `totp` | password accepted, TOTP enrolled | `POST /auth/totp` `{code}` (6 digits or a recovery code) |
| `totp_setup` | password accepted, TOTP required for the role but not enrolled | `GET /auth/totp/setup`, then `POST /auth/totp/confirm` `{code}` |
| (voluntary) | a full session enrols TOTP on its own initiative | the same two calls; both need a recent re-authentication (403 `reauth_required`) |
| `password_change` | signed in, `must_change_password` | `POST /auth/password` `{current_password, new_password}` |
| `privacy_notice` | the role sees person-level data and has not accepted the current KVKK notice (person-level endpoints answer 403 `privacy_notice_required` until then, section 7a) | `POST /auth/privacy-notice` `{version}` |
| `done` | nothing pending | — |

1. `POST /auth/login` `{username, password}` creates a session. Without TOTP (not enrolled and not
   required for the role) it is complete; otherwise it waits for the second factor
   (`user_sessions.mfa_passed = false`) and reaches only `/auth/totp*`, `/auth/me` and
   `/auth/logout`. A session cookie the browser still holds is revoked.
2. The TOTP step (or the first enrolment) **rotates** the session: new session and CSRF tokens,
   the old row revoked. The rotated session keeps the original start, so the absolute lifetime
   (`auth.absolute_hours`) counts from the password step.
3. While `must_change_password` is set (temporary passwords), every other endpoint answers 403
   `password_change_required`, except `/auth/me`, `/auth/password` and `/auth/logout`.
4. `GET /auth/me` returns the user, role with Turkish/English label, permissions (empty until the
   second factor passed), `must_change_password`, TOTP status (`passed`, `enabled`, `required`,
   `recovery_codes_left`), the privacy notice status, `idle_timeout_minutes`, the session's
   start/absolute end/last re-authentication, `next` and `site_name` (the organisation name of
   `arcilogger.toml`, `""` while it is not set; the sidebar shows it). It also answers a session
   that waits for TOTP, so a reloaded sign-in page knows which step to show.
5. `POST /auth/logout` revokes the session and deletes both cookies (204).

## 2. Passwords

**Hashing:** Argon2id, `t = 3`, `m = 65536 KiB` (64 MiB), `p = 4`, 16-byte salt, 32-byte hash —
RFC 9106 section 4, second recommended option (for systems where 2 GiB per hash is too much).
On the 4-core production server one hash takes about 50–100 ms; with at most ten users, the rate
limits and the lockout this cannot exhaust CPU or memory. The parameters are written out in
`auth/passwords.py` (never the library default). After a successful password check a hash with
other parameters is replaced (rehash on sign-in and re-authentication), so raising the parameters
later needs no migration.

**Policy** (NIST SP 800-63B style; no composition rules, no periodic change):

| Rule | Detail |
|---|---|
| Length | at least `max(12, auth.password_min_length)` characters (the setting can raise, never lower, the spec's 12; a setting above 128 counts as 128, so a typo cannot make every password — the generated temporary ones included — unacceptable), at most 256 |
| Common / breached | not on the bundled list, also not a listed word with digits/punctuation before or after (`Sunshine2026!!`), also not in leetspeak (`P@ssw0rd2026!`, `4dmin2026`, `L1verp00l1905`: `@`/`4` = a, `0` = o, `1`/`!` = i or l, `3` = e, `$`/`5` = s, `7` = t) |
| Turkish base words | not one of the hand-written words of `auth/data/common-passwords-tr.txt` (*şifre*, *parola*, *yönetici*, football clubs, the 81 provinces, common given and family names …) with only digits/punctuation around it (`Galatasaray1905`, `Şifre123456!`, `Y0n3t1c1.2026`), and not only such words with digits/punctuation between them (`Ankara.06.Ankara`) |
| Repetition / sequences | at least 5 different characters; not a run of the alphabet, digits or a keyboard row (also Turkish Q) and not a repeated chunk (`abc123abc123`) |
| Personal | must not contain the user name, a word (≥ 4 letters) of the display name, the organisation name (`site_name` when set, e.g. "Örnek A.Ş." → `ornek`) or `arcilogger`, also in leetspeak (`0rn3k`) |
| Change | the new password must differ from the current one |

Comparisons ignore case and Turkish diacritics (`Ş` = `s`, `ı`/`İ` = `i`). A refused password is a
422 `password_rejected`; `details` lists every broken rule with Turkish and English text, never the
password.

**Bundled list** (`auth/data/common-passwords.txt.gz`, 56,138 entries, ~350 KB): built from
[SecLists](https://github.com/danielmiessler/SecLists) (**MIT licence**, © 2018 Daniel Miessler):
all entries of `Passwords/Common-Credentials/10k-most-common.txt` and the entries of 12 or more
characters of `Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt`. The file
header and `auth/data/common-passwords.NOTICE.txt` carry the licence text and the SHA-256 of both
sources. The list is English-centric. **To replace or extend it** (e.g. with Turkish passwords or a
larger breach corpus whose licence permits redistribution — *verify the licence*):
`python -m arcilogger_api.auth.wordlist build --out common-passwords.txt.gz --notice NOTICE.txt
--source list1.txt:0 --source list2.txt:12` and copy the result over the shipped file.

**Turkish base words** (`auth/data/common-passwords-tr.txt`, 239 words): written by hand for
ARCILOGGER (part of the project, no third-party source, so no licence question). The choice is a
judgement, not a frequency study — **verify on site** and add words typical for the company
(product, department and place names). It is plain UTF-8 text, one word per line, edited
directly; `python -m arcilogger_api.auth.wordlist info` shows how many words are loaded.

**Temporary passwords** (new user, reset by an administrator) are random groups of four
characters (`xxxx-xxxx-xxxx-xxxx`, ~92 bits, no look-alike characters) — more groups when
`auth.password_min_length` requires a longer password — shown once to the administrator and must
be changed at the first sign-in. A generated password must pass the policy; after 100 refused
candidates (practically impossible) the request fails with 500 instead of looping.

## 3. Two-factor authentication (TOTP)

| Item | Value |
|---|---|
| Algorithm | RFC 6238 TOTP, HMAC-SHA1, 6 digits, 30-second steps (what every authenticator app supports); tested against the RFC 6238 appendix B values |
| Clock tolerance | current step ±1 (±30 s); the server clock must follow NTP (`[clock] ntp_server`, else `[clock] fallback_server`) |
| Input | exactly six ASCII digits (`0-9`; other scripts' digits such as `١٢٣٤٥٦` or full-width digits are no code: they count as a failed attempt like any wrong code) |
| Replay | the last accepted step is stored (`users.totp_last_counter`); that step and older ones are refused, also for re-authentication; concurrent requests are serialised on the user row |
| Secret | 160 random bits, encrypted with Fernet under `<secrets_dir>\secret.key` (the key of the existing secret store) in `users.totp_secret_encrypted`; shown once during enrolment (QR code as SVG from `segno`, `otpauth://` URI, text) and never logged or audited |
| Mandatory for | roles in `auth.totp_required_roles` (default `admin`, `auditor`); other users may enrol voluntarily from a full session — that needs a recent re-authentication for both `GET /auth/totp/setup` and `POST /auth/totp/confirm`, so an unattended or stolen session cannot bind its own authenticator app and lock the owner out |
| Enrolment as a GET | `GET /auth/totp/setup` stores the pending (still disabled) secret on its first call, as the contract defines it. It is idempotent, enables nothing (only the CSRF-protected `POST /auth/totp/confirm` does) and the SameSite=Strict session cookie is never sent with a cross-site request |
| Recovery codes | 10 codes of 80 random bits (`xxxx-xxxx-xxxx-xxxx`, Crockford base32), shown once; stored as `sha256("arcilogger-recovery-v1:<user id>:<code>")`; each works once in place of a TOTP code at sign-in; `POST /auth/recovery-codes` (recent re-authentication) replaces them |
| Lost phone | an administrator resets the second factor (`POST /users/{id}/reset-totp`); the user enrols again at the next sign-in |

**Installer (B7):** the API's service account (`NT SERVICE\ArciApi`) must be able to **read**
`C:\ProgramData\ARCILOGGER\secret.key`. It needs that already for the database password; the API
never creates the key (`arcilogger-core secret init` does). If the key file is missing or replaced by a different key, TOTP enrolment
and sign-in with TOTP answer 503 `secret_key_missing` (no secret is revealed). Restoring a server
therefore needs the original `secret.key` together with the database backup — otherwise every
user must enrol TOTP again (administrator: `reset-totp`).

## 4. Sessions and cookies

- `arci_session`: 256-bit random token, **HttpOnly, SameSite=Strict, Secure** (`[api]
  secure_cookies`), browser-session cookie. The database stores only its SHA-256
  (`user_sessions.token_hash`).
- A session ends when revoked, after `auth.idle_minutes` (15) without a user request, or
  `auth.absolute_hours` (12) after the sign-in. Settings are read on every request.
- Requests the web app makes on its own send `X-Arci-Background: 1`; they do not count as activity.
- Activity (`last_seen_at`) is written by the authentication dependency on the **request's own
  database session and committed at once**, before the endpoint runs:
  - kept until the end of the request, the update would hold the session row lock, and revoking
    that user's sessions (role change, password or TOTP reset) could deadlock with one of the
    user's own requests (found by `test_admins_demoting_each_other_at_once_leave_one_admin`);
  - done on a second connection instead (the first B5 version), every authenticated request held
    two pooled connections at once; with about as many concurrent requests as the pool has
    connections, each held one and waited for a second, and most failed with 500 after
    `pool_timeout` (found in review; tests
    `test_a_request_never_holds_two_pooled_connections` with a pool of one connection and
    `test_concurrent_requests_do_not_exhaust_the_connection_pool` with 32 requests on 5 + 10).
  - Consequence: the endpoint's queries run in a new transaction after that commit, and a request
    that is refused later (e.g. 403) still counts as activity.
- Sessions are revoked: at logout; all of a user's sessions when the password is changed (a new one
  is issued to the browser that changed it), when an administrator changes the role or active
  state, resets the password or the TOTP; the pending session when the account gets locked during
  the TOTP step, a password change or a re-authentication.

## 5. CSRF

Double submit bound to the session (`security.py`): every `POST/PUT/PATCH/DELETE` under `/api/`
needs the header `X-CSRF-Token` equal to the cookie `arci_csrf` (readable by the web app, issued
to any client without one); for a signed-in request its SHA-256 must also equal
`user_sessions.csrf_token_hash`. A new CSRF token comes with every new session (sign-in, TOTP step,
password change). `api/tests/test_auth_security.py` checks every mutating route.

## 6. Lockout, rate limits, uniform failures

- **Per account, progressive:** wrong passwords (sign-in, re-authentication, password change) and
  wrong TOTP or recovery codes count; every `auth.lockout_attempts` (5) failures lock the account,
  the first time for `auth.lockout_minutes` (15), each further time **twice as long** (30, 60 …
  minutes, at most 24 hours, or `auth.lockout_minutes` when that is longer). The count goes on
  after a lock expired; only a complete sign-in (password and, where enrolled, TOTP), a
  successful re-authentication or password change (both need a complete session) or an
  administrator (unlock, password reset) resets it. While locked, even the right password fails
  and is not counted. Administrators can unlock (`POST /users/{id}/unlock`).
- **Why progressive:** someone who knows the password but not the phone can guess TOTP codes.
  With a fixed 15-minute lock that is 480 guesses a day (each hits about 3 of the 10⁶ codes,
  current step ±1): about 41 % success in a year. With doubling locks it is about 1 900 guesses a
  year (≈ 0.6 %), and every lock is an `auth.lockout` audit entry with `lock` = how many locks
  since the last complete sign-in. **Recommended:** alert on repeated `auth.lockout` entries of
  one account (`lock` ≥ 2) — nothing raises such an alert yet (alerts package).
- **Per address** (in memory, `auth/ratelimit.py`): 20 requests per 5 minutes for login, TOTP,
  and re-authentication/password change each, 10 for the setup endpoint; then 429 `rate_limited`
  with `Retry-After`. Only the first refusal per address and window is audited.
- **Uniform failures:** unknown user, wrong password, locked and disabled accounts all get the same
  401 `login_failed` text. An unknown user name costs one Argon2 verification against a dummy hash
  (also an account without a usable hash), so the timing does not reveal which names exist.

## 7. Re-authentication for sensitive actions

`arcilogger_api.security.require_recent_auth(*permissions, minutes=5)` — a dependency like
`require(...)` that also needs a password (+ TOTP) confirmation within `minutes`:

```python
from arcilogger_api.security import CurrentUser, require_recent_auth


@router.get("/{package_id}/download")
def download(
    package_id: int,
    user: Annotated[CurrentUser, Depends(require_recent_auth("evidence.download"))],
) -> FileResponse: ...
```

Otherwise 403 `reauth_required`; the web app asks for the password (and a TOTP code when enrolled),
calls `POST /auth/reauth` `{password, code}` and repeats the request. A complete sign-in counts as a
re-authentication. Used today for every user-management change, new recovery codes, voluntary
TOTP enrolment, evidence package creation (`evidence.create`) and evidence download
(`evidence.download`, combined with the KVKK notice through `require_privacy_notice(...,
recent=True)`). To be used (spec 13) for the credential settings (Kamu SM, replica, SMTP) and the
retention settings when their pages exist.

## 7a. KVKK notice on person-level endpoints

`arcilogger_api.auth.privacy.require_privacy_notice(*permissions, recent=False)` — like
`require(...)` (or `require_recent_auth(...)` with `recent=True`), and the user accepted the
current KVKK notice version (`privacy.notice_version`) when the role sees person-level data
(`usage.person`, `usage.anonymous`, `lookup.run` or `search.read`: all four roles — admin,
auditor, hr and viewer, which sees names since the owner decision of 2026-10-20). Otherwise 403
`privacy_notice_required`; the web app shows the notice, calls `POST /auth/privacy-notice` and
repeats the request. Permissions are checked first (403 `forbidden`). **Every endpoint that
returns person-level data must use it** (search, live tail, 5651 lookup and its report,
person-level usage reports), so neither an API client nor a bug in the web app can skip the
notice:

```python
from arcilogger_api.auth.privacy import require_privacy_notice


@router.post("")
def run_lookup(
    body: LookupRequest,
    user: Annotated[CurrentUser, Depends(require_privacy_notice("lookup.run"))],
) -> LookupResult: ...
```

## 8. Status codes and error codes

Every error is `{"error": {"code", "message_tr", "message_en"}}`. **401** = no usable session (the
web app shows the sign-in screen) or a sign-in step failed; **403** = signed in, but refused.

| Status | Code | When |
|---|---|---|
| 401 | `not_authenticated`, `session_invalid`, `session_expired`, `session_idle`, `session_revoked`, `user_inactive` | session missing or ended |
| 401 | `mfa_required` | the session still waits for TOTP |
| 401 | `login_failed` | sign-in refused (any reason) |
| 401 | `totp_invalid` | wrong/used code in the TOTP step |
| 401 | `account_locked` | the account was locked by this failure; the session was closed |
| 400 | `totp_invalid` | wrong code when confirming the enrolment |
| 403 | `forbidden`, `csrf_failed`, `password_change_required`, `reauth_required`, `privacy_notice_required` | refused |
| 403 | `credentials_invalid` | wrong password/code for a password change or re-authentication |
| 403 | `setup_token_invalid` | wrong, used or expired setup token |
| 409 | `mfa_already_passed`, `totp_not_enabled`, `totp_already_enabled`, `totp_setup_missing`, `recovery_codes_unavailable`, `notice_outdated`, `setup_done`, `username_taken`, `last_admin`, `cannot_change_self` | state conflicts |
| 422 | `password_rejected`, `username_invalid`, `validation_error` | input refused (never echoed) |
| 429 | `rate_limited` | too many attempts from this address |
| 503 | `secret_key_missing`, `unavailable` | key file or database not available |

## 9. First run and recovery

1. On the server console, in a PowerShell **run as Administrator** (the configuration,
   `secret.key` and `spool\_audit` are readable or writable only by administrators and the
   services), by its full path — the program is not on `PATH`:
   `& "C:\Program Files\ARCILOGGER\venv\Scripts\arcilogger-api.exe" setup-token --config "C:\ProgramData\ARCILOGGER\arcilogger.toml"`.
   It prints a 256-bit token, valid 24 hours; only its SHA-256 is stored (setting
   `setup.admin_token`, an internal key written by `:system`). A new token replaces the previous
   one. Refused once any administrator exists. Operator steps: KURULUM.md / INSTALL.md section 9.
2. The web wizard asks `GET /auth/setup/status` (`needs_setup`, `token_active`) and sends
   `POST /auth/setup/admin` `{token, username, display_name, password}` (rate-limited). The status
   answer, which needs no session, also carries `idle_timeout_minutes` (setting
   `auth.idle_minutes`, shown on the sign-in page, spec 12.3) and nothing else. The token
   is checked under a row lock and deleted with the new account, so it works once. The first
   administrator keeps the chosen password and enrols TOTP at the first sign-in.
3. **Recovery** (every administrator lost phone and recovery codes): `arcilogger-api.exe
   create-admin` (same full path and console) asks for user name, display name and password
   (hidden, twice) and creates
   another administrator, who enrols TOTP at the first sign-in and resets the others. Whoever can
   run it already controls the server (console, `secret.key`, database password). It respects the
   licence's users limit ([LICENSING.md](LICENSING.md) section 6): when the limit is full it refuses
   (exit 1, the `license_limit` message, audited `license.limit_refused` with `"operation":
   "create_admin"`) unless no administrator can sign in (every administrator inactive or locked:
   true recovery; its `user.create` entry then carries `"license_over_limit": true`), or
   `--deactivate USERNAME` deactivates an active user in the same transaction (net zero; audited as
   `user.update` with `"via": "create_admin"`). A broken licence component never refuses it.

Both commands write an audit entry with the Windows account that ran them (`os_user`). Exit codes:
0 done, 1 refused, 2 usage/configuration error, 3 database unreachable.

## 10. User management

`/api/v1/users` (`users.manage`): list (sorted by user name, cursor = last name, filters `role`,
`active`), read, create (`{username, display_name, email?, role}` → temporary password),
`PATCH` (`display_name`, `email`, `role`, `active`), `reset-password`, `reset-totp`, `unlock`.
Changes need a recent re-authentication (unlock does not). Creating a user and re-enabling an
inactive one are refused with 409 `license_limit` when the active users reach the licence's limit
(section 13). Nobody changes their own role or active
state or resets their own password/TOTP here; the last active administrator can be neither demoted
nor disabled. Changes that depend on the set of administrators run under an advisory lock, so two
administrators demoting each other at the same moment leave one (tested with two concurrent
requests). Users are never deleted (audit entries refer to them); they are deactivated.

## 11. Audit log

Hash chain, spool and sealing: see `api/src/arcilogger_api/audit.py` and
[DATA-MODEL.md](DATA-MODEL.md). `GET /api/v1/audit` (`audit.read`) lists entries newest first
(filters: `from` inclusive / `to` exclusive with UTC offset, `actor` = user name, `action` = prefix,
`target` = exact; `cursor` = `next_cursor`, `limit` ≤ 500). `GET /api/v1/audit/verify` recomputes
the whole chain (`ok`, `entries`, `first_bad_id`, `last_hash`, first 20 errors). Reading the log is
not itself audited. A reader who sees usage pseudonyms (the auditor while
`usage.pseudonymize_anonymous_role` is on) gets the targets `usage:<real subject key>` as the
subject's pseudonym token (`target_masked: true`; [USAGE.md](USAGE.md) section 6). The stored
entry and its hash are unchanged; as a hash would let a guess of the real target be tested, such
a reader gets `prev_hash`, `hash` and `/audit/verify`'s `last_hash` as `null` (the server still
verifies the chain), and evidence packages with the audit log are hidden from it.

Entries of this package (`actor` `system` = no signed-in user; `target` `user:<id>`):

| Action | Details (never passwords, codes, tokens or secrets) |
|---|---|
| `auth.login` | `session` (first 12 hex of the token hash), `next`, `rehashed` |
| `auth.login_failed` | `reason`: `unknown_user` (the typed name is **not** stored — it may be a password typed into the wrong field, and the log is permanent), `bad_password`, `locked`, `inactive`; `username` for existing accounts |
| `auth.lockout` | `username`, `attempts` (since the last complete sign-in), `minutes` (of this lock), `lock` (1 = first lock since the last complete sign-in), `step` (`password`, `totp`, `reauth`, `password_change`) |
| `auth.rate_limited` | `bucket` (first refusal per address and window) |
| `auth.totp` / `auth.totp_failed` | `method` (`totp`, `recovery_code`), `session` / `reason` (`invalid`, `replay`, `locked`), `step` (`sign_in`, `enrolment`); `recovery_codes_left` after a recovery code |
| `auth.totp_setup`, `auth.totp_enrol`, `auth.recovery_codes_regenerate` | number of recovery codes, `session` |
| `auth.password_change` / `auth.password_change_failed` | `forced`, `other_sessions_revoked`, `session` / `reason` |
| `auth.reauth` / `auth.reauth_failed` | `session` / `reason` (`bad_password`, `bad_code`, `replay`, `locked`) |
| `auth.logout` | `session` |
| `privacy.accept` | `version` (KVKK notice, spec 3.3; once per user and version) |
| `setup.token_create`, `setup.admin_create`, `setup.admin_failed` | `expires_at`, `os_user` / `username`, `role` / `reason` (`missing`, `expired`, `mismatch`) |
| `user.create` | `username`, `role`, `active` (console: `via: cli`, `os_user`) |
| `user.update` | `changes` `{field: {old, new}}`, `sessions_revoked` |
| `user.password_reset`, `user.totp_reset`, `user.unlock` | `sessions_revoked`, `was_enabled`, `was_locked` |

Licence entries (section 13; target `license`, `license:<license_id>` or `user:<username>`; CLI
entries add `os_user`; never the licensee name or server ids):

| Action | Details |
|---|---|
| `license.trial_start` | `installation_id`, `trial_started_at`, `backdated`, `recreated` |
| `license.install` | `license_id`, `sha256`, `payload_sha256`, `key_id`, `modules`, `limits`, `maintenance_until`, `valid_until`, `unchanged`, `replaced_license_id` |
| `license.remove` | `license_id`, `sha256` |
| `license.refuse` | `code`, `sha256`, `size`, `license_id`, `key_id` |
| `license.limit_refused` | `limit`, `max`, `used`, `username`, `operation` (`create`, `reactivate`, `create_admin`) |
| `license.limit_exceeded`, `license.limit_ok` | `limit`, `max`, `used` |
| `license.state` | `from`, `to`, `enabled_from`, `enabled_to`, `problem` |

## 12. Residual risks and verify-on-site items

| Topic | Risk / note |
|---|---|
| First TOTP enrolment | whoever signs in first with a new temporary password enrols the second factor. Hand temporary passwords over in person; temporary passwords do not expire yet (open question in roles.md) |
| Shared address | the server firewall admits only the administration PC(s); all users may share one address, so the per-IP limits are shared (generous defaults) and an attacker on that PC can lock accounts on purpose (administrators unlock) |
| In-memory rate limits | reset when the API restarts; correct only with one API process (the Windows service runs one Uvicorn worker) — *verify* if workers are ever added |
| `secret.key` | whoever reads it and the database can decrypt TOTP secrets (and the stored credentials): NTFS ACL for administrators, the ARCILOGGER services and the scheduled tasks — which run as Windows' shared LOCAL SERVICE account, so every process running as LocalService can read it too (open owner decision, ADR-0007 section 8); backups of the database and of the key must not travel together unencrypted |
| Database dumps | `D:\ARCILOGGER\backups\scheduled` holds the nightly dumps: password hashes, TOTP secrets encrypted with `secret.key`, personal data — no stored credential (those are in `secrets.json`). Protected folder: administrators and the scheduled tasks only, no ARCILOGGER service; like `secret.key`, every process running as LocalService can read it (ADR-0007 section 8) — and can replace a dump together with its sidecar: the sidecar check detects accidental damage only, and `pg_restore` runs the archive's SQL with the PostgreSQL superuser's rights. Restore a copy whose SHA-256 was recorded off the server when it was copied (`backup restore --sha256`, BACKUP.md sections 4 and 5). The superuser password of a restore is read from stdin, the database password reaches `pg_dump` only in a temporary password file ([ADR-0009](adr/0009-database-backup.md)). Copies off the server need an encrypted medium; `secret.key` never travels with them |
| Database owner | the table owner can disable the audit trigger and rewrite the chain consistently; the database chain alone does not prove integrity against the database administrator. The daily audit spool is sealed with the Kamu SM time stamp — compare `/audit/verify`'s `last_hash` with the sealed manifests (auditor guide, later; a reader who sees usage pseudonyms gets no `last_hash`) |
| Server clock | TOTP and session expiry depend on the server time (NTP: `[clock] ntp_server`, else `[clock] fallback_server`; `arcilogger-core clock-check`) |
| Word list | the SecLists list is English-centric; the hand-written Turkish base words and the leetspeak rule cover the most obvious Turkish passwords, not combinations of several words or a Turkish breach corpus — extend `common-passwords-tr.txt` on site (own words: no licence check; third-party lists: licence check) |
| TOTP guessing | with a known password, the progressive lockout still allows about 1 900 guesses a year (≈ 0.6 % success); an alert on repeated `auth.lockout` entries is not built yet; a changed password ends it |
| Audit spool flush | resolved: `audit.py` appends committed entries to the spool in `after_transaction_end`, after the request's connection is back in the pool, so an audited request never holds two pooled connections (`api/tests/test_audit.py::test_audited_request_with_a_pool_of_one_connection`). A flush error is still logged and caught up by the periodic flush |
| XSS | an injected script could act within a session (cookies are HttpOnly, so it cannot steal them): Caddy's CSP without inline scripts is essential (installer) |
| Session rows | revoked and expired sessions stay in `user_sessions` (no clean-up job yet) |
| Unknown user names | not stored on failure: a password-spraying attempt shows as many `unknown_user` failures from one address, not as the names tried |
| Syslog sender identity | UDP syslog is unauthenticated and the device is identified only by the sender IP: a forged UDP line from the LAN is stored, sealed and used by the lookup (one forged DHCP `assigned` silently supersedes a real lease). Mitigations: TCP 514 for the FortiGate and the core router with `[[sources]] transport = "tcp"` (forged UDP is then dropped, counted and alerted as `wrong-transport`) and anti-spoofing filters on the core router (DEVICE-SETUP 1.1). Not yet done: the server firewall still opens UDP 514 for every configured sender |
| Legal | the KVKK notice text (web app) and the retention of audit entries need legal review |
| Legal drafts | for the lawyer: [legal/aydinlatma-metni-taslak.md](legal/aydinlatma-metni-taslak.md) (employee information text), [legal/saklama-imha-politikasi-taslak.md](legal/saklama-imha-politikasi-taslak.md) (retention and deletion policy) — drafts, not approved |
| Licence enforcement | an offline check against the server's own administrator: what it cannot prevent is listed in section 13; a `.pth` file in the venv's site-packages is not stopped by `python -I` (phase 2) |

## 13. Offline licensing

Decision: [ADR-0013](adr/0013-offline-licensing.md); customer guide: [LISANS.md](LISANS.md) /
[LICENSING.md](LICENSING.md).

- **No secrets in the repository.** The code holds only the licensor's **public** keys
  (`arcilogger_core/license/keys.py`, empty until the owner commits the first one) and the ids of
  revoked licences (`revoked.py`). The private signing key is generated and kept by the licensor on
  an offline machine (the issuer tool is not part of the bundle) and never enters the repository,
  the build or a server. Tests generate their own key pairs. No environment variable, configuration
  file, database row or API can add a trusted key or lift a revocation: the verifier reads the
  module constants at call time.
- **No telemetry, no network call.** Licence checks run entirely on the server; ARCILOGGER never
  contacts the licensor or anyone else for the licence. The request code (server id, installation
  id, version, organisation name, platform; not secret) and the licence file travel by e-mail,
  carried by a person.
- **Isolated Python.** The services and the scheduled tasks run the venv's interpreter with `-I`
  (`python.exe -I -X utf8 -u -m arcilogger_<package> …`; tasks `-I -m …`): no environment variable
  — machine-wide, or per service under `HKLM\SYSTEM\CurrentControlSet\Services\<id>\Environment`
  — can put a `sitecustomize.py` or another module on their path (`PYTHONPATH`, `PYTHONSTARTUP`,
  user site-packages). A `.pth` file dropped into the venv's site-packages is **not** stopped by
  this (file tampering, phase 2).
- **The 5651 path never depends on the licence.** Collector, ingest, the core evidence modules and
  the scheduled tasks never import the licence code; the shared API, worker and database paths reach
  it only through a guard that turns any failure into "5651 Temel only, no users limit". Every
  licence installation, removal, refusal (also a refused upload, in its own transaction), state
  change and limit crossing is an audit entry (`license.*`, below), without the licensee name or
  server ids.
- **Users limit.** `POST /users` and re-enabling a user are refused with 409 `license_limit` at the
  licence's users limit (recounted under the user-management lock); `POST /auth/setup/admin`, sign-in
  of existing users, role changes and deactivation never are. `create-admin`: section 9.

What the licensing **cannot** protect against offline (the threat model, ADR-0013
"Consequences"; the customer's administrator is the attacker):

- spoofing the server identity (editing the MachineGuid; on virtual machines the SMBIOS UUID) or
  cloning a virtual machine with the same ids — not detectable without a network;
- a full fresh install (a new database **and** an empty data folder) for a new trial — it loses
  every sealed day and the audit log;
- disabling the append-only triggers of the licence tables as database owner — it changes no
  licence decision (the licence is the signed file, verified on every evaluation);
- hiding vault days and rolling the clock back — sealing stops and the sealing alerts fire, so it
  is visible in production;
- reading usage tables already computed, with SQL, after the module is locked;
- a determined administrator patching the checker (phase 2 — a compiled licence module, a signed
  manifest of the program files, Authenticode — raises the cost, it does not stop this).

The remedies are contractual: the licence agreement, the licensor's register of issued licences
and the licensee name shown in the interface.
