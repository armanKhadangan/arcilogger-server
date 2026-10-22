import {
  CalendarX,
  Clock,
  LoaderCircle,
  PackageCheck,
  PackageX,
  Trash2,
  type LucideIcon,
} from 'lucide-react';

import { useTranslation } from 'react-i18next';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import type { EvidenceOut } from '@/lib/api-types';
import { currentLang } from '@/lib/i18n';

import { isExpiredUnpurged } from './evidence-model';

const STATUS: Record<string, { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }> = {
  queued: { tone: 'neutral', icon: Clock },
  building: { tone: 'info', icon: LoaderCircle },
  ready: { tone: 'success', icon: PackageCheck },
  failed: { tone: 'danger', icon: PackageX },
  expired: { tone: 'neutral', icon: CalendarX },
  deleted: { tone: 'neutral', icon: Trash2 },
};

/**
 * Package state with icon and the server's label (never colour alone). A `ready` package past its
 * expiry that the nightly purge has not marked yet is shown as expired ("Süresi doldu").
 */
export function PackageStatus({
  item,
}: {
  item: Pick<EvidenceOut, 'status' | 'status_label_tr' | 'status_label_en' | 'downloadable'>;
}) {
  const { t } = useTranslation();
  const expired = isExpiredUnpurged(item);
  const style = STATUS[expired ? 'expired' : item.status] ?? {
    tone: 'neutral' as const,
    icon: Clock,
  };
  const label = expired
    ? t('evidence.status.expired')
    : currentLang() === 'en'
      ? item.status_label_en
      : item.status_label_tr;
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {label}
    </Badge>
  );
}
