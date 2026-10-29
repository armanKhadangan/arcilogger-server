/**
 * Fixtures of the Wave 4 web tests. The first-run wizard's configurations are made from the
 * captured `GET /admin/config` (`e2e/samples/admin.json`): as captured (site name set, two enabled
 * sources, replica on, the mock time-stamp provider, no e-mail server) and as a fresh installation
 * has it.
 *
 * The views of Uyum Durumu, Sistem and the Panel disk card get hand-made answers of
 * `GET /compliance`, `GET /system`, `GET /system/health` and `GET /system/disk` (no capture exists
 * before W4-F), worded as the server words them (`api/src/arcilogger_api/compliance/texts.py`,
 * `system_health/tasks.py`, `system_health/jobs.py`). They tell one site's story at the captured
 * samples' clock (13.10.2026 07:00): Örnek A.Ş. with the sources `fortigate` and `core-r1`, the
 * Kamu SM production server, retention off, the replica on; `core-r1` silent, the MikroTik ARP
 * evidence missing, yesterday's copy late and the backup state unreadable. Licence maintenance
 * comes from the captured `GET /license` samples.
 */

import type { LicenseOut } from '@/lib/api-types';
import type {
  ComplianceAlertRef,
  ComplianceCard,
  ComplianceCardId,
  ComplianceCounts,
  ComplianceFact,
  ComplianceStatus,
  ComplianceTone,
} from '@/lib/compliance-types';
import type { AdminConfig, AdminRequestOut, SetupProblem } from '@/lib/settings-types';
import type {
  AgentHealth,
  ApplySystemHealth,
  ClockSampleOut,
  JobHealth,
  ServiceHealth,
  SystemHealth,
  TaskHealth,
} from '@/lib/system-health-types';
import type {
  CollectorInfo,
  DatabaseInfo,
  DiskForecast,
  DriveInfo,
  HostInfo,
  IngestInfo,
  StatusFileState,
  SystemDisk,
  SystemSummary,
} from '@/lib/system-types';
import { ADMIN, licenseSample } from '@/test/samples';

/** A copy of the captured configuration. */
export function capturedConfig(): AdminConfig {
  return structuredClone(ADMIN.config);
}

/** The setup problems of a fresh installation, shortened from what `config.setup_problems` says. */
export const FRESH_PROBLEMS: SetupProblem[] = [
  {
    level: 'error',
    key: 'site_name',
    message_tr: 'Kuruluş adı (site_name) ayarlanmamış',
    message_en: 'the organisation name (site_name) is not set',
  },
  {
    level: 'error',
    key: 'clock.fallback_server',
    message_tr: 'Saat sunucusu ayarlanmamış',
    message_en: 'no time server is set',
  },
  {
    level: 'warning',
    key: 'sources',
    message_tr: 'Log kaynağı ([[sources]]) tanımlanmamış',
    message_en: 'no log source ([[sources]]) is configured',
  },
];

/** A fresh installation: no site name, no time server, no source, nothing beyond the mock. */
export function freshConfig(): AdminConfig {
  const config = capturedConfig();
  config.problems = structuredClone(FRESH_PROBLEMS);
  config.sections.general.site_name = '';
  config.sections.general.clock.ntp_server = '';
  config.sections.general.clock.fallback_server = '';
  config.sections.sources = [];
  config.sections.replica.enabled = false;
  config.sections.smtp.host = null;
  config.sections.smtp.from_address = null;
  config.sections.tsa.provider = 'mock';
  config.sections.tsa.server = null;
  return config;
}

/** Every step done: production server, sealing ready, replica on and e-mail switched on. */
export function completeConfig(): AdminConfig {
  const config = capturedConfig();
  config.problems = [];
  config.sections.tsa.provider = 'zamane';
  config.sections.tsa.server = 'production';
  config.sections.tsa.seal_ready = { ok: true, causes: [] };
  config.sections.smtp.enabled = true;
  config.sections.smtp.host = 'smtp.ornek.example';
  config.sections.smtp.from_address = 'arcilogger@ornek.example';
  return config;
}

/** A "Damga testi" of the history (the captured active request, as a finished stamp test). */
export function stampTest(
  state: AdminRequestOut['state'] = 'succeeded',
  verified = true,
): AdminRequestOut {
  return {
    ...structuredClone(ADMIN.request_active),
    kind: 'action.tsa_test',
    state,
    terminal: true,
    result: { verified, errors: [], warnings: [] },
  };
}

// -- Uyum Durumu (`GET /compliance`, contract 2.3) ---------------------------------------------

/** The moment of every Wave 4 view answer: the captured samples' clock. */
export const W4_NOW = '2026-10-13T07:00:00+03:00';

/** `notice_tr` / `notice_en`, verbatim (contract 2.3.1). */
export const COMPLIANCE_NOTICE = {
  tr:
    'ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır. Bu kartlar kayıt ' +
    'toplama, mühürleme ve yedeklemenin teknik denetimlerinin sonucudur; hukuki bir ' +
    'değerlendirme ya da uygunluk beyanı değildir. Sonuçları hukuk müşavirinizle teyit edin.',
  en:
    'ARCILOGGER is designed to support 5651 obligations. These cards are the results of ' +
    'technical checks of log collection, sealing and backups; they are neither a legal ' +
    'assessment nor a statement of conformity. Confirm the results with your legal counsel.',
} as const;

/** The cards in the order of contract 2.3.3 (`number` = index + 1). */
export const COMPLIANCE_CARD_IDS: readonly ComplianceCardId[] = [
  'sources',
  'seal_yesterday',
  'chain',
  'retention',
  'tsa',
  'clock',
  'content_filter',
  'lease_evidence',
  'replica',
  'backup',
];

export const COMPLIANCE_TONES: readonly ComplianceTone[] = ['ok', 'warning', 'error', 'neutral'];

