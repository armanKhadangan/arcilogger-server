/**
 * Where to go after signing in. The target comes from the URL (`/login?next=…`), so it is
 * attacker-controlled: only same-origin paths are accepted (no `//host`, no `/\host` — browsers
 * read both as another host —, no scheme, no control characters), and never the sign-in or
 * setup pages themselves.
 */

const AUTH_PAGES = ['/login', '/setup'];

export function safeNext(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return '/';
  // eslint-disable-next-line no-control-regex -- control characters are exactly what we refuse
  if (/[\u0000-\u001f\u007f]/.test(raw)) return '/';
  const path = raw.split(/[?#]/, 1)[0] ?? '/';
  if (AUTH_PAGES.some((page) => path === page || path.startsWith(`${page}/`))) return '/';
  return raw;
}

/** Codes of a 401 that mean "the session timed out" (shown as such on the sign-in page). */
export const TIMEOUT_CODES: readonly string[] = ['session_idle', 'session_expired'];

/**
 * 401 codes the sign-in page explains (`login.reasons.<code>`). Others (no cookie, unknown
 * session) need no explanation: the user simply was not signed in.
 */
export const REASON_CODES: readonly string[] = [
  ...TIMEOUT_CODES,
  'session_revoked',
  'user_inactive',
  'account_locked',
];

/** The `reason` to put into the sign-in URL for a 401 code, or `null`. */
export function reasonFor(code: string): string | null {
  return REASON_CODES.includes(code) ? code : null;
}

/** `/login?next=…&reason=…` (both optional). */
export function loginUrl(next?: string | null, reason?: string | null): string {
  const params = new URLSearchParams();
  const target = safeNext(next);
  if (target !== '/') params.set('next', target);
  if (reason) params.set('reason', reason);
  const query = params.toString();
  return query ? `/login?${query}` : '/login';
}
