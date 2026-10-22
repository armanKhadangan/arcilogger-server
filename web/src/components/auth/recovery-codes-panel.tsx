import { Copy, Download } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/toast';
import { downloadText } from '@/lib/csv';
import { recoveryCodesFile, recoveryCodesFileName } from '@/lib/recovery-codes';

interface RecoveryCodesPanelProps {
  codes: readonly string[];
  /** Account name written into the downloaded file. */
  account: string;
  onContinue: () => void;
  continueLabel?: string;
  busy?: boolean;
}

/**
 * The recovery codes, shown exactly once (spec 13): copy, download as text, and an explicit
 * "I saved them" confirmation before the user may continue. The codes live only in the state of
 * the screen that shows them.
 */
export function RecoveryCodesPanel({
  codes,
  account,
  onContinue,
  continueLabel,
  busy = false,
}: RecoveryCodesPanelProps) {
  const { t } = useTranslation();
  const id = useId();
  const [saved, setSaved] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join('\n'));
      toast({ title: t('common.copied'), variant: 'success' });
    } catch {
      toast({ title: t('common.copyFailed'), variant: 'danger' });
    }
  }

  function download() {
    downloadText(
      recoveryCodesFileName(account),
      recoveryCodesFile(codes, account, {
        heading: t('recovery.fileHeading'),
        account: t('recovery.fileAccount'),
        created: t('recovery.fileCreated'),
      }),
      'text/plain;charset=utf-8',
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Alert tone="warning" title={t('recovery.onceTitle')} assertive={false}>
        {t('recovery.onceBody')}
      </Alert>
      <ol
        aria-label={t('recovery.listLabel')}
        className="grid grid-cols-1 gap-x-6 gap-y-1.5 rounded-lg border border-border bg-subtle px-4 py-3 font-mono text-13 sm:grid-cols-2"
      >
        {codes.map((code, index) => (
          <li key={code} className="flex gap-2 tabular">
            <span aria-hidden="true" className="w-5 text-right text-muted-strong">
              {index + 1}.
            </span>
            <span className="text-foreground">{code}</span>
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={() => void copy()}>
          <Copy aria-hidden="true" />
          {t('recovery.copy')}
        </Button>
        <Button variant="secondary" size="sm" onClick={download}>
          <Download aria-hidden="true" />
          {t('recovery.download')}
        </Button>
      </div>
      <div className="flex items-start gap-2">
        <Checkbox
          id={`${id}-saved`}
          checked={saved}
          onCheckedChange={(value) => {
            setSaved(value === true);
          }}
          className="mt-0.5"
        />
        <Label htmlFor={`${id}-saved`} className="font-normal">
          {t('recovery.savedConfirm')}
        </Label>
      </div>
      <Button onClick={onContinue} disabled={!saved} busy={busy} className="self-end">
        {busy ? t('common.working') : (continueLabel ?? t('common.continue'))}
      </Button>
    </div>
  );
}
