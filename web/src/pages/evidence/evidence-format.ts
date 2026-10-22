/** Display text of evidence packages (scope, size, counts). */

import type { TFunction } from 'i18next';

import type { EvidenceOut } from '@/lib/api-types';
import { formatDay } from '@/lib/display';
import { formatBytes, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { scopeDays } from './evidence-model';

/** "04.10.2026 – 05.10.2026 (2 gün) · tam günler · tüm kaynaklar · 5651 sorgusu #1" */
export function scopeText(item: EvidenceOut, t: TFunction): string {
  const days = scopeDays(item.scope);
  const range = days
    ? days.first === days.last
      ? formatDay(days.first)
      : `${formatDay(days.first)} – ${formatDay(days.last)}`
    : '—';
  const parts = [
    t('evidence.scope.days', { range, count: days?.count ?? 0 }),
    item.scope.content === 'extract'
      ? t('evidence.scope.extract', { count: item.scope.extract_lines })
      : t('evidence.scope.fullDays'),
    item.scope.sources ? item.scope.sources.join(', ') : t('evidence.scope.allSources'),
  ];
  if (item.lookup_id !== null) parts.push(t('evidence.scope.lookup', { id: item.lookup_id }));
  return parts.join(' · ');
}

/** Size of a ready package, else the estimate made with the request ("~4 MB (tahmini)"). */
export function packageSize(item: EvidenceOut, t: TFunction): string {
  const lang = currentLang();
  if (item.size !== null) return formatBytes(item.size, lang);
  if (item.scope.estimated_bytes)
    return t('evidence.estimated', { size: formatBytes(item.scope.estimated_bytes, lang) });
  return '—';
}

export function count(value: number): string {
  return formatNumber(value, 0, currentLang());
}
