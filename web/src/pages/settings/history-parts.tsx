import { FileArchive, KeyRound } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { pathLabel } from '@/components/admin/labels';
import { SettingsCard } from '@/components/admin/settings-frame';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import { Skeleton } from '@/components/ui/skeleton';
import { apiDownload, errorMessage } from '@/lib/api';
import { useConfigVersions } from '@/lib/api-hooks';
import { saveBlob } from '@/lib/download';
import { formatBytes, formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

/** The console command that restores a kept version (contract 2.18: restore stays on the console). */
function restoreCommand(n: number): string {
  return `python -I -m arcilogger_core.admin versions restore ${String(n)}`;
}

/**
 * The kept configurations (`GET /admin/config/versions`, contract 2.11.1): number, time, who,
 * the settings it changed and whether it touched a secret. The last applied change is undone
 * with "Geri al" in its request; an older version is restored on the server console.
 */
export function VersionsCard({ onOpen }: { onOpen: (requestId: string) => void }) {
  const { t } = useTranslation();
  const versions = useConfigVersions();
  const items = versions.data ?? [];
  const newest = items[0]?.n ?? null;
  return (
    <SettingsCard
      title={t('settings.history.versions.title')}
      description={t('settings.history.versions.description')}
      testId="settings-versions"
    >
      {versions.isPending ? (
        <Skeleton className="h-24" />
      ) : versions.isError ? (
        <Alert tone="danger">{errorMessage(versions.error)}</Alert>
      ) : items.length === 0 ? (
        <p className="text-13 text-muted">{t('settings.history.versions.empty')}</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-13">
              <caption className="sr-only">{t('settings.history.versions.caption')}</caption>
              <thead className="bg-subtle text-left text-xs text-muted-strong">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.versions.number')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.time')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.user')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.versions.paths')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('settings.history.columns.request')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((version) => (
                  <tr key={version.n} className="border-t border-border align-top">
                    <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-medium">
                      {version.n}
                      {version.n === newest ? (
                        <Badge tone="success" className="ml-2">
                          {t('settings.history.versions.current')}
                        </Badge>
                      ) : null}
                    </th>
                    <td className="px-3 py-2 tabular">{formatDateTime(version.at)}</td>
                    <td className="px-3 py-2">{version.actor}</td>
                    <td className="px-3 py-2">
                      <span className="flex flex-wrap items-center gap-1">
                        {version.paths.length === 0
                          ? '—'
                          : version.paths.map((path) => pathLabel(path, t)).join(', ')}
                        {version.has_secrets ? (
                          <Badge tone="neutral" icon={KeyRound}>
                            {t('settings.history.versions.secrets')}
                          </Badge>
                        ) : null}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {version.request_id ? (
                        <Button
                          variant="link"
                          size="sm"
                          className="h-auto p-0"
                          aria-label={t('settings.history.versions.openRequest', {
                            number: version.n,
                          })}
                          onClick={() => {
                            if (version.request_id) onOpen(version.request_id);
                          }}
                        >
                          {version.request_id.slice(0, 8)}
                        </Button>
                      ) : (
                        <span className="text-muted">{t('settings.history.versions.console')}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {items.length > 1 ? (
            <div className="flex flex-col gap-2">
              <p className="text-13 text-muted-strong">{t('settings.history.versions.restore')}</p>
              <CodeBlock
                label={t('settings.history.versions.command')}
                code={restoreCommand(items[1]?.n ?? 0)}
              />
            </div>
          ) : null}
        </>
      )}
    </SettingsCard>
  );
}

/**
 * "Tanılama paketi" (`GET /admin/diagnostics`, re-authentication): the status files, the last
 * service log lines with secrets removed, the configuration without secrets and the last
 * requests, as a ZIP for support; no raw log line is in it.
 */
export function DiagnosticsCard() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { withReauth } = useReauth();
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<{ name: string; size: number } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setProblem(null);
    setSaved(null);
    try {
      const file = await withReauth(() =>
        apiDownload('/admin/diagnostics', { fallbackName: 'arcilogger-diagnostics.zip' }),
      );
      saveBlob(file.blob, file.filename);
      setSaved({ name: file.filename, size: file.blob.size });
    } catch (error) {
      if (!isReauthCancelled(error)) setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsCard
      title={t('settings.history.diagnostics.title')}
      description={t('settings.history.diagnostics.description')}
      testId="settings-diagnostics"
    >
      <div>
        <Button variant="secondary" busy={busy} onClick={() => void download()}>
          <FileArchive aria-hidden="true" />
          {t('settings.history.diagnostics.download')}
        </Button>
      </div>
      {saved ? (
        <p role="status" className="text-13 text-foreground" data-testid="diagnostics-saved">
          {t('settings.history.diagnostics.saved', {
            name: saved.name,
            size: formatBytes(saved.size, lang),
          })}
        </p>
      ) : null}
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
    </SettingsCard>
  );
}
