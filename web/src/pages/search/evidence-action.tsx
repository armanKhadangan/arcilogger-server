import { PackagePlus } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { handoff } from '@/lib/handoff';
import { useSearchRefs } from '@/lib/search-hooks';
import type { SearchPage } from '@/lib/search-types';

import { evidenceHandoff, MAX_REFS } from './search-model';

/**
 * "Kanıt paketine aktar" (`evidence.create`, contract 2.13): the raw lines of the whole search,
 * up to the evidence extract's 10,000 (`POST /search/refs`, audited `search.refs`), go to the
 * evidence wizard in memory (`lib/handoff.ts`), which opens with them as an extract of their days.
 * The package itself is made there (its own permission, re-authentication and audit).
 */
export function EvidenceAction({
  page,
  withSearch,
}: {
  page: SearchPage;
  withSearch: <T>(action: (searchId: string) => Promise<T>) => Promise<T>;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const refs = useSearchRefs();

  async function start() {
    try {
      const found = await withSearch((searchId) => refs.mutateAsync({ searchId, limit: MAX_REFS }));
      if (found.refs.length === 0) {
        toast({ title: t('search.evidence.none'), variant: 'warning' });
        return;
      }
      handoff.put('/evidence', evidenceHandoff(page, found));
      void navigate('/evidence');
    } catch (error) {
      toast({
        title: t('search.evidence.failed'),
        description: errorMessage(error),
        variant: 'danger',
      });
    }
  }

  return (
    <Button
      variant="secondary"
      busy={refs.isPending}
      title={t('search.evidence.hint')}
      onClick={() => void start()}
    >
      <PackagePlus aria-hidden="true" />
      {refs.isPending ? t('common.working') : t('search.evidence.action')}
    </Button>
  );
}
