import type { InputHTMLAttributes, ReactNode } from 'react';

import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';

/** A text field of a settings form: label, hint or error, wired for screen readers. */
export function TextField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  required = false,
  disabled = false,
  mono = false,
  className,
  inputClassName,
  ...input
}: {
  id: string;
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
  error?: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  disabled?: boolean;
  mono?: boolean;
  className?: string;
  inputClassName?: string;
} & Pick<
  InputHTMLAttributes<HTMLInputElement>,
  'inputMode' | 'placeholder' | 'autoComplete' | 'maxLength' | 'type' | 'spellCheck'
>) {
  return (
    <Field
      id={id}
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={className}
    >
      <Input
        id={id}
        value={value}
        disabled={disabled}
        mono={mono}
        className={inputClassName}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        {...input}
      />
    </Field>
  );
}

/** An on/off setting with its label beside the switch and a hint below. */
export function SwitchField({
  id,
  label,
  checked,
  onChange,
  hint,
  disabled = false,
  className,
}: {
  id: string;
  label: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="flex items-center gap-3">
        <Switch
          id={id}
          checked={checked}
          disabled={disabled}
          aria-describedby={hint ? `${id}-hint` : undefined}
          onCheckedChange={onChange}
        />
        <Label htmlFor={id}>{label}</Label>
      </div>
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
