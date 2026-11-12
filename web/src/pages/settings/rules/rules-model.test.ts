import type { RuleOut, RuleParam } from '@/lib/rules-types';
import { RULES } from '@/test/rules-samples';

import {
  dataStatusKey,
  defaultsForm,
  fieldOfProblem,
  fromRuleForm,
  isRuleChanged,
  listLines,
  networkProblem,
  paramProblem,
  ruleBody,
  ruleState,
  shownValue,
  sortRules,
  toRuleForm,
  validateRule,
} from './rules-model';

function sample(id: string): RuleOut {
  const found = RULES.rules.find((rule) => rule.id === id);
  if (!found) throw new Error(`no sample rule ${id}`);
  return structuredClone(found);
}

function param(kind: RuleParam['kind'], min: number | null = null, max: number | null = null) {
  return { key: 'p', kind, min, max, label_tr: 'P', label_en: 'P' } satisfies RuleParam;
}

const t = (key: string) => key;

describe('the rule editor form (Wave 6 §2.3)', () => {
  it('holds numbers and lists as text, bytes in MiB, flags as flags', () => {
    const form = toRuleForm(sample('R08'));
    expect(form).toEqual({
      enabled: false,
      severity: 'medium',
      window: '3600',
      cooldown: '3600',
      email: false,
      webhook: true,
      params: { allowed_dns_servers: '', ports: '53\n853', threshold: '20' },
    });
    expect(toRuleForm(sample('R13')).params).toEqual({ hour_bytes: '1024', day_bytes: '5120' });
    expect(toRuleForm(sample('R03')).params.threshold).toBe('8');
  });

  it('turns the form back into the settings it describes', () => {
    const rule = sample('R08');
    const form = toRuleForm(rule);
    form.params = {
      allowed_dns_servers: ' 172.20.0.10 \n\n198.51.100.53',
      ports: '53',
      threshold: '5',
    };
    form.enabled = true;
    expect(fromRuleForm(form, rule)).toEqual({
      ...rule.settings,
      enabled: true,
      params: { allowed_dns_servers: ['172.20.0.10', '198.51.100.53'], ports: [53], threshold: 5 },
    });
    const r13 = sample('R13');
    const big = toRuleForm(r13);
    big.params = { hour_bytes: '1,5', day_bytes: '5120' };
    expect(fromRuleForm(big, r13).params).toEqual({
      hour_bytes: 1_572_864,
      day_bytes: 5 * 1024 ** 3,
    });
  });

  it('keeps a parameter of a kind it has no input for', () => {
    const rule = sample('R01');
    rule.params = [...rule.params, { ...param('int'), key: 'night_start', kind: 'time' as never }];
    rule.settings.params.night_start = '00:00';
    const form = toRuleForm(rule);
    expect(form.params).not.toHaveProperty('night_start');
    expect(fromRuleForm(form, rule).params.night_start).toBe('00:00');
    expect(validateRule(form, rule.params)).toEqual({});
  });

  it('sends only what differs from the defaults; the defaults back are an empty body', () => {
    const r03 = sample('R03');
    expect(ruleBody(r03.settings, r03.defaults)).toEqual({ params: { threshold: 8 } });
    expect(ruleBody(r03.defaults, r03.defaults)).toEqual({});
    const form = defaultsForm(r03);
    expect(isRuleChanged(form, r03)).toBe(true);
    expect(isRuleChanged(toRuleForm(r03), r03)).toBe(false);
    const changed = {
      ...r03.defaults,
      severity: 'critical' as const,
      window_seconds: 600,
      actions: { email: true, webhook: false },
      params: { ...r03.defaults.params, fortigate_fail_descs: ['Admin login failed', 'x'] },
    };
    expect(ruleBody(changed, r03.defaults)).toEqual({
      severity: 'critical',
      window_seconds: 600,
      actions: { webhook: false },
      params: { fortigate_fail_descs: ['Admin login failed', 'x'] },
    });
  });
});

