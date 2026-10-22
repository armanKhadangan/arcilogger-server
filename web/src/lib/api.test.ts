import { act } from '@testing-library/react';

import { errorResponse, installFetch, jsonResponse, setCsrfCookie } from '@/test/api-mock';

import {
  api,
  apiDownload,
  ApiError,
  BACKGROUND_HEADER,
  buildUrl,
  CSRF_HEADER,
  errorMessage,
  lastForegroundRequestAt,
  passwordProblems,
  passwordProblemText,
  readCookie,
  setApiEventHandlers,
} from './api';
import { setLanguage } from './i18n';

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfCookie(null);
  act(() => {
    setLanguage('tr');
  });
});

describe('buildUrl', () => {
  it('prefixes /api/v1 and leaves out empty parameters', () => {
    expect(buildUrl('/users')).toBe('/api/v1/users');
    expect(
      buildUrl('/audit', {
        action: 'auth.',
        actor: '',
        target: null,
        cursor: undefined,
        limit: 100,
      }),
    ).toBe('/api/v1/audit?action=auth.&limit=100');
  });

  it('encodes offsets and special characters', () => {
    expect(buildUrl('/audit', { from: '2026-10-12T00:00:00+03:00', target: 'user:2 & x' })).toBe(
      '/api/v1/audit?from=2026-10-12T00%3A00%3A00%2B03%3A00&target=user%3A2+%26+x',
    );
  });
});

