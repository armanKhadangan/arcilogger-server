import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';

/**
 * Canlı Akış (spec 12.3 page 4; `search.read`): the lines of the sources as they arrive, read from
 * the raw spool, so the page works while ingest lags or is stopped.
 */
export function LivePage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col">
      <PageHeader title={t('nav.live')} description={t('live.description')} />
    </div>
  );
}
