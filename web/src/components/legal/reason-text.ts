/**
 * The words of a legal-validity reason (Wave 5 contract 2.6). The sentence is the API's
 * (`message_tr` / `message_en`, W5-C): it carries the counts, hours and names the web does not get
 * separately. The web appends the documentation reference, "ZAMAN-DAMGASI.md bölüm N" /
 * "TIME-STAMPING.md section N", from the locale keys `legalStatus.docs.<code>`, so every banner text
 * ends with it. The banner and the sealing page's alert both call {@link reasonText}: the same
 * text in both places.
 */

import type { AlertTone } from '@/components/ui/alert';
import type { Lang } from '@/lib/format';
import i18n from '@/lib/i18n';

import type { LegalReason } from './types';

/** The documentation section of a reason's code in `lang`, or `null` for a code it does not know. */
export function docsReference(code: string, lang: Lang): string | null {
  const key = `legalStatus.docs.${code}`;
  return i18n.exists(key, { lng: lang }) ? i18n.t(key, { lng: lang }) : null;
}

/** The reason's sentence in `lang`, followed by its documentation section. */
export function reasonText(
  reason: Pick<LegalReason, 'code' | 'message_tr' | 'message_en'>,
  lang: Lang,
): string {
  const message = lang === 'en' ? reason.message_en : reason.message_tr;
  const reference = docsReference(reason.code, lang);
  return reference ? `${message} (${reference})` : message;
}

/** Danger is red, warning amber (`Alert` adds the icon and the role). */
export function reasonTone(level: LegalReason['level']): AlertTone {
  return level === 'danger' ? 'danger' : 'warning';
}
