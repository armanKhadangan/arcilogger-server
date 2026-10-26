import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { formatDateTime } from '@/lib/format';
import { SEARCH_AUTO_CONTINUES } from '@/lib/search-hooks';
import type { SearchPage } from '@/lib/search-types';

import type { ScanState } from './search-model';

/**
 * How far an incomplete scan got (its time budget ended before the range did, contract 2.6.1):
 * while it continues by itself ("taranıyor") and, after {@link SEARCH_AUTO_CONTINUES} rounds, the
 * banner with "Devam et". The rows above it are the newest; older ones may follow.
 */
export function PartialBanner({
  first,
  last,
  state,
  busy,
  onContinue,
}: {
  first: SearchPage;
  last: SearchPage;
  state: ScanState;
  busy: boolean;
  onContinue: () => void;
}) {
  const { t } = useTranslation();
  if (state !== 'continuing' && state !== 'paused') return null;
  const values = {
    from: formatDateTime(first.range.from),
    reached: formatDateTime(last.scan.scanned_from),
  };
  if (state === 'continuing')
    return (
      <p role="status" className="flex items-center gap-2 text-13 text-muted">
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        {t('search.partial.continuing', values)}
      </p>
    );
  return (
    <Alert tone="warning" title={t('search.partial.title')}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p>{t('search.partial.body', { ...values, times: SEARCH_AUTO_CONTINUES })}</p>
        <Button size="sm" variant="secondary" busy={busy} onClick={onContinue}>
          {t('search.partial.continue')}
        </Button>
      </div>
    </Alert>
  );
}