describe('readCookie', () => {
  it('finds the CSRF cookie among others and decodes it', () => {
    document.cookie = 'other=1; path=/';
    setCsrfCookie('abc%2Fdef');
    expect(readCookie('arci_csrf')).toBe('abc/def');
    expect(readCookie('missing')).toBeNull();
    document.cookie = 'other=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });
});

describe('apiRequest headers', () => {
  it('sends the CSRF header on POST, PATCH, PUT and DELETE but not on GET', async () => {
    setCsrfCookie('csrf-token-1');
    const calls = installFetch(() => jsonResponse(200, { ok: true }));
    await api.get('/auth/me');
    await api.post('/auth/login', { username: 'a', password: 'b' });
    await api.patch('/users/2', { role: 'viewer' });
    await api.put('/x', {});
    await api.delete('/x');
    expect(calls.map((call) => [call.method, call.headers.get(CSRF_HEADER)])).toEqual([
      ['GET', null],
      ['POST', 'csrf-token-1'],
      ['PATCH', 'csrf-token-1'],
      ['PUT', 'csrf-token-1'],
      ['DELETE', 'csrf-token-1'],
    ]);
    expect(calls[1]?.body).toEqual({ username: 'a', password: 'b' });
    expect(calls[1]?.headers.get('Content-Type')).toBe('application/json');
  });

  it('sends a FormData body as multipart: no JSON content type, the CSRF header still', async () => {
    setCsrfCookie('csrf-token-2');
    const calls = installFetch(() => jsonResponse(200, { ok: true }));
    const form = new FormData();
    form.append('file', new File(['name;department\r\n'], 'kisiler.csv', { type: 'text/csv' }));
    await api.post('/imports/persons', form, { params: { dry_run: true } });
    const call = calls[0];
    expect(call?.url).toBe('/api/v1/imports/persons?dry_run=true');
    expect(call?.body).toBe(form);
    // The browser adds `multipart/form-data; boundary=…` itself.
    expect(call?.headers.get('Content-Type')).toBeNull();
    expect(call?.headers.get(CSRF_HEADER)).toBe('csrf-token-2');
  });

  it('fetches a CSRF cookie first when the browser has none', async () => {
    const calls = installFetch((request) => {
      if (request.path === '/api/v1/health') {
        setCsrfCookie('fresh-token'); // what the API's Set-Cookie does
        return jsonResponse(200, { status: 'ok' });
      }
      return jsonResponse(200, {});
    });
    await api.post('/auth/logout');
    expect(calls.map((call) => call.path)).toEqual(['/api/v1/health', '/api/v1/auth/logout']);
    expect(calls[0]?.headers.get(BACKGROUND_HEADER)).toBe('1');
    expect(calls[1]?.headers.get(CSRF_HEADER)).toBe('fresh-token');
  });

  it('marks background requests and does not count them as activity', async () => {
    const calls = installFetch(() => jsonResponse(200, {}));
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    await api.get('/auth/me');
    expect(lastForegroundRequestAt()).toBe(1_000);
    vi.spyOn(Date, 'now').mockReturnValue(9_000);
    await api.get('/auth/me', { background: true });
    expect(lastForegroundRequestAt()).toBe(1_000);
    expect(calls[0]?.headers.get(BACKGROUND_HEADER)).toBeNull();
    expect(calls[1]?.headers.get(BACKGROUND_HEADER)).toBe('1');
    vi.restoreAllMocks();
  });

  it('returns undefined for 204 and parses JSON otherwise', async () => {
    installFetch((request) =>
      request.path.endsWith('logout') ? jsonResponse(204, null) : jsonResponse(200, { a: 1 }),
    );
    setCsrfCookie('t');
    await expect(api.post('/auth/logout')).resolves.toBeUndefined();
    await expect(api.get('/x')).resolves.toEqual({ a: 1 });
  });
});

describe('error mapping', () => {
  it('turns the API error format into ApiError with both languages', async () => {
    installFetch(() =>
      errorResponse(401, 'login_failed', {
        tr: 'Giriş başarısız.',
        en: 'Sign-in failed.',
      }),
    );
    const error = await api.post('/auth/login', {}, { silent: true }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect([apiError.status, apiError.code]).toEqual([401, 'login_failed']);
    expect(apiError.localized).toBe('Giriş başarısız.');
    act(() => {
      setLanguage('en');
    });
    expect(apiError.localized).toBe('Sign-in failed.');
    expect(errorMessage(apiError)).toBe('Sign-in failed.');
  });

  it('keeps 422 details and the Retry-After seconds of a 429', async () => {
    installFetch((request) =>
      request.path.endsWith('password')
        ? errorResponse(422, 'password_rejected', {
            details: [
              { rule: 'length', message_tr: 'Çok kısa.', message_en: 'Too short.' },
              { rule: 'common', message_tr: 'Yaygın.', message_en: 'Common.' },
              { junk: true },
            ],
          })
        : errorResponse(429, 'rate_limited', { headers: { 'Retry-After': '42' } }),
    );
    setCsrfCookie('t');
    const rejected = await api
      .post('/auth/password', {}, { silent: true })
      .catch((e: unknown) => e);
    expect(passwordProblems(rejected).map((problem) => problem.rule)).toEqual(['length', 'common']);
    expect(passwordProblemText(rejected)).toBe('Çok kısa. Yaygın.');
    const limited = (await api
      .post('/auth/login', {}, { silent: true })
      .catch((e: unknown) => e)) as ApiError;
    expect(limited.retryAfter).toBe(42);
    expect(passwordProblemText(limited)).toBeNull();
  });

  it('describes a non-JSON error page (reverse proxy) in both languages', async () => {
    installFetch(() => new Response('<html>Bad Gateway</html>', { status: 502 }));
    const error = (await api.get('/x').catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe('http_502');
    expect(error.messageTr).toContain('HTTP 502');
    expect(error.messageEn).toContain('HTTP 502');
  });

  it('reports an unreachable server as network_error (status 0)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );
    const error = (await api.get('/x').catch((e: unknown) => e)) as ApiError;
    expect([error.status, error.code]).toEqual([0, 'network_error']);
    expect(error.messageTr).toMatch(/ulaşılamadı/);
  });

  it('passes an abort through unchanged', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new DOMException('aborted', 'AbortError'))),
    );
    await expect(api.get('/x')).rejects.toHaveProperty('name', 'AbortError');
  });

  it('gives a generic text for errors that are not ApiError', () => {
    expect(errorMessage(new Error('boom'))).toBe('Beklenmeyen bir hata oluştu.');
  });
});

