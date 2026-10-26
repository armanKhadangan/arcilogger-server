import type { AdminConfig, AdminSection } from '@/lib/settings-types';

import { RequestProgress } from './request-progress';

/**
 * The held changes of a section (`GET /admin/config` `pending`, contract 2.17 `pending`): each
 * with the countdown to its `effective_at` and "İptal et", also after the page was left and
 * opened again. `except`: the request (or requests) the page already follows.
 */
export function HeldChanges({
  config,
  section,
  except,
}: {
  config: AdminConfig | undefined;
  section: AdminSection;
  except: string | null | readonly (string | null)[];
}) {
  const shown = new Set([except].flat());
  const pending =
    config?.pending.filter((item) => item.section === section && !shown.has(item.request_id)) ?? [];
  if (pending.length === 0) return null;
  return (
    <div className="flex flex-col gap-3" data-testid="held-change">
      {pending.map((item) => (
        <RequestProgress key={item.request_id} requestId={item.request_id} />
      ))}
    </div>
  );
}
