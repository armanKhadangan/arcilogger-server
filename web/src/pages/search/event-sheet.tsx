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
import { useSearchEvent } from '@/lib/search-hooks';
import type { SearchEventMessage, SearchRow } from '@/lib/search-types';

import { filledFields } from './search-model';

function List({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
      {items.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="font-mono text-xs leading-5 text-muted">{name}</dt>
          <dd className="font-mono text-xs leading-5 break-all text-foreground">{value}</dd>
        </div>
      ))}
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
 * raw line itself over this drawer (`RawLineSheet`: byte-exact, SHA-256, file, offset).
 */
export function EventSheet({
  row,
  onClose,
  onRaw,
}: {
  row: SearchRow | null;
  onClose: () => void;
  onRaw: (row: SearchRow) => void;
}) {
  const { t } = useTranslation();
  const event = useSearchEvent(row);
  const data = event.data;

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
              ? t('search.event.subtitle', {
                  source: row.source,
                  time: formatDateTime(row.time),
                  table: row.table,
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
                  <List items={filledFields(data.fields)} />
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
                    <List items={messageItems(data.message)} />
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
