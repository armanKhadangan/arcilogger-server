import { Command } from 'cmdk';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { useAuth } from '@/lib/auth';
import { handoff } from '@/lib/handoff';
import { BUILT_PAGES, NAV_GROUPS } from '@/nav';
import { termToQuery } from '@/pages/search/search-model';

import { paletteScore, usePaletteEntries } from './command-palette-model';

/**
 * "Sayfaya git": every page and Ayarlar section the user may open, found by typing a few letters
 * (Turkish letters optional, either language), opened with Enter. With text typed and
 * `search.read`, its last line searches the logs for it, as the top bar's search box does (last,
 * so that Enter opens the page that matches).
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { can } = useAuth();
  const entries = usePaletteEntries();
  const [query, setQuery] = useState('');
  const maySearch = BUILT_PAGES.has('search') && can('search.read');

  const close = () => {
    onOpenChange(false);
    setQuery('');
  };
  const go = (to: string) => {
    close();
    void navigate(to);
  };
  const searchLogs = () => {
    const q = termToQuery(query);
    close();
    if (q) handoff.put('/search', { q });
    void navigate({
      pathname: '/search',
      search: location.pathname === '/search' ? location.search : '',
    });
  };

  const headings = [
    ...NAV_GROUPS.map((group) => t(`nav.groups.${group}`)),
    t('nav.settings'),
  ].filter((heading, index, all) => all.indexOf(heading) === index);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) onOpenChange(true);
        else close();
      }}
    >
      <DialogContent
        hideClose
        data-command-palette=""
        data-testid="command-palette"
        className="top-[15vh] max-w-xl translate-y-0 gap-0 overflow-hidden p-0"
      >
        <DialogTitle className="sr-only">{t('palette.title')}</DialogTitle>
        <DialogDescription className="sr-only">{t('palette.description')}</DialogDescription>
        <Command label={t('palette.title')} filter={paletteScore} className="flex flex-col">
          <div className="flex items-center gap-2 border-b border-border px-4">
            <Search aria-hidden="true" className="size-4 shrink-0 text-muted" />
            <Command.Input
              value={query}
              onValueChange={setQuery}
              placeholder={t('palette.placeholder')}
              className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted"
            />
          </div>
          <Command.List className="max-h-[min(60vh,28rem)] overflow-y-auto p-2">
            <Command.Empty className="px-3 py-8 text-center text-13 text-muted">
              {t('palette.empty')}
            </Command.Empty>
            {headings.map((heading) => {
              const items = entries.filter((entry) => entry.heading === heading);
              if (items.length === 0) return null;
              return (
                <Command.Group
                  key={heading}
                  heading={heading}
                  className="mb-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-strong"
                >
                  {items.map((entry) => {
                    const Icon = entry.icon;
                    return (
                      <Command.Item
                        key={entry.id}
                        // Unique (a page and an Ayarlar section can share a name) and findable by
                        // its heading too: "ayarlar" lists every section.
                        value={`${entry.label} · ${entry.heading}`}
                        keywords={entry.keywords}
                        onSelect={() => {
                          go(entry.to);
                        }}
                        className="flex h-10 cursor-pointer items-center gap-3 rounded-md px-3 text-sm text-foreground select-none data-[selected=true]:bg-subtle"
                      >
                        <Icon
                          aria-hidden="true"
                          className="size-4 shrink-0 text-muted-strong"
                          strokeWidth={1.75}
                        />
                        <span className="truncate">{entry.label}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              );
            })}
            {maySearch && query.trim() ? (
              <Command.Group
                heading={t('nav.search')}
                forceMount
                className="mb-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-strong"
              >
                <Command.Item
                  value="__search_logs__"
                  forceMount
                  onSelect={searchLogs}
                  className="flex h-10 cursor-pointer items-center gap-3 rounded-md px-3 text-sm text-foreground select-none data-[selected=true]:bg-subtle"
                >
                  <Search aria-hidden="true" className="size-4 shrink-0 text-muted-strong" />
                  <span className="truncate">
                    {t('palette.searchLogs', { query: query.trim() })}
                  </span>
                </Command.Item>
              </Command.Group>
            ) : null}
          </Command.List>
          <p className="border-t border-border px-4 py-2 text-xs text-muted-strong">
            {t('palette.keys')}
          </p>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
