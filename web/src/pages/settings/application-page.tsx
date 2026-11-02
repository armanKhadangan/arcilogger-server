import { RefreshCw } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { SettingsFrame, UnsavedChangesGuard } from '@/components/admin/settings-frame';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { errorMessage } from '@/lib/api';
import { useSettings } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { currentLang } from '@/lib/i18n';
import type { SettingGroup } from '@/lib/settings-types';

import { LeaseRebuild } from './lease-rebuild';
import { SettingsGroupForm } from './settings-group-form';

/**
 * Ayarlar → Uygulama ayarları (contract 2.13, 2.17; `settings.manage`): every database setting
 * the API lists, by group (alerts, 5651 lookup and lease timeline, usage with the work hours and
 * the holidays, reports, evidence packages, sessions and privacy, database), with the server's
 * Turkish and English labels, help texts and bounds. A group is saved as a whole; nothing here
 * needs the administration service or a restart. The lookup group also rebuilds the lease
 * timeline of past days ("Kiraları yeniden oluştur").
 */
export function ApplicationPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const settings = useSettings();
  const [params, setParams] = useSearchParams();
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const groups: SettingGroup[] = settings.data?.groups ?? [];
  const requested = params.get('group');
  const active =
    requested !== null && groups.some((group) => group.id === requested)
      ? requested
      : (groups[0]?.id ?? '');
  const markDirty = useCallback((group: string, value: boolean) => {
    setDirty((current) => (current[group] === value ? current : { ...current, [group]: value }));
  }, []);
  const anyDirty = Object.values(dirty).some(Boolean);

  return (
    <SettingsFrame
      title={t('settings.application.title')}
      description={t('settings.application.description')}
      machine={false}
    >
      <UnsavedChangesGuard when={anyDirty} />
      {settings.isPending ? (
        <PageSkeleton />
      ) : settings.isError ? (
        <Alert tone="danger" title={t('settings.application.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(settings.error)}
            <Button size="sm" variant="secondary" onClick={() => void settings.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : (
        <Tabs
          value={active}
          onValueChange={(value) => {
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                next.set('group', value);
                return next;
              },
              { replace: true },
            );
          }}
        >
          <TabsList aria-label={t('settings.application.groups')}>
            {groups.map((group) => (
              <TabsTrigger key={group.id} value={group.id}>
                {lang === 'en' ? group.label_en : group.label_tr}
                {dirty[group.id] ? (
                  <span className="text-warning-ink" aria-label={t('settings.application.unsaved')}>
                    •
                  </span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
          {groups.map((group) => (
            // Every group stays mounted: switching the tab keeps what was typed.
            <TabsContent
              key={group.id}
              value={group.id}
              forceMount
              className="data-[state=inactive]:hidden"
            >
              <SettingsGroupForm
                title={lang === 'en' ? group.label_en : group.label_tr}
                settings={group.settings}
                canEdit={can('settings.manage')}
                onDirty={(value) => {
                  markDirty(group.id, value);
                }}
                testId={`settings-group-${group.id}`}
                footer={
                  group.id === 'lookup' ? (
                    <LeaseRebuild canEdit={can('settings.manage')} />
                  ) : undefined
                }
              />
            </TabsContent>
          ))}
        </Tabs>
      )}
    </SettingsFrame>
  );
}
