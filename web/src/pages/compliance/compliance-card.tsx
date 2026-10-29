import { ArrowRight, BookOpen, DatabaseBackup, MapPinCheck, ShieldAlert } from 'lucide-react';
import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import type { ComplianceCard } from '@/lib/compliance-types';
import { visibleText } from '@/lib/display';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled } from '@/lib/reauth';

import { cardTexts, TONE_STYLE, type CardLinks } from './compliance-model';

/** A card's state: the tone's icon and text ("Tamam", "Dikkat", "Sorun", "Bilgi yok"). */
export function ComplianceBadge({ tone }: { tone: ComplianceCard['tone'] }) {
  const { t } = useTranslation();
  const style = TONE_STYLE[tone];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(style.label)}
    </Badge>
  );
}

/**
 * "Yapılandırma yedeğini aldım" with its confirmation (contract 2.3.5): the dialog closes once the
 * server has written the audit entry and shows the error of a refused attempt.
 */
function ConfirmBackup({
  onConfirm,
  confirming,
}: {
  onConfirm: () => Promise<unknown>;
  confirming: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await onConfirm();
      setOpen(false);
      toast({ title: t('compliance.backupConfirmed'), variant: 'success' });
    } catch (caught) {
      if (!isReauthCancelled(caught)) setError(errorMessage(caught));
    }
  }

  return (
    <>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        <DatabaseBackup aria-hidden="true" />
        {t('compliance.confirmBackup')}
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t('compliance.confirmBackup')}
        description={t('compliance.confirmBackupBody')}
        confirmLabel={t('compliance.confirmBackupAction')}
        busy={confirming}
        error={error}
        onConfirm={() => void confirm()}
      />
    </>
  );
}

/**
 * One check of Uyum Durumu (contract 2.8.1), worded by the server: number and title, the badge,
 * the reason, the facts, the open alerts, the verify-on-site note, the document as text (no docs
 * route, D12) and when it was checked. Links the user may not open are left out (`links`).
 */
export function ComplianceCardView({
  card,
  links,
  onConfirmBackup,
  confirming = false,
}: {
  card: ComplianceCard;
  links: CardLinks;
  /** Card 10 for an administrator: the backup confirmation. */
  onConfirmBackup?: () => Promise<unknown>;
  confirming?: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const texts = cardTexts(card, lang);
  const page = t(`nav.${card.page}`);
  const headingId = `compliance-card-${card.id}`;
  const alerts = t('compliance.openAlerts', {
    count: formatNumber(card.alerts.length, 0, lang),
  });
  return (
    <Card
      className="flex h-full min-w-0 flex-col"
      data-testid={headingId}
      aria-labelledby={headingId}
      role="group"
    >
      <CardHeader className="flex-wrap items-center gap-x-2 gap-y-1">
        <h2
          id={headingId}
          className="min-w-0 flex-1 basis-[14rem] text-sm font-semibold break-words text-foreground"
        >
          {card.number}. {texts.title}
        </h2>
        <ComplianceBadge tone={card.tone} />
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <p className="text-13 break-words text-foreground">{visibleText(texts.reason)}</p>
        {texts.facts.length > 0 ? (
          <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1 text-13">
            {texts.facts.map((fact) => (
              <Fragment key={fact.key}>
                <dt className="text-muted-strong">{fact.label}</dt>
                <dd className="min-w-0 break-words text-foreground tabular">
                  {visibleText(fact.value)}
                </dd>
              </Fragment>
            ))}
          </dl>
        ) : null}
        {card.alerts.length > 0 ? (
          <p className="flex items-center gap-1.5 text-13 font-medium text-foreground">
            <ShieldAlert aria-hidden="true" className="size-4 shrink-0 text-muted-strong" />
            {links.alerts ? (
              <Link to={links.alerts} className="rounded-sm text-accent-ink hover:underline">
                {alerts}
              </Link>
            ) : (
              <span>{alerts}</span>
            )}
          </p>
        ) : null}
        {texts.verifyOnSite ? (
          <p className="flex gap-1.5 text-13 text-foreground" data-testid="verify-on-site">
            <MapPinCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-strong" />
            <span className="min-w-0 break-words">
              <span className="font-medium">{t('compliance.verifyOnSite')}:</span>{' '}
              {texts.verifyOnSite}
            </span>
          </p>
        ) : null}
        {onConfirmBackup ? (
          <div>
            <ConfirmBackup onConfirm={onConfirmBackup} confirming={confirming} />
          </div>
        ) : null}
      </CardContent>
      <CardFooter className="flex-wrap justify-between gap-x-4 gap-y-1 py-2.5">
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="inline-flex items-center gap-1">
            <BookOpen aria-hidden="true" className="size-3.5 shrink-0" />
            {t('compliance.document', { doc: texts.doc })}
          </span>
          <span className="tabular">
            {t('compliance.checkedAt', { time: formatDateTime(card.checked_at) })}
          </span>
        </span>
        {links.page ? (
          <Link
            to={links.page}
            className="inline-flex items-center gap-1 rounded-sm text-13 font-medium text-accent-ink hover:underline"
            aria-label={t('dashboard.openPage', { page })}
          >
            {page}
            <ArrowRight aria-hidden="true" className="size-3.5" />
          </Link>
        ) : null}
      </CardFooter>
    </Card>
  );
}
