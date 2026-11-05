import { FileDown } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { keepDaysOf, requiredOf } from '@/components/archive/archive-model';
import { ArchiveReloadCard } from '@/components/archive/archive-reload-card';
import { HashCheckBadge } from '@/components/evidence/hash-check-badge';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import type { ArchiveReloadRequired } from '@/lib/archive-reload-types';
import { formatDay } from '@/lib/display';
import { checkDownload, reprDigestSha256, saveBlob, type HashCheck } from '@/lib/download';
import { RECENT_AUTH_MINUTES } from '@/lib/permissions';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { downloadSearchExport } from '@/lib/search-hooks';
import type { ExportFormat } from '@/lib/search-types';
import { cn } from '@/lib/utils';

import { archivedDaysOf, truncationOf } from './search-model';

/** What each file format is like (its hint under the choice). */
const FORMAT_HINTS: Record<ExportFormat, string> = {
  xlsx: 'search.export.formatHints.xlsx',
  csv: 'search.export.formatHints.csvSemicolons',
};

/**
 * "Dışa aktar" (`search.export`, administrators): the whole search as CSV or Excel. The API asks
 * for the password again within {@link RECENT_AUTH_MINUTES} minutes (`withReauth`); the file is
 * checked against its `Repr-Digest` in the browser, and a partial file is said so (a toast; its
 * name ends in `-kismi`): cut at the row limit or the time budget, or without the days of the range
 * that are not in the database (G8, W5-G-24), which the toast names. When every day of the range
 * is gone, the API answers 409 `archive_reload_required` as the search does, and the dialog shows
 * the search's reload card in place of a refusal; once the reload is ready the export runs again.
 * Not sealed: integrity is the evidence package's job.
 */
export function ExportDialog({
  open,
  onOpenChange,
  withSearch,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Runs the export on the search's id (the search runs again once if its id expired). */
  withSearch: <T>(action: (searchId: string) => Promise<T>) => Promise<T>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const [format, setFormat] = useState<ExportFormat>('xlsx');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  /** The days of a 409 `archive_reload_required` and the API's sentence of it. */
  const [required, setRequired] = useState<{
    days: ArchiveReloadRequired;
    message: string;
  } | null>(null);
  const [done, setDone] = useState<{ filename: string; check: HashCheck } | null>(null);

  async function start() {
    setBusy(true);
    setProblem(null);
    setRequired(null);
    setDone(null);
    try {
      const file = await withReauth(() =>
        withSearch((searchId) => downloadSearchExport(searchId, format)),
      );
      saveBlob(file.blob, file.filename);
      const check = await checkDownload(
        file.blob,
        reprDigestSha256(file.headers.get('Repr-Digest')),
      );
      setDone({ filename: file.filename, check });
      const truncated = truncationOf(file);
      const archived = archivedDaysOf(file);
      const missing = !archived
        ? null
        : archived.first_day === archived.last_day
          ? t('search.export.archivedDays.one', { day: formatDay(archived.first_day) })
          : t('search.export.archivedDays.span', {
              first: formatDay(archived.first_day),
              last: formatDay(archived.last_day),
            });
      if (truncated)
        toast({
          title: t('search.export.truncatedTitle'),
          description: [t(`search.export.truncated.${truncated}`), missing]
            .filter(Boolean)
            .join(' '),
          variant: 'warning',
        });
    } catch (error) {
      const days = requiredOf(error);
      if (days) setRequired({ days, message: errorMessage(error) });
      else if (!isReauthCancelled(error)) setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        if (!next) {
          setProblem(null);
          setRequired(null);
          setDone(null);
        }
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('search.export.title')}</DialogTitle>
          <DialogDescription>{t('search.export.description')}</DialogDescription>
        </DialogHeader>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-sm font-semibold text-foreground">
            {t('search.export.format')}
          </legend>
          {(['xlsx', 'csv'] as const).map((value) => (
            <label
              key={value}
              className={cn(
                'flex cursor-pointer gap-3 rounded-lg border border-border p-3 text-13',
                format === value && 'border-primary ring-1 ring-primary',
              )}
            >
              <input
                type="radio"
                name={`${id}-format`}
                value={value}
                checked={format === value}
                onChange={() => {
                  setFormat(value);
                }}
                className="mt-0.5 size-4 accent-[var(--primary)]"
              />
              <span className="flex flex-col gap-0.5">
                <span className="font-medium text-foreground">
                  {t(`search.export.formats.${value}`)}
                </span>
                <span className="text-muted">{t(FORMAT_HINTS[value])}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-muted-strong">
          <li>{t('search.export.limit')}</li>
          <li>{t('search.export.privacy')}</li>
          <li>{t('search.export.notSealed')}</li>
          <li>{t('search.export.reauth', { minutes: RECENT_AUTH_MINUTES })}</li>
        </ul>
        {problem ? <Alert tone="danger">{problem}</Alert> : null}
        {required ? (
          <ArchiveReloadCard
            key={`${required.days.first_day}/${required.days.last_day}`}
            reason="search"
            range={required.days}
            message={required.message}
            keepDays={keepDaysOf(required.days)}
            onReady={() => void start()}
          />
        ) : null}
        {done ? (
          <div className="flex flex-wrap items-center gap-2 text-13" role="status">
            <span className="font-mono text-xs break-all text-foreground">{done.filename}</span>
            <HashCheckBadge check={done.check} />
          </div>
        ) : null}
        <DialogFooter>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {done ? t('common.close') : t('common.cancel')}
          </Button>
          <Button busy={busy} onClick={() => void start()}>
            <FileDown aria-hidden="true" />
            {busy ? t('search.export.preparing') : t('search.export.start')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
