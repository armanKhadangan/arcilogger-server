import { Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export interface Step {
  id: string;
  title: string;
  description?: string;
}

interface StepperProps {
  steps: Step[];
  /** Index of the current step. */
  current: number;
  label: string;
  className?: string;
}

/** Wizard progress (first-run setup, Kamu SM connection test, evidence package). */
export function Stepper({ steps, current, label, className }: StepperProps) {
  const { t } = useTranslation();
  return (
    <ol aria-label={label} className={cn('flex w-full items-start', className)}>
      {steps.map((step, index) => {
        const state = index < current ? 'done' : index === current ? 'current' : 'upcoming';
        return (
          <li
            key={step.id}
            aria-current={state === 'current' ? 'step' : undefined}
            className="relative flex flex-1 flex-col items-center gap-2 text-center"
          >
            {index < steps.length - 1 ? (
              <span
                aria-hidden="true"
                className={cn(
                  'absolute top-4 left-[calc(50%+1.25rem)] h-0.5 w-[calc(100%-2.5rem)] rounded-full',
                  index < current ? 'bg-primary' : 'bg-border',
                )}
              />
            ) : null}
            <span
              className={cn(
                'relative flex size-8 items-center justify-center rounded-full border-2 text-13 font-semibold tabular',
                state === 'done' && 'border-primary bg-primary text-primary-foreground',
                state === 'current' && 'border-primary bg-surface text-primary-ink',
                state === 'upcoming' && 'border-border bg-surface text-muted',
              )}
            >
              {state === 'done' ? (
                <Check aria-hidden="true" className="size-4" strokeWidth={2.5} />
              ) : (
                index + 1
              )}
              {state === 'done' ? <span className="sr-only">{t('common.completed')}</span> : null}
            </span>
            <span className="flex flex-col gap-0.5 px-2">
              <span
                className={cn(
                  'text-13 font-medium',
                  state === 'upcoming' ? 'text-muted' : 'text-foreground',
                )}
              >
                {step.title}
              </span>
              {step.description ? (
                <span className="text-xs text-muted">{step.description}</span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
