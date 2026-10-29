# ARCILOGGER

**Kayıt · Mühür · Rapor** — kuruluşun kendi sunucusunda çalışan log kayıt, mühürleme, arama ve
raporlama sistemi. Kuruluşa özgü hiçbir bilgi (ad, ağ planı, cihaz adresleri) ürünle gelmez:
kuruluş adı, saat kaynağı ve yönetici PC'sinin adresi kurulumda girilir; log kaynakları,
departmanlar ve sunucu ayarları kurulumdan sonra web arayüzünde tanımlanır
([docs/KURULUM.md](docs/KURULUM.md) bölüm 7.1, [docs/AYARLAR.md](docs/AYARLAR.md)).

> 5651 sayılı Kanun ve 11.04.2017 tarihli İnternet Toplu Kullanım Sağlayıcıları Hakkında
> Yönetmelik yükümlülüklerini **desteklemek için tasarlanmıştır**. Hukuki uygunluk için hukuk
> müşavirinizin değerlendirmesi gerekir.

## Ne yapar?

- FortiGate ve MikroTik cihazlarından syslog alır, her kaydı **değiştirmeden** saklar.
- Her gece günün loglarını sıkıştırır, SHA-256 ile özetler, önceki güne zincirler ve
  **TÜBİTAK BİLGEM Kamu SM zaman damgası** ile mühürler; en az 2 yıl saklar.
- "Bu genel IP'yi şu saatte kim kullandı?" sorusunu IP → MAC → cihaz → (kişi) zinciriyle yanıtlar,
  resmi PDF rapor ve bağımsız doğrulanabilen kanıt paketi üretir. Kişi, cihazın **olay anında**
  kime atanmış olduğuna göre (atama geçmişi) adlandırılır; atanmamış cihazda sonuç cihaz ve
  VLAN'dan türetilen departmanda biter.
- **Arama:** veritabanındaki bütün kayıtlarda alan sözdizimiyle arama (`srcip:`, `mac:`,
  `domain:`, `type:` …), zaman histogramı ve dağılımlar, kayıtlı aramalar, CSV/Excel dışa aktarma ve
  aramanın satırlarından kanıt paketi; **Canlı Akış** gelen satırları süzgeçle anında gösterir,
  **Kaynaklar** her log kaynağının durumunu, boşluklarını ve ayrıştırma hatalarını gösterir
  ([docs/ARAMA.md](docs/ARAMA.md), English: [docs/SEARCH.md](docs/SEARCH.md)). Arama ve okuma
  işlemleri KVKK bilgilendirmesi onaylanınca açılır ve denetim kaydına yazılır.
- Kullanıcılar, roller, iki adımlı doğrulama (TOTP) ve değiştirilemez (zincirli) denetim kaydı.
- **Tarayıcıdan yönetim:** kuruluş adı ve saat, uygulama ayarları, Kamu SM sihirbazı, log
  kaynakları, e-posta, replika, saklama ve yedekleme Ayarlar sayfalarından değişir; **Şimdi
  mühürle** ve **Kontör sorgula** Mühürleme sayfasındadır. Değişikliği API değil, yalnızca
  yapılandırmaya yazabilen ayrı bir hizmet (ArciAdmin) imzalı istekle uygular; hizmetler yeniden
  başlatılmaz, her değişiklik denetim kaydına yazılır. Kurulum, yükseltme, PostgreSQL, dosya
  yolları ve çalıştırılan programlar bilerek sunucu konsolunda kalır
  ([docs/AYARLAR.md](docs/AYARLAR.md) bölüm 5, English: [docs/SETTINGS.md](docs/SETTINGS.md)).
- Uyarılar: sessiz kalan log kaynağı (R15) ve sistem denetimleri (R20: mühürleme, doğrulama, disk,
  saat, kontör, replika, veritabanı yedeği …); yüksek ve kritik uyarılar `[smtp]` ayarlıysa
  e-postayla bildirilir. **Güvenlik** sayfasında listelenir, durumu değiştirilir ve not eklenir
  ([docs/UYARILAR.md](docs/UYARILAR.md)).
