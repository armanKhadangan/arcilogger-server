import { forwardRef } from 'react';
import type { ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

interface SwitchProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** A request this switch started is running: it ignores clicks but keeps the focus. */
  busy?: boolean;
}

/**
 * On/off toggle (`role="switch"`), e.g. a schedule's "Etkin". Its state is in its accessible name's
 * companion `aria-checked`, and visibly in the thumb's position and the track's fill.
 */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, onCheckedChange, busy = false, className, disabled, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-disabled={busy ? true : undefined}
      disabled={disabled}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:opacity-60',
        checked ? 'border-primary bg-primary' : 'border-muted bg-subtle',
        className,
      )}
      onClick={() => {
        if (!busy) onCheckedChange(!checked);
      }}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          'pointer-events-none block size-3.5 rounded-full shadow-sm transition-transform',
          checked ? 'translate-x-[18px] bg-primary-foreground' : 'translate-x-[2px] bg-muted',
        )}
      />
    </button>
  ),
);
Switch.displayName = 'Switch';
