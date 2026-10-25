/**
 * The one way the web app talks to the ARCILOGGER API (`/api/v1`, same origin).
 *
 * What every request gets (docs/MVP-CONTRACTS.md section 6):
 *  - `credentials: 'same-origin'`: the HttpOnly session cookie `arci_session` goes along; the
 *    app never sees it.
 *  - `X-CSRF-Token` on every POST/PUT/PATCH/DELETE, copied from the readable cookie `arci_csrf`
 *    (double submit, bound to the session by the server). Without that cookie a cheap GET of
 *    `/health` fetches one first: the API hands it to every client that has none.
 *  - `X-Arci-Background: 1` on requests the app makes on its own (polling, interval refetch,
 *    the idle check): they are authenticated but do not count as activity, so an open tab cannot
 *    keep a session alive past the idle timeout.
 *  - Errors become an {@link ApiError} with the server's Turkish and English texts; the message
 *    in the current interface language is `error.localized`.
 *
 * Global reactions (unless the caller passes `silent`, e.g. the sign-in steps, which handle their
 * own 401s): 401 → sign-in page (keeping the target URL), 403 `password_change_required` →
 * password change, 403 `privacy_notice_required` → KVKK notice, 403 `module_not_licensed` → the
 * licence state is read again. The handlers are installed by `ApiEventsBridge` (they need the
 * router and the query cache).
 */

import type { PasswordProblem } from './api-types';
import i18n, { currentLang } from './i18n';

export const API_BASE = '/api/v1';
export const CSRF_COOKIE = 'arci_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';
export const BACKGROUND_HEADER = 'X-Arci-Background';

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export type QueryValue = string | number | boolean | null | undefined;

/** An error response of the API (or a failure to reach it), in the API's error format. */
export class ApiError extends Error {
  /** HTTP status; 0 when the server could not be reached. */
  readonly status: number;
  /** Error code, e.g. `login_failed`, `reauth_required`, `network_error`. */
  readonly code: string;
  readonly messageTr: string;
  readonly messageEn: string;
  /** `details` of the error body (422 validation problems, rejected password rules). */
  readonly details: unknown;
  /** Seconds from a `Retry-After` header (429), else `null`. */
  readonly retryAfter: number | null;

  constructor(init: {
    status: number;
    code: string;
    messageTr: string;
    messageEn: string;
    details?: unknown;
    retryAfter?: number | null;
  }) {
    super(`${init.status} ${init.code}`);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.messageTr = init.messageTr;
    this.messageEn = init.messageEn;
    this.details = init.details;
    this.retryAfter = init.retryAfter ?? null;
  }

  /** The server's message in the current interface language. */
  get localized(): string {
    return currentLang() === 'en' ? this.messageEn : this.messageTr;
  }
}

export function isApiError(error: unknown, ...codes: string[]): error is ApiError {
  return error instanceof ApiError && (codes.length === 0 || codes.includes(error.code));
}

/** A text for any error in the current language (API message, else a generic one). */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.localized;
  return i18n.t('errors.unexpected');
}

/** Rules the server's password policy reported (`details` of 422 `password_rejected`). */
export function passwordProblems(error: unknown): PasswordProblem[] {
  if (!isApiError(error, 'password_rejected') || !Array.isArray(error.details)) return [];
  return (error.details as unknown[]).filter(
    (item): item is PasswordProblem =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as PasswordProblem).message_tr === 'string' &&
      typeof (item as PasswordProblem).message_en === 'string',
  );
}

/** The password-policy problems in the current language, joined into one text. */
export function passwordProblemText(error: unknown): string | null {
  const problems = passwordProblems(error);
  if (problems.length === 0) return null;
  const english = currentLang() === 'en';
  return problems.map((problem) => (english ? problem.message_en : problem.message_tr)).join(' ');
}

export interface ApiEventHandlers {
  /** 401 from a request that was not `silent`: the session is gone. */
  unauthorized?: (error: ApiError) => void;
  /** 403 `password_change_required`. */
  passwordChangeRequired?: (error: ApiError) => void;
  /** 403 `privacy_notice_required` (KVKK notice not accepted yet). */
  privacyNoticeRequired?: (error: ApiError) => void;
  /**
   * 403 `module_not_licensed`: the page knew an older licence state (a tab left open while the
   * licence changed). The caller shows the message; the licence is read again.
   */
  moduleNotLicensed?: (error: ApiError) => void;
}

let handlers: ApiEventHandlers = {};
let lastForeground = Date.now();

