import { useEffect, useState } from 'react';
import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { errorMessage } from '@/lib/api';
import { useRegenerateRecoveryCodes } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { RecoveryCodesPanel } from './recovery-codes-panel';

interface RecoveryCodesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Focus target after closing (the menu item that opened it is gone by then). */
  returnFocus?: RefObject<HTMLElement | null>;
}

/**
 * "Kurtarma kodlarını yenile" (user menu): `POST /auth/recovery-codes` after a re-authentication;
 * the old codes stop working and the new ones are shown once.
 */
export function RecoveryCodesDialog({ open, onOpenChange, returnFocus }: RecoveryCodesDialogProps) {
  const { t } = useTranslation();
  const { me } = useAuth();
  const { withReauth } = useReauth();
  const regenerate = useRegenerateRecoveryCodes();
  const [codes, setCodes] = useState<string[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setCodes(null);
      setFailure(null);
    }
  }, [open]);

  async function create() {
    setFailure(null);
    try {
      const result = await withReauth(() => regenerate.mutateAsync());
      setCodes(result.recovery_codes);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" returnFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle>{t('recovery.regenerateTitle')}</DialogTitle>
          <DialogDescription>
            {codes
              ? t('recovery.description')
              : t('recovery.regenerateBody', { count: me.mfa.recovery_codes_left })}
          </DialogDescription>
        </DialogHeader>
        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        {codes ? (
          <RecoveryCodesPanel
            codes={codes}
            account={me.user.username}
            continueLabel={t('common.close')}
            onContinue={() => {
              onOpenChange(false);
            }}
          />
        ) : (
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button onClick={() => void create()} busy={regenerate.isPending}>
              {regenerate.isPending ? t('common.working') : t('recovery.regenerate')}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
