/**
 * Fixtures of the Wave 4 web tests. The first-run wizard's configurations are made from the
 * captured `GET /admin/config` (`e2e/samples/admin.json`): as captured (site name set, two enabled
 * sources, replica on, the mock time-stamp provider, no e-mail server) and as a fresh installation
 * has it.
 */

import type { AdminConfig, AdminRequestOut, SetupProblem } from '@/lib/settings-types';
import { ADMIN } from '@/test/samples';

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
