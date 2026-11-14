import { History, Lock, RefreshCw, Send, ShieldQuestionMark, Webhook } from 'lucide-react';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { RequestProgress } from '@/components/admin/request-progress';
import { SettingsCard, SettingsFrame } from '@/components/admin/settings-frame';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Badge, SeverityBadge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
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
import { useLicense, useTestWebhook, useWebhookDeliveries, useWebhooks } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import type { AdminRequestOut } from '@/lib/settings-types';
import type { WebhookDeliveryStatus, WebhookOut } from '@/lib/webhook-types';
import { alertHref } from '@/pages/security/alerts-model';

import { formatVerified, sortWebhooks, testLocked, testResultOf } from './webhooks-model';

const STATUS_TONE: Record<WebhookDeliveryStatus, NonNullable<BadgeProps['tone']>> = {
  sent: 'success',
  queued: 'info',
  failed: 'danger',
};

function StatusBadge({ status }: { status: WebhookDeliveryStatus }) {
  const { t } = useTranslation();
  return <Badge tone={STATUS_TONE[status]}>{t(`webhooks.status.${status}`)}</Badge>;
}

/** The outcome of "Test gönder": the answer's status code, or why it failed (§2.4.2). */
function TestOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = testResultOf(request);
  if (!result) return null;
  return (
    <p className="text-13 text-foreground" data-testid="webhook-test-result">
      {result.ok
        ? t('webhooks.test.ok', { status: result.status ?? '—' })
        : t('webhooks.test.failed', { error: result.error_text ?? '—' })}
    </p>
  );
}

