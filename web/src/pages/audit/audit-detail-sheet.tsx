import { CircleHelp, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import type { AuditEntry } from '@/lib/api-types';
import { checkEntry, type EntryCheck } from '@/lib/audit-chain';
import { formatDateTime } from '@/lib/format';

import { useActionLabel } from './audit-labels';

/**
 * Why the hash could not be recomputed here (a masked target was not what was hashed; a reader who
 * sees pseudonyms gets no hashes).
 */
const UNAVAILABLE = {
  'no-crypto': 'audit.detail.noCrypto',
  'not-canonical': 'audit.detail.notCanonical',
  masked: 'audit.detail.masked',
  hidden: 'audit.detail.hidden',
} as const;

function Check({ check }: { check: EntryCheck | null }) {
  const { t } = useTranslation();
  if (check === null) return null;
  if (check.state === 'match')
    return (
      <Badge tone="success" icon={ShieldCheck}>
        {t('audit.detail.match')}
      </Badge>
    );
  if (check.state === 'mismatch')
    return (
      <Badge tone="danger" icon={ShieldAlert}>
        {t('audit.detail.mismatch')}
      </Badge>
    );
  return (
    <Badge tone="neutral" icon={CircleHelp}>
      {t(UNAVAILABLE[check.reason])}
    </Badge>
  );
}

/**
 * One audit entry: its fields, the details, the exact canonical JSON that was hashed and both
 * hashes — and the hash recomputed in this browser (`lib/audit-chain.ts`), so a reader can see that
 * the stored hash belongs to the content shown.
 */
export function AuditDetailSheet({
  entry,
  onClose,
}: {
  entry: AuditEntry | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const label = useActionLabel();
  const [check, setCheck] = useState<EntryCheck | null>(null);

  useEffect(() => {
    let current = true;
    setCheck(null);
    if (entry) {
      void checkEntry(entry).then((result) => {
        if (current) setCheck(result);
      });
    }
    return () => {
      current = false;
    };
  }, [entry]);

  const fields: [string, string][] = entry
    ? [
        [t('audit.columns.time'), formatDateTime(entry.time)],
        [t('audit.columns.actor'), entry.actor ?? '—'],
        [t('audit.detail.actorId'), entry.actor_id === null ? '—' : String(entry.actor_id)],
        [t('audit.columns.action'), entry.action],
        [t('audit.columns.target'), entry.target ?? '—'],
        [t('audit.columns.ip'), entry.ip ?? '—'],
      ]
    : [];

  return (
    <Sheet
      open={entry !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent>
        <SheetHeader>
          <SheetTitle className="text-base font-semibold text-foreground">
            {entry ? (label(entry.action) ?? entry.action) : ''}
          </SheetTitle>
          <SheetDescription className="text-13 text-muted">
            {entry ? t('audit.detail.subtitle', { id: entry.id }) : ''}
          </SheetDescription>
        </SheetHeader>
        {entry ? (
          <SheetBody className="flex flex-col gap-5">
            <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
              {fields.map(([name, value]) => (
                <div key={name} className="contents">
                  <dt className="text-muted">{name}</dt>
                  <dd className="font-mono text-xs leading-5 break-all text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
            {entry.target_masked ? (
              <p className="text-13 text-muted">{t('audit.detail.maskedTarget')}</p>
            ) : null}
            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold text-foreground">{t('audit.detail.details')}</h3>
              <CodeBlock
                label={t('audit.detail.details')}
                code={JSON.stringify(entry.details, null, 2)}
                wrap
              />
            </section>
            <section className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">
                  {t('audit.detail.integrity')}
                </h3>
                <Check check={check} />
              </div>
              <p className="text-13 text-muted">{t('audit.detail.formula')}</p>
              {check?.canonical ? (
                <CodeBlock label={t('audit.detail.canonical')} code={check.canonical} wrap />
              ) : null}
              {entry.prev_hash !== null ? (
                <CodeBlock label={t('audit.detail.prevHash')} code={entry.prev_hash} wrap />
              ) : null}
              {entry.hash !== null ? (
                <CodeBlock label={t('audit.detail.hash')} code={entry.hash} wrap />
              ) : null}
              {check?.state === 'mismatch' ? (
                <CodeBlock label={t('audit.detail.computed')} code={check.computed} wrap />
              ) : null}
            </section>
          </SheetBody>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
