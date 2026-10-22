import {
  CircleCheck,
  CircleHelp,
  Coins,
  RadioTower,
  Stamp,
  Timer,
  TriangleAlert,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Sparkline } from '@/components/charts/sparkline';
import { Badge, StatusBadge } from '@/components/ui/badge';
import type {
  DashboardClock,
  DashboardCredit,
  DashboardIngest,
  SealYesterday,
} from '@/lib/api-types';
import { formatDay, visibleText } from '@/lib/display';
import { formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { clockVerdict, creditDuration, sealStatus } from './dashboard-model';
import { BigValue, DashCard } from './dashboard-parts';

interface LinkProps {
  /** The page with the details, `null` when the user may not open it. */
  to: string | null;
}

/**
 * "Log alımı": active sources, messages per minute over the last hour, silent sources. No
 * watched source (a new installation has no `[[sources]]`): no "active" badge and no "0/0", but
 * what to add; messages of unknown senders still show in the rate.
 */
export function IngestCard({ ingest, to }: { ingest: DashboardIngest } & LinkProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const counts = ingest.sparkline.map((point) => point.count);
  const silent = ingest.silent.length > 0;
  const none = ingest.sources_total === 0;
  return (
    <DashCard
      title={t('dashboard.ingest.title')}
      icon={RadioTower}
      to={to}
      page={t('nav.sources')}
      status={none ? null : <StatusBadge status={silent ? 'silent' : 'active'} />}
      testId="card-ingest"
    >
      <BigValue
        value={
          none
            ? t('dashboard.ingest.noSources')
            : t('dashboard.ingest.active', {
                active: formatNumber(ingest.sources_active, 0, lang),
                total: formatNumber(ingest.sources_total, 0, lang),
              })
        }
        hint={t('dashboard.ingest.rate', {
          rate: formatNumber(ingest.messages_per_minute, 0, lang),
        })}
      />
      {none ? (
        <p className="text-13 text-muted" data-testid="no-sources">
          {t('dashboard.ingest.noSourcesHint')}
        </p>
      ) : null}
      {counts.length > 1 ? (
        <Sparkline
          values={counts}
          label={t('dashboard.ingest.trend')}
          formatValue={(value) => formatNumber(value, 0, lang)}
          className="h-10 w-full"
        />
      ) : null}
      {silent ? (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-danger-ink">{t('dashboard.ingest.silentTitle')}</p>
          <ul className="flex flex-col gap-0.5 text-13" data-testid="silent-sources">
            {ingest.silent.map((source) => (
              <li key={source.name} className="truncate">
                {source.last_seen
                  ? t('dashboard.ingest.silentSince', {
                      name: visibleText(source.name),
                      time: formatDateTime(source.last_seen),
                    })
                  : t('dashboard.ingest.silentNever', { name: visibleText(source.name) })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </DashCard>
  );
}

/** "Dünkü mühür": state, time, serial and verification of yesterday's seal. */
export function SealCard({ yesterday, to }: { yesterday: SealYesterday } & LinkProps) {
  const { t } = useTranslation();
  const status = sealStatus(yesterday.status, yesterday.verified);
  const verification =
    yesterday.verified === true
      ? t('dashboard.seal.verified')
      : yesterday.verified === false
        ? t('dashboard.seal.notVerified')
        : t('dashboard.seal.verifyPending');
  return (
    <DashCard
      title={t('dashboard.seal.title')}
      icon={Stamp}
      to={to}
      page={t('nav.sealing')}
      status={<StatusBadge status={status} />}
      testId="card-seal"
    >
      <BigValue
        value={
          yesterday.sealed_at
            ? `${formatDay(yesterday.day)} ${formatTime(yesterday.sealed_at)}`
            : formatDay(yesterday.day)
        }
        hint={
          yesterday.status === 'sealed'
            ? [
                yesterday.serial_short
                  ? t('dashboard.seal.serial', { serial: yesterday.serial_short })
                  : null,
                verification,
              ]
                .filter(Boolean)
                .join(' · ')
            : t(`dashboard.seal.state.${yesterday.status}`, {
                defaultValue: t('dashboard.seal.state.no_data'),
              })
        }
      />
    </DashCard>
  );
}

/** "Kontör": Kamu SM credit left and how long it lasts at the current use. */
export function CreditCard({ credit, to }: { credit: DashboardCredit } & LinkProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const low =
    credit.remaining !== null && credit.threshold !== null && credit.remaining < credit.threshold;
  const lasts = credit.estimated_days === null ? null : creditDuration(credit.estimated_days);
  return (
    <DashCard
      title={t('dashboard.credit.title')}
      icon={Coins}
      to={to}
      page={t('nav.sealing')}
      status={
        low ? (
          <Badge tone="danger" icon={TriangleAlert}>
            {t('dashboard.credit.low')}
          </Badge>
        ) : null
      }
      testId="card-credit"
    >
      <BigValue
        value={
          credit.remaining === null
            ? t('dashboard.credit.unknown')
            : t('dashboard.credit.remaining', {
                count: credit.remaining,
                formatted: formatNumber(credit.remaining, 0, lang),
              })
        }
        hint={
          lasts
            ? t(`dashboard.credit.lasts.${lasts.unit}`, {
                count: lasts.count,
                formatted: formatNumber(lasts.count, 0, lang),
              })
            : t('dashboard.credit.lastsUnknown')
        }
      />
      {credit.threshold !== null ? (
        <p className="text-xs text-muted">
          {t('dashboard.credit.threshold', { threshold: formatNumber(credit.threshold, 0, lang) })}
        </p>
      ) : null}
    </DashCard>
  );
}

const CLOCK_BADGE = {
  ok: { tone: 'success', icon: CircleCheck },
  off: { tone: 'danger', icon: TriangleAlert },
  unknown: { tone: 'neutral', icon: CircleHelp },
} as const;

/** "Saat sapması": the server clock's offset from NTP (R20 above 1 s). */
export function ClockCard({ clock, to }: { clock: DashboardClock } & LinkProps) {
  const { t } = useTranslation();
  const lang = currentLang();
  const verdict = clockVerdict(clock);
  const offset = clock.offset_ms;
  return (
    <DashCard
      title={t('dashboard.clock.title')}
      icon={Timer}
      to={to}
      page={t('nav.system')}
      status={
        <Badge tone={CLOCK_BADGE[verdict].tone} icon={CLOCK_BADGE[verdict].icon}>
          {t(`dashboard.clock.verdict.${verdict}`)}
        </Badge>
      }
      testId="card-clock"
    >
      <BigValue
        value={
          offset === null
            ? t('dashboard.clock.unknown')
            : t('dashboard.clock.offset', {
                value: formatNumber(offset, Math.abs(offset) < 10 ? 1 : 0, lang),
              })
        }
        hint={
          clock.ntp_server
            ? t('dashboard.clock.ntp', { server: clock.ntp_server })
            : t('dashboard.clock.noNtp')
        }
      />
      {clock.checked_at ? (
        <p className="text-xs text-muted">
          {t('dashboard.clock.checked', { time: formatDateTime(clock.checked_at) })}
        </p>
      ) : null}
    </DashCard>
  );
}
