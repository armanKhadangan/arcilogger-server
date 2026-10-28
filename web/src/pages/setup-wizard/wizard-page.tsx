import { ArrowRight, RefreshCw, Settings2 } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { SettingsCard, SettingsFrame } from '@/components/admin/settings-frame';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useAdminConfig, useAdminRequests } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { useSourcesOverview } from '@/lib/sources-hooks';
import type { AdminConfig } from '@/lib/settings-types';
import { cn } from '@/lib/utils';

import {
  arrivals,
  firstOpenStep,
  isWizardStep,
  nextStep,
  STEP_LINKS,
  verifiedStampTest,
  WIZARD_STEPS,
  wizardStates,
  type Arrival,
  type CheckedStepId,
  type StepState,
  type WizardStepId,
} from './wizard-model';
import { endWizardMode } from './wizard-mode';
import {
  KamusmNote,
  MissingList,
  SiteStep,
  SourcesStep,
  SummaryBadge,
  SummaryStep,
  WizardBadge,
} from './wizard-steps';

/**
 * "Ayarlar'da aç" (the sources page only with `sources.manage`, as its route) and "Bu adımı
 * şimdilik atla".
 */
function StepActions({ step, onNavigate }: { step: WizardStepId; onNavigate: () => void }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const opens = step === 'sources' && !can('sources.manage') ? undefined : STEP_LINKS[step];
  if (step === 'summary') return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {opens ? (
        <Button asChild>
          <Link to={opens}>
            <Settings2 aria-hidden="true" />
            {t('setupWizard.open')}
          </Link>
        </Button>
      ) : null}
      <Button asChild variant="secondary">
        <Link to={{ search: `?step=${nextStep(step)}` }} replace onClick={onNavigate}>
          {t('setupWizard.skip')}
          <ArrowRight aria-hidden="true" />
        </Link>
      </Button>
    </div>
  );
}

/**
 * İlk kurulum sihirbazı (`/settings/setup`, `settings.manage`; Wave 4 contract 2.8.4): the steps
 * after the first administrator's account, as a checklist that opens the Ayarlar pages in wizard
 * mode (their way back leads here). Each step's state comes from the configuration, the stamp
 * tests of the request history and, for the sources, `GET /sources`; nothing is stored, a skipped
 * step stays "Eksik". Without `?step=` the first step not done is shown; a step changed from inside
 * the panel moves the focus to it. Opening the wizard ends wizard mode.
 */
export function WizardPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const config = useAdminConfig();
  const tests = useAdminRequests('action.tsa_test');
  const panel = useRef<HTMLDivElement>(null);
  const focusPanel = useRef(false);
  useEffect(() => {
    endWizardMode();
  }, []);
  const tested = verifiedStampTest(tests.data?.pages.flatMap((page) => page.items) ?? []);
  const states = useMemo(
    () => (config.data ? wizardStates({ config: config.data, tested }) : undefined),
    [config.data, tested],
  );
  const requested = params.get('step');
  const step: WizardStepId = isWizardStep(requested)
    ? requested
    : states
      ? firstOpenStep(states)
      : 'admin';
  // Data arrival is shown on the sources step only; its state comes from the configuration.
  const overview = useSourcesOverview(can('sources.read') && step === 'sources');
  const found = useMemo(
    () => (config.data ? arrivals(config.data.sections.sources, overview.data?.items) : []),
    [config.data, overview.data],
  );
  const toPanel = () => {
    focusPanel.current = true;
  };

  useEffect(() => {
    if (focusPanel.current) {
      focusPanel.current = false;
      panel.current?.focus();
    }
  }, [step]);

  return (
    <SettingsFrame
      title={t('setupWizard.title')}
      description={t('setupWizard.description')}
      problems={false}
    >
      {config.isPending || tests.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger" title={t('setupWizard.loadFailed')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(config.error)}
            <Button size="sm" variant="secondary" onClick={() => void config.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : states ? (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <nav aria-label={t('setupWizard.stepsLabel')}>
            <ol className="flex flex-col gap-1" data-testid="wizard-steps">
              {WIZARD_STEPS.map((item, index) => {
                const current = item === step;
                return (
                  <li key={item}>
                    <Link
                      to={{ search: `?step=${item}` }}
                      replace
                      aria-current={current ? 'step' : undefined}
                      className={cn(
                        'focus-visible:ring-ring flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-13 focus-visible:ring-2 focus-visible:outline-none',
                        current
                          ? 'border-primary bg-primary-soft font-medium text-foreground'
                          : 'border-transparent text-foreground hover:bg-subtle',
                      )}
                    >
                      <span>
                        {index + 1}. {t(`setupWizard.steps.${item}.title`)}
                      </span>
                      {item === 'summary' ? (
                        <SummaryBadge states={states} />
                      ) : (
                        <WizardBadge state={states[item]} />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ol>
          </nav>
          <div ref={panel} tabIndex={-1} className="flex min-w-0 flex-col gap-4 outline-none">
            <SettingsCard
              title={`${WIZARD_STEPS.indexOf(step) + 1}. ${t(`setupWizard.steps.${step}.title`)}`}
              description={t(`setupWizard.steps.${step}.purpose`)}
              testId={`wizard-step-${step}`}
              actions={step === 'summary' ? undefined : <WizardBadge state={states[step]} />}
            >
              <StepBody
                step={step}
                config={config.data}
                states={states}
                arrivals={found}
                sourcesLoading={overview.isPending && overview.fetchStatus !== 'idle'}
                onNavigate={toPanel}
              />
              <StepActions step={step} onNavigate={toPanel} />
            </SettingsCard>
          </div>
        </div>
      ) : null}
    </SettingsFrame>
  );
}

function StepBody({
  step,
  config,
  states,
  arrivals: found,
  sourcesLoading,
  onNavigate,
}: {
  step: WizardStepId;
  config: AdminConfig;
  states: Record<CheckedStepId, StepState>;
  arrivals: readonly Arrival[];
  sourcesLoading: boolean;
  onNavigate: () => void;
}) {
  switch (step) {
    case 'summary':
      return <SummaryStep config={config} states={states} onNavigate={onNavigate} />;
    case 'site':
      return (
        <>
          <MissingList state={states.site} />
          <SiteStep config={config} />
        </>
      );
    case 'sources':
      return (
        <>
          <MissingList state={states.sources} />
          <SourcesStep config={config} arrivals={found} loading={sourcesLoading} />
        </>
      );
    case 'kamusm_test':
    case 'kamusm_production':
      return (
        <>
          <MissingList state={states[step]} />
          <KamusmNote />
        </>
      );
    default:
      return <MissingList state={states[step]} />;
  }
}
