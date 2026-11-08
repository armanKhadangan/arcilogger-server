import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { SECRET_KEPT } from '@/components/admin/secret-edit';
import { withAreas } from '@/lib/i18n';
import type { TsaSection } from '@/lib/settings-types';
import en from '@/locales/en.json';
import tr from '@/locales/tr.json';

import {
  CRL_URLS_MAX,
  PLAIN_HTTP_PHRASE,
  SEAL_CAUSES,
  SERVER_CHOICES,
  STEPS,
  TURKTRUST_KEPT,
  TURKTRUST_TEXTS,
  causeKey,
  choiceProvider,
  crlUrlList,
  currentChoice,
  evidenceChanges,
  evidenceKeys,
  firstOpen,
  isCrlUrl,
  isServerChoice,
  isStep,
  normalizeSha256,
  plainHttpConfirmed,
  providerText,
  serverChoices,
  stepStatus,
  toTurktrust,
  tsaBody,
  tsaForm,
  turktrustChanged,
  turktrustSupported,
  unplacedCauses,
  validateCustomer,
  validateEvidence,
  validateNumbers,
  validatePassword,
  validateTurktrustPassword,
  validateTurktrustUsername,
  type Credentials,
} from './kamusm-model';

const JAVA = 'C:/Program Files/Eclipse Adoptium/jre-8.0.432.6-hotspot/bin/java.exe';

/** A section as an API before W5-T reported it: without the §2.5 keys (asserted, W5-T). */
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
  } as TsaSection;
}

const KEPT: Credentials = { customer: SECRET_KEPT, password: SECRET_KEPT, clear: false };

/**
 * A site on TÜRKTRUST whose server is none of the choices (`server` null), with its two
 * credentials stored and Kamu SM's not.
 */
function turktrustSite(overrides: Partial<TsaSection> = {}): TsaSection {
  return tsa({
    provider: 'turktrust',
    server: null,
    java: null,
    zamane: { present: false, sha256: null, release: null, trusted: false, staged: [] },
    secrets: {
      'kamusm.customer': { set: false, changed_at: null, changed_by: null },
      'kamusm.password': { set: false, changed_at: null, changed_by: null },
      'turktrust.username': { set: true, changed_at: null, changed_by: 'web:tolga' },
      'turktrust.password': { set: true, changed_at: null, changed_by: 'web:tolga' },
    },
    // As the API reports it: `readiness` is Kamu SM production's, whose credentials are not stored.
    readiness: { production: false, missing: ['credentials', 'zamane_jar', 'java'] },
    ...overrides,
  });
}

/** A Kamu SM site on a server that knows TÜRKTRUST (W5-T): its credentials reported, not stored. */
function withTurktrust(overrides: Partial<TsaSection> = {}): TsaSection {
  const section = tsa(overrides);
  return {
    ...section,
    secrets: {
      ...section.secrets,
      'turktrust.username': { set: false, changed_at: null, changed_by: null },
      'turktrust.password': { set: false, changed_at: null, changed_by: null },
    },
  };
}

