import { RefreshCw, RotateCcw, Save } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { SettingsCard } from '@/components/admin/settings-frame';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError } from '@/lib/api';
import { usePatchSettings, useResetSetting } from '@/lib/api-hooks';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import type { SettingChange, SettingOut, SettingProblem } from '@/lib/settings-types';
import { focusFirstError } from '@/lib/validation';

import { SettingField } from './setting-editors';
import { fromDraft, sameValue, settingControlId, toDraft, type Draft } from './setting-values';

function drafts(settings: readonly SettingOut[]): Record<string, Draft> {
  return Object.fromEntries(settings.map((setting) => [setting.key, toDraft(setting)]));
}

function isSettingProblem(value: unknown): value is SettingProblem {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as SettingProblem).key === 'string' &&
    typeof (value as SettingProblem).message_tr === 'string'
  );
}

/** The refused keys of a 422 `setting_invalid` (`details[{key, code, message_tr, message_en}]`). */
function settingProblems(error: unknown): SettingProblem[] {
  if (!isApiError(error, 'setting_invalid') || !Array.isArray(error.details)) return [];
  return (error.details as unknown[]).filter(isSettingProblem);
}

/**
 * The settings of one group with one "Kaydet" (contract 2.13): only the changed keys are sent,
 * each with the value shown when editing began (`old`); all are saved or none. A key whose
 * change needs a re-authentication asks for the password once, then the change is sent again.
 */
export function SettingsGroupForm({
  title,
  description,
  settings,
  canEdit,
  onDirty,
  testId,
  footer,
}: {
  title: string;
  description?: ReactNode;
  settings: readonly SettingOut[];
  canEdit: boolean;
  /** The group has unsaved changes (the page's unsaved-change guard). */
  onDirty?: (dirty: boolean) => void;
  testId?: string;
  /** More of the group below its settings (e.g. "Kiraları yeniden oluştur"). */
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const patch = usePatchSettings();
  const reset = useResetSetting();
  const [values, setValues] = useState<Record<string, Draft>>(() => drafts(settings));
  const [errors, setErrors] = useState<Record<string, string>>({});
  /** The codes of the server's refusals, by key (the exits' editor shows its code). */
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);
  const [resetting, setResetting] = useState<string | null>(null);
  const shown = useRef(JSON.stringify(drafts(settings)));
  const serverText = JSON.stringify(drafts(settings));

  /** Keys whose draft is not the stored value (a draft that does not parse counts as changed). */
  const changed = useMemo(
    () =>
      settings.filter((setting) => {
        const draft = values[setting.key];
        if (draft === undefined) return false;
        const parsed = fromDraft(setting, draft);
        return parsed.error !== undefined
          ? JSON.stringify(draft) !== JSON.stringify(toDraft(setting))
          : !sameValue(parsed.value, setting.value);
      }),
    [settings, values],
  );
  const dirty = changed.length > 0;

  // A new answer of the server (after a save, a reset, someone else's change) is taken when
  // nothing is being edited.
  useEffect(() => {
    if (JSON.stringify(values) === shown.current) setValues(drafts(settings));
    shown.current = serverText;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverText]);

  useEffect(() => {
    onDirty?.(dirty);
  }, [dirty, onDirty]);

  function edit(key: string, draft: Draft) {
    setValues((current) => ({ ...current, [key]: draft }));
    setErrors((current) =>
      Object.fromEntries(Object.entries(current).filter(([name]) => name !== key)),
    );
    setCodes((current) =>
      Object.fromEntries(Object.entries(current).filter(([name]) => name !== key)),
    );
  }

  async function save() {
    setFailure(null);
    setConflict(false);
    const found: Record<string, string> = {};
    const changes: SettingChange[] = [];
    for (const setting of changed) {
      const parsed = fromDraft(setting, values[setting.key] ?? toDraft(setting));
      if (parsed.error !== undefined)
        found[setting.key] = t(parsed.error, {
          min: setting.min,
          max: setting.max,
          ...parsed.params,
        });
      else changes.push({ key: setting.key, old: setting.value, value: parsed.value });
    }
    setErrors(found);
    setCodes({});
    if (Object.keys(found).length > 0) {
      focusFirstError(found, settingControlId);
      return;
    }
    try {
      const result = await withReauth(() => patch.mutateAsync({ changes }));
      toast({
        title: t('settings.application.saved', { count: result.changed.length }),
        variant: 'success',
      });
    } catch (error) {
      if (isReauthCancelled(error)) return;
      const problems = settingProblems(error);
      if (problems.length > 0) {
        const english = currentLang() === 'en';
        const byKey = Object.fromEntries(
          problems.map((problem) => [
            problem.key,
            english ? problem.message_en : problem.message_tr,
          ]),
        );
        setErrors(byKey);
        setCodes(Object.fromEntries(problems.map((problem) => [problem.key, problem.code])));
        focusFirstError(byKey, settingControlId);
      } else if (isApiError(error, 'setting_conflict')) setConflict(true);
      setFailure(error);
    }
  }

  async function resetKey(setting: SettingOut) {
    setFailure(null);
    setResetting(setting.key);
    try {
      await withReauth(() => reset.mutateAsync(setting.key));
      toast({ title: t('settings.application.resetDone'), variant: 'success' });
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(error);
    } finally {
      setResetting(null);
    }
  }

  const busy = patch.isPending;
  const group = { draft: (key: string) => values[key], edit };
  return (
    <SettingsCard title={title} description={description} testId={testId}>
      <div className="flex flex-col gap-5">
        {settings.map((setting) => (
          <SettingField
            key={setting.key}
            setting={setting}
            draft={values[setting.key] ?? toDraft(setting)}
            onChange={(draft) => {
              edit(setting.key, draft);
            }}
            error={errors[setting.key]}
            errorCode={codes[setting.key]}
            group={group}
            disabled={!canEdit || busy}
            onReset={() => void resetKey(setting)}
            resetting={resetting === setting.key}
          />
        ))}
      </div>
      {conflict ? (
        <Alert tone="warning" title={t('settings.application.conflictTitle')}>
          <span className="flex flex-wrap items-center gap-2">
            {errorMessage(failure)}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setValues(drafts(settings));
                setConflict(false);
                setFailure(null);
              }}
            >
              <RefreshCw aria-hidden="true" />
              {t('settings.application.takeCurrent')}
            </Button>
          </span>
        </Alert>
      ) : failure ? (
        <Alert tone="danger">{errorMessage(failure)}</Alert>
      ) : null}
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void save()} disabled={!dirty} busy={busy}>
            <Save aria-hidden="true" />
            {busy ? t('common.working') : t('common.save')}
          </Button>
          {dirty ? (
            <Button
              variant="ghost"
              onClick={() => {
                setValues(drafts(settings));
                setErrors({});
                setCodes({});
                setFailure(null);
              }}
            >
              <RotateCcw aria-hidden="true" />
              {t('admin.section.restore')}
            </Button>
          ) : null}
          {dirty ? (
            <span className="text-xs text-muted-strong" role="status">
              {t('settings.application.changedCount', { count: changed.length })}
            </span>
          ) : null}
        </div>
      ) : null}
      {footer}
    </SettingsCard>
  );
}
