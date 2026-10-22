import * as ToastPrimitive from '@radix-ui/react-toast';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export type ToastVariant = 'info' | 'success' | 'warning' | 'danger';

export interface ToastMessage {
  id: number;
  title: ReactNode;
  description?: ReactNode;
  variant: ToastVariant;
}

let nextId = 1;
let messages: ToastMessage[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** Show a toast from anywhere (no hook needed). Returns its id. */
export function toast(message: Omit<ToastMessage, 'id' | 'variant'> & { variant?: ToastVariant }) {
  const id = nextId++;
  messages = [...messages.slice(-4), { variant: 'info', ...message, id }];
  emit();
  return id;
}

export function dismissToast(id: number): void {
  messages = messages.filter((message) => message.id !== id);
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const ICONS = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
} as const;
const TONES: Record<ToastVariant, string> = {
  info: 'text-info',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
};

/** Mount once near the root of the app. */
export function Toaster() {
  const { t } = useTranslation();
  const current = useSyncExternalStore(subscribe, () => messages);
  return (
    <ToastPrimitive.Provider swipeDirection="right" duration={6000} label={t('toast.region')}>
      {current.map((message) => {
        const Icon = ICONS[message.variant];
        return (
          <ToastPrimitive.Root
            key={message.id}
            onOpenChange={(open) => {
              if (!open) dismissToast(message.id);
            }}
            className="flex w-full animate-slide-in-right items-start gap-3 rounded-lg border border-border bg-surface p-4 shadow-sm"
          >
            <Icon
              aria-hidden="true"
              className={cn('mt-0.5 size-4 shrink-0', TONES[message.variant])}
            />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <ToastPrimitive.Title className="text-sm font-medium text-foreground">
                {message.title}
              </ToastPrimitive.Title>
              {message.description ? (
                <ToastPrimitive.Description className="text-13 text-muted">
                  {message.description}
                </ToastPrimitive.Description>
              ) : null}
            </div>
            <ToastPrimitive.Close
              aria-label={t('common.close')}
              className="rounded-md p-0.5 text-muted hover:bg-subtle hover:text-foreground"
            >
              <X aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        );
      })}
      <ToastPrimitive.Viewport className="fixed right-0 bottom-0 z-[60] flex w-full max-w-sm flex-col gap-2 p-4 outline-none" />
    </ToastPrimitive.Provider>
  );
}
