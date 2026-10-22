import { ChevronRight, KeyRound, Users } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { LicenseStateBadge } from '@/components/license/license-state-badge';
import { PageHeader } from '@/components/layout/page-header';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useLicense } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { LICENSE_ADMIN_PERMISSION, LICENSE_PAGE } from '@/lib/license';

/** Sections of spec 12.3 page 15 that arrive with milestone M6 ("Hakkında" is on the licence page). */
const LATER_SECTIONS = [
  'general',
  'sources',
  'kamusm',
  'sealing',
  'retention',
  'replica',
  'email',
  'usage',
  'rules',
  'privacy',
  'backup',
] as const;

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
 * Ayarlar: the sections. "Lisans" (`settings.manage`) and "Kullanıcılar ve Roller"
 * (`users.manage`) are available now.
 */
export function SettingsPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  return (
    <div>
      <PageHeader title={t('nav.settings')} description={t('settings.description')} />
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
