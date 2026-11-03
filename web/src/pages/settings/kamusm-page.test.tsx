import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { SECRET_KEPT } from '@/components/admin/secret-edit';
import { ApiError } from '@/lib/api';
import { setLanguage } from '@/lib/i18n';
import type { TsaSection } from '@/lib/settings-types';

import {
  TURKTRUST_KEPT,
  turktrustChanged,
  type Credentials,
  type EvidenceForm,
  type ServerChoice,
} from './kamusm-model';
import { CredentialsStep, EvidenceFields, ProductionStep, TsaRefusal } from './kamusm-steps';

/*
 * Ayarlar → Zaman damgası, the server choice (Wave 5 contract 2.3.2, 2.4): Kamu SM's test and
 * production servers, TÜRKTRUST over HTTPS with its username and password and the "sahada
 * doğrula" note, and TÜRKTRUST over plain HTTP, which says that the password travels unencrypted
 * and asks for a typed confirmation before anything is sent. The body each choice sends is
 * `kamusm-model.test.ts`'s; the page with the API is the E2E's (after W5-T's schema).
 */

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
    roots: [],
    // As W5-T's API reports it: TÜRKTRUST known, its credentials not stored.
    secrets: {
      'kamusm.customer': { set: true, changed_at: null, changed_by: null },
      'kamusm.password': { set: true, changed_at: null, changed_by: null },
      'turktrust.username': { set: false, changed_at: null, changed_by: null },
      'turktrust.password': { set: false, changed_at: null, changed_by: null },
    } as TsaSection['secrets'],
    readiness: { production: true, missing: [] },
    seal_ready: { ok: true, causes: [] },
    ...overrides,
  };
}

/** A TÜRKTRUST site as the API reads it before W5-T names its servers (`server` null). */
const TURKTRUST_SITE = tsa({
  provider: 'turktrust',
  server: null,
  secrets: {
    'turktrust.username': { set: true, changed_at: null, changed_by: 'web:tolga' },
    'turktrust.password': { set: true, changed_at: null, changed_by: 'web:tolga' },
  } as TsaSection['secrets'],
  readiness: { production: false, missing: ['zamane_jar', 'java'] },
});

/** A Kamu SM site whose TÜRKTRUST credentials are stored (a later switch needs nothing typed). */
const STORED = tsa({
  secrets: {
    ...tsa().secrets,
    'turktrust.username': { set: true, changed_at: null, changed_by: 'web:tolga' },
    'turktrust.password': { set: true, changed_at: null, changed_by: 'web:tolga' },
  } as TsaSection['secrets'],
});

/** A server before W5-T: no `turktrust.*` in `secrets`. */
const BEFORE_W5T = tsa({
  secrets: {
    'kamusm.customer': { set: true, changed_at: null, changed_by: null },
    'kamusm.password': { set: true, changed_at: null, changed_by: null },
  },
});

const KEPT: Credentials = {
  customer: SECRET_KEPT,
  password: SECRET_KEPT,
  clear: false,
  turktrust: TURKTRUST_KEPT,
};

/** The step with the page's credential state; `switched` records every switch it asks for. */
function Production({
  section,
  unsaved = false,
  switched,
  seen = [],
  initial = KEPT,
}: {
  section: TsaSection;
  unsaved?: boolean;
  switched: ServerChoice[];
  seen?: Credentials[];
  /** The credentials as the page holds them (typed in step 4, for instance). */
  initial?: Credentials;
}) {
  const [credentials, setCredentials] = useState<Credentials>(initial);
  return (
    <ProductionStep
      tsa={section}
      tested={false}
      disabled={false}
      unsaved={unsaved}
      turktrustUnsaved={turktrustChanged(credentials)}
      credentials={credentials}
      onCredentials={(next) => {
        seen.push(next);
        setCredentials(next);
      }}
      errors={{}}
      onSwitch={(server) => {
        switched.push(server);
      }}
    />
  );
}

function checklist() {
  return screen.getByRole('list', { name: 'Üretim için gerekenler' });
}

afterEach(() => {
  act(() => {
    setLanguage('tr');
  });
});