/** A Turkish and English text pair. */
type Pair = readonly [tr: string, en: string];

function fact(key: string, label: Pair, value: Pair | string): ComplianceFact {
  const [value_tr, value_en] = typeof value === 'string' ? [value, value] : value;
  return { key, label_tr: label[0], label_en: label[1], value_tr, value_en };
}

function alertRef(
  id: number,
  rule: string,
  severity: ComplianceAlertRef['severity'],
  opened_at: string,
): ComplianceAlertRef {
  return { id, rule, severity, opened_at };
}

const SERVER_PRODUCTION: Pair = ['Kamu SM üretim', 'Kamu SM production'];

/** Title, page, verify-on-site note, facts and alerts of each card (as the site's story has them). */
const CARD_PARTS: Record<
  ComplianceCardId,
  Pick<ComplianceCard, 'title_tr' | 'title_en' | 'page' | 'facts' | 'alerts'> & {
    verify?: Pair;
  }
> = {
  sources: {
    title_tr: 'Log kaynakları veri gönderiyor',
    title_en: 'Log sources are sending',
    page: 'sources',
    facts: [
      fact('watched', ['İzlenen kaynak', 'Watched sources'], '2'),
      fact('active', ['Veri gönderen', 'Sending'], '1'),
      fact('silent', ['Sessiz', 'Silent'], 'core-r1'),
      fact(
        'heartbeat',
        ['Kalp atışı süresi', 'Heartbeat'],
        ['kaynağa göre, varsayılan 15 dk', 'per source, default 15 min'],
      ),
    ],
    alerts: [alertRef(41, 'R15', 'high', '2026-10-13T06:12:00+03:00')],
  },
  seal_yesterday: {
    title_tr: 'Dünün kayıtları mühürlendi ve doğrulandı',
    title_en: "Yesterday's records sealed and verified",
    page: 'sealing',
    facts: [
      fact('day', ['Gün', 'Day'], '12.10.2026'),
      fact('state', ['Durum', 'State'], ['Mühürlendi ve doğrulandı', 'Sealed and verified']),
      fact('sealed_at', ['Mühürlendi', 'Sealed'], '13.10.2026 00:10:37'),
      fact(
        'verification',
        ['Doğrulama', 'Verification'],
        ['13.10.2026 01:00:41, başarılı', '13.10.2026 01:00:41, passed'],
      ),
      fact('server', ['Damga sunucusu', 'Time-stamp server'], SERVER_PRODUCTION),
    ],
    alerts: [],
  },
  chain: {
    title_tr: 'Mühür zinciri bütün',
    title_en: 'Seal chain intact',
    page: 'sealing',
    facts: [
      fact('verified', ['Doğrulanmış', 'Verified'], '30'),
      fact('not_verified', ['Doğrulanmamış', 'Not verified'], '0'),
      fact('failed', ['Başarısız', 'Failed'], '0'),
      fact('no_data', ['Veri yok ya da silinmiş', 'No data or deleted'], '0'),
      fact(
        'full_check',
        ['Son tam zincir denetimi', 'Last full chain check'],
        ['01.10.2026 04:00:12, başarılı', '01.10.2026 04:00:12, passed'],
      ),
      fact(
        'tasks',
        ['Doğrulama görevleri', 'Verification tasks'],
        [
          'günlük son 2 gün, haftalık son 90 gün, aylık tüm zincir',
          'daily the last 2 days, weekly the last 90 days, monthly the whole chain',
        ],
      ),
    ],
    alerts: [],
  },
  retention: {
    title_tr: 'Saklama süresi',
    title_en: 'Retention period',
    page: 'sealing',
    facts: [
      fact('oldest', ['En eski mühürlü gün', 'Oldest sealed day'], '05.10.2025'),
      fact('retention', ['Saklama', 'Retention'], ['kapalı', 'off']),
      fact('deleted', ['Saklama ile silinen gün', 'Days deleted by retention'], '0'),
    ],
    alerts: [],
  },
  tsa: {
    title_tr: 'Kamu SM kontörü ve zaman damgası sertifikası',
    title_en: 'Kamu SM credit and time-stamp certificate',
    page: 'sealing',
    facts: [
      fact('server', ['Damga sunucusu', 'Time-stamp server'], SERVER_PRODUCTION),
      fact('credits', ['Kalan kontör', 'Credits left'], ['1.200', '1,200']),
      fact('estimated_days', ['Tahmini gün', 'Estimated days'], '41'),
      fact('threshold', ['Eşik', 'Threshold'], ['2.000', '2,000']),
      fact('credit_at', ['Son okuma', 'Last reading'], '13.10.2026 06:00:04'),
      fact('cert_until', ['TSA sertifikası bitişi', 'TSA certificate expires'], '14.08.2030'),
      fact('cert_days', ['Kalan gün', 'Days left'], '1401'),
    ],
    alerts: [alertRef(44, 'R20-credit', 'medium', '2026-10-13T06:00:31+03:00')],
    verify: [
      'Kontör değeri Kamu SM istemcisinin çıktısından okunur; okuma biçimi sahada doğrulanmalıdır.',
      "The credit is read from the Kamu SM client's output; verify the format on site.",
    ],
  },
  clock: {
    title_tr: 'Sunucu saati',
    title_en: 'Server clock',
    page: 'system',
    facts: [
      fact('checked_at', ['Son ölçüm', 'Last measurement'], '13.10.2026 06:05:02'),
      fact('server', ['Sunucu', 'Server'], '172.20.0.1'),
      fact('offset', ['Fark', 'Offset'], ['12,4 ms', '12.4 ms']),
      fact('limit', ['Sınır', 'Limit'], ['1.000 ms', '1,000 ms']),
      fact('local_source', ['Yerel saat kaynağı', 'Local time source'], ['ayarlı', 'set']),
    ],
    alerts: [],
  },
  content_filter: {
    title_tr: 'İçerik filtresi kayıtları',
    title_en: 'Content filter records',
    page: 'search',
    facts: [
      fact('last', ['Son kayıt', 'Last record'], '13.10.2026 06:58:13'),
      fact('today_web', ['Bugün web filtresi', 'Web filter today'], '1834'),
      fact('today_dns', ['Bugün DNS filtresi', 'DNS filter today'], '0'),
      fact(
        'days',
        ['Son 7 gün', 'Last 7 days'],
        '12.10: 21407 · 11.10: 9876 · 10.10: 8455 · 09.10: 22310 · 08.10: 23012 · 07.10: 22745 · ' +
          '06.10: 21998',
      ),
      fact(
        'profiles',
        ['Web filtresi profilleri', 'Web filter profiles'],
        'ornek-web, misafir-web',
      ),
      fact(
        'dns_profile',
        ['DNS filtresi profili', 'DNS filter profile'],
        [
          'FortiGate DNS filtresi kayıtlarından profil adı okunmuyor (sahada doğrula).',
          'No profile name is read from FortiGate DNS filter records (verify on site).',
        ],
      ),
      fact(
        'expected_days',
        ['Beklenen günler', 'Expected days'],
        ['Pzt, Sal, Çar, Per, Cum, Cmt, Paz', 'Mon, Tue, Wed, Thu, Fri, Sat, Sun'],
      ),
      fact('holidays', ['Resmî tatiller', 'Public holidays'], ['beklenmez', 'not expected']),
    ],
    alerts: [],
    verify: [
      "Güvenlik duvarı politikalarının web ve DNS filtresi olaylarını kaydettiği ve ARCILOGGER'a " +
        'gönderdiği sahada doğrulanmalıdır.',
      'Verify on site that the firewall policies log web and DNS filter events and send them to ' +
        'ARCILOGGER.',
    ],
  },
  lease_evidence: {
    title_tr: 'DHCP ve ARP kayıtları (erişim kayıtları)',
    title_en: 'DHCP and ARP records (access records)',
    page: 'sources',
    facts: [
      fact(
        'mt_dhcp',
        ['MikroTik DHCP', 'MikroTik DHCP'],
        ['412 satır (24 saat)', '412 rows (24 h)'],
      ),
      fact('mt_arp', ['MikroTik ARP', 'MikroTik ARP'], ['0 satır (24 saat)', '0 rows (24 h)']),
      fact(
        'fg_dhcp',
        ['FortiGate DHCP', 'FortiGate DHCP'],
        ['1.287 satır (24 saat)', '1,287 rows (24 h)'],
      ),
      fact(
        'problems',
        ['Açık kira kanıtı sorunları', 'Open lease evidence problems'],
        [
          'Kira kanıtı kesildi: MikroTik ARP-LOG kayıtları gelmiyor',
          'Lease evidence stopped: no MikroTik ARP-LOG records',
        ],
      ),
    ],
    alerts: [alertRef(47, 'R20-lease', 'high', '2026-10-12T21:40:00+03:00')],
  },
  replica: {
    title_tr: 'İkinci kopya (replika)',
    title_en: 'Second copy (replica)',
    page: 'sealing',
    facts: [
      fact('enabled', ['Replika', 'Replica'], ['açık', 'on']),
      fact('last_copy', ['Son kopya', 'Last copy'], '12.10.2026 00:41:09'),
      fact('pending', ['Bekleyen gün', 'Days waiting'], '1'),
      fact('stale_hours', ['Gecikme eşiği (saat)', 'Delay threshold (hours)'], '48'),
    ],
    alerts: [],
    verify: [
      "Replika hedefinin değiştirilemez (WORM) olduğu ARCILOGGER'dan denetlenemez; sahada " +
        'doğrulayın.',
      'Whether the replica target is write-once (WORM) cannot be checked from ARCILOGGER; verify ' +
        'it on site.',
    ],
  },
  backup: {
    title_tr: 'Yapılandırma ve veritabanı yedekleri',
    title_en: 'Configuration and database backups',
    page: 'settings',
    facts: [
      fact('last_backup', ['Son veritabanı yedeği', 'Last database backup'], '13.10.2026 01:52:18'),
      fact('size', ['Boyut', 'Size'], ['18,4 GB', '18.4 GB']),
      fact('enabled', ['Gece yedeği', 'Nightly backup'], ['açık', 'on']),
      fact(
        'config_copy',
        ['Yapılandırma kopyası onayı', 'Configuration copy confirmed'],
        '02.10.2026 14:20:05',
      ),
    ],
    alerts: [],
  },
};

