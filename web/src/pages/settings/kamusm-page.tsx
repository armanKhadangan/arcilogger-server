import { useId, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SectionActions } from '@/components/admin/section-actions';
import { SECRET_KEPT, secretChanged, type SecretEdit } from '@/components/admin/secret-edit';
import {
  SettingsCard,
  SettingsFrame,
  UnsavedChangesGuard,
} from '@/components/admin/settings-frame';
import { useMachineSection } from '@/components/admin/use-machine-section';
import { flowState, useServerValues } from '@/components/admin/use-section-flow';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { editable } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import { useAdminRequests } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import type { AdminRequestOut, TsaSection, TsaServer, TsaTestResult } from '@/lib/settings-types';
import { cn } from '@/lib/utils';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { CreditQuery } from '../sealing/credits-card';

import {
  STEPS,
  isStep,
  stepStatus,
  tsaBody,
  tsaForm,
  validateCustomer,
  validateNumbers,
  validatePassword,
  type Credentials,
  type StepId,
  type TsaForm,
} from './kamusm-model';
import {
  CredentialsStep,
  CreditStep,
  JavaStep,
  ProductionStep,
  SealView,
  StepBadge,
  TestStep,
  TsaRefusal,
} from './kamusm-steps';
import { RootsStep, ZamaneStep } from './kamusm-uploads';

const EMPTY: TsaForm = { java: null, threshold: '500', timeout: '60' };
const KEPT: Credentials = { customer: SECRET_KEPT, password: SECRET_KEPT, clear: false };

type TsaField = 'java' | 'threshold' | 'timeout' | 'customer' | 'password';

/** The body paths the API may name for a field (422 `details`). */
const PATHS: Record<TsaField, string[]> = {
  java: ['java', 'tsa.java'],
  threshold: ['credit_alert_threshold', 'tsa.credit_alert_threshold'],
  timeout: ['timeout_seconds', 'tsa.timeout_seconds'],
  customer: ['customer', 'kamusm.customer'],
  password: ['password', 'kamusm.password'],
};
const BOUNDS: Record<TsaField, Record<string, number>> = {
  java: {},
  threshold: { min: 0, max: 1_000_000 },
  timeout: { min: 5, max: 600 },
  customer: {},
  password: {},
};

/** The steps whose values are saved with "Kaydet" (the others act on their own). */
const FORM_STEPS: readonly StepId[] = ['java', 'credentials', 'production', 'credit'];

/** A stamp test that succeeded and was verified (the newest ones; the server decides again). */
function useTested(): boolean {
  const tests = useAdminRequests('action.tsa_test');
  return (
    tests.data?.pages.some((page) =>
      page.items.some(
        (item) =>
          item.state === 'succeeded' && (item.result as TsaTestResult | null)?.verified === true,
      ),
    ) ?? false
  );
}

/** The first step still to do (an error before a missing item), else the first. */
function firstOpen(tsa: TsaSection, tested: boolean): StepId {
  return (
    STEPS.find((step) => stepStatus(step, tsa, tested) === 'error') ??
    STEPS.find((step) => stepStatus(step, tsa, tested) === 'missing') ??
    'java'
  );
}

function credentialErrors(credentials: Credentials): FieldErrors<'customer' | 'password'> {
  const errors: FieldErrors<'customer' | 'password'> = {};
  if (credentials.clear) return errors;
  const check = (edit: SecretEdit, validate: (value: string) => string | undefined) =>
    edit.editing ? validate(edit.value) : undefined;
  const customer = check(credentials.customer, validateCustomer);
  if (customer) errors.customer = customer;
  const password = check(credentials.password, validatePassword);
  if (password) errors.password = password;
  return errors;
}

/**
 * Ayarlar → Kamu SM (contract 2.17, `settings.manage`): the wizard of the Zamane time stamp
 * service, step by step with the state of each from the configuration's `readiness` and
 * `seal_ready`: Java, the Zamane client, the root certificates, the customer credentials, a
 * test stamp, the switch to the production server and the credit alert threshold. The `[seal]`
 * section is shown read-only. A change that would stop a sealer that works now is refused by
 * the server (`tsa_would_break`) with its causes.
 */
