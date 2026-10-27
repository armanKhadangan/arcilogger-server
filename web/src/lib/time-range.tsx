/**
 * The global time range of spec 12.2 ("[Son 24 saat ▾]" in the top bar), on the routes whose
 * handle asks for it (`handle: {timeRange: true}`, today only Arama).
 *
 * The range lives in the address (`?range=24h`, or `?from=…&to=…` for "Özel"): a time span is no
 * personal data, and a reload or a shared link keeps it. It is a preset until the page runs a
 * search; then it is resolved against the clock to an absolute half-open `[from, to)` on device
 * time (`resolveRange`), like the API resolves a saved search's preset (`search/saved.py`).
 * Every bound is a site time with its offset (`2026-10-09T14:30:00+03:00`). No range is longer
 * than the site's `search.max_days` (1–92 days): the picker offers only the presets that fit, and
 * a preset in the address that does not fit reads as the default.
 */

import { CalendarClock, Check } from 'lucide-react';
import { useCallback, useId, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

import { parseDay, parseTime } from './dates';
import { siteDateTime, siteToday } from './display';
import { formatDateTime } from './format';
import type { RangePreset } from './search-types';
import { SITE_UTC_OFFSET } from './site-day';
import { cn } from './utils';

/** The presets in the picker's order; "Özel" follows them. */
export const RANGE_PRESETS: readonly RangePreset[] = [
  '15m',
  '1h',
  '24h',
  'today',
  'yesterday',
  '7d',
  '30d',
];

export const DEFAULT_PRESET: RangePreset = '24h';

/** The default of `search.max_days`, until the field catalogue says the site's value. */
export const DEFAULT_MAX_DAYS = 31;

/** A preset, or a fixed span chosen as "Özel" (a histogram zoom is one too). */
export type TimeRange = { preset: RangePreset } | { from: string; to: string };

/** Presets that end now: how far back they reach (ms). */
const SPANS: Partial<Record<RangePreset, number>> = {
  '15m': 15 * 60_000,
  '1h': 3_600_000,
  '24h': 86_400_000,
  '7d': 7 * 86_400_000,
  '30d': 30 * 86_400_000,
};

/** Whether a preset fits in `maxDays` ("Bugün" and "Dün" are one site day at most). */
export function presetFits(preset: RangePreset, maxDays: number): boolean {
  return (SPANS[preset] ?? 86_400_000) <= maxDays * 86_400_000;
}

/** `range`, or the default when it is a preset longer than `maxDays`. */
export function fitRange(range: TimeRange, maxDays: number): TimeRange {
  return 'preset' in range && !presetFits(range.preset, maxDays)
    ? { preset: DEFAULT_PRESET }
    : range;
}

/** `2026-10-09T14:30:00+03:00`: the site time of a moment (to the second, below it dropped). */
export function siteIso(moment: Date | number): string {
  const { date, time } = siteDateTime(new Date(moment));
  return `${date}T${time}${SITE_UTC_OFFSET}`;
}

/** The site midnight that starts `day` (`YYYY-MM-DD`). */
function midnight(day: string): string {
  return `${day}T00:00:00${SITE_UTC_OFFSET}`;
}

/** The day before `day` (both `YYYY-MM-DD`; calendar arithmetic, no time zone involved). */
function dayBefore(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

/**
 * `[from, to)` of a preset at `now`: `today` from site midnight (and at least one second, so a
 * search just after midnight still has a range), `yesterday` the whole previous site day, the
 * others ending now — rounded up to the next whole second, so nothing logged in this second is
 * left out.
 */
export function resolvePreset(
  preset: RangePreset,
  now: Date = new Date(),
): {
  from: string;
  to: string;
} {
  const end = Math.ceil(now.getTime() / 1000) * 1000;
  const span = SPANS[preset];
  if (span !== undefined) return { from: siteIso(end - span), to: siteIso(end) };
  const today = siteToday(now);
  if (preset === 'yesterday') return { from: midnight(dayBefore(today)), to: midnight(today) };
  const start = Date.parse(midnight(today));
  return { from: midnight(today), to: siteIso(Math.max(end, start + 1000)) };
}

/** The absolute range a search runs on. */
export function resolveRange(
  range: TimeRange,
  now: Date = new Date(),
): { from: string; to: string } {
  return 'preset' in range ? resolvePreset(range.preset, now) : { from: range.from, to: range.to };
}

function isPreset(value: string | null): value is RangePreset {
  return RANGE_PRESETS.includes(value as RangePreset);
}

/** A bound from the address: an ISO time with an offset, else `null`. */
function bound(value: string | null): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.test(value))
    return null;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

/**
 * The range in the address; the default (`24h`) when it is missing, cannot be read or is a preset
 * longer than `maxDays` (a fixed span too long is left to the server's refusal, which says so).
 */
export function rangeFromParams(
  params: URLSearchParams,
  maxDays: number = DEFAULT_MAX_DAYS,
): TimeRange {
  const from = bound(params.get('from'));
  const to = bound(params.get('to'));
  if (from && to && Date.parse(from) < Date.parse(to)) return { from, to };
  const preset = params.get('range');
  return fitRange({ preset: isPreset(preset) ? preset : DEFAULT_PRESET }, maxDays);
}

/** `params` with `range` written in (the other parameters, e.g. `saved`, are kept). */
export function rangeToParams(range: TimeRange, params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete('range');
  next.delete('from');
  next.delete('to');
  if ('preset' in range) next.set('range', range.preset);
  else {
    next.set('from', range.from);
    next.set('to', range.to);
  }
  return next;
}

/** A stable text of a range (to tell whether it changed). */
export function rangeKey(range: TimeRange): string {
  return 'preset' in range ? range.preset : `${range.from}/${range.to}`;
}

/** "Son 24 saat", or "09.10.2026 00:00:00 – 10.10.2026 00:00:00". */
export function rangeLabel(range: TimeRange, t: TFunction): string {
  if ('preset' in range) return t(`timeRange.presets.${range.preset}`);
  return t('timeRange.span', { from: formatDateTime(range.from), to: formatDateTime(range.to) });
}

/** `09.10.2026 14:30` of a site time (the form "Özel" is typed in). */
export function rangeText(iso: string): string {
  const text = formatDateTime(iso);
  return text.endsWith(':00') ? text.slice(0, -3) : text;
}

/** `09.10.2026 14:30` (or a day alone: its midnight) → a site time with its offset; else `null`. */
export function parseRangeText(text: string): string | null {
  const [dayPart, timePart, ...rest] = text.trim().split(/\s+/);
  if (!dayPart || rest.length > 0) return null;
  const day = parseDay(dayPart);
  const time = timePart === undefined ? '00:00:00' : parseTime(timePart);
  if (!day || !time) return null;
  return `${day}T${time}${SITE_UTC_OFFSET}`;
}

export type RangeErrors = Partial<Record<'from' | 'to', string>>;

/** A custom range typed in the picker, or the i18n keys of its problems. */
export function customRange(
  fromText: string,
  toText: string,
  maxDays: number,
): { range: { from: string; to: string } } | { errors: RangeErrors } {
  const errors: RangeErrors = {};
  const from = parseRangeText(fromText);
  const to = parseRangeText(toText);
  if (!fromText.trim()) errors.from = 'validation.required';
  else if (!from) errors.from = 'timeRange.errors.format';
  if (!toText.trim()) errors.to = 'validation.required';
  else if (!to) errors.to = 'timeRange.errors.format';
  if (from && to && !errors.from && !errors.to) {
    const span = Date.parse(to) - Date.parse(from);
    if (span <= 0) errors.to = 'timeRange.errors.order';
    else if (span > maxDays * 86_400_000) errors.to = 'timeRange.errors.tooLong';
  }
  if (errors.from || errors.to || !from || !to) return { errors };
  return { range: { from, to } };
}

/**
 * The range of the current address (at most `maxDays` long: the site's `search.max_days`, its
 * default until the field catalogue is read), and a setter that writes it there (replacing the
 * entry).
 */
export function useTimeRange(maxDays: number = DEFAULT_MAX_DAYS): {
  range: TimeRange;
  setRange: (range: TimeRange) => void;
} {
  const [params, setParams] = useSearchParams();
  const range = useMemo(() => rangeFromParams(params, maxDays), [params, maxDays]);
  const setRange = useCallback(
    (next: TimeRange) => {
      setParams((current) => rangeToParams(next, current), { replace: true });
    },
    [setParams],
  );
  return { range, setRange };
}

/**
 * The top bar's picker: the presets that fit in `maxDays`, and "Özel" with a start and an end
 * typed in the site's format (24 hours; the browser's own date inputs follow its UI language,
 * `lib/dates.ts`), at most `maxDays` apart.
 */
export function TimeRangePicker({ maxDays = DEFAULT_MAX_DAYS }: { maxDays?: number }) {
  const { t } = useTranslation();
  const id = useId();
  const { range, setRange } = useTimeRange(maxDays);
  const presets = RANGE_PRESETS.filter((preset) => presetFits(preset, maxDays));
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [fromText, setFromText] = useState('');
  const [toText, setToText] = useState('');
  const [errors, setErrors] = useState<RangeErrors>({});
  const label = rangeLabel(range, t);

  function start(next: boolean) {
    setOpen(next);
    if (!next) return;
    const shown = resolveRange(range);
    setCustom(!('preset' in range));
    setFromText(rangeText(shown.from));
    setToText(rangeText(shown.to));
    setErrors({});
  }

  function apply() {
    const result = customRange(fromText, toText, maxDays);
    if ('errors' in result) {
      setErrors(result.errors);
      document.getElementById(`${id}-${result.errors.from ? 'from' : 'to'}`)?.focus();
      return;
    }
    setRange(result.range);
    setOpen(false);
  }

  const message = (field: 'from' | 'to') => {
    const key = errors[field];
    return key ? t(key, { days: maxDays }) : undefined;
  };

  return (
    <Popover open={open} onOpenChange={start}>
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          size="sm"
          aria-label={t('timeRange.buttonRange', { range: label })}
          className="max-w-[22rem] tabular"
        >
          <CalendarClock aria-hidden="true" />
          <span className="truncate">{label}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <div className="flex flex-col gap-3">
          <div role="group" aria-label={t('timeRange.labelRange')} className="flex flex-col gap-0.5">
            {presets.map((preset) => {
              const chosen = !custom && 'preset' in range && range.preset === preset;
              return (
                <button
                  key={preset}
                  type="button"
                  aria-pressed={chosen}
                  className={cn(
                    'flex h-8 items-center justify-between rounded-md px-2 text-left text-13 text-foreground hover:bg-subtle',
                    chosen && 'bg-subtle font-medium',
                  )}
                  onClick={() => {
                    setRange({ preset });
                    setOpen(false);
                  }}
                >
                  {t(`timeRange.presets.${preset}`)}
                  {chosen ? <Check aria-hidden="true" className="size-4 text-primary" /> : null}
                </button>
              );
            })}
            <button
              type="button"
              aria-pressed={custom}
              aria-expanded={custom}
              className={cn(
                'flex h-8 items-center justify-between rounded-md px-2 text-left text-13 text-foreground hover:bg-subtle',
                custom && 'bg-subtle font-medium',
              )}
              onClick={() => {
                setCustom(true);
              }}
            >
              {t('timeRange.presets.custom')}
              {custom ? <Check aria-hidden="true" className="size-4 text-primary" /> : null}
            </button>
          </div>
          {custom ? (
            <form
              className="flex flex-col gap-3 border-t border-border pt-3"
              noValidate
              onSubmit={(event) => {
                event.preventDefault();
                apply();
              }}
            >
              <Field
                id={`${id}-from`}
                label={t('timeRange.from')}
                hint={t('timeRange.hint')}
                error={message('from')}
                required
              >
                <Input
                  id={`${id}-from`}
                  value={fromText}
                  mono
                  autoComplete="off"
                  placeholder={t('timeRange.placeholder')}
                  aria-invalid={errors.from ? true : undefined}
                  aria-describedby={errors.from ? `${id}-from-error` : `${id}-from-hint`}
                  onChange={(event) => {
                    setFromText(event.target.value);
                  }}
                />
              </Field>
              <Field id={`${id}-to`} label={t('timeRange.to')} error={message('to')} required>
                <Input
                  id={`${id}-to`}
                  value={toText}
                  mono
                  autoComplete="off"
                  placeholder={t('timeRange.placeholder')}
                  aria-invalid={errors.to ? true : undefined}
                  aria-describedby={errors.to ? `${id}-to-error` : undefined}
                  onChange={(event) => {
                    setToText(event.target.value);
                  }}
                />
              </Field>
              <p className="text-xs text-muted">{t('timeRange.note', { days: maxDays })}</p>
              <Button type="submit" size="sm" className="self-end">
                {t('timeRange.apply')}
              </Button>
            </form>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
