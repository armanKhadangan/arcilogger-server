/**
 * Fixtures of the archive reload tests (Wave 5 contract §5, G4 to G10): the answers of
 * `POST /archive-reloads` and `GET /archive-reloads/{id}`, the 409 `archive_reload_required` of
 * `POST /lookup/5651` and `POST /search`, the `archived` range of a search answer and the refusals.
 *
 * Interim: hand-built from W5-G's captured `archive.json` (#121) until it merges; then read
 * `e2e/samples/archive.json`. The values are copied verbatim from that capture (clock 13.10.2026
 * 07:00, site today 2026-10-13, the database window from 2026-10-06).
 *
 * Shared by the unit tests and the E2E mock (`e2e/mock-archive.ts`), so it imports nothing with the
 * `@/` alias. Copy a value before changing it.
 */

import type { ApiErrorBody } from '../lib/api-types';
import type {
  ArchivedRange,
  ArchiveReload,
  ArchiveReloadList,
  ArchiveReloadRequired,
} from '../lib/archive-reload-types';

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

/**
 * `error_code` of a `failed` reload (`ArchiveReloadFailureCode` of W5-G): the API sends no text
 * for it, the web has one per code.
 */
export const ARCHIVE_FAILURE_CODES = [
  'archive_day_not_in_vault',
  'archive_file_unreadable',
] as const;

/** The 202 answer of `POST /archive-reloads`: queued, nothing loaded yet. */
export const ARCHIVE_CREATED: ArchiveReload = {
  bytes_done: 0,
  bytes_total: 64992,
  error_code: null,
  expires_at: null,
  first_day: '2026-10-04',
  id: 1,
  last_day: '2026-10-05',
  lookup_id: null,
  ready_at: null,
  reason: 'lookup',
  requested_at: '2026-10-13T04:01:26.500001Z',
  requested_by: 'ayse.yilmaz',
  status: 'queued',
};

/** The same reload half loaded. */
export const ARCHIVE_LOADING: ArchiveReload = {
  bytes_done: 32496,
  bytes_total: 64992,
  error_code: null,
  expires_at: null,
  first_day: '2026-10-04',
  id: 1,
  last_day: '2026-10-05',
  lookup_id: null,
  ready_at: null,
  reason: 'lookup',
  requested_at: '2026-10-13T04:01:26.500001Z',
  requested_by: 'ayse.yilmaz',
  status: 'loading',
};

/** The same reload loaded: the days stay until `expires_at` (ready_at + 7 days). */
export const ARCHIVE_READY: ArchiveReload = {
  bytes_done: 64992,
  bytes_total: 64992,
  error_code: null,
  expires_at: '2026-10-20T04:01:26.596464Z',
  first_day: '2026-10-04',
  id: 1,
  last_day: '2026-10-05',
  lookup_id: null,
  ready_at: '2026-10-13T04:01:26.596464Z',
  reason: 'lookup',
  requested_at: '2026-10-13T04:01:26.500001Z',
  requested_by: 'ayse.yilmaz',
  status: 'ready',
};

/** A second POST for the same days answers the user's own active request (202). */
export const ARCHIVE_EXISTING: ArchiveReload = {
  bytes_done: 64992,
  bytes_total: 64992,
  error_code: null,
  expires_at: '2026-10-20T04:01:26.596464Z',
  first_day: '2026-10-04',
  id: 1,
  last_day: '2026-10-05',
  lookup_id: null,
  ready_at: '2026-10-13T04:01:26.596464Z',
  reason: 'lookup',
  requested_at: '2026-10-13T04:01:26.500001Z',
  requested_by: 'ayse.yilmaz',
  status: 'ready',
};

/** A reload whose sealed file could not be read (`archive_file_unreadable`, no text). */
export const ARCHIVE_FAILED: ArchiveReload = {
  bytes_done: 0,
  bytes_total: 64992,
  error_code: 'archive_file_unreadable',
  expires_at: null,
  first_day: '2026-10-05',
  id: 2,
  last_day: '2026-10-05',
  lookup_id: null,
  ready_at: null,
  reason: 'search',
  requested_at: '2026-10-13T04:01:26.808511Z',
  requested_by: 'tolga',
  status: 'failed',
};

/** `GET /archive-reloads`: the active reload first, then the failed and the expired one. */
export const ARCHIVE_LIST: ArchiveReloadList = {
  items: [
    {
      bytes_done: 0,
      bytes_total: 48744,
      error_code: null,
      expires_at: null,
      first_day: '2026-10-04',
      id: 3,
      last_day: '2026-10-04',
      lookup_id: null,
      ready_at: null,
      reason: 'search',
      requested_at: '2026-10-13T04:01:26.952299Z',
      requested_by: 'tolga',
      status: 'queued',
    },
    {
      bytes_done: 0,
      bytes_total: 64992,
      error_code: 'archive_file_unreadable',
      expires_at: null,
      first_day: '2026-10-05',
      id: 2,
      last_day: '2026-10-05',
      lookup_id: null,
      ready_at: null,
      reason: 'search',
      requested_at: '2026-10-13T04:01:26.808511Z',
      requested_by: 'tolga',
      status: 'failed',
    },
    {
      bytes_done: 64992,
      bytes_total: 64992,
      error_code: null,
      expires_at: '2026-10-20T04:01:26.596464Z',
      first_day: '2026-10-04',
      id: 1,
      last_day: '2026-10-05',
      lookup_id: null,
      ready_at: '2026-10-13T04:01:26.596464Z',
      reason: 'lookup',
      requested_at: '2026-10-13T04:01:26.500001Z',
      requested_by: 'ayse.yilmaz',
      status: 'expired',
    },
  ],
};

