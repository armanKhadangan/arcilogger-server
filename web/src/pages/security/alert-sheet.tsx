import {
  ArrowRightLeft,
  Bot,
  CircleCheck,
  CircleSlash,
  MessageSquarePlus,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  Search,
  Undo2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge, SeverityBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/label';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { Timeline, type TimelineItem } from '@/components/ui/timeline';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError } from '@/lib/api';
import { useAddAlertNote, useAlert, useChangeAlertState } from '@/lib/api-hooks';
import type { AlertDetail, AlertEvent, AlertState } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { Facts } from '@/pages/devices/device-parts';

import { AlertStateBadge } from './alert-parts';
import {
  actionOf,
  activeAlertOf,
  alertSummaryText,
  closeHintKey,
  closesActive,
  detailRows,
  isActive,
  isAlertState,
  nextStates,
  noteProblem,
  ruleName,
  SYSTEM_REASONS,
  type AlertAction,
} from './alerts-model';

const ACTION_ICON: Record<AlertAction, LucideIcon> = {
  acknowledge: Search,
  resolve: CircleCheck,
  dismiss: CircleSlash,
  release: Undo2,
  reopen: RotateCcw,
  markResolved: CircleCheck,
  markDismissed: CircleSlash,
};

function stateLabel(state: string | null, t: (key: string) => string): string {
  return state && isAlertState(state) ? t(`alertStates.${state}`) : (state ?? '—');
}

/** A check's reason code in words (`messages_again` → "Kaynak yeniden mesaj gönderiyor"). */
function ReasonText({ code }: { code: string }) {
  const { t } = useTranslation();
  if (SYSTEM_REASONS.includes(code)) return <>{t(`security.reasons.${code}`)}</>;
  return <span className="font-mono text-xs">{visibleText(code)}</span>;
}

/**
 * The alert's history, newest first (the API sends it oldest first): state changes (who, from →
 * to; a check's reason) and notes.
 */
function History({ events }: { events: readonly AlertEvent[] }) {
  const { t } = useTranslation();
  if (events.length === 0)
    return <p className="text-13 text-muted">{t('security.detail.noEvents')}</p>;
  const items: TimelineItem[] = [...events]
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at) || b.id - a.id)
    .map((event) => {
      const system = event.by === 'system';
      const by = system ? t('security.detail.system') : event.by;
      if (event.kind === 'note') {
        return {
          id: String(event.id),
          time: formatDateTime(event.at),
          title: t('security.detail.noteBy', { user: by }),
          description: (
            <span className="whitespace-pre-wrap text-foreground">
              {visibleText(event.text ?? '')}
            </span>
          ),
          icon: MessageSquareText,
          tone: 'info',
        };
      }
      return {
        id: String(event.id),
        time: formatDateTime(event.at),
        title: t('security.detail.stateChange', {
          from: stateLabel(event.from_state, t),
          to: stateLabel(event.to_state, t),
        }),
        description: (
          <span className="flex flex-col gap-0.5">
            <span>{t('security.detail.by', { user: by })}</span>
            {event.text ? (
              system ? (
                <span>
                  {t('security.detail.reason')} <ReasonText code={event.text} />
                </span>
              ) : (
                <span className="whitespace-pre-wrap text-foreground">
                  {visibleText(event.text)}
                </span>
              )
            ) : null}
          </span>
        ),
        icon: system ? Bot : ArrowRightLeft,
        tone: event.to_state === 'resolved' ? 'success' : 'neutral',
      };
    });
  return <Timeline items={items} />;
}

interface Failure {
  text: string;
  /** 409: the alert changed meanwhile (reloaded) or the same problem has another active alert. */
  kind: 'conflict' | 'keyActive' | 'other';
  /** The active alert of the same problem (`alert_key_active`). */
  activeId: number | null;
}

function failureOf(error: unknown): Failure {
  const text = errorMessage(error);
  if (isApiError(error, 'alert_key_active'))
    return { text, kind: 'keyActive', activeId: activeAlertOf(error.details) };
  if (isApiError(error) && error.status === 409) return { text, kind: 'conflict', activeId: null };
  return { text, kind: 'other', activeId: null };
}

