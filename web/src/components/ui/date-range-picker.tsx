import { endOfDay, startOfDay, startOfMonth, subDays } from 'date-fns';
import { enUS, tr } from 'date-fns/locale';
import { CalendarDays } from 'lucide-react';
import { useState } from 'react';
import { DayPicker, type DateRange } from 'react-day-picker';
import 'react-day-picker/style.css';
import { useTranslation } from 'react-i18next';

import { formatDate } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { cn } from '@/lib/utils';

import { Button } from './button';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

export interface Range {
  from: Date;
  to: Date;
}

type PresetKey = 'today' | 'yesterday' | 'last7' | 'last30' | 'thisMonth';

/** Presets resolve against "now" so they stay correct when the page stays open overnight. */
export function presetRange(key: PresetKey, now: Date = new Date()): Range {
  const today = startOfDay(now);
  switch (key) {
    case 'today':
      return { from: today, to: endOfDay(now) };
    case 'yesterday':
      return { from: subDays(today, 1), to: endOfDay(subDays(today, 1)) };
    case 'last7':
      return { from: subDays(today, 6), to: endOfDay(now) };
    case 'last30':
      return { from: subDays(today, 29), to: endOfDay(now) };
    case 'thisMonth':
      return { from: startOfMonth(now), to: endOfDay(now) };
  }
}

const PRESETS: PresetKey[] = ['today', 'yesterday', 'last7', 'last30', 'thisMonth'];

interface DateRangePickerProps {
  value: Range | null;
  onChange: (range: Range) => void;
  id?: string;
  className?: string;
}

/** Turkish date range picker: Monday first, presets, no future days. */
export function DateRangePicker({ value, onChange, id, className }: DateRangePickerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRange | undefined>(value ?? undefined);
  const locale = currentLang() === 'en' ? enUS : tr;
  const from = value ? formatDate(value.from) : null;
  const to = value ? formatDate(value.to) : null;
  const text = from === null ? t('dateRange.placeholder') : from === to ? from : `${from} – ${to}`;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setDraft(value ?? undefined);
      }}
    >
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          aria-label={`${t('dateRange.label')}: ${text}`}
          className={cn(
            'inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3 text-sm whitespace-nowrap tabular',
            !value && 'text-muted',
            className,
          )}
        >
          <CalendarDays aria-hidden="true" className="size-4 text-muted" />
          {text}
        </button>
      </PopoverTrigger>
      <PopoverContent className="flex w-auto flex-col gap-3 p-3 sm:flex-row">
        <ul className="flex flex-row flex-wrap gap-1 sm:w-36 sm:flex-col">
          {PRESETS.map((key) => (
            <li key={key}>
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                onClick={() => {
                  onChange(presetRange(key));
                  setOpen(false);
                }}
              >
                {t(`dateRange.${key}`)}
              </Button>
            </li>
          ))}
        </ul>
        <div className="flex flex-col gap-3 border-border sm:border-l sm:pl-3">
          <DayPicker
            className="arci-day-picker"
            mode="range"
            locale={locale}
            weekStartsOn={1}
            numberOfMonths={2}
            defaultMonth={value?.from ?? subDays(new Date(), 31)}
            selected={draft}
            onSelect={setDraft}
            disabled={{ after: new Date() }}
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
                onChange({ from: startOfDay(draft.from), to: endOfDay(draft.to ?? draft.from) });
                setOpen(false);
              }}
            >
              {t('dateRange.apply')}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
