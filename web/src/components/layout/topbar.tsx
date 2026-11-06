import { Bell, Compass, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useMatches, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAlertSummary } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { handoff } from '@/lib/handoff';
import { useSearchFields } from '@/lib/search-hooks';
import { TimeRangePicker } from '@/lib/time-range';
import { BUILT_PAGES } from '@/nav';
import { termToQuery } from '@/pages/search/search-model';

import { paletteKeys } from './command-palette-model';
import { UserMenu } from './user-menu';

/** Whether the key press happened in a field the user types into (its keys stay its own). */
function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.closest('input, textarea, select, [contenteditable="true"], [role="combobox"]') !== null
  );
}

/**
 * "/" moves to the global search box, as on most web tools, unless the user is typing or a
 * dialog is open.
 */
function useSlashToSearch(input: React.RefObject<HTMLInputElement | null>, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      if (typingIn(event.target)) return;
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return;
      event.preventDefault();
      input.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [enabled, input]);
}

/**
 * The bell: the open security alerts (all severities) and a link to Güvenlik. Nothing is shown
 * while the count is loading or failed; the Panel and Güvenlik say more.
 */
function AlertsBell() {
  const { t } = useTranslation();
  const summary = useAlertSummary();
  const counts = summary.data?.open_by_severity;
  const open = counts
    ? counts.critical + counts.high + counts.medium + counts.low + counts.info
    : 0;
  const label = open > 0 ? t('topbar.alertsOpen', { count: open }) : t('topbar.alertsNone');
  return (
    <Button variant="ghost" size="icon" asChild>
      <Link to="/security" aria-label={label} title={label} className="relative">
        <Bell aria-hidden="true" />
        {open > 0 ? (
          <span
            aria-hidden="true"
            data-testid="alerts-count"
            className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] leading-none font-semibold text-white tabular"
          >
            {open > 99 ? '99+' : open}
          </span>
        ) : null}
      </Link>
    </Button>
  );
}

/** Whether the current route asks for the global time range (`handle: {timeRange: true}`). */
function useTimeRangeRoute(): boolean {
  return useMatches().some(
    (match) => (match.handle as { timeRange?: boolean } | undefined)?.timeRange === true,
  );
}

/**
 * Global search (with `search.read`), the global time range (on the routes that read it), alerts
 * (with `alerts.read`), user menu.
 *
 * - The search box turns its term into a query (an IPv4 address → `ip:`, a MAC address → `mac:`,
 *   a host name → `domain:`, anything else quoted free text; `pages/search/search-model.ts`) and
 *   hands it to Arama in memory (`lib/handoff.ts`), so it stays out of URLs, `?next=` after a
 *   time-out, the web server's access log and the tab's session history (`history.state`, which
 *   the browser may write to disk for session restore). On Arama itself the range in its address
 *   is kept.
 * - The time range of spec 12.2 ("[Son 24 saat ▾]", `lib/time-range.tsx`) appears only on routes
 *   with `handle.timeRange` (Arama): a picker that filters nothing would make a page look filtered
 *   when it is not (e.g. Denetim Kaydı). "Kullanım" has its own period, kept in its address
 *   (`?from=&to=`), next to its tabs. The longest range is the site's `search.max_days`.
 */
export function Topbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = useAuth();
  const [query, setQuery] = useState('');
  const searchBox = useRef<HTMLInputElement>(null);
  const mayRead = can('search.read');
  const hasSearch = BUILT_PAGES.has('search') && mayRead;
  useSlashToSearch(searchBox, hasSearch);
  const timeRange = useTimeRangeRoute() && mayRead;
  const fields = useSearchFields(timeRange);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface px-4 md:px-6">
      {hasSearch ? (
        <form
          role="search"
          className="w-full max-w-md"
          onSubmit={(event) => {
            event.preventDefault();
            const q = termToQuery(query);
            if (q) handoff.put('/search', { q });
            setQuery('');
            void navigate({
              pathname: '/search',
              search: location.pathname === '/search' ? location.search : '',
            });
          }}
        >
          <Input
            ref={searchBox}
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
            placeholder={t('topbar.searchPlaceholderLogs')}
            aria-label={t('topbar.searchLabel')}
            aria-keyshortcuts="/"
            title={t('topbar.searchShortcut')}
            leadingIcon={<Search />}
          />
        </form>
      ) : null}
      <Button
        variant="secondary"
        size="sm"
        onClick={onOpenPalette}
        aria-keyshortcuts="Control+K Meta+K"
        className="shrink-0 gap-2 text-muted-strong"
      >
        <Compass aria-hidden="true" />
        <span className="hidden lg:inline">{t('palette.open')}</span>
        <kbd className="hidden rounded border border-border bg-subtle px-1.5 font-sans text-[11px] text-muted-strong lg:inline">
          {paletteKeys()}
        </kbd>
      </Button>
      <div className="ml-auto flex items-center gap-2">
        {timeRange ? <TimeRangePicker maxDays={fields.data?.limits.max_days} /> : null}
        {can('alerts.read') ? <AlertsBell /> : null}
        <UserMenu />
      </div>
    </header>
  );
}
