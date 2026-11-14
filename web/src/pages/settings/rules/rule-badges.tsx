import { ShieldQuestionMark } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';

/** "Sahada doğrula": the rule reads a log format no sample line confirms (Wave 6 §0.4). */
export function VerifyBadge() {
  const { t } = useTranslation();
  return (
    <Badge tone="warning" icon={ShieldQuestionMark} title={t('rules.verifyHint')}>
      {t('rules.verify')}
    </Badge>
  );
}
