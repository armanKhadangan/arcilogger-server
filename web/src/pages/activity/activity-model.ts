/**
 * Canlı Durum (Wave 5 contract §6, M1–M3, N6, P3): rates as text, a line's share of its
 * capacity and the chart rows of a line and of the company's last hour (unit-tested in
 * `activity-model.test.ts`).
 *
 * The API gives rates in bits per second and volumes in bytes; the page shows Mbit/s, the unit
 * a line's contract is sold in.
 */

import type { ActivityMinute, ActivityWindow, WanLine } from '@/lib/activity-types';
import { formatNumber, formatTime, type Lang } from '@/lib/format';

/** The windows of "Şu an", in minutes (M2); 5 is the API's default. */
export const ACTIVITY_WINDOWS: readonly ActivityWindow[] = [1, 5, 15];
export const DEFAULT_WINDOW: ActivityWindow = 5;

/** `?window=` of the address, or the default. */
export function windowFromSearch(value: string | null): ActivityWindow {
  const number = Number(value);
  return (ACTIVITY_WINDOWS as readonly number[]).includes(number)
    ? (number as ActivityWindow)
    : DEFAULT_WINDOW;
}

/** Bits per second → Mbit/s. */
export function toMbps(bps: number): number {
  return bps / 1_000_000;
}

/** "12,4 Mbit/s", "850 kbit/s", "1,2 Gbit/s"; "—" before the first sample. */
export function formatBps(bps: number | null, lang: Lang): string {
  if (bps === null || !Number.isFinite(bps)) return '—';
  if (bps >= 1_000_000_000) return `${formatNumber(bps / 1_000_000_000, 2, lang)} Gbit/s`;
  if (bps >= 1_000_000) return `${formatNumber(bps / 1_000_000, 1, lang)} Mbit/s`;
  return `${formatNumber(bps / 1_000, 0, lang)} kbit/s`;
}

/** A rate's share of the line's capacity (Mbit/s as configured), or `null` when unknown. */
export function capacityShare(bps: number | null, capacityMbps: number | undefined): number | null {
  if (bps === null || !capacityMbps || capacityMbps <= 0) return null;
  return bps / (capacityMbps * 1_000_000);
}

/** "%37" (Turkish) or "37%" (English); above 100 % stays as measured. */
export function shareText(share: number, lang: Lang): string {
  const value = formatNumber(Math.round(share * 100), 0, lang);
  return lang === 'en' ? `${value}%` : `%${value}`;
}

export interface RatePoint extends Record<string, string | number> {
  /** "14:05" (site time). */
  time: string;
  /** Mbit/s. */
  download: number;
  upload: number;
}

/** A line's samples (oldest first) as chart rows in Mbit/s. */
export function lineSeries(line: WanLine): RatePoint[] {
  return line.series.map((point) => ({
    time: formatTime(point.time),
    download: toMbps(point.download_bps),
    upload: toMbps(point.upload_bps),
  }));
}

/** The company's minutes (bytes per minute, oldest first) as average Mbit/s per minute. */
export function minuteSeries(minutes: readonly ActivityMinute[]): RatePoint[] {
  return minutes.map((minute) => ({
    time: formatTime(minute.minute),
    download: toMbps((minute.download_bytes * 8) / 60),
    upload: toMbps((minute.upload_bytes * 8) / 60),
  }));
}
