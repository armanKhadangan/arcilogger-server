import type { LiveDropped, LiveLine } from '@/lib/live-types';

import {
  addDropped,
  appendLines,
  asciiLower,
  droppedTotal,
  EMPTY_FILTER,
  highlightText,
  lineColor,
  lineKey,
  liveQuery,
  loadRules,
  MAX_LINES,
  MAX_RULES,
  NO_DROPS,
  RULES_KEY,
  ruleField,
  saveRules,
  searchAround,
  searchGap,
  type HighlightRule,
} from './live-model';

function line(n: number, overrides: Partial<LiveLine> = {}): LiveLine {
  return {
    seq: n,
    received: '2026-10-13T07:00:33.000000+03:00',
    source: 'fortigate',
    sender: '172.31.255.1',
    day: '2026-10-13',
    line: n,
    offset: n * 100,
    kind: 'traffic',
    type: 'traffic',
    subtype: 'forward',
    summary: { srcip: '172.20.42.57', dstip: '203.0.113.10', dstport: 443, action: 'deny' },
    text: `srcip=172.20.42.57 dstip=203.0.113.10 action="deny" n=${n}`,
    truncated: false,
    parse_error: null,
    parsed: true,
    parse_skipped: false,
    ...overrides,
  };
}

describe('the filter as a query', () => {
  it('is empty for no filter (every line)', () => {
    expect(liveQuery(EMPTY_FILTER)).toBe('');
  });

  it('puts sources and unknown senders in one source clause, kinds in one type clause', () => {
    expect(
      liveQuery({
        sources: ['core-r1', 'ap'],
        unknown: true,
        kinds: ['dhcp', 'arp'],
        text: '  "login failure"  ',
      }),
    ).toBe('source:core-r1,ap,unknown-* type:dhcp,arp "login failure"');
    expect(liveQuery({ ...EMPTY_FILTER, unknown: true })).toBe('source:unknown-*');
    expect(liveQuery({ ...EMPTY_FILTER, text: 'sender:172.20.10.30' })).toBe('sender:172.20.10.30');
  });
});

describe('the 2,000-line list', () => {
  it('adds lines at the end and drops the oldest first', () => {
    const first = appendLines([], [line(1), line(2)]);
    expect(first.lines.map((item) => item.line)).toEqual([1, 2]);
    expect(first.removed).toBe(0);
    const full = Array.from({ length: MAX_LINES }, (_, index) => line(index + 1));
    const next = appendLines(full, [line(MAX_LINES + 1), line(MAX_LINES + 2)]);
    expect(next.lines).toHaveLength(MAX_LINES);
    expect(next.lines[0]?.line).toBe(3);
    expect(next.lines.at(-1)?.line).toBe(MAX_LINES + 2);
    expect(next.removed).toBe(2);
  });

  it('shows a line sent again (a resume, a reopened stream) once', () => {
    const shown = appendLines([], [line(1), line(2)]).lines;
    const next = appendLines(shown, [line(2), line(3), line(3)]);
    expect(next.lines.map(lineKey)).toEqual([
      'fortigate/2026-10-13/1',
      'fortigate/2026-10-13/2',
      'fortigate/2026-10-13/3',
    ]);
  });

  it('a burst larger than the list keeps its newest lines', () => {
    const burst = Array.from({ length: MAX_LINES + 500 }, (_, index) => line(index + 1));
    const next = appendLines([line(0, { line: 0 })], burst, MAX_LINES);
    expect(next.lines).toHaveLength(MAX_LINES);
    expect(next.lines[0]?.line).toBe(501);
    expect(next.removed).toBe(1);
  });

  it('a smaller limit works the same', () => {
    expect(appendLines([line(1)], [line(2), line(3)], 2).lines.map((item) => item.line)).toEqual([
      2, 3,
    ]);
  });
});

describe('dropped lines', () => {
  it('adds every event (counts since the previous one) to the totals', () => {
    const event = (fields: Partial<LiveDropped>): LiveDropped => ({
      rate_limited: 0,
      slow_client: 0,
      parse_budget: 0,
      behind: 0,
      since: '2026-10-13T04:00:00Z',
      message_tr: '',
      message_en: '',
      ...fields,
    });
    const totals = addDropped(
      addDropped(NO_DROPS, event({ rate_limited: 40 })),
      event({
        rate_limited: 10,
        behind: 7,
      }),
    );
    expect(totals).toEqual({ rate_limited: 50, slow_client: 0, parse_budget: 0, behind: 7 });
    expect(droppedTotal(totals)).toBe(57);
    expect(NO_DROPS.rate_limited).toBe(0);
  });
});

describe('hand-overs to Arama', () => {
  it('opens a line’s source five minutes either side of it', () => {
    expect(searchAround(line(1))).toEqual({
      q: 'source:fortigate',
      range: { from: '2026-10-13T03:55:33.000Z', to: '2026-10-13T04:05:33.000Z' },
    });
    expect(searchAround(line(1, { received: null }))).toBeNull();
  });

  it('opens a gap from the last line seen to now, with a minute to spare', () => {
    const now = new Date('2026-10-13T05:00:00Z');
    expect(searchGap('type:dhcp', '2026-10-13T07:30:00+03:00', now)).toEqual({
      q: 'type:dhcp',
      range: { from: '2026-10-13T04:29:00.000Z', to: '2026-10-13T05:01:00.000Z' },
    });
    expect(searchGap('', null, now).range).toEqual({
      from: '2026-10-13T04:00:00.000Z',
      to: '2026-10-13T05:01:00.000Z',
    });
  });
});

