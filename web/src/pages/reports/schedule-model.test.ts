import i18n from '@/lib/i18n';
import { REPORTS } from '@/test/samples';

import {
  checkPeriod,
  coveredPeriod,
  EMPTY_PERIOD,
  formTiming,
  MAX_PERIOD_DAYS,
  nextRun,
  ownPeriod,
  parseClock,
  runBody,
  scheduleChanges,
  scheduleCreateBody,
  scheduleFormValues,
  scheduleTiming,
  validateSchedule,
  withKind,
  type ScheduleFormValues,
  type ScheduleTiming,
} from './schedule-model';
import { kindLabel, nextRunText, ownPeriodText, periodText, whenText } from './schedule-text';

/** Tuesday 13.10.2026 15:00 site time (the samples' clock). */
const NOW = new Date('2026-10-13T15:00:00+03:00');
const t = i18n.getFixedT('tr');
const en = i18n.getFixedT('en');

function form(overrides: Partial<ScheduleFormValues> = {}): ScheduleFormValues {
  return {
    ...scheduleFormValues(null),
    name: 'Gün sonu raporu',
    recipients: ['tolga@ornek.com.tr'],
    ...overrides,
  };
}

function timing(overrides: Partial<ScheduleTiming>): ScheduleTiming {
  return { kind: 'end_of_day', time: '18:30', weekday: null, monthday: null, ...overrides };
}

function local(value: Date | null): string {
  if (!value) return 'null';
  return new Date(value.getTime() + 3 * 3_600_000).toISOString().slice(0, 16).replace('T', ' ');
}

describe('schedule form defaults (owner decision 2026-10-20)', () => {
  it('a new schedule is person-level, not pseudonymised, end of day at 18:30', () => {
    const values = scheduleFormValues(null);
    expect(values).toMatchObject({
      kind: 'end_of_day',
      time: '18:30',
      personLevel: true,
      pseudonymize: false,
      enabled: true,
      formats: { email_html: true, pdf: true, xlsx: false },
    });
  });

  it('choosing another kind moves an untouched time to that kind’s default', () => {
    expect(withKind(form(), 'weekly').time).toBe('07:15');
    expect(withKind(form({ time: '19:05' }), 'monthly').time).toBe('19:05');
    expect(withKind(form({ kind: 'monthly', time: '7:30' }), 'full_day').time).toBe('07:00');
  });

  it('reads times like the rest of the app (24 hours)', () => {
    expect(parseClock('7:15')).toBe('07:15');
    expect(parseClock('07.15')).toBe('07:15');
    expect(parseClock('18:30:00')).toBe('18:30');
    expect(parseClock('24:00')).toBeNull();
    expect(parseClock('7 pm')).toBeNull();
  });
});

describe('schedule form validation', () => {
  it('accepts a complete form', () => {
    expect(validateSchedule(form())).toEqual({});
  });

  it.each([
    [{ name: '  ' }, { name: 'validation.required' }],
    [{ name: 'x'.repeat(201) }, { name: 'validation.tooLong' }],
    [{ name: 'Rapor‮' }, { name: 'validation.controlCharacters' }],
    [{ time: '' }, { time: 'validation.required' }],
    [{ time: '25:10' }, { time: 'reports.validation.time' }],
    // 00:00 → 00:00 is no period; a full-day report at 00:00 is fine.
    [{ time: '0:00' }, { time: 'reports.validation.endOfDayMidnight' }],
    [{ kind: 'full_day' as const, time: '00:00' }, {}],
    [{ kind: 'weekly' as const, weekday: '' }, { weekday: 'validation.required' }],
    [{ kind: 'weekly' as const, weekday: '8' }, { weekday: 'validation.required' }],
    [{ kind: 'monthly' as const, monthday: '29' }, { monthday: 'reports.validation.monthday' }],
    [{ kind: 'monthly' as const, monthday: '0' }, { monthday: 'reports.validation.monthday' }],
    [{ recipients: [] }, { recipients: 'reports.validation.recipientsForEnabled' }],
    [{ pending: 'tolga@' }, { recipients: 'validation.email' }],
    [
      { formats: { email_html: false, pdf: false, xlsx: false } },
      { formats: 'reports.validation.formats' },
    ],
  ])('%j → %j', (overrides, errors) => {
    expect(validateSchedule(form(overrides as Partial<ScheduleFormValues>))).toEqual(errors);
  });

  it('a disabled schedule may wait for its recipients (as the seeded ones do)', () => {
    expect(validateSchedule(form({ enabled: false, recipients: [] }))).toEqual({});
  });

  it('counts a valid address still in the input as a recipient', () => {
    const values = form({ recipients: [], pending: 'muhasebe@ornek.com.tr' });
    expect(validateSchedule(values)).toEqual({});
    expect(scheduleCreateBody(values).recipients).toEqual(['muhasebe@ornek.com.tr']);
  });
});

