import { CalendarClock, Eye, MoreHorizontal, Pencil, Play, Trash2 } from 'lucide-react';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Switch } from '@/components/ui/switch';
import { toast } from '@/components/ui/toast';
import { useUpdateSchedule } from '@/lib/api-hooks';
import type { ScheduleOut } from '@/lib/api-types';
import { formatDateTime } from '@/lib/format';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { reportProblem, shownStatus } from './report-errors';
import { DetailBadge, FormatBadges, RunStatusBadge } from './report-parts';
import { kindLabel, whenText } from './schedule-text';

export type ScheduleAction = 'edit' | 'run' | 'preview' | 'delete' | 'history';

const TH =
  'border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong';
const TD = 'border-b border-border px-3 py-2 align-top';
const COLUMNS = [
  'name',
  'when',
  'recipients',
  'formats',
  'detail',
  'enabled',
  'next',
  'last',
  'actions',
] as const;

function Actions({
  schedule,
  onAction,
}: {
  schedule: ScheduleOut;
  onAction: (action: ScheduleAction, schedule: ScheduleOut, trigger: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();
  const trigger = useRef<HTMLButtonElement>(null);
  const item = (action: ScheduleAction, icon: typeof Pencil, text: string) => {
    const Icon = icon;
    return (
      <DropdownMenuItem
        onSelect={() => {
          onAction(action, schedule, trigger.current);
        }}
      >
        <Icon aria-hidden="true" className="size-4" />
        {text}
      </DropdownMenuItem>
    );
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          ref={trigger}
          variant="ghost"
          size="icon-sm"
          aria-label={t('reports.schedules.actionsFor', { name: schedule.name })}
        >
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {item('edit', Pencil, t('reports.schedules.edit'))}
        {item('run', Play, t('reports.schedules.runNow'))}
        {item('preview', Eye, t('reports.schedules.preview'))}
        {item('history', CalendarClock, t('reports.schedules.history'))}
        <DropdownMenuSeparator />
        {item('delete', Trash2, t('reports.schedules.delete'))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** "Etkin": on/off from the list (a schedule without recipients cannot be switched on). */
function EnabledSwitch({ schedule }: { schedule: ScheduleOut }) {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const update = useUpdateSchedule();
  const blocked = !schedule.enabled && schedule.recipients.length === 0;
  return (
    <Switch
      checked={schedule.enabled}
      busy={update.isPending}
      disabled={blocked}
      title={blocked ? t('reports.schedules.needsRecipients') : undefined}
      aria-label={t('reports.schedules.enabledFor', { name: schedule.name })}
      onCheckedChange={(enabled) => {
        void withReauth(() => update.mutateAsync({ id: schedule.id, enabled }))
          .then(() => {
            toast({
              title: t(enabled ? 'reports.schedules.switchedOn' : 'reports.schedules.switchedOff', {
                name: schedule.name,
              }),
              variant: 'success',
            });
          })
          .catch((error: unknown) => {
            if (!isReauthCancelled(error))
              toast({ title: reportProblem(error, t).text, variant: 'danger' });
          });
      }}
    />
  );
}

/** Recipients: the first address and how many more (all of them in the title). */
function RecipientsCell({ recipients }: { recipients: readonly string[] }) {
  const { t } = useTranslation();
  if (recipients.length === 0)
    return <span className="text-muted-strong">{t('reports.schedules.noRecipients')}</span>;
  const [first = ''] = recipients;
  return (
    <span className="flex flex-col" title={recipients.join(', ')}>
      <span className="max-w-56 truncate">{first}</span>
      {recipients.length > 1 ? (
        <span className="text-xs text-muted-strong">
          {t('reports.schedules.moreRecipients', { count: recipients.length - 1 })}
        </span>
      ) : null}
    </span>
  );
}

/**
 * "Zamanlanmış raporlar": every schedule with its timing, recipients, formats, detail, on/off
 * switch, next run and last run; the row menu edits, runs, previews, shows the history or deletes.
 */
export function SchedulesTable({
  schedules,
  onAction,
}: {
  schedules: readonly ScheduleOut[];
  onAction: (action: ScheduleAction, schedule: ScheduleOut, trigger: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-separate border-spacing-0 text-13">
        <caption className="sr-only">{t('reports.schedules.caption')}</caption>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th key={column} scope="col" className={TH}>
                {column === 'actions' ? (
                  <span className="sr-only">{t('reports.schedules.columns.actions')}</span>
                ) : (
                  t(`reports.schedules.columns.${column}`)
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {schedules.map((schedule) => (
            <tr key={schedule.id} data-testid={`schedule-${schedule.id}`}>
              <th scope="row" className={`${TD} text-left font-medium text-foreground`}>
                {schedule.name}
              </th>
              <td className={TD}>
                <span className="flex flex-col">
                  <span>{kindLabel(schedule.kind, t)}</span>
                  <span className="text-xs text-muted-strong">{whenText(schedule, t)}</span>
                </span>
              </td>
              <td className={TD}>
                <RecipientsCell recipients={schedule.recipients} />
              </td>
              <td className={TD}>
                <FormatBadges formats={schedule.formats} />
              </td>
              <td className={TD}>
                <DetailBadge
                  personLevel={schedule.person_level}
                  pseudonymize={schedule.pseudonymize}
                />
              </td>
              <td className={TD}>
                <EnabledSwitch schedule={schedule} />
              </td>
              <td className={`${TD} tabular`}>
                {schedule.enabled && schedule.next_run_at
                  ? formatDateTime(schedule.next_run_at)
                  : '—'}
              </td>
              <td className={TD}>
                {schedule.last_run ? (
                  <span className="flex flex-col items-start gap-1">
                    <RunStatusBadge status={shownStatus(schedule.last_run)} />
                    <span className="text-xs text-muted-strong tabular">
                      {formatDateTime(
                        schedule.last_run.finished_at ?? schedule.last_run.started_at,
                      )}
                    </span>
                  </span>
                ) : (
                  <span className="text-muted-strong">{t('reports.schedules.neverRun')}</span>
                )}
              </td>
              <td className={`${TD} text-right`}>
                <Actions schedule={schedule} onAction={onAction} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
