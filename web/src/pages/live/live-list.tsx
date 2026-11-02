import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { visibleText } from '@/lib/display';
import { formatNumber, formatTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { LiveLine } from '@/lib/live-types';
import { cn } from '@/lib/utils';

import {
  HIGHLIGHT_CLASSES,
  highlightText,
  lineColor,
  lineKey,
  type HighlightRule,
} from './live-model';

/** Height of one line (px); the list is virtual, only the lines in view are rendered. */
const ROW_HEIGHT = 28;
/** Pixels from the end that still count as "at the bottom" (rounding, a half line). */
const BOTTOM_SLACK = 8;

export interface LiveView {
  lines: LiveLine[];
  /** Lines of the previous view that the newest pushed out (the oldest first). */
  removed: number;
  /** Lines this view added at the end. */
  added: number;
}

function HighlightedText({ text, rules }: { text: string; rules: readonly HighlightRule[] }) {
  return (
    <>
      {highlightText(text, rules).map((part, index) =>
        part.color ? (
          <mark key={index} className={cn('rounded-sm px-px', HIGHLIGHT_CLASSES[part.color])}>
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

/**
 * The lines of Canlı Akış, the newest at the bottom (at most 2,000; `live-model.ts`). The list
 * follows new lines only while it is scrolled to the bottom; scrolled up, it keeps its place and a
 * "N yeni satır ↓" button goes back down. It is not a live region (`aria-live="off"`): a screen
 * reader hears the page's status line instead, at most every 10 s. Each line opens its drawer.
 */
export function LiveList({
  view,
  rules,
  kindLabel,
  onOpen,
  emptyText,
}: {
  view: LiveView;
  rules: readonly HighlightRule[];
  kindLabel: (line: LiveLine) => string;
  onOpen: (line: LiveLine) => void;
  emptyText: string;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [unseen, setUnseen] = useState(0);
  const { lines } = view;
  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 16,
    initialRect: { width: 1024, height: 480 },
    getItemKey: (index) => {
      const line = lines[index];
      return line ? lineKey(line) : index;
    },
  });

  // New lines: follow them at the bottom; scrolled up, keep the lines in view where they are.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (atBottom.current) {
      element.scrollTop = element.scrollHeight;
      return;
    }
    if (view.removed > 0)
      element.scrollTop = Math.max(0, element.scrollTop - view.removed * ROW_HEIGHT);
    if (view.added > 0) setUnseen((count) => count + view.added);
  }, [view]);

  useEffect(() => {
    if (lines.length === 0) setUnseen(0);
  }, [lines.length]);

  function toBottom() {
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
    atBottom.current = true;
    setUnseen(0);
  }

  const items = virtualizer.getVirtualItems();
  return (
    <div className="relative">
      <div
        ref={scrollRef}
        className="h-[32rem] overflow-auto rounded-lg border border-border bg-surface"
        tabIndex={-1}
        aria-live="off"
        data-testid="live-list"
        onScroll={(event) => {
          const element = event.currentTarget;
          const bottom =
            element.scrollHeight - element.scrollTop - element.clientHeight <= BOTTOM_SLACK;
          atBottom.current = bottom;
          if (bottom) setUnseen(0);
        }}
      >
        {lines.length === 0 ? (
          <p className="px-4 py-10 text-center text-13 text-muted">{emptyText}</p>
        ) : (
          <ul
            aria-label={t('live.list.label')}
            className="relative w-full"
            style={{ height: virtualizer.getTotalSize() }}
          >
            {items.map((item) => {
              const line = lines[item.index];
              if (!line) return null;
              const color = lineColor(line, rules);
              const invalid = line.parse_error === 'rawlog.invalid';
              return (
                <li
                  key={item.key}
                  className="absolute inset-x-0"
                  style={{ top: item.start, height: ROW_HEIGHT }}
                  data-line={lineKey(line)}
                >
                  <button
                    type="button"
                    onClick={() => {
                      onOpen(line);
                    }}
                    className={cn(
                      'flex h-full w-full items-center gap-3 px-3 text-left font-mono text-xs whitespace-nowrap hover:bg-subtle focus-visible:bg-subtle',
                      color ? HIGHLIGHT_CLASSES[color] : 'text-foreground',
                    )}
                    data-highlight={color ?? undefined}
                  >
                    <span className={cn('w-16 shrink-0 tabular', !color && 'text-muted-strong')}>
                      {line.received ? formatTime(line.received, true) : '—'}
                    </span>
                    <span className="w-40 shrink-0 truncate" title={line.source}>
                      {line.source}
                    </span>
                    <span
                      className={cn(
                        'w-28 shrink-0 truncate font-sans',
                        !color && 'text-muted-strong',
                      )}
                    >
                      {invalid
                        ? t('live.line.invalid')
                        : line.parse_skipped
                          ? t('live.line.skipped')
                          : kindLabel(line)}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      <HighlightedText text={visibleText(line.text)} rules={rules} />
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {unseen > 0 ? (
        <button
          type="button"
          onClick={toBottom}
          className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-13 font-medium text-primary-foreground shadow-sm hover:bg-primary-hover"
          data-testid="live-unseen"
        >
          {t('live.list.unseen', {
            count: unseen,
            formatted: formatNumber(unseen, 0, lang),
          })}
          <ArrowDown aria-hidden="true" className="size-4" />
        </button>
      ) : null}
    </div>
  );
}
