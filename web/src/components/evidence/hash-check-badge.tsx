import { CircleHelp, ShieldAlert, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import type { HashCheck } from '@/lib/download';

/** The browser's check of a downloaded file against the hash the server announced. */
export function HashCheckBadge({ check }: { check: HashCheck }) {
  const { t } = useTranslation();
  if (check.state === 'match')
    return (
      <Badge tone="success" icon={ShieldCheck}>
        {t('download.match')}
      </Badge>
    );
  if (check.state === 'mismatch')
    return (
      <Badge tone="danger" icon={ShieldAlert}>
        {t('download.mismatch')}
      </Badge>
    );
  return (
    <Badge tone="neutral" icon={CircleHelp}>
      {t(`download.unchecked.${check.reason}`)}
    </Badge>
  );
}
