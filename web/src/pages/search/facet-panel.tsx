import { Filter, FilterX } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { visibleText } from '@/lib/display';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { Facet, SearchFacets, SearchStats } from '@/lib/search-types';

/** The facets in the panel's order. */
const ORDER: (keyof SearchFacets)[] = ['kind', 'source', 'action', 'vlan', 'department'];

/**
 * The value of a /24 that holds several smaller VLANs ("Birden çok VLAN", "Birden çok
 * departman"): a count, not a filter — `vlan:multiple` and `department:multiple` name nothing.
 */
const MULTIPLE_VALUE = 'multiple';

function filterable(name: keyof SearchFacets, value: string): boolean {
  return !((name === 'vlan' || name === 'department') && value === MULTIPLE_VALUE);
}

function FacetBlock({
  name,
  facet,
  sampleRows,
  onAdd,
}: {
  name: keyof SearchFacets;
  facet: Facet;
  sampleRows: number | null;
  onAdd: (facet: keyof SearchFacets, value: string, negated: boolean) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const title = t(`search.facets.${name}`);
  return (
    <section className="flex flex-col gap-1.5" aria-label={title}>
      <h3 className="text-13 font-semibold text-foreground">{title}</h3>
      {facet.status === 'unavailable' ? (
        <p className="text-xs text-muted">{t('search.facets.unavailable')}</p>
      ) : facet.values.length === 0 ? (
        <p className="text-xs text-muted">{t('search.facets.none')}</p>
      ) : (
        <ul className="flex flex-col">
          {facet.values.map((item) => {
            const label = visibleText(lang === 'en' ? item.label_en : item.label_tr);
            const count = formatNumber(item.count, 0, lang);
            if (!filterable(name, item.value))
              return (
                <li
                  key={item.value}
                  className="flex items-center gap-2 px-1.5 py-1 text-13"
                  title={t('search.facets.multipleNote')}
                >
                  <span className="size-3.5 shrink-0" aria-hidden="true" />
                  <span className="truncate text-foreground">{label}</span>
                  <span className="sr-only">{t('search.facets.multipleNote')}</span>
                  <span className="ml-auto pl-2 text-muted tabular">{count}</span>
                </li>
              );
            return (
              <li key={item.value} className="group flex items-center gap-1 text-13">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-subtle"
                  title={t('search.facets.only', { value: label })}
                  onClick={() => {
                    onAdd(name, item.value, false);
                  }}
                >
                  <Filter aria-hidden="true" className="size-3.5 shrink-0 text-muted" />
                  <span className="truncate text-foreground">{label}</span>
                  <span className="ml-auto pl-2 text-muted tabular">{count}</span>
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  aria-label={t('search.facets.excludeValue', { value: label })}
                  onClick={() => {
                    onAdd(name, item.value, true);
                  }}
                >
                  <FilterX aria-hidden="true" />
                  {t('search.facets.exclude')}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      {facet.other > 0 ? (
        <p className="px-1.5 text-xs text-muted tabular">
          {t('search.facets.other', { formatted: formatNumber(facet.other, 0, lang) })}
        </p>
      ) : null}
      {facet.status === 'sampled' ? (
        <p className="px-1.5 text-xs text-muted">
          {sampleRows === null
            ? t('search.facets.sampledSome')
            : t('search.facets.sampled', { formatted: formatNumber(sampleRows, 0, lang) })}
        </p>
      ) : null}
    </section>
  );
}

/**
 * Kind, source, action, VLAN and department of the search's records (from its statistics). A
 * value adds `field:value` to the query, "Hariç tut" adds `-field:value` — except the VLAN and
 * department value `multiple`, shown with its count only; the department is the VLAN's default
 * department, labelled so (owner decision D1).
 */
export function FacetPanel({
  stats,
  loading,
  onAdd,
}: {
  stats: SearchStats | undefined;
  loading: boolean;
  onAdd: (facet: keyof SearchFacets, value: string, negated: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Card className="flex flex-col gap-4 p-4" aria-label={t('search.facets.title')} role="region">
      <h2 className="text-sm font-semibold text-foreground">{t('search.facets.title')}</h2>
      {loading || !stats ? (
        <div className="flex flex-col gap-2" role="status" aria-label={t('common.loading')}>
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : (
        ORDER.map((name) => (
          <FacetBlock
            key={name}
            name={name}
            facet={stats.facets[name]}
            sampleRows={stats.sample_rows}
            onAdd={onAdd}
          />
        ))
      )}
    </Card>
  );
}
