/**
 * Answers of the security rules API (Wave 6 contract §2.6.1), written from the contract's JSON
 * until W6-A's API is captured by `web/scripts/capture_api_samples.py`: six of the rules with the
 * registry defaults of §2.2, on the sample day (13.10.2026) of the other samples.
 *
 * - R01: on, data coming, every default;
 * - R03: on, its FortiGate part not confirmed by a format sample ("Sahada doğrula", V1),
 *   threshold changed to 8;
 * - R08: off and idle until its DNS servers are set ("Ayar gerekli"), no data ever;
 * - R13: a person-level rule with two byte limits;
 * - R17: the condition rule, no data within 24 hours, its last run failed;
 * - R19: off, backlog skipped.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-rules.ts`), so it imports nothing with the
 * `@/` alias and reads no file.
 */

import type { ApiErrorBody } from '../lib/api-types';
import type { DryRunResult, RuleList, RuleOut, RuleSettings } from '../lib/rules-types';

const MIB = 1_048_576;
const GIB = 1_073_741_824;

function settings(
  severity: RuleSettings['severity'],
  window: number,
  cooldown: number,
  params: RuleSettings['params'],
  enabled = true,
): RuleSettings {
  const high = severity === 'high' || severity === 'critical';
  return {
    enabled,
    severity,
    window_seconds: window,
    cooldown_seconds: cooldown,
    actions: { email: high, webhook: true },
    params,
  };
}

const R01_DEFAULTS = settings('high', 60, 3600, {
  deny_actions: ['deny'],
  mikrotik_prefixes: [],
  threshold_ports: 30,
  threshold_hosts: 50,
});

const R03_DEFAULTS = settings('high', 300, 3600, {
  fortigate_fail_logids: ['0100032002'],
  fortigate_fail_descs: ['Admin login failed'],
  threshold: 5,
});

const R08_DEFAULTS = settings(
  'medium',
  3600,
  3600,
  { ports: [53, 853], allowed_dns_servers: [], threshold: 20 },
  false,
);

const R13_DEFAULTS = settings('medium', 3600, 3600, {
  hour_bytes: GIB,
  day_bytes: 5 * GIB,
});

const R17_DEFAULTS = settings('high', 600, 3600, {
  perf_logids: ['0100040704'],
  cpu_field: 'cpu',
  mem_field: 'mem',
  cpu_percent: 80,
  mem_percent: 85,
  sustain_seconds: 600,
  conserve_phrases: ['conserve'],
});

const R19_DEFAULTS = settings('medium', 3600, 3600, {
  rcode_field: 'rcode',
  nxdomain_values: ['NXDOMAIN', '3'],
  threshold: 200,
});

function rule(fields: Omit<RuleOut, 'settings'> & { settings?: RuleSettings }): RuleOut {
  return { ...fields, settings: fields.settings ?? fields.defaults };
}

