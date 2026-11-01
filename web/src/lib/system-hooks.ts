/**
 * TanStack Query hooks of the Sistem page (`/system`, `system-types.ts`,
 * `system-health-types.ts`; Wave 4 contract 2.5, 2.6, 2.8.2) and the Panel's disk card (2.8.3).
 *
 * Every block refreshes itself every 30 s in the background (`usePollingQuery`:
 * `X-Arci-Background`, so an unattended page does not keep the session alive); "Yenile" is
 * `refresh()`, the user's doing. All three need `system.read` (administrators) and are not
 * audited; the server caches only the database sizes (5 minutes).
 */

import { usePollingQuery } from './api-hooks';
import type { SystemHealth } from './system-health-types';
import type { SystemDisk, SystemSummary } from './system-types';

export const SYSTEM_POLL_MS = 30_000;

export const systemKeys = {
  summary: ['system', 'summary'] as const,
  health: ['system', 'health'] as const,
  disk: ['system', 'disk'] as const,
};

/** `GET /system`: host, drives with their forecast, database, collector, ingest and versions. */
export function useSystemSummary(enabled = true) {
  return usePollingQuery<SystemSummary>({
    queryKey: systemKeys.summary,
    path: '/system',
    intervalMs: SYSTEM_POLL_MS,
    enabled,
  });
}

/** `GET /system/health`: services, scheduled tasks, worker jobs and the clock. */
export function useSystemHealth(enabled = true) {
  return usePollingQuery<SystemHealth>({
    queryKey: systemKeys.health,
    path: '/system/health',
    intervalMs: SYSTEM_POLL_MS,
    enabled,
  });
}

/** `GET /system/disk`: the drives alone, for the Panel (asked only with `system.read`). */
export function useSystemDisk(enabled: boolean) {
  return usePollingQuery<SystemDisk>({
    queryKey: systemKeys.disk,
    path: '/system/disk',
    intervalMs: SYSTEM_POLL_MS,
    enabled,
  });
}
