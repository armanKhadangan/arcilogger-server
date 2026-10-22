import { Building2, FileSpreadsheet, MonitorSmartphone, Users } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';

import { DepartmentsTab } from './departments-tab';
import { DevicesTab } from './devices-tab';
import { tabFrom, withTab, type DevicesTab as Tab } from './devices-model';
import { PersonsTab } from './persons-tab';
import { TransferTab } from './transfer-tab';

const TAB_ICONS = {
  devices: MonitorSmartphone,
  persons: Users,
  departments: Building2,
  transfer: FileSpreadsheet,
} as const;

/**
 * Cihazlar ve Kişiler (spec 9.2, 12.3 item 11; `devices.read`, changes `devices.manage`): the
 * devices the network has shown with their owners and the assignment history the 5651 lookup
 * relies on, persons, departments and the VLAN → department mapping, CSV import and export.
 * The tab is in the address (`?tab=persons`), like the device list's filters.
 *
 * Owners' names are person-level data: while the KVKK notice is not accepted (the shell shows
 * it), the device, person and export tabs wait instead of asking the API (which would refuse
 * with 403 `privacy_notice_required`).
 */
export function DevicesPage() {
  const { t } = useTranslation();
  const { me, can } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = tabFrom(params);
  const manage = can('devices.manage');
  const privacyPending = me.privacy_notice.required && !me.privacy_notice.accepted;

  return (
    <div className="flex flex-col">
      <PageHeader title={t('nav.devices')} description={t('devices.description')} />
      {!manage ? (
        <Alert tone="info" assertive={false} className="mb-4" data-testid="read-only">
          {t('devices.readOnly')}
        </Alert>
      ) : null}
      <Tabs
        value={tab}
        onValueChange={(value) => {
          setParams((current) => withTab(current, value as Tab), { replace: true });
        }}
      >
        <TabsList aria-label={t('devices.tabs.label')}>
          {(Object.keys(TAB_ICONS) as Tab[]).map((key) => {
            const Icon = TAB_ICONS[key];
            return (
              <TabsTrigger key={key} value={key}>
                <Icon aria-hidden="true" />
                {t(`devices.tabs.${key}`)}
              </TabsTrigger>
            );
          })}
        </TabsList>
        <TabsContent value="devices">
          <DevicesTab manage={manage} privacyPending={privacyPending} />
        </TabsContent>
        <TabsContent value="persons">
          <PersonsTab manage={manage} privacyPending={privacyPending} />
        </TabsContent>
        <TabsContent value="departments">
          <DepartmentsTab manage={manage} />
        </TabsContent>
        <TabsContent value="transfer">
          <TransferTab manage={manage} privacyPending={privacyPending} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