describe('the browser checks of a rule (§2.1.4)', () => {
  it('the window (60 s – 7 days) and the cooldown (0 – 7 days)', () => {
    const rule = sample('R01');
    const form = toRuleForm(rule);
    expect(validateRule(form, rule.params)).toEqual({});
    expect(validateRule({ ...form, window: '59', cooldown: '604801' }, rule.params)).toEqual({
      window: { key: 'admin.errors.min', values: { min: '60' } },
      cooldown: { key: 'admin.errors.max', values: { max: '604.800' } },
    });
    expect(validateRule({ ...form, window: '', cooldown: '1.5' }, rule.params)).toEqual({
      window: { key: 'validation.required' },
      cooldown: { key: 'admin.errors.integer' },
    });
    expect(validateRule({ ...form, cooldown: '0' }, rule.params)).toEqual({});
  });

  it('numbers within the parameter bounds', () => {
    expect(paramProblem(param('int', 1, 65535), '0')).toEqual({
      key: 'admin.errors.min',
      values: { min: '1' },
    });
    expect(paramProblem(param('percent', 1, 100), '101', 'en')).toEqual({
      key: 'admin.errors.max',
      values: { max: '100' },
    });
    expect(paramProblem(param('seconds', 60, 86400), '600')).toBeUndefined();
    expect(paramProblem(param('int'), 'on')).toEqual({ key: 'admin.errors.integer' });
    expect(paramProblem(param('bool'), true)).toBeUndefined();
  });

  it('bytes in MiB, with up to three decimals and either decimal mark', () => {
    const bytes = param('bytes', 1_048_576, 1_099_511_627_776);
    expect(paramProblem(bytes, '1024')).toBeUndefined();
    expect(paramProblem(bytes, '1,5')).toBeUndefined();
    expect(paramProblem(bytes, '0.5')).toEqual({
      key: 'admin.errors.min',
      values: { min: '1 MiB' },
    });
    expect(paramProblem(bytes, '1048577')).toEqual({
      key: 'admin.errors.max',
      values: { max: '1.048.576 MiB' },
    });
    expect(paramProblem(bytes, '1 GB')).toEqual({ key: 'rules.errors.mib' });
  });

  it('lists: one entry per line, the length bounds, each entry checked by its kind', () => {
    expect(listLines(' a \n\n b\r\nc ')).toEqual(['a', 'b', 'c']);
    expect(paramProblem(param('strings', 1, 20), '\n \n')).toEqual({ key: 'validation.required' });
    expect(paramProblem(param('strings', 2, 20), 'a')).toEqual({
      key: 'rules.errors.listMin',
      values: { min: '2' },
    });
    expect(paramProblem(param('strings', 0, 2), 'a\nb\nc')).toEqual({
      key: 'rules.errors.listMax',
      values: { max: '2' },
    });
    expect(paramProblem(param('strings', 0, 1000), Array(201).fill('a').join('\n'))).toEqual({
      key: 'rules.errors.listMax',
      values: { max: '200' },
    });
    expect(paramProblem(param('strings'), `ok\n${'ğ'.repeat(201)}`)).toEqual({
      key: 'rules.errors.entryTooLong',
      values: { line: 2, max: 200 },
    });
    expect(paramProblem(param('strings'), 'a​b')).toEqual({
      key: 'validation.controlCharacters',
      values: { line: 1, max: 200 },
    });
    expect(paramProblem(param('ints'), '53\n\nx53')).toEqual({
      key: 'rules.errors.entryInteger',
      values: { line: 3, max: 200 },
    });
  });

  it('values with quotes, semicolons and % are only text (bound parameters on the server)', () => {
    expect(paramProblem(param('strings'), "x'; DROP TABLE alerts; --\n100%")).toBeUndefined();
  });

  it('networks as PostgreSQL cidr takes them; host bits name the network meant', () => {
    expect(networkProblem('172.20.0.0/16')).toBeNull();
    expect(networkProblem('198.51.100.53')).toBeNull();
    expect(networkProblem('2001:db8::/32')).toBeNull();
    expect(networkProblem('2001:db8::1')).toBeNull();
    expect(networkProblem('172.20.1.0/16')).toBe('hostBits');
    expect(networkProblem('172.20.0.0/33')).toBe('invalid');
    expect(networkProblem('2001:db8::/129')).toBe('invalid');
    expect(networkProblem('172.20.0/24')).toBe('invalid');
    expect(networkProblem('ornek.example')).toBe('invalid');
    expect(networkProblem('10.0.0.0/8/8')).toBe('invalid');
    expect(paramProblem(param('networks'), '172.20.0.10\n172.20.1.0/16')).toEqual({
      key: 'rules.errors.entryHostBits',
      values: { line: 2, max: 200, network: '172.20.0.0/16' },
    });
    expect(paramProblem(param('networks'), '256.1.1.1')).toEqual({
      key: 'rules.errors.entryNetwork',
      values: { line: 1, max: 200 },
    });
  });
});

describe('the server refusal and the table', () => {
  it('maps `rule_param_invalid`.field to the form field', () => {
    const { params } = sample('R08');
    expect(fieldOfProblem({ field: 'params.allowed_dns_servers' }, params)).toBe(
      'param:allowed_dns_servers',
    );
    expect(fieldOfProblem({ field: 'params.ports[1]' }, params)).toBe('param:ports');
    expect(fieldOfProblem({ field: 'window_seconds' }, params)).toBe('window');
    expect(fieldOfProblem({ field: 'cooldown_seconds' }, params)).toBe('cooldown');
    expect(fieldOfProblem({ field: 'severity' }, params)).toBeNull();
    expect(fieldOfProblem({ field: 'params.unknown' }, params)).toBeNull();
    expect(fieldOfProblem(null, params)).toBeNull();
  });

  it('on, off, "Ayar gerekli" (also while off), the data status, the id order', () => {
    expect(ruleState(sample('R01'))).toBe('on');
    expect(ruleState(sample('R19'))).toBe('off');
    expect(ruleState(sample('R08'))).toBe('setup');
    expect(dataStatusKey(sample('R17'))).toBe('rules.dataStatus.no_data');
    expect(sortRules([{ id: 'R10' }, { id: 'R2' }, { id: 'R01' }]).map((rule) => rule.id)).toEqual([
      'R01',
      'R2',
      'R10',
    ]);
  });

  it('shows a default as the editor writes it', () => {
    expect(shownValue(param('bytes'), 1024 ** 3, 'tr', t)).toBe('1 GB');
    expect(shownValue(param('seconds'), 600, 'tr', t)).toBe('10 dk');
    expect(shownValue(param('percent'), 80, 'tr', t)).toBe('%80');
    expect(shownValue(param('percent'), 80, 'en', t)).toBe('80%');
    expect(shownValue(param('int'), 65535, 'tr', t)).toBe('65.535');
    expect(shownValue(param('ints'), [53, 853], 'tr', t)).toBe('53, 853');
    expect(shownValue(param('bool'), true, 'tr', t)).toBe('rules.state.on');
  });
});
