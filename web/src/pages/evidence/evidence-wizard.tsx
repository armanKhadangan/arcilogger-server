import { KeyRound, PackagePlus } from 'lucide-react';
import { useEffect, useId, useMemo, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Field, Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Stepper } from '@/components/ui/stepper';
import { Textarea } from '@/components/ui/textarea';
import { errorMessage, isApiError } from '@/lib/api';
import { evidenceAuditLogAccepted } from '@/lib/api-features';
import { useCreateEvidence, useLookups, useSealDay } from '@/lib/api-hooks';
import type { EvidenceCreated } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { parseDay } from '@/lib/dates';
import { formatDay, siteToday } from '@/lib/display';
import { useHandoff } from '@/lib/handoff';
import { formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { RECENT_AUTH_MINUTES } from '@/lib/permissions';
import { cn } from '@/lib/utils';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import {
  emptyWizard,
  MAX_DAYS,
  MAX_EXTRACT_LINES,
  parseLines,
  REFERENCE_MAX,
  sourcesSummary,
  toEvidenceBody,
  validateContent,
  validateScope,
  leaveOutUnsealed,
  wizardFromSearch,
  type SearchExtract,
  type WizardDraft,
  type WizardField,
} from './evidence-model';

const STEPS = ['scope', 'content', 'protection', 'summary'] as const;

/** A radio choice drawn as a bordered option (native radio: keyboard and screen readers). */
function Choice({
  name,
  value,
  checked,
  onSelect,
  title,
  description,
}: {
  name: string;
  value: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  description: string;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer gap-3 rounded-lg border border-border p-3 text-13',
        checked && 'border-primary ring-1 ring-primary',
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={onSelect}
        className="mt-0.5 size-4 accent-[var(--primary)]"
      />
      <span className="flex flex-col gap-0.5">
        <span className="font-medium text-foreground">{title}</span>
        <span className="text-muted">{description}</span>
      </span>
    </label>
  );
}

/** Extra lines for a refusal: unsealed days, sources, files, Retry-After. */
function failureDetails(error: unknown, t: TFunction): string[] {
  if (!isApiError(error)) return [];
  const lines: string[] = [];
  const details = (
    typeof error.details === 'object' && error.details !== null ? error.details : {}
  ) as Record<string, unknown>;
  const list = (value: unknown) =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  const days = list(details.days);
  if (days.length > 0)
    lines.push(t('evidence.errors.days', { days: days.map(formatDay).join(', ') }));
  const sources = list(details.sources);
  if (sources.length > 0) lines.push(t('evidence.errors.sources', { sources: sources.join(', ') }));
  const files = list(details.files);
  if (files.length > 0) lines.push(t('evidence.errors.files', { files: files.join(', ') }));
  if (error.retryAfter !== null)
    lines.push(t('evidence.errors.retryAfter', { seconds: error.retryAfter }));
  return lines;
}

/** What the wizard says about the lines a search handed over: where they came from, and why a
 * package may refuse them (cut at the extract's maximum, an incomplete scan, unsealed days). The
 * lines of unsealed days can be left out with one button (`leaveOutUnsealed`). */
function FromSearch({
  extract,
  leftOut,
  onLeaveOut,
}: {
  extract: SearchExtract;
  /** Lines of unsealed days left out so far. */
  leftOut: number;
  onLeaveOut: () => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const canLeaveOut = extract.unsealed_days.length > 0 && leaveOutUnsealed(extract) !== null;
  const warnings = [
    extract.truncated
      ? t('evidence.wizard.fromSearchTruncated', {
          max: formatNumber(MAX_EXTRACT_LINES, 0, currentLang()),
        })
      : null,
    extract.complete ? null : t('evidence.wizard.fromSearchIncomplete'),
    extract.unsealed_days.length > 0
      ? t('evidence.wizard.fromSearchUnsealed', {
          days: extract.unsealed_days.map(formatDay).join(', '),
        })
      : null,
  ].filter((text): text is string => text !== null);
  return (
    <div className="flex flex-col gap-2">
      <Alert
        tone="info"
        assertive={false}
        title={t('evidence.wizard.fromSearchTitle', { count: extract.extract.length })}
      >
        <p className="font-mono text-xs break-all">
          {t('evidence.wizard.fromSearchQuery', { query: extract.query || '—' })}
        </p>
        <p>{t('evidence.wizard.fromSearchBody')}</p>
        {leftOut > 0 ? (
          <p>
            {t('evidence.wizard.leftOutUnsealed', {
              count: leftOut,
              formatted: formatNumber(leftOut, 0, lang),
            })}
          </p>
        ) : null}
      </Alert>
      {warnings.length > 0 ? (
        <Alert tone="warning">
          {warnings.map((text) => (
            <p key={text}>{text}</p>
          ))}
          {canLeaveOut ? (
            <Button size="sm" variant="secondary" className="mt-2" onClick={onLeaveOut}>
              {t('evidence.wizard.leaveOutUnsealed')}
            </Button>
          ) : null}
        </Alert>
      ) : null}
    </div>
  );
}

/**
 * "Yeni kanıt paketi" (spec 9.6, 12.3 page 10): scope (a 5651 lookup, or days and sources),
 * content (full days or an extract), password protection, summary. Creating needs a
 * re-authentication within 5 minutes (`withReauth`). A generated password is shown exactly once
 * here — never stored in the browser — and the dialog stays open until the user confirmed that
 * it was saved.
 *
 * Arama's "Kanıt paketine aktar" hands a search's raw lines over in memory (`lib/handoff.ts`):
 * the wizard then opens by itself, the scope being their days and the content an extract of
 * these lines (still editable), with the search's warnings (Wave 2 contract 2.13).
 */
export function EvidenceWizard({
  open,
  lookupId,
  onClose,
  onCreated,
  returnFocus,
}: {
  open: boolean;
  /** Preselected 5651 lookup (from the lookup page's "Kanıt Paketi"). */
  lookupId: number | null;
  onClose: () => void;
  onCreated: (packageId: number) => void;
  /** Focus target on close when there is no opener on the page (`/evidence?lookup=17`). */
  returnFocus?: RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { can, me } = useAuth();
  // The days' ARCILOGGER audit log goes into a package only on an administrator's explicit
  // choice, and only once the API accepts the field (until then it would refuse every package).
  const mayAddAuditLog = evidenceAuditLogAccepted() && me.user.role === 'admin';
  const { withReauth } = useReauth();
  const create = useCreateEvidence();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<WizardDraft>(() => emptyWizard(lookupId));
  const [errors, setErrors] = useState<FieldErrors<WizardField>>({});
  const [failure, setFailure] = useState<{ text: string; details: string[] } | null>(null);
  const [created, setCreated] = useState<EvidenceCreated | null>(null);
  const [saved, setSaved] = useState(false);
  const { reset } = create;
  /** The lines a search handed over (the wizard is open while they are). */
  const [fromSearch, setFromSearch] = useState<SearchExtract | null>(null);
  const [leftOut, setLeftOut] = useState(0);
  useHandoff('/evidence', (extract) => {
    setLeftOut(0);
    setFromSearch(extract);
  });
  const isOpen = open || fromSearch !== null;

  useEffect(() => {
    if (isOpen) {
      setStep(0);
      setDraft(fromSearch ? wizardFromSearch(fromSearch) : emptyWizard(lookupId));
      setErrors({});
      setFailure(null);
      setCreated(null);
      setSaved(false);
      reset();
    }
  }, [isOpen, lookupId, fromSearch, reset]);

  function close() {
    setFromSearch(null);
    onClose();
  }

  function finishCreated(packageId: number) {
    setFromSearch(null);
    onCreated(packageId);
  }

  const lookups = useLookups({}, isOpen && draft.scope === 'lookup');
  const lookupItems = useMemo(() => lookups.data?.pages[0]?.items ?? [], [lookups.data]);
  const suggestionDay =
    isOpen && can('seal.read') && draft.scope === 'range' && !draft.allSources
      ? parseDay(draft.to)
      : null;
  const sealDay = useSealDay(suggestionDay);
  const knownSources = useMemo(
    () => [...new Set((sealDay.data?.files ?? []).map((file) => file.source))].sort(),
    [sealDay.data],
  );

  function set<K extends keyof WizardDraft>(field: K, value: WizardDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  /** The created package while its one-time password is on screen. */
  const shown = created !== null && created.password !== null ? created : null;
  const showingPassword = shown !== null;

  function finish() {
    const packageId = created?.package.id;
    // The password leaves memory with the dialog: component state and the mutation result.
    setCreated(null);
    setSaved(false);
    reset();
    if (packageId !== undefined) finishCreated(packageId);
    else close();
  }

  /** Keyboard and screen-reader users land on the first field with a problem. */
  function focusFirst(found: FieldErrors<WizardField>) {
    focusFirstError(found, (field) => `${id}-${field}`);
  }

  function next() {
    const found =
      step === 0 ? validateScope(draft, siteToday()) : step === 1 ? validateContent(draft) : {};
    setErrors(found);
    if (hasErrors(found)) {
      focusFirst(found);
      return;
    }
    setFailure(null);
    setStep((current) => Math.min(current + 1, STEPS.length - 1));
  }

  async function submit() {
    const scopeErrors = validateScope(draft, siteToday());
    const found = { ...scopeErrors, ...validateContent(draft) };
    setErrors(found);
    if (hasErrors(found)) {
      setStep(hasErrors(scopeErrors) ? 0 : 1);
      focusFirst(hasErrors(scopeErrors) ? scopeErrors : found);
      return;
    }
    setFailure(null);
    try {
      const result = await withReauth(() =>
        create.mutateAsync(toEvidenceBody(draft, mayAddAuditLog)),
      );
      if (result.password) {
        setCreated(result);
      } else {
        reset();
        finishCreated(result.package.id);
      }
    } catch (error) {
      if (isReauthCancelled(error)) return;
      setFailure({ text: errorMessage(error), details: failureDetails(error, t) });
    }
  }

  const message = (field: WizardField) => {
    const key = errors[field];
    return key ? t(key, { max: MAX_DAYS }) : undefined;
  };
  const describe = (field: WizardField, hint = false) =>
    errors[field] ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;

  const english = currentLang() === 'en';
  const sources = sourcesSummary(draft);
  const summary: [string, ReactNode][] = [
    [
      t('evidence.wizard.scopeTitle'),
      draft.scope === 'lookup'
        ? t('evidence.scope.lookup', { id: draft.lookupId })
        : t('evidence.wizard.rangeSummary', {
            from: formatDay(parseDay(draft.from) ?? draft.from),
            to: formatDay(parseDay(draft.to) ?? draft.to),
          }),
    ],
    [t('evidence.detail.sources'), t(sources.key, sources.values)],
    [
      t('evidence.detail.content'),
      draft.content === 'extract' ? t('evidence.wizard.extract') : t('evidence.wizard.fullDays'),
    ],
    ...(draft.scope === 'range' && draft.content === 'extract'
      ? ([[t('evidence.detail.extractLines'), String(parseLines(draft.lines).lines.length)]] as [
          string,
          ReactNode,
        ][])
      : []),
    [
      t('evidence.detail.password'),
      draft.password ? t('evidence.wizard.passwordYes') : t('evidence.detail.passwordNo'),
    ],
    [t('evidence.detail.reference'), draft.reference.trim() || '—'],
    // Said to everybody: by default a package does not hold the ARCILOGGER audit log.
    [
      t('evidence.wizard.auditLog'),
      mayAddAuditLog && draft.includeAuditLog
        ? t('evidence.wizard.auditLogYes')
        : t('evidence.wizard.auditLogNo'),
    ],
  ];

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(next) => {
        if (!next && !showingPassword) close();
      }}
    >
      <DialogContent
        className="max-w-2xl"
        returnFocus={returnFocus}
        hideClose={showingPassword}
        onEscapeKeyDown={(event) => {
          if (showingPassword) event.preventDefault();
        }}
        onInteractOutside={(event) => {
          if (showingPassword) event.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {showingPassword ? (
              <KeyRound aria-hidden="true" className="size-5 text-primary" />
            ) : (
              <PackagePlus aria-hidden="true" className="size-5 text-primary" />
            )}
            {showingPassword ? t('evidence.password.title') : t('evidence.wizard.title')}
          </DialogTitle>
          <DialogDescription>
            {shown
              ? t('evidence.password.description', {
                  name: shown.package.package_name ?? `#${shown.package.id}`,
                })
              : t('evidence.wizard.description')}
          </DialogDescription>
        </DialogHeader>

        {shown ? (
          <div className="flex flex-col gap-4">
            <Alert tone="warning" title={t('evidence.password.onceTitle')}>
              {(english ? shown.password_notice_en : shown.password_notice_tr) ??
                t('evidence.password.onceBody')}
            </Alert>
            <CodeBlock label={t('evidence.password.label')} code={shown.password ?? ''} />
            <div className="flex items-start gap-2">
              <Checkbox
                id={`${id}-saved`}
                checked={saved}
                onCheckedChange={(value) => {
                  setSaved(value === true);
                }}
                className="mt-0.5"
              />
              <Label htmlFor={`${id}-saved`} className="font-normal">
                {t('evidence.password.saved')}
              </Label>
            </div>
            <DialogFooter>
              <Button disabled={!saved} onClick={finish}>
                {t('common.finish')}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <Stepper
              label={t('evidence.wizard.progress')}
              current={step}
              steps={STEPS.map((name) => ({ id: name, title: t(`evidence.wizard.steps.${name}`) }))}
            />
            {fromSearch ? (
              <FromSearch
                extract={fromSearch}
                leftOut={leftOut}
                onLeaveOut={() => {
                  const narrowed = leaveOutUnsealed(fromSearch);
                  if (!narrowed) return;
                  setLeftOut((count) => count + narrowed.leftOut);
                  setFromSearch(narrowed.extract); // the scope and the lines follow (wizardFromSearch)
                }}
              />
            ) : null}

            {step === 0 ? (
              <div className="flex flex-col gap-4">
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-1 text-sm font-semibold text-foreground">
                    {t('evidence.wizard.scopeTitle')}
                  </legend>
                  <Choice
                    name={`${id}-scope`}
                    value="lookup"
                    checked={draft.scope === 'lookup'}
                    onSelect={() => {
                      set('scope', 'lookup');
                    }}
                    title={t('evidence.wizard.fromLookup')}
                    description={t('evidence.wizard.fromLookupHint')}
                  />
                  <Choice
                    name={`${id}-scope`}
                    value="range"
                    checked={draft.scope === 'range'}
                    onSelect={() => {
                      set('scope', 'range');
                    }}
                    title={t('evidence.wizard.fromRange')}
                    description={t('evidence.wizard.fromRangeHint', { max: MAX_DAYS })}
                  />
                </fieldset>
                {draft.scope === 'lookup' ? (
                  <Field
                    id={`${id}-lookupId`}
                    label={t('evidence.wizard.lookup')}
                    error={message('lookupId')}
                    required
                  >
                    <Select
                      value={draft.lookupId || undefined}
                      onValueChange={(value) => {
                        set('lookupId', value);
                      }}
                    >
                      <SelectTrigger
                        id={`${id}-lookupId`}
                        aria-required
                        aria-invalid={errors.lookupId ? true : undefined}
                        aria-describedby={describe('lookupId')}
                      >
                        <SelectValue placeholder={t('evidence.wizard.lookupPlaceholder')} />
                      </SelectTrigger>
                      <SelectContent>
                        {draft.lookupId &&
                        !lookupItems.some((item) => String(item.id) === draft.lookupId) ? (
                          <SelectItem value={draft.lookupId}>
                            {t('evidence.scope.lookup', { id: draft.lookupId })}
                          </SelectItem>
                        ) : null}
                        {lookupItems.map((item) => (
                          <SelectItem key={item.id} value={String(item.id)}>
                            {`#${item.id} · ${item.authority} · ${item.file_no}`}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <Field
                      id={`${id}-from`}
                      label={t('evidence.wizard.from')}
                      error={message('from')}
                      hint={t('dates.dayHint')}
                      required
                    >
                      <Input
                        id={`${id}-from`}
                        inputMode="numeric"
                        autoComplete="off"
                        placeholder={t('dates.dayPlaceholder')}
                        value={draft.from}
                        aria-invalid={errors.from ? true : undefined}
                        aria-describedby={describe('from', true)}
                        onChange={(event) => {
                          set('from', event.target.value);
                        }}
                      />
                    </Field>
                    <Field
                      id={`${id}-to`}
                      label={t('evidence.wizard.to')}
                      error={message('to')}
                      hint={t('dates.dayHint')}
                      required
                    >
                      <Input
                        id={`${id}-to`}
                        inputMode="numeric"
                        autoComplete="off"
                        placeholder={t('dates.dayPlaceholder')}
                        value={draft.to}
                        aria-invalid={errors.to ? true : undefined}
                        aria-describedby={describe('to', true)}
                        onChange={(event) => {
                          set('to', event.target.value);
                        }}
                      />
                    </Field>
                  </div>
                )}
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id={`${id}-all`}
                      checked={draft.allSources}
                      aria-describedby={`${id}-all-hint`}
                      onCheckedChange={(value) => {
                        set('allSources', value === true);
                      }}
                    />
                    <Label htmlFor={`${id}-all`} className="font-normal">
                      {t('evidence.wizard.allSources')}
                    </Label>
                  </div>
                  <p id={`${id}-all-hint`} className="text-xs text-muted">
                    {draft.scope === 'lookup'
                      ? t('evidence.wizard.allSourcesLookupHint')
                      : t('evidence.wizard.allSourcesHint')}
                  </p>
                </div>
                {draft.allSources ? null : (
                  <Field
                    id={`${id}-sources`}
                    label={t('evidence.wizard.sources')}
                    hint={
                      draft.scope === 'lookup'
                        ? t('evidence.wizard.sourcesLookupHint')
                        : knownSources.length > 0
                          ? t('evidence.wizard.sourcesKnown', {
                              sources: knownSources.join(', '),
                            })
                          : t('evidence.wizard.sourcesHint')
                    }
                    error={message('sources')}
                    required
                  >
                    <Input
                      id={`${id}-sources`}
                      mono
                      spellCheck={false}
                      autoComplete="off"
                      list={`${id}-known`}
                      value={draft.sources}
                      placeholder="fortigate, mikrotik"
                      aria-invalid={errors.sources ? true : undefined}
                      aria-describedby={describe('sources', true)}
                      onChange={(event) => {
                        set('sources', event.target.value);
                      }}
                    />
                    <datalist id={`${id}-known`}>
                      {knownSources.map((source) => (
                        <option key={source} value={source} />
                      ))}
                    </datalist>
                  </Field>
                )}
                <Field
                  id={`${id}-reference`}
                  label={t('evidence.wizard.reference')}
                  hint={
                    draft.scope === 'lookup'
                      ? t('evidence.wizard.referenceLookupHint')
                      : t('evidence.wizard.referenceHint')
                  }
                  error={message('reference')}
                >
                  <Input
                    id={`${id}-reference`}
                    value={draft.reference}
                    maxLength={REFERENCE_MAX}
                    autoComplete="off"
                    aria-invalid={errors.reference ? true : undefined}
                    aria-describedby={describe('reference', true)}
                    onChange={(event) => {
                      set('reference', event.target.value);
                    }}
                  />
                </Field>
              </div>
            ) : null}

            {step === 1 ? (
              <div className="flex flex-col gap-4">
                <fieldset className="flex flex-col gap-2">
                  <legend className="mb-1 text-sm font-semibold text-foreground">
                    {t('evidence.wizard.contentTitle')}
                  </legend>
                  <Choice
                    name={`${id}-content`}
                    value="full_days"
                    checked={draft.content === 'full_days'}
                    onSelect={() => {
                      set('content', 'full_days');
                    }}
                    title={t('evidence.wizard.fullDays')}
                    description={
                      draft.scope === 'lookup'
                        ? t('evidence.wizard.fullDaysLookupHint')
                        : t('evidence.wizard.fullDaysHint')
                    }
                  />
                  <Choice
                    name={`${id}-content`}
                    value="extract"
                    checked={draft.content === 'extract'}
                    onSelect={() => {
                      set('content', 'extract');
                    }}
                    title={t('evidence.wizard.extract')}
                    description={
                      draft.scope === 'lookup'
                        ? t('evidence.wizard.extractLookupHint')
                        : t('evidence.wizard.extractHint')
                    }
                  />
                </fieldset>
                {draft.scope === 'range' && draft.content === 'extract' ? (
                  <Field
                    id={`${id}-lines`}
                    label={t('evidence.wizard.lines')}
                    hint={t('evidence.wizard.linesHint')}
                    error={message('lines')}
                    required
                  >
                    <Textarea
                      id={`${id}-lines`}
                      rows={5}
                      className="font-mono text-13"
                      spellCheck={false}
                      value={draft.lines}
                      placeholder={'2026-10-09 fortigate 17\n2026-10-09 mikrotik 3'}
                      aria-invalid={errors.lines ? true : undefined}
                      aria-describedby={describe('lines', true)}
                      onChange={(event) => {
                        set('lines', event.target.value);
                      }}
                    />
                    <p className="text-xs text-muted">{t('evidence.wizard.fromSearchHintMake')}</p>
                  </Field>
                ) : null}
                {mayAddAuditLog ? (
                  <div className="flex flex-col gap-1">
                    <div className="flex items-start gap-2">
                      <Checkbox
                        id={`${id}-auditLog`}
                        checked={draft.includeAuditLog}
                        aria-describedby={`${id}-auditLog-hint`}
                        onCheckedChange={(value) => {
                          set('includeAuditLog', value === true);
                        }}
                        className="mt-0.5"
                      />
                      <Label htmlFor={`${id}-auditLog`} className="font-normal">
                        {t('evidence.wizard.auditLogLabel')}
                      </Label>
                    </div>
                    <p id={`${id}-auditLog-hint`} className="text-xs text-muted">
                      {t('evidence.wizard.auditLogHint')}
                    </p>
                  </div>
                ) : null}
              </div>
            ) : null}

            {step === 2 ? (
              <div className="flex flex-col gap-3">
                <div className="flex items-start gap-2">
                  <Checkbox
                    id={`${id}-password`}
                    checked={draft.password}
                    onCheckedChange={(value) => {
                      set('password', value === true);
                    }}
                    className="mt-0.5"
                  />
                  <Label htmlFor={`${id}-password`} className="font-normal">
                    {t('evidence.wizard.passwordLabel')}
                  </Label>
                </div>
                <Alert tone="info" assertive={false}>
                  {t('evidence.wizard.passwordHint')}
                </Alert>
              </div>
            ) : null}

            {step === 3 ? (
              <div className="flex flex-col gap-3">
                <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-1.5 text-13">
                  {summary.map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt className="text-muted">{name}</dt>
                      <dd className="break-words text-foreground">{value}</dd>
                    </div>
                  ))}
                </dl>
                <p className="text-13 text-muted">
                  {t('evidence.wizard.summaryNote', { minutes: RECENT_AUTH_MINUTES })}
                </p>
              </div>
            ) : null}

            {failure ? (
              <Alert tone="danger">
                <p>{failure.text}</p>
                {failure.details.map((line) => (
                  <p key={line}>{line}</p>
                ))}
              </Alert>
            ) : null}

            <DialogFooter className="justify-between">
              <Button variant="secondary" onClick={close}>
                {t('common.cancel')}
              </Button>
              <div className="flex gap-2">
                {step > 0 ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setStep((current) => current - 1);
                    }}
                  >
                    {t('common.back')}
                  </Button>
                ) : null}
                {step < STEPS.length - 1 ? (
                  <Button onClick={next}>{t('common.next')}</Button>
                ) : (
                  <Button onClick={() => void submit()} busy={create.isPending}>
                    {create.isPending ? t('common.working') : t('evidence.wizard.create')}
                  </Button>
                )}
              </div>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