describe('highlight rules', () => {
  class MemoryStorage {
    items = new Map<string, string>();
    getItem(key: string) {
      return this.items.get(key) ?? null;
    }
    setItem(key: string, value: string) {
      this.items.set(key, value);
    }
    removeItem(key: string) {
      this.items.delete(key);
    }
  }

  const throwing = (): Storage => {
    throw new DOMException('denied', 'SecurityError');
  };

  it('are kept in sessionStorage, never localStorage', () => {
    const rules: HighlightRule[] = [{ match: 'deny', color: 'red' }];
    expect(saveRules(rules)).toBe(true);
    expect(window.sessionStorage.getItem(RULES_KEY)).toBe(JSON.stringify(rules));
    expect(window.localStorage.getItem(RULES_KEY)).toBeNull();
    expect(loadRules()).toEqual(rules);
    expect(saveRules([])).toBe(true);
    expect(window.sessionStorage.getItem(RULES_KEY)).toBeNull();
  });

  it('work without storage: a throwing sessionStorage reads none and reports the failure', () => {
    expect(loadRules(throwing)).toEqual([]);
    expect(saveRules([{ match: 'deny', color: 'red' }], throwing)).toBe(false);
  });

  it('read back only valid rules, at most 10', () => {
    const storage = new MemoryStorage();
    const many = Array.from({ length: 12 }, (_, index) => ({ match: `t${index}`, color: 'teal' }));
    storage.setItem(
      RULES_KEY,
      JSON.stringify([{ match: '', color: 'red' }, { match: 'x', color: 'purple' }, 7, ...many]),
    );
    const rules = loadRules(() => storage as unknown as Storage);
    expect(rules).toHaveLength(MAX_RULES);
    expect(rules[0]).toEqual({ match: 't0', color: 'teal' });
    storage.setItem(RULES_KEY, '{not json');
    expect(loadRules(() => storage as unknown as Storage)).toEqual([]);
  });

  it('tell field rules from text rules', () => {
    expect(ruleField('srcip:172.20.41.5')).toEqual({ field: 'srcip', value: '172.20.41.5' });
    expect(ruleField('Action: deny')).toEqual({ field: 'action', value: 'deny' });
    expect(ruleField('action:deny')).toEqual({ field: 'action', value: 'deny' });
    expect(ruleField('ftp:example.com')).toBeNull();
    expect(ruleField('deny')).toBeNull();
  });

  it('mark the whole line for field:value (equal, or a prefix with *)', () => {
    const rules: HighlightRule[] = [
      { match: 'action:accept', color: 'green' },
      { match: 'srcip:172.20.42.*', color: 'yellow' },
      { match: 'action:DENY', color: 'red' },
    ];
    expect(lineColor(line(1), rules)).toBe('yellow');
    expect(lineColor(line(1, { summary: { action: 'deny' } }), rules)).toBe('red');
    expect(lineColor(line(1, { summary: {} }), rules)).toBeNull();
    expect(
      lineColor(line(1, { source: 'unknown-198.51.100.23' }), [
        { match: 'source:unknown-*', color: 'sky' },
      ]),
    ).toBe('sky');
    expect(lineColor(line(1), [{ match: 'type:traffic', color: 'blue' }])).toBe('blue');
    expect(
      lineColor(line(1, { summary: { vlan_id: 42 } }), [{ match: 'vlan:42', color: 'teal' }]),
    ).toBe('teal');
  });

  it('mark every occurrence of a text, the first rule winning, ASCII case folded', () => {
    expect(highlightText('Deny deny ALLOW', [{ match: 'deny', color: 'red' }])).toEqual([
      { text: 'Deny', color: 'red' },
      { text: ' ', color: null },
      { text: 'deny', color: 'red' },
      { text: ' ALLOW', color: null },
    ]);
    expect(
      highlightText('abcdef', [
        { match: 'bcd', color: 'yellow' },
        { match: 'def', color: 'green' },
      ]),
    ).toEqual([
      { text: 'a', color: null },
      { text: 'bcd', color: 'yellow' },
      { text: 'ef', color: 'green' },
    ]);
    // Turkish letters keep their place (only ASCII letters are folded, as in search).
    expect(asciiLower('İSTANBUL Iğdır')).toBe('İstanbul iğdır');
    expect(highlightText('kullanıcı=AYŞE', [{ match: 'ayşe', color: 'blue' }])).toEqual([
      { text: 'kullanıcı=AYŞE', color: null },
    ]);
    expect(highlightText('user=Ayşe', [{ match: 'ayşe', color: 'blue' }])).toEqual([
      { text: 'user=', color: null },
      { text: 'Ayşe', color: 'blue' },
    ]);
    expect(highlightText('x', [{ match: 'action:deny', color: 'red' }])).toEqual([
      { text: 'x', color: null },
    ]);
  });
});
