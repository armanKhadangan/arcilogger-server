import { Bell, Search } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/lib/auth';
import { handoff } from '@/lib/handoff';
import { BUILT_PAGES } from '@/nav';

import { UserMenu } from './user-menu';

/**
 * Global search (with `search.read`), alerts (with `alerts.read`), user menu.
 *
 * - The search box appears once the "Arama" page exists (`BUILT_PAGES`): until then it would do
 *   nothing. Its term (IP, MAC, person, domain) is handed to the page in memory (`lib/handoff.ts`),
 *   so it stays out of URLs, `?next=` after a time-out, the web server's access log and the tab's
 *   session history (`history.state`, which the browser may write to disk for session restore).
 * - The global time range of spec 12.2 ("[Bugün ▾]") comes with the first page that reads it
 *   (Panel, Arama; M2). A picker that filters nothing would make a page look filtered to
 *   "today" when it is not (e.g. Denetim Kaydı). "Kullanım" has its own period, kept in its
 *   address (`?from=&to=`), next to its tabs.
 */
export function Topbar() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [query, setQuery] = useState('');

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface px-4 md:px-6">
      {BUILT_PAGES.has('search') && can('search.read') ? (
        <form
          role="search"
          className="w-full max-w-md"
          onSubmit={(event) => {
            event.preventDefault();
            const q = query.trim();
            if (q) handoff.put('/search', { q });
            void navigate('/search');
          }}
        >
          <Input
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
            }}
            placeholder={t('topbar.searchPlaceholder')}
            aria-label={t('topbar.searchLabel')}
            leadingIcon={<Search />}
          />
        </form>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
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
