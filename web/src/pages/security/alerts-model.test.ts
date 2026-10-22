import en from '@/locales/en.json';
import tr from '@/locales/tr.json';
import { ALERTS } from '@/test/samples';

import {
  ALERT_STATES,
  actionOf,
  activeAlertOf,
  alertHref,
  alertIdFrom,
  alertQuery,
  alertSummaryText,
  alertViewFromSearch,
  alertViewToSearch,
  alertViewWithSorting,
  canTransition,
  closesActive,
  DEFAULT_ALERT_VIEW,
  detailRows,
  isActive,
  isAlertViewFiltered,
  nextStates,
  normalizeRule,
  noteProblem,
  ruleName,
  sortingOfAlerts,
  SYSTEM_REASONS,
} from './alerts-model';

describe("alert workflow (spec 11, the API's rule)", () => {
  it('offers every change the API allows from each state', () => {
    expect(nextStates('open')).toEqual(['acknowledged', 'resolved', 'dismissed']);
    expect(nextStates('acknowledged')).toEqual(['resolved', 'dismissed', 'open']);
    expect(nextStates('resolved')).toEqual(['open', 'dismissed']);
    expect(nextStates('dismissed')).toEqual(['open', 'resolved']);
  });

  it('refuses every other change (the same state and İnceleniyor from a closed alert)', () => {
    const allowed = new Set([
      'open>acknowledged',
      'open>resolved',
      'open>dismissed',
      'acknowledged>open',
      'acknowledged>resolved',
      'acknowledged>dismissed',
      'resolved>open',
      'resolved>dismissed',
      'dismissed>open',
      'dismissed>resolved',
    ]);
    for (const from of ALERT_STATES)
      for (const to of ALERT_STATES)
        expect(canTransition(from, to), `${from} → ${to}`).toBe(allowed.has(`${from}>${to}`));
  });

  it('names the action of each change', () => {
    expect(actionOf('open', 'acknowledged')).toBe('acknowledge');
    expect(actionOf('open', 'resolved')).toBe('resolve');
    expect(actionOf('acknowledged', 'dismissed')).toBe('dismiss');
    expect(actionOf('acknowledged', 'open')).toBe('release');
    expect(actionOf('resolved', 'open')).toBe('reopen');
    expect(actionOf('dismissed', 'open')).toBe('reopen');
    expect(actionOf('dismissed', 'resolved')).toBe('markResolved');
    expect(actionOf('resolved', 'dismissed')).toBe('markDismissed');
  });

  it('warns only when an active alert is closed by hand', () => {
    expect(closesActive('open', 'resolved')).toBe(true);
    expect(closesActive('acknowledged', 'dismissed')).toBe(true);
    expect(closesActive('open', 'acknowledged')).toBe(false);
    expect(closesActive('resolved', 'dismissed')).toBe(false);
  });

  it('finds the active alert a refused re-open names', () => {
    expect(activeAlertOf(ALERTS.errors.alert_key_active?.body.error.details)).toBe(7);
    expect(activeAlertOf({ alert_id: '7' })).toBeNull();
    expect(activeAlertOf({ alert_id: 0 })).toBeNull();
    expect(activeAlertOf(null)).toBeNull();
  });

  it('has a text for the reason of the captured automatic close', () => {
    const reason = ALERTS.detail_auto_resolved.events.find((event) => event.by === 'system');
    expect(reason?.text).toBe('credit_ok');
    expect(SYSTEM_REASONS).toContain(reason?.text);
  });

  it('every known reason code has a Turkish and an English text', () => {
    for (const code of SYSTEM_REASONS) {
      expect(tr.security.reasons).toHaveProperty(code);
      expect(en.security.reasons).toHaveProperty(code);
    }
  });

  it('counts open and acknowledged alerts as open work', () => {
    expect(ALERT_STATES.filter(isActive)).toEqual(['open', 'acknowledged']);
  });

  it('checks notes: optional on a state change, required when added alone', () => {
    expect(noteProblem('', true)).toBeUndefined();
    expect(noteProblem('   ')).toBe('validation.required');
    expect(noteProblem('Satır 1\nSatır 2\tsekme')).toBeUndefined();
    expect(noteProblem('x'.repeat(2001))).toBe('security.validation.noteTooLong');
    expect(noteProblem('ğ'.repeat(2000))).toBeUndefined();
    expect(noteProblem('fatura‮xcod.exe')).toBe('validation.controlCharacters');
    expect(noteProblem('zil\u0007')).toBe('validation.controlCharacters');
  });
});