- Kişi, cihaz ve departman bazında **tahmini** internet kullanımı (çevrimiçi ve aktif süre, siteler,
  kategoriler, URL'ler, engellenen denemeler), varsayılan olarak **adlarla** (sahip kararı
  20.10.2026; [docs/KULLANIM.md](docs/KULLANIM.md); kişi düzeyindeki görünümler için hukuki
  inceleme önerilir); zamanlanmış kullanım raporları e-posta, PDF ve Excel olarak
  ([docs/KULLANIM-RAPORLARI.md](docs/KULLANIM-RAPORLARI.md)).
- **Lisans:** süresiz lisans ve yıllık bakım, 14 günlük deneme, çevrimdışı etkinleştirme
  (istek kodu → imzalı lisans dosyası; ARCILOGGER lisans için hiçbir zaman dışarıya bağlanmaz).
  Ücretli modüller Kullanım analizi, Zamanlanmış kullanım raporları ve Toplu içe aktarma
  (CSV)'dır; 5651 Temel (toplama, mühürleme, sorgu, kanıt paketleri, uyarılar, denetim kaydı)
  hiçbir lisans durumunda kapanmaz ([docs/LISANS.md](docs/LISANS.md)).
- **Planlanan (M5):** güvenlik kurallarının geri kalanı (kural motoru), kural düzenleyici ve
  webhook'lar.

## Nasıl çalışır?

- **Tek bir Windows sunucu** (Windows Server 2022/2025 önerilir). Docker gerekmez.
- **Kullanıcılara istemci kurulmaz.** Herkes tarayıcıdan bağlanır: `https://<sunucu adresi>`
  (ya da `https://arcilogger.internal`). Çalışan bilgisayarlarına ajan kurulmaz; veriler ağ
  cihazlarından gelir.
- **Desteklenen ağ:** tek yerleşke ve tek bir FortiGate; internete bu FortiGate üzerinden çıkan
  IPv4 ağı. Kira kanıtı (IP → MAC → cihaz → kişi) bir MikroTik yönlendiricinin ya da FortiGate'in
  DHCP sunucusunun kayıtlarından gelir. Birden çok internet hattı **İnternet çıkışları** olarak
  tanımlanır (genel IP FortiGate'te ya da servis sağlayıcının NAT'ının arkasında). IPv6 ile 5651
  sorgusu desteklenmez. Ayrıntılar ve desteklenmeyenler: [docs/KURULUM.md](docs/KURULUM.md)
  bölüm 0.1 (English: [docs/INSTALL.md](docs/INSTALL.md) section 0.1).
- Ayrıntılar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Kurulum ve ilk adımlar

- Kurulum: [docs/KURULUM.md](docs/KURULUM.md) (English: [docs/INSTALL.md](docs/INSTALL.md));
  kurulum programı ve betikler: [deploy/windows/README.md](deploy/windows/README.md).
- Kurulumdan sonra (ilk yönetici, Kamu SM test → üretim, ilk 5651 sorgusu, günlük kontroller):
  [docs/ILK-ADIMLAR.md](docs/ILK-ADIMLAR.md) (English: [docs/FIRST-STEPS.md](docs/FIRST-STEPS.md));
  Ayarlar sayfaları ve konsolda kalanlar: [docs/AYARLAR.md](docs/AYARLAR.md) (English:
  [docs/SETTINGS.md](docs/SETTINGS.md)).
- Lisans ve etkinleştirme (modüller, deneme, istek kodu, bakım süresi):
  [docs/LISANS.md](docs/LISANS.md) (English: [docs/LICENSING.md](docs/LICENSING.md)); lisans
  verenin aracı (yalnızca hak sahibi için, pakete girmez):
  [tools/license-issuer/README.md](tools/license-issuer/README.md).
