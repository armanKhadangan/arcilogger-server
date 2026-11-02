import { AlertTriangle, XCircle } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import { useAuth } from '@/lib/auth';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { hideFor, isHidden, readHide, writeHide } from './banner-hide';
import { linkFor } from './links';
import { reasonText, reasonTone } from './reason-text';
import type { LegalReason } from '@/lib/legal-status-types';
import { useLegalStatus } from './use-legal-status';

const LINK_CLASS = 'font-medium whitespace-nowrap underline underline-offset-2';

/** The reason's page link, when it has one the user may open. */
function ReasonLink({ reason }: { reason: LegalReason }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const link = linkFor(reason, can);
  if (!link) return null;
  return (
    <>
      {' '}
      <Link to={link.to} className={LINK_CLASS}>
        {t(link.label)}
      </Link>
    </>
  );
}

/**
 * The legal-validity banner above every page, for every signed-in role (Wave 5 contract 2.3.2):
 * the worst reason of `GET /legal-status` with its documentation reference, red for danger and
 * amber for warning (`Alert`'s icon and text, never colour alone), and "ve N uyarı daha", which
 * opens the other reasons. Danger is announced at once and cannot be closed; a warning is polite
 * and can be hidden for 24 h in this browser (`banner-hide.ts`). Each reason links to the page
 * that fixes it, only for a user who may open that page. Nothing while the status is loading,
 * could not be read, or is `ok`. The status refreshes itself every 5 minutes in the background
 * (`useLegalStatus`), and the pause is re-decided against each answer's time, so a hidden warning
 * comes back with the first refresh after 24 h.
 */
export function LegalBanner() {
  const { t } = useTranslation();
  const legal = useLegalStatus();
  const [hide, setHide] = useState(readHide);
  const [open, setOpen] = useState(false);
  const listId = useId();
  const status = legal.data;
  if (!status || status.level === 'ok') return null;
  const [worst, ...others] = status.reasons;
  if (!worst || isHidden(status, hide, legal.dataUpdatedAt)) return null;
  const lang = currentLang();
  const hideable = status.level === 'warning';

  function hideWarning() {
    if (!status) return;
    const next = hideFor(status, Date.now());
    writeHide(next);
    setHide(next);
    toast({ title: t('legalStatus.banner.hidden') });
    // The button disappears with the banner: keep the keyboard on the page, not on `<body>`.
    document.getElementById('content')?.focus();
  }

  return (
    <div className="mb-4" data-testid="legal-banner">
      <Alert tone={reasonTone(worst.level)} data-testid={`legal-banner-${worst.code}`}>
        <p>
          <span className="sr-only">{t('legalStatus.banner.label')}: </span>
          {reasonText(worst, lang)}
          <ReasonLink reason={worst} />
        </p>
        {others.length > 0 || hideable ? (
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
            {others.length > 0 ? (
              <Button
                variant="link"
                size="sm"
                className="h-auto p-0 text-current underline underline-offset-2"
                aria-expanded={open}
                aria-controls={listId}
                onClick={() => {
                  setOpen((value) => !value);
                }}
              >
                {t('legalStatus.banner.more', {
                  count: others.length,
                  formattedCount: formatNumber(others.length, 0, lang),
                })}
              </Button>
            ) : null}
            {hideable ? (
              <Button
                variant="link"
                size="sm"
                className="h-auto p-0 text-current underline underline-offset-2"
                onClick={hideWarning}
              >
                {t('legalStatus.banner.hide')}
              </Button>
            ) : null}
          </div>
        ) : null}
        {others.length > 0 ? (
          <ul
            id={listId}
            hidden={!open}
            aria-label={t('legalStatus.banner.listLabel')}
            className="mt-2 flex flex-col gap-1.5"
          >
            {others.map((reason) => {
              const Icon = reason.level === 'danger' ? XCircle : AlertTriangle;
              return (
                <li
                  key={reason.code}
                  className="flex gap-2"
                  data-testid={`legal-reason-${reason.code}`}
                >
                  <Icon
                    aria-hidden="true"
                    className={cn(
                      'mt-0.5 size-4 shrink-0',
                      reason.level === 'danger' ? 'text-danger-ink' : 'text-warning-ink',
                    )}
                  />
                  <span className="min-w-0">
                    {reasonText(reason, lang)}
                    <ReasonLink reason={reason} />
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </Alert>
    </div>
  );
}
