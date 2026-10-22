/**
 * The 30-day hourly heat map of a person or device (spec 10.3 "Saatlik ısı haritası": 24 columns
 * × days, colour = active minutes per hour), unit-tested in `heatmap-model.test.ts`.
 *
 * The scale is fixed: an hour has at most 60 active minutes, so a colour step means the same for
 * every person and every period (steps of 12 minutes) instead of following the busiest hour.
 */

import { heatStep, type HeatmapRow } from '@/components/ui/heatmap';
import type { HeatCell } from '@/lib/api-types';

import { addDays } from './usage-model';

export const HEATMAP_DAYS = 30;
/** Active minutes that fill an hour (the darkest step). */
export const HEAT_MAX_MINUTES = 60;

/** Colour step 0…5 of an hour's active minutes on the fixed scale (more than 60 count as 60). */
export function heatLevel(minutes: number): number {
  return heatStep(Math.min(Math.max(minutes, 0), HEAT_MAX_MINUTES), HEAT_MAX_MINUTES);
}

/**
 * One row per day of the `days` days ending `endDay` (oldest first), 24 values each; hours the
 * API did not send are 0, values are clamped to 0…60.
 */
export function heatmapRows(
  cells: readonly HeatCell[],
  endDay: string,
  label: (day: string) => string,
  days: number = HEATMAP_DAYS,
): HeatmapRow[] {
  const byDay = new Map<string, number[]>();
  const first = addDays(endDay, 1 - days);
  for (const cell of cells) {
    if (cell.day < first || cell.day > endDay) continue;
    if (!Number.isInteger(cell.hour) || cell.hour < 0 || cell.hour > 23) continue;
    let values = byDay.get(cell.day);
    if (!values) {
      values = Array.from({ length: 24 }, () => 0);
      byDay.set(cell.day, values);
    }
    values[cell.hour] = Math.min(Math.max(cell.active_minutes, 0), HEAT_MAX_MINUTES);
  }
  return Array.from({ length: days }, (_, index) => {
    const day = addDays(first, index);
    return {
      key: day,
      label: label(day),
      values: byDay.get(day) ?? Array.from({ length: 24 }, () => 0),
    };
  });
}

/** Active minutes of all cells of the rows (the heat map's total). */
export function heatTotal(rows: readonly HeatmapRow[]): number {
  return rows.reduce((sum, row) => sum + row.values.reduce((a, b) => a + b, 0), 0);
}
