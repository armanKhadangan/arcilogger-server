import { ShieldOff } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';

/** Shown instead of a page the user's role may not open (the API refuses it with 403 too). */
export function ForbiddenPage() {
  const { t } = useTranslation();
  return (
    <div>
      <h1 className="sr-only">{t('forbidden.title')}</h1>
      <EmptyState
        icon={ShieldOff}
        title={t('forbidden.title')}
        description={t('forbidden.body')}
        action={
          <Button asChild size="sm">
            <Link to="/">{t('notFound.home')}</Link>
          </Button>
        }
      />
    </div>
  );
}
