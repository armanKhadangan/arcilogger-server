/**
 * Archive reload ("arşivden yeniden yükle") — Wave 5 contract §5, G4, G5 and G7 to G9.
 *
 * Mirror of `api/src/arcilogger_api/archive/models.py`: the same names, fields and optionality.
 * The database keeps the last `db.retention_days` days of log rows; an older day comes back from
 * the sealed vault on request, for `db.reload_keep_days` days.
 */

import type { DateString, DateTimeString } from './api-types';

/** The function that asks for the reload; its permission is needed (G6). */
export type ArchiveReloadReason = 'lookup' | 'search';

export type ArchiveReloadStatus = 'queued' | 'loading' | 'ready' | 'failed' | 'expired';

/** The error codes of the area: 409 for the first, 422 for the refusals of G5. */
export type ArchiveReloadErrorCode =
  | 'archive_reload_required' // a lookup or search needs days outside the window (G7, G8)
  | 'archive_reload_too_large' // more than 14 days in one request
  | 'archive_day_not_in_vault' // a day with no sealed manifest (not sealed, or removed)
  | 'archive_reload_disk_low'; // the load would leave less than 15 % free on the database drive

/** `error_code` of a `failed` reload, set by ArciIngest while it loads. */
export type ArchiveReloadFailureCode =
  | 'archive_day_not_in_vault' // vault retention removed a day after the request was queued
  | 'archive_file_unreadable'; // a sealed file kept failing to read or no longer matched

/** Body of `POST /archive-reloads` (G9). */
export interface ArchiveReloadRequest {
  reason: ArchiveReloadReason;
  first_day: DateString;
  /** At most 14 days from `first_day`, both included (G5). */
  last_day: DateString;
  /** The lookup that asked, for `reason` `lookup` (written to the audit entry, G6). */
  lookup_id?: number | null;
}

/** One reload (G4): the 202 answer of `POST /archive-reloads` and `GET /archive-reloads/{id}`. */
export interface ArchiveReload {
  id: number;
  /** The user name of the person who asked. */
  requested_by: string;
  requested_at: DateTimeString;
  reason: ArchiveReloadReason;
  lookup_id: number | null;
  first_day: DateString;
  last_day: DateString;
  status: ArchiveReloadStatus;
  /** Σ `bytes_uncompressed` of the sealed files to load. */
  bytes_total: number;
  /** Σ committed ingest offset; progress is `bytes_done / bytes_total`. */
  bytes_done: number;
  /** Set when `status` is `failed`: one of `ArchiveReloadFailureCode`. */
  error_code: string | null;
  ready_at: DateTimeString | null;
  /** `ready_at` + `db.reload_keep_days`; then the window job drops the days again. */
  expires_at: DateTimeString | null;
}

/** `GET /archive-reloads`: the active reloads first, then the recent ones. */
export interface ArchiveReloadList {
  items: ArchiveReload[];
}

/** `details` of 409 `archive_reload_required` (G7, G8): what a reload would bring back. */
export interface ArchiveReloadRequired {
  first_day: DateString;
  last_day: DateString;
  /** Σ `bytes_uncompressed` of the sealed files of those days. */
  bytes_total: number;
  /** The estimated database growth: `bytes_total` times the measured factor (G5). */
  estimated_bytes: number;
  /**
   * `db.reload_keep_days` at the time of the answer: the days a reload keeps them (C23; a lookup
   * or search user cannot read `/settings`).
   */
  keep_days: number;
}

/** The search answer's `archived` field (G8): the part of the range outside the window. */
export interface ArchivedRange {
  first_day: DateString;
  last_day: DateString;
}
