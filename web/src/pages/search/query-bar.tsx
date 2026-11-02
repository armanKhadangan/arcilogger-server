import { CircleHelp, Search } from 'lucide-react';
import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { currentLang } from '@/lib/i18n';
import type { FieldCatalogue, SearchFacets } from '@/lib/search-types';
import { cn } from '@/lib/utils';

import { completions, type Completion, type QueryProblem } from './search-model';

/** The query split at the first problem's span, for the underline behind the input. */
function underline(value: string, problem: QueryProblem | undefined) {
  if (!problem) return null;
  const start = Math.max(0, Math.min(problem.start, value.length));
  // An empty span (a missing value at the end) is drawn under one blank.
  const end = Math.max(start + 1, Math.min(problem.end, value.length));
  return {
    before: value.slice(0, start),
    marked: value.slice(start, end).padEnd(end - start, ' '),
    after: value.slice(end),
  };
}

/**
 * The query bar: one line of query language in monospace. 250 ms after typing the page asks the
 * server to read it (`POST /search/parse`); a problem is underlined at its characters (an overlay
 * behind the transparent input, scrolled with it) and named below in the interface language. Field
 * names are completed from the catalogue, values from the current statistics (a combobox list:
 * arrow keys, Enter or Tab to take one, Escape to close). "?" opens the syntax help.
 */
export function QueryBar({
  value,
  onChange,
  onSubmit,
  onHelp,
  catalogue,
  facets,
  problems,
  busy,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onHelp: () => void;
  catalogue: FieldCatalogue | undefined;
  facets: SearchFacets | undefined;
  problems: QueryProblem[];
  busy: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const lang = currentLang();
  const input = useRef<HTMLInputElement>(null);
  const [caret, setCaret] = useState(0);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [active, setActive] = useState(-1);
  const [scroll, setScroll] = useState(0);
  const placeCaret = useRef<number | null>(null);

  const found = useMemo(
    () => (focused && !dismissed ? completions(value, caret, catalogue, facets, lang) : null),
    [focused, dismissed, value, caret, catalogue, facets, lang],
  );
  const open = found !== null;
  const marked = underline(value, problems[0]);
  const listId = `${id}-list`;
  const problemsId = `${id}-problems`;

  function track() {
    const element = input.current;
    if (!element) return;
    setCaret(element.selectionStart ?? element.value.length);
    setScroll(element.scrollLeft);
  }

  function accept(item: Completion) {
    if (!found) return;
    const tail = value.slice(found.end);
    const spacer = item.insert.endsWith(':') || tail.startsWith(' ') ? '' : ' ';
    const next = `${value.slice(0, found.start)}${item.insert}${spacer}${tail}`;
    const position = found.start + item.insert.length + spacer.length;
    onChange(next);
    setActive(-1);
    setCaret(position);
    placeCaret.current = position;
  }

  // The caret goes behind a taken completion as soon as the input holds it, before the next key
  // is handled: a later frame would pull it back from what was typed meanwhile.
  useLayoutEffect(() => {
    const position = placeCaret.current;
    if (position === null) return;
    placeCaret.current = null;
    input.current?.setSelectionRange(position, position);
    track();
  });

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!found) return;
    const count = found.items.length;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((current) => (current + 1) % count);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((current) => (current <= 0 ? count - 1 : current - 1));
    } else if ((event.key === 'Enter' || event.key === 'Tab') && active >= 0) {
      const item = found.items[active];
      if (item) {
        event.preventDefault();
        accept(item);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setDismissed(true);
      setActive(-1);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <form
        role="search"
        aria-label={t('search.query.form')}
        className="flex items-start gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setDismissed(true);
          onSubmit(value);
        }}
      >
        <div className="relative min-w-0 flex-1">
          <label htmlFor={`${id}-q`} className="sr-only">
            {t('search.query.label')}
          </label>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 z-10 size-4 -translate-y-1/2 text-muted"
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 overflow-hidden rounded-lg border border-transparent pr-3 pl-9 font-mono text-13 leading-[34px] whitespace-pre text-transparent"
          >
            {marked ? (
              <span className="inline-block" style={{ transform: `translateX(${-scroll}px)` }}>
                {marked.before}
                <span
                  data-testid="query-problem"
                  className="underline decoration-danger decoration-wavy decoration-2 underline-offset-4"
                >
                  {marked.marked}
                </span>
                {marked.after}
              </span>
            ) : null}
          </div>
          <input
            ref={input}
            id={`${id}-q`}
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
            aria-invalid={problems.length > 0 ? true : undefined}
            aria-describedby={problems.length > 0 ? problemsId : `${id}-hint`}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={catalogue?.limits.max_chars ?? 1000}
            value={value}
            placeholder={t('search.query.placeholder')}
            className={cn(
              'relative h-9 w-full min-w-0 rounded-lg border border-border bg-transparent pr-3 pl-9 font-mono text-13 text-foreground placeholder:font-sans placeholder:text-muted',
              problems.length > 0 && 'border-danger',
            )}
            onChange={(event) => {
              onChange(event.target.value);
              setDismissed(false);
              setActive(-1);
              setCaret(event.target.selectionStart ?? event.target.value.length);
              setScroll(event.target.scrollLeft);
            }}
            onKeyDown={keyDown}
            onKeyUp={track}
            onClick={track}
            onSelect={track}
            onScroll={track}
            onFocus={() => {
              setFocused(true);
              track();
            }}
            onBlur={() => {
              setFocused(false);
              setActive(-1);
            }}
          />
          <ul
            id={listId}
            role="listbox"
            aria-label={t('search.query.suggestions')}
            hidden={!open}
            className="absolute top-10 right-0 left-0 z-40 max-h-72 overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-sm"
          >
            {found?.items.map((item, index) => (
              <li
                key={item.insert}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                className={cn(
                  'flex cursor-pointer items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-13',
                  index === active ? 'bg-subtle' : 'hover:bg-subtle',
                )}
                onMouseDown={(event) => {
                  // Keeps the focus (and the caret) in the input.
                  event.preventDefault();
                  accept(item);
                }}
              >
                <span className="font-mono text-xs text-foreground">{item.label}</span>
                {item.detail ? (
                  <span className="truncate text-xs text-muted">{item.detail}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="icon"
          aria-label={t('search.query.help')}
          title={t('search.query.help')}
          onClick={onHelp}
        >
          <CircleHelp aria-hidden="true" />
        </Button>
        <Button type="submit" busy={busy}>
          {busy ? t('search.query.running') : t('search.query.submit')}
        </Button>
      </form>
      {problems.length > 0 ? (
        <div id={problemsId} className="flex flex-col gap-0.5" aria-live="polite">
          {problems.map((problem) => (
            <p key={`${problem.code}-${problem.start}`} className="text-13 text-danger-ink">
              {lang === 'en' ? problem.message_en : problem.message_tr}
              {value.slice(problem.start, problem.end) ? (
                <span className="ml-1.5 font-mono text-xs">{`“${value.slice(problem.start, problem.end)}”`}</span>
              ) : null}
            </p>
          ))}
        </div>
      ) : (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {t('search.query.hint')}
        </p>
      )}
    </div>
  );
}
