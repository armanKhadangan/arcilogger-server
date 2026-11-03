import { ArchiveRestore } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { errorMessage, isApiError } from '@/lib/api';
import type { DateString } from '@/lib/api-types';
import type { ArchiveReload, ArchiveReloadReason } from '@/lib/archive-reload-types';
import { formatDay } from '@/lib/display';
import { formatBytes, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { failureKey, progressOf, reloadSpan } from './archive-model';
import { useArchiveReload, useRequestArchiveReload } from './use-archive-reload';

export interface ArchiveReloadCardProps {
  reason: ArchiveReloadReason;
  /** The days outside the window. */
  range: { first_day: DateString; last_day: DateString };
  /** The API's sentence of 409 archive_reload_required (localized), when the card comes from a 409. */
  message?: string | null;
  /** Called once when the reload is ready: the page runs the lookup or search again. */
  onReady: () => void;
}

/** The bar and the status line of a reload that is queued, loading or ready. */
function Progress({
  reload,
  reason,
}: {
  reload: ArchiveReload | undefined;
  reason: ArchiveReloadReason;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const value = reload ? Math.round(progressOf(reload) * 100) : 0;
  return (
    <div className="flex flex-col gap-2">
      <div
        role="progressbar"
        aria-label={t('archive.progress.label')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        className="h-2 w-full overflow-hidden rounded-full bg-surface"
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${value}%` }} />
      </div>
      <div role="status" className="flex flex-col gap-0.5" data-testid="archive-reload-status">
        <p>
          {!reload || reload.status === 'queued'
            ? t('archive.status.queued')
            : reload.status === 'loading'
              ? t('archive.status.loading')
              : t('archive.status.ready', {
                  expires: reload.expires_at ? formatDateTime(reload.expires_at) : '—',
                })}
        </p>
        {reload ? (
          <p className="text-xs">
            {t('archive.progress.of', {
              done: formatBytes(
                reload.status === 'ready' ? reload.bytes_total : reload.bytes_done,
                lang,
              ),
              total: formatBytes(reload.bytes_total, lang),
              percent: formatNumber(value, 0, lang),
            })}
          </p>
        ) : null}
        {reload?.status === 'ready' ? <p>{t(`archive.status.rerun.${reason}`)}</p> : null}
      </div>
    </div>
  );
}

/**
 * "Günler mühürlü arşivde" on the lookup and search pages (Wave 5 contract §5, G10): the days a
 * lookup or search needs are outside the database window. "Arşivden yükle" asks for them after a
 * confirmation that says how long they stay and that the request is audited; the card follows the
 * reload every 2 s and, once it is ready, lets the page run the lookup or search again — once per
 * reload. A range longer than one reload may ask for is not split: the days nearest the window are
 * offered, and the card says that the older ones need a narrower range. Refusals (G5) are the API's
 * texts, shown in the dialog; a failure has the web's text per `error_code`.
 */
export function ArchiveReloadCard({
  reason,
  range,
  message = null,
  onReady,
}: ArchiveReloadCardProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const span = reloadSpan(range.first_day, range.last_day);
  const [open, setOpen] = useState(false);
  const [reloadId, setReloadId] = useState<number | null>(null);
  const start = useRequestArchiveReload();
  const poll = useArchiveReload(reloadId);
  // The 202 answer until the first poll has answered (it may already be ready: an earlier request).
  const reload: ArchiveReload | undefined =
    poll.data ?? (start.data?.id === reloadId ? start.data : undefined);
  const status = reload?.status;
  const offered = reloadId === null || status === 'failed' || status === 'expired';
  /** After "Yükle" the button is gone: the focus goes to the progress. */
  const progress = useRef<HTMLDivElement>(null);
  const reported = useRef<number | null>(null);

  useEffect(() => {
    if (!reload || reload.status !== 'ready' || reported.current === reload.id) return;
    reported.current = reload.id;
    onReady();
  }, [reload, onReady]);

  const days = (first: DateString, last: DateString) => ({
    first: formatDay(first),
    last: formatDay(last),
  });

  function confirm() {
    start.mutate(
      { reason, first_day: span.first_day, last_day: span.last_day },
      {
        onSuccess: (answer) => {
          setReloadId(answer.id);
          setOpen(false);
        },
        onError: (error) => {
          // The KVKK notice opens by itself (`ApiEventsBridge`); it is not a refusal of the days.
          if (isApiError(error, 'privacy_notice_required')) setOpen(false);
        },
      },
    );
  }

  return (
    <section
      aria-labelledby={titleId}
      data-testid="archive-reload-card"
      className="flex flex-col gap-3 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2.5 text-13 text-warning-ink"
    >
      <div className="flex gap-2.5">
        <ArchiveRestore aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <div className="flex min-w-0 flex-col gap-1 break-words">
          <h3 id={titleId} className="font-medium">
            {t('archive.card.title')}
          </h3>
          <p>
            {message ? message : t('archive.card.search', days(range.first_day, range.last_day))}
          </p>
          {span.clipped ? (
            <p data-testid="archive-reload-clipped">
              {t('archive.card.clipped', {
                max: span.days,
                ...days(span.first_day, span.last_day),
              })}
            </p>
          ) : null}
        </div>
      </div>

      {reloadId !== null && status !== 'failed' && status !== 'expired' ? (
        <div ref={progress} tabIndex={-1} className="outline-none">
          <Progress reload={reload} reason={reason} />
        </div>
      ) : null}
      {status === 'failed' ? (
        <Alert tone="danger">
          {t(failureKey(reload?.error_code ?? null), { code: reload?.error_code ?? '—' })}
        </Alert>
      ) : null}
      {status === 'expired' ? <p role="status">{t('archive.status.expired')}</p> : null}
      {poll.isError ? <Alert tone="danger">{errorMessage(poll.error)}</Alert> : null}

      {offered ? (
        <div>
          <Button
            size="sm"
            onClick={() => {
              start.reset();
              setOpen(true);
            }}
          >
            <ArchiveRestore aria-hidden="true" />
            {reloadId === null ? t('archive.card.reload') : t('archive.card.retry')}
          </Button>
        </div>
      ) : null}

      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('archive.dialog.title')}
        description={t('archive.dialog.body', days(span.first_day, span.last_day))}
        confirmLabel={t('archive.dialog.confirm')}
        onConfirm={confirm}
        busy={start.isPending}
        error={start.isError ? errorMessage(start.error) : null}
        returnFocus={progress}
      />
    </section>
  );
}