const FAILURE_TESTID: Record<Failure['kind'], string> = {
  conflict: 'state-conflict',
  keyActive: 'state-key-active',
  other: 'state-error',
};

/**
 * The state buttons the API allows from the alert's state, with an optional note. A refusal is
 * explained: changed meanwhile (the alert is reloaded), or — for a re-open — the active alert of
 * the same problem, one click away.
 */
function StateActions({
  alert,
  onChanged,
  onOpenAlert,
}: {
  alert: AlertDetail;
  onChanged: () => void;
  onOpenAlert: (id: number) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const change = useChangeAlertState();
  const group = useRef<HTMLDivElement>(null);
  const [target, setTarget] = useState<AlertState | null>(null);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  useEffect(() => {
    setTarget(null);
    setNote('');
    setProblem(null);
  }, [alert.id, alert.state]);

  useEffect(() => {
    setFailure(null);
  }, [alert.id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!target) return;
    const found = noteProblem(note, true);
    setProblem(found ?? null);
    if (found) {
      document.getElementById(`${id}-note`)?.focus();
      return;
    }
    setFailure(null);
    try {
      const text = note.trim();
      await withReauth(() =>
        change.mutateAsync({ id: alert.id, state: target, ...(text ? { note: text } : {}) }),
      );
      toast({
        title: t('security.actions.done', { state: t(`alertStates.${target}`) }),
        variant: 'success',
      });
      onChanged();
      // The form and its button are gone with the old state: the focus moves to the new actions.
      window.requestAnimationFrame(() => {
        group.current?.querySelector<HTMLButtonElement>('button')?.focus();
      });
    } catch (error) {
      if (isReauthCancelled(error)) return;
      const next = failureOf(error);
      setFailure(next);
      if (next.kind === 'conflict') onChanged();
    }
  }

  const activeId = failure?.activeId ?? null;
  return (
    <div className="flex flex-col gap-3">
      {failure ? (
        <Alert
          tone="danger"
          title={
            failure.kind === 'conflict'
              ? t('security.actions.conflictTitle')
              : failure.kind === 'keyActive'
                ? t('security.actions.keyActiveTitle')
                : undefined
          }
          data-testid={FAILURE_TESTID[failure.kind]}
        >
          <span className="flex flex-col items-start gap-2">
            <span>
              {failure.text}
              {failure.kind === 'conflict' ? ` ${t('security.actions.conflictBody')}` : null}
            </span>
            {activeId !== null ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  onOpenAlert(activeId);
                }}
              >
                {t('security.actions.openActive', { id: activeId })}
              </Button>
            ) : null}
          </span>
        </Alert>
      ) : null}
      <div ref={group} className="flex flex-wrap gap-2">
        {nextStates(alert.state).map((state) => {
          const action = actionOf(alert.state, state);
          const Icon = ACTION_ICON[action];
          return (
            <Button
              key={state}
              size="sm"
              variant={target === state ? 'primary' : 'secondary'}
              aria-pressed={target === state}
              aria-describedby={closesActive(alert.state, state) ? `${id}-close-hint` : undefined}
              onClick={() => {
                setTarget(target === state ? null : state);
                setFailure(null);
              }}
            >
              <Icon aria-hidden="true" />
              {t(`security.actions.${action}`)}
            </Button>
          );
        })}
      </div>
      {isActive(alert.state) ? (
        <p id={`${id}-close-hint`} className="text-xs text-muted" data-testid="close-hint">
          {t(closeHintKey(alert.rule))}
        </p>
      ) : null}
      {target ? (
        <form
          onSubmit={(event) => void submit(event)}
          noValidate
          className="flex flex-col gap-2 rounded-lg border border-border bg-subtle/50 p-3"
        >
          <p className="text-13 text-foreground">
            {t('security.actions.confirm', {
              from: t(`alertStates.${alert.state}`),
              to: t(`alertStates.${target}`),
            })}
          </p>
          <Field
            id={`${id}-note`}
            label={t('security.actions.note')}
            hint={t('security.actions.noteHint')}
            error={problem ? t(problem, { max: 2000 }) : undefined}
          >
            <Textarea
              id={`${id}-note`}
              rows={2}
              value={note}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-note-error` : `${id}-note-hint`}
              onChange={(event) => {
                setNote(event.target.value);
              }}
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setTarget(null);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" size="sm" busy={change.isPending}>
              {change.isPending ? t('common.working') : t('security.actions.submit')}
            </Button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

/** "Not ekle" (`alerts.manage`). */
function AddNote({ alertId }: { alertId: number }) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const add = useAddAlertNote();
  const [text, setText] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    setText('');
    setProblem(null);
    setFailure(null);
  }, [alertId]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = noteProblem(text);
    setProblem(found ?? null);
    setFailure(null);
    if (found) {
      document.getElementById(`${id}-text`)?.focus();
      return;
    }
    try {
      await withReauth(() => add.mutateAsync({ id: alertId, text: text.trim() }));
      setText('');
      toast({ title: t('security.notes.added'), variant: 'success' });
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="flex flex-col gap-2">
      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      <Field
        id={`${id}-text`}
        label={t('security.notes.label')}
        error={problem ? t(problem, { max: 2000 }) : undefined}
      >
        <Textarea
          id={`${id}-text`}
          rows={3}
          value={text}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-text-error` : undefined}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" size="sm" busy={add.isPending}>
          <MessageSquarePlus aria-hidden="true" />
          {add.isPending ? t('common.working') : t('security.notes.add')}
        </Button>
      </div>
    </form>
  );
}