/** The texts of the app (the locale files with every area, `tsaProvider` among them). */
const LOCALES = { tr: withAreas(tr, 'tr'), en: withAreas(en, 'en') };

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
    // No server chosen: none is sent, the one in use stays.
    expect(
      tsaBody(section, 'tsa:1', { ...form, threshold: ' 300 ' }, KEPT, { probe: true }),
    ).toEqual({
      etag: 'tsa:1',
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

describe('Ayarlar → Zaman damgası: the server choice and TÜRKTRUST (Wave 5 contract 2.4)', () => {
  it('knows the four servers, their provider and the one in use', () => {
    expect(SERVER_CHOICES).toEqual(['test', 'production', 'turktrust', 'turktrust-http']);
    expect(SERVER_CHOICES.map(choiceProvider)).toEqual([
      'zamane',
      'zamane',
      'turktrust',
      'turktrust',
    ]);
    expect(isServerChoice('turktrust-http')).toBe(true);
    expect(isServerChoice('mock')).toBe(false);
    expect(isServerChoice(null)).toBe(false);
    expect(currentChoice(tsa())).toBe('test');
    expect(currentChoice(tsa({ server: 'production' }))).toBe('production');
    expect(currentChoice(tsa({ provider: 'mock', server: null }))).toBeNull();
    // A TÜRKTRUST server that is none of the choices cannot be told.
    expect(currentChoice(turktrustSite())).toBeNull();
    // A named server counts only with its own provider.
    expect(currentChoice(turktrustSite({ server: 'turktrust' }))).toBe('turktrust');
    expect(currentChoice(tsa({ server: 'turktrust-http' }))).toBeNull();
  });

  it('"Kaydet" on a TÜRKTRUST site keeps its server: nothing moves it to the Kamu SM test server', () => {
    const form = { java: null, threshold: '500', timeout: '60' };
    const body = tsaBody(turktrustSite(), 'tsa:1', form, KEPT, { probe: true });
    expect(body).not.toHaveProperty('server');
    expect(body).toEqual({
      etag: 'tsa:1',
      credit_alert_threshold: 500,
      timeout_seconds: 60,
      java: null,
      probe: true,
    });
    // A Zamane server at another address (set on the console) is kept as well.
    expect(tsaBody(tsa({ server: null }), 'tsa:1', form, KEPT, { probe: true })).not.toHaveProperty(
      'server',
    );
    // Only a chosen server moves it.
    expect(
      tsaBody(turktrustSite(), 'tsa:1', form, KEPT, { server: 'production', probe: true }).server,
    ).toBe('production');
    // The mock and another RFC 3161 server still go to the Kamu SM test server, as the page says.
    expect(
      tsaBody(tsa({ provider: 'rfc3161', server: null }), 'tsa:1', form, KEPT, { probe: true })
        .server,
    ).toBe('test');
  });

  it('offers TÜRKTRUST only where the server reports both its credentials, so an API before W5-T is never sent it', () => {
    // No `turktrust.*` in `secrets` (an API before W5-T): Kamu SM's two servers only.
    expect(turktrustSupported(tsa())).toBe(false);
    expect(serverChoices(tsa())).toEqual(['test', 'production']);
    expect(toTurktrust(tsa(), 'turktrust')).toBe(false);
    // Both reported (stored or not): all four.
    expect(turktrustSupported(withTurktrust())).toBe(true);
    expect(serverChoices(withTurktrust())).toEqual(SERVER_CHOICES);
    expect(serverChoices(turktrustSite())).toEqual(SERVER_CHOICES);
    // Only one of the two reported: the gate stays closed.
    const oneOnly = { ...withTurktrust().secrets };
    delete oneOnly['turktrust.password'];
    expect(turktrustSupported(tsa({ secrets: oneOnly }))).toBe(false);
  });

  it("pins the TÜRKTRUST gate on W5-T's captured sample: both credentials reported, all four servers", () => {
    const captured = JSON.parse(
      readFileSync(join(process.cwd(), 'e2e', 'samples', 'admin.json'), 'utf8'),
    ) as { config: { sections: { tsa: TsaSection } } };
    const section = captured.config.sections.tsa;
    expect(Object.keys(section.secrets)).toEqual(
      expect.arrayContaining(['turktrust.username', 'turktrust.password']),
    );
    expect(turktrustSupported(section)).toBe(true);
    expect(serverChoices(section)).toEqual(SERVER_CHOICES);
    // The §2.5 keys are reported too, so the form shows and sends them.
    expect(evidenceKeys(section)).toEqual({
      renew_before_days: section.renew_before_days,
      crl_urls: section.crl_urls,
      crl_max_age_days: section.crl_max_age_days,
    });
  });

  it('sends TÜRKTRUST credentials only with a change that goes to TÜRKTRUST', () => {
    const form = { java: JAVA, threshold: '500', timeout: '60' };
    const typed: Credentials = {
      ...KEPT,
      turktrust: {
        username: { value: 'ornek-as', editing: true, clear: false },
        password: { value: 'deneme-parola', editing: true, clear: false }, // gitleaks:allow — fake test value
      },
    };
    const site = withTurktrust();
    // Typed for TÜRKTRUST, then Kamu SM production chosen, or "Kaydet" on the Kamu SM site.
    expect(toTurktrust(site, 'production')).toBe(false);
    expect(toTurktrust(site, undefined)).toBe(false);
    for (const server of ['production', undefined] as const) {
      const body = tsaBody(site, 'tsa:1', form, typed, { server, probe: true });
      expect(body).not.toHaveProperty('turktrust_username');
      expect(body).not.toHaveProperty('turktrust_password');
      expect(body).not.toHaveProperty('allow_plain_http');
    }
    // "Kaydet" with TÜRKTRUST in use and no other server chosen: they go.
    expect(toTurktrust(turktrustSite(), undefined)).toBe(true);
    expect(tsaBody(turktrustSite(), 'tsa:1', form, typed, { probe: true })).toMatchObject({
      turktrust_username: 'ornek-as',
      turktrust_password: 'deneme-parola',
    });
    // "Kaydet" while TÜRKTRUST is chosen in step 6, before the switch: they go, with no server.
    const stored = tsaBody(site, 'tsa:1', form, typed, { chosen: 'turktrust', probe: true });
    expect(stored).toMatchObject({
      turktrust_username: 'ornek-as',
      turktrust_password: 'deneme-parola',
    });
    expect(stored).not.toHaveProperty('server');
    expect(stored).not.toHaveProperty('allow_plain_http');
    // A TÜRKTRUST site with Kamu SM chosen: not theirs.
    expect(
      tsaBody(turktrustSite(), 'tsa:1', form, typed, { chosen: 'production', probe: true }),
    ).not.toHaveProperty('turktrust_username');
    // A server without TÜRKTRUST never gets them, whatever is chosen.
    expect(
      tsaBody(tsa(), 'tsa:1', form, typed, { server: 'turktrust', probe: true }),
    ).not.toHaveProperty('turktrust_username');
  });

  it('"Kaydet" on a plain-HTTP TÜRKTRUST site sends no server, so no unconfirmed turktrust-http', () => {
    const form = { java: null, threshold: '400', timeout: '60' };
    const site = turktrustSite({ server: 'turktrust-http' as never });
    expect(currentChoice(site)).toBe('turktrust-http');
    const body = tsaBody(site, 'tsa:1', form, KEPT, { probe: true });
    expect(body).not.toHaveProperty('server');
    expect(body).not.toHaveProperty('allow_plain_http');
    expect(body).not.toHaveProperty('confirm');
    // Kamu SM production in use: "Kaydet" does not send it back either.
    expect(
      tsaBody(tsa({ server: 'production' }), 'tsa:1', form, KEPT, { probe: true }),
    ).not.toHaveProperty('server');
  });

  it('a switch to TÜRKTRUST carries the typed credentials and whether plain HTTP is allowed', () => {
    const section = withTurktrust();
    const form = tsaForm(section);
    if (!form) throw new Error('no form');
    const typed: Credentials = {
      ...KEPT,
      turktrust: {
        username: { value: 'ornek-as', editing: true, clear: false },
        password: { value: 'deneme-parola', editing: true, clear: false }, // gitleaks:allow — fake test value
      },
    };
    expect(tsaBody(section, 'tsa:1', form, typed, { server: 'turktrust', probe: true })).toEqual({
      etag: 'tsa:1',
      server: 'turktrust',
      credit_alert_threshold: 500,
      timeout_seconds: 60,
      java: JAVA,
      turktrust_username: 'ornek-as',
      turktrust_password: 'deneme-parola',
      allow_plain_http: false,
      probe: true,
    });
    expect(
      tsaBody(section, 'tsa:1', form, typed, {
        server: 'turktrust-http',
        confirm: true,
        probe: true,
      }),
    ).toMatchObject({ server: 'turktrust-http', allow_plain_http: true, confirm: true });
    // A switch between Kamu SM's servers says nothing of plain HTTP.
    expect(
      tsaBody(section, 'tsa:1', form, KEPT, { server: 'production', probe: true }),
    ).not.toHaveProperty('allow_plain_http');
    // An open field left empty keeps the stored value; nothing typed sends nothing.
    const opened: Credentials = {
      ...KEPT,
      turktrust: { ...TURKTRUST_KEPT, password: { value: '', editing: true, clear: false } },
    };
    const kept = tsaBody(turktrustSite(), 'tsa:1', form, opened, { probe: true });
    expect(kept).not.toHaveProperty('turktrust_username');
    expect(kept).not.toHaveProperty('turktrust_password');
    expect(turktrustChanged(opened)).toBe(true);
    expect(turktrustChanged({ ...KEPT, turktrust: TURKTRUST_KEPT })).toBe(false);
    expect(turktrustChanged(KEPT)).toBe(false);
  });

  it('checks the TÜRKTRUST username (128) and password (256) as the API does', () => {
    expect(validateTurktrustUsername('')).toBe('validation.required');
    expect(validateTurktrustUsername('ornek as')).toBeUndefined();
    expect(validateTurktrustUsername('ş'.repeat(128))).toBeUndefined();
    expect(validateTurktrustUsername('ş'.repeat(129))).toBe(
      'settings.kamusm.errors.turktrustUsername',
    );
    expect(validateTurktrustUsername('a\u0000b')).toBe('settings.kamusm.errors.turktrustUsername');
    expect(validateTurktrustPassword('')).toBe('validation.required');
    expect(validateTurktrustPassword('ğ'.repeat(256))).toBeUndefined();
    expect(validateTurktrustPassword('ğ'.repeat(257))).toBe(
      'settings.kamusm.errors.turktrustPassword',
    );
    expect(validateTurktrustPassword('a\u200Bb')).toBe('settings.kamusm.errors.turktrustPassword');
  });

  it('Java and the Zamane client are not needed with TÜRKTRUST; its credentials are', () => {
    const site = turktrustSite();
    expect(STEPS.map((step) => stepStatus(step, site, false))).toEqual([
      'unused',
      'unused',
      'ok',
      'ok',
      'optional',
      'ok',
      'ok',
    ]);
    // The page opens the first step in use, not "Java".
    expect(firstOpen(site, false)).toBe('roots');
    // Its credentials are done with TÜRKTRUST's two stored, although the API's `readiness` (Kamu
    // SM production's) lists `credentials`: 0.3.1 showed "Eksik" here after a correct entry.
    expect(site.readiness.missing).toContain('credentials');
    expect(stepStatus('credentials', site, false)).toBe('ok');
    const unset = turktrustSite({
      secrets: {
        'kamusm.customer': { set: true, changed_at: null, changed_by: null },
        'kamusm.password': { set: true, changed_at: null, changed_by: null },
        'turktrust.username': { set: true, changed_at: null, changed_by: null },
      },
    });
    expect(stepStatus('credentials', unset, false)).toBe('missing');
    expect(firstOpen(unset, false)).toBe('credentials');
    expect(
      stepStatus(
        'credentials',
        turktrustSite({ seal_ready: { ok: false, causes: ['credentials'] } }),
        false,
      ),
    ).toBe('error');
    // Kamu SM's steps are unchanged: a mock site still starts at Java.
    expect(firstOpen(tsa({ provider: 'mock', server: null, java: null }), false)).toBe('java');
    expect(firstOpen(tsa(), false)).toBe('java');
  });

  it('names TÜRKTRUST where the page names the provider, in Turkish and English', () => {
    for (const key of TURKTRUST_TEXTS) {
      expect(providerText('zamane', key)).toBe(`settings.kamusm.${key}`);
      expect(providerText('mock', key)).toBe(`settings.kamusm.${key}`);
      expect(providerText('turktrust', key)).toBe(`settings.kamusm.turktrust.${key}`);
      for (const [lang, locale] of Object.entries(LOCALES)) {
        const kamusm = text(locale, providerText('zamane', key));
        const turktrust = text(locale, providerText('turktrust', key));
        expect(kamusm, `${lang} ${key}`).toEqual(expect.any(String));
        expect(turktrust, `${lang} ${key}`).toEqual(expect.any(String));
        expect(turktrust, `${lang} ${key}`).not.toEqual(kamusm);
        // Only the steps of Kamu SM's own client still name Kamu SM.
        if (!key.startsWith('steps.java') && !key.startsWith('steps.zamane'))
          expect(turktrust, `${lang} ${key}`).not.toContain('Kamu SM');
      }
    }
    expect(text(LOCALES.tr, providerText('turktrust', 'roots.upload'))).toBe(
      'TÜRKTRUST kök sertifikası dosyası',
    );
    expect(causeKey('credentials', 'turktrust')).toBe(
      'settings.kamusm.turktrust.causes.credentials',
    );
    expect(causeKey('credentials', 'zamane')).toBe('settings.kamusm.causes.credentials');
    expect(causeKey('roots', 'turktrust')).toBe('settings.kamusm.causes.roots');
    for (const locale of Object.values(LOCALES)) {
      expect(text(locale, causeKey('credentials', 'turktrust'))).toMatch(/TÜRKTRUST/);
      expect(text(locale, 'settings.kamusm.status.unused')).toEqual(expect.any(String));
      // `TsaRefusal` reads the missing items of `tsa_not_ready` as nested keys.
      expect(text(locale, 'settings.kamusm.production.items.turktrust.username')).toMatch(
        /TÜRKTRUST/,
      );
      expect(text(locale, 'settings.kamusm.production.items.turktrust.password')).toMatch(
        /TÜRKTRUST/,
      );
    }
  });

  it('the page is "Zaman damgası" in Ayarlar; its title names the provider in use', () => {
    expect(text(LOCALES.tr, 'settings.sections.kamusm')).toBe('Zaman damgası');
    expect(text(LOCALES.en, 'settings.sections.kamusm')).toBe('Time stamping');
    expect(text(LOCALES.tr, 'settings.kamusm.title')).toBe('Zaman damgası');
    expect(text(LOCALES.tr, 'settings.kamusm.titles.zamane')).toBe('Kamu SM zaman damgası');
    expect(text(LOCALES.tr, 'settings.kamusm.titles.turktrust')).toBe('TÜRKTRUST zaman damgası');
    // The "sahada doğrula" note names T1 (the path), T2 (an empty balance) and T5 (port 80)
    // without a URL: host names only.
    for (const locale of Object.values(LOCALES)) {
      const note = String(text(locale, 'settings.kamusm.turktrust.note.body'));
      expect(note).toContain('zd.turktrust.com.tr');
      expect(note).toMatch(/\b80\b/);
      expect(note).toMatch(/\b443\b/);
      expect(note).not.toMatch(/https?:\/\//);
      expect(String(text(locale, 'settings.kamusm.turktrust.note.title'))).toMatch(
        /[Ss]ahada doğrula/,
      );
    }
  });
});

describe('Ayarlar → Zaman damgası: the plain-HTTP confirmation (Wave 5 contract C5)', () => {
  it('is exactly HTTP, in both languages; spaces around it are ignored, its case is not', () => {
    expect(PLAIN_HTTP_PHRASE).toBe('HTTP');
    expect(plainHttpConfirmed('HTTP')).toBe(true);
    expect(plainHttpConfirmed('  HTTP ')).toBe(true);
    for (const typed of ['', 'http', 'Http', 'HTTPS', 'DÜZ HTTP', 'PLAIN HTTP', 'H TTP'])
      expect(plainHttpConfirmed(typed)).toBe(false);
    for (const locale of Object.values(LOCALES)) {
      expect(text(locale, 'settings.kamusm.production.httpType')).toMatch(/\{\{phrase\}\}/);
      // The phrase is no text of its own: a translation cannot change what must be typed.
      expect(text(locale, 'settings.kamusm.production.httpPhrase')).toBeUndefined();
    }
  });
});

describe('Ayarlar → Zaman damgası: re-stamping and revocation lists (Wave 5 contract 2.5)', () => {
  // Built, not written out: the lint rule keeps address literals out of the source.
  const http = (rest: string) => `http://${rest}`;
  const https = (rest: string) => `https://${rest}`;
  const CRL_A = http('192.0.2.10/kok.crl');
  const CRL_B = https('crl.ornek.example/ara.crl');

  /** A section as W5-T's API reports it, with the keys of contract 2.5. */
  function w5t(overrides: Record<string, unknown> = {}): TsaSection {
    return {
      ...tsa(),
      renew_before_days: 180,
      crl_urls: [CRL_A],
      crl_max_age_days: 7,
      ...overrides,
    };
  }

  it('reads the keys only when the server reports them all', () => {
    expect(evidenceKeys(tsa())).toBeUndefined();
    expect(tsaForm(tsa())).not.toHaveProperty('evidence');
    expect(evidenceKeys(w5t({ crl_urls: undefined }))).toBeUndefined();
    expect(evidenceKeys(w5t({ renew_before_days: '180' }))).toBeUndefined();
    expect(evidenceKeys(w5t())).toEqual({
      renew_before_days: 180,
      crl_urls: [CRL_A],
      crl_max_age_days: 7,
    });
    expect(tsaForm(w5t({ crl_urls: [CRL_A, CRL_B] }))?.evidence).toEqual({
      renewBeforeDays: '180',
      crlUrls: `${CRL_A}\n${CRL_B}`,
      crlMaxAgeDays: '7',
    });
  });

  it('checks the bounds: window 30–730, age 1–60, at most 20 http(s) addresses', () => {
    const ok = { renewBeforeDays: '180', crlUrls: '', crlMaxAgeDays: '7' };
    expect(validateEvidence(ok)).toEqual({});
    expect(validateEvidence({ ...ok, renewBeforeDays: '30', crlMaxAgeDays: '60' })).toEqual({});
    expect(validateEvidence({ ...ok, renewBeforeDays: '730', crlMaxAgeDays: '1' })).toEqual({});
    expect(validateEvidence({ ...ok, renewBeforeDays: '29', crlMaxAgeDays: '61' })).toEqual({
      renewBeforeDays: 'admin.errors.min',
      crlMaxAgeDays: 'admin.errors.max',
    });
    expect(validateEvidence({ ...ok, renewBeforeDays: '731', crlMaxAgeDays: '0' })).toEqual({
      renewBeforeDays: 'admin.errors.max',
      crlMaxAgeDays: 'admin.errors.min',
    });
    expect(validateEvidence({ ...ok, renewBeforeDays: '', crlMaxAgeDays: '7,5' })).toEqual({
      renewBeforeDays: 'admin.errors.integer',
      crlMaxAgeDays: 'admin.errors.integer',
    });
    const twenty = Array.from({ length: CRL_URLS_MAX }, (_, index) =>
      http(`192.0.2.${String(index + 1)}/kok.crl`),
    );
    expect(CRL_URLS_MAX).toBe(20);
    // One per line; blank lines and spaces around an address do not count.
    expect(validateEvidence({ ...ok, crlUrls: `\n ${twenty.join('\r\n')} \n\n` })).toEqual({});
    expect(
      validateEvidence({ ...ok, crlUrls: [...twenty, http('192.0.2.99/kok.crl')].join('\n') }),
    ).toEqual({ crlUrls: 'settings.kamusm.errors.crlUrlsCount' });
    expect(validateEvidence({ ...ok, crlUrls: `${CRL_A}\nftp://192.0.2.10/kok.crl` })).toEqual({
      crlUrls: 'settings.kamusm.errors.crlUrl',
    });
  });

  it('an address is http or https with a host, without user information or white space', () => {
    expect(isCrlUrl(CRL_A)).toBe(true);
    expect(isCrlUrl(CRL_B)).toBe(true);
    expect(isCrlUrl(http('198.51.100.7:8080/crl/kok.crl?v=1'))).toBe(true);
    expect(isCrlUrl('HTTP://203.0.113.5/kok.crl')).toBe(true);
    for (const bad of [
      'ftp://192.0.2.10/kok.crl',
      'file:///C:/kok.crl',
      '192.0.2.10/kok.crl',
      'http:192.0.2.10/kok.crl',
      http(''),
      http('/kok.crl'),
      // User information is refused (contract C10), with or without a password.
      http('ornek@192.0.2.10/kok.crl'),
      http('ornek:deneme@192.0.2.10/kok.crl'),
      http('@192.0.2.10/kok.crl'),
      http('192.0.2.10/k ok.crl'),
      http('192.0.2.10/kok.crl\u0007'),
    ])
      expect(isCrlUrl(bad)).toBe(false);
    expect(crlUrlList(` ${CRL_A} \r\n\n${CRL_B}\n`)).toEqual([CRL_A, CRL_B]);
  });

  it('a change sends only the keys that differ from the server, so an API before W5-T accepts it', () => {
    const section = w5t();
    const form = tsaForm(section);
    if (!form?.evidence) throw new Error('no form');
    // Untouched (or only reformatted) keys send nothing.
    expect(evidenceChanges(section, form)).toEqual({});
    expect(
      evidenceChanges(section, {
        ...form,
        evidence: { renewBeforeDays: ' 180 ', crlUrls: `\n  ${CRL_A}  \n`, crlMaxAgeDays: '7' },
      }),
    ).toEqual({});
    expect(tsaBody(section, 'tsa:1', form, KEPT, { probe: true })).toEqual({
      etag: 'tsa:1',
      credit_alert_threshold: 500,
      timeout_seconds: 60,
      java: JAVA,
      probe: true,
    });
    // A server without the keys: the form has none and sends none.
    expect(tsaBody(tsa(), 'tsa:1', form, KEPT, { probe: true })).not.toHaveProperty(
      'renew_before_days',
    );
    expect(
      tsaBody(
        section,
        'tsa:1',
        {
          ...form,
          evidence: { renewBeforeDays: '365', crlUrls: `${CRL_B}\n${CRL_A}`, crlMaxAgeDays: '7' },
        },
        KEPT,
        { probe: true },
      ),
    ).toEqual({
      etag: 'tsa:1',
      credit_alert_threshold: 500,
      timeout_seconds: 60,
      java: JAVA,
      renew_before_days: 365,
      crl_urls: [CRL_B, CRL_A],
      probe: true,
    });
    // Emptied: an empty list goes back to the certificates' distribution points.
    expect(
      evidenceChanges(section, {
        ...form,
        evidence: { ...form.evidence, crlUrls: ' \n', crlMaxAgeDays: '14' },
      }),
    ).toEqual({ crl_urls: [], crl_max_age_days: 14 });
  });

  it('the texts: TR and EN, the bounds as placeholders, "sahada doğrula" at the addresses', () => {
    for (const locale of Object.values(LOCALES)) {
      expect(text(locale, 'settings.kamusm.evidence.renewBeforeDaysHint')).toMatch(
        /\{\{min\}\}–\{\{max\}\}.*180/,
      );
      expect(text(locale, 'settings.kamusm.evidence.crlMaxAgeDaysHint')).toMatch(
        /\{\{min\}\}–\{\{max\}\}.*\b7\b/,
      );
      // C22: fresh by the last fetch and the list's own nextUpdate, not by its thisUpdate.
      expect(text(locale, 'settings.kamusm.evidence.crlMaxAgeDaysHint')).toContain('nextUpdate');
      expect(text(locale, 'settings.kamusm.evidence.crlUrlsHint')).toMatch(/\{\{max\}\}/);
      expect(text(locale, 'settings.kamusm.errors.crlUrlsCount')).toMatch(/\{\{max\}\}/);
      // No provider address is written into a text either (K10, T4).
      expect(text(locale, 'settings.kamusm.evidence.crlNote')).not.toMatch(/https?:\/\/|\.crl/);
    }
    expect(text(LOCALES.tr, 'settings.kamusm.evidence.crlNoteTitle')).toBe('Sahada doğrula');
    expect(text(LOCALES.en, 'settings.kamusm.evidence.crlNoteTitle')).toBe('Verify on site');
    expect(text(LOCALES.tr, 'admin.paths.tsa.renew_before_days')).toBe(
      'Yeniden damgalama süresi (gün)',
    );
  });
});
