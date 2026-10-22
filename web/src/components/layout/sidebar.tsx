import { Lock, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { NavLink, useMatch, useResolvedPath } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Logo } from '@/components/brand/logo';
import { SimpleTooltip } from '@/components/ui/tooltip';
import { useLicense } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { moduleEnabled } from '@/lib/license';
import { cn } from '@/lib/utils';
import { UI_KIT_ITEM, visibleNavItems, type NavItem } from '@/nav';

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

/**
 * One menu entry. The active state is worked out here and NavLink gets a plain `className` and
 * plain children: in the collapsed rail the link sits inside a Radix `TooltipTrigger asChild`,
 * whose Slot joins class names as strings — a NavLink className *function* would arrive as its
 * source text (no `relative`, no active colours, a bar the height of the rail).
 */
function SidebarLink({
  item,
  collapsed,
  locked = false,
}: {
  item: NavItem;
  collapsed: boolean;
  /** The entry's licence module is off: a lock beside the label (the page shows why). */
  locked?: boolean;
}) {
  const { t } = useTranslation();
  const label = t(`nav.${item.key}`);
  const lockText = t('license.locked.navMark');
  const Icon = item.icon;
  const end = item.path === '/';
  const resolved = useResolvedPath(item.path);
  const isActive = useMatch({ path: resolved.pathname, end }) !== null;
  const link = (
    <NavLink
      to={item.path}
      end={end}
      aria-label={collapsed ? (locked ? `${label} (${lockText})` : label) : undefined}
      data-locked={locked ? 'true' : undefined}
      className={cn(
        'relative flex h-9 items-center gap-3 rounded-md px-3 text-sm text-sidebar-text transition-colors hover:bg-sidebar-hover hover:text-white',
        collapsed && 'justify-center px-0',
        isActive && 'bg-sidebar-active font-medium text-white hover:bg-sidebar-active',
      )}
    >
      {isActive ? (
        <span
          aria-hidden="true"
          data-testid="active-bar"
          className="absolute inset-y-1 left-0 w-[3px] rounded-r-sm bg-sidebar-bar"
        />
      ) : null}
      <Icon aria-hidden="true" className="size-[18px] shrink-0" strokeWidth={1.75} />
      {collapsed ? null : <span className="truncate">{label}</span>}
      {locked ? (
        collapsed ? (
          <Lock
            aria-hidden="true"
            data-testid="nav-lock"
            className="absolute right-2 bottom-1 size-3 text-sidebar-muted"
            strokeWidth={2}
          />
        ) : (
          <Lock
            role="img"
            aria-label={lockText}
            data-testid="nav-lock"
            className="ml-auto size-3.5 shrink-0 text-sidebar-muted"
            strokeWidth={2}
          />
        )
      ) : null}
    </NavLink>
  );
  const tooltip = locked ? (
    <span className="flex flex-col">
      <span>{label}</span>
      <span className="flex items-center gap-1 opacity-80">
        <Lock aria-hidden="true" className="size-3" />
        {lockText}
      </span>
    </span>
  ) : (
    label
  );
  return <li>{collapsed ? <SimpleTooltip content={tooltip}>{link}</SimpleTooltip> : link}</li>;
}

/**
 * Dark navigation rail; collapses to icons (state remembered per browser). Shows only the entries
 * the user's role may open (`NavItem.permissions`); an entry whose licence module is off keeps its
 * place with a lock. Under the product name: the organisation's name as the server is configured
 * (`site_name` of `/auth/me`); nothing while it is not set. Under the version: the licensee of a
 * verified licence ("Lisans: …"), nothing without one.
 */
export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const { t } = useTranslation();
  const { me, permissions, canAny } = useAuth();
  const license = useLicense();
  const items = visibleNavItems(permissions);
  const licensee = license.data?.licensee?.trim() ?? '';
  return (
    <aside
      className={cn(
        'sticky top-0 flex h-screen shrink-0 flex-col bg-sidebar text-sidebar-text transition-[width] duration-150',
        collapsed ? 'w-16' : 'w-60',
      )}
    >
      <div className={cn('flex h-14 items-center px-4', collapsed && 'justify-center px-0')}>
        <Logo collapsed={collapsed} caption={me.site_name.trim()} />
      </div>
      <nav aria-label={t('nav.label')} className="flex-1 overflow-y-auto px-2 py-2">
        <ul className="flex flex-col gap-0.5">
          {items.map((item) => (
            <SidebarLink
              key={item.key}
              item={item}
              collapsed={collapsed}
              locked={item.module !== undefined && !moduleEnabled(license.data, item.module)}
            />
          ))}
        </ul>
        {canAny(UI_KIT_ITEM.permissions) ? (
          <>
            <div className="my-3 h-px bg-sidebar-hover" />
            <ul>
              <SidebarLink item={UI_KIT_ITEM} collapsed={collapsed} />
            </ul>
          </>
        ) : null}
      </nav>
      <div
        className={cn(
          'flex items-center gap-2 border-t border-sidebar-hover px-3 py-3',
          collapsed && 'justify-center px-0',
        )}
      >
        {collapsed ? null : (
          <div className="flex min-w-0 flex-1 flex-col text-xs">
            <span className="text-sidebar-muted tabular">
              {t('nav.version')} {__APP_VERSION__}
            </span>
            {licensee ? (
              <span
                className="truncate text-sidebar-muted"
                title={licensee}
                data-testid="sidebar-licensee"
              >
                {t('license.page.sidebarLicensee', { licensee })}
              </span>
            ) : null}
          </div>
        )}
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
          title={collapsed ? t('nav.expand') : t('nav.collapse')}
          className="rounded-md p-2 text-sidebar-muted hover:bg-sidebar-hover hover:text-white"
        >
          {collapsed ? (
            <PanelLeftOpen aria-hidden="true" className="size-[18px]" strokeWidth={1.75} />
          ) : (
            <PanelLeftClose aria-hidden="true" className="size-[18px]" strokeWidth={1.75} />
          )}
        </button>
      </div>
    </aside>
  );
}
