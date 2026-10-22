import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react';
import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

export type AlertTone = 'info' | 'success' | 'warning' | 'danger';

const STYLE: Record<AlertTone, { box: string; icon: typeof Info }> = {
  info: { box: 'border-info/30 bg-info-soft text-info-ink', icon: Info },
  success: { box: 'border-success/30 bg-success-soft text-success-ink', icon: CheckCircle2 },
  warning: { box: 'border-warning/30 bg-warning-soft text-warning-ink', icon: AlertTriangle },
  danger: { box: 'border-danger/30 bg-danger-soft text-danger-ink', icon: XCircle },
};

interface AlertProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  tone?: AlertTone;
  title?: ReactNode;
  /** Announce immediately (errors); polite status otherwise. */
  assertive?: boolean;
}

/**
 * Inline message inside a form or page: icon + text, never colour alone. Errors use
 * `role="alert"` so screen readers announce them when they appear.
 */
export function Alert({
  tone = 'info',
  title,
  assertive = tone === 'danger',
  className,
  children,
  ...props
}: AlertProps) {
  const style = STYLE[tone];
  const Icon = style.icon;
  return (
    <div
      role={assertive ? 'alert' : 'status'}
      className={cn('flex gap-2.5 rounded-lg border px-3 py-2.5 text-13', style.box, className)}
      {...props}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="flex min-w-0 flex-col gap-0.5">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className="min-w-0 break-words">{children}</div> : null}
      </div>
    </div>
  );
}
