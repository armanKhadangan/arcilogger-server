/**
 * Re-authentication for sensitive actions (spec 13: credentials, retention, evidence download;
 * here: user management).
 *
 * The server decides: an endpoint guarded by `require_recent_auth` answers 403
 * `reauth_required` when the last password (+ TOTP) confirmation is older than five minutes.
 * `withReauth(action)` runs the action, and on exactly that answer asks for the password (and the
 * TOTP code when enrolled) in a dialog, calls `POST /auth/reauth` and runs the action once more.
 * Cancelling rejects with {@link ReauthCancelledError}, which callers ignore silently.
 *
 *     const { withReauth } = useReauth();
 *     await withReauth(() => createUser.mutateAsync(body));
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { ReauthDialog } from '@/components/auth/reauth-dialog';

import { isApiError } from './api';

export class ReauthCancelledError extends Error {
  constructor() {
    super('re-authentication cancelled');
    this.name = 'ReauthCancelledError';
  }
}

export function isReauthCancelled(error: unknown): boolean {
  return error instanceof ReauthCancelledError;
}

interface ReauthContextValue {
  /** Ask for the password now; resolves `true` once `/auth/reauth` succeeded. */
  confirm: () => Promise<boolean>;
  /** Run `action`; on 403 `reauth_required` confirm and run it once more. */
  withReauth: <T>(action: () => Promise<T>) => Promise<T>;
}

const ReauthContext = createContext<ReauthContextValue | null>(null);

export function ReauthProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  // One dialog for any number of concurrent requests: they all wait for the same answer.
  const pending = useRef<{ promise: Promise<boolean>; resolve: (ok: boolean) => void } | null>(
    null,
  );

  const confirm = useCallback(() => {
    if (!pending.current) {
      let resolve: (ok: boolean) => void = () => undefined;
      const promise = new Promise<boolean>((done) => {
        resolve = done;
      });
      pending.current = { promise, resolve };
      setOpen(true);
    }
    return pending.current.promise;
  }, []);

  const finish = useCallback((ok: boolean) => {
    const current = pending.current;
    pending.current = null;
    setOpen(false);
    current?.resolve(ok);
  }, []);

  const withReauth = useCallback(
    async <T,>(action: () => Promise<T>): Promise<T> => {
      try {
        return await action();
      } catch (error) {
        if (!isApiError(error, 'reauth_required')) throw error;
        if (!(await confirm())) throw new ReauthCancelledError();
        return action();
      }
    },
    [confirm],
  );

  const value = useMemo(() => ({ confirm, withReauth }), [confirm, withReauth]);
  return (
    <ReauthContext.Provider value={value}>
      {children}
      <ReauthDialog open={open} onDone={finish} />
    </ReauthContext.Provider>
  );
}

export function useReauth(): ReauthContextValue {
  const context = useContext(ReauthContext);
  if (!context) throw new Error('useReauth must be used inside <ReauthProvider>');
  return context;
}
