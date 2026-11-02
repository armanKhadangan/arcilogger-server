/**
 * Where a legal-validity reason sends the user (`LegalReason.link`, Wave 5 contract 2.3.1): the
 * sealing page or Ayarlar → Zaman damgası. `settings-tsa` is not a menu key (the page is
 * `/settings/kamusm` inside Ayarlar), so the targets are kept here rather than taken from
 * `NAV_ITEMS`. A link is shown only to a user who may open its page: hr and viewer see none, the
 * auditor only the sealing page.
 */

import type { LegalReason } from './types';

export type LegalLinkKey = NonNullable<LegalReason['link']>;

export interface LegalLink {
  /** The route. */
  to: string;
  /** The permission its page needs (`nav.ts`, `routes.tsx`). */
  permission: string;
  /** The link's text (locale key). */
  label: string;
}

export const LEGAL_LINKS: Record<LegalLinkKey, LegalLink> = {
  sealing: { to: '/sealing', permission: 'seal.read', label: 'legalStatus.link.sealing' },
  'settings-tsa': {
    to: '/settings/kamusm',
    permission: 'settings.manage',
    label: 'legalStatus.link.settingsTsa',
  },
};

/** The reason's link, or `null` when it has none or the user may not open its page. */
export function linkFor(
  reason: Pick<LegalReason, 'link'>,
  can: (permission: string) => boolean,
): LegalLink | null {
  if (reason.link === null) return null;
  // An unknown key from a newer server shows no link rather than a broken one.
  const link = (LEGAL_LINKS as Partial<Record<string, LegalLink>>)[reason.link];
  return link && can(link.permission) ? link : null;
}
