import { Download, KeyRound } from 'lucide-react';
import { useId } from 'react';
import type { ReactNode, RefObject } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { HashCheckBadge } from '@/components/evidence/hash-check-badge';
import { Alert } from '@/components/ui/alert';
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
import { Skeleton } from '@/components/ui/skeleton';
import { errorMessage } from '@/lib/api';
import { useEvidencePackage } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDay } from '@/lib/display';
import { BROWSER_HASH_LIMIT } from '@/lib/download';
import { formatBytes, formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { downloadBlockedReason, isExpiredUnpurged, isUnfinished } from './evidence-model';
import { count, packageSize, scopeText } from './evidence-format';
import { PackageStatus } from './evidence-status';
import type { DownloadState } from './use-package-download';

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
      {items.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted">{name}</dt>
          <dd className="min-w-0 break-words text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Received bytes of a running download (a bar, and the numbers as text). */
function DownloadProgress({ state }: { state: DownloadState }) {
  const { t } = useTranslation();
  const lang = currentLang();
  if (state.received === undefined) return null;
  const total = state.total ?? null;
  const text =
    total !== null && total > 0
      ? t('evidence.progress.of', {
          received: formatBytes(state.received, lang),
          total: formatBytes(total, lang),
          percent: Math.min(100, Math.floor((state.received / total) * 100)),
        })
      : t('evidence.progress.received', { received: formatBytes(state.received, lang) });
  return (
    <div className="flex flex-col gap-1">
      <progress
        className="h-2 w-full overflow-hidden rounded-full bg-subtle [&::-moz-progress-bar]:bg-primary [&::-webkit-progress-bar]:bg-subtle [&::-webkit-progress-value]:bg-primary"
        aria-label={t('evidence.progress.label')}
        {...(total !== null && total > 0
          ? { max: total, value: Math.min(state.received, total) }
          : {})}
      />
      <p className="text-xs text-muted tabular">{text}</p>
    </div>
  );
}

/**
 * One evidence package: scope, state (refreshed while it is being built), the ZIP's SHA-256, the
 * verifier's SHA-256 with the instruction to give it to the recipient on a separate channel, the
 * password flag and every download. "İndir" needs `evidence.download`; when it is disabled the
 * reason is shown next to it. Large packages get a hint (the browser receives the whole file
 * before saving it) and the download shows its progress.
 */
export function EvidenceDetailSheet({
  packageId,
  onClose,
  download,
  onDownload,
  returnFocus,
}: {
  packageId: number | null;
  onClose: () => void;
  download: DownloadState | null;
  onDownload: (item: {
    id: number;
    file: string | null;
    sha256: string | null;
    size: number | null;
  }) => void;
  /** Where the focus goes on close when the opener is gone (the sheet opened after "create"). */
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const { can } = useAuth();
  const detail = useEvidencePackage(packageId);
  const item = detail.data;
  const mine = download && download.id === packageId ? download : null;
  const blocked = item ? downloadBlockedReason(item) : null;
  const large = item?.downloadable && item.size !== null && item.size > BROWSER_HASH_LIMIT;

  return (
    <Sheet
      open={packageId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent className="max-w-2xl" returnFocus={returnFocus}>
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {item?.package_name ?? t('evidence.detail.title', { id: packageId ?? '' })}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {item ? scopeText(item, t) : t('common.loading')}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-5">
          {detail.isPending ? (
            <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
              <Skeleton className="h-24" />
              <Skeleton className="h-48" />
            </div>
          ) : detail.isError ? (
            <Alert tone="danger">{errorMessage(detail.error)}</Alert>
          ) : item ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <PackageStatus item={item} />
                {item.password_protected ? (
                  <Badge tone="info" icon={KeyRound}>
                    {t('evidence.passwordProtected')}
                  </Badge>
                ) : null}
              </div>
              {isUnfinished(item) ? (
                <Alert tone="info" assertive={false}>
                  {t('evidence.detail.building')}
                </Alert>
              ) : null}
              {item.status === 'failed' ? (
                <Alert tone="danger" title={t('evidence.detail.failedTitle')}>
                  {item.error ?? '—'}
                </Alert>
              ) : null}
              {item.status === 'expired' || item.status === 'deleted' ? (
                <Alert tone="warning" assertive={false}>
                  {t('evidence.detail.gone')}
                </Alert>
              ) : null}
              {isExpiredUnpurged(item) ? (
                <Alert tone="warning" assertive={false} data-testid="expired-unpurged">
                  {t('evidence.detail.expiredUnpurged', {
                    time: formatDateTime(item.expires_at),
                  })}
                </Alert>
              ) : null}
              {item.legal_grade === false ? (
                <Alert tone="danger" title={t('legal.notLegalTitle')} data-testid="not-legal-grade">
                  {t('evidence.detail.notLegal')}
                </Alert>
              ) : null}
              {item.status === 'ready' && item.legal_grade === null ? (
                <Alert tone="info" assertive={false} data-testid="legal-root-unconfirmed">
                  {t('evidence.detail.rootUnconfirmed')}
                </Alert>
              ) : null}

              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {t('evidence.detail.scope')}
                </h3>
                <Facts
                  items={[
                    [
                      t('evidence.detail.content'),
                      item.scope.content === 'extract'
                        ? t('evidence.wizard.extract')
                        : t('evidence.wizard.fullDays'),
                    ],
                    [t('evidence.detail.days'), item.scope.days.map(formatDay).join(', ') || '—'],
                    [
                      t('evidence.detail.sources'),
                      item.scope.sources?.join(', ') ?? t('evidence.scope.allSources'),
                    ],
                    [t('evidence.detail.extractLines'), count(item.scope.extract_lines)],
                    [
                      t('evidence.detail.lookup'),
                      item.lookup_id === null ? (
                        '—'
                      ) : can('lookup.run') ? (
                        // The API shows a non-administrator only packages of their own lookups
                        // (the lookup visibility rule), so the lookup behind it opens for them.
                        <Link
                          className="text-accent-ink underline"
                          to={`/lookup/${item.lookup_id}`}
                        >
                          {t('evidence.scope.lookup', { id: item.lookup_id })}
                        </Link>
                      ) : (
                        t('evidence.scope.lookup', { id: item.lookup_id })
                      ),
                    ],
                    [t('evidence.detail.reference'), item.scope.reference ?? '—'],
                  ]}
                />
              </section>

              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {t('evidence.detail.package')}
                </h3>
                <Facts
                  items={[
                    [
                      t('evidence.detail.file'),
                      <span className="font-mono text-xs">{item.file ?? '—'}</span>,
                    ],
                    [t('evidence.detail.size'), packageSize(item, t)],
                    [t('evidence.columns.createdBy'), item.created_by],
                    [t('evidence.columns.createdAt'), formatDateTime(item.created_at)],
                    [
                      t('evidence.detail.finishedAt'),
                      item.finished_at ? formatDateTime(item.finished_at) : '—',
                    ],
                    [t('evidence.columns.expiresAt'), formatDateTime(item.expires_at)],
                    [
                      t('evidence.detail.password'),
                      item.password_protected
                        ? t('evidence.detail.passwordYes')
                        : t('evidence.detail.passwordNo'),
                    ],
                  ]}
                />
                {item.sha256 ? (
                  <CodeBlock label={t('evidence.detail.sha256')} code={item.sha256} wrap />
                ) : null}
                {item.index_sha256 ? (
                  <CodeBlock
                    label={t('evidence.detail.indexSha256')}
                    code={item.index_sha256}
                    wrap
                  />
                ) : null}
              </section>

              {item.verifier_sha256 ? (
                <section className="flex flex-col gap-2" aria-labelledby="verifier-title">
                  <h3 id="verifier-title" className="text-sm font-semibold text-foreground">
                    {t('evidence.detail.verifierTitle')}
                  </h3>
                  <CodeBlock label="verify.py SHA-256" code={item.verifier_sha256} wrap />
                  <Alert tone="info" assertive={false}>
                    {t('evidence.detail.verifierHint')}
                  </Alert>
                </section>
              ) : null}

              <section className="flex flex-col gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {t('evidence.detail.downloads', { count: item.download_count })}
                </h3>
                {item.downloads.length === 0 ? (
                  <p className="text-13 text-muted">{t('evidence.detail.noDownloads')}</p>
                ) : (
                  <ul className="flex flex-col gap-1 text-13">
                    {item.downloads.map((entry) => (
                      <li
                        key={`${entry.time}-${entry.username}`}
                        className="flex flex-wrap gap-x-2"
                      >
                        <span className="tabular">{formatDateTime(entry.time)}</span>
                        <span className="text-foreground">{entry.username}</span>
                        <span className="font-mono text-xs text-muted-strong">
                          {entry.ip ?? '—'}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {item.extract.length > 0 ? (
                <details className="rounded-lg border border-border px-3 py-2 text-13">
                  <summary className="cursor-pointer font-medium text-foreground">
                    {t('evidence.detail.extractList', { count: item.extract.length })}
                  </summary>
                  <ul className="mt-2 flex flex-col gap-0.5 font-mono text-xs">
                    {item.extract.map((line) => (
                      <li key={`${line.day}-${line.source}-${line.line}`}>
                        {line.day} {line.source} {line.line}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {large && item.size !== null && can('evidence.download') ? (
                <Alert tone="info" assertive={false} data-testid="large-package">
                  {t('evidence.largeHint', { size: formatBytes(item.size, lang) })}
                </Alert>
              ) : null}
              {mine?.busy ? <DownloadProgress state={mine} /> : null}
              {mine?.problem ? <Alert tone="danger">{mine.problem}</Alert> : null}
              {mine?.check ? (
                <div className="flex flex-wrap items-center gap-2 text-13">
                  <span className="text-muted">
                    {t('evidence.detail.saved', { file: mine.filename ?? '' })}
                  </span>
                  <HashCheckBadge check={mine.check} />
                </div>
              ) : null}
            </>
          ) : null}
        </SheetBody>
        {item && can('evidence.download') ? (
          <SheetFooter>
            <div className="flex flex-wrap items-center justify-end gap-3">
              {blocked ? (
                <p id={`${id}-blocked`} className="text-xs text-muted">
                  {t(blocked)}
                </p>
              ) : item.size !== null ? (
                <p className="text-xs text-muted tabular">
                  {t('evidence.downloadSize', { size: formatBytes(item.size, lang) })}
                </p>
              ) : null}
              <Button
                disabled={!item.downloadable}
                busy={Boolean(mine?.busy)}
                aria-describedby={blocked ? `${id}-blocked` : undefined}
                onClick={() => {
                  onDownload(item);
                }}
              >
                <Download aria-hidden="true" />
                {mine?.busy ? t('evidence.downloading') : t('evidence.download')}
              </Button>
            </div>
          </SheetFooter>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
