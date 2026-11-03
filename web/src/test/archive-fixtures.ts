/**
 * Fixtures of the archive reload tests (Wave 5 contract §5, G4 to G10): the answers of
 * `POST /archive-reloads` and `GET /archive-reloads/{id}`, the 409 `archive_reload_required` of
 * `POST /lookup/5651` and `POST /search`, the search answer with `archived` days and the refusals.
 * They are the captured samples (`e2e/samples/archive.json`, written by
 * `web/scripts/capture_api_samples.py` from W5-G's API; clock 13.10.2026 07:00, site today
 * 2026-10-13). {@link ARCHIVE_FAILURE_CODES} lists the `error_code`s of a failed reload, which no
 * answer names: the web has a text for each.
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-archive.ts`), so it imports nothing with the
 * `@/` alias; both run in `web/`, where it reads the file as `src/test/samples.ts` does. Copy a
 * value before changing it.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { ApiErrorBody } from '../lib/api-types';
import type {
  ArchivedRange,
  ArchiveReload,
  ArchiveReloadFailureCode,
  ArchiveReloadList,
  ArchiveReloadRequired,
} from '../lib/archive-reload-types';
import type { SearchPage } from '../lib/search-types';

/** A captured error answer (as `ErrorSample` of `e2e/samples.ts`). */
export interface ArchiveErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

/** The 409 of a lookup or search that needs days outside the window. */
export interface ArchiveRequiredSample extends ArchiveErrorSample {
  body: ApiErrorBody & { error: { details: ArchiveReloadRequired } };
}

/** The refusals of `POST /archive-reloads` (422 of G5, 403) and of `GET /archive-reloads/{id}`. */
type ArchiveErrorKey =
  | 'disk_low'
  | 'forbidden'
  | 'in_window'
  | 'not_found'
  | 'not_in_vault'
  | 'privacy_notice_required'
  | 'too_large'
  | 'validation';

/** The captured cases of `samples_archive.py`. */
interface ArchiveSamples {
  created: ArchiveReload;
  loading: ArchiveReload;
  ready: ArchiveReload;
  existing: ArchiveReload;
  failed: ArchiveReload;
  list: ArchiveReloadList;
  lookup_required: ArchiveRequiredSample;
  search_required: ArchiveRequiredSample;
  search_archived: SearchPage & { archived: ArchivedRange };
  errors: Record<ArchiveErrorKey, ArchiveErrorSample>;
  meta: { site_today: string };
}

export const ARCHIVE_SAMPLES = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'archive.json'), 'utf8'),
) as ArchiveSamples;

/** `error_code` of a `failed` reload: the API sends no text for it, the web has one per code. */
export const ARCHIVE_FAILURE_CODES = [
  'archive_day_not_in_vault',
  'archive_file_unreadable',
] as const satisfies readonly ArchiveReloadFailureCode[];

/** The 202 answer of `POST /archive-reloads`: queued, nothing loaded yet. */
export const ARCHIVE_CREATED = ARCHIVE_SAMPLES.created;
/** The same reload half loaded. */
export const ARCHIVE_LOADING = ARCHIVE_SAMPLES.loading;
/** The same reload loaded: the days stay until `expires_at` (ready_at + 7 days). */
export const ARCHIVE_READY = ARCHIVE_SAMPLES.ready;
/** A second POST for the same days answers the user's own active request (202). */
export const ARCHIVE_EXISTING = ARCHIVE_SAMPLES.existing;
/** A reload whose sealed file could not be read (`archive_file_unreadable`, no text). */
export const ARCHIVE_FAILED = ARCHIVE_SAMPLES.failed;
/** `GET /archive-reloads`: the active reload first, then the failed and the expired one. */
export const ARCHIVE_LIST = ARCHIVE_SAMPLES.list;

/** 409 of `POST /lookup/5651`: the records it needs are of 04.10.2026 and 05.10.2026. */
export const ARCHIVE_LOOKUP_REQUIRED = ARCHIVE_SAMPLES.lookup_required;
/** 409 of `POST /search` when the whole range is outside the window. */
export const ARCHIVE_SEARCH_REQUIRED = ARCHIVE_SAMPLES.search_required;
/** The 200 `SearchPage` whose range reaches before the window, with its `archived` days. */
export const ARCHIVE_SEARCH_ARCHIVED = ARCHIVE_SAMPLES.search_archived;

/** The refusals of `POST /archive-reloads` (422 of G5, 403) and of `GET /archive-reloads/{id}`. */
export const ARCHIVE_ERRORS = ARCHIVE_SAMPLES.errors;

/** The site day of the capture (as `SAMPLE_TODAY`). */
export const ARCHIVE_SITE_TODAY = ARCHIVE_SAMPLES.meta.site_today;
