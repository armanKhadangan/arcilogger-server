import en from '@/locales/en.json';
import tr from '@/locales/tr.json';
import type { SealActionResult } from '@/lib/settings-types';
import { SEALS, need } from '@/test/samples';

import {
  calendarDays,
  calendarStatus,
  dayDate,
  defaultDay,
  gapReason,
  legalGrade,
  monthDate,
  monthKey,
  toneOf,
} from './seal-states';

const OCTOBER = need(SEALS.calendars['2026-10'], 'October calendar');

describe('calendar states', () => {
  it('maps every state the API knows; only "verified" is green', () => {
    const states = OCTOBER.legend.map((item) => item.state);
    expect(states).toEqual([
      'verified',
      'sealed',
      'late',
      'pending',
      'failed',
      'open',
      'future',
      'no_data',
      'deleted',
    ]);
    expect(states.filter((state) => calendarStatus(state) === 'sealed')).toEqual(['verified']);
    expect(calendarStatus('something new')).toBe('noData');
  });

  it("the calendar's labels are the API's labels", () => {
    // Exceptions: "pending" keeps the shorter shared status text, future days are disabled.
    const labels = tr.status as Record<string, string>;
    const labelsEn = en.status as Record<string, string>;
    for (const item of OCTOBER.legend) {
      if (item.state === 'pending' || item.state === 'future') continue;
      expect(labels[calendarStatus(item.state)], item.state).toBe(item.label_tr);
      expect(labelsEn[calendarStatus(item.state)], item.state).toBe(item.label_en);
    }
  });

  it('tones', () => {
    expect(OCTOBER.legend.map((item) => toneOf(item.tone))).toEqual([
      'success',
      'warning',
      'warning',
      'warning',
      'danger',
      'neutral',
      'neutral',
      'neutral',
      'neutral',
    ]);
  });

  it('days and the server messages of the captured October', () => {
    const { days, notes } = calendarDays(OCTOBER, 'tr');
    expect(days['2026-10-04']).toBe('sealed');
    expect(days['2026-10-06']).toBe('late');
    expect(days['2026-10-07']).toBe('unverified');
    expect(days['2026-10-11']).toBe('failed');
    expect(days['2026-10-12']).toBe('pending');
    expect(days['2026-10-13']).toBe('open');
    expect(days['2026-10-01']).toBe('noData');
    // The spec 12.4 sentence from the error code, never the core's English exception text.
    expect(notes['2026-10-11']).toMatch(/^Mühürleme başarısız: .* ulaşılamadı\./);
    expect(notes['2026-10-11']).not.toMatch(/unreachable/);
    expect(notes['2026-10-01']).toBeUndefined();
    expect(calendarDays(OCTOBER, 'en').notes['2026-10-11']).toMatch(/^Sealing failed/);
    expect(defaultDay(OCTOBER)).toBe('2026-10-12');
    expect(defaultDay(need(SEALS.calendars['2026-09'], 'September calendar'))).toBeNull();
  });

  it('month and day dates', () => {
    expect(monthKey(monthDate('2026-10'))).toBe('2026-10');
    expect(dayDate('2026-10-04').getDate()).toBe(4);
    expect(monthKey(dayDate('2026-01-31'))).toBe('2026-01');
  });
});

describe('day details in Turkish', () => {
  it('translates the gap reasons the sealer writes into the manifest (the captured 09.10)', () => {
    const gaps = need(SEALS.days['2026-10-09'], '09.10 detail').gaps;
    expect(gaps.length).toBeGreaterThan(0);
    for (const gap of gaps) expect(gapReason(gap.reason)).not.toBeNull();
    expect(gapReason('no messages > 60 min')).toEqual({
      key: 'sealing.gaps.reasons.longer',
      minutes: 60,
    });
    expect(gapReason('no messages')).toEqual({ key: 'sealing.gaps.reasons.none' });
    // Unknown wording: the manifest's own text is shown.
    expect(gapReason('clock jumped')).toBeNull();
    expect(tr.sealing.gaps.reasons.longer).toContain('{{minutes}}');
    expect(en.sealing.gaps.reasons.longer).toContain('{{minutes}}');
  });

  it('has a label for every seal-task state of the core (arcilogger_core.seal)', () => {
    const states = Object.values(SEALS.days)
      .map((day) => day.seal_state?.status)
      .filter((status): status is string => Boolean(status));
    expect(new Set(states)).toEqual(new Set(['sealed', 'pending']));
    // `skipped`: an empty day of a long run without any log (seal.max_consecutive_empty_days).
    for (const state of ['sealed', 'pending', 'failed', 'waiting', 'skipped']) {
      expect(tr.sealing.state.states).toHaveProperty(state);
      expect(en.sealing.state.states).toHaveProperty(state);
    }
  });

  it('never claims legal validity: the positive badge names the server only', () => {
    expect(tr.sealing.token.legal).toBe('Kamu SM üretim sunucusu');
    expect(`${tr.sealing.token.legal} ${en.sealing.token.legal}`).not.toMatch(/yasal|legal/i);
    // A production day whose root is not listed is neither confirmed nor a test stamp.
    const claimed = `${tr.sealing.token.claimed} ${en.sealing.token.claimed}`;
    expect(claimed).not.toMatch(/yasal|legal|test|mock/i);
    expect(`${tr.sealing.day.rootUnconfirmed} ${en.sealing.day.rootUnconfirmed}`).not.toMatch(
      /test|mock/i,
    );
  });
});

describe('"Şimdi mühürle"', () => {
  it("names the Seal task's outcome of the captured request in both languages", () => {
    const result = SEALS.seal_now_done.result as SealActionResult | null;
    expect(SEALS.seal_now_done.kind).toBe('action.seal');
    expect(result?.status).toBe('sealed');
    for (const locale of [tr, en]) {
      expect(Object.keys(locale.sealing.sealNow.outcome).sort()).toEqual([
        'deleted',
        'failed',
        'pending',
        'sealed',
        'skipped',
        'waiting',
      ]);
    }
  });
});

describe('legal grade of a day stamp (decision 5)', () => {
  it('never calls a claimed Kamu SM production stamp a test stamp', () => {
    expect(legalGrade({ legal_grade: true, legal_grade_claimed: true })).toBe('legal');
    expect(legalGrade({ legal_grade: false, legal_grade_claimed: true })).toBe('claimed');
    expect(legalGrade({ legal_grade: false, legal_grade_claimed: false })).toBe('test');
    expect(legalGrade({ legal_grade: false })).toBe('test');
    // A production claim on a stamp of ARCILOGGER's test TSA stays a test stamp.
    expect(legalGrade({ legal_grade: false, legal_grade_claimed: true, test_stamp: true })).toBe(
      'test',
    );
    expect(legalGrade({ legal_grade: false, legal_grade_claimed: true, test_stamp: false })).toBe(
      'claimed',
    );
  });

  it('has a label in both languages for every case', () => {
    for (const locale of [tr, en]) {
      expect(locale.sealing.token.claimed).toBeTruthy();
      expect(locale.sealing.day.rootUnconfirmed).toContain('{{provider}}');
      expect(locale.evidence.detail.rootUnconfirmed).toContain('tsa.legal_root_fingerprints');
    }
  });
});
