/**
 * What the "Uyum Durumu" page (Wave 4 contract 2.8.1) shows to whom, without components
 * (unit-tested in `compliance-model.test.ts`).
 *
 * The server words every card in Turkish and English (decision D3): the page picks the texts of
 * the interface language and never words a card itself. A card's links are nav keys; a link the
 * user may not open is left out, the card stays (D13). The auditor (D15: `compliance.read`
 * without `settings.manage`) sees every card, but neither the backup confirmation nor the wizard.
 */

import { CircleCheck, CircleDashed, CircleX, TriangleAlert, type LucideIcon } from 'lucide-react';

import type { BadgeProps } from '@/components/ui/badge';
import type {
  ComplianceCard,
  ComplianceCounts,
  ComplianceStatus,
  ComplianceTone,
} from '@/lib/compliance-types';
import type { Lang } from '@/lib/format';
import { linkTarget } from '@/pages/dashboard/dashboard-model';
import { alertHref } from '@/pages/security/alerts-model';

/** Who may confirm the offline configuration copy and open the first-run wizard. */
export const MANAGE_PERMISSION = 'settings.manage';
/** The first-run wizard (contract 2.8.4). */
export const WIZARD_PATH = '/settings/setup';

/** A tone's badge: colour, icon and text (status is never shown by colour alone). */
export const TONE_STYLE: Record<
  ComplianceTone,
  { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon; label: string }
> = {
  ok: { tone: 'success', icon: CircleCheck, label: 'compliance.tone.ok' },
  warning: { tone: 'warning', icon: TriangleAlert, label: 'compliance.tone.warning' },
  error: { tone: 'danger', icon: CircleX, label: 'compliance.tone.error' },
  neutral: { tone: 'neutral', icon: CircleDashed, label: 'compliance.tone.neutral' },
};

/** The tones from the most to the least urgent (the summary's chips). */
export const TONE_ORDER: readonly ComplianceTone[] = ['error', 'warning', 'neutral', 'ok'];

/** "{{ok}} of {{total}} checks OK": the figures of the summary line. */
export function summaryFigures(counts: ComplianceCounts): { ok: number; total: number } {
  return { ok: counts.ok, total: counts.ok + counts.warning + counts.error + counts.neutral };
}

/** The tones that occur, most urgent first, with their number of cards. */
export function toneCounts(counts: ComplianceCounts): { tone: ComplianceTone; count: number }[] {
  return TONE_ORDER.map((tone) => ({ tone, count: counts[tone] })).filter((item) => item.count > 0);
}

/** A card's texts in the interface language. */
export interface CardTexts {
  title: string;
  reason: string;
  facts: { key: string; label: string; value: string }[];
  doc: string;
  verifyOnSite: string | null;
}

export function cardTexts(card: ComplianceCard, lang: Lang): CardTexts {
  const en = lang === 'en';
  return {
    title: en ? card.title_en : card.title_tr,
    reason: en ? card.reason_en : card.reason_tr,
    facts: card.facts.map((fact) => ({
      key: fact.key,
      label: en ? fact.label_en : fact.label_tr,
      value: en ? fact.value_en : fact.value_tr,
    })),
    doc: en ? card.doc_en : card.doc_tr,
    verifyOnSite: en ? card.verify_on_site_en : card.verify_on_site_tr,
  };
}

/** Where a card links to; `null` where the user may not open the page. */
export interface CardLinks {
  /** The page with the details (`card.page`). */
  page: string | null;
  /** Güvenlik with the card's alert open (one alert) or the list (several); `null` without any. */
  alerts: string | null;
}

export function cardLinks(card: ComplianceCard, granted: ReadonlySet<string>): CardLinks {
  const security = card.alerts.length > 0 ? linkTarget('security', granted) : null;
  const only = card.alerts.length === 1 ? card.alerts[0] : undefined;
  return {
    page: linkTarget(card.page, granted),
    alerts: security && only ? alertHref(only.id) : security,
  };
}

/** "Yapılandırma yedeğini aldım": on card 10, for administrators only. */
export function canConfirmBackup(card: ComplianceCard, granted: ReadonlySet<string>): boolean {
  return card.id === 'backup' && granted.has(MANAGE_PERMISSION);
}

/** "İlk kurulum sihirbazını aç": while a card is not `ok`, for administrators only. */
export function showWizardLink(status: ComplianceStatus, granted: ReadonlySet<string>): boolean {
  return granted.has(MANAGE_PERMISSION) && status.cards.some((card) => card.tone !== 'ok');
}
