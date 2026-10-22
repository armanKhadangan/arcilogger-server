import { buildUrl } from '@/lib/api';
import type { DeviceOut } from '@/lib/api-types';
import { DEVICES, need } from '@/test/samples';

import {
  DEFAULT_VIEW,
  deviceChanges,
  deviceEditProblems,
  deviceEditValues,
  EDIT_LIMITS,
  deviceQuery,
  isFiltered,
  pageRange,
  parseTags,
  personQuery,
  sortingOf,
  tabFrom,
  viewFromSearch,
  viewToSearch,
  viewWithSorting,
  withTab,
} from './devices-model';

const search = (text: string) => new URLSearchParams(text);

describe('page address', () => {
  it('reads the tab, defaulting to the device list', () => {
    expect(tabFrom(search(''))).toBe('devices');
    expect(tabFrom(search('tab=persons'))).toBe('persons');
    expect(tabFrom(search('tab=transfer'))).toBe('transfer');
    expect(tabFrom(search('tab=nope'))).toBe('devices');
  });

  it('writes the tab and keeps the filters', () => {
    expect(withTab(search('unknown=true'), 'departments').toString()).toBe(
      'unknown=true&tab=departments',
    );
    expect(withTab(search('tab=persons&type=pc'), 'devices').toString()).toBe('type=pc');
  });

  it('reads the filters and the sort order, ignoring what it does not know', () => {
    expect(viewFromSearch(search(''))).toEqual(DEFAULT_VIEW);
    expect(
      viewFromSearch(
        search(
          'unknown=true&type=phone&vlan=60&randomized=true&infrastructure=false&sort=mac&order=asc',
        ),
      ),
    ).toEqual({
      unknown: true,
      type: 'phone',
      vlan: 60,
      randomized: true,
      infrastructure: false,
      sort: 'mac',
      order: 'asc',
    });
    expect(
      viewFromSearch(search('unknown=maybe&type=toaster&vlan=9999&sort=vendor&order=up')),
    ).toEqual(DEFAULT_VIEW);
  });

  it('writes only what differs from the default, never the search text', () => {
    expect(viewToSearch(DEFAULT_VIEW, search('tab=devices')).toString()).toBe('tab=devices');
    const view = { ...DEFAULT_VIEW, unknown: true, vlan: 43, sort: 'name' as const };
    const written = viewToSearch(view, search('type=pc&q=Ay%C5%9Fe'));
    // The old filters are replaced; a stray `q` stays untouched but is never written by the view.
    expect(written.toString()).toBe('q=Ay%C5%9Fe&unknown=true&vlan=43&sort=name&order=desc');
    expect(viewFromSearch(written)).toEqual(view);
    expect(viewToSearch(view, search('')).has('q')).toBe(false);
  });

  it('knows when the list is filtered', () => {
    expect(isFiltered(DEFAULT_VIEW, '')).toBe(false);
    expect(isFiltered({ ...DEFAULT_VIEW, sort: 'mac', order: 'asc' }, '  ')).toBe(false);
    expect(isFiltered(DEFAULT_VIEW, 'MUH')).toBe(true);
    expect(isFiltered({ ...DEFAULT_VIEW, randomized: false }, '')).toBe(true);
  });
});

