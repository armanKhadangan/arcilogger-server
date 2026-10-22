/** Text helpers of the devices and persons page (no components: `device-parts.tsx` renders). */

import type { TFunction } from 'i18next';
import { useEffect, useState } from 'react';

import type { AssignmentOut } from '@/lib/api-types';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';

import { pageRange } from './devices-model';

/** "PC", "Telefon", …; `null` = "Bilinmiyor". */
export function deviceTypeLabel(type: string | null, t: TFunction): string {
  return type ? t(`deviceTypes.${type}`, { defaultValue: type }) : t('devices.typeUnknown');
}

/** "12.03.2026 09:00:00 – 15.04.2026 17:30:00" or "12.03.2026 09:00:00 – hâlâ geçerli". */
export function assignmentSpan(
  item: Pick<AssignmentOut, 'valid_from' | 'valid_to'>,
  t: TFunction,
): string {
  return item.valid_to
    ? t('devices.history.span', {
        from: formatDateTime(item.valid_from),
        to: formatDateTime(item.valid_to),
      })
    : t('devices.history.spanOpen', { from: formatDateTime(item.valid_from) });
}

/** "1–50 / 312 cihaz" for a paged table's counter. */
export function rangeText(
  offset: number,
  shown: number,
  total: number,
  t: TFunction,
  unit: 'devices' | 'persons',
): string {
  const lang = currentLang();
  const range = pageRange(offset, shown, total);
  if (!range) return t(`devices.range.${unit}Empty`);
  return t(`devices.range.${unit}`, {
    count: range.total,
    first: formatNumber(range.first, 0, lang),
    last: formatNumber(range.last, 0, lang),
    formattedCount: formatNumber(range.total, 0, lang),
  });
}

/** `value`, but only after it stopped changing for `ms` (search boxes). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSettled(value);
    }, ms);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, ms]);
  return settled;
}