export const RULES: RuleList = {
  module_enabled: true,
  rules: [
    rule({
      id: 'R01',
      name_tr: 'İçeriden port taraması',
      name_en: 'Internal port scan',
      kind: 'event',
      inputs: ['fg_traffic', 'mt_fw'],
      person_level: false,
      format_verified: true,
      needs_setup: false,
      data_status: 'ok',
      last_input_at: '2026-10-13T11:54:41Z',
      last_run_at: '2026-10-13T11:55:02Z',
      last_error: null,
      skipped_until: null,
      defaults: R01_DEFAULTS,
      params: [
        {
          key: 'threshold_ports',
          kind: 'int',
          min: 1,
          max: 65535,
          label_tr: 'Farklı port sayısı',
          label_en: 'Distinct ports',
        },
        {
          key: 'threshold_hosts',
          kind: 'int',
          min: 1,
          max: 65535,
          label_tr: 'Farklı hedef adres sayısı',
          label_en: 'Distinct target addresses',
        },
        {
          key: 'deny_actions',
          kind: 'strings',
          min: 1,
          max: 20,
          label_tr: 'Engelleme eylemleri (FortiGate action)',
          label_en: 'Deny actions (FortiGate action)',
        },
        {
          key: 'mikrotik_prefixes',
          kind: 'strings',
          min: 0,
          max: 50,
          label_tr: 'MikroTik günlük önekleri',
          label_en: 'MikroTik log prefixes',
        },
      ],
      advice_tr:
        'Adresin hangi cihaza ait olduğunu Cihazlar sayfasında bulun. Cihaz tanınmıyorsa ağdan ayırıp inceleyin; bilinen bir tarayıcıysa adresini kuraldan çıkarmak için eşiği değil cihazı düzenleyin.',
      advice_en:
        'Find the device of the address on the Devices page. If the device is unknown, take it off the network and examine it; if it is a known scanner, change the device, not the threshold.',
    }),
    rule({
      id: 'R03',
      name_tr: 'Kaba kuvvet giriş denemesi',
      name_en: 'Brute-force login',
      kind: 'event',
      inputs: ['fg_event', 'mt_account'],
      person_level: false,
      format_verified: false,
      needs_setup: false,
      data_status: 'ok',
      last_input_at: '2026-10-13T11:53:10Z',
      last_run_at: '2026-10-13T11:55:02Z',
      last_error: null,
      skipped_until: null,
      defaults: R03_DEFAULTS,
      settings: { ...R03_DEFAULTS, params: { ...R03_DEFAULTS.params, threshold: 8 } },
      params: [
        {
          key: 'threshold',
          kind: 'int',
          min: 1,
          max: 10000,
          label_tr: 'Başarısız giriş sayısı',
          label_en: 'Failed logins',
        },
        {
          key: 'fortigate_fail_logids',
          kind: 'strings',
          min: 0,
          max: 20,
          label_tr: 'FortiGate başarısız giriş logid değerleri',
          label_en: 'FortiGate failed-login logids',
        },
        {
          key: 'fortigate_fail_descs',
          kind: 'strings',
          min: 0,
          max: 20,
          label_tr: 'FortiGate başarısız giriş açıklamaları (logdesc)',
          label_en: 'FortiGate failed-login descriptions (logdesc)',
        },
      ],
      advice_tr:
        'Hesabın sahibine denemeleri onun yapıp yapmadığını sorun. Denemeler dışarıdan geliyorsa yönetim arayüzünü yalnızca yönetim ağına açın.',
      advice_en:
        'Ask the owner of the account whether the attempts were theirs. If they come from outside, open the management interface to the management network only.',
    }),
    rule({
      id: 'R08',
      name_tr: 'DNS atlatma',
      name_en: 'DNS bypass',
      kind: 'event',
      inputs: ['fg_traffic'],
      person_level: false,
      format_verified: true,
      needs_setup: true,
      data_status: 'never',
      last_input_at: null,
      last_run_at: null,
      last_error: null,
      skipped_until: null,
      defaults: R08_DEFAULTS,
      params: [
        {
          key: 'allowed_dns_servers',
          kind: 'networks',
          min: 0,
          max: 200,
          label_tr: 'İzin verilen DNS sunucuları',
          label_en: 'Allowed DNS servers',
        },
        {
          key: 'ports',
          kind: 'ints',
          min: 1,
          max: 20,
          label_tr: 'DNS portları',
          label_en: 'DNS ports',
        },
        {
          key: 'threshold',
          kind: 'int',
          min: 1,
          max: 100000,
          label_tr: 'Bağlantı sayısı',
          label_en: 'Connections',
        },
      ],
      advice_tr:
        'Cihazın DNS ayarını kuruluşun DNS sunucusuna çevirin; güvenlik duvarında dışarıya giden 53 ve 853 numaralı portları yalnızca bu sunuculara açın.',
      advice_en:
        "Point the device's DNS setting to the organisation's DNS server; on the firewall, allow outbound ports 53 and 853 only to these servers.",
    }),
    rule({
      id: 'R13',
      name_tr: 'Büyük veri gönderimi',
      name_en: 'Large upload',
      kind: 'event',
      inputs: ['fg_traffic'],
      person_level: true,
      format_verified: true,
      needs_setup: false,
      data_status: 'ok',
      last_input_at: '2026-10-13T11:54:41Z',
      last_run_at: '2026-10-13T11:55:02Z',
      last_error: null,
      skipped_until: null,
      defaults: R13_DEFAULTS,
      params: [
        {
          key: 'hour_bytes',
          kind: 'bytes',
          min: MIB,
          max: 1_099_511_627_776,
          label_tr: 'Bir saatte gönderilen',
          label_en: 'Sent in one hour',
        },
        {
          key: 'day_bytes',
          kind: 'bytes',
          min: MIB,
          max: 1_099_511_627_776,
          label_tr: 'Bir günde gönderilen',
          label_en: 'Sent in one day',
        },
      ],
      advice_tr:
        'Gönderimin iş gereği olup olmadığını cihazın sorumlusuyla konuşun. Kişi adı yalnızca kişi verisini görme yetkisi olanlara gösterilir.',
      advice_en:
        'Talk to the person responsible for the device about whether the upload was needed for work. The name of a person is shown only to those allowed to see personal data.',
    }),
    rule({
      id: 'R17',
      name_tr: 'Güvenlik duvarı sağlığı',
      name_en: 'Firewall health',
      kind: 'condition',
      inputs: ['fg_event'],
      person_level: false,
      format_verified: false,
      needs_setup: false,
      data_status: 'no_data',
      last_input_at: '2026-10-11T08:12:40Z',
      last_run_at: '2026-10-13T11:55:02Z',
      last_error: 'canceling statement due to statement timeout',
      skipped_until: null,
      defaults: R17_DEFAULTS,
      params: [
        {
          key: 'cpu_percent',
          kind: 'percent',
          min: 1,
          max: 100,
          label_tr: 'İşlemci kullanımı',
          label_en: 'CPU usage',
        },
        {
          key: 'mem_percent',
          kind: 'percent',
          min: 1,
          max: 100,
          label_tr: 'Bellek kullanımı',
          label_en: 'Memory usage',
        },
        {
          key: 'sustain_seconds',
          kind: 'seconds',
          min: 60,
          max: 86400,
          label_tr: 'Süre',
          label_en: 'Duration',
        },
        {
          key: 'conserve_phrases',
          kind: 'strings',
          min: 0,
          max: 20,
          label_tr: 'Koruma kipi ifadeleri (logdesc)',
          label_en: 'Conserve mode phrases (logdesc)',
        },
      ],
      advice_tr:
        'Güvenlik duvarının yükünü cihazın kendi arayüzünde doğrulayın; yük sürüyorsa trafiği ve oturum sayısını inceleyin.',
      advice_en:
        "Confirm the firewall's load on the device's own interface; if it persists, examine the traffic and the number of sessions.",
    }),
    rule({
      id: 'R19',
      name_tr: 'Şüpheli DNS (DGA)',
      name_en: 'Suspicious DNS (DGA)',
      kind: 'event',
      inputs: ['fg_dns'],
      person_level: false,
      format_verified: false,
      needs_setup: false,
      data_status: 'ok',
      last_input_at: '2026-10-13T11:54:02Z',
      last_run_at: '2026-10-13T11:55:02Z',
      last_error: null,
      skipped_until: '2026-10-12T09:00:00Z',
      defaults: R19_DEFAULTS,
      settings: { ...R19_DEFAULTS, enabled: false },
      params: [
        {
          key: 'threshold',
          kind: 'int',
          min: 1,
          max: 1000000,
          label_tr: 'Yanıtsız alan adı sorgusu',
          label_en: 'Unanswered domain queries',
        },
        {
          key: 'nxdomain_values',
          kind: 'strings',
          min: 1,
          max: 20,
          label_tr: 'NXDOMAIN değerleri',
          label_en: 'NXDOMAIN values',
        },
      ],
      advice_tr:
        'Cihazda zararlı yazılım taraması yapın; çok sayıda var olmayan alan adı sorgusu kötü amaçlı yazılımın sunucusunu aramasına işaret edebilir.',
      advice_en:
        'Run a malware scan on the device; many queries for domains that do not exist can be malware looking for its server.',
    }),
  ],
};

