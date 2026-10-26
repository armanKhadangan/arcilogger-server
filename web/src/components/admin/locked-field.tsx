import { Lock } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import type { LockedPath } from '@/lib/settings-types';

/**
 * A value the browser may not change (contract 2.17 "Field states": `locked`): shown with a lock
 * and "Bu değer güvenlik nedeniyle yalnızca sunucu konsolundan değiştirilebilir".
 */
export function LockedValue({ label, value }: { label: string; value: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1" data-testid="locked-value">
      <span className="text-13 font-medium text-foreground">{label}</span>
      <span className="flex items-center gap-2 text-sm text-foreground">
        <Lock aria-hidden="true" className="size-4 shrink-0 text-muted-strong" />
        <span className="min-w-0 font-mono text-13 break-all">{value}</span>
      </span>
      <span className="text-xs text-muted">{t('admin.locked.hint')}</span>
    </div>
  );
}

/** The console-only paths of a section (`GET /admin/config` `locked`), with their reasons. */
export function LockedList({ paths }: { paths: LockedPath[] }) {
  const { t } = useTranslation();
  if (paths.length === 0) return null;
  return (
    <div className="flex flex-col gap-2" data-testid="locked-paths">
      <p className="flex items-center gap-2 text-13 text-muted-strong">
        <Lock aria-hidden="true" className="size-4 shrink-0" />
        {t('admin.locked.hint')}
      </p>
      <ul className="flex flex-wrap gap-2">
        {paths.map((item) => (
          <li
            key={item.path}
            className="rounded-md border border-border bg-subtle px-2 py-1 text-xs"
            title={t(`admin.locked.reasons.${item.reason}`)}
          >
            <span className="font-mono">{item.path}</span>
            <span className="ml-1 text-muted-strong">
              ({t(`admin.locked.reasons.${item.reason}`)})
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
