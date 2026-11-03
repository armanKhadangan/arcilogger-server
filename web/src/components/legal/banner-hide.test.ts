import { LEGAL_MIXED, LEGAL_WARNING_ONLY, legalStatusWith } from '@/test/w5-fixtures';

import {
  hideFor,
  isHidden,
  LEGAL_BANNER_HIDE_KEY,
  LEGAL_BANNER_HIDE_MS,
  readHide,
  writeHide,
} from './banner-hide';

/*
 * "24 saat gizle" (contract 2.3.2): a warning stays hidden in this browser for 24 hours while
 * every reason is one the user saw; a new reason or any danger shows it again. A broken or
 * unavailable storage hides nothing.
 */

const NOW = Date.parse('2026-10-13T12:00:00Z');
const MINUTE = 60_000;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('legal banner pause', () => {
  it('lasts 24 hours and remembers the reasons on screen', () => {
    expect(LEGAL_BANNER_HIDE_MS).toBe(24 * 60 * MINUTE);
    expect(LEGAL_BANNER_HIDE_KEY).toBe('arci.legalBanner.hidden');
    expect(hideFor(LEGAL_WARNING_ONLY, NOW)).toEqual({
      until: NOW + LEGAL_BANNER_HIDE_MS,
      codes: ['crl_stale', 'renewal_due'],
    });
  });

  it('hides the same warning until the 24 hours are over', () => {
    const hide = hideFor(LEGAL_WARNING_ONLY, NOW);
    expect(isHidden(LEGAL_WARNING_ONLY, hide, NOW)).toBe(true);
    expect(isHidden(LEGAL_WARNING_ONLY, hide, NOW + LEGAL_BANNER_HIDE_MS - MINUTE)).toBe(true);
    expect(isHidden(LEGAL_WARNING_ONLY, hide, NOW + LEGAL_BANNER_HIDE_MS)).toBe(false);
    expect(isHidden(LEGAL_WARNING_ONLY, null, NOW)).toBe(false);
  });

  it('stays hidden when a reason went away, not when a new one appears', () => {
    const hide = hideFor(LEGAL_WARNING_ONLY, NOW);
    expect(isHidden(legalStatusWith(['renewal_due']), hide, NOW)).toBe(true);
    expect(
      isHidden(legalStatusWith(['renewal_due', 'crl_stale', 'renewal_uncovered']), hide, NOW),
    ).toBe(false);
  });

  it('never hides danger', () => {
    // Even a stored pause naming every reason (written by hand, or by an older version).
    const hide = {
      until: NOW + LEGAL_BANNER_HIDE_MS,
      codes: LEGAL_MIXED.reasons.map((reason) => reason.code),
    };
    expect(isHidden(LEGAL_MIXED, hide, NOW)).toBe(false);
  });

  it('is kept in this browser and read back', () => {
    const hide = hideFor(LEGAL_WARNING_ONLY, NOW);
    writeHide(hide);
    expect(JSON.parse(window.localStorage.getItem(LEGAL_BANNER_HIDE_KEY) ?? 'null')).toEqual(hide);
    expect(readHide()).toEqual(hide);
  });

  it.each([
    ['nothing stored', null],
    ['not JSON', '{until'],
    ['not an object', '42'],
    ['null', 'null'],
    ['no end', '{"codes":["crl_stale"]}'],
    ['an end that is not a number', '{"until":"tomorrow","codes":["crl_stale"]}'],
    ['no codes', `{"until":${String(NOW)}}`],
    ['codes that are not text', `{"until":${String(NOW)},"codes":[1]}`],
  ])('reads %s as not hidden', (_, stored) => {
    if (stored !== null) window.localStorage.setItem(LEGAL_BANNER_HIDE_KEY, stored);
    expect(readHide()).toBeNull();
  });

  it('works without storage: nothing is read, writing does not throw', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    expect(() => {
      writeHide(hideFor(LEGAL_WARNING_ONLY, NOW));
    }).not.toThrow();
    expect(readHide()).toBeNull();
  });
});