const REQUIRED_DETAILS: ArchiveReloadRequired = {
  bytes_total: 64992,
  estimated_bytes: 168980,
  first_day: '2026-10-04',
  last_day: '2026-10-05',
};

const REQUIRED_TR =
  'İstenen kayıtlar için gereken günler (04.10.2026 - 05.10.2026) veritabanında değil: veritabanı son 7 günün kayıtlarını tutar. Bu günler mühürlü arşivden yeniden yüklenebilir (yaklaşık 165 KB); yükleme denetim kaydına yazılır. Bu günler okunmadan sonuç verilmez.';
const REQUIRED_EN =
  'The days these records need (2026-10-04 - 2026-10-05) are not in the database: it keeps the records of the last 7 days. They can be reloaded from the sealed archive (about 165 KB); the reload is written to the audit log. No answer is given without them.';

/** 409 of `POST /lookup/5651`: the records it needs are of 04.10.2026 and 05.10.2026. */
export const ARCHIVE_LOOKUP_REQUIRED: ArchiveRequiredSample = {
  body: {
    error: {
      code: 'archive_reload_required',
      details: { ...REQUIRED_DETAILS },
      message_en: REQUIRED_EN,
      message_tr: REQUIRED_TR,
    },
  },
  headers: {},
  status: 409,
};

/** 409 of `POST /search` when the whole range is outside the window. */
export const ARCHIVE_SEARCH_REQUIRED: ArchiveRequiredSample = {
  body: {
    error: {
      code: 'archive_reload_required',
      details: { ...REQUIRED_DETAILS },
      message_en: REQUIRED_EN,
      message_tr: REQUIRED_TR,
    },
  },
  headers: {},
  status: 409,
};

/**
 * Of the 200 `SearchPage` whose range reaches before the window: its `archived` days and its
 * `range` (the rest of that answer is an ordinary search page).
 */
export const ARCHIVE_SEARCH_ARCHIVED: {
  archived: ArchivedRange;
  range: { from: string; to: string };
} = {
  archived: { first_day: '2026-10-04', last_day: '2026-10-05' },
  range: { from: '2026-10-04T00:00:00+03:00', to: '2026-10-08T00:00:00+03:00' },
};

/** The refusals of `POST /archive-reloads` (422 of G5, 403) and of `GET /archive-reloads/{id}`. */
export const ARCHIVE_ERRORS = {
  disk_low: {
    body: {
      error: {
        code: 'archive_reload_disk_low',
        details: {
          drive: 'data',
          estimated_bytes: 168980,
          free_bytes: 80530636800,
          total_bytes: 536870912000,
        },
        message_en:
          'The reload would take about 165 KB on the database drive and leave less than 15 % free on data. Choose a shorter range or tell the administrator.',
        message_tr:
          'Yükleme veritabanı diskinde (yaklaşık 165 KB) yer kaplar ve data sürücüsünde boş alan %15 altına iner. Daha kısa bir aralık seçin veya sistem yöneticisine bildirin.',
      },
    },
    headers: {},
    status: 422,
  },
  forbidden: {
    body: {
      error: {
        code: 'forbidden',
        message_en: 'You do not have permission for this action.',
        message_tr: 'Bu işlem için yetkiniz yok.',
      },
    },
    headers: {},
    status: 403,
  },
  in_window: {
    body: {
      error: {
        code: 'validation_error',
        details: [
          {
            loc: ['body', 'first_day'],
            msg: 'in the database window, nothing to reload: 2026-10-06',
            type: 'value_error',
          },
        ],
        message_en: 'The submitted data is invalid.',
        message_tr: 'Gönderilen veriler geçersiz.',
      },
    },
    headers: {},
    status: 422,
  },
  not_found: {
    body: {
      error: {
        code: 'not_found',
        message_en: 'Not found.',
        message_tr: 'Kayıt bulunamadı.',
      },
    },
    headers: {},
    status: 404,
  },
  not_in_vault: {
    body: {
      error: {
        code: 'archive_day_not_in_vault',
        details: { days: ['2026-10-03'] },
        message_en:
          'The sealed archive holds no record of these days (not sealed yet, or removed by retention): 2026-10-03. They cannot be reloaded.',
        message_tr:
          'Şu günlerin mühürlü arşivde kaydı yok (henüz mühürlenmedi veya saklama süresi doldu): 03.10.2026. Bu günler yeniden yüklenemez.',
      },
    },
    headers: {},
    status: 422,
  },
  privacy_notice_required: {
    body: {
      error: {
        code: 'privacy_notice_required',
        message_en:
          'Read and accept the KVKK privacy notice before opening this section with personal data.',
        message_tr:
          'Kişisel veri içeren bu bölümü açmadan önce KVKK bilgilendirme metnini okuyup onaylamanız gerekiyor.',
      },
    },
    headers: {},
    status: 403,
  },
  too_large: {
    body: {
      error: {
        code: 'archive_reload_too_large',
        details: { days: 15, max_days: 14 },
        message_en:
          'One reload may ask for at most 14 days; this request has 15. Narrow the range or split it into several reloads.',
        message_tr:
          'Bir yüklemede en fazla 14 gün istenebilir; bu istek 15 gün. Aralığı daraltın veya birkaç yüklemeye bölün.',
      },
    },
    headers: {},
    status: 422,
  },
} satisfies Record<string, ArchiveErrorSample>;

/** The site day of the capture (as `SAMPLE_TODAY`). */
export const ARCHIVE_SITE_TODAY = '2026-10-13';
