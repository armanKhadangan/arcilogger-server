import {
  ArchiveX,
  BadgeCheck,
  CalendarX,
  CircleSlash,
  Hourglass,
  ServerOff,
  ShieldX,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import type { LicenseState } from '@/lib/api-types';
import { stateTone } from '@/lib/license';

const ICONS: Record<LicenseState, LucideIcon> = {
  trial: Hourglass,
  trial_expired: CircleSlash,
  licensed: BadgeCheck,
  invalid: ShieldX,
  wrong_server: ServerOff,
  expired: CalendarX,
  version_not_covered: ArchiveX,
};

/** The licence state as a badge (icon + text, never colour alone). */
export function LicenseStateBadge({ state }: { state: LicenseState }) {
  const { t } = useTranslation();
  return (
    <Badge tone={stateTone(state)} icon={ICONS[state]} data-testid="license-state">
      {t(`license.page.states.${state}`)}
    </Badge>
  );
}
