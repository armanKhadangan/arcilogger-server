import type { TFunction } from 'i18next';
import { Plus, RotateCcw, ShieldCheck, X } from 'lucide-react';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { LockedValue } from '@/components/admin/locked-field';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { formatDay } from '@/lib/display';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { roleLabel, ROLE_IDS } from '@/lib/permissions';
import type { SettingOut } from '@/lib/settings-types';

import { EgressEditor } from './egress-editor';
import { EGRESSES_KEY, listItems, type EgressRow } from './egress-model';
import { FortiGateObservedPanel, type SrcmacDrafts } from './fortigate-observed-panel';
import { parseInteger } from './settings-validation';
import {
  HOLIDAYS_KEY,
  isWorkHours,
  settingControlId,
  SERVER_SECONDS_KEY,
  SEVERITIES,
  SEVERITY_KEY,
  TOTP_MANDATORY_ROLES,
  TOTP_ROLES_KEY,
  validHoliday,
  valueText,
  WORK_HOURS_KEY,
  type Draft,
  draftText,
  type ServerSeconds,
  type WorkHours,
} from './setting-values';

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
/** FortiGate lease evidence (ADR-0016): its help is a warning while the switch is on. */
const TRAFFIC_EXTENDS_KEY = 'dhcp.fortigate_traffic_extends';
/** Maps that a lease rebuild applies to past days (the rebuild is the group's last section). */
const REBUILD_KEYS = new Set(['dhcp.fortigate_logids', 'dhcp.fortigate_dhcp_msgs']);
const SRCMAC_INTERFACES_KEY = 'lookup.srcmac_interfaces';
const SRCMAC_MAX_IPS_KEY = 'lookup.srcmac_max_ips';

/** The drafts of the other settings of the group (the observed panel adds srcmac interfaces). */
export interface GroupDrafts {
  draft: (key: string) => Draft | undefined;
  edit: (key: string, draft: Draft) => void;
}

/** "srcmac için ekle": the interfaces and the IP limit of the group's drafts, `null` without them. */
function srcmacDrafts(group: GroupDrafts | undefined): SrcmacDrafts | null {
  const interfaces = group?.draft(SRCMAC_INTERFACES_KEY);
  if (!group || typeof interfaces !== 'string') return null;
  const limit = group.draft(SRCMAC_MAX_IPS_KEY);
  const listed = listItems(interfaces, 'interfaces');
  return {
    interfaces: listed,
    maxIps: (typeof limit === 'string' ? parseInteger(limit) : null) ?? 1,
    add: (name) => {
      if (!listed.includes(name)) group.edit(SRCMAC_INTERFACES_KEY, [...listed, name].join('\n'));
    },
  };
}

/** `01-01` → "1 Ocak (her yıl)", `2026-03-20` → "20.03.2026". */
function holidayText(entry: string, t: TFunction): string {
  const parts = entry.split('-');
  if (parts.length === 3) return formatDay(entry);
  const date = new Date(Date.UTC(2000, Number(parts[0]) - 1, Number(parts[1])));
  const name = new Intl.DateTimeFormat(currentLang() === 'en' ? 'en-GB' : 'tr-TR', {
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  }).format(date);
  return t('settings.application.everyYear', { day: name });
}

interface EditorProps<D extends Draft> {
  id: string;
  draft: D;
  onChange: (draft: D) => void;
  disabled: boolean;
  invalid: boolean;
  describedBy: string;
  labelId: string;
}

