import {
  ArrowRight,
  Building2,
  FileText,
  Globe,
  MonitorSmartphone,
  Network,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestionMark,
  User,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { evidenceTableLabel } from '@/components/evidence/evidence-labels';
import type { RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { endpoint, formatDay, visibleText } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type {
  AttributionChain,
  Confidence,
  EvidenceRef,
  Explanation,
  LeaseInfo,
  TrafficInfo,
} from '@/lib/lookup-types';
import { cn } from '@/lib/utils';

import {
  assignmentText,
  chainSummary,
  explanationTone,
  LEASE_STEP_EXPLANATIONS,
  personText,
} from './chain-text';

export type OpenEvidence = (target: RawLineTarget) => void;

const CONFIDENCE: Record<Confidence, { tone: 'success' | 'warning' | 'danger'; icon: LucideIcon }> =
  {
    high: { tone: 'success', icon: ShieldCheck },
    medium: { tone: 'warning', icon: ShieldQuestionMark },
    low: { tone: 'danger', icon: ShieldAlert },
  };

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const { t } = useTranslation();
  const style = CONFIDENCE[confidence];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {t(`lookup.confidence.${confidence}`)}
    </Badge>
  );
}

/** The engine's explanation boxes, in the interface language (texts come from the API). */
export function ExplanationList({
  explanations,
  className,
}: {
  explanations: readonly Explanation[];
  className?: string;
}) {
  const english = currentLang() === 'en';
  if (explanations.length === 0) return null;
  return (
    <ul className={cn('flex flex-col gap-2', className)}>
      {explanations.map((explanation, index) => (
        <li key={`${explanation.key}-${index}`} data-explanation={explanation.key}>
          <Alert tone={explanationTone(explanation.key)} assertive={false}>
            {english ? explanation.text_en : explanation.text_tr}
          </Alert>
        </li>
      ))}
    </ul>
  );
}

