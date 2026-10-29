import { useQuery } from '@tanstack/react-query';
import { Plus, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, errorMessage } from '@/lib/api';
import type { FortigateObserved, ObservedNatRow } from '@/lib/api-types';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import {
  applyPrefill,
  draftClaim,
  prefillOptions,
  type EgressRow,
  type PrefillOption,
} from './egress-model';

/** The window of the NAT part (the API allows 1–72 hours). */
const OBSERVED_HOURS = 24;

/**
 * `GET /settings/fortigate/observed` (`settings.manage`; the server caches it 5 minutes and
 * answers 504 `fortigate_observed_timeout` when it cannot summarise in time). Under the
 * `settings` key, so saving a setting reads it again (the exits that claim each address).
 */
function useFortigateObserved(hours = OBSERVED_HOURS) {
  return useQuery({
    queryKey: ['settings', 'fortigate-observed', hours] as const,
    queryFn: ({ signal }) =>
      api.get<FortigateObserved>('/settings/fortigate/observed', { signal, params: { hours } }),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** `lookup.srcmac_interfaces` and `lookup.srcmac_max_ips` of the same settings group. */
export interface SrcmacDrafts {
  interfaces: readonly string[];
  maxIps: number;
  add: (name: string) => void;
}

function OptionButton({
  option,
  row,
  disabled,
  onTake,
}: {
  option: PrefillOption;
  row: ObservedNatRow;
  disabled: boolean;
  onTake: (option: PrefillOption) => void;
}) {
  const { t } = useTranslation();
  const label =
    option.kind === 'join'
      ? t(`network.observed.join.${option.field}`, { name: option.name })
      : option.kind === 'internal'
        ? t('network.observed.addInternal')
        : t('network.observed.add');
  return (
    <Button
      size="sm"
      variant={option.kind === 'internal' ? 'ghost' : 'secondary'}
      disabled={disabled}
      aria-label={`${label}: ${row.transip}`}
      onClick={() => {
        onTake(option);
      }}
    >
      <Plus aria-hidden="true" />
      {label}
    </Button>
  );
}

/**
 * "Kayıtlarda görülen NAT adresleri" under the exit editor (contract 2.13, D20): the
 * `(transip, dstintf, trandisp)` groups of the FortiGate's sessions of the last 24 hours with the
 * exit that claims each, so the administrator reads the values instead of guessing them. An
 * address no exit claims can be taken into the draft by the fixed rule of `prefillOptions`
 * (nothing is saved before "Kaydet"); a NAT type outside `nat.snat_trandisp` is flagged. Below,
 * the interfaces whose sessions carry srcmac, with "srcmac için ekle" for an interface where no
 * MAC was seen with more addresses than `lookup.srcmac_max_ips` allows. Counts and the
 * firewall's own addresses only.
 */
export function FortiGateObservedPanel({
  rows,
  onRows,
  disabled,
  editorId,
  srcmac,
}: {
  rows: EgressRow[];
  onRows: (rows: EgressRow[]) => void;
  disabled: boolean;
  /** The exit editor's id: a taken address moves the focus to its field there. */
  editorId: string;
  srcmac: SrcmacDrafts | null;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const observed = useFortigateObserved();
  const data = observed.data;

  function take(row: ObservedNatRow, option: PrefillOption) {
    const next = applyPrefill(rows, row, option);
    onRows(next);
    const target =
      option.kind === 'join' ? `${option.row}-${option.field}` : `${next.at(-1)?.key ?? ''}-name`;
    window.requestAnimationFrame(() => {
      document.getElementById(`${editorId}-${target.replace(/[^A-Za-z0-9-]/g, '-')}`)?.focus();
    });
  }

  return (
    <section
      className="flex flex-col gap-3 rounded-lg border border-border bg-subtle/40 p-3"
      aria-labelledby={`${editorId}-observed-title`}
      data-testid="fortigate-observed"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${editorId}-observed-title`} className="text-13 font-semibold text-foreground">
          {t('network.observed.title')}
        </h3>
        <Button
          size="sm"
          variant="ghost"
          busy={observed.isFetching}
          onClick={() => void observed.refetch()}
        >
          <RefreshCw aria-hidden="true" />
          {t('network.observed.refresh')}
        </Button>
      </div>
      {observed.isPending ? (
        <div role="status" aria-label={t('common.loading')} className="flex flex-col gap-2">
          <Skeleton className="h-5 w-64" />
          <Skeleton className="h-24" />
        </div>
      ) : observed.isError ? (
        <Alert tone="danger" title={t('network.observed.failed')}>
          {errorMessage(observed.error)}
        </Alert>
      ) : data ? (
        <>
          {data.nat.exits_invalid ? (
            <Alert tone="warning" assertive={false} data-testid="observed-exits-invalid">
              {t('network.observed.exitsInvalid')}
            </Alert>
          ) : null}
          {data.nat.rows.length === 0 ? (
            <p className="text-13 text-muted">{t('network.observed.empty')}</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border bg-surface">
              <table className="w-full text-13">
                <caption className="sr-only">{t('network.observed.caption')}</caption>
                <thead className="bg-subtle text-left text-xs text-muted-strong">
                  <tr>
                    {(
                      [
                        'transip',
                        'dstintf',
                        'trandisp',
                        'sessions',
                        'seen',
                        'egress',
                        'action',
                      ] as const
                    ).map((column) => (
                      <th
                        key={column}
                        scope="col"
                        className="px-3 py-2 font-medium whitespace-nowrap"
                      >
                        {t(`network.observed.columns.${column}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.nat.rows.map((row) => {
                    const claim = draftClaim(row, rows);
                    const unclaimed = row.egress === null && claim === null;
                    const options = unclaimed ? prefillOptions(row, rows) : [];
                    return (
                      <tr
                        key={`${row.transip}|${row.dstintf ?? ''}|${row.trandisp ?? ''}`}
                        className="border-t border-border align-top"
                      >
                        <th
                          scope="row"
                          className="px-3 py-2 text-left font-mono text-xs font-medium"
                        >
                          {row.transip}
                        </th>
                        <td className="px-3 py-2 font-mono text-xs">{row.dstintf ?? '—'}</td>
                        <td className="px-3 py-2">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="font-mono text-xs">{row.trandisp ?? '—'}</span>
                            {row.trandisp_known ? null : (
                              <Badge tone="warning">{t('network.observed.unknownTrandisp')}</Badge>
                            )}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right tabular">
                          {formatNumber(row.sessions, 0, lang)}
                        </td>
                        <td className="px-3 py-2 text-xs whitespace-nowrap text-muted-strong tabular">
                          {formatDateTime(row.first_seen)}
                          <br />
                          {formatDateTime(row.last_seen)}
                        </td>
                        <td className="px-3 py-2">
                          {row.egress ? (
                            <span className="font-mono text-xs">{row.egress}</span>
                          ) : claim ? (
                            <span className="text-xs text-muted-strong">
                              {t('network.observed.inDraft', { name: claim.name.trim() })}
                            </span>
                          ) : (
                            <Badge tone="neutral">{t('network.observed.unmapped')}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          {options.length > 0 ? (
                            <div className="flex flex-col items-start gap-1.5">
                              {options.map((option) => (
                                <OptionButton
                                  key={option.kind === 'join' ? `join-${option.row}` : option.kind}
                                  option={option}
                                  row={row}
                                  disabled={disabled}
                                  onTake={(taken) => {
                                    take(row, taken);
                                  }}
                                />
                              ))}
                              {options.some((option) => option.kind === 'internal') ? (
                                <p className="max-w-xs text-xs text-muted">
                                  {t('network.observed.internalHint')}
                                </p>
                              ) : null}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {data.nat.truncated ? (
            <p className="text-xs text-muted-strong">{t('network.observed.truncated')}</p>
          ) : null}
          {data.nat.unknown_trandisp.length > 0 ? (
            <Alert tone="warning" assertive={false} data-testid="observed-unknown-trandisp">
              {t('network.observed.unknownTrandispNote', {
                values: data.nat.unknown_trandisp.join(', '),
              })}
            </Alert>
          ) : null}
          <SrcmacTable data={data} srcmac={srcmac} disabled={disabled} />
        </>
      ) : null}
    </section>
  );
}

function SrcmacTable({
  data,
  srcmac,
  disabled,
}: {
  data: FortigateObserved;
  srcmac: SrcmacDrafts | null;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const items = data.srcmac.interfaces;
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-13 font-medium text-foreground">{t('network.observed.srcmacTitle')}</h4>
      {items.length === 0 ? (
        <p className="text-13 text-muted">{t('network.observed.srcmacEmpty')}</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface">
          <table className="w-full text-13">
            <caption className="sr-only">{t('network.observed.srcmacCaption')}</caption>
            <thead className="bg-subtle text-left text-xs text-muted-strong">
              <tr>
                {(['srcintf', 'rows', 'macs', 'maxIps', 'action'] as const).map((column) => (
                  <th key={column} scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
                    {t(`network.observed.srcmacColumns.${column}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const listed =
                  item.configured || (srcmac?.interfaces.includes(item.srcintf) ?? false);
                const limit = srcmac?.maxIps ?? 1;
                const shared = item.max_ips_per_mac > limit;
                const sharedId = `srcmac-shared-${item.srcintf.replace(/[^A-Za-z0-9-]/g, '-')}`;
                return (
                  <tr key={item.srcintf} className="border-t border-border align-top">
                    <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-medium">
                      {item.srcintf}
                    </th>
                    <td className="px-3 py-2 text-right tabular">
                      {formatNumber(item.rows, 0, lang)}
                    </td>
                    <td className="px-3 py-2 text-right tabular">
                      {formatNumber(item.macs, 0, lang)}
                    </td>
                    <td className="px-3 py-2 text-right tabular">
                      {formatNumber(item.max_ips_per_mac, 0, lang)}
                    </td>
                    <td className="px-3 py-2">
                      {listed ? (
                        <Badge tone="success">
                          {item.configured
                            ? t('network.observed.srcmacUsed')
                            : t('network.observed.inDraftShort')}
                        </Badge>
                      ) : srcmac ? (
                        <div className="flex flex-col items-start gap-1">
                          <Button
                            size="sm"
                            variant="secondary"
                            disabled={disabled || shared}
                            aria-label={`${t('network.observed.srcmacAdd')}: ${item.srcintf}`}
                            aria-describedby={shared ? sharedId : undefined}
                            onClick={() => {
                              srcmac.add(item.srcintf);
                            }}
                          >
                            <Plus aria-hidden="true" />
                            {t('network.observed.srcmacAdd')}
                          </Button>
                          {shared ? (
                            <p id={sharedId} className="max-w-xs text-xs text-muted">
                              {t('network.observed.srcmacShared', {
                                count: item.max_ips_per_mac,
                                limit,
                              })}
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {data.srcmac.truncated ? (
        <p className="text-xs text-muted-strong">{t('network.observed.srcmacTruncated')}</p>
      ) : null}
      <p className="text-xs text-muted">{t('network.observed.srcmacHint')}</p>
    </div>
  );
}
