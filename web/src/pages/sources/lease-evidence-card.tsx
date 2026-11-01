import { CircleAlert, CircleCheck, CircleSlash, KeyRound } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { LeaseEvidence, LeaseEvidenceState } from '@/lib/sources-types';

import { formatUnverified, LEASE_TABLES, noneLines } from './lease-evidence-model';

const STATE: Record<
  LeaseEvidenceState,
  { tone: 'success' | 'warning' | 'danger'; icon: LucideIcon }
> = {
  ok: { tone: 'success', icon: CircleCheck },
  none: { tone: 'warning', icon: CircleSlash },
  refused: { tone: 'danger', icon: CircleAlert },
};

/** Where the reasons of refused rows are: the lease alert on Güvenlik. */
const ALERT_LINK = '/security?rule=R20-lease';
/** The FortiGate DHCP settings (logids, `dhcp_msg` values, excluded interfaces). */
const SETTINGS_LINK = '/settings/application?group=lookup';

/**
 * "Kira kanıtı" on Kaynaklar (contract 2.13, D30): whether the DHCP and ARP-LOG records the 5651
 * lookup's lease timeline rests on arrive and are used. Three states, `refused` winning over
 * `none`: FortiGate DHCP rows arrive but are not used (the count follows the current settings
 * and sources, so it drops as soon as a correction is saved and the leases are rebuilt), no
 * usable record of a table arrives any more (one line per table), or both fine. Counts only.
 */
export function LeaseEvidenceCard({ evidence }: { evidence: LeaseEvidence }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { can } = useAuth();
  const style = STATE[evidence.state];
  const fg = evidence.fg_dhcp;
  const alerts = can('alerts.read');
  const settings = can('settings.manage');
  return (
    <Card className="flex flex-col gap-3 p-4" data-testid="lease-evidence">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-foreground">
          {t('network.leaseEvidence.title')}
        </h2>
        <Badge tone={style.tone} icon={style.icon}>
          {t(`network.leaseEvidence.states.${evidence.state}`)}
        </Badge>
      </div>
      {evidence.state === 'refused' ? (
        <Alert tone="danger" assertive={false} data-testid="lease-evidence-refused">
          <span className="flex flex-col gap-1">
            <span>
              {t('network.leaseEvidence.refused', {
                refused: formatNumber(fg.refused_rows, 0, lang),
              })}
            </span>
            {alerts || settings ? (
              <span className="flex flex-wrap gap-x-3 gap-y-1">
                {alerts ? (
                  <Link to={ALERT_LINK} className="font-medium underline">
                    {t('network.leaseEvidence.toAlert')}
                  </Link>
                ) : null}
                {settings ? (
                  <Link to={SETTINGS_LINK} className="font-medium underline">
                    {t('network.leaseEvidence.toSettings')}
                  </Link>
                ) : null}
              </span>
            ) : null}
          </span>
        </Alert>
      ) : null}
      {noneLines(evidence).map((line) => (
        <Alert
          key={line.table ?? line.key}
          tone="warning"
          assertive={false}
          data-none={line.table ?? 'absent'}
        >
          {t(`network.leaseEvidence.${line.key}`, {
            evidence: line.table ? t(`network.leaseEvidence.evidence.${line.table}`) : '',
          })}
        </Alert>
      ))}
      <dl className="grid gap-x-6 gap-y-2 text-13 sm:grid-cols-3">
        {LEASE_TABLES.map((table) => {
          const counts = evidence[table];
          return (
            <div key={table} className="flex flex-col gap-0.5" data-table={table}>
              <dt className="font-medium text-foreground">
                {t(`network.leaseEvidence.tables.${table}`)}
              </dt>
              <dd className="text-muted-strong tabular">
                {t('network.leaseEvidence.rows', {
                  count: counts.rows,
                  formatted: formatNumber(counts.rows, 0, lang),
                })}
              </dd>
              <dd className="text-muted-strong tabular">
                {t('network.leaseEvidence.leases', {
                  count: counts.leases_current,
                  formatted: formatNumber(counts.leases_current, 0, lang),
                })}
              </dd>
              <dd className="text-xs text-muted tabular">
                {counts.last_hour
                  ? t('network.leaseEvidence.lastHour', { time: formatDateTime(counts.last_hour) })
                  : t('network.leaseEvidence.noneLastHour')}
              </dd>
            </div>
          );
        })}
      </dl>
      {fg.refused_rows > 0 ? (
        <p className="text-13 text-danger-ink" data-testid="lease-evidence-refused-rows">
          {t('network.leaseEvidence.refusedRows', {
            count: fg.refused_rows,
            formatted: formatNumber(fg.refused_rows, 0, lang),
          })}
          {alerts ? (
            <>
              {' '}
              <Link to={ALERT_LINK} className="font-medium underline">
                {t('network.leaseEvidence.toAlerts')}
              </Link>
            </>
          ) : null}
        </p>
      ) : null}
      {fg.excluded_rows > 0 ? (
        <p className="text-13 text-muted-strong" data-testid="lease-evidence-excluded">
          {t('network.leaseEvidence.excluded', {
            count: fg.excluded_rows,
            formatted: formatNumber(fg.excluded_rows, 0, lang),
          })}
        </p>
      ) : null}
      {formatUnverified(evidence) ? (
        <Alert tone="info" assertive={false} data-testid="lease-evidence-unverified">
          {t('network.leaseEvidence.unverified')}
        </Alert>
      ) : null}
      {evidence.srcmac.interfaces.length > 0 ? (
        <p className="flex flex-wrap items-center gap-2 text-13 text-muted-strong">
          <KeyRound aria-hidden="true" className="size-4 shrink-0" />
          {evidence.srcmac.rows_last_hour === null
            ? t('network.leaseEvidence.srcmacUncounted', {
                interfaces: evidence.srcmac.interfaces.join(', '),
              })
            : t('network.leaseEvidence.srcmac', {
                interfaces: evidence.srcmac.interfaces.join(', '),
                rows: formatNumber(evidence.srcmac.rows_last_hour, 0, lang),
              })}
        </p>
      ) : null}
    </Card>
  );
}
