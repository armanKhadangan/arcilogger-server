import { FilterX, Search } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useUsageSubjects } from '@/lib/api-hooks';
import type { SubjectRef, UsageParams } from '@/lib/api-types';

import { SubjectView } from './subject-view';
import { ExportMenu } from './usage-export';
import { matchesApiSearch } from './usage-format';
import type { DrillTarget } from './usage-model';
import { DataNote, EstimateHint, NoTraffic, PrivacyWait, QueryState } from './usage-parts';
import { SubjectTable } from './usage-tables';

/**
 * Kişi / Cihaz: every person, or every device assigned to nobody and IP address without a MAC
 * (an assigned device's traffic is its person's), of the period with every metric, searched and
 * sorted in the browser — the search text stays out of the address —, and the view of one subject.
 */
export function SubjectsTab({
  kind,
  subject,
  params,
  privacyPending,
  onDrill,
  onBack,
}: {
  kind: 'person' | 'device';
  subject: string | null;
  params: UsageParams;
  privacyPending: boolean;
  onDrill: (target: DrillTarget) => void;
  onBack: () => void;
}) {
  const { t } = useTranslation();
  const searchId = useId();
  const [search, setSearch] = useState('');
  const list = useUsageSubjects(kind, params, !privacyPending && subject === null);
  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  const shown = useMemo(
    () =>
      // What the API's `q` matches (name, MAC, IP address, host name; a pseudonym only).
      rows.filter((row) =>
        matchesApiSearch(search, [
          row.subject.label,
          row.subject.mac,
          row.subject.ip,
          row.subject.hostname,
        ]),
      ),
    [rows, search],
  );
  const words = kind === 'person' ? 'persons' : 'devices';

  if (privacyPending) return <PrivacyWait />;
  if (subject !== null)
    return (
      <SubjectView
        subjectKey={subject}
        params={params}
        backLabel={t(`usage.back.${words}`)}
        onDrill={onDrill}
        onBack={onBack}
      />
    );

  const toolbar = (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex w-64 max-w-full flex-col gap-1">
        <Label htmlFor={searchId} className="text-xs text-muted-strong">
          {t('usage.subjects.search')}
        </Label>
        <Input
          id={searchId}
          type="search"
          autoComplete="off"
          spellCheck={false}
          leadingIcon={<Search />}
          placeholder={t(`usage.subjects.${words}Placeholder`)}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
      </div>
      {search ? (
        <Button
          variant="ghost"
          onClick={() => {
            setSearch('');
          }}
        >
          <FilterX aria-hidden="true" />
          {t('usage.subjects.clear')}
        </Button>
      ) : null}
      <span className="flex items-center gap-3 pb-2 text-13 text-muted">
        {kind === 'device' ? t('usage.subjects.devicesHint') : null}
        <EstimateHint noticeKey={list.data?.notice_key ?? null} />
      </span>
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <DataNote computedUntil={list.data?.computed_until} noticeKey={list.data?.notice_key} />
      <QueryState query={list} skeleton="table" empty={list.isSuccess && rows.length === 0}>
        {() => (
          <SubjectTable
            rows={shown}
            caption={t(`usage.subjects.${words}Caption`)}
            subjectLabel={t(kind === 'person' ? 'usage.columns.person' : 'usage.columns.device')}
            onOpen={(item: SubjectRef) => {
              onDrill({ subject: item.key });
            }}
            toolbar={toolbar}
            actions={
              <ExportMenu
                view="subjects"
                params={params}
                extra={{ kind, q: search.trim() || undefined }}
                label={t(`usage.subjects.${words}Caption`)}
              />
            }
            emptyMessage={search ? t('usage.empty.filtered') : <NoTraffic />}
            maxHeight={640}
          />
        )}
      </QueryState>
    </div>
  );
}
