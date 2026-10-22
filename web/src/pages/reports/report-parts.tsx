import {
  CircleCheck,
  CircleCheckBig,
  CircleSlash,
  CircleX,
  Clock,
  FileSpreadsheet,
  FileText,
  Loader2,
  Mail,
  TriangleAlert,
  UsersRound,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import type { ReportFormats, ReportDelivery } from '@/lib/api-types';

import type { ReportProblem, RunShownStatus } from './report-errors';

type Tone = NonNullable<BadgeProps['tone']>;

const RUN_STYLE: Record<RunShownStatus, { tone: Tone; icon: LucideIcon }> = {
  queued: { tone: 'neutral', icon: Clock },
  running: { tone: 'info', icon: Loader2 },
  sent: { tone: 'success', icon: CircleCheck },
  done: { tone: 'success', icon: CircleCheckBig },
  partial: { tone: 'warning', icon: TriangleAlert },
  failed: { tone: 'danger', icon: CircleX },
};

/** Sırada, Hazırlanıyor, Gönderildi, Tamamlandı (e-posta yok), Kısmen gönderildi, Başarısız. */
export function RunStatusBadge({ status }: { status: RunShownStatus }) {
  const { t } = useTranslation();
  const style = RUN_STYLE[status];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(`reports.status.${status}`)}
    </Badge>
  );
}

const DELIVERY_STYLE: Record<ReportDelivery['status'], { tone: Tone; icon: LucideIcon }> = {
  sent: { tone: 'success', icon: CircleCheck },
  failed: { tone: 'danger', icon: CircleX },
  skipped: { tone: 'neutral', icon: CircleSlash },
};

export function DeliveryBadge({ status }: { status: ReportDelivery['status'] }) {
  const { t } = useTranslation();
  const style = DELIVERY_STYLE[status];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(`reports.delivery.${status}`)}
    </Badge>
  );
}

const FORMAT_ICON: Record<keyof ReportFormats, LucideIcon> = {
  email_html: Mail,
  pdf: FileText,
  xlsx: FileSpreadsheet,
};

/** The formats a schedule sends, as badges ("E-posta", "PDF", "Excel"). */
export function FormatBadges({ formats }: { formats: ReportFormats }) {
  const { t } = useTranslation();
  const on = (Object.keys(FORMAT_ICON) as (keyof ReportFormats)[]).filter((key) => formats[key]);
  return (
    <span className="flex flex-wrap gap-1">
      {on.map((key) => (
        <Badge key={key} tone="outline" icon={FORMAT_ICON[key]}>
          {t(`reports.formatsShort.${key}`)}
        </Badge>
      ))}
    </span>
  );
}

/** A refusal of the report API (the missing usage engine is a notice, not an error). */
export function ReportProblemAlert({ problem }: { problem: ReportProblem }) {
  return (
    <Alert
      tone={problem.kind === 'engine' ? 'warning' : 'danger'}
      title={problem.title}
      data-testid={`report-problem-${problem.kind}`}
    >
      {problem.text}
    </Alert>
  );
}

/** "Kişi düzeyinde" (with "Takma adlı" when pseudonymised) or "Şirket geneli". */
export function DetailBadge({
  personLevel,
  pseudonymize,
}: {
  personLevel: boolean;
  pseudonymize: boolean;
}) {
  const { t } = useTranslation();
  if (!personLevel) return <Badge tone="neutral">{t('reports.detail.company')}</Badge>;
  return (
    <span className="flex flex-wrap gap-1">
      <Badge tone="primary" icon={UsersRound}>
        {t('reports.detail.person')}
      </Badge>
      {pseudonymize ? <Badge tone="neutral">{t('reports.detail.pseudonymized')}</Badge> : null}
    </span>
  );
}