describe('Ayarlar → Zaman damgası: the server to switch to', () => {
  it('lists the four servers; the one in use cannot be chosen, production is preselected', async () => {
    const switched: ServerChoice[] = [];
    render(<Production section={tsa()} switched={switched} />);
    expect(screen.getByTestId('tsa-server')).toHaveTextContent(
      'Şu an Kamu SM test sunucusu kullanılıyor.',
    );
    const group = screen.getByRole('group', { name: 'Geçilecek sunucu' });
    expect(within(group).getAllByRole('radio')).toHaveLength(4);
    const inUse = within(group).getByRole('radio', {
      name: 'Kamu SM test sunucusu (şu an kullanılıyor)',
    });
    expect(inUse).toBeDisabled();
    expect(inUse).not.toBeChecked();
    expect(within(group).getByRole('radio', { name: 'Kamu SM üretim sunucusu' })).toBeChecked();
    expect(
      within(group).getByRole('radio', { name: 'TÜRKTRUST (HTTPS)' }),
    ).toHaveAccessibleDescription(
      'TÜRKTRUST kullanıcı adı ve parolası gerekir; Java ve Zamane istemcisi gerekmez.',
    );
    // Kamu SM's checklist; no TÜRKTRUST field, no note.
    expect(within(checklist()).getByText('Onaylı Zamane istemcisi')).toBeInTheDocument();
    expect(screen.queryByTestId('turktrust-credentials')).not.toBeInTheDocument();
    expect(screen.queryByTestId('turktrust-note')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Üretim sunucusuna geç' }));
    const ask = screen.getByRole('dialog', { name: 'Üretim sunucusuna geçilsin mi?' });
    await userEvent.click(within(ask).getByRole('button', { name: 'Üretim sunucusuna geç' }));
    expect(switched).toEqual(['production']);
  });

  it('TÜRKTRUST: the username and password, write-only, with the "sahada doğrula" note', async () => {
    const switched: ServerChoice[] = [];
    const seen: Credentials[] = [];
    render(<Production section={tsa()} switched={switched} seen={seen} />);
    await userEvent.click(screen.getByRole('radio', { name: 'TÜRKTRUST (HTTPS)' }));

    const fields = screen.getByTestId('turktrust-credentials');
    expect(fields).toHaveTextContent('TÜRKTRUST kullanıcı adı');
    expect(fields).toHaveTextContent('Kayıtlı değil');
    expect(fields.querySelector('input')).toBeNull();
    const note = screen.getByTestId('turktrust-note');
    expect(note).toHaveTextContent('Sahada doğrula');
    expect(note).toHaveTextContent('zd.turktrust.com.tr');
    expect(note).toHaveTextContent('turktrust.md bölüm 2');
    // Java and the Zamane client are not TÜRKTRUST's: its own two items are checked.
    const items = checklist();
    expect(within(items).queryByText('Onaylı Zamane istemcisi')).not.toBeInTheDocument();
    expect(within(items).getByText('TÜRKTRUST kullanıcı adı kayıtlı')).toBeInTheDocument();
    expect(within(items).getByText('TÜRKTRUST parolası kayıtlı').parentElement).toHaveTextContent(
      ': Eksik',
    );
    expect(screen.queryByTestId('tsa-plain-http')).not.toBeInTheDocument();
    // Neither credential is stored or typed: the switch waits (the server decides again).
    expect(screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' })).toBeDisabled();
    expect(
      screen.getByText('Üretime geçmek için yukarıdaki eksikleri tamamlayın.'),
    ).toBeInTheDocument();

    const [username, password] = within(fields).getAllByRole('button', { name: 'Gir' });
    if (!username || !password) throw new Error('no fields');
    await userEvent.click(username);
    await userEvent.type(screen.getByLabelText('TÜRKTRUST kullanıcı adı'), 'ornek-as');
    await userEvent.click(password);
    const secret = screen.getByLabelText('TÜRKTRUST parolası');
    expect(secret).toHaveAttribute('type', 'password');
    expect(secret).toHaveAttribute('autocomplete', 'new-password');
    await userEvent.type(secret, 'deneme-parola'); // gitleaks:allow — fake test value
    expect(seen.at(-1)?.turktrust).toEqual({
      username: { value: 'ornek-as', editing: true, clear: false },
      password: { value: 'deneme-parola', editing: true, clear: false },
    });
    // Typed, not stored: said so, and enough for the switch, which sends them.
    for (const item of [
      'TÜRKTRUST kullanıcı adı girildi, kaydedilmedi',
      'TÜRKTRUST parolası girildi, kaydedilmedi',
    ])
      expect(within(checklist()).getByText(item).parentElement).toHaveTextContent(': Tamam');
    expect(within(checklist()).queryByText('TÜRKTRUST parolası kayıtlı')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' }));
    const ask = screen.getByRole('dialog', { name: 'TÜRKTRUST sunucusuna geçilsin mi?' });
    expect(ask).toHaveTextContent('HTTPS');
    await userEvent.click(within(ask).getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' }));
    expect(switched).toEqual(['turktrust']);
  });

  it('plain HTTP: says the password travels unencrypted and waits for the typed phrase', async () => {
    const switched: ServerChoice[] = [];
    render(<Production section={STORED} switched={switched} />);
    await userEvent.click(screen.getByRole('radio', { name: 'TÜRKTRUST (düz HTTP)' }));
    expect(screen.getByTestId('tsa-plain-http')).toHaveTextContent(
      'kullanıcı adı ve parolası ağda şifrelenmeden, açık metin olarak gider',
    );
    expect(screen.getByTestId('turktrust-note')).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna düz HTTP ile geç' }),
    );
    let ask = screen.getByRole('dialog', {
      name: 'TÜRKTRUST sunucusuna düz HTTP ile geçilsin mi?',
    });
    expect(ask).toHaveTextContent('şifrelenmeden');
    const confirm = within(ask).getByRole('button', {
      name: 'TÜRKTRUST sunucusuna düz HTTP ile geç',
    });
    expect(confirm).toBeDisabled();
    // The phrase is exactly HTTP (Wave 5 contract C5): another case or word does not do.
    const phrase = within(ask).getByLabelText('Onaylamak için HTTP yazın');
    await userEvent.type(phrase, 'http');
    expect(confirm).toBeDisabled();
    await userEvent.clear(phrase);
    await userEvent.type(phrase, 'DÜZ HTTP');
    expect(confirm).toBeDisabled();
    // "İptal" asks nothing and forgets what was typed.
    await userEvent.click(within(ask).getByRole('button', { name: 'İptal' }));
    expect(switched).toEqual([]);
    await userEvent.click(
      screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna düz HTTP ile geç' }),
    );
    ask = screen.getByRole('dialog', { name: 'TÜRKTRUST sunucusuna düz HTTP ile geçilsin mi?' });
    expect(within(ask).getByLabelText('Onaylamak için HTTP yazın')).toHaveValue('');
    await userEvent.type(within(ask).getByLabelText('Onaylamak için HTTP yazın'), ' HTTP ');
    await userEvent.click(
      within(ask).getByRole('button', { name: 'TÜRKTRUST sunucusuna düz HTTP ile geç' }),
    );
    expect(switched).toEqual(['turktrust-http']);
  });

  it('back from production to the test server asks again; unsaved changes wait', async () => {
    const switched: ServerChoice[] = [];
    const { unmount } = render(
      <Production section={tsa({ server: 'production' })} switched={switched} />,
    );
    expect(screen.getByRole('radio', { name: 'Kamu SM test sunucusu' })).toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Test sunucusuna dön' }));
    const back = screen.getByRole('dialog', { name: 'Test sunucusuna dönülsün mü?' });
    expect(back).toHaveTextContent('Üretim damgaları durur');
    await userEvent.click(within(back).getByRole('button', { name: 'Test sunucusuna dön' }));
    expect(switched).toEqual(['test']);
    unmount();

    render(<Production section={tsa()} unsaved switched={switched} />);
    expect(screen.getByRole('button', { name: 'Üretim sunucusuna geç' })).toBeDisabled();
    expect(
      screen.getByText('Önce diğer adımlardaki değişiklikleri kaydedin ya da bırakın.'),
    ).toBeInTheDocument();
  });

  it('Kamu SM production waits for its readiness; TÜRKTRUST does not need Java or Zamane', async () => {
    const switched: ServerChoice[] = [];
    render(
      <Production
        section={{
          ...STORED,
          provider: 'mock',
          server: null,
          readiness: { production: false, missing: ['zamane_jar', 'java'] },
        }}
        switched={switched}
      />,
    );
    expect(screen.getByTestId('tsa-server')).toHaveTextContent(
      'Şu an mock sağlayıcısı kullanılıyor.',
    );
    expect(screen.getByRole('button', { name: 'Üretim sunucusuna geç' })).toBeDisabled();
    expect(
      screen.getByText('Üretime geçmek için yukarıdaki eksikleri tamamlayın.'),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: 'TÜRKTRUST (HTTPS)' }));
    expect(screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' })).toBeEnabled();
    // From the mock, the test server stops no production stamps: no red "dön".
    await userEvent.click(screen.getByRole('radio', { name: 'Kamu SM test sunucusu' }));
    await userEvent.click(screen.getByRole('button', { name: 'Kamu SM test sunucusuna geç' }));
    const ask = screen.getByRole('dialog', { name: 'Kamu SM test sunucusuna geçilsin mi?' });
    expect(ask).toHaveTextContent('yasal geçerliliği yoktur');
    await userEvent.click(within(ask).getByRole('button', { name: 'Kamu SM test sunucusuna geç' }));
    expect(switched).toEqual(['test']);
  });

  it('a TÜRKTRUST site: its server in words, its stored credentials never shown', () => {
    render(<Production section={TURKTRUST_SITE} switched={[]} />);
    expect(screen.getByTestId('tsa-server')).toHaveTextContent(
      'Şu an TÜRKTRUST sunucusu kullanılıyor.',
    );
    // Its server has no name yet (W5-T): every choice stays open, production is preselected.
    expect(screen.getAllByRole('radio').filter((radio) => radio.hasAttribute('disabled'))).toEqual(
      [],
    );
  });

  it("before W5-T the server knows no TÜRKTRUST: only Kamu SM's two servers", () => {
    render(<Production section={BEFORE_W5T} switched={[]} />);
    const group = screen.getByRole('group', { name: 'Geçilecek sunucu' });
    expect(within(group).getAllByRole('radio')).toHaveLength(2);
    expect(
      within(group).getByRole('radio', { name: 'Kamu SM test sunucusu (şu an kullanılıyor)' }),
    ).toBeDisabled();
    expect(within(group).getByRole('radio', { name: 'Kamu SM üretim sunucusu' })).toBeChecked();
    expect(within(group).queryByRole('radio', { name: /TÜRKTRUST/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('turktrust-credentials')).not.toBeInTheDocument();
  });

  it('values typed for TÜRKTRUST hold back a switch elsewhere; choosing another server drops them', async () => {
    const switched: ServerChoice[] = [];
    const seen: Credentials[] = [];
    // A TÜRKTRUST site whose new username was typed in step 4 and not saved.
    const initial: Credentials = {
      ...KEPT,
      turktrust: {
        ...TURKTRUST_KEPT,
        username: { value: 'ornek-as', editing: true, clear: false },
      },
    };
    render(
      <Production section={TURKTRUST_SITE} switched={switched} seen={seen} initial={initial} />,
    );
    // Preselected: Kamu SM production, which would not send the username.
    expect(screen.getByRole('radio', { name: 'Kamu SM üretim sunucusu' })).toBeChecked();
    expect(
      screen.getByText('Önce diğer adımlardaki değişiklikleri kaydedin ya da bırakın.'),
    ).toBeInTheDocument();
    // To TÜRKTRUST, the typed username goes with the switch: nothing waits.
    await userEvent.click(screen.getByRole('radio', { name: 'TÜRKTRUST (HTTPS)' }));
    expect(screen.getByRole('button', { name: 'TÜRKTRUST sunucusuna geç' })).toBeEnabled();
    expect(
      screen.queryByText('Önce diğer adımlardaki değişiklikleri kaydedin ya da bırakın.'),
    ).not.toBeInTheDocument();
    expect(seen).toEqual([]);
    // Another server: what was typed for TÜRKTRUST is dropped.
    await userEvent.click(screen.getByRole('radio', { name: 'Kamu SM test sunucusu' }));
    expect(seen.at(-1)?.turktrust).toEqual(TURKTRUST_KEPT);
    expect(screen.queryByTestId('turktrust-credentials')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Kamu SM test sunucusuna geç' })).toBeEnabled();
  });

  it('in English', async () => {
    act(() => {
      setLanguage('en');
    });
    render(<Production section={STORED} switched={[]} />);
    await userEvent.click(screen.getByRole('radio', { name: 'TÜRKTRUST (plain HTTP)' }));
    expect(screen.getByTestId('tsa-plain-http')).toHaveTextContent('cross the network unencrypted');
    expect(screen.getByTestId('turktrust-note')).toHaveTextContent('Verify on site');
    await userEvent.click(
      screen.getByRole('button', { name: 'Switch to the TÜRKTRUST server over plain HTTP' }),
    );
    expect(
      screen.getByRole('dialog', { name: 'Switch to the TÜRKTRUST server over plain HTTP?' }),
    ).toHaveTextContent('Type HTTP to confirm');
  });
});

describe('Ayarlar → Zaman damgası: the credentials of the provider in use', () => {
  function Step({ section }: { section: TsaSection }) {
    const [credentials, setCredentials] = useState<Credentials>(KEPT);
    return (
      <CredentialsStep
        tsa={section}
        credentials={credentials}
        onChange={setCredentials}
        disabled={false}
        errors={{}}
        probe
        onProbe={() => undefined}
      />
    );
  }

  it('TÜRKTRUST in use: its username and password, stored ones shown as "Kayıtlı" only', () => {
    render(<Step section={TURKTRUST_SITE} />);
    const fields = screen.getByTestId('turktrust-credentials');
    expect(within(fields).getAllByText('Kayıtlı', { exact: false })).not.toHaveLength(0);
    expect(within(fields).getAllByRole('button', { name: 'Değiştir' })).toHaveLength(2);
    expect(fields.querySelector('input')).toBeNull();
    expect(screen.queryByText('Müşteri numarası')).not.toBeInTheDocument();
    expect(screen.getByTestId('turktrust-note')).toBeInTheDocument();
    expect(
      screen.getByLabelText('Kaydetmeden önce yeni bilgilerle TÜRKTRUST bağlantısını dene'),
    ).toBeChecked();
  });

  it('Kamu SM in use: the customer number and password, as before', () => {
    render(<Step section={tsa()} />);
    expect(screen.getByText('Müşteri numarası')).toBeInTheDocument();
    expect(screen.queryByTestId('turktrust-credentials')).not.toBeInTheDocument();
    expect(
      screen.getByLabelText('Kaydetmeden önce yeni bilgilerle kontör sorgusu yap'),
    ).toBeChecked();
  });

  it('a refusal names the TÜRKTRUST items `tsa_not_ready` lists', () => {
    const error = new ApiError({
      status: 422,
      code: 'tsa_not_ready',
      messageTr: 'Sunucu henüz hazır değil.',
      messageEn: 'The server is not ready yet.',
      details: { missing: ['turktrust.username', 'turktrust.password'] },
    });
    render(<TsaRefusal error={error} provider="turktrust" />);
    const refusal = screen.getByTestId('tsa-refusal');
    expect(refusal).toHaveTextContent('TÜRKTRUST kullanıcı adı kayıtlı');
    expect(refusal).toHaveTextContent('TÜRKTRUST parolası kayıtlı');
  });
});

describe('Ayarlar → Zaman damgası: re-stamping and revocation lists (Wave 5 contract 2.5)', () => {
  const FORM: EvidenceForm = { renewBeforeDays: '180', crlUrls: '', crlMaxAgeDays: '7' };

  function Fields({
    changed = false,
    error,
    seen,
  }: {
    changed?: boolean;
    error?: string;
    seen: Partial<EvidenceForm>[];
  }) {
    return (
      <EvidenceFields
        id="evidence"
        form={FORM}
        disabled={false}
        changed={changed}
        errors={{ crlUrls: error }}
        onChange={(next) => {
          seen.push(next);
        }}
      />
    );
  }

  it('the window, the age and the addresses with their bounds and the "sahada doğrula" note', async () => {
    const seen: Partial<EvidenceForm>[] = [];
    const { rerender } = render(<Fields error="Her satır bir adres olmalı." seen={seen} />);
    const group = screen.getByRole('region', { name: 'Yeniden damgalama ve iptal listeleri' });
    expect(within(group).getByLabelText('Yeniden damgalama süresi (gün)')).toHaveValue('180');
    expect(group).toHaveTextContent('30–730 gün, varsayılan 180.');
    expect(within(group).getByLabelText('İptal listesinin en büyük yaşı (gün)')).toHaveValue('7');
    expect(group).toHaveTextContent('1–60 gün, varsayılan 7.');
    const urls = within(group).getByLabelText('İptal listesi adresleri');
    expect(urls).toHaveAccessibleDescription('Her satır bir adres olmalı.');
    expect(urls).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByTestId('tsa-crl-note')).toHaveTextContent('Sahada doğrula');
    expect(screen.getByTestId('tsa-crl-note')).toHaveTextContent('hiçbir adres eklemez');
    expect(group).not.toHaveTextContent('parolanız yeniden sorulur');
    await userEvent.type(urls, 'x');
    expect(seen).toEqual([{ crlUrls: 'x' }]);
    rerender(<Fields changed seen={seen} />);
    expect(urls).toHaveAccessibleDescription(
      /^Her satıra bir adres, http:\/\/ ya da https:\/\/ ile/,
    );
    expect(group).toHaveTextContent('Bu ayarların değişikliği için parolanız yeniden sorulur.');
  });

  it('in English', () => {
    act(() => {
      setLanguage('en');
    });
    render(<Fields seen={[]} />);
    expect(screen.getByLabelText('Re-stamping window (days)')).toHaveValue('180');
    expect(screen.getByTestId('tsa-crl-note')).toHaveTextContent('Verify on site');
    expect(screen.getByText(/at most 20 addresses/)).toBeInTheDocument();
  });
});
