import { History } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { TextField } from '@/components/admin/form-fields';
import { RequestProgress } from '@/components/admin/request-progress';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { errorMessage } from '@/lib/api';
import { useLeaseRebuild } from '@/lib/api-hooks';
import { dayText, parseDay } from '@/lib/dates';
import type { AdminRequestOut, LeaseRebuildResult } from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { leaseRangeErrors, siteToday } from './lease-range';

function Outcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = request.result as LeaseRebuildResult | null;
  if (!result) return null;
  return (
    <div className="flex flex-col gap-1 text-13" data-testid="lease-rebuild-result">
      <p>
        {t('settings.leaseRebuild.progress', { done: result.done_days, total: result.total_days })}
      </p>
      {result.failed_days.length > 0 ? (
        <p className="text-danger-ink">
          {t('settings.leaseRebuild.failedDays', {
            days: result.failed_days.map(dayText).join(', '),
          })}
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Kiraları yeniden oluştur" (contract 2.9, in the 5651 lookup group of Uygulama ayarları): the
 * worker rebuilds the lease timeline of at most 31 past days, day by day, with its progress.
 * Useful after a change of the lease settings; the raw logs are only read.
 */
export function LeaseRebuild({ canEdit }: { canEdit: boolean }) {
  const { t } = useTranslation();
  const id = useId();
  const rebuild = useLeaseRebuild();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'from' | 'to'>>({});
  const [asking, setAsking] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  function review() {
    const found = leaseRangeErrors(from, to, siteToday());
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    setAsking(true);
  }

  async function run() {
    const first = parseDay(from);
    const last = parseDay(to);
    if (!first || !last) return;
    setProblem(null);
    try {
      const accepted = await rebuild.mutateAsync({ from: first, to: last });
      setRequestId(accepted.request_id);
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setAsking(false);
    }
  }

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-3 border-t border-border pt-4"
      data-testid="lease-rebuild"
    >
      <h3 id={`${id}-title`} className="text-sm font-semibold text-foreground">
        {t('settings.leaseRebuild.title')}
      </h3>
      <p className="text-13 text-muted">{t('settings.leaseRebuild.description')}</p>
      <div className="flex flex-wrap items-start gap-4">
        <TextField
          id={`${id}-from`}
          label={t('settings.leaseRebuild.from')}
          hint={t('settings.leaseRebuild.dayHint')}
          value={from}
          error={errors.from ? t(errors.from) : undefined}
          disabled={!canEdit}
          inputClassName="w-36"
          autoComplete="off"
          onChange={(value) => {
            setFrom(value);
            setErrors({});
          }}
        />
        <TextField
          id={`${id}-to`}
          label={t('settings.leaseRebuild.to')}
          hint={t('settings.leaseRebuild.dayHint')}
          value={to}
          error={errors.to ? t(errors.to) : undefined}
          disabled={!canEdit}
          inputClassName="w-36"
          autoComplete="off"
          onChange={(value) => {
            setTo(value);
            setErrors({});
          }}
        />
      </div>
      <div>
        <Button variant="secondary" disabled={!canEdit} busy={rebuild.isPending} onClick={review}>
          <History aria-hidden="true" />
          {t('settings.leaseRebuild.button')}
        </Button>
      </div>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          renderResult={(request) => <Outcome request={request} />}
        />
      ) : null}
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title={t('settings.leaseRebuild.confirmTitle')}
        description={t('settings.leaseRebuild.confirmBody', {
          from: dayText(parseDay(from) ?? from),
          to: dayText(parseDay(to) ?? to),
        })}
        confirmLabel={t('settings.leaseRebuild.button')}
        busy={rebuild.isPending}
        onConfirm={() => void run()}
      />
    </section>
  );
}
