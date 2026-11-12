/**
 * The security rules API of Wave 6 (`design/wave-6-contract.md` §2.6.1, W6-A's
 * `arcilogger_api.routers.rules`): the rules R01–R19 with their settings, the change of one rule
 * and the trial run over the last 24 hours ("Son 24 saatte dene").
 *
 * Written from the contract's JSON while W6-A's API is on its way; `api-types.test.ts` checks
 * this file against the OpenAPI document once the API is in the generated contract.
 */

import type { AlertSeverity } from './api-types';

/** `event`: an alert per key, closed by a person; `condition`: closes itself (D9). */
export type RuleKind = 'event' | 'condition';

/** How a parameter is typed (`arcilogger_worker.rules.model.Param.kind`). */
export type RuleParamKind =
  | 'int'
  | 'bytes'
  | 'seconds'
  | 'percent'
  | 'strings'
  | 'networks'
  | 'ints'
  | 'bool';

/**
 * Whether the tables the rule reads receive rows: `ok`, `no_data` (nothing within
 * `rules.no_data_hours`), `never`.
 */
export type RuleDataStatus = 'ok' | 'no_data' | 'never';

/** A parameter's value: a number, a flag or a list (strings, CIDR networks, numbers). */
export type RuleParamValue = number | boolean | string | readonly string[] | readonly number[];

export interface RuleActions {
  email: boolean;
  webhook: boolean;
}

/** The effective settings of a rule (and, as `defaults`, the registry's). */
export interface RuleSettings {
  enabled: boolean;
  severity: AlertSeverity;
  window_seconds: number;
  cooldown_seconds: number;
  actions: RuleActions;
  params: Record<string, RuleParamValue>;
}

/**
 * The body of `PUT /rules/{id}` and of the trial run: the shape of the setting `rules.<ID>`,
 * which holds only what differs from the registry's defaults (§2.1.4).
 */
export interface RuleSettingsIn {
  enabled?: boolean;
  severity?: AlertSeverity;
  window_seconds?: number;
  cooldown_seconds?: number;
  actions?: Partial<RuleActions>;
  params?: Record<string, RuleParamValue>;
}

/** A rule-specific parameter; `min`/`max` bound a number, or a list's length. */
export interface RuleParam {
  key: string;
  kind: RuleParamKind;
  min: number | null;
  max: number | null;
  label_tr: string;
  label_en: string;
}

export interface RuleOut {
  id: string;
  name_tr: string;
  name_en: string;
  kind: RuleKind;
  /** The tables the rule reads, e.g. `fg_traffic`. */
  inputs: string[];
  /** R09, R13, R14: the alert names a person (only for `usage.person`, D3). */
  person_level: boolean;
  /** `false`: the log format is not confirmed by a sample ("Sahada doğrula", §0.4). */
  format_verified: boolean;
  /** Idle until a parameter is set ("Ayar gerekli"). */
  needs_setup: boolean;
  data_status: RuleDataStatus;
  last_input_at: string | null;
  last_run_at: string | null;
  last_error: string | null;
  /** Backlog older than `rules.max_catch_up_hours` was skipped up to this moment. */
  skipped_until: string | null;
  defaults: RuleSettings;
  settings: RuleSettings;
  params: RuleParam[];
  advice_tr: string;
  advice_en: string;
}

/** `GET /rules`: readable without the module; the changes need it (D1). */
export interface RuleList {
  module_enabled: boolean;
  rules: RuleOut[];
}

/** One key the trial run would have alerted on. */
export interface DryRunKey {
  key: string;
  count: number;
  first_time: string;
  last_time: string;
}

/** `POST /rules/{id}/dry-run`. */
export interface DryRunResult {
  would_open: number;
  /** The first 20 keys; `truncated` when there were more. */
  keys: DryRunKey[];
  truncated: boolean;
  duration_ms: number;
}