/** Install the global reactions; returns a function that removes them again. */
export function setApiEventHandlers(next: ApiEventHandlers): () => void {
  handlers = next;
  return () => {
    if (handlers === next) handlers = {};
  };
}

/** When the app last sent a request that counts as user activity (ms since epoch). */
export function lastForegroundRequestAt(): number {
  return lastForeground;
}

/** Value of a cookie readable by scripts (`arci_csrf`), `null` when absent. */
export function readCookie(name: string): string | null {
  for (const part of document.cookie.split(';')) {
    const index = part.indexOf('=');
    if (index < 0) continue;
    if (part.slice(0, index).trim() === name) {
      const raw = part.slice(index + 1).trim();
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return null;
}

/** `/users?role=admin&active=true` — `null`, `undefined` and empty strings are left out. */
export function buildUrl(path: string, params?: Record<string, QueryValue>): string {
  const url = `${API_BASE}${path}`;
  if (!params) return url;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    search.append(key, String(value));
  }
  const query = search.toString();
  return query ? `${url}?${query}` : url;
}

export interface RequestOptions {
  method?: Method;
  /** Sent as JSON; a `FormData` as `multipart/form-data` (file uploads, e.g. CSV imports). */
  body?: unknown;
  params?: Record<string, QueryValue>;
  /** Automatic request (polling, refetch, idle check): sends `X-Arci-Background: 1`. */
  background?: boolean;
  signal?: AbortSignal;
  /** The caller handles every error itself; no global 401/403 reaction. */
  silent?: boolean;
}

function fixedTexts(key: string, options?: Record<string, unknown>): [string, string] {
  return [i18n.getFixedT('tr')(key, options), i18n.getFixedT('en')(key, options)];
}

function retryAfterOf(response: Response): number | null {
  const value = response.headers.get('Retry-After');
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  return Number(value.trim());
}

async function errorOf(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null; // e.g. an HTML error page of the reverse proxy
  }
  const error = (body as Partial<ApiErrorShape> | null)?.error;
  if (
    error &&
    typeof error.code === 'string' &&
    typeof error.message_tr === 'string' &&
    typeof error.message_en === 'string'
  ) {
    return new ApiError({
      status: response.status,
      code: error.code,
      messageTr: error.message_tr,
      messageEn: error.message_en,
      details: error.details,
      retryAfter: retryAfterOf(response),
    });
  }
  const [messageTr, messageEn] = fixedTexts('errors.http', { status: response.status });
  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    messageTr,
    messageEn,
    retryAfter: retryAfterOf(response),
  });
}

interface ApiErrorShape {
  error: { code: unknown; message_tr: unknown; message_en: unknown; details?: unknown };
}

let csrfPending: Promise<void> | null = null;

/** Make sure the `arci_csrf` cookie exists before the first mutating request. */
async function ensureCsrfCookie(): Promise<string | null> {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  csrfPending ??= fetch(`${API_BASE}/health`, {
    credentials: 'same-origin',
    headers: { [BACKGROUND_HEADER]: '1' },
  })
    .then(() => undefined)
    .catch(() => undefined)
    .finally(() => {
      csrfPending = null;
    });
  await csrfPending;
  return readCookie(CSRF_COOKIE);
}

function react(error: ApiError, silent: boolean): void {
  if (silent) return;
  if (error.status === 401) handlers.unauthorized?.(error);
  else if (error.status === 403 && error.code === 'password_change_required')
    handlers.passwordChangeRequired?.(error);
  else if (error.status === 403 && error.code === 'privacy_notice_required')
    handlers.privacyNoticeRequired?.(error);
  else if (error.status === 403 && error.code === 'module_not_licensed')
    handlers.moduleNotLicensed?.(error);
}

/**
 * The global reaction to an error of a `silent` request, for a caller that handles only some
 * errors itself: the password change of the user menu sends a 401 (account locked, session
 * ended) to the sign-in page like any other request of the app would.
 */
export function reactGlobally(error: ApiError): void {
  react(error, false);
}