describe('request bodies', () => {
  it('create: the kind with only its own day field, the time normalised', () => {
    expect(scheduleCreateBody(form({ kind: 'weekly', weekday: '3', time: '7:15' }))).toEqual({
      name: 'Gün sonu raporu',
      kind: 'weekly',
      enabled: true,
      time: '07:15',
      weekday: 3,
      recipients: ['tolga@ornek.com.tr'],
      formats: { email_html: true, pdf: true, xlsx: false },
      person_level: true,
      pseudonymize: false,
    });
  });

  it('update: only what changed; the kind travels with its day fields', () => {
    const weekly = REPORTS.schedules.find((item) => item.kind === 'weekly');
    if (!weekly) throw new Error('sample missing');
    expect(scheduleChanges(weekly, scheduleFormValues(weekly))).toEqual({});
    const values = withKind(scheduleFormValues(weekly), 'monthly');
    expect(scheduleChanges(weekly, { ...values, monthday: '15' })).toEqual({
      kind: 'monthly',
      weekday: null,
      monthday: 15,
      time: '07:30',
    });
    expect(
      scheduleChanges(weekly, {
        ...scheduleFormValues(weekly),
        recipients: ['ik@ornek.com.tr'],
        enabled: true,
        pseudonymize: true,
      }),
    ).toEqual({ recipients: ['ik@ornek.com.tr'], enabled: true, pseudonymize: true });
  });

  it('run now: the schedule’s own period unless one is given', () => {
    expect(runBody(true)).toEqual({ send: true });
    expect(runBody(false, { from: 'a', to: 'b' })).toEqual({ from: 'a', to: 'b', send: false });
  });
});

describe('next run and period (site time)', () => {
  it.each([
    [timing({}), '2026-10-13 18:30', '13.10.2026 00:00–18:30'],
    [timing({ kind: 'full_day', time: '07:00' }), '2026-10-14 07:00', '13.10.2026'],
    [
      timing({ kind: 'weekly', time: '07:15', weekday: 1 }),
      '2026-10-19 07:15',
      '12.10.2026 – 18.10.2026',
    ],
    [
      timing({ kind: 'monthly', time: '07:30', monthday: 1 }),
      '2026-11-01 07:30',
      '01.10.2026 – 31.10.2026',
    ],
    // Exactly now is not "next": the run of 15:00 today has started.
    [
      timing({ kind: 'monthly', time: '15:00', monthday: 13 }),
      '2026-11-13 15:00',
      '01.10.2026 – 31.10.2026',
    ],
    [
      timing({ kind: 'weekly', time: '15:01', weekday: 2 }),
      '2026-10-13 15:01',
      '05.10.2026 – 11.10.2026',
    ],
  ])('%j', (value, at, period) => {
    const next = nextRun(value, NOW);
    expect(local(next)).toBe(at);
    if (!next) throw new Error('no next run');
    const covered = coveredPeriod(value.kind, next);
    expect(periodText(covered.from, covered.to)).toBe(period);
  });

  it('crosses the year', () => {
    const december = new Date('2026-12-15T09:00:00+03:00');
    const next = nextRun(timing({ kind: 'monthly', time: '07:30', monthday: 1 }), december);
    expect(local(next)).toBe('2027-01-01 07:30');
    if (!next) throw new Error('no next run');
    const covered = coveredPeriod('monthly', next);
    expect(periodText(covered.from, covered.to)).toBe('01.12.2026 – 31.12.2026');
  });

  it('the default period of "run now" and a preview is the API\'s: the kind\'s own, now', () => {
    const today = ownPeriod('end_of_day', NOW);
    expect(periodText(today.from, today.to)).toBe('13.10.2026 00:00–15:00');
    const yesterday = ownPeriod('full_day', NOW);
    expect(periodText(yesterday.from, yesterday.to)).toBe('12.10.2026');
    expect(ownPeriodText('weekly', NOW, t)).toBe(
      'Şimdi çalışırsa kapsadığı dönem: 05.10.2026 – 11.10.2026',
    );
    expect(ownPeriodText('monthly', NOW, en)).toBe('If run now it covers: 01.09.2026 – 30.09.2026');
  });

  it('shows the next run as the form changes (Turkish and English)', () => {
    const values = form({ kind: 'weekly', weekday: '1', time: '07:15' });
    expect(nextRunText(formTiming(values), NOW, t)).toBe(
      'Sonraki çalışma: 19.10.2026 07:15 · kapsadığı dönem: 12.10.2026 – 18.10.2026',
    );
    expect(nextRunText(formTiming(values), NOW, en)).toBe(
      'Next run: 19.10.2026 07:15 · covers: 12.10.2026 – 18.10.2026',
    );
    expect(nextRunText(formTiming(form({ time: '31:00' })), NOW, t)).toBe(
      'Saat ve gün geçerli olunca sonraki çalışma burada görünür.',
    );
  });

  it('describes timings and kinds', () => {
    const [endOfDay, , weekly, monthly] = REPORTS.schedules;
    if (!endOfDay || !weekly || !monthly) throw new Error('samples missing');
    expect(whenText(endOfDay, t)).toBe('Her gün 18:30');
    expect(whenText(weekly, t)).toBe('Pazartesi, 07:15');
    expect(whenText(monthly, t)).toBe('Ayın 1. günü, 07:30');
    expect(kindLabel('custom', t)).toBe('Özel dönem');
    expect(scheduleTiming(weekly)).toEqual({
      kind: 'weekly',
      time: '07:15',
      weekday: 1,
      monthday: null,
    });
  });

  it('writes periods with times when they do not cover whole days', () => {
    expect(periodText('2026-10-11T21:00:00Z', '2026-10-12T21:00:00Z')).toBe('12.10.2026');
    expect(periodText('2026-10-12T05:00:00Z', '2026-10-13T08:30:00Z')).toBe(
      '12.10.2026 08:00 – 13.10.2026 11:30',
    );
  });
});

