import { ChevronDown, KeyRound, LogOut } from 'lucide-react';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { RecoveryCodesDialog } from '@/components/auth/recovery-codes-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAuth, useSignOut } from '@/lib/auth';
import { currentLang, setLanguage } from '@/lib/i18n';
import { useTheme, type ThemePreference } from '@/lib/theme';
import { initials } from '@/lib/utils';

/** Name, role, theme, language, session note and "Çıkış yap" (spec 12.2: top right). */
export function UserMenu() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const signOut = useSignOut();
  const { preference, setPreference } = useTheme();
  const [codesOpen, setCodesOpen] = useState(false);
  /** The dialog opened from the menu gives the focus back to the menu's button. */
  const trigger = useRef<HTMLButtonElement>(null);
  const lang = currentLang();
  const roleLabel = lang === 'en' ? me.user.role_label_en : me.user.role_label_tr;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            ref={trigger}
            variant="ghost"
            className="gap-2 px-2"
            aria-label={t('topbar.userMenuFor', { name: me.user.display_name })}
          >
            <span
              aria-hidden="true"
              className="flex size-7 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary-ink"
            >
              {initials(me.user.display_name)}
            </span>
            <span className="hidden max-w-40 truncate text-sm lg:inline">
              {me.user.display_name}
            </span>
            <ChevronDown aria-hidden="true" className="text-muted" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-64">
          <div className="flex flex-col px-2 py-1.5">
            <span className="truncate text-sm font-medium text-foreground">
              {me.user.display_name}
            </span>
            <span className="truncate text-xs text-muted">
              {roleLabel} · <span className="font-mono">{me.user.username}</span>
            </span>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>{t('topbar.theme')}</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={preference}
            onValueChange={(value) => {
              setPreference(value as ThemePreference);
            }}
          >
            <DropdownMenuRadioItem value="light">{t('topbar.themeLight')}</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">{t('topbar.themeDark')}</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="system">{t('topbar.themeSystem')}</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>{t('topbar.language')}</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={lang}
            onValueChange={(value) => {
              setLanguage(value === 'en' ? 'en' : 'tr');
            }}
          >
            <DropdownMenuRadioItem value="tr">Türkçe</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="en">English</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          {me.mfa.enabled ? (
            <DropdownMenuItem
              onSelect={() => {
                setCodesOpen(true);
              }}
            >
              <KeyRound aria-hidden="true" />
              {t('recovery.regenerateTitle')}
            </DropdownMenuItem>
          ) : null}
          <p className="px-2 py-1.5 text-xs text-muted">
            {t('topbar.sessionNote', { minutes: me.idle_timeout_minutes })}
          </p>
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOut aria-hidden="true" />
            {t('topbar.logout')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RecoveryCodesDialog open={codesOpen} onOpenChange={setCodesOpen} returnFocus={trigger} />
    </>
  );
}
