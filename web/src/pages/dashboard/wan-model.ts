/**
 * The Panel card "İnternet hattı" (Wave 5 contract §6 N7, P4): which lines it shows and what
 * takes its place without them. The figures come from `GET /activity/wan?range=60m` (N6,
 * `dashboard.view` or `system.read`), polled as Canlı Durum polls it.
 */

import type { WanLine, WanRates } from '@/lib/activity-types';
import { hasAny } from '@/lib/permissions';

/** The rate texts are Canlı Durum's, so the card and the page say the same (W5-P-16). */
export { capacityShare, formatBps, shareText } from '@/pages/activity/activity-model';

/** Who may read the line speed (N6). */
export const WAN_PERMISSIONS: readonly string[] = ['dashboard.view', 'system.read'];
/** Who switches SNMP on for a source (Ayarlar → Kaynaklar). */
export const WAN_SETUP_PERMISSION = 'sources.manage';

/** The lines read over SNMP: a source whose SNMP is switched off reports `disabled`. */
export function polledLines(rates: WanRates | undefined): WanLine[] {
  return (rates?.lines ?? []).filter((line) => line.status !== 'disabled');
}

/**
 * What the Panel shows: the card with its lines; for an administrator while no source has SNMP
 * on, a link to Kaynaklar instead; else nothing (also while the answer is not there yet, or on
 * an error: the card is no health box, Canlı Durum and Sistem say more).
 */
export function wanCardState(
  rates: WanRates | undefined,
  granted: ReadonlySet<string>,
): 'lines' | 'setup' | 'hidden' {
  if (!rates) return 'hidden';
  if (polledLines(rates).length > 0) return 'lines';
  return granted.has(WAN_SETUP_PERMISSION) ? 'setup' : 'hidden';
}

/** The card's request is made only for those who may read the lines. */
export function wanRequested(granted: ReadonlySet<string>): boolean {
  return hasAny(granted, WAN_PERMISSIONS);
}

/** A line's 60 minutes of download (or upload) in Mbit/s, oldest first, for the sparkline. */
export function sparkValues(line: WanLine, direction: 'download' | 'upload'): number[] {
  return line.series.map(
    (point) => (direction === 'download' ? point.download_bps : point.upload_bps) / 1_000_000,
  );
}
