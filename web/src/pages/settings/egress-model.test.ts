import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import i18n from '@/lib/i18n';

import {
  applyPrefill,
  draftClaim,
  egressWarnings,
  emptyRow,
  isPrivateOrShared,
  prefillOptions,
  rowsFromValue,
  storedRefusal,
  validateEgresses,
  valueFromRows,
  type EgressRow,
} from './egress-model';

interface EgressCase {
  name: string;
  value: unknown;
  code?: string;
  normalised?: unknown;
}

/**
 * The exit validation cases (contract 2.8.1). A HAND MOCK of `tests/fixtures/egress-cases.json`,
 * which `db/tests/test_egress.py` reads too once WP-C1 has written it; the second step of WP-E
 * reads that file here instead.
 */
const CASES = (
  JSON.parse(
    readFileSync(join(process.cwd(), 'src', 'test', 'egress-cases.hand.json'), 'utf8'),
  ) as { cases: EgressCase[] }
).cases;

/** The server's codes of the exit rules (`arcilogger_db.egress.ERROR_CODES` and the shared two). */
const SERVER_CODES = [
  'setting_type',
  'setting_too_many',
  'setting_egress_name',
  'setting_egress_field',
  'setting_egress_dynamic',
  'setting_egress_mode',
  'setting_egress_label',
  'setting_egress_address',
  'setting_ipv6_not_supported',
  'setting_egress_overlap',
  'setting_egress_direct',
  'setting_egress_not_logged',
  'setting_egress_internal',
  'setting_egress_port_preservation',
  'setting_egress_catch_all',
  'setting_egress_public_private',
  'setting_egress_isp_nat_direct',
  'setting_interface_invalid',
];