const CHECK_FAILED_TR = 'Bu denetim şu anda yapılamadı: ';

/** "This check could not be run now" (contract 2.3.2): any card may be `neutral` with it. */
function checkFailed(errorCode: string): Pair {
  return [`${CHECK_FAILED_TR}${errorCode}`, `This check could not be run now: ${errorCode}`];
}

/**
 * One reason of contract 2.3.3 for every card and tone, filled for the site's story; a tone the
 * card's table has no row for (card 1 and card 6 `neutral`) takes the "could not be run" reason.
 */
export const COMPLIANCE_REASONS: Record<ComplianceCardId, Record<ComplianceTone, Pair>> = {
  sources: {
    ok: [
      'İzlenen 2 kaynağın tümü kalp atışı süresi içinde veri gönderdi.',
      'All 2 watched sources sent data within their heartbeat.',
    ],
    warning: [
      'core-r1 kalp atışı süresinden uzun süredir sessiz; uyarı başlangıç bekleme süresi içinde.',
      'core-r1 silent for longer than the heartbeat; the alert is still in its start-up grace ' +
        'period.',
    ],
    error: [
      '1 kaynak kalp atışı süresinden uzun süredir veri göndermiyor: core-r1.',
      '1 source(s) have sent nothing for longer than their heartbeat: core-r1.',
    ],
    neutral: checkFailed('OperationalError'),
  },
  seal_yesterday: {
    ok: [
      'Dün (12.10.2026) mühürlendi ve doğrulandı.',
      'Yesterday (12.10.2026) was sealed and verified.',
    ],
    warning: [
      "Dün (12.10.2026) mühürlendi, henüz doğrulanmadı (VerifyDaily 01:00'de çalışır).",
      'Yesterday (12.10.2026) is sealed but not verified yet (VerifyDaily runs at 01:00).',
    ],
    error: [
      "Dün (12.10.2026) saat 9:00'a kadar mühürlenmedi.",
      'Yesterday (12.10.2026) was not sealed by 9:00.',
    ],
    neutral: [
      'Dün için kayıt yok; henüz izlenen kaynak tanımlanmadı.',
      'No records for yesterday; no watched source is defined yet.',
    ],
  },
  chain: {
    ok: [
      'Son 30 günün mühürlü 30 günü doğrulandı; tam zincir denetimi 01.10.2026 04:00:12 ' +
        'tarihinde başarılı.',
      'All 30 sealed days of the last 30 are verified; the full chain check of 01.10.2026 ' +
        '04:00:12 passed.',
    ],
    warning: [
      'Son 30 günde 1 gün henüz mühürlenmedi: 10.10.2026.',
      '1 day(s) of the last 30 are not sealed yet: 10.10.2026.',
    ],
    error: [
      'Son 30 günde 1 gün başarısız: 08.10.2026.',
      '1 day(s) of the last 30 failed: 08.10.2026.',
    ],
    neutral: ['Son 30 günde mühürlü gün yok.', 'No sealed day in the last 30 days.'],
  },
  retention: {
    ok: [
      'En eski mühürlü gün 05.10.2025 (373 gün önce); kurulum 730 günden genç.',
      'The oldest sealed day is 05.10.2025 (373 days ago); the installation is younger than 730 ' +
        'days.',
    ],
    warning: [
      'Saklama kapalı ve 365 günden eski 8 mühürlü gün kasada duruyor (en eskisi 05.10.2025). ' +
        'Kişisel verilerin gereğinden uzun saklanmaması için saklamayı açın.',
      'Retention is off and 8 sealed day(s) older than 365 days are in the vault (the oldest ' +
        '05.10.2025). Turn retention on so that personal data is not kept longer than needed.',
    ],
    error: ['Saklama görevinin açık bir uyarısı var.', 'The retention task has an open alert.'],
    neutral: ['Henüz mühürlü gün yok.', 'No sealed day yet.'],
  },
  tsa: {
    ok: [
      'Kalan kontör 12.400 (eşik 2.000); son damganın TSA sertifikası 1401 gün daha geçerli.',
      '12,400 credits left (threshold 2,000); the TSA certificate of the newest time stamp is ' +
        'valid for 1401 more days.',
    ],
    warning: [
      'Kalan kontör 1.200; eşik 2.000. Tahmini 41 gün yeter.',
      '1,200 credits left; threshold 2,000. Estimated to last 41 days.',
    ],
    error: ['Kamu SM kontörü bitti.', 'The Kamu SM credit is used up.'],
    neutral: [
      'Henüz Kamu SM ile damgalanmış gün yok.',
      'No day has been stamped with Kamu SM yet.',
    ],
  },
  clock: {
    ok: [
      'Saat farkı 12,4 ms (172.20.0.1, 13.10.2026 06:05:02); sınır 1.000 ms.',
      'Clock offset 12.4 ms (172.20.0.1, 13.10.2026 06:05:02); limit 1,000 ms.',
    ],
    warning: [
      'Son saat ölçümü 13.10.2026 02:05:01; 3 saatten eski (ClockCheck saatte bir çalışır).',
      'The last clock measurement was at 13.10.2026 02:05:01, more than 3 hours ago ' +
        '(ClockCheck runs hourly).',
    ],
    error: ['Saat farkı 1.250 ms; sınır 1.000 ms.', 'Clock offset 1,250 ms; limit 1,000 ms.'],
    neutral: checkFailed('PermissionError'),
  },
  content_filter: {
    ok: [
      'Son kayıt 13.10.2026 06:58:13; bugün 1834 web, 402 DNS filtresi kaydı.',
      'Last record 13.10.2026 06:58:13; today 1834 web and 402 DNS filter records.',
    ],
    warning: [
      'DNS filtresi kaydı 24 saattir gelmiyor; diğer tür geliyor.',
      'No DNS filter record for 24 hours; the other kind still arrives.',
    ],
    error: [
      'Beklenen günlerde son 24 saattir web ya da DNS filtresi kaydı gelmedi (son kayıt: ' +
        '12.10.2026 05:41:20).',
      'No web or DNS filter record in the last 24 hours of expected days (last record: ' +
        '12.10.2026 05:41:20).',
    ],
    neutral: [
      'FortiGate kaynağı tanımlı değil; içerik filtresi kaydı beklenmiyor.',
      'No FortiGate source is defined; no content filter records are expected.',
    ],
  },
  lease_evidence: {
    ok: [
      'Son 24 saatte MikroTik DHCP, MikroTik ARP, FortiGate DHCP kayıtları geldi; açık kira ' +
        'kanıtı uyarısı yok.',
      'MikroTik DHCP, MikroTik ARP, FortiGate DHCP records arrived in the last 24 hours; no ' +
        'lease evidence alert is open.',
    ],
    warning: [
      'Son 24 saatte DHCP ya da ARP kaydı gelmedi.',
      'No DHCP or ARP record arrived in the last 24 hours.',
    ],
    error: ['1 kira kanıtı sorunu açık.', '1 lease evidence problem(s) open.'],
    neutral: [
      'İzlenen kaynak yok; erişim kaydı beklenmiyor.',
      'No watched source; no access records are expected.',
    ],
  },
  replica: {
    ok: [
      'Son 7 günün mühürlü 7 günü replika hedefine kopyalandı ve denetlendi.',
      'All 7 sealed days of the last 7 were copied to the replica target and checked.',
    ],
    warning: [
      '1 mühürlü gün henüz kopyalanmadı (en eskisi 12.10.2026).',
      '1 sealed day(s) not copied yet (the oldest 12.10.2026).',
    ],
    error: [
      'Replika kapalı: mühürlü günlerin ikinci kopyası alınmıyor.',
      'The replica is off: no second copy of the sealed days is made.',
    ],
    neutral: ['Son 7 günde mühürlü gün yok.', 'No sealed day in the last 7 days.'],
  },
  backup: {
    ok: [
      'Son veritabanı yedeği 13.10.2026 01:52:18 (18,4 GB); yapılandırma kopyası 02.10.2026 ' +
        '14:20:05 tarihinde onaylandı.',
      'Last database backup 13.10.2026 01:52:18 (18.4 GB); configuration copy confirmed on ' +
        '02.10.2026 14:20:05.',
    ],
    warning: [
      'Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3).',
      'The offline copy of the configuration files was never confirmed (BACKUP.md section 3).',
    ],
    error: ['Gece veritabanı yedeği kapalı.', 'The nightly database backup is off.'],
    neutral: checkFailed('PermissionError'),
  },
};

