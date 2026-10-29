import { CircleCheck, CircleDashed, CircleX, TriangleAlert } from 'lucide-react';

import { makeMe } from '@/test/api-mock';
import {
  allOkCompliance,
  complianceCard,
  complianceStatus,
  COMPLIANCE_CARD_IDS,
  COMPLIANCE_TONES,
} from '@/test/w4-fixtures';

import {
  canConfirmBackup,
  cardLinks,
  cardTexts,
  showWizardLink,
  summaryFigures,
  toneCounts,
  TONE_STYLE,
} from './compliance-model';

/*
 * The model of Uyum Durumu (Wave 4 contract 2.8.1): the tones' badges, the server's texts by
 * interface language, and who sees which link and action (D13, D15).
 */

const ADMIN = new Set(makeMe('admin').permissions);
/** The auditor once W4-F grants `compliance.read` (D15). */
const AUDITOR = new Set([...makeMe('auditor').permissions, 'compliance.read']);

describe('TONE_STYLE', () => {
  it('gives every tone a badge tone, an icon and a text (never colour alone)', () => {
    expect(TONE_STYLE).toEqual({
      ok: { tone: 'success', icon: CircleCheck, label: 'compliance.tone.ok' },
      warning: { tone: 'warning', icon: TriangleAlert, label: 'compliance.tone.warning' },
      error: { tone: 'danger', icon: CircleX, label: 'compliance.tone.error' },
      neutral: { tone: 'neutral', icon: CircleDashed, label: 'compliance.tone.neutral' },
    });
    expect(Object.keys(TONE_STYLE).sort()).toEqual([...COMPLIANCE_TONES].sort());
  });
});

describe('the summary', () => {
  it('counts the cards that are ok out of all', () => {
    expect(summaryFigures(complianceStatus().counts)).toEqual({ ok: 4, total: 10 });
    expect(summaryFigures(allOkCompliance().counts)).toEqual({ ok: 10, total: 10 });
  });

  it('lists the tones that occur, most urgent first', () => {
    expect(toneCounts(complianceStatus().counts)).toEqual([
      { tone: 'error', count: 2 },
      { tone: 'warning', count: 3 },
      { tone: 'neutral', count: 1 },
      { tone: 'ok', count: 4 },
    ]);
    expect(toneCounts(allOkCompliance().counts)).toEqual([{ tone: 'ok', count: 10 }]);
  });
});

describe('cardTexts', () => {
  it('takes the Turkish texts in Turkish and the English ones in English', () => {
    const card = complianceCard('tsa');
    const tr = cardTexts(card, 'tr');
    const en = cardTexts(card, 'en');
    expect(tr.title).toBe('Kamu SM kontörü ve zaman damgası sertifikası');
    expect(en.title).toBe('Kamu SM credit and time-stamp certificate');
    expect(tr.reason).toBe(card.reason_tr);
    expect(en.reason).toBe(card.reason_en);
    expect(tr.doc).toBe('UYUM-DURUMU.md bölüm 2.5');
    expect(en.doc).toBe('COMPLIANCE-STATUS.md section 2.5');
    expect(tr.verifyOnSite).toBe(card.verify_on_site_tr);
    expect(en.verifyOnSite).toBe(card.verify_on_site_en);
    expect(tr.facts.map((fact) => fact.key)).toEqual(card.facts.map((fact) => fact.key));
    expect(tr.facts[0]).toEqual({
      key: card.facts[0]?.key,
      label: card.facts[0]?.label_tr,
      value: card.facts[0]?.value_tr,
    });
    expect(en.facts[0]).toEqual({
      key: card.facts[0]?.key,
      label: card.facts[0]?.label_en,
      value: card.facts[0]?.value_en,
    });
  });

  it('keeps a card without a verify-on-site note without one', () => {
    expect(cardTexts(complianceCard('sources'), 'tr').verifyOnSite).toBeNull();
    expect(cardTexts(complianceCard('sources'), 'en').verifyOnSite).toBeNull();
  });
});

describe('cardLinks', () => {
  it('links every card to its page and an alert to Güvenlik for the administrator', () => {
    const links = COMPLIANCE_CARD_IDS.map((id) => cardLinks(complianceCard(id), ADMIN).page);
    expect(links).toEqual([
      '/sources',
      '/sealing',
      '/sealing',
      '/sealing',
      '/sealing',
      '/system',
      '/search',
      '/sources',
      '/sealing',
      '/settings',
    ]);
    // Card 1 has one alert (opened on the security page), card 2 none.
    expect(cardLinks(complianceCard('sources'), ADMIN).alerts).toBe('/security?alert=41');
    expect(cardLinks(complianceCard('seal_yesterday'), ADMIN).alerts).toBeNull();
  });

  it('links a card with several alerts to the list of Güvenlik', () => {
    const card = complianceCard('sources');
    const first = card.alerts[0];
    if (!first) throw new Error('card 1 of the fixtures has an alert');
    card.alerts = [first, { ...first, id: 42 }];
    expect(cardLinks(card, ADMIN).alerts).toBe('/security');
  });

  it('leaves the auditor the links to Mühürleme and Arama only (D15)', () => {
    const pages = COMPLIANCE_CARD_IDS.map((id) => cardLinks(complianceCard(id), AUDITOR).page);
    expect(pages).toEqual([
      null,
      '/sealing',
      '/sealing',
      '/sealing',
      '/sealing',
      null,
      '/search',
      null,
      '/sealing',
      null,
    ]);
    for (const id of COMPLIANCE_CARD_IDS)
      expect(cardLinks(complianceCard(id), AUDITOR).alerts, id).toBeNull();
  });
});

describe('administrator actions', () => {
  it('offers the backup confirmation on card 10 with settings.manage only', () => {
    expect(canConfirmBackup(complianceCard('backup'), ADMIN)).toBe(true);
    expect(canConfirmBackup(complianceCard('replica'), ADMIN)).toBe(false);
    expect(canConfirmBackup(complianceCard('backup'), AUDITOR)).toBe(false);
  });

  it('offers the wizard while a card is not ok, with settings.manage only', () => {
    expect(showWizardLink(complianceStatus(), ADMIN)).toBe(true);
    expect(showWizardLink(complianceStatus(), AUDITOR)).toBe(false);
    expect(showWizardLink(allOkCompliance(), ADMIN)).toBe(false);
    for (const tone of ['warning', 'error', 'neutral'] as const) {
      const status = allOkCompliance();
      const card = status.cards[6];
      if (!card) throw new Error('ten cards');
      card.tone = tone;
      expect(showWizardLink(status, ADMIN), tone).toBe(true);
    }
  });
});
