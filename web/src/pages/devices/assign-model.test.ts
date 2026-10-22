import {
  ASSIGN_MAX_DEVICES,
  checkAssign,
  initialDraft,
  latestAssignedSince,
  siteLocalToIso,
  type AssignDraft,
} from './assign-model';

/** 13.10.2026 15:00 site time (the E2E browser clock). */
const NOW = new Date('2026-10-13T15:00:00+03:00');
const owner = { id: 1, name: 'Selin Kaya' };
const PC = { mac: '00:00:5e:00:53:57', assigned_since: '2026-03-12T09:00:00+03:00', person: owner };
const UNKNOWN = { mac: '3c:52:82:00:00:05', assigned_since: null, person: null };

function draft(patch: Partial<AssignDraft> = {}): AssignDraft {
  return { ...initialDraft(NOW), personId: 6, ...patch };
}

describe('start of validity', () => {
  it('starts as "now", with the fields showing the site time to the minute', () => {
    expect(initialDraft(NOW)).toEqual({
      personId: null,
      when: 'now',
      day: '13.10.2026',
      time: '15:00',
      note: '',
    });
    // Whatever the browser's time zone is: 12:00 UTC is 15:00 in Istanbul.
    expect(initialDraft(new Date('2026-10-13T12:00:00Z')).time).toBe('15:00');
  });

  it('turns a site-local day and time into an ISO moment with the site offset', () => {
    expect(siteLocalToIso('2026-03-12', '09:00:00')).toBe('2026-03-12T09:00:00+03:00');
    expect(siteLocalToIso('2026-12-31', '23:59:59')).toBe('2026-12-31T23:59:59+03:00');
    expect(Date.parse(siteLocalToIso('2026-10-13', '15:00:00'))).toBe(NOW.getTime());
  });

  it('knows the latest open assignment of the selection', () => {
    expect(latestAssignedSince([UNKNOWN])).toBeNull();
    expect(
      latestAssignedSince([PC, { ...UNKNOWN, assigned_since: '2026-10-05T10:15:00+03:00' }]),
    ).toBe('2026-10-05T10:15:00+03:00');
  });
});

describe('bulk assignment checks', () => {
  it('"now": no valid_from (the server clock decides), the note trimmed or left out', () => {
    expect(checkAssign('assign', draft(), [PC, UNKNOWN], NOW)).toEqual({
      errors: {},
      body: { macs: [PC.mac, UNKNOWN.mac], person_id: 6 },
    });
    expect(
      checkAssign('assign', draft({ note: '  Zimmet 2026/88 ' }), [UNKNOWN], NOW).body,
    ).toEqual({ macs: [UNKNOWN.mac], person_id: 6, note: 'Zimmet 2026/88' });
  });

  it('a given moment is sent with the site offset', () => {
    const check = checkAssign(
      'assign',
      draft({ when: 'at', day: '01.10.2026', time: '8:00' }),
      [UNKNOWN],
      NOW,
    );
    expect(check.body).toEqual({
      macs: [UNKNOWN.mac],
      person_id: 6,
      valid_from: '2026-10-01T08:00:00+03:00',
    });
  });

  it('needs a person, devices, and at most 500 of them', () => {
    expect(checkAssign('assign', draft({ personId: null }), [UNKNOWN], NOW)).toEqual({
      errors: { person: 'devices.assign.errors.person' },
      body: null,
    });
    expect(checkAssign('assign', draft(), [], NOW).errors.devices).toBe(
      'devices.assign.errors.noDevices',
    );
    const many = Array.from({ length: ASSIGN_MAX_DEVICES + 1 }, (_, index) => ({
      ...UNKNOWN,
      mac: `3c:52:82:00:${String(Math.floor(index / 256)).padStart(2, '0')}:${(index % 256).toString(16).padStart(2, '0')}`,
    }));
    expect(checkAssign('assign', draft(), many, NOW).errors.devices).toBe(
      'devices.assign.errors.tooMany',
    );
    expect(
      checkAssign('assign', draft(), many.slice(0, ASSIGN_MAX_DEVICES), NOW).body?.macs,
    ).toHaveLength(ASSIGN_MAX_DEVICES);
  });

  it('refuses what the server would refuse with 409: future, before the current assignment', () => {
    expect(
      checkAssign('assign', draft({ when: 'at', day: '13.10.2026', time: '15:01' }), [UNKNOWN], NOW)
        .errors,
    ).toEqual({ day: 'devices.assign.errors.future' });
    expect(
      checkAssign(
        'assign',
        draft({ when: 'at', day: '01.03.2026', time: '08:00' }),
        [PC, UNKNOWN],
        NOW,
      ).errors,
    ).toEqual({ day: 'devices.assign.errors.beforeCurrent' });
    // Exactly the start of the current assignment is allowed (it closes at that moment).
    expect(
      checkAssign('assign', draft({ when: 'at', day: '12.03.2026', time: '09:00' }), [PC], NOW).body
        ?.valid_from,
    ).toBe('2026-03-12T09:00:00+03:00');
  });

  it('checks the typed day and time', () => {
    expect(
      checkAssign('assign', draft({ when: 'at', day: '', time: '' }), [UNKNOWN], NOW).errors,
    ).toEqual({ day: 'validation.required', time: 'validation.required' });
    expect(
      checkAssign('assign', draft({ when: 'at', day: '31.02.2026', time: '25:00' }), [UNKNOWN], NOW)
        .errors,
    ).toEqual({ day: 'lookup.validation.date', time: 'lookup.validation.time' });
    // The fields do not matter while "now" is chosen.
    expect(checkAssign('assign', draft({ day: 'x', time: 'y' }), [UNKNOWN], NOW).errors).toEqual(
      {},
    );
  });

  it('removing an assignment needs no person but a device that has an owner', () => {
    expect(checkAssign('unassign', draft({ personId: null }), [PC, UNKNOWN], NOW).body).toEqual({
      macs: [PC.mac, UNKNOWN.mac],
      person_id: null,
    });
    expect(checkAssign('unassign', draft(), [UNKNOWN], NOW).errors).toEqual({
      devices: 'devices.assign.errors.nothingToUnassign',
    });
  });

  it('removing an assignment sends no note (it would have nowhere to go)', () => {
    const typed = draft({ personId: null, note: 'İade formu 42' });
    expect(checkAssign('unassign', typed, [PC], NOW)).toEqual({
      errors: {},
      body: { macs: [PC.mac], person_id: null },
    });
    // Not even a too long one is an error there; assigning still sends and checks it.
    const long = draft({ note: 'x'.repeat(501) });
    expect(checkAssign('unassign', long, [PC], NOW).errors).toEqual({});
    expect(checkAssign('assign', long, [PC], NOW).errors).toEqual({ note: 'validation.tooLong' });
    expect(checkAssign('assign', draft({ note: 'İade formu 42' }), [PC], NOW).body?.note).toBe(
      'İade formu 42',
    );
  });
});
