import { HardDrive, TrendingUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '@/lib/auth';
import { currentLang } from '@/lib/i18n';
import type { SystemDisk } from '@/lib/system-types';
import { DriveMeter } from '@/pages/system/drive-meter';
import { forecastText } from '@/pages/system/system-model';

import { linkTarget } from './dashboard-model';
import { DashCard } from './dashboard-parts';

/**
 * "Disk" on the Panel (`GET /system/disk`, `system.read`; Wave 4 contract 2.8.3): each drive's
 * used share with the level as icon and text, its forecast in one line, and "Sistem sayfasını
 * aç" when the user may open Sistem (the Panel's link rule, `linkTarget`).
 */
export function DiskCard({ disk }: { disk: SystemDisk }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { permissions } = useAuth();
  return (
    <DashCard
      title={t('system.diskCard.title')}
      icon={HardDrive}
      to={linkTarget('system', permissions)}
      page={t('nav.system')}
      testId="card-disk"
    >
      {disk.drives.length === 0 ? (
        <p className="text-13 text-muted">{t('system.drive.noDrives')}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {disk.drives.map((drive) => (
            <li key={drive.path} className="flex flex-col gap-1.5">
              <DriveMeter drive={drive} compact />
              <p
                className="flex items-start gap-1.5 text-xs text-muted-strong"
                data-testid="disk-card-forecast"
              >
                <TrendingUp aria-hidden="true" className="mt-px size-3.5 shrink-0" />
                {forecastText(drive, disk.checked_at, t, lang)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </DashCard>
  );
}
