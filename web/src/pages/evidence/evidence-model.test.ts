import { EVIDENCE, need } from '@/test/samples';

import {
  daySpan,
  downloadBlockedReason,
  emptyWizard,
  extractText,
  isExpiredUnpurged,
  isUnfinished,
  parseLines,
  scopeDays,
  shownStatus,
  sourceList,
  sourcesSummary,
  toEvidenceBody,
  validateContent,
  validateScope,
  wizardFromSearch,
  type WizardDraft,
} from './evidence-model';

const TODAY = '2026-10-13';

function range(extra: Partial<WizardDraft> = {}): WizardDraft {
  return { ...emptyWizard(), from: '04.10.2026', to: '05.10.2026', ...extra };
}

describe('scope step', () => {
  it('starts with a day range, or with the lookup it was opened from', () => {
    expect(emptyWizard()).toMatchObject({ scope: 'range', lookupId: '', content: 'full_days' });
    expect(emptyWizard(7)).toMatchObject({ scope: 'lookup', lookupId: '7' });
  });

  it('accepts closed days in order', () => {
    expect(validateScope(range(), TODAY)).toEqual({});
    expect(validateScope(range({ from: '2026-10-04', to: '12.10.2026' }), TODAY)).toEqual({});
  });

  it('needs both days, finished (sealed) days, in order, at most 31', () => {
    expect(validateScope(range({ from: '', to: '' }), TODAY)).toEqual({
      from: 'validation.required',
      to: 'validation.required',
    });
    expect(validateScope(range({ to: '13.10.2026' }), TODAY)).toEqual({
      to: 'evidence.validation.notClosed',
    });
    expect(validateScope(range({ from: '05.10.2026', to: '04.10.2026' }), TODAY)).toEqual({
      to: 'evidence.validation.order',
    });
    expect(validateScope(range({ from: '01.09.2026', to: '02.10.2026' }), TODAY)).toEqual({
      to: 'evidence.validation.tooManyDays',
    });
    expect(validateScope(range({ from: '31.09.2026' }), TODAY)).toEqual({
      from: 'evidence.validation.date',
    });
  });

  it('checks the chosen sources and the reference', () => {
    expect(validateScope(range({ allSources: false }), TODAY)).toEqual({
      sources: 'evidence.validation.sourcesRequired',
    });
    expect(validateScope(range({ allSources: false, sources: 'FortiGate' }), TODAY)).toEqual({
      sources: 'evidence.validation.sourceName',
    });
    expect(
      validateScope(range({ allSources: false, sources: 'fortigate, core-r1' }), TODAY),
    ).toEqual({});
    expect(validateScope(range({ reference: 'x'.repeat(201) }), TODAY)).toEqual({
      reference: 'validation.tooLong',
    });
    expect(validateScope(range({ reference: 'Savcılık\u202e' }), TODAY)).toEqual({
      reference: 'validation.controlCharacters',
    });
  });

  it('a lookup scope needs a lookup id', () => {
    expect(validateScope({ ...emptyWizard(), scope: 'lookup' }, TODAY)).toEqual({
      lookupId: 'evidence.validation.lookup',
    });
    expect(validateScope(emptyWizard(3), TODAY)).toEqual({});
  });
});

describe('raw lines of an extract', () => {
  it('reads one line per row in either order, without duplicates', () => {
    expect(
      parseLines('2026-10-09 fortigate 2\n\nfortigate 09.10.2026 27\r\n2026-10-09 fortigate 2'),
    ).toEqual({
      lines: [
        { day: '2026-10-09', source: 'fortigate', line: 2 },
        { day: '2026-10-09', source: 'fortigate', line: 27 },
      ],
      bad: [],
    });
  });

  it('names the rows it cannot read', () => {
    expect(
      parseLines('2026-10-09 fortigate\nFortiGate 2026-10-09 2\n2026-10-09 core-r1 0').bad,
    ).toEqual([1, 2, 3]);
  });

  it('content step: an extract needs readable lines inside the range', () => {
    expect(validateContent(range())).toEqual({});
    expect(validateContent(range({ content: 'extract' }))).toEqual({
      lines: 'evidence.validation.linesRequired',
    });
    expect(validateContent(range({ content: 'extract', lines: 'fortigate 2' }))).toEqual({
      lines: 'evidence.validation.linesFormat',
    });
    expect(validateContent(range({ content: 'extract', lines: '2026-10-09 fortigate 2' }))).toEqual(
      { lines: 'evidence.validation.lineOutside' },
    );
    expect(validateContent(range({ content: 'extract', lines: '2026-10-05 fortigate 2' }))).toEqual(
      {},
    );
  });
});

