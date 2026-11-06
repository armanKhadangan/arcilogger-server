import { useEffect, type ReactNode } from 'react';

/** The browser tab's name outside a page (and of `index.html`). */
const PRODUCT_TITLE = 'ARCILOGGER';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  /** Primary action(s), right-aligned. */
  actions?: ReactNode;
}

/**
 * Names the browser tab after the page ("Mühürleme · ARCILOGGER"), so several open tabs and the
 * browser's history tell the pages apart; back to the product's name when the page goes.
 */
function usePageTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · ${PRODUCT_TITLE}` : PRODUCT_TITLE;
    return () => {
      document.title = PRODUCT_TITLE;
    };
  }, [title]);
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  usePageTitle(title);
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 pb-6">
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="text-2xl font-semibold text-foreground">{title}</h1>
        {description ? <p className="max-w-3xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
