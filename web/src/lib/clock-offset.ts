import type { TFunction } from 'i18next';

/**
 * A device clock's typical offset from the receive time as the pages show it (#222): whole
 * minutes behind or ahead, "on time" under a minute (`source_clock_samples` keeps an offset under
 * a minute as 0).
 */
export function clockOffsetText(seconds: number, t: TFunction): string {
  if (seconds === 0) return t('sources.clock.onTime');
  const count = Math.max(1, Math.round(Math.abs(seconds) / 60));
  return t(seconds < 0 ? 'sources.clock.behind' : 'sources.clock.ahead', { count });
}
