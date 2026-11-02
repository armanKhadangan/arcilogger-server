/**
 * "24 saat gizle" of the legal-validity banner (Wave 5 contract 2.3.2): a warning can be hidden
 * for 24 hours in this browser; danger never. The browser keeps the end of the pause and the codes
 * the user saw (`arci.legalBanner.hidden`, JSON `{until, codes}`), so a new reason, or any danger,
 * shows the banner again at once. Storage can be unavailable or hold anything: every access is
 * guarded (`lib/storage.ts`) and an unreadable value counts as not hidden.
 */

import { readPreference, writePreference } from '@/lib/storage';

import type { LegalStatus } from './types';

export const LEGAL_BANNER_HIDE_KEY = 'arci.legalBanner.hidden';
/** How long "24 saat gizle" hides a warning. */
export const LEGAL_BANNER_HIDE_MS = 24 * 60 * 60_000;

export interface BannerHide {
  /** Epoch milliseconds the pause ends. */
  until: number;
  /** The reasons on screen when the user hid the banner. */
  codes: string[];
}

/** The pause of `status`'s warning from `now`. */
export function hideFor(status: LegalStatus, now: number): BannerHide {
  return { until: now + LEGAL_BANNER_HIDE_MS, codes: status.reasons.map((reason) => reason.code) };
}

/** The stored pause, or `null` when there is none or it cannot be read. */
export function readHide(): BannerHide | null {
  const stored = readPreference(LEGAL_BANNER_HIDE_KEY);
  if (stored === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(stored);
  } catch {
    return null;
  }
  if (typeof value !== 'object' || value === null) return null;
  const { until, codes } = value as Partial<Record<keyof BannerHide, unknown>>;
  if (typeof until !== 'number' || !Number.isFinite(until)) return null;
  if (!Array.isArray(codes) || !codes.every((code) => typeof code === 'string')) return null;
  return { until, codes };
}

/** Keep the pause in this browser (silently nothing when storage is unavailable). */
export function writeHide(hide: BannerHide): void {
  writePreference(LEGAL_BANNER_HIDE_KEY, JSON.stringify(hide));
}

/**
 * Whether `status` stays hidden at `now`: only a warning, only before the pause ends, and only
 * while every reason is one the user already saw.
 */
export function isHidden(status: LegalStatus, hide: BannerHide | null, now: number): boolean {
  if (status.level !== 'warning' || hide === null || now >= hide.until) return false;
  return status.reasons.every((reason) => hide.codes.includes(reason.code));
}
