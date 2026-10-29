import {
  CircleCheck,
  CircleDashed,
  FileWarning,
  ListChecks,
  SearchX,
  ShieldAlert,
  Target,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import type { LookupOut } from '@/lib/api-types';
import { endpoint, formatDay, formatDistance, protocolName, visibleText } from '@/lib/display';
import { formatDateTime, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import {
  outcomeOf,
  queryOf,
  type DataWindow,
  type EgressEntry,
  type LookupMode,
  type NatMatch,
} from '@/lib/lookup-types';

import { egressLine } from './chain-text';
import { ChainView, EvidenceLinks, ExplanationList, type OpenEvidence } from './chain-view';

const MODE: Record<
  LookupMode,
  { tone: 'success' | 'warning' | 'info' | 'neutral'; icon: LucideIcon }
> = {
  exact: { tone: 'success', icon: CircleCheck },
  candidates: { tone: 'warning', icon: ListChecks },
  internal: { tone: 'info', icon: Target },
  none: { tone: 'neutral', icon: CircleDashed },
};

export function ModeBadge({ mode }: { mode: string | null }) {
  const { t } = useTranslation();
  const known = mode && mode in MODE ? (mode as LookupMode) : null;
  if (!known) return <Badge tone="neutral">{mode ?? '—'}</Badge>;
  const style = MODE[known];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(`lookup.mode.${known}`)}
    </Badge>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-muted">{label}</dt>
      <dd className="break-words text-foreground">{children}</dd>
    </div>
  );
}

/** What was asked (request details and the query), as stored by the server. */
function RequestSummary({ lookup }: { lookup: LookupOut }) {
  const { t } = useTranslation();
  const query = queryOf(lookup);
  const input = query.time_input;
  // A lookup stored before ADR-0016 has no `egress` (read `null`): no exit line.
  const egress = outcomeOf(lookup)?.egress ?? null;
  const protocol = protocolName(query.protocol);
  const destination =
    query.dest_ip || query.dest_port !== null || query.dest_domain
      ? [endpoint(query.dest_ip, query.dest_port), query.dest_domain].filter(Boolean).join(' · ')
      : null;
  return (
    <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-13">
      <Fact label={t('lookup.form.authority')}>{lookup.request.authority}</Fact>
      <Fact label={t('lookup.form.fileNo')}>{lookup.request.file_no}</Fact>
      <Fact label={t('lookup.form.requestDate')}>{formatDay(lookup.request.request_date)}</Fact>
      <Fact label={t('lookup.form.reason')}>
        <span className="whitespace-pre-wrap">{lookup.request.reason}</span>
      </Fact>
      <Fact label={t('lookup.result.moment')}>
        <span className="tabular">
          {formatDateTime(query.at)} {t('lookup.result.siteTime')}
        </span>
        {input ? (
          <span className="block text-xs text-muted">
            {input.offset_given
              ? t('lookup.result.enteredWithOffset', { text: input.text })
              : t('lookup.result.enteredLocal', { text: input.text, zone: input.time_zone ?? '' })}
          </span>
        ) : null}
      </Fact>
      <Fact label={t('lookup.result.tolerance')}>
        ±{' '}
        {t('lookup.result.seconds', {
          count: outcomeOf(lookup)?.tolerance_seconds ?? query.tolerance_seconds ?? 0,
        })}
      </Fact>
      {query.public_ip || query.public_port !== null ? (
        <Fact label={t('lookup.form.publicIp')}>
          <span className="font-mono">
            {endpoint(query.public_ip, query.public_port)}
            {protocol ? ` · ${protocol}` : ''}
          </span>
        </Fact>
      ) : null}
      {egress ? (
        <Fact label={t('network.lookup.exit')}>
          <span data-testid="lookup-exit">{egressLine(egress, t)}</span>
        </Fact>
      ) : null}
      {destination ? (
        <Fact label={t('lookup.result.destination')}>
          <span className="font-mono">{visibleText(destination)}</span>
        </Fact>
      ) : null}
      {query.internal_ip ? (
        <Fact label={t('lookup.form.internalIp')}>
          <span className="font-mono">{query.internal_ip}</span>
        </Fact>
      ) : null}
      {query.mac ? (
        <Fact label={t('lookup.form.mac')}>
          <span className="font-mono">{query.mac}</span>
        </Fact>
      ) : null}
    </dl>
  );
}

