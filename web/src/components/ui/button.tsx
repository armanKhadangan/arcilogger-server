import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, MouseEvent } from 'react';

import { cn } from '@/lib/utils';

export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 rounded-lg font-medium whitespace-nowrap transition-colors select-none disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        secondary: 'border border-border bg-surface text-foreground hover:bg-subtle',
        ghost: 'text-foreground hover:bg-subtle',
        danger: 'bg-danger-solid text-white hover:bg-danger-solid-hover',
        link: 'h-auto px-0 text-accent underline-offset-4 hover:underline',
      },
      size: {
        sm: 'h-8 px-3 text-13',
        md: 'h-9 px-4 text-sm',
        lg: 'h-10 px-5 text-sm',
        icon: 'size-9',
        'icon-sm': 'size-8',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a router link) with button styles. */
  asChild?: boolean;
  /**
   * A request this button started is running. The button looks disabled and ignores clicks,
   * Enter and Space (also the implicit submission of its form), but it is not `disabled`: a
   * focused button that becomes `disabled` drops keyboard focus to `<body>` (no focus ring, the
   * next Enter does nothing; WCAG 2.4.3). Use `disabled` only for a button that is unavailable.
   */
  busy?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, busy = false, onClick, ...props }, ref) => {
    const Component = asChild ? Slot : 'button';
    return (
      <Component
        ref={ref}
        type={asChild ? undefined : (type ?? 'button')}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
        aria-disabled={busy ? true : props['aria-disabled']}
        onClick={
          busy
            ? (event: MouseEvent<HTMLButtonElement>) => {
                // Cancels the click and, for a submit button, the form submission.
                event.preventDefault();
              }
            : onClick
        }
      />
    );
  },
);
Button.displayName = 'Button';
