import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { forwardRef } from 'react';
import type { ComponentPropsWithoutRef, ElementRef, HTMLAttributes } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { DialogOverlay } from './dialog';
import { useReturnFocus, type ReturnFocusProps } from './focus-return';

/** Right-side detail drawer (row details: normalised fields, raw line, evidence links). */
export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

/** The drawer. On close the focus goes back to what opened it (a table row, `focus-return.tsx`). */
export const SheetContent = forwardRef<
  ElementRef<typeof DialogPrimitive.Content>,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & ReturnFocusProps
>(({ className, children, returnFocus, onCloseAutoFocus, ...props }, ref) => {
  const { t } = useTranslation();
  const focus = useReturnFocus(returnFocus, onCloseAutoFocus);
  return (
    <DialogPrimitive.Portal>
      <DialogOverlay className="bg-slate-950/30" />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          'fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-xl animate-slide-in-right flex-col border-l border-border bg-surface shadow-sm',
          className,
        )}
        onCloseAutoFocus={focus.onCloseAutoFocus}
        {...props}
      >
        {focus.origin}
        {children}
        <DialogPrimitive.Close
          className="absolute top-4 right-4 rounded-md p-1 text-muted hover:bg-subtle hover:text-foreground"
          aria-label={t('common.close')}
        >
          <X aria-hidden="true" className="size-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});
SheetContent.displayName = 'SheetContent';

export function SheetHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex flex-col gap-1 border-b border-border px-6 py-4 pr-12', className)}
      {...props}
    />
  );
}

export function SheetBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex-1 overflow-y-auto px-6 py-4', className)} {...props} />;
}

export function SheetFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('flex justify-end gap-2 border-t border-border px-6 py-3', className)}
      {...props}
    />
  );
}

export const SheetTitle = DialogPrimitive.Title;
export const SheetDescription = DialogPrimitive.Description;