/** The deliveries of one webhook, newest first, each linked to its alert. */
function DeliveriesSheet({ name, onClose }: { name: string | null; onClose: () => void }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const deliveries = useWebhookDeliveries(name);
  const head = 'px-3 py-2 text-left text-xs font-medium text-muted-strong';
  const items = deliveries.data?.items ?? [];
  return (
    <Sheet
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-3xl" data-testid="webhook-deliveries">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {t('webhooks.deliveries.title', { name: name ?? '' })}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {t('webhooks.deliveries.description')}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-3">
          {deliveries.isPending ? (
            <div role="status" aria-label={t('common.loading')}>
              <Skeleton className="h-40" />
            </div>
          ) : deliveries.isError ? (
            <Alert tone="danger">{errorMessage(deliveries.error)}</Alert>
          ) : items.length === 0 ? (
            <p className="text-13 text-muted">{t('webhooks.deliveries.empty')}</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-13">
                <caption className="sr-only">
                  {t('webhooks.deliveries.title', { name: name ?? '' })}
                </caption>
                <thead className="bg-subtle">
                  <tr>
                    {(['created', 'alert', 'status', 'attempts', 'error'] as const).map(
                      (column) => (
                        <th key={column} scope="col" className={head}>
                          {t(`webhooks.deliveries.columns.${column}`)}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={`${item.alert_id}-${item.created_at}`}
                      className="border-t border-border align-top"
                      data-testid="webhook-delivery"
                    >
                      <th scope="row" className="px-3 py-1.5 text-left font-normal tabular">
                        {formatDateTime(item.created_at)}
                      </th>
                      <td className="px-3 py-1.5">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <Link
                            to={alertHref(item.alert_id)}
                            className="rounded-sm font-mono text-xs text-accent-ink hover:underline"
                          >
                            {t('webhooks.deliveries.alert', { id: item.alert_id, rule: item.rule })}
                          </Link>
                          <SeverityBadge severity={item.severity} />
                        </span>
                      </td>
                      <td className="px-3 py-1.5">
                        <span className="flex flex-col items-start gap-0.5">
                          <StatusBadge status={item.status} />
                          {item.sent_at ? (
                            <span className="text-xs text-muted tabular">
                              {formatDateTime(item.sent_at)}
                            </span>
                          ) : item.next_attempt_at ? (
                            <span className="text-xs text-muted tabular">
                              {t('webhooks.deliveries.next', {
                                time: formatDateTime(item.next_attempt_at),
                              })}
                            </span>
                          ) : null}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 tabular">
                        {formatNumber(item.attempts, 0, lang)}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs break-all">
                        {item.last_error ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function WebhookRow({
  webhook,
  canTest,
  busy,
  onTest,
  onDeliveries,
}: {
  webhook: WebhookOut;
  canTest: boolean;
  busy: boolean;
  onTest: (name: string) => void;
  onDeliveries: (name: string) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const counts = webhook.counts_7d;
  return (
    <tr
      className="border-t border-border align-middle"
      data-testid="webhook-row"
      data-name={webhook.name}
    >
      <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-medium">
        {webhook.name}
      </th>
      <td className="px-3 py-2">
        <span className="flex flex-wrap items-center gap-1.5">
          {t(`webhooks.formats.${webhook.format}`)}
          {!formatVerified(webhook.format) ? (
            <Badge tone="warning" icon={ShieldQuestionMark} title={t('webhooks.verifyHint')}>
              {t('webhooks.verify')}
            </Badge>
          ) : null}
        </span>
      </td>
      <td className="px-3 py-2 font-mono text-xs">{webhook.host}</td>
      <td className="px-3 py-2">
        <span className="flex flex-col items-start gap-0.5">
          <SeverityBadge severity={webhook.min_severity} />
          <span className="text-xs text-muted">
            {webhook.rules.length === 0
              ? t('webhooks.allRules')
              : t('webhooks.someRules', { rules: webhook.rules.join(', ') })}
          </span>
        </span>
      </td>
      <td className="px-3 py-2">
        <Badge tone={webhook.enabled ? 'success' : 'neutral'}>
          {t(webhook.enabled ? 'webhooks.on' : 'webhooks.off')}
        </Badge>
      </td>
      <td className="px-3 py-2">
        {webhook.last_delivery ? (
          <span className="flex flex-col items-start gap-0.5">
            <StatusBadge status={webhook.last_delivery.status} />
            <span className="text-xs text-muted tabular">
              {formatDateTime(webhook.last_delivery.time)}
            </span>
          </span>
        ) : (
          <span className="text-muted">{t('webhooks.never')}</span>
        )}
      </td>
      <td className="px-3 py-2 text-xs whitespace-nowrap tabular" data-testid="webhook-counts">
        {t('webhooks.counts', {
          sent: formatNumber(counts.sent, 0, lang),
          failed: formatNumber(counts.failed, 0, lang),
        })}
      </td>
      <td className="px-3 py-2">
        <span className="flex flex-wrap justify-end gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('webhooks.deliveriesOf', { name: webhook.name })}
            onClick={() => {
              onDeliveries(webhook.name);
            }}
          >
            <History aria-hidden="true" />
            {t('webhooks.deliveriesButton')}
          </Button>
          {canTest ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy || !webhook.enabled}
              aria-label={t('webhooks.testOf', { name: webhook.name })}
              onClick={() => {
                onTest(webhook.name);
              }}
            >
              <Send aria-hidden="true" />
              {t('webhooks.testButton')}
            </Button>
          ) : null}
        </span>
      </td>
    </tr>
  );
}

/**
 * Ayarlar → Webhook'lar (Wave 6 contract §2.4.4, W6-F; `alerts.read`): the webhooks written into
 * `arcilogger.toml` on the server (D4: the browser never configures one) with their format, host,
 * minimum severity, rules, last delivery and the counts of the last 7 days; each one's deliveries;
 * and, with `settings.manage` and the `security_rules` module, "Test gönder", which queues the
 * probe as an administration request and follows it like the SMTP trial.
 */
export function WebhooksPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const { withReauth } = useReauth();
  const webhooks = useWebhooks();
  const license = useLicense();
  const test = useTestWebhook();
  const [deliveries, setDeliveries] = useState<string | null>(null);
  const [trial, setTrial] = useState<{ name: string; requestId: string } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const progress = useRef<HTMLDivElement>(null);
  const locked = testLocked(license.data);
  const canTest = can('settings.manage') && !locked;
  const list = sortWebhooks(webhooks.data?.webhooks ?? []);

  async function runTest(name: string) {
    setFailure(null);
    try {
      const accepted = await withReauth(() => test.mutateAsync(name));
      setTrial({ name, requestId: accepted.request_id });
      window.requestAnimationFrame(() => {
        progress.current?.focus();
      });
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  if (webhooks.isPending) return <PageSkeleton />;

  return (
    <SettingsFrame
      title={t('webhooks.title')}
      description={t('webhooks.description')}
      machine={false}
    >
      <Alert tone="info" assertive={false} data-testid="webhooks-server-only">
        {t('webhooks.serverOnly')}
      </Alert>
      {locked && can('settings.manage') ? (
        <Alert tone="warning" assertive={false} data-testid="webhooks-locked">
          <span className="flex items-start gap-2">
            <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {t('webhooks.locked')}
          </span>
        </Alert>
      ) : null}
      {webhooks.isError ? (
        <Alert tone="danger">
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(webhooks.error)}
            <Button size="sm" variant="secondary" onClick={() => void webhooks.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {failure ? (
        <Alert tone="danger" data-testid="webhook-test-error">
          {failure}
        </Alert>
      ) : null}
      {trial ? (
        <div
          ref={progress}
          tabIndex={-1}
          className="flex flex-col gap-2 outline-none"
          data-testid="webhook-test"
        >
          <h2 className="text-sm font-semibold text-foreground">
            {t('webhooks.test.title', { name: trial.name })}
          </h2>
          <RequestProgress
            requestId={trial.requestId}
            quiet
            renderResult={(request) => <TestOutcome request={request} />}
          />
        </div>
      ) : null}
      {webhooks.data ? (
        list.length === 0 ? (
          <EmptyState
            icon={Webhook}
            title={t('webhooks.empty')}
            description={t('webhooks.emptyHint')}
          />
        ) : (
          <SettingsCard title={t('webhooks.caption')} testId="webhooks-table">
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-13">
                <caption className="sr-only">{t('webhooks.caption')}</caption>
                <thead className="bg-subtle text-left text-xs text-muted-strong">
                  <tr>
                    {(
                      ['name', 'format', 'host', 'severity', 'enabled', 'last', 'counts'] as const
                    ).map((column) => (
                      <th
                        key={column}
                        scope="col"
                        className="px-3 py-2 font-medium whitespace-nowrap"
                      >
                        {t(`webhooks.columns.${column}`)}
                      </th>
                    ))}
                    <th scope="col" className="px-3 py-2 font-medium">
                      <span className="sr-only">{t('webhooks.columns.actions')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((webhook) => (
                    <WebhookRow
                      key={webhook.name}
                      webhook={webhook}
                      canTest={canTest}
                      busy={test.isPending}
                      onTest={(name) => void runTest(name)}
                      onDeliveries={setDeliveries}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </SettingsCard>
        )
      ) : null}
      <DeliveriesSheet
        name={deliveries}
        onClose={() => {
          setDeliveries(null);
        }}
      />
    </SettingsFrame>
  );
}
