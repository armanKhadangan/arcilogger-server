import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Known audit action names (`<area>.<verb>`, docs/MVP-CONTRACTS.md section 6) with a readable
 * label in `auditActions.*`; later packages add theirs. Unknown actions show their code only.
 */
export const KNOWN_ACTIONS = [
  'auth.login',
  'auth.login_failed',
  'auth.rate_limited',
  'auth.lockout',
  'auth.totp',
  'auth.totp_failed',
  'auth.totp_setup',
  'auth.totp_enrol',
  'auth.recovery_codes_regenerate',
  'auth.password_change',
  'auth.password_change_failed',
  'auth.reauth',
  'auth.reauth_failed',
  'auth.logout',
  'privacy.accept',
  'setup.token_create',
  'setup.admin_create',
  'setup.admin_failed',
  'user.create',
  'user.update',
  'user.password_reset',
  'user.totp_reset',
  'user.unlock',
  'settings.change',
  'lookup.run',
  'lookup.run_failed',
  'lookup.view',
  'lookup.report',
  'lookup.report_download',
  'raw.view',
  'evidence.request',
  'evidence.create',
  'evidence.download',
  'evidence.purge',
  'seal.verify',
  'seal.download',
  'seal.run',
  'retention.run',
  // Licensing (contract 6.7): records of actions, never a licence mark in evidence.
  'license.trial_start',
  'license.install',
  'license.remove',
  'license.refuse',
  'license.limit_refused',
  'license.limit_exceeded',
  'license.limit_ok',
  'license.state',
] as const;

/** Prefixes offered in the action filter (the API filters by prefix). */
export const ACTION_PREFIXES = [
  'auth.',
  'user.',
  'setup.',
  'privacy.',
  'settings.',
  'lookup.',
  'raw.',
  'evidence.',
  'seal.',
  'retention.',
  'license.',
] as const;

/** i18n key of an action's label (`auth.login` → `auditActions.auth_login`). */
export function actionKey(action: string): string {
  return `auditActions.${action.replace(/\./g, '_')}`;
}

/** Label of an action in the current language, `null` for actions without one. */
export function useActionLabel(): (action: string) => string | null {
  const { t, i18n } = useTranslation();
  return useCallback(
    (action: string) => {
      const key = actionKey(action);
      return i18n.exists(key) ? t(key) : null;
    },
    [t, i18n],
  );
}
