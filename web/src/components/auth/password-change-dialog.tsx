import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/components/ui/toast';
import { reactGlobally } from '@/lib/api';

import { PasswordChangeForm } from './password-change-form';

interface PasswordChangeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Focus target after closing (the menu item that opened it is gone by then). */
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * "Parolayı değiştir" (user menu): the signed-in user's own password, `POST /auth/password`. The
 * current password confirms the change (no separate re-authentication); the server ends the
 * user's other sessions and renews this one. A 401 (too many wrong current passwords lock the
 * account and end the session) goes to the sign-in page with its reason, like any request.
 */
export function PasswordChangeDialog({
  open,
  onOpenChange,
  returnFocus,
}: PasswordChangeDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle>{t('password.changeTitle')}</DialogTitle>
          <DialogDescription>{t('password.changeDescription')}</DialogDescription>
        </DialogHeader>
        <PasswordChangeForm
          onSessionLost={reactGlobally}
          onChanged={() => {
            toast({ title: t('password.changed'), variant: 'success' });
            onOpenChange(false);
          }}
          onCancel={() => {
            onOpenChange(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
