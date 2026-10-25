import { Palette } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useAuth } from '@/lib/auth';
import { UI_KIT_ITEM, type NavItem } from '@/nav';

/**
 * Stand-in for pages whose content arrives in a later version. The pointer to the UI kit is only
 * where it is built in (never in the shipped bundle) and for those who may open it
 * (administrators): everyone else would land on "no permission".
 */
export function PlaceholderPage({ item }: { item: NavItem }) {
  const { t } = useTranslation();
  const { canAny } = useAuth();
  const uiKit = UI_KIT_ITEM && canAny(UI_KIT_ITEM.permissions) ? UI_KIT_ITEM : null;
  return (
    <div>
      <PageHeader title={t(`nav.${item.key}`)} />
      <EmptyState
        icon={item.icon}
        title={t('placeholder.body')}
        description={uiKit ? t('placeholder.hint') : undefined}
        action={
          uiKit ? (
            <Button asChild variant="secondary" size="sm">
              <Link to={uiKit.path}>
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
