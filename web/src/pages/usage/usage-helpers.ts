/** Non-component helpers of the usage views (the components are in `usage-parts.tsx`). */

import type { TFunction } from 'i18next';
import type { LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Metrics } from '@/lib/api-types';
import { formatBytes, formatDate, formatDuration, formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

/** Key of the method note in the locales (`notice_key` of every usage answer). */
export const METHOD_NOTE_KEY = 'usage.method_note';

/** The method note: the API names its locale key; an unknown key falls back to ours. */
export function useMethodNote(noticeKey?: string | null): string {
  const { t, i18n } = useTranslation();
  return noticeKey && i18n.exists(noticeKey) ? t(noticeKey) : t(METHOD_NOTE_KEY);
}

export interface Tile {
  key: string;
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  /** Shows the "tahmini" info icon. */
  estimate?: boolean;
}

/** `08:05 – 17:42` on one day, `12.10 08:05 – 13.10 17:42` across days. */
export function activitySpan(first: string | null, last: string | null): string | null {
  if (!first || !last) return null;
  if (formatDate(first) === formatDate(last)) return `${formatTime(first)} – ${formatTime(last)}`;
  const short = (value: string) => `${formatDate(value).slice(0, 5)} ${formatTime(value)}`;
  return `${short(first)} – ${short(last)}`;
}

/** The metric tiles of a subject, department, site or category. */
export function metricTiles(metrics: Metrics, t: TFunction): Tile[] {
  const lang = currentLang();
  const span = activitySpan(metrics.first_activity, metrics.last_activity);
  return [
    {
      key: 'online',
      label: t('usage.metrics.online'),
      value: formatDuration(metrics.online_seconds, lang),
      hint: t('usage.hints.online'),
      estimate: true,
    },
    {
      key: 'active',
      label: t('usage.metrics.active'),
      value: formatDuration(metrics.active_seconds, lang),
      hint: t('usage.hints.active'),
      estimate: true,
    },
    {
      key: 'download',
      label: t('usage.metrics.download'),
      value: formatBytes(metrics.download_bytes, lang),
    },
    {
      key: 'upload',
      label: t('usage.metrics.upload'),
      value: formatBytes(metrics.upload_bytes, lang),
    },
    {
      key: 'sessions',
      label: t('usage.metrics.sessions'),
      value: formatNumber(metrics.sessions, 0, lang),
    },
    {
      key: 'blocked',
      label: t('usage.metrics.blocked'),
      value: formatNumber(metrics.blocked, 0, lang),
      hint: t('usage.hints.blocked'),
    },
    {
      key: 'offHours',
      label: t('usage.metrics.offHours'),
      value: formatDuration(metrics.off_hours_active_seconds, lang),
      hint: t('usage.hints.offHours'),
      estimate: true,
    },
    {
      key: 'span',
      label: t('usage.metrics.firstLast'),
      value: span ?? '—',
      hint: span ? t('usage.hints.firstLast') : t('usage.hints.noActivity'),
    },
  ];
}

/** True when a metrics block shows no traffic at all. */
export function noTraffic(metrics: Metrics | undefined): boolean {
  return (
    !metrics ||
    (metrics.online_seconds === 0 &&
      metrics.active_seconds === 0 &&
      metrics.download_bytes === 0 &&
      metrics.upload_bytes === 0 &&
      metrics.sessions === 0 &&
      metrics.blocked === 0)
  );
}

/** Height of a table area for `rows` rows (the table is virtualised beyond it). */
export function tableHeight(rows: number, max = 520, rowHeight = 32): number {
  return Math.min(max, 44 + Math.max(rows, 3) * rowHeight);
}
