import type { LucideIcon } from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';

import { seriesColor } from '@/components/charts/palette';
import { formatDuration, type Lang } from '@/lib/format';
import { cn } from '@/lib/utils';

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info';

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'bg-subtle text-muted',
  primary: 'bg-primary-soft text-primary-ink',
  success: 'bg-success-soft text-success-ink',
  warning: 'bg-warning-soft text-warning-ink',
  danger: 'bg-danger-soft text-danger-ink',
  info: 'bg-info-soft text-info-ink',
};

export interface TimelineItem {
  id: string;
  /** Pre-formatted time, e.g. "09:44:07". */
  time: string;
  title: ReactNode;
  description?: ReactNode;
  icon: LucideIcon;
  tone?: Tone;
}

/** Vertical list of events (seal steps, evidence chain, alert history). */
export function Timeline({ items, className }: { items: TimelineItem[]; className?: string }) {
  return (
    <ol className={cn('flex flex-col', className)}>
      {items.map((item, index) => {
        const Icon = item.icon;
        return (
          <li key={item.id} className="relative flex gap-3 pb-5 last:pb-0">
            {index < items.length - 1 ? (
              <span
                aria-hidden="true"
                className="absolute top-8 bottom-0 left-[15px] w-px bg-border"
              />
            ) : null}
            <span
              className={cn(
                'relative flex size-8 shrink-0 items-center justify-center rounded-full',
                TONE_CLASS[item.tone ?? 'neutral'],
              )}
            >
              <Icon aria-hidden="true" className="size-4" strokeWidth={1.75} />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5 pt-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-medium text-foreground">{item.title}</span>
                <time className="font-mono text-xs text-muted tabular">{item.time}</time>
              </div>
              {item.description ? (
                <div className="text-13 text-muted">{item.description}</div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export interface DaySegment {
  /** Minutes since 00:00 (site time). */
  start: number;
  end: number;
  /** Category label, e.g. "Video". */
  category: string;
  /** Extra text for the hover line and the screen reader, e.g. the top domain. */
  detail?: string;
}

interface DayActivityBarProps {
  segments: DaySegment[];
  /** Category order fixes the colours (never re-coloured by rank). */
  categories: string[];
  lang?: Lang;
  label: string;
  className?: string;
}

function clock(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function describe(segment: DaySegment, lang: Lang): string {
  const text = `${clock(segment.start)}–${clock(segment.end)} · ${segment.category} · ${formatDuration((segment.end - segment.start) * 60, lang)}`;
  return segment.detail ? `${text} · ${segment.detail}` : text;
}

/** Horizontal 24-hour bar of active minutes, coloured by category (person day view). */
export function DayActivityBar({
  segments,
  categories,
  lang = 'tr',
  label,
  className,
}: DayActivityBarProps) {
  const [hover, setHover] = useState<DaySegment | null>(null);
  return (
    <figure className={cn('flex flex-col gap-2', className)}>
      <figcaption className="sr-only">{label}</figcaption>
      <div className="relative h-6 w-full rounded-md bg-subtle" role="list" aria-label={label}>
        {segments.map((segment) => {
          const colour = seriesColor(categories.indexOf(segment.category));
          const text = describe(segment, lang);
          return (
            <span
              key={`${segment.start}-${segment.category}`}
              role="listitem"
              aria-label={text}
              className="absolute inset-y-0 border-x border-surface first:rounded-l-md"
              style={{
                left: `${(segment.start / 1440) * 100}%`,
                width: `${Math.max(((segment.end - segment.start) / 1440) * 100, 0.3)}%`,
                background: colour,
              }}
              onMouseEnter={() => {
                setHover(segment);
              }}
              onMouseLeave={() => {
                setHover(null);
              }}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-muted tabular" aria-hidden="true">
        {['00', '06', '12', '18', '24'].map((hour) => (
          <span key={hour}>{hour}</span>
        ))}
      </div>
      <p className="min-h-5 text-13 text-muted" aria-live="polite">
        {hover ? describe(hover, lang) : null}
      </p>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-13">
        {categories.map((category, index) => (
          <li key={category} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="size-2.5 rounded-sm"
              style={{ background: seriesColor(index) }}
            />
            {category}
          </li>
        ))}
      </ul>
    </figure>
  );
}
