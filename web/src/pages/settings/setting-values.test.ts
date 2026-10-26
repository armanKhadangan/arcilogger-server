import type { SettingOut } from '@/lib/settings-types';

import {
  fromDraft,
  sameValue,
  settingControlId,
  toDraft,
  validHoliday,
  valueText,
} from './setting-values';
import { integerError, validateClock, validateHost, validateSiteName } from './settings-validation';

function setting(
  overrides: Partial<SettingOut> & Pick<SettingOut, 'key' | 'type' | 'value'>,
): SettingOut {
  return {
    default: overrides.value,
    is_default: true,
    min: null,
    max: null,
    reauth: false,
    locked: false,
    label_tr: overrides.key,
    label_en: overrides.key,
    help_tr: '',
    help_en: '',
    updated_by: null,
    updated_at: null,
    ...overrides,
  };
}

describe('database settings as drafts and back (contract 2.13)', () => {
  it('numbers: typed as text, checked against the bounds of the API', () => {
    const limit = setting({
      key: 'alerts.email_max_per_hour',
      type: 'integer',
      value: 20,
      min: 1,
      max: 1000,
    });
    expect(toDraft(limit)).toBe('20');
    expect(fromDraft(limit, '30')).toEqual({ value: 30 });
    expect(fromDraft(limit, ' 30 ')).toEqual({ value: 30 });
    expect(fromDraft(limit, '0')).toEqual({ error: 'admin.errors.min' });
    expect(fromDraft(limit, '1001')).toEqual({ error: 'admin.errors.max' });
    expect(fromDraft(limit, '2.5')).toEqual({ error: 'admin.errors.integer' });
    // Without a lower bound an integer setting must be >= 0, like `validate_setting`.
    const free = setting({ key: 'usage.change_min_seconds', type: 'integer', value: 3600 });
    expect(fromDraft(free, '-1')).toEqual({ error: 'admin.errors.min' });
  });

  it('switches, texts and lists (one value per line, commas too)', () => {
    const flag = setting({ key: 'lookup.port_hint', type: 'boolean', value: false });
    expect(toDraft(flag)).toBe(false);
    expect(fromDraft(flag, true)).toEqual({ value: true });
    const text = setting({ key: 'alerts.web_base_url', type: 'string', value: '' });
    expect(fromDraft(text, '  https://127.0.0.1:8443 ')).toEqual({
      value: 'https://127.0.0.1:8443',
    });
    const ips = setting({ key: 'nat.public_ips', type: 'string_list', value: ['198.51.100.7'] });
    expect(toDraft(ips)).toBe('198.51.100.7');
    expect(fromDraft(ips, '198.51.100.7\n203.0.113.9, 192.0.2.1\n\n')).toEqual({
      value: ['198.51.100.7', '203.0.113.9', '192.0.2.1'],
    });
    const mails = setting({ key: 'alerts.email_recipients', type: 'string_list', value: [] });
    expect(fromDraft(mails, 'bt@ornek.example')).toEqual({ value: ['bt@ornek.example'] });
    expect(fromDraft(mails, 'bt@ornek')).toEqual({ error: 'validation.email' });
    const ports = setting({
      key: 'usage.background_ports',
      type: 'integer_list',
      value: [53, 123],
    });
    expect(toDraft(ports)).toBe('53\n123');
    expect(fromDraft(ports, '53\n853')).toEqual({ value: [53, 853] });
    expect(fromDraft(ports, '53\nport')).toEqual({
      error: 'settings.application.errors.integerList',
    });
  });

  it('work hours: days, times in order', () => {
    const hours = setting({
      key: 'usage.work_hours',
      type: 'object',
      value: { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' },
    });
    const draft = toDraft(hours);
    expect(draft).toEqual({ days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' });
    expect(fromDraft(hours, { days: [5, 1, 1], start: '08:30', end: '17:00' })).toEqual({
      value: { days: [1, 5], start: '08:30', end: '17:00' },
    });
    expect(fromDraft(hours, { days: [], start: '08:00', end: '18:00' })).toEqual({
      error: 'settings.application.errors.workDays',
    });
    expect(fromDraft(hours, { days: [1], start: '8:00', end: '18:00' })).toEqual({
      error: 'settings.application.errors.workTime',
    });
    expect(fromDraft(hours, { days: [1], start: '18:00', end: '08:00' })).toEqual({
      error: 'settings.application.errors.workOrder',
    });
  });

  it('holidays: MM-DD every year or YYYY-MM-DD once, real dates only, sorted', () => {
    expect(validHoliday('10-29')).toBe(true);
    expect(validHoliday('02-29')).toBe(true);
    expect(validHoliday('2026-03-20')).toBe(true);
    expect(validHoliday('2026-02-29')).toBe(false);
    expect(validHoliday('13-01')).toBe(false);
    expect(validHoliday('1-1')).toBe(false);
    const holidays = setting({ key: 'usage.holidays', type: 'string_list', value: ['01-01'] });
    expect(toDraft(holidays)).toEqual(['01-01']);
    expect(fromDraft(holidays, ['10-29', '01-01', '2026-03-20', '01-01'])).toEqual({
      value: ['01-01', '10-29', '2026-03-20'],
    });
    expect(fromDraft(holidays, ['31-12'])).toEqual({
      error: 'settings.application.errors.holiday',
    });
  });

  it('lease time per DHCP server and the two-factor roles', () => {
    const servers = setting({
      key: 'lease.server_seconds',
      type: 'object',
      value: { dhcp60: 3600 },
    });
    expect(toDraft(servers)).toEqual([{ server: 'dhcp60', seconds: '3600' }]);
    expect(fromDraft(servers, [{ server: ' dhcp70 ', seconds: '7200' }])).toEqual({
      value: { dhcp70: 7200 },
    });
    expect(fromDraft(servers, [{ server: 'dhcp70', seconds: '30' }])).toEqual({
      error: 'settings.application.errors.serverSeconds',
    });
    expect(fromDraft(servers, [{ server: '', seconds: '3600' }])).toEqual({
      error: 'settings.application.errors.serverName',
    });
    const roles = setting({
      key: 'auth.totp_required_roles',
      type: 'string_list',
      value: ['admin', 'auditor'],
    });
    expect(fromDraft(roles, ['admin', 'auditor', 'hr'])).toEqual({
      value: ['admin', 'auditor', 'hr'],
    });
    expect(fromDraft(roles, ['admin'])).toEqual({ error: 'settings.application.errors.totpRoles' });
  });

  it('any other object is edited as JSON', () => {
    const other = setting({ key: 'x.object', type: 'object', value: { a: 1 } });
    expect(fromDraft(other, '{"b": 2}')).toEqual({ value: { b: 2 } });
    expect(fromDraft(other, '[1]')).toEqual({ error: 'settings.application.errors.object' });
    expect(fromDraft(other, '{')).toEqual({ error: 'settings.application.errors.object' });
  });

  it('compares values regardless of key order and writes them as one line', () => {
    expect(sameValue({ start: '08:00', days: [1] }, { days: [1], start: '08:00' })).toBe(true);
    expect(sameValue([1, 2], [2, 1])).toBe(false);
    expect(valueText({ days: [1, 2], start: '08:00', end: '18:00' })).toBe('1,2 08:00–18:00');
    expect(valueText([])).toBe('—');
    expect(valueText({})).toBe('—');
    expect(valueText({ dhcp60: 3600 })).toBe('dhcp60: 3600');
    expect(settingControlId('usage.work_hours')).toBe('setting-usage-work-hours');
  });
});

describe('machine configuration checks in the browser (contract 2.6)', () => {
  it('site name: required, one line of at most 200 characters', () => {
    expect(validateSiteName('Örnek A.Ş.')).toBeUndefined();
    expect(validateSiteName('  ')).toBe('settings.general.errors.siteNameRequired');
    expect(validateSiteName('ş'.repeat(200))).toBeUndefined();
    expect(validateSiteName('ş'.repeat(201))).toBe('settings.general.errors.siteNameTooLong');
    expect(validateSiteName('Örnek‮A.Ş.')).toBe('validation.controlCharacters');
  });

  it('time servers: a host name or a dotted-quad IPv4 address, at least one of the two', () => {
    expect(validateHost('time.ume.tubitak.gov.tr', false)).toBeUndefined();
    expect(validateHost('172.20.10.1', false)).toBeUndefined();
    expect(validateHost('172.20.10.256', false)).toBe('admin.errors.hostInvalid');
    expect(validateHost('ntp server', false)).toBe('admin.errors.hostInvalid');
    expect(validateHost('', true)).toBeUndefined();
    expect(validateHost('', false)).toBe('validation.required');
    expect(validateClock({ ntp_server: '', fallback_server: '', alert_offset_ms: '1000' })).toEqual(
      {
        fallback_server: 'settings.general.errors.clockServersRequired',
      },
    );
    expect(
      validateClock({ ntp_server: '172.20.10.1', fallback_server: '', alert_offset_ms: '0' }),
    ).toEqual({
      alert_offset_ms: 'admin.errors.min',
    });
    expect(integerError('60001', 1, 60_000)).toBe('admin.errors.max');
  });
});