function DataWindowNote({ window }: { window: DataWindow }) {
  const { t } = useTranslation();
  const range =
    window.db_first_day && window.db_last_day
      ? t('lookup.window.range', {
          first: formatDay(window.db_first_day),
          last: formatDay(window.db_last_day),
        })
      : t('lookup.window.empty');
  return (
    <p className="text-xs text-muted-strong">
      {t('lookup.window.text', {
        day: formatDay(window.requested_day),
        database: window.in_database ? t('lookup.window.yes') : t('lookup.window.no'),
        vault: window.in_vault ? t('lookup.window.yes') : t('lookup.window.no'),
        range,
      })}
    </p>
  );
}

/**
 * "Çıkış: hat-2 (Yedek hat) · dstintf wan2", as the PDF prints it on every match (contract 2.12);
 * "-" for a session no exit claims, also under the implicit catch-all of `nat.egresses = {}`,
 * whose name is `""`. `null` for a lookup stored before ADR-0016.
 */
function matchExit(match: NatMatch, exits: readonly EgressEntry[], shown: boolean): string | null {
  const name = match.egress || null;
  const dstintf = match.traffic.dstintf ?? null;
  if (!shown && name === null && dstintf === null) return null;
  const exit = exits.find((item) => item.name === name);
  const label = exit?.label ? ` (${visibleText(exit.label)})` : '';
  return `${name === null ? '-' : `${name}${label}`} · dstintf ${dstintf ?? '-'}`;
}

