import { ArrowDown, ArrowUp, Gauge, Settings2, TriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { Sparkline } from '@/components/charts/sparkline';
import { Badge } from '@/components/ui/badge';
import type { WanLine, WanRates } from '@/lib/activity-types';
import { formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { DashCard } from './dashboard-parts';
import { capacityShare, formatBps, polledLines, shareText, sparkValues } from './wan-model';

function Direction({ line, direction }: { line: WanLine; direction: 'download' | 'upload' }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const bps = direction === 'download' ? line.download_bps : line.upload_bps;
  const capacity = direction === 'download' ? line.capacity_mbps?.down : line.capacity_mbps?.up;
  const share = capacityShare(bps, capacity);
  const Icon = direction === 'download' ? ArrowDown : ArrowUp;
  const label = t(`snmp.card.${direction}`);
  const values = sparkValues(line, direction);
  return (
    <div className="flex items-center gap-2">
      <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-strong" />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-xs text-muted">{label}</span>
        <span className="text-lg leading-6 font-semibold text-foreground tabular">
          {formatBps(bps, lang)}
          {share !== null ? (
            <span className="ml-1.5 text-xs font-normal text-muted tabular">
              {t('snmp.card.share', { share: shareText(share, lang) })}
            </span>
          ) : null}
        </span>
      </span>
      {values.length > 1 ? (
        <Sparkline
          className="h-8 w-24 shrink-0"
          values={values}
          label={t('snmp.card.spark', { what: label, line: line.name })}
          formatValue={(value) => formatBps(value * 1_000_000, lang)}
        />
      ) : null}
    </div>
  );
}

function LineBox({ line, many }: { line: WanLine; many: boolean }) {
  const { t } = useTranslation();
  const failing = line.status !== 'ok';
  const down = line.oper_status !== null && line.oper_status !== 'up';
  return (
    <li
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
      data-testid="wan-line"
      data-line={`${line.source}/${line.name}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-13 font-medium text-foreground">{line.name}</span>
        {many ? <span className="text-xs text-muted">{line.source}</span> : null}
        {failing ? (
          <Badge tone="warning" icon={TriangleAlert} className="ml-auto">
            {t(`snmp.card.status.${line.status}`)}
          </Badge>
        ) : down ? (
          <Badge tone="warning" icon={TriangleAlert} className="ml-auto">
            {t('snmp.card.operDown', { state: line.oper_status })}
          </Badge>
        ) : null}
      </div>
      <Direction line={line} direction="download" />
      <Direction line={line} direction="upload" />
      <p className="text-xs text-muted tabular">
        {line.updated_at
          ? t('snmp.card.updated', { time: formatTime(line.updated_at, true) })
          : t('snmp.card.noSample')}
      </p>
    </li>
  );
}

/**
 * The Panel card "İnternet hattı" (Wave 5 contract §6 N7): each line read over SNMP with its
 * download and upload now, its share of the capacity when one is set, and the last 60 minutes
 * as a sparkline; a line that is not read (no answer, refused keys, …) says so. Its link opens
 * Canlı Durum for those who may open it.
 */
export function WanCard({
  rates,
  to,
  className,
}: {
  rates: WanRates;
  /** Canlı Durum, when the user may open it. */
  to: string | null;
  className?: string;
}) {
  const { t } = useTranslation();
  const lines = polledLines(rates);
  const many = new Set(lines.map((line) => line.source)).size > 1;
  return (
    <DashCard
      title={t('snmp.card.title')}
      icon={Gauge}
      to={to}
      page={t('nav.activity')}
      className={className}
      testId="card-wan"
    >
      <ul
        className={cn(
          'grid gap-3',
          lines.length > 1 ? 'sm:grid-cols-2' : null,
          lines.length > 2 ? 'xl:grid-cols-4' : null,
        )}
        aria-label={t('snmp.card.lines')}
      >
        {lines.map((line) => (
          <LineBox key={`${line.source}/${line.name}`} line={line} many={many} />
        ))}
      </ul>
    </DashCard>
  );
}

/**
 * In place of the card for an administrator while no source has SNMP on: where to switch it on
 * (Ayarlar → Kaynaklar → kaynak → SNMP).
 */
export function WanSetupCard({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <DashCard
      title={t('snmp.card.title')}
      icon={Gauge}
      className={className}
      testId="card-wan-setup"
    >
      <p className="text-13 text-muted">{t('snmp.card.setup')}</p>
      <Link
        to="/settings/sources"
        className="inline-flex items-center gap-1.5 self-start rounded-sm text-13 font-medium text-accent-ink hover:underline"
      >
        <Settings2 aria-hidden="true" className="size-3.5" />
        {t('snmp.card.setupLink')}
      </Link>
    </DashCard>
  );
}
