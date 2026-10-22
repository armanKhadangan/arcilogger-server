import { Plus, Scale } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { RawLineSheet, type RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { errorMessage } from '@/lib/api';
import { useLookup } from '@/lib/api-hooks';
import { parseId } from '@/lib/display';

import { LookupForm } from './lookup-form';
import { draftFrom, EMPTY_DRAFT } from './lookup-form-model';
import { LookupHistory } from './lookup-history';
import { LookupResults } from './lookup-results';
import { ReportPanel } from './report-panel';

/**
 * 5651 Sorgu (spec 9.5, 12.3 page 6; `lookup.run`): two columns — the request details and the
 * query on the left, the results on the right with every evidence row opening its raw line —
 * then the official report and the evidence package. `/lookup/:id` reopens a stored lookup
 * (form filled with it, results as stored); earlier lookups are listed on the second tab.
 */
export function LookupPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const params = useParams();
  const lookupId = parseId(params.lookupId);
  const invalidId = params.lookupId !== undefined && lookupId === null;
  const lookup = useLookup(lookupId);
  const [tab, setTab] = useState('query');
  const [target, setTarget] = useState<RawLineTarget | null>(null);
  const focusResults = useRef(false);

  useEffect(() => {
    if (focusResults.current && lookup.data) {
      focusResults.current = false;
      document.getElementById('lookup-result')?.focus();
    }
  }, [lookup.data]);

  const loadingStored = lookupId !== null && lookup.isPending;
  return (
    <div className="flex flex-col">
      <PageHeader
        title={t('nav.lookup')}
        description={t('lookup.description')}
        actions={
          lookupId !== null ? (
            <Button
              variant="secondary"
              onClick={() => {
                setTab('query');
                void navigate('/lookup');
              }}
            >
              <Plus aria-hidden="true" />
              {t('lookup.newLookup')}
            </Button>
          ) : undefined
        }
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label={t('lookup.tabs.label')}>
          <TabsTrigger value="query">{t('lookup.tabs.query')}</TabsTrigger>
          <TabsTrigger value="history">{t('lookup.tabs.history')}</TabsTrigger>
        </TabsList>
        <TabsContent value="query">
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(22rem,27rem)_minmax(0,1fr)]">
            <div className="min-w-0">
              {loadingStored ? (
                <Skeleton className="h-[40rem]" />
              ) : (
                <LookupForm
                  key={lookup.data?.id ?? 'new'}
                  initial={lookup.data ? draftFrom(lookup.data) : EMPTY_DRAFT}
                  onDone={(result) => {
                    focusResults.current = true;
                    void navigate(`/lookup/${result.id}`);
                  }}
                />
              )}
            </div>
            <section
              id="lookup-result"
              tabIndex={-1}
              aria-label={t('lookup.result.region')}
              className="flex min-w-0 flex-col gap-4 outline-none"
            >
              {invalidId ? (
                <Alert tone="danger">{t('lookup.invalidId')}</Alert>
              ) : lookupId === null ? (
                <EmptyState
                  icon={Scale}
                  title={t('lookup.emptyTitle')}
                  description={t('lookup.emptyBody')}
                />
              ) : lookup.isPending ? (
                <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-3">
                  <Skeleton className="h-40" />
                  <Skeleton className="h-64" />
                </div>
              ) : lookup.isError ? (
                <Alert tone="danger">{errorMessage(lookup.error)}</Alert>
              ) : (
                <>
                  <LookupResults lookup={lookup.data} onOpen={setTarget} />
                  <ReportPanel lookup={lookup.data} />
                </>
              )}
            </section>
          </div>
        </TabsContent>
        <TabsContent value="history">
          <LookupHistory
            onOpen={(id) => {
              setTab('query');
              void navigate(`/lookup/${id}`);
            }}
          />
        </TabsContent>
      </Tabs>
      <RawLineSheet
        target={target}
        onClose={() => {
          setTarget(null);
        }}
      />
    </div>
  );
}
