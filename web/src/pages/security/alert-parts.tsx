import { CircleCheck, CircleDot, CircleSlash, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import type { AlertState } from '@/lib/api-types';

const STATE_STYLE: Record<AlertState, { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }> =
  {
    open: { tone: 'danger', icon: CircleDot },
    acknowledged: { tone: 'warning', icon: Search },
    resolved: { tone: 'success', icon: CircleCheck },
    dismissed: { tone: 'neutral', icon: CircleSlash },
  };

/** Yeni, İnceleniyor, Kapandı, Yanlış alarm — icon and text, never colour alone. */
export function AlertStateBadge({ state, className }: { state: AlertState; className?: string }) {
  const { t } = useTranslation();
  const style = STATE_STYLE[state];
  return (
    <Badge tone={style.tone} icon={style.icon} className={className}>
      {t(`alertStates.${state}`)}
    </Badge>
  );
}
