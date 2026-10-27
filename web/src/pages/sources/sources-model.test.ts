import type { SourceHealth } from '@/lib/sources-types';

import {
  dayRange,
  failurePercent,
  failureSearch,
  filterCounts,
  promotion,
  queryValue,
  shownState,
  sourceRows,
  sourceSearch,
  sourcesSummary,
  STATE_STYLE,
} from './sources-model';

function source(name: string, overrides: Partial<SourceHealth> = {}): SourceHealth {
  return {
    name,
    type: 'mikrotik',
    address: '172.20.10.1',
    configured: true,
    network_of: null,
    unknown: false,
    enabled: true,
    expected: true,
    heartbeat_minutes: 15,
    first_seen: '2026-09-03T07:00:00+03:00',
    last_message: '2026-10-13T06:59:33+03:00',
    last_message_origin: 'collector',
    state: 'active',
    silent_minutes: null,
    messages_per_minute: 12.5,
    sparkline: Array.from({ length: 60 }, () => 12),
    lines_today: 4,
    pending_bytes: 0,
    parse_failures_today: 0,
    parse_failure_permille: null,
    promoted_to: null,
    suggested: null,
    ...overrides,
  };
}

const ITEMS: SourceHealth[] = [
  source('core-r1'),
  source('ap', { address: '172.20.10.0/28', type: 'generic' }),
  source('fortigate', { type: 'fortigate', state: 'silent', silent_minutes: 40 }),
  source('ap-172.20.10.3', { network_of: 'ap', address: '172.20.10.3', state: 'active' }),
  source('ap-172.20.10.4', { network_of: 'ap', address: '172.20.10.4', state: 'never' }),
  source('switch-3', { state: 'silent', expected: false }),
  source('lab', { enabled: false, state: 'disabled' }),
  source('unknown-198.51.100.23', {
    configured: false,
    unknown: true,
    state: 'unknown',
    address: '198.51.100.23',
    type: 'generic',
    suggested: { name: 'kaynak-23', type: 'generic' },
  }),
  source('unknown-198.51.100.7', {
    configured: false,
    unknown: true,
    state: 'promoted',
    address: '198.51.100.7',
    promoted_to: 'edge-1',
  }),
];

describe('rows of the table', () => {
  it('lists each network with its members under it, in the API order', () => {
    expect(sourceRows(ITEMS, 'all').map((row) => [row.item.name, row.member])).toEqual([
      ['core-r1', false],
      ['ap', false],
      ['ap-172.20.10.3', true],
      ['ap-172.20.10.4', true],
      ['fortigate', false],
      ['switch-3', false],
      ['lab', false],
      ['unknown-198.51.100.23', false],
      ['unknown-198.51.100.7', false],
    ]);
  });

  it('"Sessiz" shows silent and never-heard sources; a member stands alone without its network', () => {
    expect(sourceRows(ITEMS, 'silent').map((row) => [row.item.name, row.member])).toEqual([
      ['fortigate', false],
      ['ap-172.20.10.4', false],
      ['switch-3', false],
    ]);
  });

  it('"Bilinmeyen" shows unknown senders, promoted ones too', () => {
    expect(sourceRows(ITEMS, 'unknown').map((row) => row.item.name)).toEqual([
      'unknown-198.51.100.23',
      'unknown-198.51.100.7',
    ]);
    expect(filterCounts(ITEMS)).toEqual({ all: 9, silent: 3, unknown: 2 });
  });
});

describe('state and summary', () => {
  it('a silent source that is not expected to send says so', () => {
    expect(shownState({ state: 'silent', expected: false })).toBe('silentUnwatched');
    expect(shownState({ state: 'silent', expected: true })).toBe('silent');
    expect(shownState({ state: 'never', expected: false })).toBe('never');
  });

  it('every state has a badge with an icon (never colour alone)', () => {
    for (const style of Object.values(STATE_STYLE)) expect(style.icon).toBeTruthy();
    expect(STATE_STYLE.active.tone).toBe('success');
    expect(STATE_STYLE.silent.tone).toBe('danger');
  });

  it('counts the configured, switched-on sources and the unknown senders', () => {
    // core-r1, ap, fortigate, switch-3 (lab is off; members and unknown senders are not counted).
    expect(sourcesSummary(ITEMS)).toEqual({ active: 2, total: 4, unknown: 1 });
    expect(sourcesSummary([])).toEqual({ active: 0, total: 0, unknown: 0 });
  });

  it('turns failures per thousand into a percentage', () => {
    expect(failurePercent(250)).toBe(25);
    expect(failurePercent(64.5)).toBeCloseTo(6.45);
    expect(failurePercent(null)).toBeNull();
  });
});

describe('hand-overs', () => {
  it('fills the add dialog with an unknown sender (name to be reviewed)', () => {
    const unknown = ITEMS.find((item) => item.name === 'unknown-198.51.100.23');
    if (!unknown) throw new Error('fixture');
    expect(promotion(unknown)).toEqual({
      prefill: { name: 'kaynak-23', type: 'generic', address: '198.51.100.23' },
    });
    for (const item of ITEMS.filter((entry) => entry !== unknown))
      expect(promotion(item), item.name).toBeNull();
  });

  it('writes query values bare or quoted', () => {
    expect(queryValue('unknown-198.51.100.23')).toBe('unknown-198.51.100.23');
    expect(queryValue('fortigate.kv')).toBe('fortigate.kv');
    expect(queryValue('a b')).toBe('"a b"');
    expect(queryValue('x,y')).toBe('"x,y"');
    expect(queryValue('say "hi"\\')).toBe('"say \\"hi\\"\\\\"');
    expect(queryValue('core*')).toBe('"core*"');
  });

  it('opens Arama with a source and a site day', () => {
    expect(sourceSearch('core-r1')).toEqual({ q: 'source:core-r1' });
    expect(dayRange('2026-10-31')).toEqual({
      from: '2026-10-31T00:00:00+03:00',
      to: '2026-11-01T00:00:00+03:00',
    });
    expect(sourceSearch('core-r1', { preset: '7d' })).toEqual({
      q: 'source:core-r1',
      range: { preset: '7d' },
    });
  });

  it('opens Arama with a parse-failure pattern, never with lines Arama cannot find', () => {
    expect(failureSearch('fortigate', 'fortigate.kv', { preset: 'today' })).toEqual({
      q: 'source:fortigate error:fortigate.kv',
      range: { preset: 'today' },
    });
    expect(failureSearch('core-r1', 'rawlog.invalid')).toBeNull();
  });
});