describe('global reactions', () => {
  function handlers() {
    const unauthorized = vi.fn();
    const passwordChangeRequired = vi.fn();
    const privacyNoticeRequired = vi.fn();
    const remove = setApiEventHandlers({
      unauthorized,
      passwordChangeRequired,
      privacyNoticeRequired,
    });
    return { unauthorized, passwordChangeRequired, privacyNoticeRequired, remove };
  }

  it('reports 401 to the unauthorized handler with the code', async () => {
    const h = handlers();
    installFetch(() => errorResponse(401, 'session_idle'));
    await expect(api.get('/audit')).rejects.toBeInstanceOf(ApiError);
    expect(h.unauthorized).toHaveBeenCalledTimes(1);
    expect((h.unauthorized.mock.calls[0]?.[0] as ApiError).code).toBe('session_idle');
    h.remove();
  });

  it('routes the two special 403 codes and ignores other 403s', async () => {
    const h = handlers();
    const codes = ['password_change_required', 'privacy_notice_required', 'forbidden'];
    let index = 0;
    installFetch(() => errorResponse(403, codes[index++] ?? 'forbidden'));
    for (let i = 0; i < codes.length; i += 1) await api.get('/x').catch(() => undefined);
    expect(h.passwordChangeRequired).toHaveBeenCalledTimes(1);
    expect(h.privacyNoticeRequired).toHaveBeenCalledTimes(1);
    expect(h.unauthorized).not.toHaveBeenCalled();
    h.remove();
  });

  it('stays quiet for silent requests (sign-in steps handle their own 401)', async () => {
    const h = handlers();
    installFetch(() => errorResponse(401, 'totp_invalid'));
    await api.post('/auth/totp', { code: '1' }, { silent: true }).catch(() => undefined);
    expect(h.unauthorized).not.toHaveBeenCalled();
    h.remove();
  });

  it('removing the handlers stops the reactions', async () => {
    const h = handlers();
    h.remove();
    installFetch(() => errorResponse(401, 'session_revoked'));
    await api.get('/x').catch(() => undefined);
    expect(h.unauthorized).not.toHaveBeenCalled();
  });
});

describe('apiDownload', () => {
  function zipResponse(chunks: Uint8Array[], length: string | null) {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    });
    return new Response(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="ARCILOGGER-kanit-KP-1.zip"',
        ...(length === null ? {} : { 'Content-Length': length }),
      },
    });
  }

  it('reports the bytes received while the file arrives and returns all of it', async () => {
    installFetch(() => zipResponse([new Uint8Array([1, 2, 3]), new Uint8Array([4, 5])], '5'));
    const seen: [number, number | null][] = [];
    const file = await apiDownload('/evidence/1/download', {
      fallbackName: 'paket.zip',
      onProgress: (received, total) => {
        seen.push([received, total]);
      },
    });
    expect(seen).toEqual([
      [3, 5],
      [5, 5],
    ]);
    expect(file.filename).toBe('ARCILOGGER-kanit-KP-1.zip');
    expect(new Uint8Array(await file.blob.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3, 4, 5]));
  });

  it('reports an unknown total without Content-Length', async () => {
    installFetch(() => zipResponse([new Uint8Array([9, 9])], null));
    const seen: [number, number | null][] = [];
    await apiDownload('/evidence/1/download', {
      fallbackName: 'paket.zip',
      onProgress: (received, total) => {
        seen.push([received, total]);
      },
    });
    expect(seen).toEqual([[2, null]]);
  });

  it('refusals are errors with the server text, without progress', async () => {
    installFetch(() => errorResponse(410, 'evidence_gone', { tr: 'Paketin süresi doldu.' }));
    const onProgress = vi.fn();
    await expect(
      apiDownload('/evidence/3/download', { fallbackName: 'paket.zip', onProgress }),
    ).rejects.toMatchObject({ status: 410, code: 'evidence_gone' });
    expect(onProgress).not.toHaveBeenCalled();
  });
});
