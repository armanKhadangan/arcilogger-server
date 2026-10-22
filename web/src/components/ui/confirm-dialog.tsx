import type { ReactNode, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from './alert';
import { Button } from './button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './dialog';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description: ReactNode;
  confirmLabel: ReactNode;
  onConfirm: () => void;
  /** Destructive action: red confirm button. */
  danger?: boolean;
  busy?: boolean;
  /** Error of the last attempt, shown inside the dialog. */
  error?: string | null;
  /** Extra content between the text and the buttons (e.g. a select). */
  children?: ReactNode;
  /** Focus target after closing when the opener is gone (e.g. the menu's trigger button). */
  returnFocus?: RefObject<HTMLElement | null>;
}

/** "Are you sure?" for actions that change accounts; the focus starts on "İptal". */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  danger = false,
  busy = false,
  error = null,
  children,
  returnFocus,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {children}
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <DialogFooter>
          <Button
            variant="secondary"
            autoFocus
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t('common.cancel')}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} busy={busy} onClick={onConfirm}>
            {busy ? t('common.working') : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
