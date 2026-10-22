import { Children, cloneElement, isValidElement } from 'react';
import type { LabelHTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn('text-13 font-medium text-foreground', className)} {...props} />;
}

interface FieldProps {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * A required field's control (the child whose `id` is the field's) gets `aria-required`, so
 * screen readers announce it — the visible asterisk is hidden from them. Not the native
 * `required`: the forms validate themselves (`noValidate`, Turkish messages). A control whose id
 * sits deeper (a Radix Select's trigger) sets `aria-required` itself.
 */
function markRequired(children: ReactNode, id: string): ReactNode {
  return Children.map(children, (child) =>
    isValidElement<{ id?: string; 'aria-required'?: boolean }>(child) && child.props.id === id
      ? cloneElement(child, { 'aria-required': true })
      : child,
  );
}

/** Label + control + hint/error, wired with ids so screen readers announce all of it. */
export function Field({ id, label, hint, error, required, children, className }: FieldProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {/* The asterisk stays outside the <label>: the field's accessible name is the label alone. */}
      <span className="flex items-baseline">
        <Label htmlFor={id}>{label}</Label>
        {required ? (
          <span aria-hidden="true" className="ml-0.5 text-13 text-danger">
            *
          </span>
        ) : null}
      </span>
      {required ? markRequired(children, id) : children}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger-ink">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
