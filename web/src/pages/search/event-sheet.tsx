import { FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { formatDay, visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { useSearchEvent } from '@/lib/search-hooks';
import type { FieldCatalogue, SearchEventMessage, SearchRow } from '@/lib/search-types';

import { filledFields } from './search-model';

/**
 * Columns with a name people read: the drawer's own labels (`search.event.columns.<column>`), else
 * the query field of that name (the catalogue's label), else none (the column name alone).
 */
function useColumnLabel(catalogue: FieldCatalogue | undefined): (column: string) => string | null {
  const { t, i18n } = useTranslation();
  const lang = currentLang();
  return (column) => {
    const key = `search.event.columns.${column}`;
    if (i18n.exists(key)) return t(key);
    const field = catalogue?.fields.find((entry) => entry.name === column);
    if (!field) return null;
    return lang === 'en' ? field.label_en : field.label_tr;
  };
}

function List({
  items,
  label,
}: {
  items: [string, string][];
  label: (column: string) => string | null;
}) {
  return (
    <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
      {items.map(([name, value]) => {
        const shown = label(name);
        return (
          <div key={name} className="contents">
            <dt className="flex flex-wrap items-baseline gap-x-1.5 text-xs leading-5 text-muted-strong">
              {shown ?? <span className="font-mono text-muted">{name}</span>}
              {shown ? <span className="font-mono text-[11px] text-muted">{name}</span> : null}
            </dt>
            <dd className="font-mono text-xs leading-5 break-all text-foreground">{value}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function messageItems(message: SearchEventMessage): [string, string][] {
  return filledFields({
    type: message.type,
    subtype: message.subtype,
    severity: message.severity,
    facility: message.facility,
    program: message.program,
    host: message.host,
    parse_error: message.parse_error,
  });
}

/**
 * One record (`GET /search/event`, audited `search.event`): "Alanlar", every column of its
 * typed row (or of `messages`), the `messages` row it came from, and "Ham satır", which opens the
 * raw line itself over this drawer (`RawLineSheet`: byte-exact, SHA-256, file, offset). Columns
 * are named in the interface language with the column name beside them; times are in site time,
 * so the device's time and the receive time can be compared.
 */
export function EventSheet({
  row,
  catalogue,
  onClose,
  onRaw,
}: {
  row: SearchRow | null;
  /** `GET /search/fields`: the labels of the query fields and of the kinds. */
  catalogue: FieldCatalogue | undefined;
  onClose: () => void;
  onRaw: (row: SearchRow) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const event = useSearchEvent(row);
  const data = event.data;
  const label = useColumnLabel(catalogue);
  const kind = catalogue?.kinds.find((entry) => entry.key === row?.kind);
  const kindLabel = kind ? (lang === 'en' ? kind.label_en : kind.label_tr) : (row?.kind ?? '');

  return (
    <Sheet
      open={row !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {t('search.event.title')}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {row
              ? t('search.event.subtitleKind', {
                  source: row.source,
                  time: formatDateTime(row.time),
                  kind: kindLabel,
                })
              : ''}
          </SheetDescription>
        </SheetHeader>
        {row ? (
          <SheetBody className="flex flex-col gap-5">
            {event.isPending ? (
              <div className="flex flex-col gap-2" role="status" aria-label={t('common.loading')}>
                <Skeleton className="h-40" />
                <Skeleton className="h-24" />
              </div>
            ) : event.isError ? (
              <Alert tone="danger">{errorMessage(event.error)}</Alert>
            ) : data ? (
              <>
                <section className="flex flex-col gap-2" aria-labelledby="search-event-fields">
                  <h3 id="search-event-fields" className="text-sm font-semibold text-foreground">
                    {t('search.event.fields')}
                  </h3>
                  <List items={filledFields(data.fields)} label={label} />
                  <p className="text-xs text-muted">{t('search.event.emptyLeftOut')}</p>
                </section>
                {Object.keys(data.extra).length > 0 ? (
                  <CodeBlock
                    label={t('search.event.extra')}
                    code={visibleText(JSON.stringify(data.extra, null, 2))}
                    wrap
                  />
                ) : null}
                {data.message ? (
                  <section className="flex flex-col gap-2" aria-labelledby="search-event-message">
                    <h3 id="search-event-message" className="text-sm font-semibold text-foreground">
                      {t('search.event.message')}
                    </h3>
                    <List items={messageItems(data.message)} label={label} />
                    <CodeBlock
                      label={t('search.event.text')}
                      code={visibleText(data.message.text)}
                      wrap
                    />
                  </section>
                ) : null}
                <p className="text-xs text-muted">
                  {t('search.event.rawRef', {
                    source: data.raw.source,
                    day: formatDay(data.raw.day),
                    line: data.raw.line,
                    offset: data.raw.offset,
                  })}
                </p>
                <p className="text-xs text-muted">{t('search.event.audited')}</p>
              </>
            ) : null}
          </SheetBody>
        ) : null}
        {row ? (
          <SheetFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onRaw(row);
              }}
            >
              <FileText aria-hidden="true" />
              {t('search.event.openRaw')}
            </Button>
          </SheetFooter>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
