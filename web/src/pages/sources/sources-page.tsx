import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';

/**
 * Kaynaklar in the main menu (spec 12.3 page 3; `sources.read`): the state of every device that
 * sends logs. It only reads; the sources are edited in Ayarlar → Kaynaklar
 * (`pages/settings/sources-page.tsx`, `SourcesPage`).
 */
export function SourcesHealthPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col">
      <PageHeader title={t('nav.sources')} description={t('sources.description')} />
    </div>
  );
}
