import en from '@/locales/areas/sealRenewal/en.json';
import tr from '@/locales/areas/sealRenewal/tr.json';
import { SEALS } from '@/test/samples';

import { RENEWAL_KEY, RENEWAL_STATUSES, RENEWAL_TONE, revocationTone } from './seal-renewal';

/*
 * Re-stamping and revocation on the day page (Wave 5 contract 2.3.1 "Day detail", 2.6, C4): the
 * tones of W5-C's `renewal` and `revocation` (`lib/seal-types.ts`) and the texts of 2.6 verbatim.
 */

describe('the day detail’s re-stamping and revocation fields', () => {
  it('shows the captured days’ finding as neutral information', () => {
    // The mock provider's sealed days: no stored list covers its certificate (`crl-none`, info).
    const found = Object.values(SEALS.days).flatMap((day) =>
      day.revocation ? [day.revocation] : [],
    );
    expect(found.length).toBeGreaterThan(0);
    for (const revocation of found) {
      expect(revocation).toMatchObject({ code: 'crl-none', level: 'info' });
      expect(revocationTone(revocation.level)).toBe('info');
    }
  });

  it.each([
    ['fail', 'danger'],
    ['warn', 'warning'],
    ['info', 'info'],
    ['ok', 'success'],
    ['unknown', 'info'],
  ])('shows a revocation of level %s as %s (C4)', (level, tone) => {
    expect(revocationTone(level)).toBe(tone);
  });

  it('does not take the legal status’ words for a revocation level', () => {
    expect(revocationTone('danger')).toBe('info');
    expect(revocationTone('warning')).toBe('info');
  });

  it('marks due, late and uncovered as warnings (decision 6), covered as fine', () => {
    expect(RENEWAL_TONE).toEqual({
      not_due: 'info',
      due: 'warning',
      covered: 'success',
      late: 'warning',
      uncovered: 'warning',
    });
  });
});

describe('the re-stamping texts (contract 2.6)', () => {
  it('have a label and a sentence for every status in both languages', () => {
    for (const status of RENEWAL_STATUSES) {
      const key = RENEWAL_KEY[status] as keyof typeof tr.sealing.renewal.text;
      for (const texts of [tr, en]) {
        expect(texts.sealing.renewal.status).toHaveProperty(key);
        expect(texts.sealing.renewal.text[key]).toBeTruthy();
      }
    }
  });

  it('are worded as 2.6, with i18next placeholders', () => {
    expect(tr.sealing.renewal.text).toMatchObject({
      covered: 'Bu damga, sertifikası sona ermeden yeniden damgalanmış: {{renewal}}.',
      due: 'Damga sertifikası {{date}} tarihinde sona eriyor; yeniden damgalama bekleniyor.',
      late: 'Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({{date}}).',
      uncovered:
        'Damga sertifikasının süresi {{date}} tarihinde doldu ve yeniden damgalama yok; damga, alındığı an için geçerlidir.',
      notDue: 'Yeniden damgalama henüz gerekmiyor (sertifika {{date}} tarihine kadar geçerli).',
    });
    expect(en.sealing.renewal.text).toMatchObject({
      covered: 'Re-stamped before its certificate expired: {{renewal}}.',
      due: 'The stamp certificate expires on {{date}}; re-stamping is due.',
      late: 'Re-stamped after the old certificate had expired ({{date}}).',
      uncovered:
        'The stamp certificate expired on {{date}} with no re-stamping; the stamp is valid for the moment it was made.',
      notDue: 'Re-stamping is not due yet (certificate valid until {{date}}).',
    });
    expect(tr.sealing.credits.noQuery).toBe(
      'Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin.',
    );
    expect(en.sealing.credits.noQuery).toBe(
      "This provider offers no credit query; watch the balance in the provider's portal.",
    );
  });

  it('never call a TÜRKTRUST stamp legal in its badge, nor a claimed one a test', () => {
    const legal = `${tr.sealing.turktrust.legal} ${en.sealing.turktrust.legal}`;
    expect(legal).not.toMatch(/yasal|legal/i);
    const claimed = `${tr.sealing.turktrust.claimed} ${en.sealing.turktrust.claimed}`;
    expect(claimed).not.toMatch(/yasal|legal|test|mock/i);
    for (const texts of [tr, en]) {
      expect(texts.sealing.turktrust.rootUnconfirmed).toContain('{{provider}}');
      expect(texts.sealing.turktrust.rootUnconfirmed).toContain('tsa.legal_root_fingerprints');
      expect(texts.sealing.turktrust.rootUnconfirmed).not.toMatch(/test|mock|Kamu SM/i);
    }
  });
});

describe('a provider without a credit query (contract 2.4, C3, C21)', () => {
  it('has a plain title, without a provider name', () => {
    expect(tr.sealing.credits.titleNoQuery).toBe('Kontör');
    expect(en.sealing.credits.titleNoQuery).toBe('Credit');
  });
});