/** The tone of each card in the site's story: every tone at least once. */
const STORY_TONES: Record<ComplianceCardId, ComplianceTone> = {
  sources: 'error',
  seal_yesterday: 'ok',
  chain: 'ok',
  retention: 'ok',
  tsa: 'warning',
  clock: 'ok',
  content_filter: 'warning',
  lease_evidence: 'error',
  replica: 'warning',
  backup: 'neutral',
};

/** One card of the site's story, or with another `tone` (its reason of `COMPLIANCE_REASONS`). */
export function complianceCard(
  id: ComplianceCardId,
  tone: ComplianceTone = STORY_TONES[id],
): ComplianceCard {
  const number = COMPLIANCE_CARD_IDS.indexOf(id) + 1;
  const { verify, ...parts } = structuredClone(CARD_PARTS[id]);
  const [reason_tr, reason_en] = COMPLIANCE_REASONS[id][tone];
  if (reason_tr.startsWith(CHECK_FAILED_TR)) {
    // A card whose computation raised carries no facts and no alerts (contract 2.3.2).
    parts.facts = [];
    parts.alerts = [];
  }
  return {
    id,
    number,
    tone,
    ...parts,
    reason_tr,
    reason_en,
    doc_tr: `UYUM-DURUMU.md bölüm 2.${number}`,
    doc_en: `COMPLIANCE-STATUS.md section 2.${number}`,
    verify_on_site_tr: verify?.[0] ?? null,
    verify_on_site_en: verify?.[1] ?? null,
    checked_at: W4_NOW,
  };
}

