import { ScrollText } from 'lucide-react';
import { useRef, useState } from 'react';
import type { RefObject } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ChangesTable } from '@/components/admin/change-review';
import { RequestProgress } from '@/components/admin/request-progress';
import { SettingsCard, SettingsFrame } from '@/components/admin/settings-frame';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import { EmptyState } from '@/components/ui/empty-state';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { requestSectionState } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useAdminRequests, useAdminStatus } from '@/lib/api-hooks';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { AdminRequestOut, RequestKind } from '@/lib/settings-types';

import { requestTitle } from './request-labels';

const KINDS: RequestKind[] = [
  'config.apply',
  'config.revert',
  'config.cancel',
  'action.seal',
  'action.credit',
  'action.tsa_test',
  'action.replica_test',
  'worker.test_email',
  'worker.lease_rebuild',
];
const ALL = 'all';

function stateTone(request: AdminRequestOut): NonNullable<BadgeProps['tone']> {
  const state = requestSectionState(request.state);
  if (state === 'active') return 'success';
  if (state === 'failed' || state === 'needs_attention' || state === 'rejected') return 'danger';
  if (
    state === 'partially_active' ||
    state === 'conflict' ||
    state === 'expired' ||
    state === 'pending'
  )
    return 'warning';
  return request.terminal ? 'neutral' : 'info';
}

/** A request in the drawer: its progress (still followed when open), changes and audit target. */
function RequestSheet({
  request,
  onClose,
  returnFocus,
}: {
  request: AdminRequestOut | null;
  onClose: () => void;
  returnFocus: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  return (
    <Sheet
      open={request !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent returnFocus={returnFocus}>
        {request ? (
          <>
            <SheetHeader>
              <SheetTitle className="text-base font-semibold">
                {requestTitle(request, t)}
              </SheetTitle>
              <SheetDescription className="text-13 text-muted">
                {t('settings.history.requested', {
                  user: request.requested_by,
                  time: formatDateTime(request.created_at),
                })}
              </SheetDescription>
            </SheetHeader>
            <SheetBody className="flex flex-col gap-4">
              <RequestProgress requestId={request.id} quiet />
              {Object.keys(request.changes).length > 0 ? (
                <section className="flex flex-col gap-2">
                  <h3 className="text-sm font-semibold text-foreground">
                    {t('settings.history.changes')}
                  </h3>
                  <ChangesTable
                    changes={request.changes}
                    caption={t('settings.history.changesCaption')}
                  />
                </section>
              ) : null}
              {request.version ? (
                <p className="text-13 text-muted-strong">
                  {t('settings.history.version', { number: request.version.number })}
                </p>
              ) : null}
              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {t('settings.history.audit')}
                </h3>
                <p className="text-13 text-muted">{t('settings.history.auditHint')}</p>
                <CodeBlock
                  label={t('settings.history.auditTarget')}
                  code={`admin-request:${request.id}`}
                />
                <Button asChild variant="secondary" size="sm" className="self-start">
                  <Link to="/audit">
                    <ScrollText aria-hidden="true" />
                    {t('settings.history.openAudit')}
                  </Link>
                </Button>
              </section>
            </SheetBody>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/**
 * Ayarlar → Değişiklik geçmişi (contract 2.17, `settings.manage`): every change request and
 * action of the browser administration, newest first, with its state; a request opens with its
 * steps, changes (secrets only as set/unset), "İptal et" for a held change, "Geri al" for an
 * applied one and the audit log target. Below: the kept versions and the diagnostics package.
 */
export function HistoryPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const [kind, setKind] = useState<RequestKind | null>(null);
  const [open, setOpen] = useState<AdminRequestOut | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const status = useAdminStatus();
  const requests = useAdminRequests(kind);
  const items = requests.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <SettingsFrame
      title={t('settings.history.title')}
      description={t('settings.history.description')}
    >
      {status.data?.config_origin === 'outside' ? (
        <Alert
          tone="warning"
          assertive={false}
          title={t('settings.history.outsideTitle')}
          data-testid="config-outside"
        >
          {t('settings.history.outside')}
        </Alert>
      ) : null}
      <SettingsCard
        title={t('settings.history.listTitle')}
        testId="settings-history"
        actions={
          <div className="flex items-center gap-2">
            <Label htmlFor="history-kind" className="text-xs text-muted-strong">
              {t('settings.history.kind')}
            </Label>
            <Select
              value={kind ?? ALL}
              onValueChange={(value) => {
                setKind(value === ALL ? null : (value as RequestKind));
              }}
            >
              <SelectTrigger id="history-kind" className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t('settings.history.allKinds')}</SelectItem>
                {KINDS.map((item) => (
                  <SelectItem key={item} value={item}>
                    {t(`admin.kinds.${item}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        }
      >
        {requests.isPending ? (
          <Skeleton className="h-40" />
        ) : requests.isError ? (
          <Alert tone="danger">{errorMessage(requests.error)}</Alert>
        ) : items.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title={t('settings.history.empty')}
            description={t('settings.history.emptyHint')}
          />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-13">
              <caption className="sr-only">{t('settings.history.caption')}</caption>
              <thead className="bg-subtle text-left text-xs text-muted-strong">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.time')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.request')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.state')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.user')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.number')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((request) => (
                  <tr key={request.id} className="border-t border-border align-top">
                    <td className="px-3 py-2 tabular">{formatDateTime(request.created_at)}</td>
                    <th scope="row" className="px-3 py-2 text-left font-medium">
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0 text-left"
                        onClick={(event) => {
                          opener.current = event.currentTarget;
                          setOpen(request);
                        }}
                      >
                        {requestTitle(request, t)}
                      </Button>
                      {request.effective_at && request.state === 'pending' ? (
                        <span className="block text-xs font-normal text-muted-strong">
                          {t('settings.history.effective', {
                            time: formatDateTime(request.effective_at),
                          })}
                        </span>
                      ) : null}
                    </th>
                    <td className="px-3 py-2">
                      <Badge tone={stateTone(request)}>
                        {lang === 'en' ? request.label_en : request.label_tr}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">{request.requested_by}</td>
                    <td className="px-3 py-2 font-mono text-xs">{request.short_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {requests.hasNextPage ? (
          <Button
            variant="secondary"
            className="self-start"
            busy={requests.isFetchingNextPage}
            onClick={() => void requests.fetchNextPage()}
          >
            {t('common.loadMore')}
          </Button>
        ) : null}
      </SettingsCard>
      <RequestSheet
        request={open}
        onClose={() => {
          setOpen(null);
        }}
        returnFocus={opener}
      />
    </SettingsFrame>
  );
}