describe('the address of the security page', () => {
  it('reads filters, sort and the open alert, ignoring nonsense', () => {
    const params = new URLSearchParams(
      'state=acknowledged&severity=high&rule=r15&from=2026-10-13&to=2026-10-01&sort=severity&order=asc&alert=41',
    );
    expect(alertViewFromSearch(params)).toEqual({
      state: 'acknowledged',
      severity: 'high',
      rule: 'R15',
      from: '2026-10-01',
      to: '2026-10-13',
      sort: 'severity',
      order: 'asc',
    });
    expect(alertIdFrom(params)).toBe(41);
    expect(
      alertViewFromSearch(
        new URLSearchParams('state=gone&severity=extreme&from=2026-02-30&sort=size&rule=;drop'),
      ),
    ).toEqual(DEFAULT_ALERT_VIEW);
    expect(alertIdFrom(new URLSearchParams('alert=0'))).toBeNull();
  });

  it('writes only what differs from the default and keeps other parameters', () => {
    const next = alertViewToSearch(
      { ...DEFAULT_ALERT_VIEW, state: 'open', rule: 'R20-disk' },
      new URLSearchParams('alert=7&severity=low'),
    );
    expect(next.toString()).toBe('alert=7&state=open&rule=R20-disk');
    const sorted = alertViewToSearch(
      { ...DEFAULT_ALERT_VIEW, sort: 'severity', order: 'desc' },
      new URLSearchParams(),
    );
    expect(sorted.toString()).toBe('sort=severity&order=desc');
  });

  it('turns days into site-time bounds (end exclusive) and pages', () => {
    const query = alertQuery(
      { ...DEFAULT_ALERT_VIEW, from: '2026-10-01', to: '2026-10-13', severity: 'medium' },
      '172.20',
      2,
      50,
    );
    expect(query).toEqual({
      sort: 'time',
      order: 'desc',
      limit: 50,
      offset: 100,
      severity: 'medium',
      q: '172.20',
      from: '2026-10-01T00:00:00+03:00',
      to: '2026-10-14T00:00:00+03:00',
    });
    expect(alertQuery(DEFAULT_ALERT_VIEW, '', 0, 50)).toEqual({
      sort: 'time',
      order: 'desc',
      limit: 50,
      offset: 0,
    });
  });

  it('knows whether anything filters the list', () => {
    expect(isAlertViewFiltered(DEFAULT_ALERT_VIEW, '')).toBe(false);
    expect(isAlertViewFiltered({ ...DEFAULT_ALERT_VIEW, sort: 'severity' }, '')).toBe(false);
    expect(isAlertViewFiltered(DEFAULT_ALERT_VIEW, ' sw ')).toBe(true);
    expect(isAlertViewFiltered({ ...DEFAULT_ALERT_VIEW, from: '2026-10-01' }, '')).toBe(true);
  });

  it('maps table sorting to the API and back', () => {
    expect(sortingOfAlerts(DEFAULT_ALERT_VIEW)).toEqual([{ id: 'time', desc: true }]);
    expect(alertViewWithSorting(DEFAULT_ALERT_VIEW, [{ id: 'severity', desc: false }])).toEqual({
      ...DEFAULT_ALERT_VIEW,
      sort: 'severity',
      order: 'asc',
    });
    expect(
      alertViewWithSorting({ ...DEFAULT_ALERT_VIEW, sort: 'severity', order: 'asc' }, []),
    ).toEqual(DEFAULT_ALERT_VIEW);
  });

  it('normalises typed rule ids', () => {
    expect(normalizeRule(' r15 ')).toBe('R15');
    expect(normalizeRule('R20-seal')).toBe('R20-seal');
    expect(normalizeRule('new_source')).toBe('new_source');
    expect(normalizeRule('R15; DROP')).toBe('');
    expect(alertHref(41)).toBe('/security?alert=41');
  });
});

describe('alert texts', () => {
  const first = ALERTS.detail;

  it('uses the interface language, the title when a summary is empty', () => {
    expect(alertSummaryText(first, 'tr')).toContain('switch-3 kaynağından');
    expect(alertSummaryText(first, 'en')).toContain('No message from switch-3');
    expect(alertSummaryText({ ...first, summary_tr: ' ' }, 'tr')).toBe(first.title);
    expect(ruleName(first, 'tr')).toBe('Log kaynağı sessiz');
    expect(ruleName({ ...first, rule_name_en: '' }, 'en')).toBe('R15');
  });

  it('shows details as text rows, nested values as JSON', () => {
    expect(
      detailRows({
        source: 'switch-3',
        threshold_minutes: 15,
        stale: true,
        macs: ['a', 'b'],
        x: null,
      }),
    ).toEqual([
      ['source', 'switch-3'],
      ['threshold_minutes', '15'],
      ['stale', 'true'],
      ['macs', '["a","b"]'],
      ['x', '—'],
    ]);
  });
});
