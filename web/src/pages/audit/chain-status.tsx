import { CircleHelp, Clock, RefreshCw, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useAuditVerify } from '@/lib/api-hooks';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

/**
 * Chain verification badge (spec 8.5, 12.3 page 14): `GET /audit/verify` recomputes every hash on
 * the server when the page opens; "Yeniden doğrula" asks again.
 */
export function ChainStatus() {
  const { t } = useTranslation();
  const verify = useAuditVerify();
  const lang = currentLang();
  const checking = verify.isFetching;

  let badge;
  if (verify.isPending) {
    badge = (
      <Badge tone="info" icon={Clock}>
        {t('audit.chain.checking')}
      </Badge>
    );
  } else if (verify.isError) {
    badge = (
      <Badge tone="neutral" icon={CircleHelp}>
        {t('audit.chain.unknown')}
      </Badge>
    );
  } else if (verify.data.ok) {
    badge = (
      <Badge tone="success" icon={ShieldCheck}>
        {t('audit.chain.ok', {
          count: verify.data.entries,
          formattedCount: formatNumber(verify.data.entries, 0, lang),
        })}
      </Badge>
    );
  } else {
    badge = (
      <Badge tone="danger" icon={ShieldAlert} className="font-semibold">
        {verify.data.first_bad_id === null
          ? t('audit.chain.broken')
          : t('audit.chain.brokenAt', { id: verify.data.first_bad_id })}
      </Badge>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2" aria-live="polite">
        {badge}
        {verify.data ? (
          <span className="text-xs text-muted tabular">
            {t('audit.chain.verifiedAt', { time: formatDateTime(verify.data.verified_at) })}
          </span>
        ) : null}
        <Button variant="secondary" size="sm" busy={checking} onClick={() => void verify.refetch()}>
          <RefreshCw aria-hidden="true" className={checking ? 'animate-spin' : undefined} />
          {t('audit.chain.recheck')}
        </Button>
      </div>
      {verify.isError ? <Alert tone="danger">{errorMessage(verify.error)}</Alert> : null}
      {verify.data && !verify.data.ok ? (
        <Alert tone="danger" title={t('audit.chain.brokenTitle')}>
          <p>{t('audit.chain.brokenBody')}</p>
          {verify.data.errors.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 font-mono text-xs">
              {verify.data.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          ) : null}
        </Alert>
      ) : null}
    </div>
  );
}
