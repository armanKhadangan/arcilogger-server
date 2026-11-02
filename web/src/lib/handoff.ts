/**
 * What one page hands to another: the top bar's term to Arama, a search's raw lines to the
 * evidence wizard, an unknown sender to Ayarlar → Kaynaklar, a Panel or Canlı Akış link to Arama.
 *
 * It is often personal data (an IP, a MAC or a name inside a query), so it is kept in memory only,
 * in this module's map: never in the address (the web server's access log, `?next=` after a
 * time-out), never in router state (`history.state`, which the browser may write to disk for
 * session restore) and never in browser storage. Each entry is taken once, by the page it was
 * left for; signing out (or a session the server ended) forgets every entry (`lib/auth.tsx`).
 *
 * Usage: `handoff.put('/search', { q })`, then navigate to `/search`; the page receives it with
 * `useHandoff('/search', receive)`.
 */

import { useEffect, useRef } from 'react';

import type { DateString, DateTimeString, RawLine } from './api-types';
import type { SourceType } from './settings-types';

/** The presets of a search's time range (`saved_searches.range_preset`). */
export type SearchRangePreset = '15m' | '1h' | '24h' | 'today' | 'yesterday' | '7d' | '30d';

/** A search's time range: a preset (resolved against the clock when it runs) or a fixed span. */
export type SearchRange =
  { preset: SearchRangePreset } | { from: DateTimeString; to: DateTimeString };

/** What each page can be handed. */
export interface HandoffPayloads {
  /** Arama: a query and, when the link means a particular time, its range. */
  '/search': { q: string; range?: SearchRange };
  /** Kanıt Paketleri: the raw lines of a search (`POST /search/refs`) for the wizard's extract. */
  '/evidence': {
    extract: RawLine[];
    from_day: DateString;
    to_day: DateString;
    truncated: boolean;
    complete: boolean;
    unsealed_days: DateString[];
    /** The search's normalised query, shown beside the lines. */
    query: string;
  };
  /** Ayarlar → Kaynaklar: an unknown sender, to open the add dialog filled in. */
  '/settings/sources': { prefill: { name: string; type: SourceType; address: string } };
}

export type HandoffTarget = keyof HandoffPayloads;

const pending = new Map<HandoffTarget, HandoffPayloads[HandoffTarget]>();
/** Open pages waiting for their target (`useHandoff`). */
const receivers = new Set<() => void>();

export const handoff = {
  /** Leave `payload` for the page at `target` (replacing what it has not taken yet). */
  put<T extends HandoffTarget>(target: T, payload: HandoffPayloads[T]): void {
    pending.set(target, payload);
    for (const receiver of [...receivers]) receiver();
  },

  /** What was left for `target`, once: the next call returns `null`. */
  take<T extends HandoffTarget>(target: T): HandoffPayloads[T] | null {
    const payload = pending.get(target) as HandoffPayloads[T] | undefined;
    pending.delete(target);
    return payload ?? null;
  },

  /** Forget everything: nothing left for a page reaches whoever signs in next. */
  clear(): void {
    pending.clear();
  },
};

/**
 * Receive what is handed to `target` while the page is open: what was left before it opened and
 * whatever arrives later (the top bar's search while Arama is already open). The entry is taken
 * in an effect, not while rendering: React's StrictMode renders twice in development, and the
 * second render would find a one-shot entry gone.
 */
export function useHandoff<T extends HandoffTarget>(
  target: T,
  receive: (payload: HandoffPayloads[T]) => void,
): void {
  const latest = useRef(receive);
  useEffect(() => {
    latest.current = receive;
  });
  useEffect(() => {
    const deliver = () => {
      const payload = handoff.take(target);
      if (payload !== null) latest.current(payload);
    };
    deliver();
    receivers.add(deliver);
    return () => {
      receivers.delete(deliver);
    };
  }, [target]);
}
