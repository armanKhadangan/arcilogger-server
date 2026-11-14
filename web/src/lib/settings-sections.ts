import {
  Building2,
  CalendarClock,
  DatabaseBackup,
  HardDriveUpload,
  History,
  Mail,
  Network,
  SlidersHorizontal,
  Webhook,
  Stamp,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface SettingsSection {
  /** i18n key under "settings.sections." (title) and "settings.hints." (one line). */
  key: string;
  to: string;
  icon: LucideIcon;
  /** Every one of them is needed. */
  permissions: readonly string[];
}

/**
 * The sections of the browser administration (contract 2.17), in the order of the Ayarlar page.
 * The "Sayfaya git" palette lists them too, so both offer the same sections to the same roles.
 */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
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
  // Read-only (D4: configured on the server); "Test gönder" needs `settings.manage`.
  { key: 'webhooks', to: '/settings/webhooks', icon: Webhook, permissions: ['alerts.read'] },
  { key: 'history', to: '/settings/history', icon: History, permissions: ['settings.manage'] },
];
