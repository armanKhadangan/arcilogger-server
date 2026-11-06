import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Clock,
  Stamp,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { TextField } from '@/components/admin/form-fields';
import { LockedList } from '@/components/admin/locked-field';
import { RequestProgress } from '@/components/admin/request-progress';
import { SecretField } from '@/components/admin/secret-field';
import { SECRET_KEPT, type SecretEdit } from '@/components/admin/secret-edit';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { errorMessage, isApiError } from '@/lib/api';
import { useTsaTest } from '@/lib/api-hooks';
import { formatDateTime } from '@/lib/format';
import type {
  AdminConfig,
  AdminRequestOut,
  ReadinessItem,
  TsaSection,
  TsaTestResult,
} from '@/lib/settings-types';
import { cn } from '@/lib/utils';

import {
  CRL_MAX_AGE_DAYS,
  CRL_URLS_MAX,
  PLAIN_HTTP_PHRASE,
  RENEW_BEFORE_DAYS,
  causeKey,
  choiceProvider,
  currentChoice,
  plainHttpConfirmed,
  providerText,
  secretMeta,
  serverChoices,
  TURKTRUST_KEPT,
  type Credentials,
  type EvidenceForm,
  type ServerChoice,
  type StepStatus,
  type TsaForm,
} from './kamusm-model';

const STATUS: Record<StepStatus, { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }> = {
  ok: { tone: 'success', icon: CircleCheck },
  missing: { tone: 'warning', icon: CircleDashed },
  error: { tone: 'danger', icon: CircleX },
  pending: { tone: 'info', icon: Clock },
  optional: { tone: 'neutral', icon: CircleDashed },
  unused: { tone: 'neutral', icon: CircleMinus },
};

/** A step's state: ok, missing, error (stops sealing), waiting, optional. */
export function StepBadge({ status, label }: { status: StepStatus; label?: string }) {
  const { t } = useTranslation();
  const style = STATUS[status];
  return (
    <Badge tone={style.tone} icon={style.icon}>
      {label ?? t(`settings.kamusm.status.${status}`)}
    </Badge>
  );
}

