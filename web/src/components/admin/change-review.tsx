import { Clock, ShieldCheck, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';
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
import { changeValueText, isSecretChange, localized } from '@/lib/admin-requests';
import { formatDateTime } from '@/lib/format';
import type { AdminCheck, Change } from '@/lib/settings-types';

import { effectLabel, pathLabel } from './labels';

function SecretCell({ value }: { value: 'set' | 'unset' | 'deleted' }) {
  const { t } = useTranslation();
  return <span className="italic">{t(`admin.review.secret.${value}`)}</span>;
}

function ChangeCells({ change }: { change: Change }) {
  if (isSecretChange(change))
    return (
      <>
        <td className="px-3 py-2">
          <SecretCell value={change.old} />
        </td>
        <td className="px-3 py-2">
          <SecretCell value={change.new === 'set' && change.changed ? 'set' : change.new} />
        </td>
      </>
    );
  return (
    <>
      <td className="px-3 py-2 font-mono text-xs break-all">{changeValueText(change.old)}</td>
      <td className="px-3 py-2 font-mono text-xs break-all">{changeValueText(change.new)}</td>
    </>
  );
}

/** The changes of a check or a request: setting, before, after (secrets only set/unset). */
export function ChangesTable({
  changes,
  caption,
}: {
  changes: Record<string, Change>;
  caption: string;
}) {
  const { t } = useTranslation();
  const entries = Object.entries(changes);
  if (entries.length === 0) return <p className="text-13 text-muted">{t('admin.review.none')}</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-13">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-subtle text-left text-xs text-muted-strong">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">
              {t('admin.review.setting')}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {t('admin.review.old')}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {t('admin.review.new')}
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([path, change]) => (
            <tr key={path} className="border-t border-border align-top">
              <th scope="row" className="px-3 py-2 text-left font-medium">
                {pathLabel(path, t)}
                <span className="block font-mono text-xs font-normal text-muted-strong">
                  {path}
                </span>
              </th>
              <ChangeCells change={change} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * "Değişiklikleri onaylayın" (contract 2.17 state `confirm`): the result of
 * `POST /admin/config/{section}/check` — what changes, which services take it, the warnings,
 * whether it is held and until when, and whether a password confirmation follows. `children`
 * holds a section's own confirmation (the number of days to delete, the source's name).
 */
export function ChangeReviewDialog({
  open,
  onOpenChange,
  check,
  title,
  description,
  confirmLabel,
  onConfirm,
  busy = false,
  danger = false,
  confirmDisabled = false,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  check: AdminCheck | null;
  title?: string;
  description?: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  busy?: boolean;
  danger?: boolean;
  confirmDisabled?: boolean;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title ?? t('admin.review.title')}</DialogTitle>
          <DialogDescription>{description ?? t('admin.review.description')}</DialogDescription>
        </DialogHeader>
        {check ? (
          <div className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto">
            <ChangesTable changes={check.changes} caption={t('admin.review.caption')} />
            {check.effects.length > 0 ? (
              <div className="flex flex-col gap-1">
                <h3 className="text-13 font-semibold text-foreground">
                  {t('admin.review.effects')}
                </h3>
                <ul className="list-disc pl-5 text-13">
                  {check.effects.map((effect) => (
                    <li key={effect}>{effectLabel(effect, t)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {check.warnings.length > 0 ? (
              <Alert tone="warning" assertive={false} title={t('admin.review.warnings')}>
                <ul className="flex flex-col gap-1">
                  {check.warnings.map((warning) => (
                    <li key={warning.code} className="flex items-start gap-1.5">
                      <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                      {localized(warning)}
                    </li>
                  ))}
                </ul>
              </Alert>
            ) : null}
            {check.guarded ? (
              <Alert tone="warning" assertive={false} data-testid="review-held">
                <span className="flex items-start gap-1.5">
                  <Clock aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                  {check.effective_at
                    ? t('admin.review.heldUntil', { time: formatDateTime(check.effective_at) })
                    : t('admin.review.held')}
                </span>
              </Alert>
            ) : null}
            {check.reauth_required ? (
              <p className="flex items-center gap-1.5 text-13 text-muted-strong">
                <ShieldCheck aria-hidden="true" className="size-4 shrink-0" />
                {t('admin.review.reauth')}
              </p>
            ) : null}
            {children}
          </div>
        ) : (
          children
        )}
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
          <Button
            variant={danger ? 'danger' : 'primary'}
            busy={busy}
            disabled={confirmDisabled}
            onClick={onConfirm}
          >
            {busy ? t('common.working') : (confirmLabel ?? t('admin.review.confirm'))}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