function MatchCard({
  match,
  rank,
  exitText,
  onOpen,
}: {
  match: NatMatch;
  rank: number;
  exitText: string | null;
  onOpen: OpenEvidence;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const traffic = match.traffic;
  const protocol = protocolName(traffic.proto);
  const untrusted = match.untrusted_sources ?? [];
  const distance =
    match.time_distance_ms === 0
      ? t('lookup.match.inside')
      : formatDistance(match.time_distance_ms, lang);
  return (
    <Card className="flex flex-col gap-4 p-4" data-testid="match">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-foreground">
          {t('lookup.match.title', { rank })}
        </h3>
        <Badge tone={match.kind === 'exact' ? 'success' : 'warning'}>
          {t(`lookup.match.kind.${match.kind}`)}
        </Badge>
        <span className="text-13 text-muted-strong tabular">
          {t('lookup.match.score', { score: formatNumber(match.score, 0, lang) })}
        </span>
        <span className="text-13 text-muted-strong">·</span>
        <span className="text-13 text-muted-strong tabular">
          {t('lookup.match.distance', { distance })}
        </span>
        <span className="text-13 text-muted-strong">·</span>
        <span className="text-13 text-muted-strong">
          {t(`lookup.destinationMatch.${match.destination_match}`)}
        </span>
        {untrusted.length > 0 ? (
          <Badge tone="danger" icon={ShieldAlert} data-testid="untrusted-sender">
            {t('network.lookup.untrusted', { sources: untrusted.join(', ') })}
          </Badge>
        ) : null}
      </header>
      <section className="flex flex-col gap-1">
        <h4 className="text-xs font-medium tracking-wide text-muted-strong uppercase">
          {t('lookup.match.session')}
        </h4>
        <p className="font-mono text-13 wrap-anywhere text-foreground">
          {protocol ? `${protocol} ` : ''}
          {endpoint(traffic.srcip, traffic.srcport)} → {endpoint(traffic.dstip, traffic.dstport)}
          {traffic.transip ? ` · NAT ${endpoint(traffic.transip, traffic.transport)}` : ''}
        </p>
        <p className="text-xs text-muted-strong tabular">
          {t('lookup.match.span', {
            start: formatDateTime(traffic.start),
            end: formatTime(traffic.end, true),
          })}
          {traffic.sessionid !== null
            ? ` · ${t('lookup.match.sessionId', { id: traffic.sessionid })}`
            : ''}
        </p>
        {exitText ? (
          <p className="text-xs text-muted-strong" data-testid="match-exit">
            {t('network.lookup.matchExit', { exit: exitText })}
          </p>
        ) : null}
        <EvidenceLinks refs={traffic.evidence} onOpen={onOpen} />
      </section>
      {match.chain ? (
        <ChainView chain={match.chain} traffic={traffic} onOpen={onOpen} />
      ) : (
        <p className="text-13 text-muted-strong">{t('lookup.match.noChain')}</p>
      )}
    </Card>
  );
}

/**
 * The answer of a 5651 lookup: what was asked, the engine's explanations (double NAT, data only
 * in the vault, truncation …), then every match with its full attribution chain — or the chain of
 * the requested internal address. Every evidence row opens its raw line.
 */
export function LookupResults({ lookup, onOpen }: { lookup: LookupOut; onOpen: OpenEvidence }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const outcome = outcomeOf(lookup);
  // The stored summary of an unconverted row is Turkish only: the fixed label in both languages
  // (as the report prints it), in the list and in the raw-line sheet it opens.
  const unconverted = (outcome?.unconverted_dhcp ?? []).map((ref) => ({
    ...ref,
    summary: t('network.lookup.unconvertedSummary'),
  }));
  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-foreground">
            {t('lookup.result.title', { id: lookup.id })}
          </h2>
          <ModeBadge mode={lookup.mode} />
          <span className="text-13 text-muted-strong">
            {t('lookup.result.count', {
              count: lookup.result_count,
              formatted: formatNumber(lookup.result_count, 0, lang),
            })}
          </span>
        </div>
        <p className="text-xs text-muted-strong tabular">
          {t('lookup.result.meta', {
            time: formatDateTime(lookup.created_at),
            user: lookup.username,
          })}
          {typeof lookup.elapsed_ms === 'number'
            ? ` · ${t('lookup.result.elapsed', { ms: formatNumber(lookup.elapsed_ms, 0, lang) })}`
            : ''}
        </p>
        <RequestSummary lookup={lookup} />
        {outcome?.data_window ? <DataWindowNote window={outcome.data_window} /> : null}
      </Card>
      {outcome ? (
        <>
          <ExplanationList explanations={outcome.explanations} />
          {outcome.matches.length > 0 ? (
            <section className="flex flex-col gap-3" aria-label={t('lookup.result.matches')}>
              {outcome.matches.map((match, index) => (
                <MatchCard
                  key={`${match.traffic.sessionid ?? 'x'}-${match.traffic.end}-${index}`}
                  match={match}
                  rank={index + 1}
                  exitText={matchExit(
                    match,
                    outcome.egress?.egresses ?? [],
                    (outcome.egress ?? null) !== null,
                  )}
                  onOpen={onOpen}
                />
              ))}
            </section>
          ) : null}
          {outcome.attribution ? (
            <Card className="flex flex-col gap-3 p-4" data-testid="attribution">
              <h3 className="text-sm font-semibold text-foreground">
                {t('lookup.result.internal', { ip: outcome.attribution.internal_ip })}
              </h3>
              <ChainView chain={outcome.attribution} traffic={null} onOpen={onOpen} />
            </Card>
          ) : null}
          {unconverted.length > 0 ? (
            <Card className="flex flex-col gap-2 p-4" data-testid="unconverted-dhcp">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <FileWarning aria-hidden="true" className="size-4 text-warning-ink" />
                {t('network.lookup.unconvertedTitle')}
              </h3>
              <p className="text-13 text-muted-strong">{t('network.lookup.unconvertedNote')}</p>
              <EvidenceLinks refs={unconverted} onOpen={onOpen} />
            </Card>
          ) : null}
          {outcome.matches.length === 0 && !outcome.attribution ? (
            <EmptyState
              icon={SearchX}
              title={t('lookup.result.noneTitle')}
              description={t('lookup.result.noneBody')}
            />
          ) : null}
          {outcome.truncated ? (
            <p className="text-xs text-muted-strong">{t('lookup.result.truncatedNote')}</p>
          ) : null}
        </>
      ) : (
        <EmptyState icon={CircleDashed} title={t('lookup.result.unfinished')} />
      )}
    </div>
  );
}
