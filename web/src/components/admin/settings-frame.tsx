import { ArrowLeft } from 'lucide-react';
import { useEffect, useId } from 'react';
import type { ReactNode } from 'react';
import { Link, useBlocker } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useAdminConfig, useAdminStatus } from '@/lib/api-hooks';

import { AgentBanner, AgentStatusChip, ConfigProblems, RestartBanner } from './agent-status';

/**
 * A page of "Ayarlar" (contract 2.17): the way back, the title with the administration
 * service's chip, and why nothing can be saved when the service is not running (the forms are
 * read-only then; the banner names the console path) or the configuration does not load.
 */
export function SettingsFrame({
  title,
  description,
  children,
  machine = true,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  /** The page changes the machine configuration (chip and banners); else only the way back. */
  machine?: boolean;
}) {
  const { t } = useTranslation();
  const status = useAdminStatus(machine);
  const config = useAdminConfig(machine);
  return (
    <div>
      <Button asChild variant="link" size="sm" className="mb-2">
        <Link to="/settings">
          <ArrowLeft aria-hidden="true" />
          {t('nav.settings')}
        </Link>
      </Button>
      <PageHeader
        title={title}
        description={description}
        actions={machine ? <AgentStatusChip status={status.data} /> : undefined}
      />
      <div className="flex flex-col gap-4">
        {machine ? (
          <>
            <AgentBanner status={status.data} />
            <ConfigProblems problems={config.data?.problems} levels={['error']} />
            <RestartBanner status={status.data} />
          </>
        ) : null}
        {children}
      </div>
    </div>
  );
}

/** One section of a settings page: a card with a level-2 heading. */
export function SettingsCard({
  title,
  description,
  children,
  testId,
  actions,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  testId?: string;
  actions?: ReactNode;
}) {
  const id = useId();
  return (
    <Card className="flex flex-col gap-4 p-5" data-testid={testId}>
      <section aria-labelledby={id} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <h2 id={id} className="text-base font-semibold text-foreground">
              {title}
            </h2>
            {description ? <p className="text-13 text-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </div>
        {children}
      </section>
    </Card>
  );
}

/**
 * The unsaved-change guard (2.17 `dirty`): leaving the page in the app asks first, closing or
 * reloading the tab gets the browser's own question.
 */
export function UnsavedChangesGuard({ when }: { when: boolean }) {
  const { t } = useTranslation();
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      when && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (!when) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => {
      window.removeEventListener('beforeunload', warn);
    };
  }, [when]);
  return (
    <ConfirmDialog
      open={blocker.state === 'blocked'}
      onOpenChange={(open) => {
        if (!open && blocker.state === 'blocked') blocker.reset();
      }}
      title={t('admin.unsaved.title')}
      description={t('admin.unsaved.body')}
      confirmLabel={t('admin.unsaved.leave')}
      danger
      onConfirm={() => {
        if (blocker.state === 'blocked') blocker.proceed();
      }}
    />
  );
}