describe('the lines of a search ("Kanıt paketine aktar")', () => {
  const extract = {
    extract: [
      { day: '2026-10-09', source: 'fortigate', line: 1234 },
      { day: '2026-10-10', source: 'core-r1', line: 7 },
    ],
    from_day: '2026-10-09',
    to_day: '2026-10-10',
    truncated: false,
    complete: true,
    unsealed_days: [],
    query: 'srcip:172.20.42.57',
  };

  it('fill the line box in the form typed lines have', () => {
    expect(extractText(extract.extract)).toBe('2026-10-09 fortigate 1234\n2026-10-10 core-r1 7');
    expect(parseLines(extractText(extract.extract))).toEqual({ lines: extract.extract, bad: [] });
    expect(extractText([])).toBe('');
  });

  it('open the wizard on their days with an extract of exactly these lines', () => {
    const draft = wizardFromSearch(extract);
    expect(draft).toMatchObject({
      scope: 'range',
      from: '09.10.2026',
      to: '10.10.2026',
      content: 'extract',
      allSources: true,
      password: false,
    });
    expect(validateScope(draft, TODAY)).toEqual({});
    expect(validateContent(draft)).toEqual({});
    expect(toEvidenceBody(draft)).toMatchObject({
      from: '2026-10-09',
      to: '2026-10-10',
      sources: null,
      extract: extract.extract,
      content: 'extract',
    });
  });

  it('are checked like typed ones: a day that is not closed yet is refused', () => {
    const draft = wizardFromSearch({ ...extract, to_day: TODAY });
    expect(validateScope(draft, TODAY)).toEqual({ to: 'evidence.validation.notClosed' });
  });
});

describe('toEvidenceBody', () => {
  it('a day range with full days of some sources', () => {
    expect(
      toEvidenceBody(
        range({
          allSources: false,
          sources: 'fortigate fortigate',
          password: true,
          reference: ' R ',
        }),
      ),
    ).toEqual({
      from: '2026-10-04',
      to: '2026-10-05',
      sources: ['fortigate'],
      extract: [],
      content: 'full_days',
      password: true,
      reference: 'R',
    });
  });

  it('an extract takes its sources from its lines', () => {
    expect(
      toEvidenceBody(
        range({
          allSources: false,
          sources: 'core-r1',
          content: 'extract',
          lines: '2026-10-05 fortigate 9',
        }),
      ),
    ).toMatchObject({
      sources: null,
      extract: [{ day: '2026-10-05', source: 'fortigate', line: 9 }],
      content: 'extract',
    });
  });

  it('a lookup scope', () => {
    expect(toEvidenceBody({ ...emptyWizard(1), content: 'extract' })).toEqual({
      lookup_id: 1,
      content: 'extract',
      password: false,
      reference: null,
    });
  });

  it('a lookup scope narrowed to chosen sources (full days only)', () => {
    const narrowed = { ...emptyWizard(1), allSources: false, sources: 'fortigate, core-r1' };
    expect(toEvidenceBody(narrowed)).toEqual({
      lookup_id: 1,
      sources: ['fortigate', 'core-r1'],
      content: 'full_days',
      password: false,
      reference: null,
    });
    // An extract takes the sources of the cited lines; the API refuses `sources` with it.
    expect(toEvidenceBody({ ...narrowed, content: 'extract' })).not.toHaveProperty('sources');
  });

  it('adds the audit log only when it was chosen and is allowed', () => {
    expect(toEvidenceBody(range(), true)).not.toHaveProperty('include_audit_log');
    expect(toEvidenceBody(emptyWizard(1), true)).not.toHaveProperty('include_audit_log');
    expect(toEvidenceBody(range({ includeAuditLog: true }), true)).toMatchObject({
      include_audit_log: true,
    });
    expect(toEvidenceBody({ ...emptyWizard(1), includeAuditLog: true }, true)).toMatchObject({
      lookup_id: 1,
      include_audit_log: true,
    });
  });

  it('never sends the audit log choice while it is not allowed (the API would refuse it)', () => {
    // Not accepted by the API (yet), or not an administrator: the field stays out of the body.
    expect(toEvidenceBody(range({ includeAuditLog: true }))).not.toHaveProperty(
      'include_audit_log',
    );
    expect(toEvidenceBody({ ...emptyWizard(1), includeAuditLog: true }, false)).not.toHaveProperty(
      'include_audit_log',
    );
  });
});