export function KamusmPage() {
  const { t } = useTranslation();
  const id = useId();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const section = useMachineSection('tsa');
  const { config, flow } = section;
  const tsa = config.data?.sections.tsa;
  const server = useMemo(() => tsaForm(tsa), [tsa]);
  const form = useServerValues(server, EMPTY);
  const [credentials, setCredentials] = useState<Credentials>(KEPT);
  const [probe, setProbe] = useState(true);
  const [errors, setErrors] = useState<FieldErrors<TsaField>>({});
  const tested = useTested();
  const credentialsDirty =
    credentials.clear ||
    secretChanged(credentials.customer) ||
    secretChanged(credentials.password) ||
    credentials.customer.editing ||
    credentials.password.editing;
  const dirty = form.dirty || credentialsDirty;
  const state = flowState(flow, {
    loading: config.isPending,
    canEdit: section.canEdit,
    agent: section.agent,
    dirty,
    invalid: hasErrors(errors),
    restartRequired: section.restartRequired,
  });
  const locked = !editable(state) || !section.writable;
  const requested = params.get('step');
  const step: StepId = isStep(requested) ? requested : tsa ? firstOpen(tsa, tested) : 'java';
  const values = form.values;

  function change(next: Partial<TsaForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function restore() {
    form.restore();
    setCredentials(KEPT);
    setErrors({});
  }

  function send(options: { server?: TsaServer; confirm?: boolean }) {
    if (!tsa) return;
    const found: FieldErrors<TsaField> = {
      ...validateNumbers(values),
      ...credentialErrors(credentials),
    };
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    void flow.start(tsaBody(tsa, section.etag, values, credentials, { ...options, probe }));
  }

  function settled(request: AdminRequestOut) {
    // The new credentials are stored (or deleted): the fields go back to "Kayıtlı ✓".
    if (request.state === 'active' || request.state === 'partially_active') setCredentials(KEPT);
  }

  const message = (field: TsaField) =>
    fieldMessage(t, flow.problems, errors[field], PATHS[field], BOUNDS[field]);

  function body(tsaSection: TsaSection) {
    const policy = config.data?.policy;
    switch (step) {
      case 'java':
        return (
          <JavaStep
            tsa={tsaSection}
            java={values.java}
            disabled={locked}
            onChange={(java) => {
              change({ java });
            }}
          />
        );
      case 'zamane':
        return policy ? (
          <ZamaneStep
            tsa={tsaSection}
            etag={section.etag}
            writable={section.writable}
            policy={policy}
          />
        ) : null;
      case 'roots':
        return <RootsStep tsa={tsaSection} etag={section.etag} writable={section.writable} />;
      case 'credentials':
        return (
          <CredentialsStep
            tsa={tsaSection}
            credentials={credentials}
            disabled={locked}
            errors={{ customer: message('customer'), password: message('password') }}
            probe={probe}
            onProbe={setProbe}
            onChange={(next) => {
              setCredentials(next);
              setErrors({});
              flow.edit();
            }}
          />
        );
      case 'test':
        return (
          <TestStep
            tsa={tsaSection}
            writable={section.writable}
            perDay={policy?.tsa_test_per_day ?? 0}
          />
        );
      case 'production':
        return (
          <ProductionStep
            tsa={tsaSection}
            tested={tested}
            disabled={locked || dirty}
            blockedHint={dirty ? t('settings.kamusm.production.saveFirst') : undefined}
            onSwitch={(target) => {
              send({ server: target, confirm: target === 'test' });
            }}
          />
        );
      case 'credit':
        return (
          <div className="flex flex-col gap-4">
            <CreditStep
              form={values}
              disabled={locked}
              errors={{ threshold: message('threshold'), timeout: message('timeout') }}
              onChange={change}
            />
            {can('seal.manage') ? <CreditQuery /> : null}
          </div>
        );
    }
  }

  return (
    <SettingsFrame
      title={t('settings.kamusm.title')}
      description={t('settings.kamusm.description')}
    >
      <UnsavedChangesGuard when={dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : tsa ? (
        <>
          {tsa.provider !== 'zamane' ? (
            <Alert tone="info" assertive={false} data-testid="tsa-provider">
              {t('settings.kamusm.provider', { provider: tsa.provider })}
            </Alert>
          ) : null}
          <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
            <nav aria-label={t('settings.kamusm.stepsLabel')}>
              <ol className="flex flex-col gap-1" data-testid="kamusm-steps">
                {STEPS.map((item, index) => {
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
                          {index + 1}. {t(`settings.kamusm.steps.${item}.title`)}
                        </span>
                        <StepBadge status={stepStatus(item, tsa, tested)} />
                      </Link>
                    </li>
                  );
                })}
              </ol>
            </nav>
            <div className="flex min-w-0 flex-col gap-4">
              <SettingsCard
                title={`${STEPS.indexOf(step) + 1}. ${t(`settings.kamusm.steps.${step}.title`)}`}
                description={t(`settings.kamusm.steps.${step}.description`)}
                testId={`kamusm-step-${step}`}
                actions={<StepBadge status={stepStatus(step, tsa, tested)} />}
              >
                {body(tsa)}
              </SettingsCard>
              {FORM_STEPS.includes(step) ? (
                <SectionActions
                  state={state}
                  flow={flow}
                  dirty={dirty}
                  onSave={() => {
                    send({});
                  }}
                  onRestore={restore}
                  onReload={() => {
                    void config.refetch().then((result) => {
                      const fresh = tsaForm(result.data?.sections.tsa);
                      if (fresh) form.setValues(fresh);
                      setCredentials(KEPT);
                      setErrors({});
                    });
                  }}
                  probingText={t('settings.kamusm.probing')}
                  onTerminal={settled}
                  failureDetails={<TsaRefusal error={flow.failure} />}
                />
              ) : null}
              {dirty && !FORM_STEPS.includes(step) ? (
                <Alert tone="info" assertive={false}>
                  {t('settings.kamusm.unsaved')}
                </Alert>
              ) : null}
              <SettingsCard
                title={t('settings.kamusm.seal.title')}
                description={t('settings.kamusm.seal.description')}
                testId="kamusm-seal"
              >
                <SealView config={config.data} />
              </SettingsCard>
            </div>
          </div>
        </>
      ) : null}
      <ChangeReviewDialog
        open={flow.phase === 'confirm' || flow.phase === 'submitting'}
        onOpenChange={(open) => {
          if (!open) flow.dismiss();
        }}
        check={flow.check}
        busy={flow.phase === 'submitting'}
        onConfirm={() => void flow.confirm()}
      />
    </SettingsFrame>
  );
}