function WorkHoursEditor({
  id,
  draft,
  onChange,
  disabled,
  invalid,
  describedBy,
  labelId,
}: EditorProps<WorkHours>) {
  const { t } = useTranslation();
  return (
    <fieldset
      className="flex flex-col gap-3"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-invalid={invalid ? true : undefined}
      id={id}
      tabIndex={-1}
    >
      <div
        className="flex flex-wrap gap-3"
        role="group"
        aria-label={t('settings.application.workDays')}
      >
        {WEEKDAYS.map((day) => (
          <div key={day} className="flex items-center gap-1.5">
            <Checkbox
              id={`${id}-day-${day}`}
              checked={draft.days.includes(day)}
              disabled={disabled}
              onCheckedChange={(checked) => {
                const days =
                  checked === true ? [...draft.days, day] : draft.days.filter((d) => d !== day);
                onChange({ ...draft, days });
              }}
            />
            <Label htmlFor={`${id}-day-${day}`} className="font-normal">
              {t(`settings.application.weekdays.${day}`)}
            </Label>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-start`}>{t('settings.application.workStart')}</Label>
          <Input
            id={`${id}-start`}
            className="w-28"
            inputMode="numeric"
            placeholder="08:00"
            value={draft.start}
            disabled={disabled}
            onChange={(event) => {
              onChange({ ...draft, start: event.target.value.trim() });
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`${id}-end`}>{t('settings.application.workEnd')}</Label>
          <Input
            id={`${id}-end`}
            className="w-28"
            inputMode="numeric"
            placeholder="18:00"
            value={draft.end}
            disabled={disabled}
            onChange={(event) => {
              onChange({ ...draft, end: event.target.value.trim() });
            }}
          />
        </div>
      </div>
    </fieldset>
  );
}

function HolidayEditor({
  id,
  draft,
  onChange,
  disabled,
  invalid,
  describedBy,
  labelId,
}: EditorProps<string[]>) {
  const { t } = useTranslation();
  const [entry, setEntry] = useState('');
  const [problem, setProblem] = useState(false);
  function add() {
    const day = entry.trim();
    if (!validHoliday(day)) {
      setProblem(true);
      return;
    }
    setProblem(false);
    setEntry('');
    if (!draft.includes(day)) onChange([...draft, day].sort());
  }
  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
    >
      {draft.length === 0 ? (
        <p className="text-13 text-muted">{t('settings.application.noHolidays')}</p>
      ) : (
        <ul className="flex flex-wrap gap-2" aria-label={t('settings.application.holidayList')}>
          {draft.map((day) => (
            <li
              key={day}
              className="flex items-center gap-1 rounded-md border border-border bg-subtle py-0.5 pr-0.5 pl-2 text-13"
            >
              <span>{holidayText(day, t)}</span>
              <span className="font-mono text-xs text-muted-strong">{day}</span>
              <Button
                variant="ghost"
                size="sm"
                className="size-7 p-0"
                disabled={disabled}
                aria-label={t('settings.application.removeHoliday', { day: holidayText(day, t) })}
                onClick={() => {
                  onChange(draft.filter((item) => item !== day));
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={id}>{t('settings.application.addHoliday')}</Label>
          <Input
            id={id}
            className="w-44"
            placeholder={t('settings.application.holidayPlaceholder')}
            value={entry}
            disabled={disabled}
            aria-invalid={problem || invalid ? true : undefined}
            aria-describedby={problem ? `${id}-entry-error` : `${id}-entry-hint`}
            onChange={(event) => {
              setEntry(event.target.value);
              setProblem(false);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add();
              }
            }}
          />
        </div>
        <Button variant="secondary" disabled={disabled || entry.trim() === ''} onClick={add}>
          <Plus aria-hidden="true" />
          {t('settings.application.add')}
        </Button>
      </div>
      {problem ? (
        <p id={`${id}-entry-error`} className="text-xs text-danger-ink">
          {t('settings.application.errors.holiday')}
        </p>
      ) : (
        <p id={`${id}-entry-hint`} className="text-xs text-muted">
          {t('settings.application.holidayHint')}
        </p>
      )}
    </div>
  );
}

function ServerSecondsEditor({
  id,
  draft,
  onChange,
  disabled,
  describedBy,
  labelId,
}: EditorProps<ServerSeconds[]>) {
  const { t } = useTranslation();
  const update = (index: number, row: Partial<ServerSeconds>) => {
    onChange(draft.map((item, at) => (at === index ? { ...item, ...row } : item)));
  };
  return (
    <div
      className="flex flex-col gap-2"
      role="group"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      id={id}
      tabIndex={-1}
    >
      {draft.length === 0 ? (
        <p className="text-13 text-muted">{t('settings.application.noServers')}</p>
      ) : null}
      {draft.map((row, index) => (
        <div key={index} className="flex flex-wrap items-end gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-server-${index}`}>{t('settings.application.serverName')}</Label>
            <Input
              id={`${id}-server-${index}`}
              className="w-48"
              mono
              value={row.server}
              disabled={disabled}
              onChange={(event) => {
                update(index, { server: event.target.value });
              }}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-seconds-${index}`}>
              {t('settings.application.serverSeconds')}
            </Label>
            <Input
              id={`${id}-seconds-${index}`}
              className="w-32"
              inputMode="numeric"
              value={row.seconds}
              disabled={disabled}
              onChange={(event) => {
                update(index, { seconds: event.target.value });
              }}
            />
          </div>
          <Button
            variant="ghost"
            disabled={disabled}
            aria-label={t('settings.application.removeServer', { server: row.server || index + 1 })}
            onClick={() => {
              onChange(draft.filter((_, at) => at !== index));
            }}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
      ))}
      <div>
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => {
            onChange([...draft, { server: '', seconds: '86400' }]);
          }}
        >
          <Plus aria-hidden="true" />
          {t('settings.application.addServer')}
        </Button>
      </div>
    </div>
  );
}

function RolesEditor({
  id,
  draft,
  onChange,
  disabled,
  describedBy,
  labelId,
}: EditorProps<string[]>) {
  const lang = currentLang();
  return (
    <div
      className="flex flex-wrap gap-4"
      role="group"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      id={id}
      tabIndex={-1}
    >
      {ROLE_IDS.map((role) => {
        const mandatory = (TOTP_MANDATORY_ROLES as readonly string[]).includes(role);
        return (
          <div key={role} className="flex items-center gap-1.5">
            <Checkbox
              id={`${id}-${role}`}
              checked={draft.includes(role)}
              disabled={disabled || mandatory}
              onCheckedChange={(checked) => {
                onChange(
                  checked === true ? [...draft, role] : draft.filter((item) => item !== role),
                );
              }}
            />
            <Label htmlFor={`${id}-${role}`} className="font-normal">
              {roleLabel(role, lang)}
            </Label>
          </div>
        );
      })}
    </div>
  );
}

/** One database setting with its editor, help, default and last change (contract 2.13). */
export function SettingField({
  setting,
  draft,
  onChange,
  error,
  errorCode,
  disabled,
  onReset,
  resetting = false,
  group,
}: {
  setting: SettingOut;
  draft: Draft;
  onChange: (draft: Draft) => void;
  error?: string | undefined;
  /** The code of a refusal of the server (shown for the exits, whose 422 has no field path). */
  errorCode?: string | undefined;
  disabled: boolean;
  onReset: () => void;
  resetting?: boolean;
  /** The other drafts of the settings group (`nat.egresses` adds srcmac interfaces). */
  group?: GroupDrafts;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const labelId = useId();
  const id = settingControlId(setting.key);
  const label = lang === 'en' ? setting.label_en : setting.label_tr;
  const help = lang === 'en' ? setting.help_en : setting.help_tr;
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = error ? `${errorId} ${helpId}` : helpId;
  const invalid = Boolean(error);
  const shown = (value: unknown) =>
    typeof value === 'boolean'
      ? t(value ? 'settings.application.on' : 'settings.application.off')
      : valueText(value);

  if (setting.locked)
    return (
      <div className="flex flex-col gap-1" data-testid={`setting-${setting.key}`}>
        <LockedValue label={label} value={shown(setting.value)} />
        <p className="text-xs text-muted">{help}</p>
      </div>
    );

  let editor: ReactNode;
  const common = { id, disabled, invalid, describedBy, labelId };
  if (setting.key === WORK_HOURS_KEY && isWorkHours(draft))
    editor = <WorkHoursEditor {...common} draft={draft} onChange={onChange} />;
  else if (setting.key === HOLIDAYS_KEY && Array.isArray(draft))
    editor = <HolidayEditor {...common} draft={draft as string[]} onChange={onChange} />;
  else if (setting.key === SERVER_SECONDS_KEY && Array.isArray(draft))
    editor = (
      <ServerSecondsEditor {...common} draft={draft as ServerSeconds[]} onChange={onChange} />
    );
  else if (setting.key === TOTP_ROLES_KEY && Array.isArray(draft))
    editor = <RolesEditor {...common} draft={draft as string[]} onChange={onChange} />;
  else if (setting.key === EGRESSES_KEY && Array.isArray(draft))
    editor = <EgressEditor {...common} draft={draft as EgressRow[]} onChange={onChange} />;
  else if (setting.key === SEVERITY_KEY)
    editor = (
      <Select value={draftText(draft)} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          id={id}
          className="w-56"
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-invalid={invalid ? true : undefined}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SEVERITIES.map((severity) => (
            <SelectItem key={severity} value={severity}>
              {t(`severity.${severity}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  else if (setting.type === 'boolean')
    editor = (
      <Switch
        id={id}
        checked={draft === true}
        disabled={disabled}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        onCheckedChange={onChange}
      />
    );
  else if (setting.type === 'integer')
    editor = (
      <Input
        id={id}
        className="w-48"
        inputMode="numeric"
        value={draftText(draft)}
        disabled={disabled}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    );
  else if (setting.type === 'string')
    editor = (
      <Input
        id={id}
        value={draftText(draft)}
        disabled={disabled}
        mono={setting.key.includes('regex')}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    );
  else
    editor = (
      <Textarea
        id={id}
        rows={setting.type === 'object' ? 5 : 4}
        className="font-mono text-13"
        value={draftText(draft)}
        disabled={disabled}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        aria-invalid={invalid ? true : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    );

  const bounds =
    setting.type === 'integer' && (setting.min !== null || setting.max !== null)
      ? setting.max === null
        ? t('settings.application.min', { min: setting.min })
        : t('settings.application.range', { min: setting.min ?? 0, max: setting.max })
      : null;
  return (
    <div className="flex flex-col gap-1.5" data-testid={`setting-${setting.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span id={labelId} className="text-13 font-medium text-foreground">
          {label}
        </span>
        <span className="font-mono text-xs text-muted-strong">{setting.key}</span>
        {setting.reauth ? (
          <Badge tone="outline" icon={ShieldCheck}>
            {t('settings.application.reauth')}
          </Badge>
        ) : null}
      </div>
      {editor}
      {error ? (
        <p id={errorId} className="text-xs text-danger-ink">
          {error}
          {errorCode && setting.key === EGRESSES_KEY ? (
            <span className="font-mono"> ({errorCode})</span>
          ) : null}
        </p>
      ) : null}
      {setting.key === TRAFFIC_EXTENDS_KEY && draft === true ? (
        <div id={helpId}>
          <Alert tone="warning" assertive={false} data-testid="traffic-extends-warning">
            {help}
          </Alert>
        </div>
      ) : (
        <p id={helpId} className="text-xs text-muted">
          {help}
          {bounds ? ` ${bounds}` : ''}
          {setting.type === 'string_list' || setting.type === 'integer_list'
            ? ` ${t('settings.application.onePerLine')}`
            : ''}
          {REBUILD_KEYS.has(setting.key) ? ` ${t('network.settings.rebuildHint')}` : ''}
        </p>
      )}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-strong">
        <span>{t('settings.application.default', { value: shown(setting.default) })}</span>
        {setting.updated_at && !setting.is_default ? (
          <span>
            {t('settings.application.changed', {
              time: formatDateTime(setting.updated_at),
              by: setting.updated_by ?? '—',
            })}
          </span>
        ) : null}
        {!setting.is_default ? (
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            disabled={disabled}
            busy={resetting}
            aria-describedby={labelId}
            onClick={onReset}
          >
            <RotateCcw aria-hidden="true" />
            {t('settings.application.reset')}
          </Button>
        ) : null}
      </p>
      {setting.key === EGRESSES_KEY && Array.isArray(draft) ? (
        <FortiGateObservedPanel
          rows={draft as EgressRow[]}
          onRows={onChange}
          disabled={disabled}
          editorId={id}
          srcmac={srcmacDrafts(group)}
        />
      ) : null}
    </div>
  );
}