describe('sourcesSummary (what the package will hold)', () => {
  it('a full-day lookup package: every source of the days unless narrowed', () => {
    expect(sourcesSummary(emptyWizard(1))).toEqual({ key: 'evidence.wizard.sourcesLookupAll' });
    expect(
      sourcesSummary({ ...emptyWizard(1), allSources: false, sources: 'fortigate core-r1' }),
    ).toEqual({
      key: 'evidence.wizard.sourcesLookupChosen',
      values: { sources: 'fortigate, core-r1' },
    });
    expect(sourcesSummary({ ...emptyWizard(1), content: 'extract' })).toEqual({
      key: 'evidence.wizard.sourcesFromLookupLines',
    });
  });

  it('a day range', () => {
    expect(sourcesSummary(range())).toEqual({ key: 'evidence.wizard.sourcesAll' });
    expect(sourcesSummary(range({ allSources: false, sources: 'fortigate' }))).toEqual({
      key: 'evidence.wizard.sourcesChosen',
      values: { sources: 'fortigate' },
    });
    expect(sourcesSummary(range({ content: 'extract', allSources: false }))).toEqual({
      key: 'evidence.wizard.sourcesFromLines',
    });
  });

  it('a lookup scope checks chosen sources like a day range', () => {
    expect(validateScope({ ...emptyWizard(3), allSources: false }, TODAY)).toEqual({
      sources: 'evidence.validation.sourcesRequired',
    });
    expect(
      validateScope({ ...emptyWizard(3), allSources: false, sources: 'FortiGate' }, TODAY),
    ).toEqual({ sources: 'evidence.validation.sourceName' });
  });
});

describe('helpers', () => {
  it('counts days and lists sources', () => {
    expect(daySpan('2026-10-04', '2026-10-04')).toBe(1);
    expect(daySpan('2026-09-30', '2026-10-30')).toBe(31);
    expect(sourceList(' fortigate,core-r1; fortigate ')).toEqual(['fortigate', 'core-r1']);
  });

  it('reads the scope of a captured package', () => {
    const detail = need(EVIDENCE.details['1'], 'package 1');
    expect(scopeDays(detail.scope)).toEqual({
      first: '2026-10-04',
      last: '2026-10-05',
      count: 2,
    });
    expect(isUnfinished(detail)).toBe(false);
    expect(isUnfinished(need(EVIDENCE.details['5'], 'package 5'))).toBe(true);
  });

  it('shows a package past its expiry as expired before the nightly purge marks it', () => {
    // Captured from the API: still "ready" (label "Hazır"), but no longer downloadable.
    const stale = EVIDENCE.ready_expired;
    expect([stale.status, stale.status_label_tr, stale.downloadable]).toEqual([
      'ready',
      'Hazır',
      false,
    ]);
    expect(isExpiredUnpurged(stale)).toBe(true);
    expect(shownStatus(stale)).toBe('expired');
    expect(downloadBlockedReason(stale)).toBe('evidence.blocked.expired');

    const ready = need(EVIDENCE.details['1'], 'package 1');
    expect(isExpiredUnpurged(ready)).toBe(false);
    expect(shownStatus(ready)).toBe('ready');
    expect(downloadBlockedReason(ready)).toBeNull();
  });

  it('names why a package cannot be downloaded', () => {
    const reasons = Object.values(EVIDENCE.details).map((item) => [
      item.status,
      downloadBlockedReason(item),
    ]);
    expect(Object.fromEntries(reasons)).toEqual({
      ready: null,
      failed: 'evidence.blocked.failed',
      expired: 'evidence.blocked.expired',
      queued: 'evidence.blocked.unfinished',
    });
    expect(downloadBlockedReason({ status: 'deleted', downloadable: false })).toBe(
      'evidence.blocked.deleted',
    );
    expect(downloadBlockedReason({ status: 'building', downloadable: false })).toBe(
      'evidence.blocked.unfinished',
    );
  });
});
