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
import { cn } from '@/lib/utils';

import { EgressEditor } from './egress-editor';
import { EGRESSES_KEY, type EgressRow } from './egress-model';
import { FortiGateObservedPanel, type SrcmacDrafts } from './fortigate-observed-panel';
import { parseInteger } from './settings-validation';
import {
  dayParts,
  HOLIDAYS_KEY,
  isWorkWeek,
  settingControlId,
  SERVER_SECONDS_KEY,
  SEVERITIES,
  SEVERITY_KEY,
  stringListItems,
  TOTP_MANDATORY_ROLES,
  TOTP_ROLES_KEY,
  validHoliday,
  valueText,
  WORK_BREAKS_MAX,
  WORK_HOURS_KEY,
  type Draft,
  draftText,
  type ServerSeconds,
  type WorkBreak,
  type WorkDayDraft,
  type WorkWeek,
} from './setting-values';

/** FortiGate lease evidence (ADR-0016): its help is a warning while the switch is on. */
const TRAFFIC_EXTENDS_KEY = 'dhcp.fortigate_traffic_extends';
/**
 * Maps that a lease rebuild applies to past days (the rebuild is the group's last section) and
 * whose server help does not say so: `dhcp.fortigate_logids` states it in its own help.
 */
const REBUILD_HINT_KEYS = new Set(['dhcp.fortigate_dhcp_msgs']);
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
  // Read as the setting's own list editor reads it (`fromDraft`).
  const listed = stringListItems(interfaces);
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

/** The strip of a weekday row: work and break time on a 24-hour bar (P1). */
function DayStrip({ row, name }: { row: WorkDayDraft; name: string }) {
  const { t } = useTranslation();
  const parts = dayParts(row);
  const pauses = parts.filter((part) => part.part === 'break');
  const label =
    parts.length === 0
      ? t('settings.application.stripOff', { day: name })
      : pauses.length === 0
        ? t('settings.application.stripWork', { day: name, hours: `${row.start}–${row.end}` })
        : t('settings.application.stripBreaks', {
            day: name,
            hours: `${row.start}–${row.end}`,
            breaks: pauses.map((part) => `${clockOf(part.start)}–${clockOf(part.end)}`).join(', '),
          });
  return (
    <div
      className="relative h-3 w-full min-w-28 overflow-hidden rounded-sm bg-subtle"
      role="img"
      aria-label={label}
      title={label}
    >
      {parts.map((part) => (
        <span
          key={`${part.part}-${part.start}`}
          aria-hidden="true"
          className={cn('absolute inset-y-0', part.part === 'work' ? 'bg-primary' : 'bg-warning')}
          style={{
            left: `${(part.start / 1440) * 100}%`,
            width: `${((part.end - part.start) / 1440) * 100}%`,
          }}
        />
      ))}
    </div>
  );
}

