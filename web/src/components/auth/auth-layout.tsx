import { Languages, Moon, Sun } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { LogoMark } from '@/components/brand/logo';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { currentLang, setLanguage } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';

interface AuthLayoutProps {
  /** Heading of the card (the page's h1). */
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** Wider card (TOTP enrolment, setup wizard). */
  wide?: boolean;
  /**
   * The step of a multi-step flow shown in the card (sign-in: password, TOTP, enrolment, recovery
   * codes, password change). When it changes, the focus moves to the heading — unless the new
   * step already focused one of its fields — so keyboard users go on inside the card instead of
   * at the top of the page, and screen readers announce the new step.
   */
  step?: string | undefined;
}

/**
 * Pages outside the application shell (sign-in, first-run setup): centred card with the logo and
 * the tagline "Kayıt · Mühür · Rapor" (spec 12.1/12.3), language and theme switch, and the
 * product disclaimer.
 */
export function AuthLayout({ title, description, children, wide = false, step }: AuthLayoutProps) {
  const { t } = useTranslation();
  const { resolved, setPreference } = useTheme();
  const english = currentLang() === 'en';
  const heading = useRef<HTMLHeadingElement>(null);
  const shownStep = useRef(step);

  useEffect(() => {
    const previous = shownStep.current;
    shownStep.current = step;
    // The first step after loading is the page itself: the browser's start (or its field) rules.
    if (previous === undefined || previous === step) return;
    const active = document.activeElement;
    const main = heading.current?.closest('main');
    if (active && active !== document.body && main?.contains(active)) return;
    heading.current?.focus();
  }, [step]);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex justify-end gap-1 px-4 pt-4">
        <Button
          variant="ghost"
          size="sm"
          lang={english ? 'tr' : 'en'}
          onClick={() => {
            setLanguage(english ? 'tr' : 'en');
          }}
        >
          <Languages aria-hidden="true" />
          {english ? 'Türkçe' : 'English'}
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={resolved === 'dark' ? t('auth.themeLight') : t('auth.themeDark')}
          title={resolved === 'dark' ? t('auth.themeLight') : t('auth.themeDark')}
          onClick={() => {
            setPreference(resolved === 'dark' ? 'light' : 'dark');
          }}
        >
          {resolved === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        </Button>
      </header>
      <main
        id="content"
        className="flex flex-1 items-start justify-center px-4 pt-4 pb-10 sm:items-center"
      >
        <div className={cn('flex w-full flex-col gap-6', wide ? 'max-w-2xl' : 'max-w-md')}>
          <div className="flex flex-col items-center gap-1.5 text-center">
            <LogoMark className="size-11 text-primary" />
            <span className="text-2xl font-bold tracking-[0.04em] text-foreground">ARCILOGGER</span>
            <span className="text-13 text-muted">{t('app.tagline')}</span>
          </div>
          <Card className="flex flex-col gap-5 p-6 sm:p-8">
            <div className="flex flex-col gap-1">
              <h1
                ref={heading}
                tabIndex={-1}
                className="text-xl font-semibold text-foreground outline-none"
              >
                {title}
              </h1>
              {description ? <p className="text-13 text-muted">{description}</p> : null}
            </div>
            {children}
          </Card>
          <p className="text-center text-xs text-muted">{t('app.disclaimer')}</p>
        </div>
      </main>
    </div>
  );
}
