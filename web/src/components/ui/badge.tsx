import { cva, type VariantProps } from 'class-variance-authority';
import {
  AlertOctagon,
  AlertTriangle,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleX,
  Clock,
  Hourglass,
  Info,
  ShieldAlert,
  ShieldQuestionMark,
  Signal,
  SignalZero,
  Trash2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { HTMLAttributes } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export const badgeVariants = cva(
  'inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      tone: {
        neutral: 'bg-subtle text-foreground',
        primary: 'bg-primary-soft text-primary-ink',
        accent: 'bg-accent-soft text-accent-ink',
        success: 'bg-success-soft text-success-ink',
        warning: 'bg-warning-soft text-warning-ink',
        danger: 'bg-danger-soft text-danger-ink',
        info: 'bg-info-soft text-info-ink',
        outline: 'border border-border text-foreground',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {
  icon?: LucideIcon;
}

export function Badge({ className, tone, icon: Icon, children, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...props}>
      {Icon ? <Icon aria-hidden="true" strokeWidth={2} /> : null}
      {children}
    </span>
  );
}

export type Status =
  | 'sealed'
  | 'unverified'
  | 'pending'
  | 'late'
  | 'failed'
  | 'open'
  | 'deleted'
  | 'noData'
  | 'active'
  | 'silent'
  | 'unknown';

type Tone = NonNullable<BadgeProps['tone']>;

/** Status → tone + icon. Status is never shown by colour alone. */
export const STATUS_STYLE: Record<Status, { tone: Tone; icon: LucideIcon }> = {
  sealed: { tone: 'success', icon: CircleCheck },
  unverified: { tone: 'warning', icon: ShieldQuestionMark },
  pending: { tone: 'warning', icon: Clock },
  late: { tone: 'warning', icon: AlertTriangle },
  failed: { tone: 'danger', icon: CircleX },
  open: { tone: 'neutral', icon: Hourglass },
  deleted: { tone: 'neutral', icon: Trash2 },
  noData: { tone: 'neutral', icon: CircleDashed },
  active: { tone: 'success', icon: Signal },
  silent: { tone: 'danger', icon: SignalZero },
  unknown: { tone: 'neutral', icon: CircleHelp },
};

export function StatusBadge({ status, className }: { status: Status; className?: string }) {
  const { t } = useTranslation();
  const style = STATUS_STYLE[status];
  return (
    <Badge tone={style.tone} icon={style.icon} className={className}>
      {t(`status.${status}`)}
    </Badge>
  );
}

export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';

export const SEVERITY_STYLE: Record<Severity, { tone: Tone; icon: LucideIcon }> = {
  info: { tone: 'info', icon: Info },
  low: { tone: 'neutral', icon: CircleHelp },
  medium: { tone: 'warning', icon: AlertTriangle },
  high: { tone: 'danger', icon: ShieldAlert },
  critical: { tone: 'danger', icon: AlertOctagon },
};

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const { t } = useTranslation();
  const style = SEVERITY_STYLE[severity];
  return (
    <Badge
      tone={style.tone}
      icon={style.icon}
      className={cn(severity === 'critical' && 'font-semibold ring-1 ring-danger', className)}
    >
      {t(`severity.${severity}`)}
    </Badge>
  );
}
