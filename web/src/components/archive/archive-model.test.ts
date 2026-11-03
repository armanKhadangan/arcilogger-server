import { ApiError } from '@/lib/api';
import {
  ARCHIVE_CREATED,
  ARCHIVE_FAILED,
  ARCHIVE_FAILURE_CODES,
  ARCHIVE_LIST,
  ARCHIVE_LOADING,
  ARCHIVE_LOOKUP_REQUIRED,
  ARCHIVE_READY,
  ARCHIVE_SEARCH_ARCHIVED,
  ARCHIVE_ERRORS,
  type ArchiveErrorSample,
} from '@/test/archive-fixtures';
import { SEARCH } from '@/test/samples';

import {
  ARCHIVE_RELOAD_POLL_MS,
  MAX_RELOAD_DAYS,
  archivedOf,
  failureKey,
  isFinished,
  progressOf,
  reloadSpan,
  requiredOf,
} from './archive-model';

/*
 * The pure part of the archive reload card (Wave 5 contract §5, G5 and G10): the days one reload
 * asks for (at most 14, the newest of a longer range), its progress, when the poll stops, the text
 * of a failure and the 409 that offers the reload.
 */

function apiError({ status, body }: ArchiveErrorSample): ApiError {
  return new ApiError({
    status,
    code: body.error.code,
    messageTr: body.error.message_tr,
    messageEn: body.error.message_en,
    details: body.error.details,
  });
}

describe('archive model', () => {
  it('fixes the limits of the contract', () => {
    expect(MAX_RELOAD_DAYS).toBe(14);
    expect(ARCHIVE_RELOAD_POLL_MS).toBe(2_000);
  });

  it('reloadSpan: a range of up to 14 days is asked for whole', () => {
    expect(reloadSpan('2026-10-04', '2026-10-05')).toEqual({
      first_day: '2026-10-04',
      last_day: '2026-10-05',
      days: 2,
      clipped: false,
    });
    expect(reloadSpan('2026-10-05', '2026-10-05')).toEqual({
      first_day: '2026-10-05',
      last_day: '2026-10-05',
      days: 1,
      clipped: false,
    });
    expect(reloadSpan('2026-09-22', '2026-10-05')).toEqual({
      first_day: '2026-09-22',
      last_day: '2026-10-05',
      days: 14,
      clipped: false,
    });
  });

  it('reloadSpan: a longer range gives its 14 days nearest the window', () => {
    expect(reloadSpan('2026-09-21', '2026-10-05')).toEqual({
      first_day: '2026-09-22',
      last_day: '2026-10-05',
      days: 14,
      clipped: true,
    });
    // 31 days across a month and a year end.
    expect(reloadSpan('2025-12-01', '2026-01-02')).toEqual({
      first_day: '2025-12-20',
      last_day: '2026-01-02',
      days: 14,
      clipped: true,
    });
    // Over the end of summer time in Europe (25.10.2026): plain calendar days.
    expect(reloadSpan('2026-10-01', '2026-10-30')).toMatchObject({
      first_day: '2026-10-17',
      days: 14,
    });
    // A leap day.
    expect(reloadSpan('2028-02-01', '2028-03-05')).toMatchObject({ first_day: '2028-02-21' });
    expect(() => reloadSpan('04.10.2026', '2026-10-05')).toThrow(RangeError);
  });

  it('isFinished: the poll stops on ready, failed and expired', () => {
    expect(isFinished('queued')).toBe(false);
    expect(isFinished('loading')).toBe(false);
    expect(isFinished('ready')).toBe(true);
    expect(isFinished('failed')).toBe(true);
    expect(isFinished('expired')).toBe(true);
  });

  it('progressOf: bytes done of bytes total, 1 once ready, 0 without a total', () => {
    expect(progressOf(ARCHIVE_CREATED)).toBe(0);
    expect(progressOf(ARCHIVE_LOADING)).toBe(0.5);
    expect(progressOf(ARCHIVE_READY)).toBe(1);
    expect(progressOf({ ...ARCHIVE_LOADING, bytes_total: 0, bytes_done: 0 })).toBe(0);
    expect(progressOf({ ...ARCHIVE_READY, bytes_total: 0, bytes_done: 0 })).toBe(1);
    expect(progressOf({ ...ARCHIVE_LOADING, bytes_done: 70_000 })).toBe(1);
    expect(progressOf(ARCHIVE_FAILED)).toBe(0);
  });

  it('failureKey: a text per known failure code, else the generic one', () => {
    expect(ARCHIVE_FAILURE_CODES.map(failureKey)).toEqual([
      'archive.failure.archive_day_not_in_vault',
      'archive.failure.archive_file_unreadable',
    ]);
    expect(failureKey(ARCHIVE_FAILED.error_code)).toBe('archive.failure.archive_file_unreadable');
    expect(failureKey('archive_something_new')).toBe('archive.failure.unknown');
    expect(failureKey(null)).toBe('archive.failure.unknown');
    // Not a way into other keys.
    expect(failureKey('unknown')).toBe('archive.failure.unknown');
  });

  it('requiredOf: the days of a 409 archive_reload_required', () => {
    expect(requiredOf(apiError(ARCHIVE_LOOKUP_REQUIRED))).toEqual({
      bytes_total: 64992,
      estimated_bytes: 168980,
      first_day: '2026-10-04',
      last_day: '2026-10-05',
    });
    expect(requiredOf(apiError(ARCHIVE_ERRORS.too_large))).toBeNull();
    expect(requiredOf(apiError(ARCHIVE_ERRORS.not_in_vault))).toBeNull();
    const broken = (details: unknown) =>
      apiError({
        ...ARCHIVE_LOOKUP_REQUIRED,
        body: { error: { ...ARCHIVE_LOOKUP_REQUIRED.body.error, details } },
      });
    expect(requiredOf(broken(undefined))).toBeNull();
    expect(requiredOf(broken(null))).toBeNull();
    expect(requiredOf(broken({ first_day: '2026-10-04' }))).toBeNull();
    expect(requiredOf(new Error('archive_reload_required'))).toBeNull();
    expect(requiredOf(null)).toBeNull();
  });

  it('archivedOf: the archived days of a search answer, null without them', () => {
    const page = SEARCH.search.typed;
    expect(archivedOf(page)).toBeNull();
    expect(archivedOf({ ...page, archived: null } as typeof page)).toBeNull();
    expect(archivedOf({ ...page, ...ARCHIVE_SEARCH_ARCHIVED })).toEqual({
      first_day: '2026-10-04',
      last_day: '2026-10-05',
    });
  });

  it('the fixtures keep the captured reload statuses', () => {
    expect(ARCHIVE_LIST.items.map((item) => item.status)).toEqual(['queued', 'failed', 'expired']);
    expect(ARCHIVE_LIST.items[1]).toEqual(ARCHIVE_FAILED);
  });
});
