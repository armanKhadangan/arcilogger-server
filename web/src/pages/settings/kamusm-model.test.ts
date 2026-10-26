import { SECRET_KEPT } from '@/components/admin/secret-edit';
import type { TsaSection } from '@/lib/settings-types';

import {
  STEPS,
  causeKey,
  isStep,
  normalizeSha256,
  stepStatus,
  tsaBody,
  tsaForm,
  validateCustomer,
  validateNumbers,
  validatePassword,
  type Credentials,
} from './kamusm-model';

const JAVA = 'C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe';

function tsa(overrides: Partial<TsaSection> = {}): TsaSection {
  return {
    provider: 'zamane',
    server: 'test',
    credit_alert_threshold: 500,
    timeout_seconds: 60,
    java: JAVA,
    java_choices: [JAVA],
    zamane: {
      present: true,
      sha256: 'a'.repeat(64),
      release: null,
      trusted: 'installer',
      staged: [],
    },
    roots: [
      {
        sha256: 'b'.repeat(64),
        subject: 'CN=Kamu SM Test Kök Sertifikası (örnek)',
        not_after: '2036-01-01T00:00:00+03:00',
        legal: false,
        source: 'installer',
      },
    ],
    secrets: {
      'kamusm.customer': { set: true, changed_at: null },
      'kamusm.password': { set: true, changed_at: null },
    },
    readiness: { production: true, missing: [] },
    seal_ready: { ok: true, causes: [] },
    ...overrides,
  };
}

const KEPT: Credentials = { customer: SECRET_KEPT, password: SECRET_KEPT, clear: false };

describe('the Kamu SM wizard (contract 2.17)', () => {
  it('knows its seven steps', () => {
    expect(STEPS).toEqual([
      'java',
      'zamane',
      'roots',
      'credentials',
      'test',
      'production',
      'credit',
    ]);
    expect(isStep('roots')).toBe(true);
    expect(isStep('seal')).toBe(false);
    expect(isStep(null)).toBe(false);
  });

  it('every step is ok on a complete test-server setup; test and production are optional', () => {
    const complete = tsa();
    expect(STEPS.map((step) => stepStatus(step, complete, false))).toEqual([
      'ok',
      'ok',
      'ok',
      'ok',
      'optional',
      'optional',
      'ok',
    ]);
    expect(stepStatus('test', complete, true)).toBe('ok');
    expect(stepStatus('production', tsa({ server: 'production' }), false)).toBe('ok');
  });

  it('a missing item is "missing"; a cause that stops sealing now is "error"', () => {
    const fresh = tsa({
      provider: 'mock',
      server: null,
      java: null,
      zamane: { present: false, sha256: null, release: null, trusted: false, staged: [] },
      roots: [],
      secrets: {
        'kamusm.customer': { set: false, changed_at: null },
        'kamusm.password': { set: true, changed_at: null },
      },
      readiness: { production: false, missing: ['credentials', 'zamane_jar', 'java', 'roots'] },
    });
    expect(
      ['java', 'zamane', 'roots', 'credentials'].map((step) =>
        stepStatus(step as never, fresh, false),
      ),
    ).toEqual(['missing', 'missing', 'missing', 'missing']);
    const broken = tsa({
      seal_ready: { ok: false, causes: ['zamane-jar', 'credentials', 'roots', 'executable'] },
    });
    expect(stepStatus('java', broken, false)).toBe('error');
    expect(stepStatus('zamane', broken, false)).toBe('error');
    expect(stepStatus('roots', broken, false)).toBe('error');
    expect(stepStatus('credentials', broken, false)).toBe('error');
    // A client that may not run is an error; one waiting for its approval is "pending".
    expect(stepStatus('zamane', tsa({ zamane: { ...tsa().zamane, trusted: false } }), false)).toBe(
      'error',
    );
    expect(
      stepStatus(
        'zamane',
        tsa({
          zamane: {
            ...tsa().zamane,
            staged: [{ sha256: 'c'.repeat(64), effective_at: '2026-10-27T10:00:00+03:00' }],
          },
        }),
        false,
      ),
    ).toBe('pending');
  });

  it('the body: numbers as typed, credentials only when a new value was typed', () => {
    const section = tsa();
    const form = tsaForm(section);
    expect(form).toEqual({ java: JAVA, threshold: '500', timeout: '60' });
    if (!form) return;
    expect(
      tsaBody(section, 'tsa:1', { ...form, threshold: ' 300 ' }, KEPT, { probe: true }),
    ).toEqual({
      etag: 'tsa:1',
      server: 'test',
      credit_alert_threshold: 300,
      timeout_seconds: 60,
      java: JAVA,
      probe: true,
    });
    const typed: Credentials = {
      customer: { value: '100200300', editing: true, clear: false },
      // "Değiştir" pressed, nothing typed: the stored password stays.
      password: { value: '', editing: true, clear: false },
      clear: false,
    };
    const body = tsaBody(section, 'tsa:1', form, typed, { probe: false });
    expect(body).toMatchObject({ customer: '100200300', probe: false });
    expect(body).not.toHaveProperty('password');
    expect(
      tsaBody(section, 'tsa:1', form, { ...KEPT, clear: true }, { probe: true }),
    ).toMatchObject({ clear_credentials: true });
    expect(
      tsaBody(tsa({ server: 'production' }), 'tsa:1', form, KEPT, {
        server: 'test',
        confirm: true,
        probe: true,
      }),
    ).toMatchObject({ server: 'test', confirm: true });
    // The first setup from another provider goes to the Zamane test server.
    expect(
      tsaBody(tsa({ provider: 'mock', server: null }), 'tsa:1', form, KEPT, { probe: true }).server,
    ).toBe('test');
  });

  it('checks the credentials and the numbers as the API does', () => {
    expect(validateCustomer('')).toBe('validation.required');
    expect(validateCustomer('12 34')).toBe('settings.kamusm.errors.customer');
    expect(validateCustomer('x'.repeat(65))).toBe('settings.kamusm.errors.customer');
    expect(validateCustomer('100200300')).toBeUndefined();
    expect(validatePassword('')).toBe('validation.required');
    expect(validatePassword('a\u0007b')).toBe('settings.kamusm.errors.password');
    expect(validatePassword('ç'.repeat(128))).toBeUndefined();
    expect(validatePassword('ç'.repeat(129))).toBe('settings.kamusm.errors.password');
    expect(validateNumbers({ java: null, threshold: '-1', timeout: '601' })).toEqual({
      threshold: 'admin.errors.min',
      timeout: 'admin.errors.max',
    });
    expect(validateNumbers({ java: null, threshold: '0', timeout: '5' })).toEqual({});
  });

  it('reads a published SHA-256 with spaces and capitals; names the sealing causes', () => {
    const digest = 'ab'.repeat(32);
    expect(normalizeSha256(`${digest.toUpperCase().slice(0, 32)} ${digest.slice(32)}`)).toBe(
      digest,
    );
    expect(normalizeSha256('abc')).toBeNull();
    expect(causeKey('zamane-jar')).toBe('settings.kamusm.causes.zamane_jar');
    expect(causeKey('secret-store')).toBe('settings.kamusm.causes.secret_store');
  });
});