/** Buttons that open the raw lines behind a step (one per evidence row). */
export function EvidenceLinks({
  refs,
  onOpen,
}: {
  refs: readonly EvidenceRef[];
  onOpen: OpenEvidence;
}) {
  const { t } = useTranslation();
  if (refs.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1" aria-label={t('lookup.evidence')}>
      {refs.map((ref, index) => {
        const label = evidenceTableLabel(ref.table, t);
        const where = ref.raw
          ? t('lookup.rawWhere', {
              source: ref.raw.source,
              day: formatDay(ref.raw.day),
              line: ref.raw.line,
            })
          : t('lookup.noRawLine');
        const raw = ref.raw;
        return (
          <li key={`${ref.table}-${ref.row_id ?? index}-${index}`}>
            {raw ? (
              <button
                type="button"
                onClick={() => {
                  onOpen({ ref: raw, evidence: ref });
                }}
                className="group flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-left text-xs hover:bg-subtle"
              >
                <FileText aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-accent" />
                <span className="flex min-w-0 flex-col">
                  <span className="font-medium text-accent-ink group-hover:underline">
                    {label} · {where}
                  </span>
                  <span className="font-mono wrap-anywhere text-muted-strong">
                    {visibleText(ref.summary)}
                  </span>
                </span>
              </button>
            ) : (
              <span className="flex items-start gap-2 px-1.5 py-1 text-xs text-muted-strong">
                <FileText aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
                <span>
                  {label} · {where} · {visibleText(ref.summary)}
                </span>
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

interface StepProps {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  confidence?: Confidence | undefined;
  evidence?: readonly EvidenceRef[];
  /** Explanations that belong to this step (why the lease makes the chain `low`). */
  notes?: readonly Explanation[];
  onOpen: OpenEvidence;
}

function Step({
  icon: Icon,
  label,
  value,
  detail,
  confidence,
  evidence = [],
  notes = [],
  onOpen,
}: StepProps) {
  return (
    <li className="group/step relative flex gap-3 pb-4 last:pb-0">
      <span
        aria-hidden="true"
        className="absolute top-8 bottom-0 left-[15px] w-px bg-border group-last/step:hidden"
      />
      <span className="relative flex size-8 shrink-0 items-center justify-center rounded-full bg-subtle text-muted-strong">
        <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1 pt-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-xs font-medium tracking-wide text-muted-strong uppercase">
            {label}
          </span>
          {confidence ? <ConfidenceBadge confidence={confidence} /> : null}
        </div>
        <div className="text-sm wrap-anywhere text-foreground">{value}</div>
        {detail ? <div className="text-xs text-muted-strong">{detail}</div> : null}
        <ExplanationList explanations={notes} />
        <EvidenceLinks refs={evidence} onOpen={onOpen} />
      </div>
    </li>
  );
}

/**
 * The span of a lease with its end reason, its source (`evidenceTables`) and, for a FortiGate
 * lease, the lease time of its record and its last DHCP evidence at the requested moment.
 */
function LeaseDetail({ lease }: { lease: LeaseInfo }) {
  const { t } = useTranslation();
  const span = lease.end
    ? t('lookup.chain.leaseSpan', {
        start: formatDateTime(lease.start),
        end: formatDateTime(lease.end),
        reason: t(`lookup.endReason.${lease.end_reason}`, { defaultValue: lease.end_reason }),
      })
    : // Still running: "– hâlâ açık", not "– açık (hâlâ açık)".
      t('lookup.chain.leaseSpanOpen', { start: formatDateTime(lease.start) });
  const source = lease.source_table ?? null;
  return (
    <>
      <span className="block">{span}</span>
      {source ? (
        <span className="block" data-testid="lease-source">
          {t('network.lookup.leaseSource', { source: evidenceTableLabel(source, t) })}
        </span>
      ) : null}
      {source === 'fg_dhcp' ? (
        <span className="block" data-testid="lease-fortigate">
          {lease.lease_seconds !== null && lease.lease_seconds !== undefined
            ? t('network.lookup.fortigateLease', {
                lease: lease.lease_seconds,
                last: lease.last_dhcp_evidence ? formatDateTime(lease.last_dhcp_evidence) : '—',
              })
            : t('network.lookup.fortigateLeaseConfigured', {
                last: lease.last_dhcp_evidence ? formatDateTime(lease.last_dhcp_evidence) : '—',
              })}
        </span>
      ) : null}
    </>
  );
}

/**
 * The interface whose session record gave a srcmac chain its MAC: the chain's own `srcmac_only`
 * names it (an internal-mode chain is shown without a session row), else the session's `srcintf`.
 */
function srcmacInterface(chain: AttributionChain, traffic: TrafficInfo | null): string | null {
  const named = chain.explanations.find((item) => item.key === 'srcmac_only')?.params.interface;
  return typeof named === 'string' && named !== '' ? named : (traffic?.srcintf ?? null);
}

/** One attribution chain, step by step, every step with its evidence (spec 9.4). */
function ChainSteps({
  chain,
  traffic,
  onOpen,
}: {
  chain: AttributionChain;
  traffic: TrafficInfo | null;
  onOpen: OpenEvidence;
}) {
  const { t } = useTranslation();
  const lease = chain.lease;
  const device = chain.device;
  const person = chain.person;
  const assignment = person ? assignmentText(person, t) : null;
  const personDetail = person?.title
    ? visibleText(person.title)
    : chain.department_source === 'vlan'
      ? t('lookup.chain.departmentFromVlan')
      : null;
  return (
    <ol className="flex flex-col">
      {traffic?.transip ? (
        <Step
          icon={Globe}
          label={t('lookup.chain.wan')}
          value={<span className="font-mono">{endpoint(traffic.transip, traffic.transport)}</span>}
          onOpen={onOpen}
        />
      ) : null}
      <Step
        icon={Network}
        label={t('lookup.chain.internalIp')}
        value={
          <span className="font-mono">{endpoint(chain.internal_ip, traffic?.srcport ?? null)}</span>
        }
        detail={
          chain.vlan !== null
            ? t('lookup.chain.vlan', { vlan: chain.vlan, name: chain.vlan_name ?? '—' })
            : undefined
        }
        onOpen={onOpen}
      />
      {chain.mac_source === 'srcmac' ? (
        <Step
          icon={ArrowRight}
          label={t('network.lookup.srcmacStep')}
          value={
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-mono">MAC {device?.mac ?? traffic?.srcmac ?? '—'}</span>
              <Badge tone="warning" icon={ShieldQuestionMark}>
                {t('network.lookup.srcmacBadge')}
              </Badge>
            </span>
          }
          detail={t('network.lookup.srcmacDetail', {
            interface: srcmacInterface(chain, traffic) ?? '—',
          })}
          onOpen={onOpen}
        />
      ) : (
        <Step
          icon={ArrowRight}
          label={t('lookup.chain.lease')}
          value={
            lease ? (
              <span className="font-mono">
                MAC {lease.mac}
                {lease.hostname ? ` (${visibleText(lease.hostname)})` : ''}
              </span>
            ) : (
              t('lookup.chain.noLease')
            )
          }
          detail={lease ? <LeaseDetail lease={lease} /> : undefined}
          confidence={lease?.confidence}
          evidence={lease?.evidence}
          notes={chain.explanations.filter((item) => LEASE_STEP_EXPLANATIONS.includes(item.key))}
          onOpen={onOpen}
        />
      )}
      <Step
        icon={MonitorSmartphone}
        label={t('lookup.chain.device')}
        value={
          device ? (
            <span>
              {device.name ? `"${visibleText(device.name)}"` : t('lookup.chain.unnamedDevice')}
              {device.type
                ? ` · ${t(`deviceTypes.${device.type}`, { defaultValue: device.type })}`
                : ''}
            </span>
          ) : (
            t('lookup.chain.noDevice')
          )
        }
        detail={
          device
            ? [
                device.vendor ? visibleText(device.vendor) : null,
                device.hostnames.length > 0
                  ? t('lookup.chain.hostnames', {
                      names: device.hostnames.map(visibleText).join(', '),
                    })
                  : null,
                device.randomized ? t('lookup.chain.randomized') : null,
                device.infrastructure ? t('lookup.chain.infrastructure') : null,
              ]
                .filter(Boolean)
                .join(' · ') || undefined
            : undefined
        }
        confidence={device?.confidence}
        evidence={device?.evidence}
        onOpen={onOpen}
      />
      <Step
        icon={person ? User : Building2}
        label={t('lookup.chain.person')}
        value={visibleText(personText(chain, t))}
        detail={
          personDetail || assignment ? (
            <>
              {personDetail ? <span className="block">{personDetail}</span> : null}
              {assignment ? (
                <span className="block" data-testid="person-assignment">
                  {assignment}
                </span>
              ) : null}
            </>
          ) : undefined
        }
        confidence={person?.confidence}
        evidence={person?.evidence}
        onOpen={onOpen}
      />
    </ol>
  );
}

/**
 * An attribution chain. With an IP conflict every possible chain is shown side by side with the
 * same weight, under a red warning — the page never picks one device or person silently.
 */
export function ChainView({
  chain,
  traffic,
  onOpen,
}: {
  chain: AttributionChain;
  traffic: TrafficInfo | null;
  onOpen: OpenEvidence;
}) {
  const { t } = useTranslation();
  const all = [chain, ...chain.alternatives];
  const conflict = chain.alternatives.length > 0;
  const optionLabel = (item: AttributionChain, index: number) =>
    item.mac_source === 'srcmac'
      ? t('network.lookup.srcmacOption', { number: index + 1 })
      : t('lookup.conflict.option', { number: index + 1 });
  // A srcmac that differs from the lease's MAC (D10): the same side by side view, its own words.
  // Two leases on the IP keep the IP conflict's own statement beside it.
  const srcmac = chain.alternatives.some((item) => item.mac_source === 'srcmac');
  const leaseConflict =
    !srcmac || all.some((item) => item.explanations.some((note) => note.key === 'ip_conflict'));
  const leaseChains = all.filter((item) => item.mac_source !== 'srcmac').length;
  // The lease-step texts stand beside the lease step; the IP conflict's under its header.
  const above = (item: AttributionChain, header: boolean) =>
    item.explanations.filter(
      (explanation) =>
        !(header && explanation.key === 'ip_conflict') &&
        !LEASE_STEP_EXPLANATIONS.includes(explanation.key),
    );
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-13 font-medium text-foreground">{t('lookup.chain.title')}</span>
        <ConfidenceBadge confidence={chain.confidence} />
      </div>
      {conflict ? (
        <div className="flex flex-col gap-3" data-testid="ip-conflict">
          {leaseConflict ? (
            <Alert tone="danger" title={t('lookup.conflict.title', { count: leaseChains })}>
              {t('lookup.conflict.body')}
            </Alert>
          ) : null}
          {srcmac ? (
            <Alert
              tone="danger"
              title={t('network.lookup.srcmacConflictTitle', { count: all.length })}
            >
              {t('network.lookup.srcmacConflictBody')}
            </Alert>
          ) : null}
          <div className="grid gap-3 xl:grid-cols-2">
            {all.map((item, index) => (
              <section
                key={`${item.lease?.mac ?? item.device?.mac ?? 'none'}-${index}`}
                aria-label={optionLabel(item, index)}
                className="flex flex-col gap-3 rounded-lg border border-danger/40 p-3"
              >
                <h4 className="text-13 font-semibold text-foreground">
                  {optionLabel(item, index)}
                </h4>
                <p className="font-mono text-xs wrap-anywhere text-muted-strong">
                  {chainSummary(item, traffic, t)}
                </p>
                <ExplanationList explanations={above(item, leaseConflict)} />
                <ChainSteps chain={item} traffic={traffic} onOpen={onOpen} />
              </section>
            ))}
          </div>
        </div>
      ) : (
        <>
          <p
            className="font-mono text-xs wrap-anywhere text-muted-strong"
            data-testid="chain-summary"
          >
            {chainSummary(chain, traffic, t)}
          </p>
          <ExplanationList explanations={above(chain, false)} />
          <ChainSteps chain={chain} traffic={traffic} onOpen={onOpen} />
        </>
      )}
    </div>
  );
}
