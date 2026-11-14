import { FlaskConical, Lock, RotateCcw, Save } from 'lucide-react';
import { useRef, useState, type ReactNode, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { SwitchField, TextField } from '@/components/admin/form-fields';
import { Alert } from '@/components/ui/alert';
import { SeverityBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError } from '@/lib/api';
import { useRuleDryRun, useUpdateRule } from '@/lib/api-hooks';
import type { AlertSeverity } from '@/lib/api-types';
import { formatDateTime, formatDuration, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { DryRunResult, RuleOut } from '@/lib/rules-types';
import { focusFirstError } from '@/lib/validation';
import { ALERT_SEVERITIES } from '@/pages/security/alerts-model';

import { VerifyBadge } from './rule-badges';
import { RuleParamInput } from './rule-params';
import {
  defaultsForm,
  fieldOfProblem,
  fromRuleForm,
  hasRuleErrors,
  isRuleChanged,
  paramField,
  ruleBody,
  ruleFieldId,
  ruleName,
  toRuleForm,
  validateRule,
  type RuleErrors,
  type RuleField,
  type RuleForm,
  type RuleProblem,
} from './rules-model';

function typedSeconds(text: string): number | null {
  const value = text.trim();
  return /^\d+$/.test(value) ? Number(value) : null;
}

/** The trial run's answer and the form it ran with (a later change makes it stale). */
interface Trial {
  result: DryRunResult;
  form: string;
}

function DryRunPanel({ trial, stale }: { trial: Trial; stale: boolean }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { result } = trial;
  const head = 'px-3 py-2 text-left text-xs font-medium text-muted-strong';
  return (
    <div className="flex flex-col gap-2" data-testid="rule-dry-run-result">
      <p className="text-13 font-medium text-foreground" role="status">
        {t('rules.sheet.dryRunResult', {
          count: result.would_open,
          n: formatNumber(result.would_open, 0, lang),
        })}
      </p>
      {stale ? (
        <Alert tone="info" assertive={false}>
          {t('rules.sheet.dryRunStale')}
        </Alert>
      ) : null}
      {result.keys.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-13">
            <caption className="sr-only">{t('rules.sheet.dryRunKeys')}</caption>
            <thead className="bg-subtle">
              <tr>
                <th scope="col" className={head}>
                  {t('rules.sheet.dryRunKey')}
                </th>
                <th scope="col" className={`${head} text-right`}>
                  {t('rules.sheet.dryRunCount')}
                </th>
                <th scope="col" className={head}>
                  {t('rules.sheet.dryRunFirst')}
                </th>
                <th scope="col" className={head}>
                  {t('rules.sheet.dryRunLast')}
                </th>
              </tr>
            </thead>
            <tbody>
              {result.keys.map((item) => (
                <tr key={item.key} className="border-t border-border">
                  <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-medium">
                    {item.key}
                  </th>
                  <td className="px-3 py-1.5 text-right tabular">
                    {formatNumber(item.count, 0, lang)}
                  </td>
                  <td className="px-3 py-1.5 tabular">{formatDateTime(item.first_time)}</td>
                  <td className="px-3 py-1.5 tabular">{formatDateTime(item.last_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="text-xs text-muted">
        {result.truncated ? `${t('rules.sheet.dryRunTruncated')} ` : null}
        {t('rules.sheet.dryRunDuration', { ms: formatNumber(result.duration_ms, 0, lang) })}
      </p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-13 font-semibold text-foreground">{title}</h3>
      {children}
    </section>
  );
}

/** The sheet's content for one rule; its state starts again for another rule (`key`). */
function RuleEditor({
  rule,
  editable,
  locked,
}: {
  rule: RuleOut;
  editable: boolean;
  locked: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const update = useUpdateRule();
  const dryRun = useRuleDryRun();
  const [form, setForm] = useState<RuleForm>(() => toRuleForm(rule));
  const [errors, setErrors] = useState<RuleErrors>({});
  const [serverError, setServerError] = useState<{ field: RuleField | null; text: string } | null>(
    null,
  );
  const [trial, setTrial] = useState<Trial | null>(null);
  const [trialError, setTrialError] = useState<string | null>(null);
  /** Save turns disabled once saved: the focus stays in the sheet (Escape still closes it). */
  const content = useRef<HTMLDivElement>(null);
  const changed = isRuleChanged(form, rule);
  const busy = update.isPending || dryRun.isPending;

  const set = (next: Partial<RuleForm>) => {
    setForm((current) => ({ ...current, ...next }));
  };
  const setParam = (key: string, value: string | boolean) => {
    setForm((current) => ({ ...current, params: { ...current.params, [key]: value } }));
  };

  const message = (field: RuleField): string | undefined => {
    if (serverError?.field === field) return serverError.text;
    const problem: RuleProblem | undefined = errors[field];
    return problem ? t(problem.key, problem.values) : undefined;
  };

  /** The browser's checks; the body to send when they pass. */
  function checked() {
    const found = validateRule(form, rule.params, lang);
    setErrors(found);
    setServerError(null);
    if (hasRuleErrors(found)) {
      focusFirstError(
        Object.fromEntries(Object.entries(found).map(([field, problem]) => [field, problem?.key])),
        (field) => ruleFieldId(field as RuleField),
      );
      return null;
    }
    return ruleBody(fromRuleForm(form, rule), rule.defaults);
  }

  /** A refusal: at its field when the server names one, else above the buttons. */
  function refused(error: unknown): string {
    const text = errorMessage(error);
    if (isApiError(error, 'rule_param_invalid')) {
      const field = fieldOfProblem(error.details, rule.params);
      if (field) {
        setServerError({ field, text });
        focusFirstError({ [field]: text }, (key) => ruleFieldId(key as RuleField));
        return text;
      }
    }
    setServerError({ field: null, text });
    return text;
  }

  async function save() {
    const body = checked();
    if (!body) return;
    try {
      await update.mutateAsync({ id: rule.id, body });
      content.current?.focus();
      toast({ title: t('rules.sheet.saved', { id: rule.id }), variant: 'success' });
    } catch (error) {
      refused(error);
    }
  }

  async function tryIt() {
    const body = checked();
    if (!body) return;
    setTrialError(null);
    const snapshot = JSON.stringify(form);
    try {
      const result = await dryRun.mutateAsync({ id: rule.id, body });
      setTrial({ result, form: snapshot });
    } catch (error) {
      setTrial(null);
      if (isApiError(error, 'rule_param_invalid')) refused(error);
      else setTrialError(errorMessage(error));
    }
  }

  const advice = lang === 'en' ? rule.advice_en : rule.advice_tr;
  const window = typedSeconds(form.window);
  const cooldown = typedSeconds(form.cooldown);
  const disabled = !editable || busy;
  const general = serverError && serverError.field === null ? serverError.text : null;

  return (
    <SheetBody>
      <div ref={content} tabIndex={-1} className="flex flex-col gap-5 outline-none">
        <div className="flex flex-col gap-2 empty:hidden">
          {locked ? (
            <Alert tone="warning" assertive={false}>
              <span className="flex items-start gap-2">
                <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                {t('rules.locked')}
              </span>
            </Alert>
          ) : null}
          {rule.needs_setup ? (
            <Alert tone="warning" assertive={false} data-testid="rule-needs-setup">
              {t('rules.state.setup')}: {t('rules.setupHint')}
            </Alert>
          ) : null}
          {!rule.format_verified ? (
            <Alert tone="info" assertive={false} data-testid="rule-verify">
              <span className="flex flex-col items-start gap-1">
                <VerifyBadge />
                {t('rules.verifyHint')}
              </span>
            </Alert>
          ) : null}
          {rule.person_level ? (
            <Alert tone="info" assertive={false}>
              {t('rules.personLevel')}: {t('rules.personLevelHint')}
            </Alert>
          ) : null}
          {rule.last_error ? (
            <Alert tone="danger" assertive={false} data-testid="rule-last-error">
              {t('rules.lastError', { error: rule.last_error })}
            </Alert>
          ) : null}
          {rule.skipped_until ? (
            <Alert tone="info" assertive={false} data-testid="rule-skipped">
              {t('rules.skipped', { time: formatDateTime(rule.skipped_until) })}
            </Alert>
          ) : null}
        </div>

        <SwitchField
          id="rule-enabled"
          label={t('rules.sheet.enabled')}
          hint={t('rules.sheet.enabledHint')}
          checked={form.enabled}
          disabled={disabled}
          onChange={(enabled) => {
            set({ enabled });
          }}
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="rule-severity" label={t('rules.sheet.severity')}>
            <Select
              value={form.severity}
              disabled={disabled}
              onValueChange={(severity) => {
                set({ severity: severity as AlertSeverity });
              }}
            >
              <SelectTrigger id="rule-severity" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALERT_SEVERITIES.map((severity) => (
                  <SelectItem key={severity} value={severity}>
                    {t(`severity.${severity}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div />
          <TextField
            id={ruleFieldId('window')}
            label={t('rules.sheet.window')}
            hint={t('rules.sheet.windowHint', {
              duration: window === null ? '—' : formatDuration(window, lang),
            })}
            value={form.window}
            error={message('window')}
            disabled={disabled}
            inputMode="numeric"
            inputClassName="w-40"
            autoComplete="off"
            onChange={(value) => {
              set({ window: value });
            }}
          />
          <TextField
            id={ruleFieldId('cooldown')}
            label={t('rules.sheet.cooldown')}
            hint={t('rules.sheet.cooldownHint', {
              duration:
                cooldown === null
                  ? '—'
                  : cooldown === 0
                    ? t('rules.sheet.noCooldown')
                    : formatDuration(cooldown, lang),
            })}
            value={form.cooldown}
            error={message('cooldown')}
            disabled={disabled}
            inputMode="numeric"
            inputClassName="w-40"
            autoComplete="off"
            onChange={(value) => {
              set({ cooldown: value });
            }}
          />
        </div>

        {rule.params.length > 0 ? (
          <Section title={t('rules.sheet.params')}>
            <p className="text-xs text-muted" data-testid="rule-starting-value">
              {t('rules.sheet.startingValue')}
            </p>
            <div className="flex flex-col gap-4">
              {rule.params.map((param) => (
                <RuleParamInput
                  key={param.key}
                  param={param}
                  value={form.params[param.key]}
                  stored={rule.settings.params[param.key]}
                  fallback={rule.defaults.params[param.key]}
                  error={message(paramField(param.key))}
                  disabled={disabled}
                  onChange={(value) => {
                    setParam(param.key, value);
                  }}
                />
              ))}
            </div>
          </Section>
        ) : null}

        <Section title={t('rules.sheet.actions')}>
          <SwitchField
            id="rule-action-email"
            label={t('rules.sheet.email')}
            hint={t('rules.sheet.emailHint')}
            checked={form.email}
            disabled={disabled}
            onChange={(email) => {
              set({ email });
            }}
          />
          <SwitchField
            id="rule-action-webhook"
            label={t('rules.sheet.webhook')}
            hint={t('rules.sheet.webhookHint')}
            checked={form.webhook}
            disabled={disabled}
            onChange={(webhook) => {
              set({ webhook });
            }}
          />
        </Section>

        {advice.trim() ? (
          <Section title={t('rules.sheet.advice')}>
            <p className="text-13 whitespace-pre-line text-foreground" data-testid="rule-advice">
              {advice}
            </p>
          </Section>
        ) : null}

        {editable ? (
          <Section title={t('rules.sheet.dryRun')}>
            <p className="text-xs text-muted">{t('rules.sheet.dryRunHint')}</p>
            <Button
              variant="secondary"
              className="self-start"
              disabled={busy}
              onClick={() => void tryIt()}
            >
              <FlaskConical aria-hidden="true" />
              {dryRun.isPending ? t('rules.sheet.dryRunRunning') : t('rules.sheet.dryRun')}
            </Button>
            {trialError ? (
              <Alert tone="danger" data-testid="rule-dry-run-error">
                {trialError}
              </Alert>
            ) : null}
            {trial ? (
              <DryRunPanel trial={trial} stale={trial.form !== JSON.stringify(form)} />
            ) : null}
          </Section>
        ) : null}

        {editable ? (
          <div className="flex flex-col gap-2 border-t border-border pt-4">
            {general ? (
              <Alert tone="danger" data-testid="rule-save-error">
                {general}
              </Alert>
            ) : null}
            {hasRuleErrors(errors) ? (
              <p className="text-xs text-danger-ink">{t('rules.sheet.fixErrors')}</p>
            ) : null}
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => {
                  setForm(defaultsForm(rule));
                  setErrors({});
                  setServerError(null);
                }}
              >
                <RotateCcw aria-hidden="true" />
                {t('rules.sheet.resetDefaults')}
              </Button>
              <Button disabled={busy || !changed} onClick={() => void save()}>
                <Save aria-hidden="true" />
                {t('rules.sheet.save')}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </SheetBody>
  );
}

/**
 * A rule's sheet (Wave 6 §2.3): its kind and inputs, the marks and the last run's problems, the
 * settings (on/off, severity, window, cooldown, the rule's own parameters, the actions), the
 * advice, "Son 24 saatte dene" and Save. Read-only without `settings.manage` or the module.
 */
export function RuleSheet({
  rule,
  editable,
  locked,
  returnFocus,
  onClose,
}: {
  rule: RuleOut | null;
  editable: boolean;
  locked: boolean;
  returnFocus?: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Sheet
      open={rule !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {rule ? (
        <SheetContent className="max-w-2xl" returnFocus={returnFocus} data-testid="rule-sheet">
          <SheetHeader>
            <SheetTitle className="flex flex-wrap items-center gap-2 text-base font-semibold text-foreground">
              {t('rules.sheet.title', { id: rule.id, name: ruleName(rule, lang) })}
              <SeverityBadge severity={rule.settings.severity} />
            </SheetTitle>
            <SheetDescription className="text-13 text-muted">
              {t(`rules.sheet.kind.${rule.kind}`)}{' '}
              {t('rules.sheet.inputs', { tables: rule.inputs.join(', ') })}
            </SheetDescription>
          </SheetHeader>
          <RuleEditor key={rule.id} rule={rule} editable={editable} locked={locked} />
        </SheetContent>
      ) : null}
    </Sheet>
  );
}
