import { FileDown } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

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
import { checkDownload, reprDigestSha256, saveBlob, type HashCheck } from '@/lib/download';
import { RECENT_AUTH_MINUTES } from '@/lib/permissions';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { downloadSearchExport } from '@/lib/search-hooks';
import type { ExportFormat } from '@/lib/search-types';
import { cn } from '@/lib/utils';

import { truncationOf } from './search-model';

/** What each file format is like (its hint under the choice). */
const FORMAT_HINTS: Record<ExportFormat, string> = {
  xlsx: 'search.export.formatHints.xlsx',
  csv: 'search.export.formatHints.csvSemicolons',
};

/**
 * "Dışa aktar" (`search.export`, administrators): the whole search as CSV or Excel. The API asks
 * for the password again within {@link RECENT_AUTH_MINUTES} minutes (`withReauth`); the file is
 * checked against its `Repr-Digest` in the browser, and a file cut at the row limit or the time
 * budget is said so (a toast; its name ends in `-kismi`). Not sealed: integrity is the evidence
 * package's job.
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
  const [done, setDone] = useState<{ filename: string; check: HashCheck } | null>(null);

  async function start() {
    setBusy(true);
    setProblem(null);
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
      if (truncated)
        toast({
          title: t('search.export.truncatedTitle'),
          description: t(`search.export.truncated.${truncated}`),
          variant: 'warning',
        });
    } catch (error) {
      if (!isReauthCancelled(error)) setProblem(errorMessage(error));
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
