import { ArrowDownRight, ArrowRight, ArrowUpRight, Info } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { HTMLAttributes, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Sparkline } from '@/components/charts/sparkline';
import { cn } from '@/lib/utils';

import { SimpleTooltip } from './tooltip';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-lg border border-border bg-surface text-foreground', className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('flex items-start justify-between gap-3 px-5 pt-4', className)} {...props} />
  );
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-sm font-semibold text-foreground', className)} {...props} />;
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-13 text-muted', className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 py-4', className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 border-t border-border px-5 py-3 text-13 text-muted',
        className,
      )}
      {...props}
    />
  );
}

export interface KpiDelta {
  /** Already formatted, signed text, e.g. "+12 %". */
  text: string;
  direction: 'up' | 'down' | 'flat';
  /** Whether "up" is good for this metric (more sealed days) or bad (more alerts). */
  upIsGood?: boolean;
}

interface KpiCardProps {
  label: string;
  value: ReactNode;
  /** Secondary line under the value. */
  hint?: ReactNode;
  icon?: LucideIcon;
  delta?: KpiDelta;
  /** Recent values, oldest first (about 12 points). */
  trend?: number[];
  trendLabel?: string;
  formatTrend?: (value: number) => string;
  /** Shows the "estimated value" info icon; a text replaces its default explanation. */
  estimate?: boolean | string;
  status?: ReactNode;
  className?: string;
}

function deltaTone(delta: KpiDelta): string {
  if (delta.direction === 'flat') return 'text-muted';
  const good = (delta.direction === 'up') === (delta.upIsGood ?? true);
  return good ? 'text-success-ink' : 'text-danger-ink';
}

/** Stat tile: label · value · optional delta · optional sparkline. */
export function KpiCard({
  label,
  value,
  hint,
  icon: Icon,
  delta,
  trend,
  trendLabel,
  formatTrend,
  estimate = false,
  status,
  className,
}: KpiCardProps) {
  const { t } = useTranslation();
  const DeltaIcon =
    delta?.direction === 'up'
      ? ArrowUpRight
      : delta?.direction === 'down'
        ? ArrowDownRight
        : ArrowRight;
  return (
    <Card className={cn('flex flex-col gap-2 p-4', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-13 font-medium whitespace-nowrap text-muted">
          {Icon ? <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} /> : null}
          {label}
          {estimate ? (
            <SimpleTooltip content={typeof estimate === 'string' ? estimate : t('common.estimate')}>
              <button
                type="button"
                className="rounded-sm text-muted"
                aria-label={typeof estimate === 'string' ? estimate : t('common.estimate')}
              >
                <Info aria-hidden="true" className="size-3.5" />
              </button>
            </SimpleTooltip>
          ) : null}
        </span>
        {status}
      </div>
      <span className="text-2xl leading-8 font-semibold text-foreground tabular">{value}</span>
      <div className="flex items-end justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          {hint ? <span className="truncate text-13 text-muted">{hint}</span> : null}
          {delta ? (
            <span className={cn('flex items-center gap-1 text-xs font-medium', deltaTone(delta))}>
              <DeltaIcon aria-hidden="true" className="size-3.5" />
              {delta.text}
              <span className="font-normal text-muted">{t('common.vsPrevious')}</span>
            </span>
          ) : null}
        </div>
        {trend && trend.length > 1 ? (
          <Sparkline
            values={trend}
            label={trendLabel ?? label}
            className="h-8 w-24 shrink-0"
            {...(formatTrend ? { formatValue: formatTrend } : {})}
          />
        ) : null}
      </div>
    </Card>
  );
}
