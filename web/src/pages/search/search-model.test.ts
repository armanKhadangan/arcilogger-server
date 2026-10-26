import { ApiError } from '@/lib/api';
import {
  SEARCH_AUTO_CONTINUES as AUTO_CONTINUES,
  SEARCH_VIEW_ROWS as MAX_VIEW_ROWS,
} from '@/lib/search-hooks';

import {
  addClause,
  completions,
  evidenceHandoff,
  fieldNames,
  normalizeMac,
  problemOf,
  quoteValue,
  removeClause,
  rowSummary,
  scanState,
  splitClauses,
  termToQuery,
} from './search-model';
import { CATALOGUE, EMPTY, page, row, stats } from './test-data';

describe('the top bar term as a query', () => {
  it.each([
    ['172.20.41.23', 'ip:172.20.41.23'],
    [' 172.20.41.0/24 ', 'ip:172.20.41.0/24'],
    ['00:00:5E:00:53:21', 'mac:00:00:5e:00:53:21'],
    ['00-00-5e-00-53-21', 'mac:00:00:5e:00:53:21'],
    ['0000.5e00.5321', 'mac:00:00:5e:00:53:21'],
    ['00005E005321', 'mac:00:00:5e:00:53:21'],
    ['www.Example.com', 'domain:www.example.com'],
    ['example.com.', 'domain:example.com'],
    ['ornek-şirket.example.com', 'domain:ornek-şirket.example.com'],
    ['PLN-PC14', '"PLN-PC14"'],
    ['login failure', '"login failure"'],
    ['172.20.41.300', '"172.20.41.300"'],
    ['say "hello" \\ there', '"say \\"hello\\" \\\\ there"'],
    ['srcip:172.20.41.5', '"srcip:172.20.41.5"'],
    ['   ', ''],
  ])('%s → %s', (term, query) => {
    expect(termToQuery(term)).toBe(query);
  });

  it('reads a MAC address only in a known form', () => {
    expect(normalizeMac('00:00:5e:00:53')).toBeNull();
    expect(normalizeMac('00:00-5e:00:53:21')).toBeNull();
    expect(normalizeMac('00:00:5e:00:53:2g')).toBeNull();
  });
});

describe('clauses of the query text', () => {
  it('writes a value bare when it can, else quoted (literal)', () => {
    expect(quoteValue('fortigate')).toBe('fortigate');
    expect(quoteValue('ap-172.20.60.2')).toBe('ap-172.20.60.2');
    expect(quoteValue('İdari İşler')).toBe('"İdari İşler"');
    expect(quoteValue('a,b')).toBe('"a,b"');
    expect(quoteValue('x*')).toBe('"x*"');
    expect(quoteValue('')).toBe('""');
  });

  it('finds the clauses, quotes and escapes respected', () => {
    const q =
      'srcip:172.20.42.57 -action:deny "login failure" NOT type:web department:"İdari \\"İşler\\""';
    expect(
      splitClauses(q).map(({ text, negated, field, value }) => [text, negated, field, value]),
    ).toEqual([
      ['srcip:172.20.42.57', false, 'srcip', '172.20.42.57'],
      ['-action:deny', true, 'action', 'deny'],
      ['"login failure"', false, null, '"login failure"'],
      ['type:web', true, 'type', 'web'],
      ['department:"İdari \\"İşler\\""', false, 'department', '"İdari \\"İşler\\""'],
    ]);
    const [first] = splitClauses('  Kaynak:core-r1');
    expect(first).toMatchObject({ start: 2, end: 16, field: 'kaynak' });
  });

  it('adds a facet clause once, and replaces the opposite one', () => {
    const names = fieldNames(CATALOGUE.fields);
    expect(addClause('srcip:172.20.42.57', 'type', 'traffic', false, names)).toBe(
      'srcip:172.20.42.57 type:traffic',
    );
    expect(addClause('srcip:172.20.42.57 type:traffic', 'type', 'traffic', false, names)).toBe(
      'srcip:172.20.42.57 type:traffic',
    );
    // "Hariç tut" after "only this": the clause is turned round, not doubled.
    expect(addClause('srcip:172.20.42.57 type:traffic', 'type', 'traffic', true, names)).toBe(
      'srcip:172.20.42.57 -type:traffic',
    );
    expect(addClause('-tur:dhcp', 'type', 'dhcp', false, names)).toBe('type:dhcp');
    expect(addClause('', 'department', 'İdari İşler', false, names)).toBe(
      'department:"İdari İşler"',
    );
    expect(addClause('departman:"idari işler"', 'department', 'İdari İşler', false, names)).toBe(
      'departman:"idari işler"',
    );
  });

  it('removes a clause by its span', () => {
    expect(removeClause('a:1  b:2 c:3', 5, 8)).toBe('a:1 c:3');
  });
});

