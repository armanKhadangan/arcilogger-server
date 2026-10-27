import { Bell, Search } from 'lucide-react';
import { useState } from 'react';
import { useLocation, useMatches, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/lib/auth';
import { handoff } from '@/lib/handoff';
import { useSearchFields } from '@/lib/search-hooks';
import { TimeRangePicker } from '@/lib/time-range';
import { BUILT_PAGES } from '@/nav';
import { termToQuery } from '@/pages/search/search-model';

import { UserMenu } from './user-menu';

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
export function Topbar() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = useAuth();
  const [query, setQuery] = useState('');
  const mayRead = can('search.read');
  const timeRange = useTimeRangeRoute() && mayRead;
  const fields = useSearchFields(timeRange);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface px-4 md:px-6">
      {BUILT_PAGES.has('search') && mayRead ? (
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
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
            placeholder={t('topbar.searchPlaceholderLogs')}
            aria-label={t('topbar.searchLabel')}
            leadingIcon={<Search />}
          />
        </form>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {timeRange ? <TimeRangePicker maxDays={fields.data?.limits.max_days} /> : null}
        {can('alerts.read') ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('topbar.notifications')}
            title={t('topbar.notifications')}
          >
            <Bell aria-hidden="true" />
          </Button>
        ) : null}
        <UserMenu />
      </div>
    </header>
  );
}
