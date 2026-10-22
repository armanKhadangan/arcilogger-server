/**
 * Layout of a person's (or device's) day timeline: the API's merged active spans
 * ({@link TimelineSpan}, instants) become minutes 0…1440 of the site day, coloured by category
 * (unit-tested in `timeline-model.test.ts`).
 *
 * - Spans are clipped to the day (a span over midnight belongs partly to the next day).
 * - Touching or overlapping spans of the same category are merged.
 * - The categories get the chart colours in the order of their time on that day; beyond the
 *   palette's seven colours the rest fold into "Diğer" (never re-coloured by rank elsewhere).
 * - Each segment knows the top domains of the hours it covers (hover: time + top domain).
 */

import type { SubjectHour, TimelineSpan } from '@/lib/api-types';
import { siteDateTime } from '@/lib/display';

/** Colours of the chart palette before "Diğer". */
export const TIMELINE_COLOURS = 7;
const DAY_MINUTES = 24 * 60;

export interface TimelineSegment {
  /** Minutes since 00:00 site time (`end` exclusive). */
  start: number;
  end: number;
  category: string;
  /** Top domains of the hours the segment covers, in hour order, without repeats. */
  topDomains: string[];
}

export interface TimelineLayout {
  segments: TimelineSegment[];
  /** Legend order; the index is the colour (`seriesColor`). */
  categories: string[];
  /** Active minutes in the day (after merging). */
  activeMinutes: number;
}

/** Start of a site day as epoch milliseconds (UTC+03:00 all year, see `site-day.ts`). */
function dayStart(day: string): number {
  return Date.parse(`${day}T00:00:00+03:00`);
}

/** `top_domain` of each hour 0…23 of `day`. */
function topDomainsByHour(hourly: readonly SubjectHour[], day: string): (string | null)[] {
  const hours: (string | null)[] = Array.from({ length: 24 }, () => null);
  for (const item of hourly) {
    const { date, time } = siteDateTime(item.hour);
    if (date !== day) continue;
    const hour = Number(time.slice(0, 2));
    if (hour >= 0 && hour < 24) hours[hour] = item.top_domain;
  }
  return hours;
}

function merge(segments: TimelineSegment[]): TimelineSegment[] {
  const merged: TimelineSegment[] = [];
  for (const segment of [...segments].sort((a, b) => a.start - b.start || a.end - b.end)) {
    const last = merged.at(-1);
    if (last && last.category === segment.category && segment.start <= last.end) {
      last.end = Math.max(last.end, segment.end);
    } else {
      merged.push({ ...segment });
    }
  }
  return merged;
}

export function layoutTimeline(
  spans: readonly TimelineSpan[],
  day: string,
  hourly: readonly SubjectHour[],
  labels: { unknown: string; other: string },
): TimelineLayout {
  const origin = dayStart(day);
  const clipped: TimelineSegment[] = [];
  for (const span of spans) {
    const from = Math.max(0, (Date.parse(span.start) - origin) / 60_000);
    const to = Math.min(DAY_MINUTES, (Date.parse(span.end) - origin) / 60_000);
    if (!Number.isFinite(from) || !Number.isFinite(to)) continue;
    const start = Math.floor(from);
    const end = Math.ceil(to);
    if (end <= start || start >= DAY_MINUTES || end <= 0) continue;
    clipped.push({ start, end, category: span.category ?? labels.unknown, topDomains: [] });
  }
  let segments = merge(clipped);

  // Colour order: most minutes first; the eighth category and beyond become "Diğer".
  const minutes = new Map<string, number>();
  for (const segment of segments)
    minutes.set(
      segment.category,
      (minutes.get(segment.category) ?? 0) + segment.end - segment.start,
    );
  const ranked = [...minutes.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([category]) => category);
  let categories = ranked;
  if (ranked.length > TIMELINE_COLOURS) {
    const kept = new Set(ranked.slice(0, TIMELINE_COLOURS));
    segments = merge(
      segments.map((segment) =>
        kept.has(segment.category) ? segment : { ...segment, category: labels.other },
      ),
    );
    categories = [...ranked.slice(0, TIMELINE_COLOURS), labels.other];
  }

  const tops = topDomainsByHour(hourly, day);
  for (const segment of segments) {
    const first = Math.floor(segment.start / 60);
    const last = Math.floor((segment.end - 1) / 60);
    const domains: string[] = [];
    for (let hour = first; hour <= last; hour += 1) {
      const domain = tops[hour];
      if (domain && !domains.includes(domain)) domains.push(domain);
    }
    segment.topDomains = domains;
  }

  return {
    segments,
    categories,
    activeMinutes: segments.reduce((sum, segment) => sum + segment.end - segment.start, 0),
  };
}

/** `08:05` of a minute of the day. */
export function clockOfMinute(minute: number): string {
  const value = Math.max(0, Math.min(DAY_MINUTES, Math.round(minute)));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}