/** `GET /rules` without the `security_rules` module: readable, every change locked (D1). */
export const RULES_LOCKED: RuleList = { ...RULES, module_enabled: false };

/** A trial run of R01 with its defaults: three addresses would have alerted. */
export const DRY_RUN: DryRunResult = {
  would_open: 3,
  keys: [
    {
      key: '172.20.41.17',
      count: 47,
      first_time: '2026-10-12T14:03:11Z',
      last_time: '2026-10-12T14:04:02Z',
    },
    {
      key: '172.20.10.23',
      count: 33,
      first_time: '2026-10-13T07:41:50Z',
      last_time: '2026-10-13T07:42:31Z',
    },
    {
      key: '172.20.62.5',
      count: 31,
      first_time: '2026-10-13T09:12:09Z',
      last_time: '2026-10-13T09:12:58Z',
    },
  ],
  truncated: false,
  duration_ms: 812,
};

/** The refusals of §2.6.4, as `arcilogger_api.errors.MESSAGES` will word them. */
export const RULE_ERRORS: Record<string, ApiErrorBody> = {
  rule_not_found: {
    error: {
      code: 'rule_not_found',
      message_tr: 'Böyle bir güvenlik kuralı yok.',
      message_en: 'No such security rule.',
    },
  },
  rule_param_invalid: {
    error: {
      code: 'rule_param_invalid',
      message_tr: 'Kural ayarı geçersiz: params.allowed_dns_servers.',
      message_en: 'Invalid rule setting: params.allowed_dns_servers.',
      details: { field: 'params.allowed_dns_servers' },
    },
  },
  rule_dry_run_busy: {
    error: {
      code: 'rule_dry_run_busy',
      message_tr: 'Başka bir deneme sürüyor; bitince tekrar deneyin.',
      message_en: 'Another trial run is in progress; try again when it has finished.',
    },
  },
  rule_dry_run_timeout: {
    error: {
      code: 'rule_dry_run_timeout',
      message_tr: 'Deneme 30 sn içinde bitmedi; pencereyi ya da listeleri küçültün.',
      message_en: 'The trial run did not finish within 30 s; shorten the window or the lists.',
    },
  },
  // The contract (D1) calls it `module_disabled`, "the existing code"; the code the API has is
  // `module_not_licensed` (`arcilogger_api.licensing`), with these texts.
  module_not_licensed: {
    error: {
      code: 'module_not_licensed',
      message_tr:
        'Bu özellik lisansınızda yok: Güvenlik kuralları. Ayrıntılar Ayarlar → Lisans sayfasında.',
      message_en:
        'This feature is not part of your licence: Security rules. See Settings → Licence.',
    },
  },
};
