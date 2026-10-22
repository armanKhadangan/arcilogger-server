import { Lock } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import type { LicenseOut } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { currentLang } from '@/lib/i18n';
import { LICENSE_ADMIN_PERMISSION, LICENSE_PAGE, moduleName, moduleOf } from '@/lib/license';
import { cn } from '@/lib/utils';

function useModuleName(status: LicenseOut, moduleKey: string): string {
  useTranslation(); // follow language changes
  return moduleName(moduleOf(status, moduleKey), moduleKey, currentLang());
}

/**
 * The page of a module the licence does not enable (contract 8.4), shown by `ModuleGate` instead
 * of the page itself, so none of the page's requests is made.
 */
export function ModuleLockedPage({ moduleKey, status }: { moduleKey: string; status: LicenseOut }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const name = useModuleName(status, moduleKey);
  return (
    <div data-testid="module-locked">
      <PageHeader title={t('license.locked.title', { module: name })} />
      <Card className="flex max-w-3xl flex-col gap-4 p-5">
        <div className="flex items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-subtle text-muted-strong">
            <Lock aria-hidden="true" className="size-5" strokeWidth={1.75} />
          </span>
          <p className="text-sm text-foreground">{t('license.locked.text')}</p>
        </div>
        {can(LICENSE_ADMIN_PERMISSION) ? (
          <div>
            <Button asChild>
              <Link to={LICENSE_PAGE}>{t('license.locked.openPage')}</Link>
            </Button>
          </div>
        ) : (
          <p className="text-13 text-muted-strong">{t('license.banner.contactAdmin')}</p>
        )}
      </Card>
    </div>
  );
}

/**
 * A small card in place of a locked module's part of a page (the Panel's usage widgets, the CSV
 * import), with the licence page link for administrators.
 */
export function ModuleLockedCard({
  moduleKey,
  status,
  className,
  'data-testid': testId,
}: {
  moduleKey: string;
  status: LicenseOut;
  className?: string;
  'data-testid'?: string;
}) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const name = useModuleName(status, moduleKey);
  return (
    <Card className={cn('flex items-start gap-3 p-4', className)} data-testid={testId}>
      <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-strong" />
      <div className="flex min-w-0 flex-col gap-1 text-13">
        <p className="font-medium text-foreground">{t('license.locked.card', { module: name })}</p>
        {can(LICENSE_ADMIN_PERMISSION) ? (
          <Link
            to={LICENSE_PAGE}
            className="self-start font-medium text-primary-ink underline underline-offset-2"
          >
            {t('license.banner.pageLink')}
          </Link>
        ) : null}
      </div>
    </Card>
  );
}