/** `counts` as the server makes them from the cards' tones. */
export function countTones(cards: readonly ComplianceCard[]): ComplianceCounts {
  const counts: ComplianceCounts = { ok: 0, warning: 0, error: 0, neutral: 0 };
  for (const card of cards) counts[card.tone] += 1;
  return counts;
}

/**
 * `GET /compliance` of the site's story: 4 ok, 3 warning (5, 7, 9), 2 error (1, 8), 1 neutral
 * (10, its check could not be run); alerts on cards 1, 5 and 8. `tones` changes single cards.
 */
export function complianceStatus(
  tones: Partial<Record<ComplianceCardId, ComplianceTone>> = {},
): ComplianceStatus {
  const cards = COMPLIANCE_CARD_IDS.map((id) => complianceCard(id, tones[id] ?? STORY_TONES[id]));
  return {
    generated_at: W4_NOW,
    site_name: 'Örnek A.Ş.',
    counts: countTones(cards),
    cards,
    notice_tr: COMPLIANCE_NOTICE.tr,
    notice_en: COMPLIANCE_NOTICE.en,
  };
}

/** Every card `ok` (no alert refs left: an `ok` card has none). */
export function allOkCompliance(): ComplianceStatus {
  const status = complianceStatus(
    Object.fromEntries(COMPLIANCE_CARD_IDS.map((id) => [id, 'ok' as const])),
  );
  for (const card of status.cards) card.alerts = [];
  return status;
}

/** A status whose ten cards are all of one tone (as `allOkCompliance` for `ok`). */
export function complianceAllOf(tone: ComplianceTone): ComplianceStatus {
  if (tone === 'ok') return allOkCompliance();
  return complianceStatus(Object.fromEntries(COMPLIANCE_CARD_IDS.map((id) => [id, tone])));
}

// -- Sistem: resources (`GET /system`, `GET /system/disk`, contract 2.5) ------------------------

const GIB = 1024 ** 3;

/**
 * The host: `measured` (Windows, every value read), `first_reading` (the API's first request: CPU
 * still "ölçülüyor"), `unreadable` (a platform where nothing is read: every value null).
 */
export function hostInfo(kind: 'measured' | 'first_reading' | 'unreadable' = 'measured'): HostInfo {
  if (kind === 'unreadable') {
    return {
      platform: 'other',
      cpu_percent: null,
      memory_total_bytes: null,
      memory_used_bytes: null,
      uptime_seconds: null,
    };
  }
  return {
    platform: 'windows',
    cpu_percent: kind === 'measured' ? 23 : null,
    memory_total_bytes: 16 * GIB,
    memory_used_bytes: 9 * GIB,
    uptime_seconds: 874_800,
  };
}

