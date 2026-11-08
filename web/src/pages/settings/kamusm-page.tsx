import { useId, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { HeldChanges } from '@/components/admin/held-change';
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
import { errorMessage, isApiError } from '@/lib/api';
import { useAdminRequests, useCredits } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import type { AdminRequestOut, TsaSection, TsaTestResult } from '@/lib/settings-types';
import { cn } from '@/lib/utils';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { CreditQuery } from '../sealing/credits-card';

import {
  CRL_MAX_AGE_DAYS,
  CRL_URLS_MAX,
  RENEW_BEFORE_DAYS,
  STEPS,
  TESTS_PER_DAY,
  TURKTRUST_KEPT,
  TURKTRUST_TEXTS,
  causeKey,
  choiceProvider,
  evidenceChanges,
  firstOpen,
  isStep,
  providerText,
  stepStatus,
  testsPerDay,
  tsaBody,
  toTurktrust,
  tsaForm,
  turktrustChanged,
  unplacedCauses,
  validateCustomer,
  validateEvidence,
  validateNumbers,
  validatePassword,
  validateTurktrustPassword,
  validateTurktrustUsername,
  type Credentials,
  type ProviderText,
  type ServerChoice,
  type StepId,
  type TsaForm,
} from './kamusm-model';
import {
  CredentialsStep,
  CreditStep,
  EvidenceFields,
  JavaStep,
  ProductionStep,
  SealView,
  StepBadge,
  TestStep,
  TsaRefusal,
} from './kamusm-steps';
import { RootsStep, ZamaneStep } from './kamusm-uploads';

const EMPTY: TsaForm = { java: null, threshold: '500', timeout: '60', testsPerDay: '10' };
const KEPT: Credentials = {
  customer: SECRET_KEPT,
  password: SECRET_KEPT,
  clear: false,
  turktrust: TURKTRUST_KEPT,
};

type TsaField =
  | 'java'
  | 'threshold'
  | 'timeout'
  | 'testsPerDay'
  | 'customer'
  | 'password'
  | 'turktrustUsername'
  | 'turktrustPassword'
  | 'renewBeforeDays'
  | 'crlUrls'
  | 'crlMaxAgeDays';

/**
 * The body paths the API may name for a field (422 `details`), W5-T's TÜRKTRUST fields and
 * re-stamping and revocation-list keys included (`TsaBody`).
 */
const PATHS: Record<TsaField, string[]> = {
  java: ['java', 'tsa.java'],
  threshold: ['credit_alert_threshold', 'tsa.credit_alert_threshold'],
  timeout: ['timeout_seconds', 'tsa.timeout_seconds'],
  testsPerDay: ['tests_per_day', 'tsa.tests_per_day'],
  customer: ['customer', 'kamusm.customer'],
  password: ['password', 'kamusm.password'],
  turktrustUsername: ['turktrust_username', 'turktrust.username'],
  turktrustPassword: ['turktrust_password', 'turktrust.password'],
  renewBeforeDays: ['renew_before_days', 'tsa.renew_before_days'],
  crlUrls: ['crl_urls', 'tsa.crl_urls'],
  crlMaxAgeDays: ['crl_max_age_days', 'tsa.crl_max_age_days'],
};
const SHOWN = Object.values(PATHS).flat();
const BOUNDS: Record<TsaField, Record<string, number>> = {
  java: {},
  threshold: { min: 0, max: 1_000_000 },
  timeout: { min: 5, max: 600 },
  testsPerDay: TESTS_PER_DAY,
  customer: {},
  password: {},
  turktrustUsername: {},
  turktrustPassword: {},
  renewBeforeDays: RENEW_BEFORE_DAYS,
  crlUrls: { max: CRL_URLS_MAX },
  crlMaxAgeDays: CRL_MAX_AGE_DAYS,
};

/** The steps whose values are saved with "Kaydet" (the others act on their own). */
const FORM_STEPS: readonly StepId[] = ['java', 'credentials', 'production', 'credit'];

/**
 * What `send` adds to the form: the server to switch to, and the confirmation of a switch back
 * to the test server or to plain HTTP.
 */
interface SendOptions {
  server?: ServerChoice;
  confirm?: boolean;
}

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

type CredentialField = 'customer' | 'password' | 'turktrustUsername' | 'turktrustPassword';

/**
 * The errors of the credentials a change sends. TÜRKTRUST's are checked only when the change goes
 * to TÜRKTRUST (`toTurktrust`): a field left behind on another server is neither checked nor sent.
 */
function credentialErrors(
  credentials: Credentials,
  turktrustToo: boolean,
): FieldErrors<CredentialField> {
  const errors: FieldErrors<CredentialField> = {};
  const check = (edit: SecretEdit, validate: (value: string) => string | undefined) =>
    edit.editing ? validate(edit.value) : undefined;
  if (turktrustToo) {
    const turktrust = credentials.turktrust ?? TURKTRUST_KEPT;
    const username = check(turktrust.username, validateTurktrustUsername);
    if (username) errors.turktrustUsername = username;
    const secret = check(turktrust.password, validateTurktrustPassword);
    if (secret) errors.turktrustPassword = secret;
  }
  if (credentials.clear) return errors;
  const customer = check(credentials.customer, validateCustomer);
  if (customer) errors.customer = customer;
  const password = check(credentials.password, validatePassword);
  if (password) errors.password = password;
  return errors;
}

/** A step's description in the words of the provider in use. */
function stepDescription(provider: string, step: StepId): string {
  const key = `steps.${step}.description`;
  return (TURKTRUST_TEXTS as readonly string[]).includes(key)
    ? providerText(provider, key as ProviderText)
    : `settings.kamusm.${key}`;
}

/**
 * Ayarlar → Zaman damgası (contract 2.17, `settings.manage`; the route stays `/settings/kamusm`):
 * the wizard of the time-stamp service, step by step with the state of each from the
 * configuration's `readiness` and `seal_ready`: Java, the Zamane client, the root certificates,
 * the credentials, a test stamp, the server (Kamu SM test or production, TÜRKTRUST; Wave 5
 * contract 2.4) and the credit alert threshold, with the re-stamping window and the revocation
 * lists (Wave 5 contract 2.5) once the server reports them. Java and the Zamane client are
 * Kamu SM's only and read "Gerekmez" while TÜRKTRUST is in use. The `[seal]` section is shown
 * read-only. A change that would stop a sealer that works now is refused by
 * the server (`tsa_would_break`) with its causes. A cause no step shows (a moved secret store,
 * contract D0-2) is named above the steps, and so is a held change of the section (an unknown
 * Zamane client waiting for the console) with its countdown and "İptal et".
 */
export function KamusmPage() {
  const { t } = useTranslation();
  const id = useId();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const section = useMachineSection('tsa', { fields: SHOWN });
  const { config, flow } = section;
  const tsa = config.data?.sections.tsa;
  const server = useMemo(() => tsaForm(tsa), [tsa]);
  const form = useServerValues(server, EMPTY);
  const [credentials, setCredentials] = useState<Credentials>(KEPT);
  const [probe, setProbe] = useState(true);
  const [errors, setErrors] = useState<FieldErrors<TsaField>>({});
  // The request the Zamane or the roots step started; the step shows it while it is open.
  const [started, setStarted] = useState<{ step: StepId; id: string } | null>(null);
  // "Yeniden dene" sends what the last attempt sent (a switch of the server included).
  const lastSend = useRef<SendOptions>({});
  // The provider the last change went to (the server it switched to, else the one in use).
  const [sentTo, setSentTo] = useState('');
  // The server chosen in step 6 and not switched to yet: kept while another step is open.
  const [chosen, setChosen] = useState<ServerChoice | null>(null);
  const tested = useTested();
  // "Kontör sorgula" only for a provider with a credit query (C3, C21: the flag decides).
  const credits = useCredits();
  const kamusmDirty =
    credentials.clear ||
    secretChanged(credentials.customer) ||
    secretChanged(credentials.password) ||
    credentials.customer.editing ||
    credentials.password.editing;
  const turktrustDirty = turktrustChanged(credentials);
  const dirty = form.dirty || kamusmDirty || turktrustDirty;
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
  const unplaced = tsa ? unplacedCauses(tsa) : [];
  const provider = tsa?.provider ?? '';
  const title =
    provider === 'zamane' || provider === 'turktrust'
      ? t(`settings.kamusm.titles.${provider}`)
      : t('settings.kamusm.title');

  function change(next: Partial<TsaForm>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function restore() {
    form.restore();
    setCredentials(KEPT);
    setChosen(null);
    setErrors({});
  }

  function send(options: SendOptions) {
    if (!tsa) return;
    lastSend.current = options;
    setSentTo(options.server ? choiceProvider(options.server) : tsa.provider);
    const found: FieldErrors<TsaField> = {
      ...validateNumbers(values),
      ...(values.evidence ? validateEvidence(values.evidence) : {}),
      ...credentialErrors(credentials, toTurktrust(tsa, options.server ?? chosen ?? undefined)),
    };
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    void flow.start(
      tsaBody(tsa, section.etag, values, credentials, {
        ...options,
        chosen: chosen ?? undefined,
        probe,
      }),
    );
  }

  function settled(request: AdminRequestOut) {
    // The new credentials are stored (or deleted): the fields go back to "Kayıtlı ✓"; after a
    // switch the server in use is the one chosen.
    if (request.state === 'active' || request.state === 'partially_active') {
      setCredentials(KEPT);
      if (lastSend.current.server) setChosen(null);
    }
  }

  const message = (field: TsaField) =>
    fieldMessage(t, flow.problems, errors[field], PATHS[field], BOUNDS[field]);

  function body(tsaSection: TsaSection) {
    const policy = config.data?.policy;
    // Java and the Zamane client are kept for a way back to Kamu SM; TÜRKTRUST needs neither.
    const unused =
      tsaSection.provider === 'turktrust' ? (
        <Alert tone="info" assertive={false} className="mb-4" data-testid="tsa-step-unused">
          {t('settings.kamusm.turktrust.unusedStep')}
        </Alert>
      ) : null;
    switch (step) {
      case 'java':
        return (
          <>
            {unused}
            <JavaStep
              tsa={tsaSection}
              java={values.java}
              disabled={locked}
              onChange={(java) => {
                change({ java });
              }}
            />
          </>
        );
      case 'zamane':
        return policy ? (
          <>
            {unused}
            <ZamaneStep
              tsa={tsaSection}
              etag={section.etag}
              writable={section.writable}
              policy={policy}
              requestId={started?.step === 'zamane' ? started.id : null}
              onStarted={(requestId) => {
                setStarted({ step: 'zamane', id: requestId });
              }}
            />
          </>
        ) : null;
      case 'roots':
        return (
          <RootsStep
            tsa={tsaSection}
            etag={section.etag}
            writable={section.writable}
            requestId={started?.step === 'roots' ? started.id : null}
            onStarted={(requestId) => {
              setStarted({ step: 'roots', id: requestId });
            }}
          />
        );
      case 'credentials':
        return (
          <CredentialsStep
            tsa={tsaSection}
            credentials={credentials}
            disabled={locked}
            errors={{
              customer: message('customer'),
              password: message('password'),
              turktrustUsername: message('turktrustUsername'),
              turktrustPassword: message('turktrustPassword'),
            }}
            probe={probe}
            onProbe={setProbe}
            onChange={(next) => {
              setCredentials(next);
              // TÜRKTRUST's credentials edited for the provider in use: no other server is chosen.
              if (tsaSection.provider === 'turktrust' && chosen && !toTurktrust(tsaSection, chosen))
                setChosen(null);
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
            perDay={testsPerDay(tsaSection, policy?.tsa_test_per_day)}
          />
        );
      case 'production':
        return (
          <ProductionStep
            tsa={tsaSection}
            tested={tested}
            disabled={locked}
            unsaved={form.dirty || kamusmDirty}
            turktrustUnsaved={turktrustDirty}
            picked={chosen}
            onPick={setChosen}
            credentials={credentials}
            onCredentials={(next) => {
              setCredentials(next);
              setErrors({});
              flow.edit();
            }}
            errors={{
              turktrustUsername: message('turktrustUsername'),
              turktrustPassword: message('turktrustPassword'),
            }}
            onSwitch={(target) => {
              send({ server: target, confirm: target === 'test' || target === 'turktrust-http' });
            }}
            httpRefusal={
              isApiError(flow.failure, 'tsa_plain_http_confirm_required') ? flow.failure : null
            }
          />
        );
      case 'credit':
        return (
          <div className="flex flex-col gap-4">
            <CreditStep
              form={values}
              disabled={locked}
              errors={{
                threshold: message('threshold'),
                timeout: message('timeout'),
                testsPerDay: message('testsPerDay'),
              }}
              ceiling={policy?.tsa_test_per_day}
              onChange={change}
            />
            {values.evidence ? (
              <EvidenceFields
                id={id}
                form={values.evidence}
                disabled={locked}
                changed={Object.keys(evidenceChanges(tsaSection, values)).length > 0}
                errors={{
                  renewBeforeDays: message('renewBeforeDays'),
                  crlUrls: message('crlUrls'),
                  crlMaxAgeDays: message('crlMaxAgeDays'),
                }}
                onChange={(next) => {
                  const evidence = values.evidence;
                  if (evidence) change({ evidence: { ...evidence, ...next } });
                }}
              />
            ) : null}
            {can('seal.manage') && credits.data?.supported !== false ? <CreditQuery /> : null}
          </div>
        );
    }
  }

  return (
    <SettingsFrame title={title} description={t('settings.kamusm.description')}>
      <UnsavedChangesGuard when={dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : tsa ? (
        <>
          {tsa.provider !== 'zamane' && tsa.provider !== 'turktrust' ? (
            <Alert tone="info" assertive={false} data-testid="tsa-provider">
              {t('settings.kamusm.provider', { provider: tsa.provider })}
            </Alert>
          ) : null}
          {unplaced.length > 0 ? (
            <Alert
              tone="danger"
              title={t('settings.kamusm.seal.unplacedTitle')}
              data-testid="seal-unplaced"
            >
              <ul className="list-disc pl-5">
                {unplaced.map((cause) => (
                  <li key={cause}>{t(causeKey(cause, provider), { defaultValue: cause })}</li>
                ))}
              </ul>
              <p className="mt-1">{t('settings.kamusm.seal.unplacedBody')}</p>
            </Alert>
          ) : null}
          <HeldChanges
            config={config.data}
            section="tsa"
            except={[
              FORM_STEPS.includes(step) ? flow.requestId : null,
              started?.step === step ? started.id : null,
            ]}
          />
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
                description={t(stepDescription(provider, step))}
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
                  onRetry={() => {
                    send(lastSend.current);
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
                  probingText={t(providerText(sentTo || provider, 'probing'))}
                  onTerminal={settled}
                  failureDetails={<TsaRefusal error={flow.failure} provider={provider} />}
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
