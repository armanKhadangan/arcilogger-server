import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { ChangeReviewDialog } from '@/components/admin/change-review';
import { fieldMessage } from '@/components/admin/field-messages';
import { SwitchField, TextField } from '@/components/admin/form-fields';
import { LockedList } from '@/components/admin/locked-field';
import { RequestProgress } from '@/components/admin/request-progress';
import { SettingsCard, SettingsFrame } from '@/components/admin/settings-frame';
import { useSectionFlow } from '@/components/admin/use-section-flow';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { Switch } from '@/components/ui/switch';
import { agentWritable } from '@/lib/admin-requests';
import { errorMessage, isApiError } from '@/lib/api';
import {
  useAddSource,
  useAdminConfig,
  useAdminStatus,
  useCheckSection,
  useRemoveSource,
  useUpdateSource,
} from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import { useHandoff, type HandoffPayloads } from '@/lib/handoff';
import type {
  FirewallState,
  SourceBody,
  SourceOut,
  SourceTransport,
  SourceType,
} from '@/lib/settings-types';
import { focusFirstError, hasErrors, type FieldErrors } from '@/lib/validation';

import {
  HEARTBEAT_MAX,
  HEARTBEAT_MIN,
  NEW_SOURCE,
  SOURCE_TYPES,
  TRANSPORTS,
  fromSourceForm,
  sourceConfig,
  toSourceForm,
  validateSource,
  type SourceField,
  type SourceForm,
} from './sources-model';

const PERMISSION = 'sources.manage';

const FIREWALL: Record<FirewallState, NonNullable<BadgeProps['tone']>> = {
  open: 'success',
  closed: 'neutral',
  pending: 'info',
  not_managed: 'outline',
};

/** A change of one source: added, or changed in place (the name never changes). */
interface SourceChange {
  mode: 'add' | 'update';
  body: SourceBody;
  /** Typed in the dialog, whose fields show the refusals (`false`: a row's enable switch). */
  dialog: boolean;
}

interface Removal {
  name: string;
  etag: string;
  confirm: string;
}

interface Editing {
  mode: 'add' | 'update';
  /** The source being changed, `null` for a new one. */
  name: string | null;
  values: SourceForm;
  /** Filled in from Kaynaklar's "Kaynak olarak tanımla": the unknown sender's address. */
  prefilledFrom?: string;
}

type SourcePrefill = HandoffPayloads['/settings/sources']['prefill'];

/** The configuration paths the API names for a field of the source `name` (422/409 `details`). */
function sourcePaths(name: string): Record<SourceField, string[]> {
  const source = `sources.${name.trim()}`;
  return {
    name: [`${source}.name`],
    address: [`${source}.address`],
    heartbeat: [`${source}.heartbeat_minutes`],
  };
}

