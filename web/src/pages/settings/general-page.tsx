import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { SectionActions } from '@/components/admin/section-actions';
import {
  SettingsCard,
  SettingsFrame,
  UnsavedChangesGuard,
} from '@/components/admin/settings-frame';
import { useMachineSection } from '@/components/admin/use-machine-section';
import { flowState, useServerValues } from '@/components/admin/use-section-flow';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { editable, localized } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import type { GeneralSection } from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import { validateSiteName } from './settings-validation';

const EMPTY: GeneralSection = {
  site_name: '',
  clock: { ntp_server: '', fallback_server: '', alert_offset_ms: 1000 },
};

type GeneralField = 'site_name';

/**
 * Ayarlar → Genel (contract 2.17, `settings.manage`): the organisation's name, shown in reports,
 * e-mails and evidence packages. The administration service writes it into `arcilogger.toml`;
 * the API and the worker take it without a restart.
 */
export function GeneralPage() {
  const { t } = useTranslation();
  const id = useId();
  const section = useMachineSection('general');
  const { config, flow } = section;
  const general = config.data?.sections.general;
  const form = useServerValues(general, EMPTY);
  const [errors, setErrors] = useState<FieldErrors<GeneralField>>({});
  const state = flowState(flow, {
    loading: config.isPending,
    canEdit: section.canEdit,
    agent: section.agent,
    dirty: form.dirty,
    invalid: hasErrors(errors),
    restartRequired: section.restartRequired,
  });
  const locked = !editable(state) || !section.writable;

  function change(next: Partial<GeneralSection>) {
    form.setValues((current) => ({ ...current, ...next }));
    setErrors({});
    flow.edit();
  }

  function save() {
    const found: FieldErrors<GeneralField> = {};
    const name = validateSiteName(form.values.site_name);
    if (name) found.site_name = name;
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    void flow.start({
      etag: section.etag,
      site_name: form.values.site_name.trim(),
      clock: form.values.clock,
      probe: true,
    });
  }

  const served = (field: string) => flow.problems.find((problem) => problem.field === field);
  const message = (field: GeneralField) => {
    const problem = served(field);
    return problem ? localized(problem) : errors[field] && t(errors[field]);
  };

  return (
    <SettingsFrame
      title={t('settings.general.title')}
      description={t('settings.general.description')}
    >
      <UnsavedChangesGuard when={form.dirty && editable(state)} />
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : (
        <SettingsCard
          title={t('settings.general.siteTitle')}
          description={t('settings.general.siteDescription')}
          testId="settings-site-name"
        >
          <Field
            id={`${id}-site_name`}
            label={t('settings.general.siteName')}
            hint={t('settings.general.siteNameHint')}
            error={message('site_name')}
            required
          >
            <Input
              id={`${id}-site_name`}
              value={form.values.site_name}
              disabled={locked}
              maxLength={400}
              autoComplete="organization"
              aria-invalid={message('site_name') ? true : undefined}
              aria-describedby={
                message('site_name') ? `${id}-site_name-error` : `${id}-site_name-hint`
              }
              onChange={(event) => {
                change({ site_name: event.target.value });
              }}
            />
          </Field>
          <SectionActions
            state={state}
            flow={flow}
            dirty={form.dirty}
            onSave={save}
            onRestore={() => {
              form.restore();
              setErrors({});
            }}
            onReload={() => {
              // "Sayfayı yenile": the values saved meanwhile replace what was typed.
              void config.refetch().then((result) => {
                if (result.data) form.setValues(result.data.sections.general);
                setErrors({});
              });
            }}
          />
        </SettingsCard>
      )}
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