/** `570` → `"09:30"`. */
function clockOf(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

/** A new break: lunch when it fits the day, otherwise empty fields to fill in. */
function newBreak(row: WorkDayDraft): WorkBreak {
  return row.start <= '12:00' && row.end >= '13:00' && row.breaks.length === 0
    ? { start: '12:00', end: '13:00' }
    : { start: '', end: '' };
}

/**
 * The work hours (contract W5 §6 P1): a week table with each day's hours and breaks, "Tüm günlere
 * uygula", and a strip of each day. The value saved keeps the hours most days share as the
 * general ones and the other days as `by_day` (`workHoursOf`).
 */
function WorkHoursEditor({
  id,
  draft,
  onChange,
  disabled,
  invalid,
  describedBy,
  labelId,
}: EditorProps<WorkWeek>) {
  const { t } = useTranslation();
  const name = (index: number) => t(`settings.application.weekdays.${index + 1}`);
  function setRow(index: number, row: WorkDayDraft) {
    onChange({ week: draft.week.map((item, at) => (at === index ? row : item)) });
  }
  function applyToAll(index: number) {
    const source = draft.week[index];
    if (!source) return;
    onChange({
      week: draft.week.map((item) => ({
        ...item,
        start: source.start,
        end: source.end,
        breaks: source.breaks.map((pause) => ({ ...pause })),
      })),
    });
  }
  return (
    <fieldset
      className="flex flex-col gap-3"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-invalid={invalid ? true : undefined}
      id={id}
      tabIndex={-1}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[46rem] text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th scope="col" className="py-1.5 pr-3 font-medium">
                {t('settings.application.workDay')}
              </th>
              <th scope="col" className="py-1.5 pr-3 font-medium">
                {t('settings.application.workStart')}
              </th>
              <th scope="col" className="py-1.5 pr-3 font-medium">
                {t('settings.application.workEnd')}
              </th>
              <th scope="col" className="py-1.5 pr-3 font-medium">
                {t('settings.application.breaks')}
              </th>
              <th scope="col" className="py-1.5 pr-3 font-medium">
                {t('settings.application.preview')}
              </th>
              <th scope="col" className="py-1.5 font-medium">
                <span className="sr-only">{t('settings.application.applyAll')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {draft.week.map((row, index) => {
              const day = name(index);
              const off = disabled || !row.work;
              return (
                <tr key={day} className="border-t border-border align-top">
                  <td className="py-2 pr-3">
                    <div className="flex h-9 items-center gap-1.5">
                      <Checkbox
                        id={`${id}-day-${index + 1}`}
                        checked={row.work}
                        disabled={disabled}
                        onCheckedChange={(checked) => {
                          setRow(index, { ...row, work: checked === true });
                        }}
                      />
                      <Label htmlFor={`${id}-day-${index + 1}`} className="font-normal">
                        {day}
                      </Label>
                    </div>
                  </td>
                  <td className="py-2 pr-3">
                    <Input
                      className="w-20"
                      inputMode="numeric"
                      placeholder="08:00"
                      aria-label={t('settings.application.dayStart', { day })}
                      value={row.start}
                      disabled={off}
                      onChange={(event) => {
                        setRow(index, { ...row, start: event.target.value.trim() });
                      }}
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <Input
                      className="w-20"
                      inputMode="numeric"
                      placeholder="18:00"
                      aria-label={t('settings.application.dayEnd', { day })}
                      value={row.end}
                      disabled={off}
                      onChange={(event) => {
                        setRow(index, { ...row, end: event.target.value.trim() });
                      }}
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-col gap-1.5">
                      {row.breaks.map((pause, at) => {
                        const number = at + 1;
                        const edit = (change: Partial<WorkBreak>) => {
                          setRow(index, {
                            ...row,
                            breaks: row.breaks.map((item, which) =>
                              which === at ? { ...item, ...change } : item,
                            ),
                          });
                        };
                        return (
                          <div key={at} className="flex items-center gap-1">
                            <Input
                              className="w-20"
                              inputMode="numeric"
                              placeholder="12:00"
                              aria-label={t('settings.application.breakStart', { day, number })}
                              value={pause.start}
                              disabled={off}
                              onChange={(event) => {
                                edit({ start: event.target.value.trim() });
                              }}
                            />
                            <span aria-hidden="true">–</span>
                            <Input
                              className="w-20"
                              inputMode="numeric"
                              placeholder="13:00"
                              aria-label={t('settings.application.breakEnd', { day, number })}
                              value={pause.end}
                              disabled={off}
                              onChange={(event) => {
                                edit({ end: event.target.value.trim() });
                              }}
                            />
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={off}
                              aria-label={t('settings.application.removeBreak', { day, number })}
                              onClick={() => {
                                setRow(index, {
                                  ...row,
                                  breaks: row.breaks.filter((_, which) => which !== at),
                                });
                              }}
                            >
                              <X aria-hidden="true" />
                            </Button>
                          </div>
                        );
                      })}
                      <div>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={off || row.breaks.length >= WORK_BREAKS_MAX}
                          aria-label={t('settings.application.addBreakFor', { day })}
                          onClick={() => {
                            setRow(index, { ...row, breaks: [...row.breaks, newBreak(row)] });
                          }}
                        >
                          <Plus aria-hidden="true" />
                          {t('settings.application.addBreak')}
                        </Button>
                      </div>
                    </div>
                  </td>
                  <td className="py-2 pr-3 align-middle">
                    <DayStrip row={row} name={day} />
                  </td>
                  <td className="py-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={off}
                      aria-label={t('settings.application.applyAllFrom', { day })}
                      onClick={() => {
                        applyToAll(index);
                      }}
                    >
                      {t('settings.application.applyAll')}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-13 text-muted">
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-sm bg-primary" />
          {t('usage.hours.work')}
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-sm bg-warning" />
          {t('usage.hours.break')}
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-sm bg-subtle ring-1 ring-border" />
          {t('usage.hours.off')}
        </li>
      </ul>
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
  if (setting.key === WORK_HOURS_KEY && isWorkWeek(draft))
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
    editor = (
      <EgressEditor
        {...common}
        draft={draft as EgressRow[]}
        stored={setting.value}
        onChange={onChange}
      />
    );
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
          {REBUILD_HINT_KEYS.has(setting.key) ? ` ${t('network.settings.rebuildHint')}` : ''}
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
