import type { TFunction } from 'i18next';

import { localized } from '@/lib/admin-requests';
import type { FieldProblem } from '@/lib/settings-types';

/**
 * The message shown at a field: the API's refusal of it (422 `details`, its own TR/EN text)
 * before the browser's check (an i18n key, with `values` for its placeholders, e.g. the bounds).
 * `paths` are the body paths the API may name for the field (`clock.ntp_server`, …).
 */
export function fieldMessage(
  t: TFunction,
  problems: readonly FieldProblem[],
  error: string | undefined,
  paths: readonly string[],
  values?: Record<string, unknown>,
): string | undefined {
  const problem = problems.find((item) => paths.includes(item.field));
  if (problem) return localized(problem);
  return error ? t(error, values) : undefined;
}
