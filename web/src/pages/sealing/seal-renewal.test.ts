import en from '@/locales/areas/sealRenewal/en.json';
import tr from '@/locales/areas/sealRenewal/tr.json';
import type { SealDayDetail } from '@/lib/seal-types';
import { SEALS, need } from '@/test/samples';

import {
  noCreditQuery,
  RENEWAL_KEY,
  RENEWAL_STATUSES,
  RENEWAL_TONE,
  renewalDay,
  renewalOf,
  revocationOf,
  revocationTone,
} from './seal-renewal';

/*
 * Re-stamping and revocation on the day page (Wave 5 contract 2.3.1 "Day detail", 2.6), read
 * from the day detail until W5-C adds the fields to `SealDayDetail`; the texts of 2.6 verbatim.
 */

const SEALED = need(SEALS.days['2026-10-04'], 'sealed day sample');

function withFields(fields: Record<string, unknown>): SealDayDetail {
  return { ...SEALED, ...fields };
}

describe('the day detail’s re-stamping and revocation fields', () => {
  it('are absent from answers without them (older servers, the captured samples)', () => {
    expect(renewalOf(SEALED)).toBeNull();
    expect(revocationOf(SEALED)).toBeNull();
    expect(renewalOf(withFields({ renewal: null, revocation: null }))).toBeNull();
    expect(revocationOf(withFields({ renewal: null, revocation: null }))).toBeNull();
  });

  it('are read as the API sends them; a status this version does not know shows nothing', () => {
    const renewal = {
      status: 'covered',
      cert_not_after: '2031-10-01T00:00:00Z',
      covered_by: 'renewals/2031-05-04-1.json.zd',
    };
    const revocation = {
      code: 'crl-revoked',
      level: 'fail',
      message_tr: 'Sertifika iptal edilmiş.',
      message_en: 'The certificate is revoked.',
    };
    expect(renewalOf(withFields({ renewal, revocation }))).toEqual(renewal);
    expect(revocationOf(withFields({ renewal, revocation }))).toEqual(revocation);
    expect(renewalOf(withFields({ renewal: { ...renewal, status: 'renewed' } }))).toBeNull();
  });

  it('takes a renewal’s date from its file name (decision 1) and nothing else', () => {
    expect(renewalDay('renewals/2031-05-04-1.json.zd')).toBe('2031-05-04');
    expect(renewalDay('renewals/2031-05-04-12.json')).toBe('2031-05-04');
    expect(renewalDay('2031-05-04-2.json.zd')).toBe('2031-05-04');
    expect(renewalDay('renewals/latest.json.zd')).toBeNull();
    expect(renewalDay('renewals/2031-05-04.json.zd')).toBeNull();
    expect(renewalDay(null)).toBeNull();
  });

  it.each([
    ['fail', 'danger'],
    ['danger', 'danger'],
    ['warn', 'warning'],
    ['warning', 'warning'],
    ['info', 'info'],
    ['ok', 'info'],
    ['unknown', 'info'],
  ])('shows a revocation of level %s as %s', (level, tone) => {
    expect(revocationTone(level)).toBe(tone);
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

describe('a provider without a credit query (contract 2.4)', () => {
  it('is TÜRKTRUST, or any credit status the task recorded as not supported', () => {
    expect(noCreditQuery({ provider: 'turktrust' })).toBe(true);
    expect(noCreditQuery({ provider: 'zamane', supported: false } as { provider: string })).toBe(
      true,
    );
    expect(noCreditQuery({ provider: 'zamane' })).toBe(false);
    expect(noCreditQuery({ provider: 'mock' })).toBe(false);
  });
});