/** Send one request; resolves with the parsed JSON (`undefined` for 204), rejects with ApiError. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  // The browser writes the multipart Content-Type itself (with the boundary).
  const form = typeof FormData !== 'undefined' && options.body instanceof FormData;
  if (options.body !== undefined && !form) headers['Content-Type'] = 'application/json';
  if (options.background) headers[BACKGROUND_HEADER] = '1';
  if (UNSAFE_METHODS.has(method)) {
    const token = await ensureCsrfCookie();
    if (token) headers[CSRF_HEADER] = token;
  }
  if (!options.background) lastForeground = Date.now();

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.params), {
      method,
      headers,
      credentials: 'same-origin',
      body:
        options.body === undefined
          ? null
          : form
            ? (options.body as FormData)
            : JSON.stringify(options.body),
      signal: options.signal ?? null,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    const [messageTr, messageEn] = fixedTexts('errors.network');
    throw new ApiError({ status: 0, code: 'network_error', messageTr, messageEn });
  }

  if (!response.ok) {
    const error = await errorOf(response);
    react(error, options.silent ?? false);
    throw error;
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('Content-Type') ?? '';
  if (type.includes('json')) return (await response.json()) as T;
  return (await response.text()) as T;
}

/** A file the API sent (report PDF, time-stamp token, manifest, evidence package). */
export interface DownloadedFile {
  blob: Blob;
  /** From `Content-Disposition` (path separators removed), else the fallback name. */
  filename: string;
  headers: Headers;
}

/** The file name of a `Content-Disposition` header (`filename*=UTF-8''…` preferred). */
export function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  let name: string | null = null;
  const extended = /filename\*\s*=\s*utf-8''([^;]+)/i.exec(disposition);
  if (extended?.[1]) {
    try {
      name = decodeURIComponent(extended[1].trim());
    } catch {
      name = null;
    }
  }
  if (name === null) {
    const quoted = /filename\s*=\s*"([^"]*)"/i.exec(disposition);
    const bare = /filename\s*=\s*([^;"\s]+)/i.exec(disposition);
    name = quoted?.[1] ?? bare?.[1] ?? null;
  }
  // Only a file name: no folders, no control characters.
  const cleaned = (name ?? '')
    .split(/[\\/]/)
    .pop()
    ?.replace(/[\p{Cc}\p{Cf}]/gu, '')
    .trim();
  return cleaned ? cleaned : fallback;
}

/** Bytes received so far and the announced total (`Content-Length`, `null` when unknown). */
export type DownloadProgress = (received: number, total: number | null) => void;

/**
 * The response body as a Blob, reporting each chunk's arrival. The chunks only pass through
 * (the browser builds the Blob, as with `response.blob()`); without stream support in the
 * browser the body is read in one go.
 */
async function countedBlob(response: Response, onProgress: DownloadProgress): Promise<Blob> {
  const length = Number(response.headers.get('Content-Length'));
  const total = Number.isFinite(length) && length > 0 ? length : null;
  if (!response.body || typeof TransformStream === 'undefined') {
    const blob = await response.blob();
    onProgress(blob.size, total);
    return blob;
  }
  let received = 0;
  const counted = response.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        received += chunk.byteLength;
        onProgress(received, total);
        controller.enqueue(chunk);
      },
    }),
  );
  const type = response.headers.get('Content-Type') ?? 'application/octet-stream';
  return new Response(counted, { headers: { 'Content-Type': type } }).blob();
}

/**
 * GET a file as a Blob with the session cookie (same error handling and global reactions as
 * {@link apiRequest}): the caller can read the API's integrity headers (`X-Arci-Sha256`,
 * `Repr-Digest`) and show refusals (403 `reauth_required`, 410, …) in place. `onProgress`
 * reports the bytes received (large evidence packages).
 */
export async function apiDownload(
  path: string,
  options: Pick<RequestOptions, 'params' | 'signal' | 'silent'> & {
    fallbackName: string;
    onProgress?: DownloadProgress;
  },
): Promise<DownloadedFile> {
  lastForeground = Date.now();
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.params), {
      method: 'GET',
      headers: { Accept: '*/*' },
      credentials: 'same-origin',
      signal: options.signal ?? null,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    const [messageTr, messageEn] = fixedTexts('errors.network');
    throw new ApiError({ status: 0, code: 'network_error', messageTr, messageEn });
  }
  if (!response.ok) {
    const error = await errorOf(response);
    react(error, options.silent ?? false);
    throw error;
  }
  const blob = options.onProgress
    ? await countedBlob(response, options.onProgress)
    : await response.blob();
  return {
    blob,
    filename: filenameFrom(response.headers.get('Content-Disposition'), options.fallbackName),
    headers: response.headers,
  };
}

type Options = Omit<RequestOptions, 'method' | 'body'>;

export const api = {
  get: <T>(path: string, options?: Options) => apiRequest<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: Options) =>
    apiRequest<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: Options) =>
    apiRequest<T>(path, { ...options, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, options?: Options) =>
    apiRequest<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: Options) =>
    apiRequest<T>(path, { ...options, method: 'DELETE' }),
};
