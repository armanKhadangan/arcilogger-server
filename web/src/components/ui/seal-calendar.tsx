import { enUS, tr } from 'date-fns/locale';
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  format,
  getISODay,
  isAfter,
  startOfMonth,
  startOfWeek,
  addDays,
} from 'date-fns';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { STATUS_STYLE } from './badge';
import { Button } from './button';

/**
 * Day states of the sealing calendar (spec 12.3 page 9): green sealed and verified, amber
 * sealed-but-not-verified-recently / late / pending, red failed, grey no data — always with an
 * icon and a text label too.
 */
export type SealDayStatus =
  'sealed' | 'unverified' | 'late' | 'pending' | 'failed' | 'open' | 'deleted' | 'noData';

/** Colours of a day cell (also the dashboard's 30-day strip). */
export const DAY_CLASS: Record<SealDayStatus, string> = {
  sealed: 'bg-success-soft text-success-ink',
  unverified: 'bg-warning-soft text-warning-ink',
  pending: 'bg-warning-soft text-warning-ink',
  late: 'bg-warning-soft text-warning-ink',
  failed: 'bg-danger-soft text-danger-ink',
  open: 'bg-subtle text-muted-strong',
  deleted: 'bg-subtle text-muted-strong',
  noData: 'bg-subtle text-muted-strong',
};

/** Legend order; `legend` limits it (e.g. to the states the page can show). */
export const SEAL_STATUSES: SealDayStatus[] = [
  'sealed',
  'unverified',
  'late',
  'pending',
  'failed',
  'open',
  'deleted',
  'noData',
];

interface SealCalendarProps {
  /** Any date inside the month to show. */
  month: Date;
  /** Status per day, keyed "yyyy-MM-dd". Days without an entry are "noData". */
  days: Record<string, SealDayStatus>;
  /** Extra text per day for its accessible name and tooltip (e.g. the server's message). */
  notes?: Record<string, string>;
  selected?: string | null;
  onSelect?: (day: string) => void;
  onMonthChange?: (month: Date) => void;
  /** Days after this are in the future and cannot be selected. */
  today?: Date;
  /** Statuses listed in the legend (default: the five basic ones). */
  legend?: SealDayStatus[];
  className?: string;
}

const BASIC_LEGEND: SealDayStatus[] = ['sealed', 'late', 'pending', 'failed', 'noData'];

export function dayKey(date: Date): string {
  return format(date, 'yyyy-MM-dd');
}

/** Month view of the sealing status: one cell per day, colour + icon + accessible label. */
export function SealCalendar({
  month,
  days,
  notes,
  selected,
  onSelect,
  onMonthChange,
  today = new Date(),
  legend = BASIC_LEGEND,
  className,
}: SealCalendarProps) {
  const { t } = useTranslation();
  const locale = currentLang() === 'en' ? enUS : tr;
  const first = startOfMonth(month);
  const dates = eachDayOfInterval({ start: first, end: endOfMonth(month) });
  const leading = getISODay(first) - 1; // Monday first
  const weekStart = startOfWeek(first, { weekStartsOn: 1 });
  const weekdays = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const cells: (Date | null)[] = [...Array<null>(leading).fill(null), ...dates];
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks = Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));

  return (
    <div className={cn('flex w-full max-w-sm flex-col gap-3', className)}>
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('common.previousMonth')}
          onClick={() => onMonthChange?.(addMonths(first, -1))}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <h4 className="text-sm font-semibold capitalize" aria-live="polite">
          {format(first, 'LLLL yyyy', { locale })}
        </h4>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('common.nextMonth')}
          onClick={() => onMonthChange?.(addMonths(first, 1))}
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
      <table className="w-full table-fixed border-separate border-spacing-1">
        <caption className="sr-only">{t('sealCalendar.label')}</caption>
        <thead>
          <tr>
            {weekdays.map((weekday) => (
              <th
                key={weekday.toISOString()}
                scope="col"
                className="py-1 text-xs font-medium text-muted"
              >
                {format(weekday, 'EEEEEE', { locale })}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {weeks.map((week, weekIndex) => (
            <tr key={weekIndex}>
              {week.map((date, dayIndex) => {
                if (!date) return <td key={`blank-${dayIndex}`} />;
                const key = dayKey(date);
                const future = isAfter(date, today);
                const status: SealDayStatus = future ? 'noData' : (days[key] ?? 'noData');
                const Icon = STATUS_STYLE[status].icon;
                const dateText = format(date, 'd MMMM yyyy', { locale });
                const statusText = t(`status.${status}`);
                const base = t('sealCalendar.dayLabel', { date: dateText, status: statusText });
                const note = future ? undefined : notes?.[key];
                // A note that already starts with the state replaces it (no "X — X: …").
                const label = !note
                  ? base
                  : note.toLocaleLowerCase().startsWith(statusText.toLocaleLowerCase())
                    ? t('sealCalendar.dayLabel', { date: dateText, status: note })
                    : `${base} — ${note}`;
                return (
                  <td key={key} className="p-0">
                    <button
                      type="button"
                      disabled={future}
                      aria-label={label}
                      aria-pressed={selected === key}
                      title={label}
                      onClick={() => onSelect?.(key)}
                      className={cn(
                        'flex h-11 w-full flex-col items-center justify-center gap-0.5 rounded-md text-xs font-medium tabular disabled:opacity-40',
                        DAY_CLASS[status],
                        selected === key && 'ring-2 ring-accent ring-offset-1 ring-offset-surface',
                      )}
                    >
                      {format(date, 'd')}
                      {future ? null : (
                        <Icon aria-hidden="true" className="size-3" strokeWidth={2} />
                      )}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <ul
        className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted"
        aria-label={t('sealCalendar.legend')}
      >
        {legend.map((status) => {
          const Icon = STATUS_STYLE[status].icon;
          return (
            <li key={status} className="flex items-center gap-1">
              <span
                className={cn(
                  'flex size-4 items-center justify-center rounded-sm',
                  DAY_CLASS[status],
                )}
              >
                <Icon aria-hidden="true" className="size-3" />
              </span>
              {t(`status.${status}`)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
