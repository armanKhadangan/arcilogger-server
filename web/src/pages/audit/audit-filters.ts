import type { Range } from '@/components/ui/date-range-picker';
import type { AuditFilters } from '@/lib/api-types';
import { siteDayEnd, siteDayStart } from '@/lib/site-day';

/** What the filter form holds before "Filtrele" is pressed. */
export interface AuditDraft {
  range: Range | null;
  actor: string;
  action: string;
  target: string;
}

export const EMPTY_DRAFT: AuditDraft = { range: null, actor: '', action: '', target: '' };

/**
 * The API query of a draft: the range as whole site days (`from` = start of the first day,
 * inclusive; `to` = start of the day after the last, exclusive — the API's convention), the
 * user name and target exact, the action as a prefix; empty fields are left out.
 */
export function toFilters(draft: AuditDraft): AuditFilters {
  const filters: AuditFilters = {};
  if (draft.range) {
    filters.from = siteDayStart(draft.range.from);
    filters.to = siteDayEnd(draft.range.to);
  }
  const actor = draft.actor.trim();
  const action = draft.action.trim();
  const target = draft.target.trim();
  if (actor) filters.actor = actor;
  if (action) filters.action = action;
  if (target) filters.target = target;
  return filters;
}
