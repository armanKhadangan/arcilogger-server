import { useQueryClient } from '@tanstack/react-query';
import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Loader2,
  RefreshCw,
  TriangleAlert,
  Undo2,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert, type AlertTone } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import {
  cancellable,
  isTerminal,
  localized,
  requestSectionState,
  revertible,
  servicesNotReloaded,
  STEP_SERVICES,
  systemStepFailed,
  type SectionState,
} from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { queryKeys, useAdminRequest, useCancelRequest, useRevertRequest } from '@/lib/api-hooks';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import type { AdminRequestOut, RequestStepOut, StepState } from '@/lib/settings-types';
import { cn } from '@/lib/utils';

const STEP_ICON: Record<StepState, { icon: LucideIcon; className: string }> = {
  pending: { icon: CircleDashed, className: 'text-muted' },
  running: { icon: Loader2, className: 'animate-spin text-info' },
  done: { icon: CircleCheck, className: 'text-success' },
  skipped: { icon: CircleMinus, className: 'text-muted' },
  warning: { icon: TriangleAlert, className: 'text-warning' },
  failed: { icon: CircleX, className: 'text-danger' },
};

/** The banner of each state the section shows for a request (2.17). */
const TONE: Partial<Record<SectionState, AlertTone>> = {
  queued: 'info',
  waiting_for_agent: 'warning',
  probing: 'info',
  pending: 'warning',
  applying: 'info',
  applied: 'info',
  active: 'success',
  partially_active: 'warning',
  failed: 'danger',
  needs_attention: 'danger',
  conflict: 'warning',
  rejected: 'danger',
  expired: 'warning',
  cancelled: 'info',
};

const BADGE: Partial<Record<SectionState, NonNullable<BadgeProps['tone']>>> = {
  active: 'success',
  partially_active: 'warning',
  failed: 'danger',
  needs_attention: 'danger',
  rejected: 'danger',
  conflict: 'warning',
  expired: 'warning',
  pending: 'warning',
  waiting_for_agent: 'warning',
};

/** Seconds left until `at`, refreshed every second while it is shown. */
function useCountdown(at: string | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!at) return undefined;
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1_000);
    return () => {
      window.clearInterval(timer);
    };
  }, [at]);
  if (!at) return null;
  return Math.max(0, Math.round((Date.parse(at) - now) / 1000));
}

