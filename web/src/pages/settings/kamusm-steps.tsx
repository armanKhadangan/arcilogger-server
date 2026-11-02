import {
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  Stamp,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { TextField } from '@/components/admin/form-fields';
import { LockedList } from '@/components/admin/locked-field';
import { RequestProgress } from '@/components/admin/request-progress';
import { SecretField } from '@/components/admin/secret-field';
import { SECRET_KEPT } from '@/components/admin/secret-edit';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Label } from '@/components/ui/label';
import { errorMessage, isApiError } from '@/lib/api';
import { useTsaTest } from '@/lib/api-hooks';
import { formatDateTime } from '@/lib/format';
import type {
  AdminConfig,
  AdminRequestOut,
  ReadinessItem,
  TsaSection,
  TsaServer,
  TsaTestResult,
} from '@/lib/settings-types';
import { cn } from '@/lib/utils';

import { causeKey, type Credentials, type StepStatus, type TsaForm } from './kamusm-model';

const STATUS: Record<StepStatus, { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }> = {
  ok: { tone: 'success', icon: CircleCheck },
  missing: { tone: 'warning', icon: CircleDashed },
  error: { tone: 'danger', icon: CircleX },
  pending: { tone: 'info', icon: Clock },
  optional: { tone: 'neutral', icon: CircleDashed },
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

/**
 * Step 4 "Müşteri bilgileri": the customer number and the password, write-only. Saving tries
 * them first with a credit query on the server (unless the box is cleared).
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
  errors: { customer?: string | undefined; password?: string | undefined };
  probe: boolean;
  onProbe: (probe: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="flex flex-col gap-4">
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
          {t('settings.kamusm.credentials.probe')}
        </Label>
      </div>
    </div>
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

/**
 * Step 6 "Üretime geç": the checklist of `readiness` (and the warnings that do not block), the
 * switch to the production server after a confirmation, and back to the test server, which
 * stops production stamps and asks again.
 */
export function ProductionStep({
  tsa,
  tested,
  disabled,
  blockedHint,
  onSwitch,
}: {
  tsa: TsaSection;
  tested: boolean;
  disabled: boolean;
  /** Why the switch waits (e.g. other changes are not saved yet). */
  blockedHint?: string | undefined;
  onSwitch: (server: TsaServer) => void;
}) {
  const { t } = useTranslation();
  const [asking, setAsking] = useState<TsaServer | null>(null);
  const production = tsa.provider === 'zamane' && tsa.server === 'production';
  const missing = new Set<string>(tsa.readiness.missing);
  const legal = tsa.roots.some((root) => root.legal);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-13 text-foreground" data-testid="tsa-server">
        {tsa.provider === 'zamane'
          ? t(`settings.kamusm.production.current.${tsa.server ?? 'test'}`)
          : t('settings.kamusm.production.current.other', { provider: tsa.provider })}
      </p>
      <ul
        className="flex flex-col gap-1.5 text-13"
        aria-label={t('settings.kamusm.production.checklist')}
      >
        {READINESS.map((item) => {
          const ok = !missing.has(item);
          const Icon = ok ? CircleCheck : CircleX;
          return (
            <li key={item} className="flex items-center gap-2">
              <Icon
                aria-hidden="true"
                className={cn('size-4', ok ? 'text-success' : 'text-danger')}
              />
              {t(`settings.kamusm.production.items.${item}`)}
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
        {production ? (
          <Button
            variant="secondary"
            disabled={disabled}
            onClick={() => {
              setAsking('test');
            }}
          >
            {t('settings.kamusm.production.toTest')}
          </Button>
        ) : (
          <Button
            disabled={disabled || !tsa.readiness.production}
            onClick={() => {
              setAsking('production');
            }}
          >
            {t('settings.kamusm.production.toProduction')}
          </Button>
        )}
      </div>
      {!production && !tsa.readiness.production ? (
        <p className="text-xs text-muted">{t('settings.kamusm.production.notReady')}</p>
      ) : null}
      {blockedHint ? <p className="text-xs text-muted">{blockedHint}</p> : null}
      <ConfirmDialog
        open={asking !== null}
        onOpenChange={(open) => {
          if (!open) setAsking(null);
        }}
        title={t(
          asking === 'test'
            ? 'settings.kamusm.production.testTitle'
            : 'settings.kamusm.production.confirmTitle',
        )}
        description={t(
          asking === 'test'
            ? 'settings.kamusm.production.testBody'
            : 'settings.kamusm.production.confirmBody',
        )}
        confirmLabel={t(
          asking === 'test'
            ? 'settings.kamusm.production.toTest'
            : 'settings.kamusm.production.toProduction',
        )}
        danger={asking === 'test'}
        onConfirm={() => {
          if (asking) onSwitch(asking);
          setAsking(null);
        }}
      />
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

/** Why a `PUT /admin/config/tsa` was refused: what production still needs, what would stop sealing. */
export function TsaRefusal({ error }: { error: unknown }) {
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
            : t(causeKey(item), { defaultValue: item })}
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
              <li key={cause}>{t(causeKey(cause), { defaultValue: cause })}</li>
            ))}
          </ul>
        </Alert>
      )}
      <LockedList paths={locked} />
    </div>
  );
}
