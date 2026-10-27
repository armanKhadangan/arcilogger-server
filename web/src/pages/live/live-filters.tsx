import { ChevronDown, Search, UserRoundSearch } from 'lucide-react';
import type { FormEvent, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { currentLang } from '@/lib/i18n';
import type { FieldCatalogue } from '@/lib/search-types';
import { cn } from '@/lib/utils';

import { liveQuery, menuSources, type LiveFilter } from './live-model';

function toggled(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

/** A chip that is on or off (`aria-pressed`). */
function Chip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-13 font-medium whitespace-nowrap transition-colors [&_svg]:size-3.5',
        pressed
          ? 'border-primary bg-primary-soft text-primary-ink'
          : 'border-border bg-surface text-foreground hover:bg-subtle',
      )}
    >
      {children}
    </button>
  );
}

/**
 * Canlı Akış's filter: the sources of the field catalogue (`menuSources`: a network's name
 * follows its members too), the chip "Bilinmeyen göndericiler" (`unknown-<ip>` folders), kind
 * chips and a box for free text or any query of the search language. Together they are one query
 * (`liveQuery`), shown under the box; Enter starts or applies it.
 */
export function LiveFilters({
  filter,
  onChange,
  onSubmit,
  catalogue,
}: {
  filter: LiveFilter;
  onChange: (filter: LiveFilter) => void;
  onSubmit: () => void;
  catalogue: FieldCatalogue | undefined;
}) {
  const { t } = useTranslation();
  const english = currentLang() === 'en';
  const sources = menuSources(catalogue?.sources ?? []);
  const kinds = catalogue?.kinds ?? [];
  const query = liveQuery(filter);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={submit}
      role="search"
      aria-label={t('live.filters.label')}
    >
      <div className="flex flex-wrap items-center gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary" size="sm" data-testid="live-sources">
              {filter.sources.length === 0
                ? t('live.filters.sourcesAll')
                : t('live.filters.sourcesSome', { count: filter.sources.length })}
              <ChevronDown aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-h-80 overflow-y-auto">
            <DropdownMenuLabel>{t('live.filters.sourcesTitle')}</DropdownMenuLabel>
            <p className="max-w-72 px-2 pb-1.5 text-xs text-muted">
              {t('live.filters.sourcesHint')}
            </p>
            {sources.length === 0 ? (
              <p className="px-2 py-1.5 text-13 text-muted">{t('live.filters.noSources')}</p>
            ) : (
              sources.map((source) => (
                <DropdownMenuCheckboxItem
                  key={source.name}
                  checked={filter.sources.includes(source.name)}
                  onCheckedChange={() => {
                    onChange({ ...filter, sources: toggled(filter.sources, source.name) });
                  }}
                  onSelect={(event) => {
                    event.preventDefault();
                  }}
                >
                  <span className="font-mono text-xs">{source.name}</span>
                  {source.network ? (
                    <span className="ml-2 text-xs text-muted">{t('live.filters.network')}</span>
                  ) : null}
                </DropdownMenuCheckboxItem>
              ))
            )}
            {filter.sources.length > 0 ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => {
                    onChange({ ...filter, sources: [] });
                  }}
                >
                  {t('live.filters.sourcesClear')}
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
        <Chip
          pressed={filter.unknown}
          onClick={() => {
            onChange({ ...filter, unknown: !filter.unknown });
          }}
        >
          <UserRoundSearch aria-hidden="true" />
          {t('live.filters.unknown')}
        </Chip>
      </div>
      {kinds.length > 0 ? (
        <div
          role="group"
          aria-label={t('live.filters.kinds')}
          className="flex flex-wrap items-center gap-1.5"
        >
          {kinds.map((kind) => (
            <Chip
              key={kind.key}
              pressed={filter.kinds.includes(kind.key)}
              onClick={() => {
                onChange({ ...filter, kinds: toggled(filter.kinds, kind.key) });
              }}
            >
              {english ? kind.label_en : kind.label_tr}
            </Chip>
          ))}
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="live-text">{t('live.filters.text')}</Label>
        <Input
          id="live-text"
          type="search"
          mono
          autoComplete="off"
          spellCheck={false}
          maxLength={1000}
          leadingIcon={<Search />}
          placeholder={t('live.filters.textPlaceholder')}
          value={filter.text}
          aria-describedby="live-query"
          onChange={(event) => {
            onChange({ ...filter, text: event.target.value });
          }}
        />
        <p id="live-query" className="text-xs text-muted">
          {query ? (
            <>
              {t('live.filters.query')}{' '}
              <code className="font-mono break-all text-foreground" data-testid="live-query">
                {query}
              </code>
            </>
          ) : (
            t('live.filters.everything')
          )}
        </p>
      </div>
    </form>
  );
}
