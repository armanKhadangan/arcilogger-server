/**
 * Hand-made answers of the search API for the unit tests of `pages/search` (shapes of
 * `lib/search-types.ts`, contract 2.10; the example world of the repository). Not imported by
 * the app.
 */

import type {
  CatalogueField,
  FieldCatalogue,
  SearchPage,
  SearchRow,
  SearchStats,
} from '@/lib/search-types';

function field(
  name: string,
  kind: string,
  aliases: string[],
  label: [string, string],
  indexed: string[] = [],
): CatalogueField {
  return {
    name,
    aliases,
    kind,
    label_tr: label[0],
    label_en: label[1],
    help_tr: `${label[0]} alanı.`,
    help_en: `The ${label[1]} field.`,
    tables: ['messages'],
    indexed,
    examples: [`${name}:x`],
  };
}

export const CATALOGUE: FieldCatalogue = {
  fields: [
    field('source', 'source', ['kaynak'], ['Kaynak', 'Source'], ['messages']),
    field('type', 'kind', ['tur', 'tip'], ['Tür', 'Type'], ['messages']),
    field('srcip', 'ip', ['kaynakip', 'istemci'], ['Kaynak IP', 'Source IP'], ['fg_traffic']),
    field('sender', 'ip', ['gonderen'], ['Gönderen', 'Sender']),
    field('severity', 'severity', ['onem'], ['Önem', 'Severity']),
    field('action', 'token', ['eylem'], ['Eylem', 'Action']),
    field('domain', 'domain', ['alanadi', 'site'], ['Alan adı', 'Domain'], ['fg_web']),
    field('vlan', 'vlan', [], ['VLAN', 'VLAN'], ['fg_traffic']),
    field('department', 'department', ['departman'], ["Departman (VLAN'a göre)", 'Department (by VLAN)']),
    field('text', 'freetext', ['metin'], ['Metin', 'Text']),
  ],
  kinds: [
    { key: 'traffic', label_tr: 'Trafik', label_en: 'Traffic' },
    { key: 'web', label_tr: 'Web filtresi', label_en: 'Web filter' },
    { key: 'dhcp', label_tr: 'DHCP', label_en: 'DHCP' },
  ],
  sources: [
    { name: 'core-r1', type: 'mikrotik', network: false },
    { name: 'fortigate', type: 'fortigate', network: false },
  ],
  limits: {
    max_chars: 1000,
    max_clauses: 16,
    max_values: 20,
    max_terms: 5,
    max_days: 31,
    page_size_max: 500,
  },
};

const EMPTY_SUMMARY = {
  srcip: null,
  srcport: null,
  dstip: null,
  dstport: null,
  natip: null,
  natport: null,
  action: null,
  domain: null,
  url: null,
  app: null,
  category: null,
  user: null,
  mac: null,
  proto: null,
  policy: null,
  interface: null,
  vlan_id: null,
};

/** A row (a FortiGate traffic line unless `overrides` says otherwise). */
export function row(overrides: Partial<SearchRow> = {}): SearchRow {
  return {
    key: 'fg_traffic:2026-10-09T10:30:00Z:25',
    table: 'fg_traffic',
    time: '2026-10-09T10:30:00Z',
    time_received: '2026-10-09T10:30:00.003Z',
    source: 'fortigate',
    sender: '172.20.10.254',
    kind: 'traffic',
    type: 'traffic',
    subtype: 'forward',
    summary: {
      ...EMPTY_SUMMARY,
      srcip: '172.20.42.57',
      srcport: 61001,
      dstip: '203.0.113.10',
      dstport: 443,
      natip: '198.51.100.7',
      natport: 61001,
      action: 'accept',
      proto: 'tcp',
      vlan_id: 42,
    },
    text: 'srcip=172.20.42.57 dstip=203.0.113.10 action="accept"',
    raw: { source: 'fortigate', day: '2026-10-09', line: 27, offset: 10260 },
    parse_error: null,
    ...overrides,
  };
}

export const EMPTY = EMPTY_SUMMARY;

/** A first page of `q` over the site day 09.10.2026. */
export function page(overrides: Partial<SearchPage> = {}): SearchPage {
  return {
    search_id: 'ornek-arama-01',
    expires_at: '2026-10-09T12:00:00Z',
    query: { normalized: 'srcip:172.20.42.57', clauses: [], text: [] },
    range: { from: '2026-10-09T00:00:00+03:00', to: '2026-10-10T00:00:00+03:00' },
    plan: { driver: 'typed', tables: ['fg_traffic'], kinds: ['traffic'], selective: true, rollup_eligible: false },
    items: [row()],
    next_cursor: null,
    scan: {
      complete: true,
      scanned_from: '2026-10-09T00:00:00+03:00',
      scanned_to: '2026-10-10T00:00:00+03:00',
      windows: 5,
      elapsed_ms: 14,
    },
    notices: [],
    ...overrides,
  };
}

/** Statistics of {@link page}: hourly buckets of the day, two kinds, two sources. */
export function stats(overrides: Partial<SearchStats> = {}): SearchStats {
  const buckets = Array.from({ length: 24 }, (_, hour) => ({
    start: `2026-10-09T${String(hour).padStart(2, '0')}:00:00+03:00`,
    count: hour === 13 ? 3 : hour === 11 ? 1 : 0,
  }));
  const facet = (values: [string, string, string, number][]) => ({
    status: 'ok' as const,
    values: values.map(([value, label_tr, label_en, count]) => ({ value, label_tr, label_en, count })),
    other: 0,
  });
  return {
    search_id: 'ornek-arama-01',
    mode: 'rollup',
    complete: true,
    covered_from: '2026-10-09T00:00:00+03:00',
    covered_to: '2026-10-10T00:00:00+03:00',
    total: { value: 4, exact: true },
    histogram: { bucket_seconds: 3600, buckets },
    facets: {
      source: facet([
        ['fortigate', 'fortigate', 'fortigate', 3],
        ['core-r1', 'core-r1', 'core-r1', 1],
      ]),
      kind: facet([
        ['traffic', 'Trafik', 'Traffic', 3],
        ['dhcp', 'DHCP', 'DHCP', 1],
      ]),
      action: facet([['accept', 'accept', 'accept', 3]]),
      vlan: facet([['42', 'VLAN 42 (Planlama)', 'VLAN 42 (Planlama)', 4]]),
      department: facet([['İdari İşler', 'İdari İşler', 'İdari İşler', 1]]),
    },
    sampled: false,
    sample_rows: null,
    notices: [],
    elapsed_ms: 8,
    ...overrides,
  };
}
