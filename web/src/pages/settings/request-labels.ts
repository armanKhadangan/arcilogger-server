import type { TFunction } from 'i18next';

import type { AdminRequestOut } from '@/lib/settings-types';

/** "Genel · Yapılandırma değişikliği", "Şimdi mühürle", … */
export function requestTitle(
  request: Pick<AdminRequestOut, 'kind' | 'section'>,
  t: TFunction,
): string {
  const kind = t(`admin.kinds.${request.kind}`);
  return request.section ? `${t(`admin.sections.${request.section}`)} · ${kind}` : kind;
}
