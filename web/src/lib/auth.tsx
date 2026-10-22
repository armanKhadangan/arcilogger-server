/**
 * Who is signed in, what they may see, and what happens when the session ends.
 *
 * - `RequireAuth` guards the application shell: it reads `GET /auth/me` and sends anyone without
 *   a complete sign-in (no session, TOTP step open, forced password change) to `/login`, keeping
 *   the target URL. Inside it, `useAuth()` returns the user and the permission checks.
 * - `RequirePermission` guards one page; the menu hides what the user may not open
 *   (`visibleNavItems`). Both are conveniences: the API refuses with 403 anyway.
 * - `ApiEventsBridge` installs the global reactions of `lib/api.ts`: a 401 empties the query cache
 *   (no personal data stays in memory) and goes to the sign-in page — with "Oturumunuz zaman
 *   aşımına uğradı" when the server reports an idle or expired session.
 * - The idle watch asks the server (in the background, so the question itself is not activity)
 *   once the idle time has passed without a request from the user: an unattended screen then
 *   closes itself instead of showing personal data until the next click.
 */

import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { ForbiddenPage } from '@/pages/forbidden-page';

import { api, errorMessage, isApiError, lastForegroundRequestAt, setApiEventHandlers } from './api';
import { queryKeys, useMe } from './api-hooks';
import type { Me } from './api-types';
import { hasAny } from './permissions';
import { loginUrl, reasonFor } from './redirect';
import { writePreference } from './storage';

/** Remembered so the sign-in page can show the real idle minutes before the next sign-in. */
export const IDLE_MINUTES_KEY = 'arci.idleMinutes';
/** How often the idle watch looks at the clock. */
export const IDLE_CHECK_MS = 30_000;
/** Slack after the idle limit before asking the server (clock and request timing). */
const IDLE_GRACE_MS = 5_000;

interface AuthContextValue {
  me: Me;
  permissions: ReadonlySet<string>;
  can: (permission: string) => boolean;
  canAny: (permissions: readonly string[]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ me, children }: { me: Me; children: ReactNode }) {
  const value = useMemo<AuthContextValue>(() => {
    const permissions = new Set(me.permissions);
    return {
      me,
      permissions,
      can: (permission) => permissions.has(permission),
      canAny: (wanted) => hasAny(permissions, wanted),
    };
  }, [me]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <RequireAuth>');
  return context;
}

function currentTarget(location: { pathname: string; search: string; hash: string }): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

function FullPageLoading() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen items-center justify-center bg-background" role="status">
      <span className="text-sm text-muted">{t('common.loading')}</span>
    </div>
  );
}

/**
 * Why the last session ended (401 code), kept until the next complete sign-in. When a page's
 * request reports `session_idle`, the session is gone on the server, so the `/auth/me` asked
 * right afterwards only says `not_authenticated`; the sign-in page should still explain the
 * timeout, whichever of the two redirects lands last.
 */
let sessionEndReason: string | null = null;

/** Only for users who completed the sign-in; everyone else goes to `/login`. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const location = useLocation();
  const me = useMe();

  if (me.isPending) return <FullPageLoading />;
  if (me.isError) {
    if (isApiError(me.error) && me.error.status === 401) {
      // The first explained ending wins: the redirect of the failed request and this one
      // must show the same message.
      const reason = sessionEndReason ?? reasonFor(me.error.code);
      return <Navigate to={loginUrl(currentTarget(location), reason)} replace />;
    }
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-6">
        <EmptyState
          icon={RefreshCw}
          title={t('auth.loadFailed')}
          description={errorMessage(me.error)}
          action={
            <Button size="sm" onClick={() => void me.refetch()}>
              {t('common.retry')}
            </Button>
          }
        />
      </div>
    );
  }
  if (me.data.next !== 'done' && me.data.next !== 'privacy_notice') {
    return <Navigate to={loginUrl(currentTarget(location))} replace />;
  }
  return (
    <AuthProvider me={me.data}>
      <IdleWatch minutes={me.data.idle_timeout_minutes} />
      {children}
    </AuthProvider>
  );
}

/** A page for holders of any of `anyOf`; others see "no permission" (and the API says 403). */
export function RequirePermission({
  anyOf,
  children,
}: {
  anyOf: readonly string[];
  children: ReactNode;
}) {
  const { canAny } = useAuth();
  return canAny(anyOf) ? children : <ForbiddenPage />;
}

function IdleWatch({ minutes }: { minutes: number }) {
  useEffect(() => {
    sessionEndReason = null; // signed in completely: the last ending is explained
  }, []);

  useEffect(() => {
    writePreference(IDLE_MINUTES_KEY, String(minutes));
    const limit = minutes * 60_000 + IDLE_GRACE_MS;
    const timer = window.setInterval(() => {
      if (Date.now() - lastForegroundRequestAt() < limit) return;
      // Background: the check itself must not count as activity. A 401 reaches the global
      // handler (ApiEventsBridge), which shows the timeout message on the sign-in page.
      api.get<Me>('/auth/me', { background: true }).catch(() => undefined);
    }, IDLE_CHECK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [minutes]);
  return null;
}

const OUTSIDE_PAGES = ['/login', '/setup'];

/**
 * Global reactions to 401 / 403 answers (see `lib/api.ts`). Mount once inside the router.
 *
 * Several requests of a page usually fail together when the session ends; only the first one
 * leads to the sign-in page (with its reason and the page to return to), the rest find the
 * browser already there and change nothing.
 */
export function ApiEventsBridge() {
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const here = useRef(location);
  here.current = location;

  useEffect(() => {
    const outside = () => OUTSIDE_PAGES.includes(here.current.pathname);
    /** Go to sign-in once; later failures of the same page arrive before the next render. */
    const toLogin = (reason: string | null) => {
      const target = currentTarget(here.current);
      here.current = { ...here.current, pathname: '/login', search: '', hash: '' };
      void navigate(loginUrl(target, reason), { replace: true });
    };
    return setApiEventHandlers({
      unauthorized: (error) => {
        // Already on (or on the way to) the sign-in page: its own queries must stay.
        if (outside()) return;
        sessionEndReason = reasonFor(error.code);
        // Cancel first: a request still running must not put data back into the cache.
        void client.cancelQueries();
        client.clear();
        toLogin(sessionEndReason);
      },
      passwordChangeRequired: () => {
        void client.invalidateQueries({ queryKey: queryKeys.me });
        if (!outside()) toLogin(null);
      },
      privacyNoticeRequired: () => {
        // The shell shows the KVKK notice as soon as /auth/me says it is not accepted.
        void client.invalidateQueries({ queryKey: queryKeys.me });
      },
      moduleNotLicensed: () => {
        // A stale licence state: the menu marks, module gates and banners follow the new one.
        void client.invalidateQueries({ queryKey: queryKeys.license });
      },
    });
  }, [client, navigate]);
  return null;
}

/** "Çıkış": end the session on the server, forget every cached answer, go to sign-in. */
export function useSignOut() {
  const client = useQueryClient();
  const navigate = useNavigate();
  return useCallback(async () => {
    try {
      await api.post('/auth/logout', undefined, { silent: true });
    } catch {
      // The session may already be gone; the cookies are cleared by the server either way.
    }
    await client.cancelQueries();
    client.clear();
    void navigate('/login', { replace: true });
  }, [client, navigate]);
}