describe('filters → query string of GET /devices', () => {
  it('sends the default sort, the page size and offset only without filters', () => {
    expect(buildUrl('/devices', { ...deviceQuery(DEFAULT_VIEW, '', 0, 50) })).toBe(
      '/api/v1/devices?sort=last_seen&order=desc&limit=50&offset=0',
    );
  });

  it('sends every filter, the trimmed search text and the page offset', () => {
    const view = {
      unknown: true,
      type: 'pc' as const,
      vlan: 43,
      randomized: false,
      infrastructure: false,
      sort: 'mac' as const,
      order: 'asc' as const,
    };
    expect(buildUrl('/devices', { ...deviceQuery(view, '  PLN-PC ', 2, 50) })).toBe(
      '/api/v1/devices?q=PLN-PC&unknown=true&type=pc&vlan=43&randomized=false&infrastructure=false&sort=mac&order=asc&limit=50&offset=100',
    );
  });

  it('person list: search, state, department, paging', () => {
    expect(
      buildUrl('/persons', {
        ...personQuery({ search: ' Ayşe ', active: true, departmentId: 7 }, 1, 50),
      }),
    ).toBe('/api/v1/persons?q=Ay%C5%9Fe&active=true&department_id=7&limit=50&offset=50');
    expect(
      buildUrl('/persons', {
        ...personQuery({ search: '', active: null, departmentId: null }, 0, 20),
      }),
    ).toBe('/api/v1/persons?limit=20&offset=0');
  });

  it('table sorting ↔ view (removing the sort returns to the default)', () => {
    expect(sortingOf(DEFAULT_VIEW)).toEqual([{ id: 'last_seen', desc: true }]);
    expect(viewWithSorting(DEFAULT_VIEW, [{ id: 'mac', desc: false }])).toMatchObject({
      sort: 'mac',
      order: 'asc',
    });
    expect(viewWithSorting({ ...DEFAULT_VIEW, sort: 'mac', order: 'asc' }, [])).toMatchObject({
      sort: 'last_seen',
      order: 'desc',
    });
    // A column the API cannot sort by is ignored.
    expect(viewWithSorting(DEFAULT_VIEW, [{ id: 'vendor', desc: false }])).toMatchObject({
      sort: 'last_seen',
    });
  });

  it('page range of the counter', () => {
    expect(pageRange(0, 50, 312)).toEqual({ first: 1, last: 50, total: 312 });
    expect(pageRange(300, 12, 312)).toEqual({ first: 301, last: 312, total: 312 });
    expect(pageRange(0, 0, 0)).toBeNull();
  });
});

describe('device edit form', () => {
  // Captured: "Planlama-PC-14", a PC without tags or notes.
  const device: DeviceOut = need(
    DEVICES.list.items.find((item) => item.mac === '00:00:5e:00:53:57'),
    '00:00:5e:00:53:57',
  );

  it('splits tags at commas and line breaks, without blanks or duplicates', () => {
    expect(parseTags(' muhasebe, kat-2 ,,muhasebe\nyazıcı ')).toEqual([
      'muhasebe',
      'kat-2',
      'yazıcı',
    ]);
    expect(parseTags('  ')).toEqual([]);
  });

  it('sends only what changed; a cleared text becomes null, "unknown" type null', () => {
    const values = deviceEditValues(device);
    expect(deviceChanges(device, values)).toEqual({});
    expect(
      deviceChanges(device, {
        ...values,
        name: '  ',
        type: 'unknown',
        infrastructure: true,
        tags: 'kat-1, kat-1',
        notes: ' Yeni not ',
      }),
    ).toEqual({ name: null, type: null, infrastructure: true, tags: ['kat-1'], notes: 'Yeni not' });
  });

  it('checks the API limits before sending', () => {
    const values = deviceEditValues(device);
    expect(deviceEditProblems(values)).toEqual({});
    expect(
      deviceEditProblems({
        ...values,
        name: 'x'.repeat(EDIT_LIMITS.name + 1),
        tags: Array.from({ length: EDIT_LIMITS.tags + 1 }, (_, index) => `t${index}`).join(','),
        notes: 'satır 1\nsatır 2',
      }),
    ).toEqual({ name: 'devices.detail.tooLong', tags: 'devices.detail.tooManyTags' });
    expect(
      deviceEditProblems({
        ...values,
        name: 'Yazıcı\u0007',
        tags: 'y'.repeat(EDIT_LIMITS.tag + 1),
        notes: 'n'.repeat(EDIT_LIMITS.notes + 1),
      }),
    ).toEqual({
      name: 'validation.controlCharacters',
      tags: 'devices.detail.tagTooLong',
      notes: 'devices.detail.tooLong',
    });
  });
});
