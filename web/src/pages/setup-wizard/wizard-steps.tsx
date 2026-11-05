import {
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleMinus,
  ListChecks,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ConfigProblems } from '@/components/admin/agent-status';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import type { AdminConfig } from '@/lib/settings-types';

import { StepBadge } from '../settings/kamusm-steps';

import {
  CHECKED_STEPS,
  firewallNotes,
  siteProblems,
  type Arrival,
  type CheckedStepId,
  type StepState,
} from './wizard-model';

/** A step's state: "Tamam" or "Eksik" (icon and text). */
export function WizardBadge({ state }: { state: StepState }) {
  return <StepBadge status={state.done ? 'ok' : 'missing'} />;
}

/** The summary's badge in the step list: how many steps are done. */
export function SummaryBadge({ states }: { states: Record<CheckedStepId, StepState> }) {
  const { t } = useTranslation();
  const done = CHECKED_STEPS.filter((step) => states[step].done).length;
  return (
    <Badge tone="neutral" icon={ListChecks}>
      {t('setupWizard.doneCount', { done, total: CHECKED_STEPS.length })}
    </Badge>
  );
}

/** What a step still lacks ("Eksik olan"), or that nothing is missing. */
export function MissingList({ state }: { state: StepState }) {
  const { t } = useTranslation();
  if (state.done)
    return <p className="text-13 text-muted-strong">{t('setupWizard.nothingMissing')}</p>;
  return (
    <div className="flex flex-col gap-1" data-testid="wizard-missing">
      <h3 className="text-13 font-semibold text-foreground">{t('setupWizard.missingTitle')}</h3>
      <ul className="list-disc pl-5 text-13 text-foreground">
        {state.missing.map((item) => (
          <li key={item.key}>{t(item.key, { defaultValue: item.key, ...item.values })}</li>
        ))}
      </ul>
    </div>
  );
}

/** "Site ayarları": the problems of the site name and the time servers, every level. */
export function SiteStep({ config }: { config: AdminConfig }) {
  return <ConfigProblems problems={siteProblems(config.problems)} />;
}

const ARRIVAL: Record<
  'off' | 'unknown' | 'arrived' | 'waiting',
  { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon; key: string }
> = {
  off: { tone: 'neutral', icon: CircleMinus, key: 'setupWizard.sources.disabled' },
  unknown: { tone: 'neutral', icon: CircleHelp, key: 'setupWizard.sources.arrivalUnknown' },
  arrived: { tone: 'success', icon: CircleCheck, key: 'setupWizard.sources.arrived' },
  waiting: { tone: 'warning', icon: CircleDashed, key: 'setupWizard.sources.notArrived' },
};

function arrivalStyle(item: Arrival) {
  if (!item.enabled) return ARRIVAL.off;
  if (item.arrived === null) return ARRIVAL.unknown;
  return item.arrived ? ARRIVAL.arrived : ARRIVAL.waiting;
}

/**
 * "Log kaynakları": the firewall note by each enabled source's firewall state, where the device
 * settings are described (no device commands here, no pre-filled device list) and, per
 * configured source, whether its data arrived (`GET /sources`).
 */
export function SourcesStep({
  config,
  arrivals,
  loading,
}: {
  config: AdminConfig;
  arrivals: readonly Arrival[];
  /** `GET /sources` is still loading. */
  loading: boolean;
}) {
  const { t } = useTranslation();
  const sources = config.sections.sources;
  const notes = firewallNotes(sources);
  return (
    <div className="flex flex-col gap-3">
      {notes.map((note) => (
        <Alert
          key={note}
          tone={note === 'managed' ? 'info' : 'warning'}
          assertive={false}
          data-testid={`wizard-firewall-${note}`}
        >
          {t(
            note === 'managed'
              ? 'setupWizard.sources.firewallManaged'
              : 'setupWizard.sources.firewallNotManaged',
          )}
        </Alert>
      ))}
      <p className="flex items-start gap-2 text-13 text-foreground">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-ink" />
        {t('setupWizard.sources.deviceSetup')}
      </p>
      {sources.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h3 className="text-13 font-semibold text-foreground">
            {t('setupWizard.sources.configured')}
          </h3>
          <ul className="flex flex-col divide-y divide-border" data-testid="wizard-arrivals">
            {arrivals.map((item) => {
              const address = sources.find((source) => source.name === item.name)?.address;
              const style = arrivalStyle(item);
              return (
                <li
                  key={item.name}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-13"
                >
                  <span className="flex min-w-0 flex-col">
                    <span className="font-medium text-foreground">{item.name}</span>
                    {address ? (
                      <span className="font-mono text-xs text-muted-strong">{address}</span>
                    ) : null}
                  </span>
                  {loading && item.enabled ? (
                    <Skeleton className="h-6 w-28" />
                  ) : (
                    <span className="flex flex-wrap items-center gap-2">
                      <Badge tone={style.tone} icon={style.icon}>
                        {t(style.key)}
                      </Badge>
                      {item.enabled && item.lastMessage ? (
                        <span className="text-xs text-muted-strong">
                          {t('setupWizard.sources.lastMessage', {
                            time: formatDateTime(item.lastMessage),
                          })}
                        </span>
                      ) : null}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** The Kamu SM steps: the test server's stamps have no legal validity (verify on site). */
export function KamusmNote() {
  const { t } = useTranslation();
  return (
    <Alert tone="warning" assertive={false} data-testid="wizard-kamusm-legal">
      {t('setupWizard.kamusm.legal')}
    </Alert>
  );
}

/**
 * "Özet": every step with its state, the configuration's problems (every level, in the interface
 * language), the checklist after installation, and Uyum Durumu for those who may open it.
 */
export function SummaryStep({
  config,
  states,
  onNavigate,
}: {
  config: AdminConfig;
  states: Record<CheckedStepId, StepState>;
  /** A step's link was followed (the wizard moves the focus to it). */
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { can } = useAuth();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-13 font-semibold text-foreground">{t('setupWizard.summary.steps')}</h3>
        <ol className="flex flex-col divide-y divide-border" data-testid="wizard-summary-steps">
          {CHECKED_STEPS.map((step, index) => (
            <li key={step} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <Link
                to={{ search: `?step=${step}` }}
                replace
                onClick={onNavigate}
                className="text-13 font-medium text-primary-ink underline underline-offset-2"
              >
                {index + 1}. {t(`setupWizard.steps.${step}.title`)}
              </Link>
              <WizardBadge state={states[step]} />
            </li>
          ))}
        </ol>
      </div>
      {config.problems.length > 0 ? (
        <ConfigProblems problems={config.problems} />
      ) : (
        <p className="text-13 text-muted-strong">{t('setupWizard.summary.noProblems')}</p>
      )}
      <p className="text-13 text-foreground" data-testid="wizard-checklist">
        {t('setupWizard.summary.checklist')}
      </p>
      {can('compliance.read') ? (
        <p>
          <Link
            to="/compliance"
            className="text-13 font-medium text-primary-ink underline underline-offset-2"
          >
            {t('setupWizard.summary.compliance')}
          </Link>
        </p>
      ) : null}
    </div>
  );
}
