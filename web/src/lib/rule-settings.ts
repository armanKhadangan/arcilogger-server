/**
 * The rule editor's view of the security rules API (`rules-types.ts`, W6-A, contract §2.6.1):
 * the API answers a rule's `defaults` and `settings` as JSON objects of the setting `rules.<ID>`'s
 * shape (§2.1.4); the editor reads them with that shape, typed here (W6-F).
 */

import type { AlertSeverity } from './api-types';
import type { DryRunKey, DryRunOut, ParamKind, ParamOut, RuleOut, RulesOut } from './rules-types';

export type { DryRunKey, DryRunOut, ParamOut };

/** `event`: an alert per key, closed by a person; `condition`: closes itself (D9). */
export type RuleKind = RuleOut['kind'];

/** How a parameter is typed (`arcilogger_worker.rules.model.Param.kind`). */
export type RuleParamKind = ParamKind;

/**
 * Whether the tables the rule reads receive rows: `ok`, `no_data` (nothing within
 * `rules.no_data_hours`), `never`.
 */
export type RuleDataStatus = RuleOut['data_status'];

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

/** A rule as the editor reads it: `RuleOut` with its `defaults` and `settings` typed. */
export interface Rule extends Omit<RuleOut, 'defaults' | 'settings'> {
  defaults: RuleSettings;
  settings: RuleSettings;
}

/** `GET /rules` as the editor reads it. */
export interface Rules extends Omit<RulesOut, 'rules'> {
  rules: Rule[];
}
