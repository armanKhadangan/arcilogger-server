import { useEffect } from 'react';
import { useBlocker } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ConfirmDialog } from '@/components/ui/confirm-dialog';

interface UnsavedChangesGuardProps {
  when: boolean;
  /**
   * The paths that stay on this page (`/lookup` covers `/lookup/12`); by default only the same
   * path.
   */
  area?: string;
  title?: string;
  description?: string;
}

function within(pathname: string, area: string): boolean {
  return pathname === area || pathname.startsWith(`${area}/`);
}

/**
 * The unsaved-change guard (2.17 `dirty`): leaving the page in the app asks first, closing or
 * reloading the tab gets the browser's own question.
 */
export function UnsavedChangesGuard({ when, area, title, description }: UnsavedChangesGuardProps) {
  const { t } = useTranslation();
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      when &&
      (area === undefined
        ? currentLocation.pathname !== nextLocation.pathname
        : !within(nextLocation.pathname, area)),
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
      title={title ?? t('admin.unsaved.title')}
      description={description ?? t('admin.unsaved.body')}
      confirmLabel={t('admin.unsaved.leave')}
      danger
      onConfirm={() => {
        if (blocker.state === 'blocked') blocker.proceed();
      }}
    />
  );
}
