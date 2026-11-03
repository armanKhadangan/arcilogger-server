import { LEGAL_REASONS } from '@/test/w5-fixtures';

import { docsReference, reasonText, reasonTone } from './reason-text';
import type { LegalReasonCode } from '@/lib/legal-status-types';

/*
 * The text of a reason (contract 2.6): the API's sentence in the language, ending with the
 * documentation section of ZAMAN-DAMGASI.md / TIME-STAMPING.md (sections 5.2–5.9).
 */

const SECTIONS: Record<LegalReasonCode, string> = {
  provider_not_legal: '5.2',
  credentials_in_clear: '5.3',
  legal_root_missing: '5.4',
  unstamped_days: '5.5',
  renewal_due: '5.6',
  renewal_uncovered: '5.7',
  certificate_revoked: '5.8',
  crl_stale: '5.9',
};

describe('legal-status reason text', () => {
  it.each(Object.entries(SECTIONS))('%s refers to section %s in both languages', (code, n) => {
    expect(docsReference(code, 'tr')).toBe(`ZAMAN-DAMGASI.md bölüm ${n}`);
    expect(docsReference(code, 'en')).toBe(`TIME-STAMPING.md section ${n}`);
  });

  it('is the API’s sentence followed by the reference', () => {
    const reason = LEGAL_REASONS.provider_not_legal;
    expect(reasonText(reason, 'tr')).toBe(`${reason.message_tr} (ZAMAN-DAMGASI.md bölüm 5.2)`);
    expect(reasonText(reason, 'en')).toBe(`${reason.message_en} (TIME-STAMPING.md section 5.2)`);
    expect(reasonText(LEGAL_REASONS.unstamped_days, 'tr')).toBe(
      '1 kapanmış gün 24 saatten uzun süredir damgalanmadı. (ZAMAN-DAMGASI.md bölüm 5.5)',
    );
  });

  it('gives a code this version does not know its sentence alone', () => {
    const reason = {
      code: 'tsa_unreachable' as LegalReasonCode,
      message_tr: 'Metin.',
      message_en: 'Text.',
    };
    expect(docsReference(reason.code, 'tr')).toBeNull();
    expect(reasonText(reason, 'tr')).toBe('Metin.');
    expect(reasonText(reason, 'en')).toBe('Text.');
  });

  it('is red for danger and amber for warning', () => {
    expect(reasonTone('danger')).toBe('danger');
    expect(reasonTone('warning')).toBe('warning');
  });
});