/** A forecast in each state; `estimated` for the data drive below (54 %, 6 GiB a day). */
export function diskForecast(state: DiskForecast['state']): DiskForecast {
  switch (state) {
    case 'estimated':
      return {
        state,
        samples: 30,
        growth_bytes_per_day: 6 * GIB,
        warning_on: '2026-12-30',
        full_on: '2027-03-02',
      };
    case 'insufficient':
      return {
        state,
        samples: 4,
        growth_bytes_per_day: null,
        warning_on: null,
        full_on: null,
      };
    case 'not_growing':
      return {
        state,
        samples: 30,
        growth_bytes_per_day: null,
        warning_on: null,
        full_on: null,
      };
  }
}

/**
 * The data drive `D:` (2 TB, 54 % used, forecast `estimated`) or the system drive `C:` (238 GB,
 * 84 % used: past the 80 % mark, forecast `insufficient`); `forecast` and `usedPercent` change it.
 */
export function driveInfo(
  role: DriveInfo['role'],
  { forecast, usedPercent }: { forecast?: DiskForecast['state']; usedPercent?: number } = {},
): DriveInfo {
  const total = role === 'data' ? 2_000_398_934_016 : 255_383_244_800;
  const used =
    usedPercent !== undefined
      ? Math.ceil((total * usedPercent) / 100)
      : role === 'data'
        ? 1_100_219_413_708
        : 214_521_937_977;
  return {
    role,
    path: role === 'data' ? 'D:' : 'C:',
    total_bytes: total,
    used_bytes: used,
    free_bytes: total - used,
    used_percent: Math.floor((used * 100) / total),
    warning_percent: 80,
    critical_percent: 95,
    forecast: diskForecast(forecast ?? (role === 'data' ? 'estimated' : 'insufficient')),
  };
}

/** The database: plain PostgreSQL, TimescaleDB, or a size query that failed (`total_bytes` null). */
export function databaseInfo(
  kind: 'postgresql' | 'timescaledb' | 'unmeasured' = 'postgresql',
): DatabaseInfo {
  const storage = kind === 'timescaledb' ? 'timescaledb' : 'postgresql';
  if (kind === 'unmeasured') return { storage, total_bytes: null, tables: [], measured_at: null };
  return {
    storage,
    total_bytes: 412_316_860_416,
    tables: [
      { name: 'fg_traffic', bytes: 251_658_240_000 },
      { name: 'fg_web', bytes: 61_203_283_968 },
      { name: 'fg_dns', bytes: 38_654_705_664 },
      { name: 'messages', bytes: 21_474_836_480 },
      { name: 'mt_fw', bytes: 12_884_901_888 },
      { name: 'fg_event', bytes: 6_442_450_944 },
      { name: 'mt_dhcp', bytes: 1_073_741_824 },
      { name: 'leases', bytes: 805_306_368 },
      { name: 'job_runs', bytes: 402_653_184 },
      { name: 'audit_log', bytes: 134_217_728 },
    ],
    measured_at: '2026-10-13T06:58:30+03:00',
  };
}

/** The collector's status file: `running` (both sources, two unknown senders counted, never
 * named), `stale` (last written 9 minutes ago) or `missing` (no file). */
export function collectorInfo(state: StatusFileState = 'running'): CollectorInfo {
  if (state === 'missing') {
    return {
      state,
      updated_at: null,
      started_at: null,
      sources: [],
      unknown_senders: { count: 0, messages: 0, bytes: 0 },
      dropped_unknown: 0,
      dropped_transport: 0,
      write_failures: 0,
      write_error_since: null,
      torn_lines: 0,
      tcp_connections: 0,
      tcp_framing_errors: 0,
    };
  }
  return {
    state,
    updated_at: state === 'running' ? '2026-10-13T06:59:55+03:00' : '2026-10-13T06:51:00+03:00',
    started_at: '2026-10-03T09:14:22+03:00',
    sources: [
      {
        name: 'fortigate',
        messages: 48_213_904,
        bytes: 19_431_207_552,
        last_received: '2026-10-13T06:59:54+03:00',
      },
      {
        name: 'core-r1',
        messages: 2_904_118,
        bytes: 611_220_480,
        last_received: '2026-10-13T05:47:10+03:00',
      },
    ],
    unknown_senders: { count: 2, messages: 37, bytes: 6_512 },
    dropped_unknown: 0,
    dropped_transport: 3,
    write_failures: state === 'stale' ? 2 : 0,
    write_error_since: state === 'stale' ? '2026-10-13T06:50:12+03:00' : null,
    torn_lines: 0,
    tcp_connections: 1,
    tcp_framing_errors: 0,
  };
}

/** The ingest's status file: `running`, `stale` (a source with an error, database unreachable)
 * or `missing` (no file: every value null). */
export function ingestInfo(state: StatusFileState = 'running'): IngestInfo {
  if (state === 'missing') {
    return {
      state,
      service_state: null,
      updated_at: null,
      database_ok: null,
      workers_configured: null,
      workers_active: null,
      lines_per_second: null,
      sources: [],
      unknown_sources: 0,
    };
  }
  const stale = state === 'stale';
  return {
    state,
    service_state: 'running',
    updated_at: stale ? '2026-10-13T06:50:40+03:00' : '2026-10-13T06:59:58+03:00',
    database_ok: !stale,
    workers_configured: 4,
    workers_active: stale ? 0 : 2,
    lines_per_second: stale ? 0 : 812.5,
    sources: [
      {
        name: 'fortigate',
        lag_bytes: stale ? 73_400_320 : 0,
        lag_seconds: stale ? 560 : 0,
        lines_per_second: stale ? 0 : 790.2,
        unfinished_days: 1,
        has_error: stale,
      },
      {
        name: 'core-r1',
        lag_bytes: 20_480,
        lag_seconds: 3,
        lines_per_second: 22.3,
        unfinished_days: 1,
        has_error: false,
      },
    ],
    unknown_sources: 1,
  };
}