describe('validateEgresses mirrors the server rules (shared case list)', () => {
  it.each(CASES.map((item) => [item.name, item] as const))('%s', (_name, item) => {
    const result = validateEgresses(item.value);
    if (item.code !== undefined) expect(result.refusal?.code).toBe(item.code);
    else expect(result).toEqual({ value: item.normalised });
  });

  it('the case list produces every code of the rules, and each has a text in both languages', () => {
    const produced = new Set(CASES.flatMap((item) => (item.code ? [item.code] : [])));
    expect([...produced].sort()).toEqual([...SERVER_CODES].sort());
    for (const code of SERVER_CODES)
      for (const lang of ['tr', 'en']) {
        const text = i18n.getFixedT(lang)(`network.egress.codes.${code}`, {
          limit: 16,
          expected_tr: 'nesne',
          expected_en: 'an object',
        });
        expect(text, `${lang} ${code}`).not.toContain('network.egress');
        expect(text).not.toMatch(/\{\{/);
      }
  });

  it('names the exit and the field of a refusal (the editor shows it there)', () => {
    const result = validateEgresses({
      'hat-1': { mode: 'direct', public: ['203.0.113.200'] },
      'hat-2': { mode: 'isp_nat', public: ['100.64.12.9'] },
    });
    expect(result.refusal).toEqual({
      code: 'setting_egress_public_private',
      facts: {},
      exit: 'hat-2',
      field: 'public',
    });
  });

  it('classifies only RFC 1918 and RFC 6598 space as private or shared', () => {
    for (const address of ['10.1.2.3', '172.20.42.57', '192.168.1.1', '100.64.13.9'])
      expect(isPrivateOrShared(address), address).toBe(true);
    for (const address of ['203.0.113.200', '198.51.100.77', '192.0.2.1'])
      expect(isPrivateOrShared(address), address).toBe(false);
  });
});

function row(fields: Partial<EgressRow>): EgressRow {
  return emptyRow([], { key: fields.name ?? 'x', ...fields });
}

describe('the editor rows', () => {
  it('open a stored value and give it back unchanged', () => {
    const stored = {
      dmz: {
        label: "DMZ'ye NAT",
        mode: 'internal',
        public: [],
        public_dynamic: false,
        transips: ['172.20.99.1'],
        interfaces: ['dmz'],
        port_preservation: 'unknown',
      },
      'hat-2': {
        label: 'Yedek hat',
        mode: 'isp_nat',
        public: ['198.51.100.77'],
        public_dynamic: true,
        transips: ['100.64.13.9'],
        interfaces: ['wan2'],
        port_preservation: 'preserved',
      },
    };
    const rows = rowsFromValue(stored);
    expect(rows.map((item) => item.key)).toEqual(['dmz', 'hat-2']);
    expect(rows[1]?.transips).toBe('100.64.13.9');
    expect(valueFromRows(rows)).toEqual({ value: stored });
  });

  it('send only the fields the mode takes; what was typed stays in the draft', () => {
    const typed = row({
      name: 'dmz',
      mode: 'internal',
      public: '203.0.113.9',
      transips: '172.20.99.1',
      public_dynamic: true,
      port_preservation: 'preserved',
    });
    expect(valueFromRows([typed]).value?.dmz).toEqual({
      label: '',
      mode: 'internal',
      public: [],
      public_dynamic: false,
      transips: ['172.20.99.1'],
      interfaces: [],
      port_preservation: 'unknown',
    });
    expect(typed.public).toBe('203.0.113.9');
  });

  it('split addresses at spaces and commas, interfaces only at line breaks', () => {
    const value = valueFromRows([
      row({
        name: 'hat-1',
        public: '203.0.113.200, 203.0.113.208/29\n 203.0.113.200/32 ',
        interfaces: 'port 1\na,b\n wan1 \n',
      }),
    ]).value;
    expect(value?.['hat-1']?.public).toEqual(['203.0.113.200', '203.0.113.208/29']);
    // A FortiGate interface name may hold a space or a comma (the interface rule).
    expect(value?.['hat-1']?.interfaces).toEqual(['port 1', 'a,b', 'wan1']);
  });

  it('give labels and interface names back exactly as stored', () => {
    const stored = {
      'hat-1': {
        label: ' Ana hat ',
        mode: 'direct',
        public: ['203.0.113.200'],
        public_dynamic: false,
        transips: [],
        interfaces: ['a,b', 'port 1;2'],
        port_preservation: 'unknown',
      },
    };
    const rows = rowsFromValue(stored);
    expect(rows[0]?.stored).toBeUndefined();
    expect(rows[0]?.label).toBe(' Ana hat ');
    expect(valueFromRows(rows)).toEqual({ value: stored });
  });

  it('keep a stored exit the rows cannot give back as stored, and send it as stored', () => {
    const hat1 = {
      label: 'Ana hat',
      mode: 'direct',
      public: ['203.0.113.200'],
      public_dynamic: false,
      transips: [],
      interfaces: ['wan1'],
      port_preservation: 'unknown',
    };
    // An unknown mode (written around the rules, or by a later version before a rollback).
    const misafir = { mode: 'notlogged', public: ['198.51.100.90'] };
    const rows = rowsFromValue({ misafir, 'hat-1': hat1 });
    expect(rows[0]?.stored).toEqual(misafir);
    expect(rows[1]?.stored).toBeUndefined();
    expect(storedRefusal(rows[0] as EgressRow)).toMatchObject({
      code: 'setting_egress_mode',
      field: 'mode',
    });
    // Never read into a direct line: the whole value is refused on that row.
    expect(valueFromRows(rows).refusal).toMatchObject({
      code: 'setting_egress_mode',
      row: 'misafir',
    });
    // Not a line to warn about, join or claim an address with.
    expect(egressWarnings(rows).rows.misafir).toEqual([]);
    expect(draftClaim({ transip: '198.51.100.90', dstintf: null }, rows)).toBeNull();
    // Removed, the rest is sent.
    expect(valueFromRows(rows.slice(1))).toEqual({ value: { 'hat-1': hat1 } });
  });

  it.each([
    ['an unknown field', { mode: 'direct', public: ['203.0.113.200'], vlan: 5 }],
    ['public_dynamic not a boolean', { mode: 'isp_nat', public_dynamic: 'yes' }],
    ['an unknown port preservation', { mode: 'isp_nat', port_preservation: 'kept' }],
    ['a label with a line break', { label: 'Ana\nhat', mode: 'direct', public: ['203.0.113.200'] }],
    ['public not a list', { mode: 'direct', public: '203.0.113.200' }],
    ['an interface with a trailing space', { mode: 'isp_nat', interfaces: ['wan1 '] }],
    [
      'a field its mode does not take',
      { mode: 'direct', public: ['203.0.113.200'], transips: ['100.64.12.9'] },
    ],
    ['no object', 'direct'],
  ])('keep %s as stored, refused with the rule of the server', (_, item) => {
    const rows = rowsFromValue({ x: item });
    expect(rows[0]?.stored).toEqual(item);
    const code = validateEgresses({ x: item }).refusal?.code;
    expect(code).toBeDefined();
    expect(valueFromRows(rows).refusal).toMatchObject({ code, row: 'x' });
  });

  it('keep a stored name the rules refuse', () => {
    const rows = rowsFromValue({ 'Hat 1': { mode: 'direct', public: ['203.0.113.200'] } });
    expect(rows[0]?.stored).toBeDefined();
    expect(valueFromRows(rows).refusal).toMatchObject({
      code: 'setting_egress_name',
      row: 'Hat 1',
    });
  });

  it('refuse an empty or doubled name on its row', () => {
    expect(
      valueFromRows([row({ key: 'new-1', name: ' ', public: '203.0.113.200' })]).refusal,
    ).toMatchObject({ code: 'setting_egress_name', row: 'new-1', field: 'name' });
    const twice = [
      row({ key: 'a', name: 'hat-1', public: '203.0.113.200' }),
      row({ key: 'b', name: 'hat-1', public: '203.0.113.201' }),
    ];
    expect(valueFromRows(twice).refusal).toMatchObject({
      code: 'egress_duplicate_name',
      row: 'b',
    });
  });
});

describe('the editor warnings (contract 2.13)', () => {
  it('two lines while one has no interfaces', () => {
    const rows = [
      row({ name: 'hat-1', public: '203.0.113.200', interfaces: 'wan1' }),
      row({ name: 'hat-2', mode: 'isp_nat', public: '198.51.100.77', transips: '100.64.13.9' }),
    ];
    expect(egressWarnings(rows).editor).toEqual(['interfaces']);
    expect(
      egressWarnings([rows[0] as EgressRow, { ...(rows[1] as EgressRow), interfaces: 'wan2' }])
        .editor,
    ).toEqual([]);
    // A not-logged line and a NAT towards the DMZ are no lines the lookup separates.
    expect(
      egressWarnings([
        rows[0] as EgressRow,
        row({ name: 'misafir', mode: 'not_logged', public: '198.51.100.90' }),
        row({ name: 'dmz', mode: 'internal', transips: '172.20.99.1' }),
      ]).editor,
    ).toEqual([]);
  });

  it('an ISP NAT exit: no public address, dynamic, catch-all, a public NAT address', () => {
    const rows = [
      row({ key: 'a', name: 'tanimsiz', mode: 'isp_nat' }),
      row({
        key: 'b',
        name: 'hat-2',
        mode: 'isp_nat',
        public: '198.51.100.77',
        public_dynamic: true,
        transips: '100.64.13.9',
      }),
      row({
        key: 'c',
        name: 'hat-3',
        mode: 'isp_nat',
        public: '198.51.100.78',
        transips: '198.51.100.10',
      }),
    ];
    const found = egressWarnings(rows).rows;
    expect(found.a).toEqual(['noPublic', 'catchAll']);
    expect(found.b).toEqual(['dynamic']);
    expect(found.c).toEqual(['publicNat']);
  });

  it('a converted exit without interfaces, only while its converted partner is beside it', () => {
    const genel = row({ key: 'genel-1', name: 'genel-1', public: '203.0.113.200' });
    const wan = row({ key: 'wan', name: 'wan', mode: 'isp_nat', transips: '100.64.12.9' });
    expect(egressWarnings([genel, wan]).rows.genel).toBeUndefined();
    expect(egressWarnings([genel, wan]).rows['genel-1']).toContain('converted');
    expect(egressWarnings([genel, wan]).rows.wan).toContain('converted');
    // The partner edited (interfaces entered): the old behaviour has ended for both.
    const edited = { ...wan, interfaces: 'wan1' };
    expect(egressWarnings([genel, edited]).rows['genel-1']).not.toContain('converted');
    expect(egressWarnings([genel, edited]).rows.wan).not.toContain('converted');
    // Alone (legacy (c)), a converted wan has no partner to be one line with.
    expect(egressWarnings([wan]).rows.wan).not.toContain('converted');
  });
});

describe('the observed panel prefill (contract 2.13)', () => {
  it('a public transip prefills Direct with the address and the row dstintf', () => {
    const observed = { transip: '203.0.113.200', dstintf: 'wan1' };
    expect(prefillOptions(observed, [])).toEqual([{ kind: 'direct' }]);
    const [added] = applyPrefill([], observed, { kind: 'direct' });
    expect(added).toMatchObject({
      name: 'hat-1',
      mode: 'direct',
      public: '203.0.113.200',
      interfaces: 'wan1',
      transips: '',
    });
  });

  it('100.64.13.9 prefills ISP NAT with an empty public list and the warning, or internal', () => {
    const observed = { transip: '100.64.13.9', dstintf: 'wan2' };
    expect(prefillOptions(observed, [])).toEqual([{ kind: 'isp_nat' }, { kind: 'internal' }]);
    const rows = applyPrefill([], observed, { kind: 'isp_nat' });
    expect(rows[0]).toMatchObject({
      mode: 'isp_nat',
      transips: '100.64.13.9',
      interfaces: 'wan2',
      public: '',
    });
    expect(egressWarnings(rows).rows[rows[0]?.key ?? '']).toContain('noPublic');
    const internal = applyPrefill([], observed, { kind: 'internal' })[0];
    expect(internal).toMatchObject({ name: 'nat-1', mode: 'internal', transips: '100.64.13.9' });
  });

  it('a new NAT address on an existing line is offered to that line first', () => {
    const hat2 = row({
      key: 'hat-2',
      name: 'hat-2',
      mode: 'isp_nat',
      public: '198.51.100.77',
      transips: '100.64.13.9',
      interfaces: 'wan2',
    });
    const observed = { transip: '100.64.13.10', dstintf: 'wan2' };
    const options = prefillOptions(observed, [hat2]);
    expect(options[0]).toEqual({ kind: 'join', row: 'hat-2', name: 'hat-2', field: 'transips' });
    const joined = applyPrefill([hat2], observed, options[0] ?? { kind: 'isp_nat' });
    expect(joined).toHaveLength(1);
    expect(joined[0]?.transips).toBe('100.64.13.9\n100.64.13.10');
    expect(draftClaim(observed, joined)?.name).toBe('hat-2');
    expect(draftClaim(observed, [hat2])).toBeNull();
  });

  it('names a new exit with the first free number', () => {
    const rows = [row({ key: 'hat-1', name: 'hat-1', public: '203.0.113.200' })];
    const added = applyPrefill(
      rows,
      { transip: '198.51.100.77', dstintf: 'wan2' },
      { kind: 'direct' },
    );
    expect(added.map((item) => item.name)).toEqual(['hat-1', 'hat-2']);
    expect(new Set(added.map((item) => item.key)).size).toBe(2);
  });
});
