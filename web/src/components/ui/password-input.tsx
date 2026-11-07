import { ArrowBigUpDash, Eye, EyeOff } from 'lucide-react';
import { forwardRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { Input, type InputProps } from './input';

/**
 * A password field with "Parolayı göster" (a pressed toggle, so the label stays the same) and a
 * "Caps Lock açık" line while it is on: a capital-letter password is the most common reason a
 * sign-in fails twice. The line sits in a live region that is always there, so screen readers
 * hear it when it appears.
 */
export const PasswordInput = forwardRef<HTMLInputElement, Omit<InputProps, 'type'>>(
  ({ className, onKeyDown, onKeyUp, onBlur, ...props }, ref) => {
    const { t } = useTranslation();
    const [shown, setShown] = useState(false);
    const [capsLock, setCapsLock] = useState(false);
    const watch = (event: KeyboardEvent<HTMLInputElement>) => {
      setCapsLock(event.getModifierState('CapsLock'));
    };
    return (
      <div className="flex flex-col">
        <div className="relative w-full">
          <Input
            ref={ref}
            type={shown ? 'text' : 'password'}
            className={cn('pr-10', className)}
            onKeyDown={(event) => {
              watch(event);
              onKeyDown?.(event);
            }}
            onKeyUp={(event) => {
              watch(event);
              onKeyUp?.(event);
            }}
            onBlur={(event) => {
              setCapsLock(false);
              onBlur?.(event);
            }}
            {...props}
          />
          <button
            type="button"
            aria-pressed={shown}
            aria-controls={props.id}
            disabled={props.disabled}
            onClick={() => {
              setShown((current) => !current);
            }}
            className="absolute top-1/2 right-1 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted hover:bg-subtle hover:text-foreground disabled:opacity-50 [&_svg]:size-4"
          >
            {shown ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
            {/* Named by its text, not aria-label: "Parola" alone still finds only the field. */}
            <span className="sr-only">{t('password.show')}</span>
          </button>
        </div>
        <p
          role="status"
          className={
            capsLock ? 'mt-1.5 flex items-center gap-1 text-xs text-warning-ink' : 'sr-only'
          }
        >
          {capsLock ? (
            <>
              <ArrowBigUpDash aria-hidden="true" className="size-3.5" />
              {t('password.capsLock')}
            </>
          ) : null}
        </p>
      </div>
    );
  },
);
PasswordInput.displayName = 'PasswordInput';