/** `GET /system`: the measured Windows host, both drives, PostgreSQL, both services running. */
export function systemSummary(): SystemSummary {
  return {
    checked_at: W4_NOW,
    host: hostInfo(),
    drives: [driveInfo('data'), driveInfo('system')],
    database: databaseInfo(),
    collector: collectorInfo(),
    ingest: ingestInfo(),
    version: {
      product: 'ARCILOGGER',
      version: '0.1.0',
      core_version: '0.1.0',
      release_date: '2026-10-01',
    },
  };
}

/** `GET /system/disk` for the Panel card (the drives of `systemSummary`, or the ones given). */
export function systemDisk(
  drives: DriveInfo[] = [driveInfo('data'), driveInfo('system')],
): SystemDisk {
  return { checked_at: W4_NOW, drives };
}

/**
 * `GET /license` for Sistem's maintenance line: the captured licence (maintenance until
 * 20.10.2027), maintenance ending soon (25.10.2026), ended (05.10.2026), or the trial (none).
 */
export function systemLicense(
  name: 'licensed' | 'maintenance_ends_soon' | 'maintenance_expired' | 'trial' = 'licensed',
): LicenseOut {
  return licenseSample(name);
}

// -- Sistem: health (`GET /system/health`, contract 2.6) ----------------------------------------

/** Every service state: ArciIngest stale, ArciWorker missing; ArciApi always runs (it answers). */
export const SERVICES: readonly ServiceHealth[] = [
  { name: 'ArciCollector', state: 'running', updated_at: '2026-10-13T06:59:55+03:00' },
  { name: 'ArciIngest', state: 'stale', updated_at: '2026-10-13T06:50:40+03:00' },
  { name: 'ArciWorker', state: 'missing', updated_at: null },
  { name: 'ArciApi', state: 'running', updated_at: W4_NOW },
];

export function agentHealth(state: AgentHealth['state'] = 'ok'): AgentHealth {
  const heartbeat: Record<AgentHealth['state'], string | null> = {
    ok: '2026-10-13T06:59:40+03:00',
    stale: '2026-10-13T06:41:02+03:00',
    missing: null,
    disabled: null,
  };
  return { state, heartbeat_at: heartbeat[state] };
}

export function applySystemHealth(state: ApplySystemHealth['state'] = 'ok'): ApplySystemHealth {
  switch (state) {
    case 'ok':
      return { state, at: '2026-10-13T06:55:03+03:00', code: null };
    case 'failed':
      return { state, at: '2026-10-13T06:40:11+03:00', code: 'system_sync_failed' };
    default:
      return { state, at: null, code: null };
  }
}

/**
 * One row per `TaskName` in the server's order, texts as `system_health/tasks.py` has them; every
 * state: Replica `late`, Retention `not_used` (retention off), Backup `missing` (its state file is
 * unreadable), Requests `not_observable`.
 */
export const TASKS: readonly TaskHealth[] = [
  {
    task: 'Seal',
    schedule_tr: "Her gün 00:10'dan sonra 5 dakikada bir",
    schedule_en: 'Every 5 minutes from 00:10',
    state: 'ok',
    last_evidence_at: '2026-10-13T00:10:37+03:00',
    evidence: 'state\\seal\\<YYYY-MM-DD>.json',
  },
  {
    task: 'VerifyDaily',
    schedule_tr: 'Her gün 01:00',
    schedule_en: 'Daily at 01:00',
    state: 'ok',
    last_evidence_at: '2026-10-13T01:00:41+03:00',
    evidence: 'state\\verify\\last-last-1.json, last-last-2.json',
  },
  {
    task: 'VerifyWeekly',
    schedule_tr: 'Pazar 03:00',
    schedule_en: 'Sundays at 03:00',
    state: 'ok',
    last_evidence_at: '2026-10-11T03:02:15+03:00',
    evidence: 'state\\verify\\last-last-<N>.json (N ≥ 3)',
  },
  {
    task: 'VerifyMonthly',
    schedule_tr: "Ayın 1'i 04:00",
    schedule_en: '1st of the month at 04:00',
    state: 'ok',
    last_evidence_at: '2026-10-01T04:00:12+03:00',
    evidence: 'state\\verify\\last-full.json',
  },
  {
    task: 'Replica',
    schedule_tr: "Saatte bir, 00:40'tan",
    schedule_en: 'Hourly from 00:40',
    state: 'late',
    last_evidence_at: '2026-10-12T00:41:09+03:00',
    evidence: 'state\\replica\\<YYYY-MM-DD>.json',
  },
  {
    task: 'Retention',
    schedule_tr: 'Her gün 02:00',
    schedule_en: 'Daily at 02:00',
    state: 'not_used',
    last_evidence_at: null,
    evidence: '',
  },
  {
    task: 'Credit',
    schedule_tr: 'Her gün 06:00',
    schedule_en: 'Daily at 06:00',
    state: 'ok',
    last_evidence_at: '2026-10-13T06:00:04+03:00',
    evidence: 'state\\credit.jsonl',
  },
  {
    task: 'ClockCheck',
    schedule_tr: "Saatte bir, 00:05'ten",
    schedule_en: 'Hourly from 00:05',
    state: 'ok',
    last_evidence_at: '2026-10-13T06:05:02+03:00',
    evidence: 'state\\clock\\<YYYY-MM-DD>.jsonl',
  },
  {
    task: 'Backup',
    schedule_tr: 'Her gün 01:40',
    schedule_en: 'Daily at 01:40',
    state: 'missing',
    last_evidence_at: null,
    evidence: 'state\\backup\\last.json',
  },
  {
    task: 'Requests',
    schedule_tr: 'Olayla ve 5 dakikada bir',
    schedule_en: 'On event and every 5 minutes',
    state: 'not_observable',
    last_evidence_at: null,
    evidence: 'state\\requests',
  },
  {
    task: 'ApplySystem',
    schedule_tr: 'Olayla ve 5 dakikada bir',
    schedule_en: 'On event and every 5 minutes',
    state: 'ok',
    last_evidence_at: '2026-10-13T06:55:03+03:00',
    evidence: 'admin\\system\\status.json',
  },
];

