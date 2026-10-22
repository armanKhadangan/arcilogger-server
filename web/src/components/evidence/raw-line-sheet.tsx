import { Clock, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useRawLine } from '@/lib/api-hooks';
import { formatDay, visibleText } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { EvidenceRef, RawRef } from '@/lib/lookup-types';

import { evidenceTableLabel } from './evidence-labels';

export interface RawLineTarget {
  ref: RawRef;
  /** The database row that cited the line (lookup evidence), when known. */
  evidence?: EvidenceRef;
}

/**
 * Right-side drawer with one raw log line exactly as stored (`GET /raw`): the display text with
 * escapes visible, the file, line number, byte offset and the line's SHA-256 — so a reader can
 * compare it with the sealed file. Every view is audited by the server (`raw.view`).
 */
export function RawLineSheet({
  target,
  onClose,
}: {
  target: RawLineTarget | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const raw = useRawLine(target?.ref ?? null);
  const data = raw.data;
  const ref = target?.ref;

  const fields: [string, string][] = data
    ? [
        [t('raw.file'), data.file],
        [t('raw.line'), formatNumber(data.line, 0, lang)],
        [t('raw.offset'), formatNumber(data.offset, 0, lang)],
        [
          t('raw.length'),
          t('raw.bytes', { count: data.length, formatted: formatNumber(data.length, 0, lang) }),
        ],
        [t('raw.received'), data.received ? formatDateTime(data.received) : '—'],
        [t('raw.sender'), data.sender ?? '—'],
      ]
    : [];

  return (
    <Sheet
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {t('raw.title')}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {ref
              ? t('raw.subtitle', { source: ref.source, day: formatDay(ref.day), line: ref.line })
              : ''}
          </SheetDescription>
        </SheetHeader>
        {target ? (
          <SheetBody className="flex flex-col gap-5">
            {target.evidence ? (
              <section className="flex flex-col gap-1">
                <h3 className="text-sm font-semibold text-foreground">
                  {evidenceTableLabel(target.evidence.table, t)}
                </h3>
                <p className="font-mono text-xs break-all text-muted-strong">
                  {visibleText(target.evidence.summary)}
                </p>
                <p className="text-xs text-muted">
                  {t('raw.recordTime', { time: formatDateTime(target.evidence.time) })}
                </p>
              </section>
            ) : null}
            {raw.isPending ? (
              <div className="flex flex-col gap-2" role="status" aria-label={t('common.loading')}>
                <Skeleton className="h-24" />
                <Skeleton className="h-40" />
              </div>
            ) : raw.isError ? (
              <Alert tone="danger">{errorMessage(raw.error)}</Alert>
            ) : data ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  {data.origin === 'vault' ? (
                    <Badge tone="success" icon={Lock}>
                      {t('raw.origin.vault')}
                    </Badge>
                  ) : (
                    <Badge tone="warning" icon={Clock}>
                      {t('raw.origin.spool')}
                    </Badge>
                  )}
                </div>
                <CodeBlock label={t('raw.text')} code={visibleText(data.text)} wrap />
                <p className="text-xs text-muted">{t('raw.escapes')}</p>
                <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
                  {fields.map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt className="text-muted">{name}</dt>
                      <dd className="font-mono text-xs leading-5 break-all text-foreground">
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
                <CodeBlock label={t('raw.sha256')} code={data.sha256} wrap />
                <details className="rounded-lg border border-border px-3 py-2 text-13">
                  <summary className="cursor-pointer font-medium text-foreground">
                    {t('raw.base64')}
                  </summary>
                  <CodeBlock className="mt-2" label="base64" code={data.raw_base64} wrap />
                </details>
                <p className="text-xs text-muted">{t('raw.audited')}</p>
              </>
            ) : null}
          </SheetBody>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