- Cihaz ayarları: [docs/DEVICE-SETUP.md](docs/DEVICE-SETUP.md); Kamu SM: [docs/kamusm.md](docs/kamusm.md);
  denetçi: [docs/DENETCI-KILAVUZU.md](docs/DENETCI-KILAVUZU.md).
- Hukuk müşavirine verilecek taslaklar: [aydınlatma metni](docs/legal/aydinlatma-metni-taslak.md),
  [saklama ve imha politikası](docs/legal/saklama-imha-politikasi-taslak.md).
- Kararlar ve varsayımlar: [docs/adr/](docs/adr/README.md), [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md);
  değişiklikler: [CHANGELOG.md](CHANGELOG.md).

## Durum

Bu sürüm (0.1.0, **MVP**): M2/M3'ün temel parçaları, kimlik doğrulama ve denetim kaydı (M6'dan)
ve Windows kurulum programı (M7'den). Tablo, 0.1.0'dan sonra eklenenleri de gösterir
([CHANGELOG.md](CHANGELOG.md), "Unreleased"). Menüde henüz içeriği olmayan sayfalar
**yer tutuculardır** ("Bu sayfa sonraki bir sürümde eklenecek.").

| Aşama | Kapsam | Durum |
|---|---|---|
| M0 | Depo iskeleti, CI, kod standartları, tasarım belirteçleri, bileşen kiti | ✅ |
| M1 | Syslog toplayıcı, ham kayıt, mühürleme (Kamu SM), doğrulama, zincir, replika, saklama | ✅ |
| M2 | Ayrıştırıcılar, veritabanı, arama, canlı akış, kaynaklar | ✅ FortiGate/MikroTik ayrıştırıcıları, veritabanı, ingest (spool → PostgreSQL), Panel, Arama, Canlı Akış ve Kaynaklar sayfaları ([docs/ARAMA.md](docs/ARAMA.md)); TimescaleDB ve Windows üzerindeki arama hız ölçümleri CI'da bekleniyor |
| M3 | Kira zaman çizelgesi, NAT dizini, 5651 sorgu, PDF rapor, kanıt paketi | kısmen — ✅ kira zaman çizelgesi, 5651 Sorgu + resmi PDF rapor, Kanıt Paketleri, Mühürleme sayfası, Cihazlar ve Kişiler sayfası, bağımsız doğrulayıcı |
| M4 | Kullanım analitiği, zamanlanmış raporlar, e-posta | kısmen — ✅ kullanım analizi motoru ve API'si ([docs/KULLANIM.md](docs/KULLANIM.md)), zamanlanmış kullanım raporları (HTML e-posta, PDF, Excel; [docs/KULLANIM-RAPORLARI.md](docs/KULLANIM-RAPORLARI.md)), Raporlar sayfası (zamanlamalar, şimdi çalıştır, önizleme, gönderim geçmişi), Kullanım sayfası |
| M5 | Güvenlik kuralları (20 kural), uyarılar | kısmen — ✅ R15 (log kaynağı sessiz) ve R20 sistem denetimleri, uyarı e-postası, Güvenlik sayfası (uyarı listesi, durum akışı, notlar; [docs/UYARILAR.md](docs/UYARILAR.md)); kural motoru, kural düzenleyici ve webhook'lar yok |
| M6 | Yetkilendirme, 2FA, denetim kaydı, uyum sayfası, ayarlar, ilk kurulum sihirbazı | kısmen — ✅ kullanıcılar ve roller, TOTP, zincirli denetim kaydı (Denetim Kaydı sayfası), ilk yönetici kurulumu, tarayıcıdan yönetim (Ayarlar sayfaları, Kamu SM sihirbazı, Şimdi mühürle, Kontör sorgula; [docs/AYARLAR.md](docs/AYARLAR.md)); yer tutucu: Uyum Durumu, Sistem; ilk kurulum sihirbazı yok |
| M7 | Sağlamlaştırma, Windows kurulum programı, yedekleme/geri yükleme, belgeler | kısmen — ✅ Windows kurulum programı (çevrimdışı paket, yükseltme, geri alma), yük üreteci, gece veritabanı yedeği ve geri yükleme ([docs/YEDEKLEME.md](docs/YEDEKLEME.md)); `ISLETIM.md` yok |

Yer tutucu sayfalar: **Uyum Durumu, Sistem**. Çalışan sayfalar: Panel, Arama, Canlı Akış,
Kaynaklar, 5651 Sorgu, Kullanım, Güvenlik, Mühürleme, Kanıt Paketleri, Cihazlar ve Kişiler,
Raporlar, Denetim Kaydı, Ayarlar (Genel, Uygulama ayarları, Kamu SM, Kaynaklar, E-posta, Replika,
Saklama, Yedekleme, Değişiklik geçmişi, Kullanıcılar ve Roller, Lisans). Ana menüdeki Kaynaklar
sayfası kaynakların durumunu yalnızca gösterir; kaynaklar Ayarlar → Kaynaklar'da düzenlenir
(bilinmeyen bir göndericide "Kaynak olarak tanımla" bu sayfayı doldurarak açar).

## Geliştirme / Development

Requirements: Python 3.12 via [uv](https://docs.astral.sh/uv/), Node.js 22 with **npm ≥ 11**.
Everything works the same on Windows and Linux.

```bash
# Python workspace (core, db, collector, ingest, api, worker)
uv sync --all-packages
uv run poe check          # ruff + mypy --strict + pytest
uv run poe api-dev        # API on http://127.0.0.1:8000/api/v1/health

# Database tests need a throwaway PostgreSQL 16 role with CREATEDB (docs/MVP-CONTRACTS.md §7)
export ARCILOGGER_TEST_PG=postgresql://arci_test:arci_test@127.0.0.1:5432/postgres

# A real local stack (fresh database, setup token, optional seed data of the fictional example
# network) for the web app
uv run python web/scripts/live_stack.py --help
# Synthetic syslog for load tests and demo data (tools/loadgen/README.md)
uv run python tools/loadgen/arcilogger_loadgen.py --help

# Web interface
cd web
npm install               # npm >= 11
npm run dev               # http://127.0.0.1:5173  (UI kit: /ui-kit, not in the shipped build)
npm run check             # eslint + prettier + tsc + vitest + build (no UI kit, no source maps)
npm run e2e               # Playwright + axe accessibility checks (builds dist-e2e with the UI kit)

# Optional development database and mail catcher (development only)
docker compose -f deploy/dev/compose.yaml up -d
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

## Depo yapısı / Repository layout

```
core/        arcilogger-core: sealing, verification, retention, time stamps, evidence packages,
             standalone verifier, CLI (Task Scheduler)
db/          arcilogger-db: models, migrations, seed, settings, monthly partitions
collector/   syslog receiver → raw spool (Windows service)
ingest/      spool → FortiGate/MikroTik parsers → PostgreSQL (TimescaleDB optional)
api/         FastAPI: auth/TOTP, users, audit log, 5651 lookup + PDF report, evidence, seals
worker/      scheduled application jobs: lease timeline, 5651 lookup engine, audit import
web/         React web interface (Turkish first, English)
deploy/      Windows installer (offline bundle), services, tasks, scripts; development compose file
docs/        installation, first steps, auditor guide, architecture, decisions, legal drafts (TR/EN)
tools/       load generator, pointer to the standalone verifier, licence issuer (owner only,
             never in the bundle)
tests/       shared fixtures (synthetic log format samples) and cross-package tests
```

## Lisans

Tescilli yazılım. Telif hakkı sahibi ve kullanım koşulları: [LICENSE](LICENSE). Kurulumun
lisansı (modüller, deneme, etkinleştirme, bakım): [docs/LISANS.md](docs/LISANS.md).
