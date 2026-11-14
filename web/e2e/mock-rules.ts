/**
 * The mocked security rules API of Wave 6 (contract §2.3, §2.6.1) for the E2E tests, from the
 * contract's JSON (`src/test/rules-samples.ts`) until W6-A's API is captured:
 *
 * - `GET /rules`: `alerts.read`; readable without the `security_rules` module (D1).
 * - `PUT /rules/{id}`: `settings.manage` and the module (403 `module_not_licensed`); 404
 *   `rule_not_found`; the body is `rules.<ID>` (only what differs from the defaults), checked as
 *   §2.1.4 says (unknown keys, the window's and cooldown's bounds, a parameter's bounds, a list's
 *   length, a network that is not one: 422 `rule_param_invalid` with `field`). The answer is the
 *   rule with its new effective settings; a rule that needs setup (R08's DNS servers) stops
 *   needing it once its list is filled.
 * - `POST /rules/{id}/dry-run`: the same checks; the answer counts the sample keys whose count
 *   reaches the candidate's threshold. `dryRun: 'busy' | 'timeout'` answers 409 / 504 instead.
 */

import type { RuleList, RuleOut, RuleParamValue, RuleSettings } from '../src/lib/rules-types';
import { DRY_RUN, RULE_ERRORS, RULES, RULES_LOCKED } from '../src/test/rules-samples';

import type { RecordedCall } from './mock-api';
import type { Answer, MockHost } from './mock-records';
import { copy } from './samples';

export interface RulesOptions {
  /** The licence lacks `security_rules`: readable, every change refused. */
  locked?: boolean;
  /** The trial run is refused as another one runs, or runs out of time. */
  dryRun?: 'busy' | 'timeout';
}

const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'];
const SETTING_KEYS = [
  'enabled',
  'severity',
  'window_seconds',
  'cooldown_seconds',
  'actions',
  'params',
];
const WEEK = 7 * 86_400;
const NETWORK = /^(\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?|[0-9a-fA-F:]+:[0-9a-fA-F:.]*(\/\d{1,3})?)$/;

/** 422 `rule_param_invalid` naming `field`, worded as §2.6.4. */
function invalid(field: string): Answer {
  return [
    422,
    {
      error: {
        code: 'rule_param_invalid',
        message_tr: `Kural ayarı geçersiz: ${field}.`,
        message_en: `Invalid rule setting: ${field}.`,
        details: { field },
      },
    },
  ];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export class RulesMock {
  private readonly list: RuleList;

  constructor(private readonly options: RulesOptions = {}) {
    this.list = copy(options.locked ? RULES_LOCKED : RULES);
  }

  /** The rule as the mock holds it now. */
  rule(id: string): RuleOut | undefined {
    return this.list.rules.find((item) => item.id === id);
  }

  answer(call: RecordedCall, host: MockHost): Answer | null {
    const { method, path } = call;
    if (method === 'GET' && path === '/rules') {
      const account = host.requirePermission(call, 'alerts.read');
      return Array.isArray(account) ? account : [200, copy(this.list)];
    }
    const match = /^\/rules\/([^/]+)(\/dry-run)?$/.exec(path);
    if (!match) return null;
    const dry = match[2] === '/dry-run';
    if (!(dry ? method === 'POST' : method === 'PUT')) return null;
    const account = host.requirePermission(call, 'settings.manage');
    if (Array.isArray(account)) return account;
    if (!this.list.module_enabled) return [403, copy(RULE_ERRORS.module_not_licensed)];
    const rule = this.rule(decodeURIComponent(match[1] ?? ''));
    if (!rule) return [404, copy(RULE_ERRORS.rule_not_found)];
    const merged = this.merge(rule, call.body);
    if (!('enabled' in merged)) return merged;
    if (dry) return this.dryRun(merged);
    rule.settings = merged;
    if (rule.id === 'R08') {
      const servers = merged.params.allowed_dns_servers;
      rule.needs_setup = !Array.isArray(servers) || servers.length === 0;
    }
    return [200, copy(rule)];
  }

  /** The defaults with the body's overrides, or the refusal of the body. */
  private merge(rule: RuleOut, body: unknown): RuleSettings | Answer {
    if (!isRecord(body)) return invalid('body');
    for (const key of Object.keys(body)) if (!SETTING_KEYS.includes(key)) return invalid(key);
    const settings: RuleSettings = copy(rule.defaults);
    if ('enabled' in body) {
      if (typeof body.enabled !== 'boolean') return invalid('enabled');
      settings.enabled = body.enabled;
    }
    if ('severity' in body) {
      if (typeof body.severity !== 'string' || !SEVERITIES.includes(body.severity))
        return invalid('severity');
      settings.severity = body.severity as RuleSettings['severity'];
    }
    const window = body.window_seconds;
    if (window !== undefined) {
      if (typeof window !== 'number' || !Number.isInteger(window) || window < 60 || window > WEEK)
        return invalid('window_seconds');
      settings.window_seconds = window;
    }
    const cooldown = body.cooldown_seconds;
    if (cooldown !== undefined) {
      if (typeof cooldown !== 'number' || !Number.isInteger(cooldown) || cooldown < 0)
        return invalid('cooldown_seconds');
      if (cooldown > WEEK) return invalid('cooldown_seconds');
      settings.cooldown_seconds = cooldown;
    }
    if (body.actions !== undefined) {
      if (!isRecord(body.actions)) return invalid('actions');
      for (const [key, value] of Object.entries(body.actions)) {
        if ((key !== 'email' && key !== 'webhook') || typeof value !== 'boolean')
          return invalid(`actions.${key}`);
        settings.actions[key] = value;
      }
    }
    if (body.params !== undefined) {
      if (!isRecord(body.params)) return invalid('params');
      for (const [key, value] of Object.entries(body.params)) {
        const param = rule.params.find((item) => item.key === key);
        if (!param) return invalid(`params.${key}`);
        const field = `params.${key}`;
        if (Array.isArray(value)) {
          if (!['strings', 'networks', 'ints'].includes(param.kind)) return invalid(field);
          if (param.min !== null && value.length < param.min) return invalid(field);
          if (value.length > Math.min(param.max ?? 200, 200)) return invalid(field);
          if (param.kind === 'networks' && !value.every((item) => NETWORK.test(String(item))))
            return invalid(field);
          if (param.kind === 'ints' && !value.every((item) => Number.isInteger(item)))
            return invalid(field);
        } else if (typeof value === 'number') {
          if (!Number.isInteger(value)) return invalid(field);
          if (param.min !== null && value < param.min) return invalid(field);
          if (param.max !== null && value > param.max) return invalid(field);
        } else if (typeof value !== 'boolean') return invalid(field);
        settings.params[key] = value as RuleParamValue;
      }
    }
    return settings;
  }

  private dryRun(settings: RuleSettings): Answer {
    if (this.options.dryRun === 'busy') return [409, copy(RULE_ERRORS.rule_dry_run_busy)];
    if (this.options.dryRun === 'timeout') return [504, copy(RULE_ERRORS.rule_dry_run_timeout)];
    const threshold = settings.params.threshold_ports ?? settings.params.threshold ?? 0;
    const keys = DRY_RUN.keys.filter((item) => item.count >= Number(threshold));
    return [200, { ...copy(DRY_RUN), would_open: keys.length, keys: copy(keys) }];
  }
}
