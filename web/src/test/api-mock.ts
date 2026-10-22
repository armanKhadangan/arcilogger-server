/**
 * Test doubles for the API: a `fetch` stub that records every request, response builders in the
 * API's real formats, and `/auth/me` answers per role (permissions from the generated contract).
 */

import type { Me, Role, SignInStep } from '@/lib/api-types';
import { ROLES } from '@/lib/permissions';

export interface RecordedRequest {
  method: string;
  /** Path and query without the origin, e.g. `/api/v1/audit?limit=100`. */
  url: string;
  path: string;
  params: URLSearchParams;
  headers: Headers;
  body: unknown;
}

export type MockHandler = (request: RecordedRequest) => Response | Promise<Response>;

export function jsonResponse(status: number, body: unknown, headers?: Record<string, string>) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

export function errorResponse(
  status: number,
  code: string,
  extra: { tr?: string; en?: string; details?: unknown; headers?: Record<string, string> } = {},
) {
  return jsonResponse(
    status,
    {
      error: {
        code,
        message_tr: extra.tr ?? `TR ${code}`,
        message_en: extra.en ?? `EN ${code}`,
        ...(extra.details === undefined ? {} : { details: extra.details }),
      },
    },
    extra.headers,
  );
}

/** Replace `fetch`; returns the recorded requests. Unknown requests answer 404. */
export function installFetch(handler: MockHandler): RecordedRequest[] {
  const calls: RecordedRequest[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
        'http://127.0.0.1',
      );
      const rawBody = init.body;
      const request: RecordedRequest = {
        method: (init.method ?? 'GET').toUpperCase(),
        url: `${url.pathname}${url.search}`,
        path: url.pathname,
        params: url.searchParams,
        headers: new Headers(init.headers),
        body:
          typeof rawBody === 'string'
            ? (JSON.parse(rawBody) as unknown)
            : rawBody instanceof FormData
              ? rawBody
              : undefined,
      };
      calls.push(request);
      return handler(request);
    }),
  );
  return calls;
}

export function makeMe(role: Role = 'admin', overrides: Partial<Me> = {}): Me {
  const info = ROLES.find((item) => item.id === role);
  if (!info) throw new Error(`unknown role ${role}`);
  const next: SignInStep = overrides.next ?? 'done';
  const passed = next !== 'totp' && next !== 'totp_setup';
  return {
    user: {
      id: 1,
      username: 'tolga',
      display_name: 'Tolga Yılmaz',
      role,
      role_label_tr: info.label_tr,
      role_label_en: info.label_en,
    },
    email: null,
    permissions: passed ? [...info.permissions] : [],
    must_change_password: next === 'password_change',
    mfa: {
      passed,
      enabled: true,
      required: role === 'admin' || role === 'auditor',
      recovery_codes_left: 10,
    },
    privacy_notice: { version: '1', accepted: true, required: role !== 'viewer' },
    idle_timeout_minutes: 15,
    session: {
      created_at: '2026-10-12T07:00:00Z',
      expires_at: '2026-10-12T19:00:00Z',
      reauthenticated_at: null,
    },
    next,
    // The tests' fictional organisation; the product ships no name (set at installation).
    site_name: 'Örnek A.Ş.',
    ...overrides,
  };
}

/** Set the readable CSRF cookie like the API does. */
export function setCsrfCookie(value: string | null): void {
  document.cookie =
    value === null
      ? 'arci_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
      : `arci_csrf=${value}; path=/`;
}
