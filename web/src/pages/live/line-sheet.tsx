import { FileText, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { Badge } from '@/components/ui/badge';
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
import { formatDay, visibleText } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import type { HandoffPayloads } from '@/lib/handoff';
import { currentLang } from '@/lib/i18n';
import type { LiveLine } from '@/lib/live-types';

import { searchAround } from './live-model';

type FieldValue = string | number | null | undefined;

/** The preview fields of a line that it has (empty ones are left out). */
function fieldItems(line: LiveLine, kind: string): [string, string][] {
  const summary = Object.entries(line.summary) as [string, FieldValue][];
  const items: [string, FieldValue][] = [
    ['kind', kind],
    ['type', line.type],
    ['subtype', line.subtype],
    ['parse_error', line.parse_error],
    ...summary,
  ];
  return items
    .filter(
      (item): item is [string, string | number] =>
        item[1] !== null && item[1] !== undefined && item[1] !== '',
    )
    .map(([name, value]) => [name, String(value)]);
}

/**
 * One line of Canlı Akış: its fields as the hub parsed them — a preview ("önizleme"): ingest's row
 * may differ in details, and a line over the parse budget or not in the collector's format has
 * none —, its text as the stream sent it (display-safe, at most 8 KiB), "Ham satır" (the line
 * byte-exact over this drawer, `GET /raw`, audited `raw.view`) and "Arama'da aç" (this source,
 * five minutes either side, handed over in memory).
 */
export function LineSheet({
  line,
  kindLabel,
  onClose,
  onRaw,
  onSearch,
}: {
  line: LiveLine | null;
  kindLabel: (line: LiveLine) => string;
  onClose: () => void;
  onRaw: (target: RawLineTarget) => void;
  onSearch: (payload: HandoffPayloads['/search']) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const search = line ? searchAround(line) : null;
  const invalid = line?.parse_error === 'rawlog.invalid';
  return (
    <Sheet
      open={line !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl" data-testid="line-sheet">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {t('live.sheet.title')}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {line
              ? t('live.sheet.subtitle', {
                  source: line.source,
                  day: formatDay(line.day),
                  line: formatNumber(line.line, 0, lang),
                })
              : ''}
          </SheetDescription>
        </SheetHeader>
        {line ? (
          <SheetBody className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
              {invalid ? (
                <Badge tone="warning">{t('live.line.invalid')}</Badge>
              ) : line.parse_skipped ? (
                <Badge tone="warning">{t('live.line.skipped')}</Badge>
              ) : (
                <Badge tone="info">{t('live.sheet.preview')}</Badge>
              )}
              {line.truncated ? <Badge tone="neutral">{t('live.sheet.truncated')}</Badge> : null}
            </div>
            <p className="text-13 text-muted">
              {invalid
                ? t('live.sheet.invalidHint')
                : line.parse_skipped
                  ? t('live.sheet.skippedHint')
                  : t('live.sheet.previewHint')}
            </p>
            <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
              <dt className="text-muted">{t('live.sheet.received')}</dt>
              <dd className="font-mono text-xs leading-5 text-foreground">
                {line.received ? formatDateTime(line.received) : '—'}
              </dd>
              <dt className="text-muted">{t('live.sheet.sender')}</dt>
              <dd className="font-mono text-xs leading-5 text-foreground">{line.sender ?? '—'}</dd>
              {fieldItems(line, kindLabel(line)).map(([name, value]) => (
                <div key={name} className="contents">
                  <dt className="font-mono text-xs leading-5 text-muted">{name}</dt>
                  <dd className="font-mono text-xs leading-5 break-all text-foreground">
                    {visibleText(value)}
                  </dd>
                </div>
              ))}
            </dl>
            <CodeBlock label={t('live.sheet.text')} code={visibleText(line.text)} wrap />
          </SheetBody>
        ) : null}
        {line ? (
          <SheetFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onRaw({
                  ref: { source: line.source, day: line.day, line: line.line, offset: line.offset },
                });
              }}
            >
              <FileText aria-hidden="true" />
              {t('live.sheet.openRaw')}
            </Button>
            {search ? (
              <Button
                variant="secondary"
                onClick={() => {
                  onSearch(search);
                }}
              >
                <Search aria-hidden="true" />
                {t('live.sheet.search')}
              </Button>
            ) : null}
          </SheetFooter>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
