import { ArrowRight, Info } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SimpleTooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/**
 * A block of the Panel: icon and title, an optional status badge, and — when the user may open
 * it — a link to the page with the details (spec 12.3 item 2: "KPIs link to their pages").
 */
export function DashCard({
  title,
  icon: Icon,
  to,
  page,
  status,
  estimate,
  className,
  children,
  testId,
}: {
  title: string;
  icon: LucideIcon;
  /** Target of the "details" link; `null`/absent: no link (the user may not open the page). */
  to?: string | null;
  /** Name of the target page (link text). */
  page?: string;
  status?: ReactNode;
  /** The usage method note: an info icon next to the title. */
  estimate?: string;
  className?: string;
  children: ReactNode;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <Card className={cn('flex min-w-0 flex-col', className)} data-testid={testId}>
      <CardHeader className="items-center">
        <CardTitle className="flex min-w-0 items-center gap-2">
          <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-strong" />
          <span className="truncate">{title}</span>
          {estimate ? <EstimateHint text={estimate} /> : null}
        </CardTitle>
        <div className="flex shrink-0 items-center gap-2">
          {status}
          {to && page ? (
            <Link
              to={to}
              className="inline-flex items-center gap-1 rounded-sm text-13 font-medium whitespace-nowrap text-accent-ink hover:underline"
              aria-label={t('dashboard.openPage', { page })}
            >
              {page}
              <ArrowRight aria-hidden="true" className="size-3.5" />
            </Link>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">{children}</CardContent>
    </Card>
  );
}

/** "Tahmini" info icon: usage figures are estimates from connection logs (never exact). */
export function EstimateHint({ text }: { text: string }) {
  return (
    <SimpleTooltip content={text}>
      <button type="button" className="rounded-sm text-muted" aria-label={text}>
        <Info aria-hidden="true" className="size-3.5" />
      </button>
    </SimpleTooltip>
  );
}

/** The large figure of a card with a line under it. */
export function BigValue({ value, hint }: { value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-2xl leading-8 font-semibold text-foreground tabular">{value}</span>
      {hint ? <span className="text-13 text-muted">{hint}</span> : null}
    </div>
  );
}
