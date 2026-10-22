import { ShieldAlert } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import { useAcceptPrivacyNotice } from '@/lib/api-hooks';
import { useAuth, useSignOut } from '@/lib/auth';

/** Paragraph keys of the notice text (`privacy.body.*`), in order. */
const PARAGRAPHS = ['data', 'purpose', 'access', 'audit', 'storage'] as const;

/**
 * KVKK notice (spec 3.3), shown once per user and notice version to roles that see person-level
 * data, until `POST /auth/privacy-notice` records the acceptance (audited as `privacy.accept`).
 * It cannot be dismissed: the user accepts or signs out. The server enforces the same rule on
 * every person-level endpoint (403 `privacy_notice_required`).
 *
 * The text is a DRAFT ("taslak — hukuk müşaviri onayı gerekir") until legal counsel approved it;
 * a new approved text needs a new `privacy.notice_version`, which asks everyone again.
 */
export function PrivacyNoticeDialog() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const signOut = useSignOut();
  const accept = useAcceptPrivacyNotice();
  const id = useId();
  const [read, setRead] = useState(false);
  const notice = me.privacy_notice;
  const open = notice.required && !notice.accepted;

  return (
    <Dialog open={open}>
      <DialogContent
        hideClose
        className="max-w-2xl"
        onEscapeKeyDown={(event) => {
          event.preventDefault();
        }}
        onPointerDownOutside={(event) => {
          event.preventDefault();
        }}
        onInteractOutside={(event) => {
          event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2">
            <ShieldAlert aria-hidden="true" className="size-5 text-primary" />
            {t('privacy.title')}
            <Badge tone="warning">{t('privacy.draft')}</Badge>
          </DialogTitle>
          <DialogDescription>{t('privacy.intro')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3 text-13 text-foreground">
          {PARAGRAPHS.map((key) => (
            <p key={key}>{t(`privacy.body.${key}`)}</p>
          ))}
          <p className="text-xs text-muted">{t('privacy.version', { version: notice.version })}</p>
        </div>
        {accept.isError ? <Alert tone="danger">{errorMessage(accept.error)}</Alert> : null}
        <div className="flex items-start gap-2">
          <Checkbox
            id={`${id}-read`}
            checked={read}
            onCheckedChange={(value) => {
              setRead(value === true);
            }}
            className="mt-0.5"
          />
          <Label htmlFor={`${id}-read`} className="font-normal">
            {t('privacy.readConfirm')}
          </Label>
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={() => void signOut()}>
            {t('topbar.logout')}
          </Button>
          <Button
            disabled={!read}
            busy={accept.isPending}
            onClick={() => {
              accept.mutate(notice.version);
            }}
          >
            {accept.isPending ? t('common.working') : t('privacy.accept')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
