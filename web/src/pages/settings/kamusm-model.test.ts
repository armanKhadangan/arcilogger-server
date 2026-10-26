import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { SECRET_KEPT } from '@/components/admin/secret-edit';
import type { TsaSection } from '@/lib/settings-types';
import en from '@/locales/en.json';
import tr from '@/locales/tr.json';

import {
  SEAL_CAUSES,
  STEPS,
  causeKey,
  isStep,
  normalizeSha256,
  stepStatus,
  tsaBody,
  tsaForm,
  unplacedCauses,
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
      'kamusm.customer': { set: true, changed_at: null, changed_by: null },
      'kamusm.password': { set: true, changed_at: null, changed_by: null },
    },
    readiness: { production: true, missing: [] },
    seal_ready: { ok: true, causes: [] },
    ...overrides,
  };
}

const KEPT: Credentials = { customer: SECRET_KEPT, password: SECRET_KEPT, clear: false };

/** The keys of `CONFIG_CAUSES` in `core/src/arcilogger_core/seal.py` (the tests run from `web/`). */
function coreCauses(): string[] {
  const source = readFileSync(
    join(process.cwd(), '..', 'core', 'src', 'arcilogger_core', 'seal.py'),
    'utf8',
  );
  const block = /^CONFIG_CAUSES[^=]*= \{\n([\s\S]*?)^\}/m.exec(source)?.[1] ?? '';
  return [...block.matchAll(/^ {4}"([a-z-]+)": \(/gm)].map((match) => match[1] ?? '');
}

/** The text of a dotted key in a locale file, `undefined` when there is none. */
function text(locale: object, key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>(
      (node, part) =>
        node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
      locale,
    );
}

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
        'kamusm.customer': { set: false, changed_at: null, changed_by: null },
        'kamusm.password': { set: true, changed_at: null, changed_by: null },
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

  it('every sealing cause of the core has a Turkish and an English text', () => {
    const core = coreCauses();
    expect(core).toEqual(expect.arrayContaining(['zamane-jar', 'credentials', 'roots']));
    expect(SEAL_CAUSES).toEqual(expect.arrayContaining(core));
    for (const cause of SEAL_CAUSES) {
      expect(text(tr, causeKey(cause)), cause).toEqual(expect.any(String));
      expect(text(en, causeKey(cause)), cause).toEqual(expect.any(String));
    }
    // `location` is the refusal of a moved secret store or data folder (D0-2, WP2-1).
    for (const locale of [tr, en]) {
      expect(text(locale, causeKey('location'))).toMatch(/secrets_dir.*data_dir/);
      expect(text(locale, causeKey('location'))).toContain('arcilogger.toml');
    }
  });

  it('names the causes no step shows, so the steps cannot all read "Tamam" unexplained', () => {
    const stopped = tsa({
      seal_ready: { ok: false, causes: ['location', 'roots', 'secret-store', 'provider'] },
    });
    expect(unplacedCauses(stopped)).toEqual(['location', 'secret-store', 'provider']);
    expect(STEPS.some((step) => stepStatus(step, stopped, false) === 'error')).toBe(true);
    const moved = tsa({ seal_ready: { ok: false, causes: ['location'] } });
    expect(STEPS.map((step) => stepStatus(step, moved, false))).not.toContain('error');
    expect(unplacedCauses(moved)).toEqual(['location']);
    expect(unplacedCauses(tsa())).toEqual([]);
    expect(
      unplacedCauses(tsa({ seal_ready: { ok: false, causes: ['executable', 'zamane-jar'] } })),
    ).toEqual([]);
  });
});
