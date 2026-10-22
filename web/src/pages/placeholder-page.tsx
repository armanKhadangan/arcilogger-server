import { Palette } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useAuth } from '@/lib/auth';
import { UI_KIT_ITEM, type NavItem } from '@/nav';

/**
 * Stand-in for pages whose content arrives in a later milestone. The pointer to the UI kit is
 * only for those who may open it (administrators): everyone else would land on "no permission".
 */
export function PlaceholderPage({ item }: { item: NavItem }) {
  const { t } = useTranslation();
  const { canAny } = useAuth();
  const uiKit = canAny(UI_KIT_ITEM.permissions);
  return (
    <div>
      <PageHeader title={t(`nav.${item.key}`)} />
      <EmptyState
        icon={item.icon}
        title={t('placeholder.body', { milestone: item.milestone })}
        description={uiKit ? t('placeholder.hint') : undefined}
        action={
          uiKit ? (
            <Button asChild variant="secondary" size="sm">
              <Link to={UI_KIT_ITEM.path}>
                <Palette aria-hidden="true" />
                {t('placeholder.openUiKit')}
              </Link>
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}