describe('custom period of a manual run or a preview', () => {
  it('needs both days in the Turkish format', () => {
    expect(checkPeriod(EMPTY_PERIOD, NOW).errors).toEqual({
      fromDay: 'validation.required',
      toDay: 'validation.required',
    });
    expect(
      checkPeriod(
        { ...EMPTY_PERIOD, fromDay: '32.10.2026', toDay: '13.10.2026', fromTime: '8' },
        NOW,
      ).errors,
    ).toEqual({ fromDay: 'reports.validation.day', fromTime: 'reports.validation.time' });
  });

  it('an empty end time means the whole last day, up to now for today', () => {
    expect(
      checkPeriod({ ...EMPTY_PERIOD, fromDay: '01.10.2026', toDay: '07.10.2026' }, NOW),
    ).toEqual({
      errors: {},
      from: '2026-10-01T00:00:00+03:00',
      to: '2026-10-07T21:00:00.000Z',
    });
    expect(
      checkPeriod({ ...EMPTY_PERIOD, fromDay: '13.10.2026', toDay: '13.10.2026' }, NOW).to,
    ).toBe('2026-10-13T12:00:00.000Z');
    expect(
      checkPeriod(
        { fromDay: '12.10.2026', fromTime: '08:00', toDay: '12.10.2026', toTime: '17:30' },
        NOW,
      ),
    ).toEqual({ errors: {}, from: '2026-10-12T08:00:00+03:00', to: '2026-10-12T17:30:00+03:00' });
  });

  it('the end must follow the start and must not be in the future', () => {
    expect(
      checkPeriod({ ...EMPTY_PERIOD, fromDay: '12.10.2026', toDay: '11.10.2026' }, NOW).errors,
    ).toEqual({ toDay: 'reports.validation.periodOrder' });
    expect(
      checkPeriod(
        { fromDay: '13.10.2026', fromTime: '', toDay: '13.10.2026', toTime: '16:00' },
        NOW,
      ).errors,
    ).toEqual({ toDay: 'reports.validation.periodFuture' });
  });

  it(`covers at most ${MAX_PERIOD_DAYS} days (the API's limit)`, () => {
    expect(
      checkPeriod({ ...EMPTY_PERIOD, fromDay: '13.07.2026', toDay: '12.10.2026' }, NOW).errors,
    ).toEqual({});
    expect(
      checkPeriod({ ...EMPTY_PERIOD, fromDay: '12.07.2026', toDay: '12.10.2026' }, NOW).errors,
    ).toEqual({ toDay: 'reports.validation.periodTooLong' });
  });
});
