import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';

/**
 * Arama (spec 12.3 page 5; `search.read`): field and text search over the loaded log lines, each
 * row leading to its raw line. The route carries `handle.timeRange`, so the top bar shows the
 * global time range here and on no other page.
 */
export function SearchPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col">
      <PageHeader title={t('nav.search')} description={t('search.description')} />
    </div>
  );
}