function SourceDialog({
  editing,
  onChange,
  onClose,
  onSubmit,
  errors,
  busy,
  problemMessage,
}: {
  editing: Editing;
  onChange: (values: SourceForm) => void;
  onClose: () => void;
  onSubmit: () => void;
  errors: Record<SourceField, string | undefined>;
  busy: boolean;
  problemMessage: string | null;
}) {
  const { t } = useTranslation();
  // One dialog at a time: fixed ids, which the page focuses after a refusal.
  const id = 'source';
  const values = editing.values;
  const set = (next: Partial<SourceForm>) => {
    onChange({ ...values, ...next });
  };
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>
              {editing.mode === 'add'
                ? t('settings.sources.addTitle')
                : t('settings.sources.editTitle', { name: editing.name })}
            </DialogTitle>
            <DialogDescription>{t('settings.sources.dialogDescription')}</DialogDescription>
          </DialogHeader>
          {editing.prefilledFrom ? (
            <Alert tone="info" assertive={false} data-testid="source-prefilled">
              {t('settings.sources.prefillNotice', { address: editing.prefilledFrom })}
            </Alert>
          ) : null}
          <TextField
            id={`${id}-name`}
            label={t('settings.sources.columns.name')}
            hint={
              editing.mode === 'add'
                ? t('settings.sources.nameHint')
                : t('settings.sources.nameFixed')
            }
            value={values.name}
            disabled={editing.mode === 'update'}
            error={errors.name}
            mono
            spellCheck={false}
            autoComplete="off"
            required
            onChange={(name) => {
              set({ name });
            }}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id={`${id}-type`} label={t('settings.sources.columns.type')}>
              <Select
                value={values.type}
                onValueChange={(type) => {
                  set({ type: type as SourceType });
                }}
              >
                <SelectTrigger id={`${id}-type`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SOURCE_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {t(`settings.sources.types.${type}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id={`${id}-transport`} label={t('settings.sources.columns.transport')}>
              <Select
                value={values.transport}
                onValueChange={(transport) => {
                  set({ transport: transport as SourceTransport });
                }}
              >
                <SelectTrigger id={`${id}-transport`}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TRANSPORTS.map((transport) => (
                    <SelectItem key={transport} value={transport}>
                      {t(`settings.sources.transports.${transport}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <TextField
            id={`${id}-address`}
            label={t('settings.sources.columns.address')}
            hint={t('settings.sources.addressHint')}
            value={values.address}
            error={errors.address}
            mono
            spellCheck={false}
            autoComplete="off"
            required
            onChange={(address) => {
              set({ address });
            }}
          />
          <TextField
            id={`${id}-heartbeat`}
            label={t('settings.sources.heartbeat')}
            hint={t('settings.sources.heartbeatHint')}
            value={values.heartbeat}
            error={errors.heartbeat}
            inputMode="numeric"
            inputClassName="w-32"
            onChange={(heartbeat) => {
              set({ heartbeat });
            }}
          />
          <SwitchField
            id={`${id}-expected`}
            label={t('settings.sources.columns.expected')}
            hint={t('settings.sources.expectedHint')}
            checked={values.expected}
            onChange={(expected) => {
              set({ expected });
            }}
          />
          <SwitchField
            id={`${id}-enabled`}
            label={t('settings.sources.columns.enabled')}
            hint={t('settings.sources.enabledHint')}
            checked={values.enabled}
            onChange={(enabled) => {
              set({ enabled });
            }}
          />
          {problemMessage ? <Alert tone="danger">{problemMessage}</Alert> : null}
          <DialogFooter>
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={busy}>
              {busy ? t('common.working') : t('settings.sources.review')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Ayarlar → Kaynaklar (contract 2.17, `sources.manage`): the `[[sources]]` of `arcilogger.toml`
 * with the collector's last message and the firewall state of each, added, changed, switched on
 * and off and removed one at a time. The collector and ingest take a change without a restart;
 * the system task opens or closes the syslog port for the source's address. Switching a source
 * off and removing it need a password confirmation; removing it also the name typed.
 *
 * "Kaynak olarak tanımla" on Kaynaklar (contract 2.16.4) hands over an unknown sender
 * (`lib/handoff.ts`, in memory): once the configuration is read, the add dialog opens with its
 * suggested name, type and address for the administrator to review. The old `unknown-<ip>`
 * folder and its records stay where they are.
 */
export function SourcesPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const config = useAdminConfig();
  const status = useAdminStatus();
  const check = useCheckSection();
  const add = useAddSource();
  const update = useUpdateSource();
  const remove = useRemoveSource();
  const { mutateAsync: checkAsync } = check;
  const { mutateAsync: addAsync } = add;
  const { mutateAsync: updateAsync } = update;
  const { mutateAsync: removeAsync } = remove;
  const change = useSectionFlow<SourceChange>({
    check: useCallback(
      (body: SourceChange) =>
        checkAsync({ section: 'sources', body: { ...body.body, operation: body.mode } }),
      [checkAsync],
    ),
    submit: useCallback(
      (body: SourceChange) =>
        body.mode === 'add'
          ? addAsync(body.body)
          : updateAsync({ name: body.body.source.name, body: body.body }),
      [addAsync, updateAsync],
    ),
    fields: useCallback(
      (body: SourceChange) =>
        body.dialog ? Object.values(sourcePaths(body.body.source.name)).flat() : [],
      [],
    ),
  });
  const removal = useSectionFlow<Removal>({
    check: null,
    submit: removeAsync,
  });
  const [editing, setEditing] = useState<Editing | null>(null);
  const [errors, setErrors] = useState<FieldErrors<SourceField>>({});
  const [typed, setTyped] = useState('');
  const [last, setLast] = useState<'change' | 'remove'>('change');
  const [prefill, setPrefill] = useState<SourcePrefill | null>(null);
  /** A hand-over that could not open the dialog (no right to change, the service is down). */
  const [prefillBlocked, setPrefillBlocked] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const agent = status.data?.agent.state ?? null;
  const canEdit = can(PERMISSION);
  const writable = canEdit && agentWritable(agent);
  const sources = config.data?.sections.sources ?? [];
  const policy = config.data?.policy;
  const etag = config.data?.etags.sources ?? '';
  const tracked = last === 'change' ? change.request : removal.request;
  const busy =
    (tracked !== undefined && !tracked.terminal) ||
    change.phase === 'checking' ||
    change.phase === 'submitting' ||
    removal.phase === 'submitting';
  const systemTask = status.data?.agent.system_task;
  const removing = removal.body;
  const ready = config.data !== undefined && status.data !== undefined;

  useHandoff('/settings/sources', (payload) => {
    setPrefill(payload.prefill);
  });
  useEffect(() => {
    if (prefill === null || !ready) return;
    setPrefill(null);
    if (!writable) {
      setPrefillBlocked(prefill.address);
      return;
    }
    setPrefillBlocked(null);
    setErrors({});
    setEditing({
      mode: 'add',
      name: null,
      values: { ...NEW_SOURCE, name: prefill.name, type: prefill.type, address: prefill.address },
      prefilledFrom: prefill.address,
    });
  }, [prefill, ready, writable]);

  function open(next: Editing) {
    change.reset();
    removal.reset();
    setErrors({});
    setEditing(next);
  }

  function review() {
    if (!editing) return;
    const found = validateSource(editing.values, sources, policy, editing.name);
    if (editing.mode === 'add' && policy && sources.length >= policy.max_sources)
      found.name = 'settings.sources.errors.limit';
    setErrors(found);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `source-${field}`);
      return;
    }
    setLast('change');
    void change.start({
      mode: editing.mode,
      body: { etag, source: fromSourceForm(editing.values) },
      dialog: true,
    });
  }

  function toggle(source: SourceOut, enabled: boolean) {
    change.reset();
    removal.reset();
    setEditing(null);
    setLast('change');
    void change.start({
      mode: 'update',
      body: { etag, source: { ...sourceConfig(source), enabled } },
      dialog: false,
    });
  }

  const message = (field: SourceField) =>
    fieldMessage(
      t,
      change.problems,
      errors[field],
      sourcePaths(editing?.values.name ?? '')[field],
      {
        min: HEARTBEAT_MIN,
        max: HEARTBEAT_MAX,
      },
    );
  // The dialog stays open while the change is checked and comes back after a refusal.
  const dialogOpen =
    editing !== null &&
    change.requestId === null &&
    (change.phase === 'edit' || change.phase === 'checking');
  const locked = config.data?.locked.filter((item) => item.path.startsWith('collector.')) ?? [];

  return (
    <SettingsFrame
      title={t('settings.sources.title')}
      description={t('settings.sources.description')}
    >
      {config.isPending ? (
        <PageSkeleton />
      ) : config.isError ? (
        <Alert tone="danger">{errorMessage(config.error)}</Alert>
      ) : (
        <>
          {prefillBlocked ? (
            <Alert tone="warning" assertive={false} data-testid="source-prefill-blocked">
              {t('settings.sources.prefillBlocked', { address: prefillBlocked })}
            </Alert>
          ) : null}
          {systemTask && !systemTask.ok ? (
            <Alert tone="warning" assertive={false} data-testid="system-task-warning">
              {t('settings.sources.systemTask')}
            </Alert>
          ) : null}
          {change.conflict ? (
            <Alert
              tone="warning"
              title={t('admin.section.conflictTitle')}
              data-testid="section-conflict"
            >
              <span className="flex flex-wrap items-center gap-2">
                {t('admin.section.conflict')}
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    change.reset();
                    setEditing(null);
                    void config.refetch();
                  }}
                >
                  {t('admin.progress.reload')}
                </Button>
              </span>
            </Alert>
          ) : null}
          {!dialogOpen && change.failure ? (
            <Alert tone="danger">{errorMessage(change.failure)}</Alert>
          ) : null}
          {removal.failure ? <Alert tone="danger">{errorMessage(removal.failure)}</Alert> : null}
          <SettingsCard
            title={t('settings.sources.tableTitle')}
            description={t('settings.sources.tableDescription', {
              count: sources.length,
              max: policy?.max_sources ?? 0,
            })}
            testId="sources-table"
            actions={
              canEdit ? (
                <Button
                  size="sm"
                  disabled={!writable || busy}
                  onClick={(event) => {
                    opener.current = event.currentTarget;
                    open({ mode: 'add', name: null, values: NEW_SOURCE });
                  }}
                >
                  <Plus aria-hidden="true" />
                  {t('settings.sources.add')}
                </Button>
              ) : undefined
            }
          >
            {sources.length === 0 ? (
              <p className="text-13 text-muted">{t('settings.sources.empty')}</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-13">
                  <caption className="sr-only">{t('settings.sources.caption')}</caption>
                  <thead className="bg-subtle text-left text-xs text-muted-strong">
                    <tr>
                      {(
                        [
                          'name',
                          'type',
                          'address',
                          'transport',
                          'heartbeat',
                          'expected',
                          'enabled',
                          'lastSeen',
                          'firewall',
                        ] as const
                      ).map((column) => (
                        <th
                          key={column}
                          scope="col"
                          className="px-3 py-2 font-medium whitespace-nowrap"
                        >
                          {t(`settings.sources.columns.${column}`)}
                        </th>
                      ))}
                      {canEdit ? (
                        <th scope="col" className="px-3 py-2 font-medium">
                          <span className="sr-only">{t('settings.sources.columns.actions')}</span>
                        </th>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((source) => (
                      <tr key={source.name} className="border-t border-border align-middle">
                        <th
                          scope="row"
                          className="px-3 py-2 text-left font-mono text-xs font-medium"
                        >
                          {source.name}
                        </th>
                        <td className="px-3 py-2">{t(`settings.sources.types.${source.type}`)}</td>
                        <td className="px-3 py-2 font-mono text-xs">{source.address}</td>
                        <td className="px-3 py-2">
                          {t(`settings.sources.transports.${source.transport}`)}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap tabular">
                          {t('settings.sources.minutes', { count: source.heartbeat_minutes })}
                        </td>
                        <td className="px-3 py-2">
                          {source.expected ? t('settings.sources.yes') : t('settings.sources.no')}
                        </td>
                        <td className="px-3 py-2">
                          <Switch
                            checked={source.enabled}
                            disabled={!writable || busy}
                            aria-label={t('settings.sources.enabledFor', { name: source.name })}
                            onCheckedChange={(enabled) => {
                              toggle(source, enabled);
                            }}
                          />
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap tabular">
                          {source.last_seen
                            ? formatDateTime(source.last_seen)
                            : t('settings.sources.never')}
                        </td>
                        <td className="px-3 py-2">
                          <Badge tone={FIREWALL[source.firewall]}>
                            {t(`settings.sources.firewall.${source.firewall}`)}
                          </Badge>
                        </td>
                        {canEdit ? (
                          <td className="px-3 py-2">
                            <span className="flex justify-end gap-1">
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={!writable || busy}
                                aria-label={t('settings.sources.editFor', { name: source.name })}
                                onClick={(event) => {
                                  opener.current = event.currentTarget;
                                  open({
                                    mode: 'update',
                                    name: source.name,
                                    values: toSourceForm(source),
                                  });
                                }}
                              >
                                <Pencil aria-hidden="true" />
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={!writable || busy}
                                aria-label={t('settings.sources.removeFor', { name: source.name })}
                                onClick={() => {
                                  change.reset();
                                  setEditing(null);
                                  setTyped('');
                                  setLast('remove');
                                  void removal.start({ name: source.name, etag, confirm: '' });
                                }}
                              >
                                <Trash2 aria-hidden="true" />
                              </Button>
                            </span>
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!canEdit ? <p className="text-13 text-muted">{t('admin.section.readOnly')}</p> : null}
            {last === 'change' && change.requestId ? (
              <RequestProgress
                requestId={change.requestId}
                onReload={() => {
                  change.reset();
                  void config.refetch();
                }}
              />
            ) : null}
            {last === 'remove' && removal.requestId ? (
              <RequestProgress requestId={removal.requestId} />
            ) : null}
          </SettingsCard>
          {locked.length > 0 ? (
            <SettingsCard title={t('settings.sources.lockedTitle')} testId="sources-locked">
              <LockedList paths={locked} />
            </SettingsCard>
          ) : null}
        </>
      )}
      {dialogOpen ? (
        <SourceDialog
          editing={editing}
          errors={{
            name: message('name'),
            address: message('address'),
            heartbeat: message('heartbeat'),
          }}
          busy={change.phase === 'checking'}
          problemMessage={change.failure ? errorMessage(change.failure) : null}
          onChange={(values) => {
            setEditing({ ...editing, values });
            setErrors({});
            change.edit();
          }}
          onClose={() => {
            setEditing(null);
            change.reset();
            opener.current?.focus();
          }}
          onSubmit={review}
        />
      ) : null}
      <ChangeReviewDialog
        open={change.phase === 'confirm' || change.phase === 'submitting'}
        onOpenChange={(next) => {
          if (!next) change.dismiss();
        }}
        check={change.check}
        busy={change.phase === 'submitting'}
        onConfirm={() => void change.confirm()}
      />
      <ChangeReviewDialog
        open={removing !== null && (removal.phase === 'confirm' || removal.phase === 'submitting')}
        onOpenChange={(next) => {
          if (!next) removal.dismiss();
        }}
        check={null}
        title={t('settings.sources.removeTitle', { name: removing?.name ?? '' })}
        description={t('settings.sources.removeBody')}
        confirmLabel={t('settings.sources.remove')}
        danger
        busy={removal.phase === 'submitting'}
        confirmDisabled={typed !== removing?.name}
        onConfirm={() => void removal.confirm({ confirm: typed })}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="source-remove-name">
            {t('settings.sources.removeType', { name: removing?.name ?? '' })}
          </Label>
          <Input
            id="source-remove-name"
            value={typed}
            mono
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => {
              setTyped(event.target.value);
            }}
          />
          {isApiError(removal.failure, 'source_confirm_mismatch') ? (
            <p className="text-xs text-danger-ink">{errorMessage(removal.failure)}</p>
          ) : null}
        </div>
      </ChangeReviewDialog>
    </SettingsFrame>
  );
}
