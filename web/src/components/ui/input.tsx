import { forwardRef } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

const inputClass =
  'h-9 w-full min-w-0 rounded-lg border border-border bg-surface px-3 text-sm text-foreground placeholder:text-muted disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Icon shown inside the field on the left (decorative). */
  leadingIcon?: ReactNode;
  /** Use the monospace font (IP addresses, MACs, hashes). */
  mono?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, leadingIcon, mono = false, type = 'text', ...props }, ref) => {
    const input = (
      <input
        ref={ref}
        type={type}
        className={cn(inputClass, mono && 'font-mono text-13', leadingIcon && 'pl-9', className)}
        {...props}
      />
    );
    if (!leadingIcon) return input;
    return (
      <div className="relative w-full">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted [&_svg]:size-4"
        >
          {leadingIcon}
        </span>
        {input}
      </div>
    );
  },
);
Input.displayName = 'Input';