describe('completions', () => {
  it('offers field names and aliases while there is no colon', () => {
    const found = completions('srcip:172.20.42.57 ka', 21, CATALOGUE, undefined, 'tr');
    expect(found).toEqual({
      start: 19,
      end: 21,
      items: [
        { insert: 'kaynak:', label: 'kaynak:', detail: 'Kaynak' },
        { insert: 'kaynakip:', label: 'kaynakip:', detail: 'Kaynak IP' },
      ],
    });
    expect(completions('-ey', 3, CATALOGUE, undefined, 'en')?.items).toEqual([
      { insert: '-eylem:', label: 'eylem:', detail: 'Action' },
    ]);
    expect(completions('srcip', 5, CATALOGUE, undefined, 'tr')).toBeNull();
    expect(completions('', 0, CATALOGUE, undefined, 'tr')).toBeNull();
    expect(completions('src', 3, undefined, undefined, 'tr')).toBeNull();
  });

  it('offers the values found for a field, the kinds and the sources', () => {
    const facets = stats().facets;
    expect(
      completions('type:', 5, CATALOGUE, facets, 'tr')?.items.map((item) => item.insert),
    ).toEqual(['type:traffic', 'type:dhcp', 'type:web']);
    expect(completions('kaynak:co', 9, CATALOGUE, facets, 'tr')?.items).toEqual([
      { insert: 'kaynak:core-r1', label: 'core-r1' },
    ]);
    expect(completions('department:', 11, CATALOGUE, facets, 'tr')?.items).toEqual([
      { insert: 'department:"İdari İşler"', label: 'İdari İşler' },
    ]);
    expect(completions('vlan:4', 6, CATALOGUE, facets, 'en')?.items).toEqual([
      { insert: 'vlan:42', label: '42', detail: 'VLAN 42 (Planlama)' },
    ]);
    expect(completions('nosuch:x', 8, CATALOGUE, facets, 'tr')).toBeNull();
  });
});

describe('a row summary', () => {
  it('says who went where, how and through which address', () => {
    expect(rowSummary(row())).toBe(
      '172.20.42.57:61001 → 203.0.113.10:443 · tcp · accept · NAT 198.51.100.7:61001',
    );
  });

  it('names the domain of web records, and the MAC of DHCP records', () => {
    const web = row({
      kind: 'web',
      summary: {
        ...EMPTY,
        srcip: '172.20.42.165',
        domain: 'www.example.com',
        action: 'blocked',
        category: 'Haber',
        url: '/gundem',
      },
    });
    expect(rowSummary(web)).toBe('172.20.42.165 → www.example.com · blocked · Haber · /gundem');
    const dhcp = row({
      kind: 'dhcp',
      summary: { ...EMPTY, srcip: '172.20.42.57', mac: '00:00:5e:00:53:57', action: 'assigned' },
    });
    expect(rowSummary(dhcp)).toBe('172.20.42.57 · assigned · 00:00:5e:00:53:57');
  });

  it('falls back to the record text, with invisible characters made visible', () => {
    expect(rowSummary(row({ summary: EMPTY, text: 'fatura‮xcod.exe' }))).toBe(
      'fatura\\u202excod.exe',
    );
    expect(rowSummary(row({ summary: EMPTY, text: null }))).toBe('—');
  });
});

describe('the state of a scan', () => {
  it('tells more rows from a scan cut by its time budget', () => {
    expect(scanState(page(), 0, 1)).toBe('complete');
    expect(scanState(page({ next_cursor: 'c1' }), 0, 100)).toBe('more');
    const partial = page({
      next_cursor: 'frontier',
      scan: { ...page().scan, complete: false },
    });
    expect(scanState(partial, 0, 3)).toBe('continuing');
    expect(scanState(partial, AUTO_CONTINUES - 1, 3)).toBe('continuing');
    expect(scanState(partial, AUTO_CONTINUES, 3)).toBe('paused');
    expect(scanState(partial, 0, MAX_VIEW_ROWS)).toBe('capped');
    expect(scanState(page({ next_cursor: 'c1' }), 0, MAX_VIEW_ROWS)).toBe('capped');
  });
});

describe('refusals', () => {
  it('give the span of a query problem', () => {
    const error = new ApiError({
      status: 422,
      code: 'search_syntax',
      messageTr: 'Sorgu okunamadı: kapanmamış tırnak işareti (7. karakter).',
      messageEn: 'The query cannot be read: a quotation mark is not closed (character 7).',
      details: { start: 6, end: 13, params: { position: 7 } },
    });
    expect(problemOf(error)).toEqual({
      code: 'search_syntax',
      message_tr: error.messageTr,
      message_en: error.messageEn,
      start: 6,
      end: 13,
    });
    expect(
      problemOf(
        new ApiError({
          status: 422,
          code: 'validation_error',
          messageTr: '',
          messageEn: '',
          details: [],
        }),
      ),
    ).toBeNull();
    expect(problemOf(new Error('x'))).toBeNull();
  });
});

describe('the hand-over to the evidence wizard', () => {
  it('lists the lines and the days they span', () => {
    const handed = evidenceHandoff(
      page({ query: { normalized: 'type:dhcp', clauses: [], text: [] } }),
      {
        refs: [
          { source: 'fortigate', day: '2026-10-09', line: 27, offset: 10260 },
          { source: 'core-r1', day: '2026-10-08', line: 5, offset: 635 },
        ],
        truncated: true,
        complete: false,
        unsealed_days: ['2026-10-09', '2026-10-08'],
        sha256: 'a'.repeat(64),
      },
    );
    expect(handed).toEqual({
      extract: [
        { day: '2026-10-09', source: 'fortigate', line: 27 },
        { day: '2026-10-08', source: 'core-r1', line: 5 },
      ],
      from_day: '2026-10-08',
      to_day: '2026-10-09',
      truncated: true,
      complete: false,
      unsealed_days: ['2026-10-08', '2026-10-09'],
      query: 'type:dhcp',
    });
  });

  it('takes the search range as the days when no line matched (its end is exclusive)', () => {
    const handed = evidenceHandoff(page(), {
      refs: [],
      truncated: false,
      complete: true,
      unsealed_days: [],
      sha256: 'b'.repeat(64),
    });
    expect([handed.from_day, handed.to_day]).toEqual(['2026-10-09', '2026-10-09']);
  });
});
