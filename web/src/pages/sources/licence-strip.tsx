import { KeyRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { useLicense } from '@/lib/api-hooks';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

/**
 * "Lisans: 7 / 10 log kaynağı (son 30 günde gönderenler dahil)" (contract 2.16.4) with the licence
 * page's own words for a full or exceeded limit (`license.page.limits.*`). It only informs:
 * the lines of every source are collected and sealed whatever the licence says (5651 Temel).
 * `GET /license` answers every signed-in user.
 */
export function LicenceStrip() {
  const { t } = useTranslation();
  const lang = currentLang();
  const license = useLicense();
  const limit = license.data?.limits.log_sources;
  if (!limit) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-13 text-muted" data-testid="licence-strip">
      <KeyRound aria-hidden="true" className="size-4 shrink-0" />
      <span className="tabular">
        {t('sources.licence.strip', {
          used: formatNumber(limit.used, 0, lang),
          max:
            limit.max === null
              ? t('license.page.limits.unlimited')
              : formatNumber(limit.max, 0, lang),
        })}
      </span>
      {limit.exceeded ? (
        <>
          <Badge tone="warning">{t('license.page.limits.exceeded')}</Badge>
          <span>{t('sources.licence.collected')}</span>
        </>
      ) : limit.full ? (
        <Badge tone="neutral">{t('license.page.limits.full')}</Badge>
      ) : null}
    </p>
  );
}