function countdownText(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const pad = (value: number) => String(value).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${pad(minutes)}:${pad(rest)}`;
}

function StepLine({ step }: { step: RequestStepOut }) {
  const { t } = useTranslation();
  const style = STEP_ICON[step.state];
  const Icon = style.icon;
  const english = currentLang() === 'en';
  const message = (english ? step.message_en : step.message_tr) ?? null;
  return (
    <li className="flex items-start gap-2 text-13">
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', style.className)} />
      <span className="flex min-w-0 flex-col">
        <span>
          {t(`admin.steps.${step.step}`, { defaultValue: step.step })}
          <span className="sr-only">: </span>
          <span className="ml-2 text-xs text-muted-strong">
            {t(`admin.stepStates.${step.state}`)}
            {step.at ? ` · ${formatDateTime(step.at)}` : ''}
          </span>
        </span>
        {message ? <span className="text-xs text-muted-strong">{message}</span> : null}
      </span>
    </li>
  );
}

/** "ArciCollector ✓ · ArciIngest …": which service has loaded the new configuration. */
function ServiceChips({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const reloads = request.steps.filter((step) => STEP_SERVICES[step.step] !== undefined);
  if (reloads.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label={t('admin.progress.services')}>
      {reloads.map((step) => {
        const service = STEP_SERVICES[step.step] ?? step.step;
        const tone =
          step.state === 'done'
            ? 'success'
            : step.state === 'failed'
              ? 'danger'
              : step.state === 'skipped'
                ? 'neutral'
                : 'info';
        return (
          <li key={step.step}>
            <Badge tone={tone} icon={STEP_ICON[step.state].icon}>
              {service}
              <span className="sr-only">: {t(`admin.stepStates.${step.state}`)}</span>
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

export interface RequestProgressProps {
  requestId: string;
  /** Shown while the new values are tried (e.g. "Kamu SM ile bağlantı deneniyor…"). */
  probingText?: string;
  /** Called once when the request has reached a terminal state. */
  onTerminal?: (request: AdminRequestOut) => void;
  /** "Yeniden dene" after a failure (the section sends its form again). */
  onRetry?: () => void;
  /** "Sayfayı yenile" after a conflict (the section reloads the configuration). */
  onReload?: () => void;
  /** The result of an action, shown below the state (seal, credit, test …). */
  renderResult?: (request: AdminRequestOut) => ReactNode;
  /** No toast on success (the caller shows the result itself). */
  quiet?: boolean;
  className?: string;
}

/**
 * One request of the browser administration as it goes (contract 2.10, 2.17): its state with the
 * API's label and the short request number for support, the steps, which services loaded the new
 * configuration, the hold with its countdown and "İptal et", the outcome ("Uygulandı", partly
 * applied, "Uygulanmadı, önceki ayarlar geçerli" with "Yeniden dene" and "Geri al", the manual
 * steps of a change that needs attention) and the warnings. Polled by `useAdminRequest`.
 */
export function RequestProgress({
  requestId,
  probingText,
  onTerminal,
  onRetry,
  onReload,
  renderResult,
  quiet = false,
  className,
}: RequestProgressProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const client = useQueryClient();
  const { withReauth } = useReauth();
  const query = useAdminRequest(requestId);
  const cancel = useCancelRequest();
  const revert = useRevertRequest();
  const [follow, setFollow] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const request = query.data;
  const reported = useRef<string | null>(null);
  const seconds = useCountdown(request?.state === 'pending' ? request.effective_at : null);

  useEffect(() => {
    if (!request || !isTerminal(request.state) || reported.current === request.id) return;
    reported.current = request.id;
    void client.invalidateQueries({ queryKey: queryKeys.adminConfig });
    void client.invalidateQueries({ queryKey: queryKeys.adminStatus });
    void client.invalidateQueries({ queryKey: queryKeys.adminRequestsAll });
    if (!quiet && (request.state === 'active' || request.state === 'succeeded'))
      toast({ title: t('admin.progress.appliedToast'), variant: 'success' });
    onTerminal?.(request);
  }, [request, client, onTerminal, quiet, t]);

  if (follow)
    return (
      <RequestProgress
        requestId={follow}
        probingText={probingText}
        onTerminal={onTerminal}
        onRetry={onRetry}
        onReload={onReload}
        renderResult={renderResult}
        quiet={quiet}
        className={className}
      />
    );

  if (!request)
    return query.isError ? (
      <Alert tone="danger" className={className}>
        {errorMessage(query.error)}
      </Alert>
    ) : (
      <div role="status" className={cn('flex items-center gap-2 text-13 text-muted', className)}>
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        {t('admin.progress.sending')}
      </div>
    );

  const state = requestSectionState(request.state);
  const label = lang === 'en' ? request.label_en : request.label_tr;
  const error = request.error ? localized(request.error) : null;
  const manual = lang === 'en' ? request.manual_steps_en : request.manual_steps_tr;
  const notReloaded = request.state === 'partially_active' ? servicesNotReloaded(request) : [];
  const systemFailed = request.state === 'partially_active' ? systemStepFailed(request) : [];

  async function cancelHeld() {
    setProblem(null);
    try {
      // A held change is cancelled by a request of its own; this one then ends `cancelled`.
      await withReauth(() => cancel.mutateAsync(requestId));
      void query.refetch();
    } catch (failure) {
      if (!isReauthCancelled(failure)) setProblem(errorMessage(failure));
    }
  }

  async function revertApplied() {
    setProblem(null);
    try {
      const accepted = await withReauth(() => revert.mutateAsync(requestId));
      setFollow(accepted.request_id);
    } catch (failure) {
      if (!isReauthCancelled(failure)) setProblem(errorMessage(failure));
    }
  }

  const body: ReactNode[] = [];
  if (state === 'probing')
    body.push(<p key="probing">{probingText ?? t('admin.progress.probing')}</p>);
  if (state === 'queued') body.push(<p key="queued">{t('admin.progress.queued')}</p>);
  if (state === 'waiting_for_agent')
    body.push(<p key="waiting">{t('admin.progress.waitingForAgent')}</p>);
  if (state === 'applying' || state === 'applied')
    body.push(<p key="applying">{t(`admin.progress.${state}`)}</p>);
  if (state === 'pending')
    body.push(
      <p key="pending" data-testid="hold-countdown">
        {request.effective_at
          ? t('admin.progress.heldUntil', {
              time: formatDateTime(request.effective_at),
              left: countdownText(seconds ?? 0),
            })
          : t('admin.progress.held')}
      </p>,
    );
  if (state === 'active') body.push(<p key="active">{t('admin.progress.active')}</p>);
  if (state === 'partially_active') {
    body.push(<p key="partly">{t('admin.progress.partiallyActive')}</p>);
    for (const service of notReloaded)
      body.push(<p key={service}>{t('admin.progress.notReloaded', { service })}</p>);
    for (const step of systemFailed)
      body.push(<p key={step}>{t(`admin.progress.systemFailed.${step.replace('.', '_')}`)}</p>);
  }
  if (state === 'failed') body.push(<p key="failed">{t('admin.progress.failed')}</p>);
  if (state === 'conflict' || state === 'rejected')
    body.push(<p key="conflict">{t('admin.progress.conflict')}</p>);
  if (state === 'expired') body.push(<p key="expired">{t('admin.progress.expired')}</p>);
  if (state === 'cancelled') body.push(<p key="cancelled">{t('admin.progress.cancelled')}</p>);
  if (error) body.push(<p key="error">{error}</p>);
  if (request.rolled_back) body.push(<p key="rolled">{t('admin.progress.rolledBack')}</p>);
  if (state === 'needs_attention') {
    body.push(
      <p key="attention">
        {request.version
          ? t('admin.progress.needsAttentionVersion', { version: request.version.number })
          : t('admin.progress.needsAttention')}
      </p>,
    );
    if (manual)
      body.push(
        <pre key="manual" className="mt-1 font-mono text-xs whitespace-pre-wrap">
          {manual}
        </pre>,
      );
  }

  return (
    <div
      className={cn('flex flex-col gap-3', className)}
      data-testid="request-progress"
      data-state={request.state}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={BADGE[state] ?? 'info'} data-testid="request-state">
          {label}
        </Badge>
        <span className="text-xs text-muted-strong">
          {t('admin.progress.requestNumber', { id: request.short_id })}
        </span>
      </div>
      <Alert
        tone={TONE[state] ?? 'info'}
        assertive={state === 'failed' || state === 'needs_attention'}
      >
        <div className="flex flex-col gap-1">{body}</div>
      </Alert>
      <ServiceChips request={request} />
      {request.warnings.length > 0 ? (
        <ul className="flex flex-col gap-1 text-13" aria-label={t('admin.progress.warnings')}>
          {request.warnings.map((warning) => (
            <li key={warning.code} className="flex items-start gap-2 text-warning-ink">
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {localized(warning)}
            </li>
          ))}
        </ul>
      ) : null}
      {renderResult ? renderResult(request) : null}
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        {cancellable(request) ? (
          <Button
            variant="secondary"
            size="sm"
            busy={cancel.isPending}
            onClick={() => void cancelHeld()}
          >
            {t('admin.progress.cancel')}
          </Button>
        ) : null}
        {state === 'failed' || state === 'expired' ? (
          onRetry ? (
            <Button variant="secondary" size="sm" onClick={onRetry}>
              <RefreshCw aria-hidden="true" />
              {t('admin.progress.retry')}
            </Button>
          ) : null
        ) : null}
        {(state === 'conflict' || state === 'rejected') && onReload ? (
          <Button variant="secondary" size="sm" onClick={onReload}>
            <RefreshCw aria-hidden="true" />
            {t('admin.progress.reload')}
          </Button>
        ) : null}
        {revertible(request) ? (
          <Button
            variant="secondary"
            size="sm"
            busy={revert.isPending}
            onClick={() => void revertApplied()}
          >
            <Undo2 aria-hidden="true" />
            {t('admin.progress.revert')}
          </Button>
        ) : null}
      </div>
      {request.steps.length > 0 ? (
        <details className="text-13">
          <summary className="cursor-pointer font-medium text-foreground">
            {t('admin.progress.steps', { count: request.steps.length })}
          </summary>
          <ol className="mt-2 flex flex-col gap-1.5">
            {request.steps.map((step) => (
              <StepLine key={step.step} step={step} />
            ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}