/**
 * Worker jobs of `default_jobs()` with the schedule texts `system_health/jobs.py` makes, every
 * state once or more: `alerts.notify` failing, `leases.incremental` running, `usage.today` skipped
 * (licence module off), `reports.run_due` never run.
 */
export const JOBS: readonly JobHealth[] = [
  {
    job: 'partitions.ensure',
    schedule_tr: "Her gün 00:15'ten sonra",
    schedule_en: 'Daily after 00:15',
    module: null,
    state: 'ok',
    last_status: 'ok',
    last_started: '2026-10-13T00:15:04+03:00',
    last_finished: '2026-10-13T00:15:06+03:00',
    duration_ms: 1_840,
    failures: 0,
    next_due: '2026-10-14T00:15:00+03:00',
  },
  {
    job: 'license.check',
    schedule_tr: 'Her 5 dakikada bir',
    schedule_en: 'Every 5 minutes',
    module: null,
    state: 'ok',
    last_status: 'ok',
    last_started: '2026-10-13T06:55:00+03:00',
    last_finished: '2026-10-13T06:55:00+03:00',
    duration_ms: 112,
    failures: 0,
    next_due: '2026-10-13T07:00:00+03:00',
  },
  {
    job: 'leases.incremental',
    schedule_tr: 'Her 1 dakikada bir',
    schedule_en: 'Every 1 minute',
    module: null,
    state: 'running',
    last_status: 'running',
    last_started: '2026-10-13T06:59:30+03:00',
    last_finished: null,
    duration_ms: 4_210,
    failures: 0,
    next_due: null,
  },
  {
    job: 'alerts.notify',
    schedule_tr: 'Her 1 dakikada bir',
    schedule_en: 'Every 1 minute',
    module: null,
    state: 'failing',
    last_status: 'failed',
    last_started: '2026-10-13T06:58:00+03:00',
    last_finished: '2026-10-13T06:58:30+03:00',
    duration_ms: 30_012,
    failures: 3,
    next_due: '2026-10-13T07:02:00+03:00',
  },
  {
    job: 'usage.today',
    schedule_tr: 'Her 5 dakikada bir',
    schedule_en: 'Every 5 minutes',
    module: 'usage_analytics',
    state: 'skipped',
    last_status: 'skipped',
    last_started: '2026-10-13T06:55:00+03:00',
    last_finished: '2026-10-13T06:55:00+03:00',
    duration_ms: 3,
    failures: 0,
    next_due: '2026-10-13T07:00:00+03:00',
  },
  {
    job: 'reports.run_due',
    schedule_tr: 'Her 1 dakikada bir',
    schedule_en: 'Every 1 minute',
    module: 'usage_reports',
    state: 'never',
    last_status: null,
    last_started: null,
    last_finished: null,
    duration_ms: null,
    failures: 0,
    next_due: null,
  },
  {
    job: 'search.counts_backfill',
    schedule_tr: 'Her 10 dakikada bir',
    schedule_en: 'Every 10 minutes',
    module: null,
    state: 'ok',
    last_status: 'ok',
    last_started: '2026-10-13T06:50:00+03:00',
    last_finished: '2026-10-13T06:50:02+03:00',
    duration_ms: 2_406,
    failures: 0,
    next_due: '2026-10-13T07:00:00+03:00',
  },
];

/** The clock samples of the last 24 hours, newest first: answered ones and one without an answer. */
export const CLOCK_SAMPLES: readonly ClockSampleOut[] = [
  {
    time: '2026-10-13T06:05:02+03:00',
    server: '172.20.0.1',
    ok: true,
    offset_ms: 12.4,
    delay_ms: 0.8,
    stratum: 2,
  },
  {
    time: '2026-10-13T05:05:01+03:00',
    server: '172.20.0.1',
    ok: true,
    offset_ms: -8.1,
    delay_ms: 0.9,
    stratum: 2,
  },
  {
    time: '2026-10-13T04:05:03+03:00',
    server: '172.20.0.1',
    ok: false,
    offset_ms: null,
    delay_ms: null,
    stratum: null,
  },
  {
    time: '2026-10-13T03:05:01+03:00',
    server: 'time.ume.tubitak.gov.tr',
    ok: true,
    offset_ms: 21.7,
    delay_ms: 14.2,
    stratum: 1,
  },
];

/** `GET /system/health`: every service, task and job state; agent and ApplySystem `ok`. */
export function systemHealth(): SystemHealth {
  return {
    checked_at: W4_NOW,
    services: structuredClone([...SERVICES]),
    agent: agentHealth(),
    apply_system: applySystemHealth(),
    tasks: structuredClone([...TASKS]),
    jobs: structuredClone([...JOBS]),
    time: {
      ntp_server: '172.20.0.1',
      fallback_server: 'time.ume.tubitak.gov.tr',
      alert_offset_ms: 1000,
      applied_peers: '172.20.0.1,0x8 time.ume.tubitak.gov.tr,0xA',
      applied_at: '2026-10-05T03:20:41+03:00',
      samples: structuredClone([...CLOCK_SAMPLES]),
    },
  };
}
