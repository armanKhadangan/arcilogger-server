import {
  Building2,
  CalendarClock,
  ChevronRight,
  DatabaseBackup,
  HardDriveUpload,
  History,
  KeyRound,
  ListChecks,
  Mail,
  Network,
  SlidersHorizontal,
  Stamp,
  Users,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { AgentStatusChip, ConfigProblems } from '@/components/admin/agent-status';
import { LicenseStateBadge } from '@/components/license/license-state-badge';
import { PageHeader } from '@/components/layout/page-header';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useAdminConfig, useAdminStatus, useLicense } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { LICENSE_ADMIN_PERMISSION, LICENSE_PAGE } from '@/lib/license';
import { endWizardMode, WIZARD_PATH } from '@/pages/setup-wizard/wizard-mode';

/** Sections of spec 12.3 page 15 that come later ("Gizlilik" is in Uygulama ayarları). */
const LATER_SECTIONS = ['rules'] as const;

interface Section {
  key: string;
  to: string;
  icon: LucideIcon;
  /** Every one of them is needed. */
  permissions: readonly string[];
}

/** The sections of the browser administration (contract 2.17), in the order of the page. */
const SECTIONS: readonly Section[] = [
  { key: 'general', to: '/settings/general', icon: Building2, permissions: ['settings.manage'] },
  {
    key: 'application',
    to: '/settings/application',
    icon: SlidersHorizontal,
    permissions: ['settings.manage'],
  },
  { key: 'kamusm', to: '/settings/kamusm', icon: Stamp, permissions: ['settings.manage'] },
  {
    key: 'sources',
    to: '/settings/sources',
    icon: Network,
    permissions: ['settings.manage', 'sources.manage'],
  },
  { key: 'email', to: '/settings/email', icon: Mail, permissions: ['settings.manage'] },
  {
    key: 'replica',
    to: '/settings/replica',
    icon: HardDriveUpload,
    permissions: ['settings.manage'],
  },
  {
    key: 'retention',
    to: '/settings/retention',
    icon: CalendarClock,
    permissions: ['settings.manage'],
  },
  { key: 'backup', to: '/settings/backup', icon: DatabaseBackup, permissions: ['settings.manage'] },
  { key: 'history', to: '/settings/history', icon: History, permissions: ['settings.manage'] },
];

function SectionCard({
  to,
  icon: Icon,
  title,
  hint,
  extra,
  testId,
}: {
  to: string;
  icon: LucideIcon;
  title: string;
  hint: ReactNode;
  extra?: ReactNode;
  testId?: string;
}) {
  return (
    <Card className="p-0" data-testid={testId}>
      <Link to={to} className="flex items-center gap-4 rounded-lg p-5 hover:bg-subtle">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary-ink">
          <Icon aria-hidden="true" className="size-5" strokeWidth={1.75} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-sm font-semibold text-foreground">{title}</span>
          <span className="text-13 text-muted-strong">{hint}</span>
          {extra}
        </span>
        <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted" />
      </Link>
    </Card>
  );
}

/** "Lisans": the state badge and the licensee of a verified licence. */
function LicenseCard() {
  const { t } = useTranslation();
  const license = useLicense();
  const status = license.data;
  return (
    <SectionCard
      to={LICENSE_PAGE}
      icon={KeyRound}
      title={t('license.page.title')}
      hint={t('license.page.settingsHint')}
      testId="settings-license"
      extra={
        status ? (
          <span className="mt-1 flex flex-wrap items-center gap-2">
            <LicenseStateBadge state={status.status} />
            {status.licensee ? (
              <span className="truncate text-13 text-foreground">{status.licensee}</span>
            ) : null}
          </span>
        ) : null
      }
    />
  );
}

/**
 * Ayarlar: the sections. "Lisans" and the machine configuration (`settings.manage`),
 * "Kullanıcılar ve Roller" (`users.manage`); the header chip tells whether the administration
 * service that applies machine changes is running. "İlk kurulum sihirbazı" opens the first-run
 * wizard (Wave 4 contract 2.8.4); coming back here ends its wizard mode.
 */
export function SettingsPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const manage = can('settings.manage');
  const status = useAdminStatus(manage);
  // The installation's setup problems (e.g. no local time source), every one of them here.
  const config = useAdminConfig(manage);
  const sections = SECTIONS.filter((section) => section.permissions.every((item) => can(item)));
  useEffect(() => {
    endWizardMode();
  }, []);
  return (
    <div>
      <PageHeader
        title={t('nav.settings')}
        description={t('settings.description')}
        actions={manage ? <AgentStatusChip status={status.data} /> : undefined}
      />
      {manage ? (
        <div className="mb-4 empty:hidden">
          <ConfigProblems problems={config.data?.problems} />
        </div>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {can(LICENSE_ADMIN_PERMISSION) ? <LicenseCard /> : null}
        {can('users.manage') ? (
          <SectionCard
            to="/settings/users"
            icon={Users}
            title={t('users.title')}
            hint={t('settings.usersHint')}
          />
        ) : null}
        {manage ? (
          <SectionCard
            to={WIZARD_PATH}
            icon={ListChecks}
            title={t('setupWizard.title')}
            hint={t('setupWizard.settingsHint')}
            testId="settings-setup"
          />
        ) : null}
        {sections.map((section) => (
          <SectionCard
            key={section.key}
            to={section.to}
            icon={section.icon}
            title={t(`settings.sections.${section.key}`)}
            hint={t(`settings.hints.${section.key}`)}
            testId={`settings-${section.key}`}
          />
        ))}
        <Card className="p-5">
          <h2 className="text-sm font-semibold text-foreground">{t('settings.laterTitle')}</h2>
          <p className="mt-1 text-13 text-muted">{t('settings.laterHint')}</p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {LATER_SECTIONS.map((key) => (
              <li key={key}>
                <Badge tone="outline">{t(`settings.sections.${key}`)}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
