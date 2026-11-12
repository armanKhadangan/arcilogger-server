/** Mirror of api/src/arcilogger_api/routers/rules.py (Wave 6 contract §2.6.1). */
import type { DateTimeString } from './api-types';

export type ParamKind =
  'int' | 'bytes' | 'seconds' | 'percent' | 'strings' | 'networks' | 'ints' | 'bool';

/** One parameter of a rule: an integer (min/max bound the value) or a list (they bound its length). */
export interface ParamOut {
  key: string;
  kind: ParamKind;
  min: number | null;
  max: number | null;
  label_tr: string;
  label_en: string;
}

/** One security rule as the editor shows it; `defaults` and `settings` have the `rules.<ID>` shape. */
export interface RuleOut {
  id: string;
  name_tr: string;
  name_en: string;
  kind: 'event' | 'condition';
  inputs: string[];
  person_level: boolean;
  /** `false`: "Sahada doğrula". */
  format_verified: boolean;
  /** "Ayar gerekli". */
  needs_setup: boolean;
  data_status: 'ok' | 'no_data' | 'never';
  last_input_at: DateTimeString | null;
  last_run_at: DateTimeString | null;
  last_error: string | null;
  skipped_until: DateTimeString | null;
  defaults: Record<string, unknown>;
  settings: Record<string, unknown>;
  params: ParamOut[];
  advice_tr: string;
  advice_en: string;
  note_tr: string;
  note_en: string;
}

/** `GET /rules`; `module_enabled` false: the editor shows the lock. */
export interface RulesOut {
  module_enabled: boolean;
  rules: RuleOut[];
}

export interface DryRunKey {
  key: string;
  count: number;
  first_time: DateTimeString;
  last_time: DateTimeString;
}

/** `POST /rules/{id}/dry-run`: "Son 24 saatte bu ayarlarla {n} uyarı açılırdı". */
export interface DryRunOut {
  would_open: number;
  keys: DryRunKey[];
  truncated: boolean;
  duration_ms: number;
}
