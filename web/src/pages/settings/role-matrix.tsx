import { Check, Minus, ShieldAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { currentLang } from '@/lib/i18n';
import {
  PERMISSIONS,
  permissionLabel,
  PRIVACY_PERMISSIONS,
  roleHas,
  ROLES,
} from '@/lib/permissions';

/**
 * Read-only role matrix (spec 13), exactly as the server enforces it (`src/generated/roles.json`
 * from `arcilogger_api.security`). Roles are fixed in this version; changing the matrix is a
 * decision of the owner (docs/roles.md, open questions).
 */
export function RoleMatrix() {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <div className="flex flex-col gap-3">
      <p className="max-w-3xl text-13 text-muted">{t('roles.intro')}</p>
      <div className="overflow-x-auto rounded-lg border border-border bg-surface">
        <table className="w-full border-separate border-spacing-0 text-13">
          <caption className="sr-only">{t('roles.caption')}</caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky left-0 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
              >
                {t('roles.permission')}
              </th>
              {ROLES.map((role) => (
                <th
                  key={role.id}
                  scope="col"
                  className="border-b border-border bg-subtle px-3 py-2 text-center text-xs font-medium whitespace-nowrap text-muted-strong"
                >
                  {lang === 'en' ? role.label_en : role.label_tr}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PERMISSIONS.map((permission) => (
              <tr key={permission.id}>
                <th
                  scope="row"
                  className="border-b border-border px-3 py-2 text-left font-normal text-foreground"
                >
                  <span className="flex flex-wrap items-center gap-2">
                    {permissionLabel(permission, lang)}
                    <code className="font-mono text-xs text-muted">{permission.id}</code>
                    {PRIVACY_PERMISSIONS.includes(permission.id) ? (
                      <Badge tone="warning" icon={ShieldAlert}>
                        {t('roles.personalData')}
                      </Badge>
                    ) : null}
                  </span>
                </th>
                {ROLES.map((role) => {
                  const granted = roleHas(role.id, permission.id);
                  return (
                    <td key={role.id} className="border-b border-border px-3 py-2 text-center">
                      {granted ? (
                        <Check aria-hidden="true" className="mx-auto size-4 text-success" />
                      ) : (
                        <Minus aria-hidden="true" className="mx-auto size-4 text-muted" />
                      )}
                      <span className="sr-only">{granted ? t('roles.yes') : t('roles.no')}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
