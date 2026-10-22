import { enUS, tr } from 'date-fns/locale';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { useId, useState } from 'react';
import { DayPicker, type DateRange } from 'react-day-picker';
import 'react-day-picker/style.css';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { UsageHours } from '@/lib/api-types';
import { formatDay } from '@/lib/display';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import {
  clampRange,
  dayCount,
  HOURS_FILTERS,
  presetOf,
  presetRange,
  RANGE_PRESETS,
  shiftRange,
  type DayRange,
} from './usage-model';

/** A site day `YYYY-MM-DD` as the browser-local Date of that calendar day (for the calendar). */
function toDate(day: string): Date {
  return new Date(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10)));
}

/** The calendar day of a browser-local Date as `YYYY-MM-DD` (no time-zone shift). */
function toDay(date: Date): string {
  return [
    String(date.getFullYear()).padStart(4, '0'),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

/**
 * The period of the usage page: the presets of spec 12.3 (day default) or any range of site
 * days in the calendar (Monday first, nothing after today), and the previous / next period.
 */
export function PeriodPicker({
  range,
  todayDay,
  onChange,
}: {
  range: DayRange;
  todayDay: string;
  onChange: (range: DayRange) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange | undefined>(undefined);
  const locale = currentLang() === 'en' ? enUS : tr;
  const preset = presetOf(range, todayDay);
  const label = preset ? t(`usage.range.${preset}`) : t('usage.range.custom');
  const previous = shiftRange(range, -1, todayDay);
  const next = shiftRange(range, 1, todayDay);
  const days = dayCount(range);
  const rangeText =
    range.from === range.to
      ? formatDay(range.from)
      : `${formatDay(range.from)} – ${formatDay(range.to)}`;

  return (
    <div className="flex items-center gap-1" role="group" aria-label={t('usage.range.label')}>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('usage.range.previous')}
        title={t('usage.range.previous')}
        disabled={previous === null}
        onClick={() => {
          if (previous) onChange(previous);
        }}
      >
        <ChevronLeft aria-hidden="true" />
      </Button>
      <Popover
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (value) setDraft({ from: toDate(range.from), to: toDate(range.to) });
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3 text-sm whitespace-nowrap"
            aria-label={`${t('usage.range.label')}: ${label}, ${rangeText}`}
            data-testid="period"
          >
            <CalendarDays aria-hidden="true" className="size-4 text-muted" />
            <span className="font-medium">{label}</span>
            <span className="text-muted-strong tabular">{rangeText}</span>
            {days > 1 ? (
              <span className="text-xs text-muted">{t('usage.range.days', { count: days })}</span>
            ) : null}
          </button>
        </PopoverTrigger>
        <PopoverContent className="flex w-auto flex-col gap-3 p-3 sm:flex-row">
          <ul className="flex flex-row flex-wrap gap-1 sm:w-36 sm:flex-col">
            {RANGE_PRESETS.map((key) => (
              <li key={key}>
                <Button
                  variant={preset === key ? 'secondary' : 'ghost'}
                  size="sm"
                  className="w-full justify-start"
                  aria-pressed={preset === key}
                  onClick={() => {
                    onChange(presetRange(key, todayDay));
                    setOpen(false);
                  }}
                >
                  {t(`usage.range.${key}`)}
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex flex-col gap-3 border-border sm:border-l sm:pl-3">
            <p className="text-13 text-muted">{t('usage.range.pick')}</p>
            <DayPicker
              className="arci-day-picker"
              mode="range"
              locale={locale}
              weekStartsOn={1}
              numberOfMonths={2}
              defaultMonth={toDate(range.from)}
              selected={draft}
              onSelect={setDraft}
              disabled={{ after: toDate(todayDay) }}
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  setOpen(false);
                }}
              >
                {t('common.cancel')}
              </Button>
              <Button
                size="sm"
                disabled={!draft?.from}
                onClick={() => {
                  if (!draft?.from) return;
                  onChange(
                    clampRange(
                      { from: toDay(draft.from), to: toDay(draft.to ?? draft.from) },
                      todayDay,
                    ),
                  );
                  setOpen(false);
                }}
              >
                {t('usage.range.apply')}
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('usage.range.next')}
        title={t('usage.range.next')}
        disabled={next === null}
        onClick={() => {
          if (next) onChange(next);
        }}
      >
        <ChevronRight aria-hidden="true" />
      </Button>
    </div>
  );
}

/** Tüm saatler / Mesai içi / Mesai dışı (the work-hours setting of the server). */
export function HoursFilter({
  value,
  onChange,
}: {
  value: UsageHours;
  onChange: (hours: UsageHours) => void;
}) {
  const { t } = useTranslation();
  const hintId = useId();
  return (
    <div
      role="group"
      aria-label={t('usage.hours.label')}
      aria-describedby={hintId}
      className="inline-flex items-center rounded-lg border border-border bg-surface p-0.5"
    >
      {HOURS_FILTERS.map((hours) => (
        <button
          key={hours}
          type="button"
          aria-pressed={value === hours}
          onClick={() => {
            onChange(hours);
          }}
          className={cn(
            'h-8 rounded-md px-3 text-13 font-medium whitespace-nowrap text-muted-strong hover:text-foreground',
            value === hours && 'bg-primary-soft text-primary-ink',
          )}
        >
          {t(`usage.hours.${hours}`)}
        </button>
      ))}
      <span id={hintId} className="sr-only">
        {t('usage.hours.hint')}
      </span>
    </div>
  );
}