function Deliveries({ alert }: { alert: AlertDetail }) {
  const { t } = useTranslation();
  if (alert.deliveries.length === 0)
    return <p className="text-13 text-muted">{t('security.detail.noDeliveries')}</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-separate border-spacing-0 text-13">
        <caption className="sr-only">{t('security.detail.deliveries')}</caption>
        <thead>
          <tr>
            {(['kind', 'recipient', 'status', 'attempts', 'sent'] as const).map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
              >
                {t(`security.detail.deliveryColumns.${column}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {alert.deliveries.map((delivery, index) => (
            <tr key={`${delivery.kind}-${delivery.recipient_masked}-${index}`}>
              <td className="border-b border-border px-3 py-1.5">
                {t(`security.detail.deliveryKind.${delivery.kind}`)}
              </td>
              <td className="border-b border-border px-3 py-1.5 font-mono text-xs">
                {delivery.recipient_masked}
              </td>
              <td className="border-b border-border px-3 py-1.5">
                {t(`security.detail.deliveryStatus.${delivery.status}`, {
                  defaultValue: delivery.status,
                })}
              </td>
              <td className="border-b border-border px-3 py-1.5 tabular">{delivery.attempts}</td>
              <td className="border-b border-border px-3 py-1.5 tabular">
                {delivery.sent_at ? formatDateTime(delivery.sent_at) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2" aria-labelledby={id}>
      <h3 id={id} className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * One alert (`GET /alerts/{id}`): summary, the detector's details, the runbook in the interface
 * language, the history, notes and the e-mails sent about it. With `alerts.manage` the state can be
 * changed (spec 11) and notes added.
 */
export function AlertSheet({
  alertId,
  manage,
  onClose,
  onOpenAlert,
}: {
  alertId: number | null;
  manage: boolean;
  onClose: () => void;
  /** Show another alert in the drawer (the active alert of the same problem). */
  onOpenAlert: (id: number) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const detail = useAlert(alertId);
  const alert = detail.data;
  const notFound = detail.isError && isApiError(detail.error) && detail.error.status === 404;

  return (
    <Sheet
      open={alertId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {alert
              ? visibleText(alertSummaryText(alert, lang))
              : t('security.detail.title', { id: alertId ?? '' })}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {alert
              ? `${alert.rule} · ${ruleName(alert, lang)} · ${formatDateTime(alert.time)}`
              : t('security.detail.loading')}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-5">
          {detail.isPending ? (
            <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
              <Skeleton className="h-24" />
              <Skeleton className="h-48" />
            </div>
          ) : notFound ? (
            <Alert tone="warning">{t('security.detail.notFound')}</Alert>
          ) : detail.isError ? (
            <Alert tone="danger">
              <span className="flex flex-wrap items-center gap-2">
                {errorMessage(detail.error)}
                <Button size="sm" variant="secondary" onClick={() => void detail.refresh()}>
                  <RefreshCw aria-hidden="true" />
                  {t('common.retry')}
                </Button>
              </span>
            </Alert>
          ) : alert ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={alert.severity} />
                <AlertStateBadge state={alert.state} />
                {alert.auto_resolved ? (
                  <Badge tone="info" icon={Bot}>
                    {t('security.autoResolved')}
                  </Badge>
                ) : null}
              </div>

              {manage ? (
                <Section id={`${id}-actions`} title={t('security.actions.title')}>
                  <StateActions
                    alert={alert}
                    onChanged={() => void detail.refresh()}
                    onOpenAlert={onOpenAlert}
                  />
                </Section>
              ) : null}

              <Section id={`${id}-summary`} title={t('security.detail.summary')}>
                <p className="text-13 whitespace-pre-wrap text-foreground">
                  {visibleText(alertSummaryText(alert, lang))}
                </p>
                <Facts
                  items={[
                    [t('security.columns.rule'), `${alert.rule} · ${ruleName(alert, lang)}`],
                    [t('security.columns.time'), formatDateTime(alert.time)],
                    [t('security.detail.created'), formatDateTime(alert.created_at)],
                    [t('security.detail.updated'), formatDateTime(alert.updated_at)],
                    [
                      t('security.detail.resolved'),
                      alert.resolved_at ? formatDateTime(alert.resolved_at) : '—',
                    ],
                  ]}
                />
              </Section>

              <Section id={`${id}-details`} title={t('security.detail.details')}>
                {Object.keys(alert.details).length === 0 ? (
                  <p className="text-13 text-muted">{t('security.detail.noDetails')}</p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full border-separate border-spacing-0 text-13">
                      <caption className="sr-only">{t('security.detail.details')}</caption>
                      <thead>
                        <tr>
                          <th
                            scope="col"
                            className="border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
                          >
                            {t('security.detail.field')}
                          </th>
                          <th
                            scope="col"
                            className="border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
                          >
                            {t('security.detail.value')}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {detailRows(alert.details).map(([key, value]) => (
                          <tr key={key}>
                            <th
                              scope="row"
                              className="border-b border-border px-3 py-1.5 text-left font-mono text-xs font-normal text-muted-strong"
                            >
                              {key}
                            </th>
                            <td className="border-b border-border px-3 py-1.5 font-mono text-xs break-all">
                              {visibleText(value)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>

              <Section id={`${id}-runbook`} title={t('security.detail.runbook')}>
                <div
                  className="rounded-lg border border-border bg-subtle/50 px-3 py-2 text-13 whitespace-pre-wrap text-foreground"
                  data-testid="runbook"
                >
                  {(lang === 'en' ? alert.runbook_en : alert.runbook_tr) ||
                    t('security.detail.noRunbook')}
                </div>
              </Section>

              <Section id={`${id}-history`} title={t('security.detail.history')}>
                <History events={alert.events} />
              </Section>

              {manage ? (
                <Section id={`${id}-notes`} title={t('security.notes.title')}>
                  <AddNote alertId={alert.id} />
                </Section>
              ) : null}

              <Section id={`${id}-deliveries`} title={t('security.detail.deliveries')}>
                <Deliveries alert={alert} />
                <p className="text-xs text-muted">{t('security.detail.deliveriesNote')}</p>
              </Section>
            </>
          ) : null}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
