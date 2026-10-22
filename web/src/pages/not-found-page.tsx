import { MapPinOff } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={MapPinOff}
      title={t('notFound.title')}
      description={t('notFound.body')}
      action={
        <Button asChild size="sm">
          <Link to="/">{t('notFound.home')}</Link>
        </Button>
      }
    />
  );
}