/** Step 1 "Java": one of the Java 8 installations the administration service found. */
export function JavaStep({
  tsa,
  java,
  onChange,
  disabled,
}: {
  tsa: TsaSection;
  java: string | null;
  onChange: (java: string) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  if (tsa.java_choices.length === 0)
    return (
      <Alert tone="warning" assertive={false} title={t('settings.kamusm.java.noneTitle')}>
        {t('settings.kamusm.java.none')}
      </Alert>
    );
  return (
    <fieldset className="flex flex-col gap-2" aria-describedby={`${id}-hint`}>
      <legend className="text-13 font-medium text-foreground">
        {t('settings.kamusm.java.choose')}
      </legend>
      {tsa.java_choices.map((choice, index) => (
        <label
          key={choice}
          htmlFor={`${id}-${index}`}
          className={cn(
            'flex cursor-pointer items-center gap-3 rounded-lg border p-3 text-13',
            java === choice ? 'border-primary bg-primary-soft' : 'border-border',
          )}
        >
          <input
            type="radio"
            id={`${id}-${index}`}
            name={`${id}-java`}
            className="size-4 accent-primary"
            checked={java === choice}
            disabled={disabled}
            onChange={() => {
              onChange(choice);
            }}
          />
          <span className="font-mono text-xs break-all">{choice}</span>
        </label>
      ))}
      <p id={`${id}-hint`} className="text-xs text-muted">
        {t('settings.kamusm.java.hint')}
      </p>
      {tsa.java && !tsa.java_choices.includes(tsa.java) ? (
        <Alert tone="warning" assertive={false}>
          {t('settings.kamusm.java.notListed', { java: tsa.java })}
        </Alert>
      ) : null}
    </fieldset>
  );
}

/** The errors of the credential fields (the browser's check or the API's refusal). */
export interface CredentialErrors {
  customer?: string | undefined;
  password?: string | undefined;
  turktrustUsername?: string | undefined;
  turktrustPassword?: string | undefined;
}

/**
 * "Sahada doğrula" (Wave 5 contract 0.4, T1, T2, T5): what this installation does not confirm of
 * TÜRKTRUST, shown wherever its credentials are entered.
 */
function TurktrustNote() {
  const { t } = useTranslation();
  return (
    <Alert
      tone="info"
      assertive={false}
      title={t('settings.kamusm.turktrust.note.title')}
      data-testid="turktrust-note"
    >
      {t('settings.kamusm.turktrust.note.body')}
    </Alert>
  );
}

/**
 * TÜRKTRUST's username and password (Wave 5 contract 2.4), write-only like every credential:
 * a stored value is shown as "Kayıtlı" and never sent to the browser.
 */
export function TurktrustFields({
  tsa,
  credentials,
  onChange,
  disabled,
  errors,
}: {
  tsa: TsaSection;
  credentials: Credentials;
  onChange: (credentials: Credentials) => void;
  disabled: boolean;
  errors: CredentialErrors;
}) {
  const { t } = useTranslation();
  const id = useId();
  const turktrust = credentials.turktrust ?? TURKTRUST_KEPT;
  return (
    <div className="flex flex-col gap-4" data-testid="turktrust-credentials">
      <SecretField
        id={`${id}-username`}
        label={t('settings.kamusm.turktrust.credentials.username')}
        meta={secretMeta(tsa, 'turktrust.username')}
        edit={turktrust.username}
        onChange={(username) => {
          onChange({ ...credentials, turktrust: { ...turktrust, username } });
        }}
        disabled={disabled}
        error={errors.turktrustUsername}
        hint={t('settings.kamusm.turktrust.credentials.usernameHint')}
      />
      <SecretField
        id={`${id}-password`}
        label={t('settings.kamusm.turktrust.credentials.password')}
        meta={secretMeta(tsa, 'turktrust.password')}
        edit={turktrust.password}
        onChange={(password) => {
          onChange({ ...credentials, turktrust: { ...turktrust, password } });
        }}
        disabled={disabled}
        error={errors.turktrustPassword}
        hint={t('settings.kamusm.turktrust.credentials.passwordHint')}
      />
      <TurktrustNote />
    </div>
  );
}

/**
 * Step 4 "Müşteri bilgileri": the customer number and the password, write-only (with TÜRKTRUST in
 * use, its username and password). Saving tries them first on the server, with a credit query for
 * Kamu SM (unless the box is cleared).
 */
export function CredentialsStep({
  tsa,
  credentials,
  onChange,
  disabled,
  errors,
  probe,
  onProbe,
}: {
  tsa: TsaSection;
  credentials: Credentials;
  onChange: (credentials: Credentials) => void;
  disabled: boolean;
  errors: CredentialErrors;
  probe: boolean;
  onProbe: (probe: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const turktrust = tsa.provider === 'turktrust';
  return (
    <div className="flex flex-col gap-4">
      {turktrust ? (
        <TurktrustFields
          tsa={tsa}
          credentials={credentials}
          onChange={onChange}
          disabled={disabled}
          errors={errors}
        />
      ) : (
        <KamusmFields
          tsa={tsa}
          credentials={credentials}
          onChange={onChange}
          disabled={disabled}
          errors={errors}
        />
      )}
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${id}-probe`}
          checked={probe}
          disabled={disabled}
          onCheckedChange={(checked) => {
            onProbe(checked === true);
          }}
        />
        <Label htmlFor={`${id}-probe`} className="font-normal">
          {t(providerText(tsa.provider, 'credentials.probe'))}
        </Label>
      </div>
    </div>
  );
}

/** The Kamu SM customer number and password; "Sil" deletes both. */
function KamusmFields({
  tsa,
  credentials,
  onChange,
  disabled,
  errors,
}: {
  tsa: TsaSection;
  credentials: Credentials;
  onChange: (credentials: Credentials) => void;
  disabled: boolean;
  errors: CredentialErrors;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <>
      <SecretField
        id={`${id}-customer`}
        label={t('settings.kamusm.credentials.customer')}
        meta={tsa.secrets['kamusm.customer']}
        edit={credentials.clear ? { ...SECRET_KEPT, clear: true } : credentials.customer}
        onChange={(customer) => {
          onChange({ ...credentials, customer, clear: customer.clear });
        }}
        disabled={disabled}
        allowClear
        error={errors.customer}
        hint={t('settings.kamusm.credentials.customerHint')}
      />
      <SecretField
        id={`${id}-password`}
        label={t('settings.kamusm.credentials.password')}
        meta={tsa.secrets['kamusm.password']}
        edit={credentials.clear ? { ...SECRET_KEPT, clear: true } : credentials.password}
        onChange={(password) => {
          onChange({ ...credentials, password, clear: password.clear });
        }}
        disabled={disabled}
        allowClear
        error={errors.password}
      />
      {credentials.clear ? (
        <Alert tone="warning" assertive={false}>
          {t('settings.kamusm.credentials.clear')}
        </Alert>
      ) : null}
    </>
  );
}

function TestOutcome({ request }: { request: AdminRequestOut }) {
  const { t } = useTranslation();
  const result = request.result as TsaTestResult | null;
  if (!result) return null;
  return (
    <dl
      className="grid gap-x-6 gap-y-1.5 text-13 sm:grid-cols-[minmax(10rem,auto)_1fr]"
      data-testid="tsa-test-result"
    >
      <dt className="text-muted-strong">{t('settings.kamusm.test.verified')}</dt>
      <dd>
        <Badge
          tone={result.verified ? 'success' : 'danger'}
          icon={result.verified ? CircleCheck : CircleX}
        >
          {result.verified
            ? t('settings.kamusm.test.verifiedYes')
            : t('settings.kamusm.test.verifiedNo')}
        </Badge>
      </dd>
      <dt className="text-muted-strong">{t('settings.kamusm.test.provider')}</dt>
      <dd className="font-mono text-xs">{result.provider}</dd>
      <dt className="text-muted-strong">{t('settings.kamusm.test.serial')}</dt>
      <dd className="font-mono text-xs">{result.serial ?? '—'}</dd>
      <dt className="text-muted-strong">{t('settings.kamusm.test.genTime')}</dt>
      <dd className="tabular">{result.gen_time ? formatDateTime(result.gen_time) : '—'}</dd>
      <dt className="text-muted-strong">{t('settings.kamusm.test.tsa')}</dt>
      <dd className="font-mono text-xs break-all">{result.tsa ?? '—'}</dd>
      <dt className="text-muted-strong">{t('settings.kamusm.test.legal')}</dt>
      <dd>
        {result.legal_grade
          ? t('settings.kamusm.test.legalYes')
          : t('settings.kamusm.test.legalNo')}
      </dd>
      {result.errors.length > 0 ? (
        <>
          <dt className="text-muted-strong">{t('settings.kamusm.test.errors')}</dt>
          <dd className="text-danger-ink">{result.errors.join('; ')}</dd>
        </>
      ) : null}
    </dl>
  );
}

/**
 * Step 5 "Test sunucusunda damga testi": one time stamp from the configured server, verified on
 * the server (1 credit; limited per day by the installation's policy).
 */
export function TestStep({
  tsa,
  writable,
  perDay,
}: {
  tsa: TsaSection;
  writable: boolean;
  perDay: number;
}) {
  const { t } = useTranslation();
  const test = useTsaTest();
  const [confirming, setConfirming] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function run() {
    setProblem(null);
    try {
      const accepted = await test.mutateAsync({ confirm_credit: true });
      setRequestId(accepted.request_id);
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-13 text-muted-strong">
        {t(
          tsa.provider === 'zamane'
            ? `settings.kamusm.test.server.${tsa.server ?? 'test'}`
            : tsa.provider === 'turktrust'
              ? 'settings.kamusm.test.server.turktrust'
              : 'settings.kamusm.test.server.other',
          { provider: tsa.provider },
        )}
      </p>
      <div>
        <Button
          variant="secondary"
          disabled={!writable}
          busy={test.isPending}
          onClick={() => {
            setConfirming(true);
          }}
        >
          <Stamp aria-hidden="true" />
          {t('settings.kamusm.test.button')}
        </Button>
      </div>
      <p className="text-xs text-muted">{t('settings.kamusm.test.limit', { count: perDay })}</p>
      {problem ? <Alert tone="danger">{problem}</Alert> : null}
      {requestId ? (
        <RequestProgress
          requestId={requestId}
          quiet
          renderResult={(request) => <TestOutcome request={request} />}
        />
      ) : null}
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('settings.kamusm.test.confirmTitle')}
        description={t('settings.kamusm.test.confirmBody')}
        confirmLabel={t('settings.kamusm.test.confirm')}
        busy={test.isPending}
        onConfirm={() => void run()}
      />
    </div>
  );
}

const READINESS: ReadinessItem[] = ['credentials', 'zamane_jar', 'java', 'roots'];

/** The text keys of a server choice (`settings.kamusm.servers.*`, `….production.current.*`). */
const CHOICE_KEYS: Record<ServerChoice, string> = {
  test: 'test',
  production: 'production',
  turktrust: 'turktrustHttps',
  'turktrust-http': 'turktrustHttp',
};

/** The server in use in words: a choice, TÜRKTRUST at a server not named yet, or another provider. */
function currentText(tsa: TsaSection, current: ServerChoice | null): string {
  if (current) return `settings.kamusm.production.current.${CHOICE_KEYS[current]}`;
  return tsa.provider === 'turktrust'
    ? 'settings.kamusm.production.current.turktrust'
    : 'settings.kamusm.production.current.other';
}

/** A new value was typed for a credential (sent with the switch). */
function typedValue(edit: SecretEdit): boolean {
  return edit.editing && edit.value !== '';
}

/**
 * Step 6 "Üretime geç": the server to switch to (Kamu SM test or production, TÜRKTRUST over HTTPS
 * or over plain HTTP, Wave 5 contract 2.4), the checklist of what it needs (`readiness` for
 * Kamu SM, the username and password for TÜRKTRUST) with the warnings that do not block, and the
 * switch after a confirmation. TÜRKTRUST's servers are offered only where the server knows
 * TÜRKTRUST (`serverChoices`); a switch to them waits for both credentials, stored or typed here,
 * and choosing another server drops what was typed for TÜRKTRUST. Back to the test server stops production stamps and asks again;
 * plain HTTP says that the password then travels unencrypted and asks for a typed confirmation
 * before anything is sent, because the server's dry run applies the same rule. Should the server
 * still refuse a plain-HTTP switch for want of it (`tsa_plain_http_confirm_required`, W5-T-4),
 * the confirmation opens again with the server's text, and the typed phrase is asked again.
 */
export function ProductionStep({
  tsa,
  tested,
  disabled,
  unsaved,
  turktrustUnsaved,
  picked,
  onPick,
  credentials,
  onCredentials,
  errors,
  onSwitch,
  httpRefusal = null,
}: {
  tsa: TsaSection;
  tested: boolean;
  disabled: boolean;
  /** Changes of the other steps are not saved yet: a switch waits for them. */
  unsaved: boolean;
  /**
   * TÜRKTRUST credentials are typed: they go with a switch to TÜRKTRUST, so they hold back only a
   * switch to another server (which would not send them).
   */
  turktrustUnsaved: boolean;
  /** The server chosen and not switched to yet (the page keeps it while another step is open). */
  picked: ServerChoice | null;
  onPick: (choice: ServerChoice) => void;
  credentials: Credentials;
  onCredentials: (credentials: Credentials) => void;
  errors: CredentialErrors;
  onSwitch: (server: ServerChoice) => void;
  /** The server's refusal `tsa_plain_http_confirm_required`: the confirmation opens with its text. */
  httpRefusal?: unknown;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [asking, setAsking] = useState<ServerChoice | null>(null);
  const [typed, setTyped] = useState('');
  // A refusal shown once stays closed until the next one.
  const [seen, setSeen] = useState<unknown>(null);
  const refusal = httpRefusal !== null && httpRefusal !== seen ? httpRefusal : null;
  const current = currentChoice(tsa);
  const choices = serverChoices(tsa);
  // The server in use cannot be chosen; the next one is preselected (production, or back to test).
  const target: ServerChoice =
    picked !== null && picked !== current && choices.includes(picked)
      ? picked
      : current === 'production'
        ? 'test'
        : 'production';
  const turktrust = choiceProvider(target) === 'turktrust';
  const missing = new Set<string>(tsa.readiness.missing);
  const legal = tsa.roots.some((root) => root.legal);
  const entered = credentials.turktrust ?? TURKTRUST_KEPT;
  // A TÜRKTRUST credential is stored, or typed and not saved yet (it goes with the switch).
  const credential = (name: 'username' | 'password') => {
    const typedHere = typedValue(entered[name]);
    return {
      key: `turktrust.${name}${typedHere ? 'Typed' : ''}`,
      ok: typedHere || (secretMeta(tsa, `turktrust.${name}`)?.set ?? false),
    };
  };
  const checklist: { key: string; ok: boolean }[] = turktrust
    ? [credential('username'), credential('password')]
    : READINESS.map((item) => ({ key: item, ok: !missing.has(item) }));
  // Kamu SM production needs its readiness; TÜRKTRUST both credentials (the server decides again).
  const ready = turktrust
    ? checklist.every((item) => item.ok)
    : target !== 'production' || tsa.readiness.production;
  const waiting = unsaved || (!turktrust && turktrustUnsaved);
  const stopsProduction = current === 'production' || tsa.provider === 'turktrust';
  const phrase = PLAIN_HTTP_PHRASE;
  const switchLabel =
    target === 'production'
      ? t('settings.kamusm.production.toProduction')
      : target === 'test'
        ? t(
            current === 'production'
              ? 'settings.kamusm.production.toTest'
              : 'settings.kamusm.production.to.test',
          )
        : t(`settings.kamusm.production.to.${CHOICE_KEYS[target]}`);

  function confirmed() {
    const server = asking ?? (refusal !== null ? 'turktrust-http' : null);
    if (server) onSwitch(server);
    setAsking(null);
    setTyped('');
    setSeen(httpRefusal);
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-13 text-foreground" data-testid="tsa-server">
        {t(currentText(tsa, current), { provider: tsa.provider })}
      </p>
      <fieldset className="flex flex-col gap-2" data-testid="tsa-servers">
        <legend className="mb-1 text-13 font-medium text-foreground">
          {t('settings.kamusm.servers.legend')}
        </legend>
        {choices.map((choice) => {
          const inUse = choice === current;
          const key = CHOICE_KEYS[choice];
          return (
            <div
              key={choice}
              className={cn(
                'flex items-start gap-3 rounded-lg border p-3 text-13',
                target === choice ? 'border-primary bg-primary-soft' : 'border-border',
              )}
            >
              <input
                type="radio"
                id={`${id}-${choice}`}
                name={`${id}-server`}
                className="mt-0.5 size-4 accent-primary"
                checked={target === choice}
                disabled={disabled || inUse}
                aria-describedby={`${id}-${choice}-hint`}
                onChange={() => {
                  onPick(choice);
                  // Leaving TÜRKTRUST drops what was typed for it: it would not be sent.
                  if (choiceProvider(choice) !== 'turktrust' && turktrustUnsaved)
                    onCredentials({ ...credentials, turktrust: TURKTRUST_KEPT });
                }}
              />
              <div className="flex flex-col gap-0.5">
                <label
                  htmlFor={`${id}-${choice}`}
                  className={cn('font-medium text-foreground', inUse ? null : 'cursor-pointer')}
                >
                  {t(`settings.kamusm.servers.${key}`)}
                  {inUse ? ` (${t('settings.kamusm.servers.inUse')})` : null}
                </label>
                <p id={`${id}-${choice}-hint`} className="text-xs text-muted-strong">
                  {t(`settings.kamusm.servers.${key}Hint`)}
                </p>
              </div>
            </div>
          );
        })}
      </fieldset>
      {target === 'turktrust-http' ? (
        <Alert tone="warning" assertive={false} data-testid="tsa-plain-http">
          {t('settings.kamusm.turktrust.plainHttp')}
        </Alert>
      ) : null}
      {turktrust ? (
        <TurktrustFields
          tsa={tsa}
          credentials={credentials}
          onChange={onCredentials}
          disabled={disabled}
          errors={errors}
        />
      ) : null}
      <ul
        className="flex flex-col gap-1.5 text-13"
        aria-label={t('settings.kamusm.production.checklist')}
      >
        {checklist.map(({ key, ok }) => {
          const Icon = ok ? CircleCheck : CircleX;
          return (
            <li key={key} className="flex items-center gap-2">
              <Icon
                aria-hidden="true"
                className={cn('size-4', ok ? 'text-success' : 'text-danger')}
              />
              {t(`settings.kamusm.production.items.${key}`)}
              <span className="sr-only">
                : {ok ? t('settings.kamusm.status.ok') : t('settings.kamusm.status.missing')}
              </span>
            </li>
          );
        })}
        <li className="flex items-center gap-2">
          {legal ? (
            <CircleCheck aria-hidden="true" className="size-4 text-success" />
          ) : (
            <TriangleAlert aria-hidden="true" className="size-4 text-warning" />
          )}
          {t(legal ? 'settings.kamusm.production.legal' : 'settings.kamusm.production.noLegal')}
        </li>
        <li className="flex items-center gap-2">
          {tested ? (
            <CircleCheck aria-hidden="true" className="size-4 text-success" />
          ) : (
            <TriangleAlert aria-hidden="true" className="size-4 text-warning" />
          )}
          {t(tested ? 'settings.kamusm.production.tested' : 'settings.kamusm.production.notTested')}
        </li>
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button
          variant={target === 'test' ? 'secondary' : 'primary'}
          disabled={disabled || waiting || !ready}
          onClick={() => {
            setTyped('');
            setAsking(target);
          }}
        >
          {switchLabel}
        </Button>
      </div>
      {!ready ? (
        <p className="text-xs text-muted">{t('settings.kamusm.production.notReady')}</p>
      ) : null}
      {waiting ? (
        <p className="text-xs text-muted">{t('settings.kamusm.production.saveFirst')}</p>
      ) : null}
      <ConfirmDialog
        open={asking !== null && asking !== 'turktrust-http'}
        onOpenChange={(open) => {
          if (!open) setAsking(null);
        }}
        title={t(
          asking === 'test'
            ? stopsProduction
              ? 'settings.kamusm.production.testTitle'
              : 'settings.kamusm.production.testOtherTitle'
            : asking === 'turktrust'
              ? 'settings.kamusm.production.turktrustTitle'
              : 'settings.kamusm.production.confirmTitle',
        )}
        description={t(
          asking === 'test'
            ? stopsProduction
              ? 'settings.kamusm.production.testBody'
              : 'settings.kamusm.production.testOtherBody'
            : asking === 'turktrust'
              ? 'settings.kamusm.production.turktrustBody'
              : 'settings.kamusm.production.confirmBody',
        )}
        confirmLabel={switchLabel}
        danger={asking === 'test' && stopsProduction}
        onConfirm={confirmed}
      />
      <ChangeReviewDialog
        open={asking === 'turktrust-http' || (asking === null && refusal !== null)}
        onOpenChange={(open) => {
          if (open) return;
          setAsking(null);
          setTyped('');
          setSeen(httpRefusal);
        }}
        check={null}
        title={t('settings.kamusm.production.httpTitle')}
        description={t('settings.kamusm.turktrust.plainHttp')}
        confirmLabel={t('settings.kamusm.production.to.turktrustHttp')}
        danger
        confirmDisabled={!plainHttpConfirmed(typed)}
        onConfirm={confirmed}
      >
        {refusal !== null ? (
          <Alert tone="danger" assertive={false} data-testid="tsa-http-refusal">
            {errorMessage(refusal)}
          </Alert>
        ) : null}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-phrase`}>
            {t('settings.kamusm.production.httpType', { phrase })}
          </Label>
          <Input
            id={`${id}-phrase`}
            value={typed}
            autoComplete="off"
            spellCheck={false}
            className="max-w-xs"
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
        </div>
      </ChangeReviewDialog>
    </div>
  );
}

/** Step 7 "Kontör uyarı eşiği": the threshold and the time limit of a stamp request. */
export function CreditStep({
  form,
  onChange,
  disabled,
  errors,
}: {
  form: TsaForm;
  onChange: (form: Partial<TsaForm>) => void;
  disabled: boolean;
  errors: { threshold?: string | undefined; timeout?: string | undefined };
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <TextField
        id={`${id}-threshold`}
        label={t('settings.kamusm.credit.threshold')}
        hint={t('settings.kamusm.credit.thresholdHint')}
        value={form.threshold}
        inputMode="numeric"
        inputClassName="w-40"
        disabled={disabled}
        error={errors.threshold}
        onChange={(threshold) => {
          onChange({ threshold });
        }}
      />
      <TextField
        id={`${id}-timeout`}
        label={t('settings.kamusm.credit.timeout')}
        hint={t('settings.kamusm.credit.timeoutHint')}
        value={form.timeout}
        inputMode="numeric"
        inputClassName="w-40"
        disabled={disabled}
        error={errors.timeout}
        onChange={(timeout) => {
          onChange({ timeout });
        }}
      />
    </div>
  );
}

/**
 * Step 7, below the credit threshold: the re-stamping window and the revocation lists (Wave 5
 * contract 2.5, D4, D7; `tsa.renew_before_days`, `tsa.crl_urls`, `tsa.crl_max_age_days`), saved
 * with "Kaydet" and a password confirmation. They apply to both providers. The revocation-list
 * addresses of Kamu SM and TÜRKTRUST are not confirmed (T4, K10), so the field says "sahada
 * doğrula" and the code builds none in: left empty, the certificates' own distribution points
 * are used.
 */
export function EvidenceFields({
  id,
  form,
  onChange,
  disabled,
  changed,
  errors,
}: {
  /** The page's id: the controls are `<id>-<field>`, so the page can focus the first error. */
  id: string;
  form: EvidenceForm;
  onChange: (form: Partial<EvidenceForm>) => void;
  disabled: boolean;
  /** A key differs from the server's: the change asks for the password. */
  changed: boolean;
  errors: {
    renewBeforeDays?: string | undefined;
    crlUrls?: string | undefined;
    crlMaxAgeDays?: string | undefined;
  };
}) {
  const { t } = useTranslation();
  const urlsHint = `${id}-crlUrls-hint`;
  return (
    <section
      className="flex flex-col gap-4 border-t border-border pt-4"
      aria-labelledby={`${id}-evidence-title`}
      data-testid="tsa-evidence"
    >
      <div className="flex flex-col gap-1">
        <h3 id={`${id}-evidence-title`} className="text-sm font-semibold text-foreground">
          {t('settings.kamusm.evidence.title')}
        </h3>
        <p className="text-13 text-muted-strong">{t('settings.kamusm.evidence.description')}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField
          id={`${id}-renewBeforeDays`}
          label={t('settings.kamusm.evidence.renewBeforeDays')}
          hint={t('settings.kamusm.evidence.renewBeforeDaysHint', RENEW_BEFORE_DAYS)}
          value={form.renewBeforeDays}
          inputMode="numeric"
          inputClassName="w-40"
          disabled={disabled}
          error={errors.renewBeforeDays}
          onChange={(renewBeforeDays) => {
            onChange({ renewBeforeDays });
          }}
        />
        <TextField
          id={`${id}-crlMaxAgeDays`}
          label={t('settings.kamusm.evidence.crlMaxAgeDays')}
          hint={t('settings.kamusm.evidence.crlMaxAgeDaysHint', CRL_MAX_AGE_DAYS)}
          value={form.crlMaxAgeDays}
          inputMode="numeric"
          inputClassName="w-40"
          disabled={disabled}
          error={errors.crlMaxAgeDays}
          onChange={(crlMaxAgeDays) => {
            onChange({ crlMaxAgeDays });
          }}
        />
      </div>
      <Field
        id={`${id}-crlUrls`}
        label={t('settings.kamusm.evidence.crlUrls')}
        hint={t('settings.kamusm.evidence.crlUrlsHint', { max: CRL_URLS_MAX })}
        error={errors.crlUrls}
      >
        <Textarea
          id={`${id}-crlUrls`}
          rows={4}
          className="font-mono text-13"
          value={form.crlUrls}
          disabled={disabled}
          spellCheck={false}
          aria-invalid={errors.crlUrls ? true : undefined}
          aria-describedby={errors.crlUrls ? `${id}-crlUrls-error` : urlsHint}
          onChange={(event) => {
            onChange({ crlUrls: event.target.value });
          }}
        />
      </Field>
      <Alert
        tone="info"
        assertive={false}
        title={t('settings.kamusm.evidence.crlNoteTitle')}
        data-testid="tsa-crl-note"
      >
        {t('settings.kamusm.evidence.crlNote')}
      </Alert>
      {changed ? (
        <p className="text-xs text-muted-strong">{t('settings.kamusm.evidence.reauth')}</p>
      ) : null}
    </section>
  );
}

/** Why a `PUT /admin/config/tsa` was refused: what production still needs, what would stop sealing. */
export function TsaRefusal({ error, provider }: { error: unknown; provider?: string }) {
  const { t } = useTranslation();
  if (!isApiError(error, 'tsa_not_ready', 'tsa_would_break')) return null;
  const details = (error.details ?? {}) as { missing?: unknown; causes?: unknown };
  const items = error.code === 'tsa_not_ready' ? details.missing : details.causes;
  const list = Array.isArray(items)
    ? items.filter((item): item is string => typeof item === 'string')
    : [];
  if (list.length === 0) return null;
  return (
    <ul className="list-disc pl-5 text-13 text-danger-ink" data-testid="tsa-refusal">
      {list.map((item) => (
        <li key={item}>
          {error.code === 'tsa_not_ready'
            ? t(`settings.kamusm.production.items.${item}`, { defaultValue: item })
            : t(causeKey(item, provider), { defaultValue: item })}
        </li>
      ))}
    </ul>
  );
}

/**
 * "Mühürleme": the `[seal]` section and the expert `[tsa]` keys are read-only here (contract
 * 2.17); whether the Seal task can seal with the current configuration, and why not.
 */
export function SealView({ config }: { config: AdminConfig }) {
  const { t } = useTranslation();
  const ready = config.sections.tsa.seal_ready;
  const locked = config.locked.filter(
    (item) => item.path.startsWith('seal.') || item.path.startsWith('tsa.'),
  );
  return (
    <div className="flex flex-col gap-3" data-testid="seal-view">
      {ready.ok ? (
        <Alert tone="success" assertive={false}>
          {t('settings.kamusm.seal.ready')}
        </Alert>
      ) : (
        <Alert tone="danger" title={t('settings.kamusm.seal.notReady')}>
          <ul className="list-disc pl-5">
            {ready.causes.map((cause) => (
              <li key={cause}>
                {t(causeKey(cause, config.sections.tsa.provider), { defaultValue: cause })}
              </li>
            ))}
          </ul>
        </Alert>
      )}
      <LockedList paths={locked} />
    </div>
  );
}
