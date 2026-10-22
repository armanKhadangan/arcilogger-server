import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { LicenseOut, LicenseTimeSource, Role } from '@/lib/api-types';
import { setLanguage } from '@/lib/i18n';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import {
  LICENSE_DOCUMENT_NAMES,
  type LicenseErrorName,
  type LicenseErrorSample,
} from '@/test/license-samples';
import { renderApp } from '@/test/render';
import {
  LICENSE_ID,
  LICENSE_INSTALLATION_ID,
  LICENSE_REQUEST_CODE,
  LICENSE_SERVER_ID,
  licenseError,
  licenseSample,
} from '@/test/samples';

/*
 * Ayarlar → Lisans (contract 8.2): the state with modules and limits, activation (request code
 * copy and download), the licence file (upload with re-authentication, refusals with the API's
 * texts, a client-side size check, removal with confirmation), history and "Hakkında".
 */

interface FakeLicenseApi {
  calls: RecordedRequest[];
  /** What the next POST /license answers, by file name (default: the licensed state). */
  refusals: Map<string, LicenseErrorSample>;
  state: () => LicenseOut;
}

function serve(initial: LicenseOut, role: Role = 'admin'): FakeLicenseApi {
  setCsrfCookie('csrf-1');
  let status = initial;
  let reauthenticated = false;
  const refusals = new Map<string, LicenseErrorSample>();
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe(role));
    if (request.path === '/api/v1/auth/reauth') {
      reauthenticated = true;
      return jsonResponse(200, { reauthenticated_at: '2026-10-13T04:00:00Z', valid_minutes: 5 });
    }
    if (request.path !== '/api/v1/license') return errorResponse(404, 'not_found');
    if (request.method === 'GET') return jsonResponse(200, status);
    if (!reauthenticated) {
      const { status: code, body } = licenseError('reauth_required');
      return jsonResponse(code, body);
    }
    if (request.method === 'DELETE') {
      if (status.status === 'trial' || status.status === 'trial_expired') {
        const { status: code, body } = licenseError('remove_not_installed');
        return jsonResponse(code, body);
      }
      status = licenseSample('trial_expired');
      return jsonResponse(204, null);
    }
    const file = (request.body as FormData).get('file') as File;
    const refusal = refusals.get(file.name);
    if (refusal) return jsonResponse(refusal.status, refusal.body);
    status = licenseSample('upload_ok');
    return jsonResponse(200, status);
  });
  return { calls, refusals, state: () => status };
}

async function openPage(): Promise<HTMLElement> {
  renderApp('/settings/license');
  expect(await screen.findByRole('heading', { level: 1, name: 'Lisans' })).toBeInTheDocument();
  return screen.findByTestId('license-section-status');
}

async function confirmPassword(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await screen.findByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
  await user.type(within(dialog).getByLabelText(/^Parola/), 'parola');
  await user.type(within(dialog).getByLabelText(/Doğrulama kodu/), '123456');
  await user.click(within(dialog).getByRole('button', { name: 'Doğrula' }));
}

function licenceFile(name: string, size = 900): File {
  return new File([new Uint8Array(size).fill(0x20)], name, { type: 'application/json' });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setCsrfCookie(null);
  act(() => {
    setLanguage('tr');
  });
});

const STATE_LABELS: Record<LicenseOut['status'], string> = {
  trial: 'Deneme sürümü',
  trial_expired: 'Lisanssız',
  licensed: 'Lisanslı',
  invalid: 'Geçersiz lisans',
  wrong_server: 'Bu sunucu için değil',
  expired: 'Süresi doldu',
  version_not_covered: 'Sürüm bakım kapsamında değil',
};

describe('Lisans — every captured document', () => {
  it.each(LICENSE_DOCUMENT_NAMES.filter((name) => !name.endsWith('_viewer')))(
    '%s: the state, the four modules, the limits and the history',
    async (name) => {
      const status = licenseSample(name);
      serve(status);
      const section = await openPage();
      expect(within(section).getByTestId('license-state')).toHaveTextContent(
        STATE_LABELS[status.status],
      );
      const modules = within(section).getByTestId('license-modules');
      expect(within(modules).getAllByRole('row')).toHaveLength(1 + status.modules.length);
      const limits = within(section).getByTestId('license-limits');
      expect(within(limits).getByRole('row', { name: /Kullanıcı sayısı/ })).toHaveTextContent(
        `${status.limits.users.used} / ${status.limits.users.max ?? 'Sınırsız'}`,
      );
      const history = status.details?.history ?? [];
      expect(within(screen.getByTestId('license-history')).getAllByRole('row')).toHaveLength(
        1 + history.length,
      );
    },
  );
});

describe('Lisans — Durum', () => {
  it('a licence: state, licensee, ids, perpetual validity, maintenance, modules, limits', async () => {
    serve(licenseSample('licensed'));
    const section = await openPage();
    expect(within(section).getByTestId('license-state')).toHaveTextContent('Lisanslı');
    expect(within(section).getByTestId('license-licensee')).toHaveTextContent('Örnek A.Ş.');
    expect(within(section).getByTestId('license-id')).toHaveTextContent(LICENSE_ID);
    expect(within(section).getByTestId('license-valid-until')).toHaveTextContent(
      'Süresiz (kalıcı lisans)',
    );
    expect(within(section).getByTestId('license-maintenance')).toHaveTextContent(
      '20.10.2027 (372 gün kaldı)',
    );
    const rows = within(within(section).getByTestId('license-modules')).getAllByRole('row');
    expect(rows.slice(1).map((row) => row.textContent)).toEqual([
      expect.stringMatching(/^5651 TemelHer lisanstaLog toplama.*Açık$/),
      expect.stringMatching(/^Kullanım analizi.*Açık$/),
      expect.stringMatching(/^Zamanlanmış kullanım raporları.*Açık$/),
      expect.stringMatching(/^Toplu içe aktarma \(CSV\).*Lisansta yok$/),
    ]);
    const limits = within(section).getByTestId('license-limits');
    expect(within(limits).getByRole('row', { name: /Log kaynağı sayısı/ })).toHaveTextContent(
      '3 / 10',
    );
    expect(within(limits).getByRole('row', { name: /Kullanıcı sayısı/ })).toHaveTextContent(
      '4 / 25',
    );
  });

  it('the trial: days left, unlimited limits, unconfigured senders, every module on', async () => {
    serve(licenseSample('trial'));
    const section = await openPage();
    expect(within(section).getByTestId('license-state')).toHaveTextContent('Deneme sürümü');
    expect(within(section).getByTestId('license-licensee')).toHaveTextContent('Kurulu lisans yok');
    expect(within(section).getByTestId('license-trial')).toHaveTextContent(
      '19.10.2026 03:12:00 (6 gün kaldı)',
    );
    expect(within(section).queryByTestId('license-maintenance')).not.toBeInTheDocument();
    const limits = within(section).getByTestId('license-limits');
    expect(within(limits).getByRole('row', { name: /Log kaynağı sayısı/ })).toHaveTextContent(
      '3 / Sınırsız1 yapılandırılmamış gönderici dahil',
    );
    expect(within(limits).getByRole('row', { name: /Kullanıcı sayısı/ })).toHaveTextContent(
      '4 / Sınırsız',
    );
    expect(within(section).getAllByText('Açık')).toHaveLength(4);
  });

  it('expired: the reason, the end date, licensed modules off', async () => {
    serve(licenseSample('expired'));
    const section = await openPage();
    expect(within(section).getByTestId('license-state')).toHaveTextContent('Süresi doldu');
    expect(within(section).getByTestId('license-problem')).toHaveTextContent(
      'Lisansın geçerlilik süresi doldu.',
    );
    expect(within(section).getByTestId('license-valid-until')).toHaveTextContent('10.10.2026');
    const modules = within(section).getByTestId('license-modules');
    expect(within(modules).getAllByText('Kapalı')).toHaveLength(2);
    expect(within(modules).getByText('Lisansta yok')).toBeInTheDocument();
  });

  it('limits over the maximum are marked', async () => {
    serve(licenseSample('licensed_limits_exceeded'));
    const section = await openPage();
    const limits = within(section).getByTestId('license-limits');
    expect(within(limits).getAllByText('Sınır aşıldı')).toHaveLength(2);
    expect(limits).toHaveTextContent('2 yapılandırılmamış gönderici dahil');
  });

  it('only for settings.manage', async () => {
    serve(licenseSample('trial'), 'auditor');
    renderApp('/settings/license');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeInTheDocument();
  });
});

describe('Lisans — Etkinleştirme', () => {
  it('shows the steps, the request code and this server’s ids', async () => {
    serve(licenseSample('trial'));
    await openPage();
    const section = screen.getByTestId('license-section-activation');
    expect(within(section).getByTestId('license-steps')).toHaveTextContent(
      'İstek kodunu kopyalayın.arman@khadangan.com adresine e-postayla gönderin.Gelen .lic dosyasını aşağıdan yükleyin.',
    );
    expect(within(section).getByRole('link', { name: 'arman@khadangan.com' })).toHaveAttribute(
      'href',
      'mailto:arman@khadangan.com',
    );
    expect(within(section).getByTestId('request-code')).toHaveTextContent(LICENSE_REQUEST_CODE);
    expect(within(section).getByTestId('license-server-id')).toHaveTextContent(LICENSE_SERVER_ID);
    expect(section).toHaveTextContent(LICENSE_INSTALLATION_ID);
    expect(within(section).getByTestId('license-organization')).toHaveTextContent('Örnek A.Ş.');
  });

  it('copies the code on one line', async () => {
    serve(licenseSample('trial'));
    const user = userEvent.setup();
    await openPage();
    const section = screen.getByTestId('license-section-activation');
    await user.click(within(section).getByRole('button', { name: 'Kopyala' }));
    expect(await navigator.clipboard.readText()).toBe(LICENSE_REQUEST_CODE);
    expect(await screen.findByText('Panoya kopyalandı')).toBeInTheDocument();
  });

  it('downloads arcilogger-istek-kodu.txt: the code plus CRLF', async () => {
    serve(licenseSample('trial'));
    const blobs: Blob[] = [];
    const names: string[] = [];
    Object.assign(URL, {
      createObjectURL: (blob: Blob) => {
        blobs.push(blob);
        return 'blob:code';
      },
      revokeObjectURL: () => undefined,
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      names.push(this.download);
    });
    const user = userEvent.setup();
    await openPage();
    await user.click(screen.getByRole('button', { name: 'İndir (.txt)' }));
    expect(names).toEqual(['arcilogger-istek-kodu.txt']);
    // jsdom's Blob has no `text()`.
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => {
        resolve(typeof reader.result === 'string' ? reader.result : '');
      };
      reader.readAsText(blobs[0] as Blob);
    });
    expect(text).toBe(`${LICENSE_REQUEST_CODE}\r\n`);
    expect(blobs[0]?.type).toBe('text/plain;charset=utf-8');
  });

  it('warns about a missing organisation name and an unreadable server id', async () => {
    const status = licenseSample('trial');
    if (status.details) {
      status.details.organization = '';
      status.details.server_id = null;
      status.details.server_id_error = 'FingerprintError: MachineGuid unreadable';
      status.details.request_code = null;
    }
    serve(status);
    await openPage();
    const section = screen.getByTestId('license-section-activation');
    expect(within(section).getByTestId('license-organization')).toHaveTextContent(
      'Kuruluş adı ayarlanmamış.',
    );
    expect(within(section).getByTestId('request-code-unavailable')).toHaveTextContent(
      'Sunucu kimliği okunamadı; istek kodu oluşturulamıyor.FingerprintError: MachineGuid unreadable',
    );
    expect(within(section).queryByRole('button', { name: 'Kopyala' })).not.toBeInTheDocument();
    expect(within(section).getByTestId('license-server-id')).toHaveTextContent('—');
  });
});

describe('Lisans — Lisans dosyası', () => {
  it('uploads after re-authentication; the page then shows the licence', async () => {
    const api = serve(licenseSample('trial'));
    const user = userEvent.setup();
    await openPage();
    const section = screen.getByTestId('license-section-file');
    await user.upload(
      within(section).getByLabelText('Lisans dosyası (.lic)'),
      licenceFile('ok.lic'),
    );
    expect(within(section).getByTestId('license-file-hint')).toHaveTextContent('ok.lic (900 B)');
    await user.click(within(section).getByRole('button', { name: 'Yükle' }));
    await confirmPassword(user);
    expect(await screen.findByText('Lisans dosyası yüklendi.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('license-state')).toHaveTextContent('Lisanslı');
    });
    const posts = api.calls.filter(
      (call) => call.method === 'POST' && call.path === '/api/v1/license',
    );
    expect(posts).toHaveLength(2);
    const sent = (posts[1]?.body as FormData).get('file') as File;
    expect(sent.name).toBe('ok.lic');
    expect(posts[1]?.headers.get('X-CSRF-Token')).toBe('csrf-1');
    expect(screen.getByRole('button', { name: 'Lisansı kaldır' })).toBeInTheDocument();
  });

  it.each<[LicenseErrorName, string]>([
    ['refuse_malformed', 'Dosya bir ARCILOGGER lisans dosyası değil ya da bozuk.'],
    ['refuse_unknown_key', 'Lisans, bu sürümün tanımadığı bir anahtarla imzalanmış (arci-2027a).'],
    [
      'refuse_signature_invalid',
      'Lisans dosyasının imzası geçersiz: dosya değiştirilmiş olabilir.',
    ],
    ['refuse_revoked', 'Bu lisans iptal edilmiş (ARCI-L-20261001-TEST0009).'],
    [
      'refuse_other_server',
      `Lisans bu sunucu için düzenlenmemiş (bu sunucu: ${LICENSE_SERVER_ID}). Yeni lisans için istek kodunu gönderin.`,
    ],
    ['refuse_expired', 'Lisansın geçerlilik süresi 10.10.2026 tarihinde doldu.'],
    [
      'refuse_version_not_covered',
      'Bu sürüm (0.1.0, yayın tarihi 01.10.2026) lisansın bakım süresinden (30.09.2026 tarihine kadar) sonra yayımlandı.',
    ],
    ['refuse_too_large', 'Lisans dosyası çok büyük (en çok 64 KiB).'],
  ])('shows the refusal %s with the API’s text', async (name, text) => {
    const api = serve(licenseSample('trial'));
    api.refusals.set('bad.lic', licenseError(name));
    const user = userEvent.setup();
    await openPage();
    const section = screen.getByTestId('license-section-file');
    await user.upload(
      within(section).getByLabelText('Lisans dosyası (.lic)'),
      licenceFile('bad.lic'),
    );
    await user.click(within(section).getByRole('button', { name: 'Yükle' }));
    await confirmPassword(user);
    const refusal = await within(section).findByTestId('license-refusal');
    expect(refusal).toHaveAttribute('role', 'alert');
    expect(refusal).toHaveTextContent(`Lisans dosyası reddedildi${text}`);
    expect(api.state().status).toBe('trial');
  });

  it('refuses a file over 64 KiB before sending it', async () => {
    const api = serve(licenseSample('trial'));
    const user = userEvent.setup();
    await openPage();
    const section = screen.getByTestId('license-section-file');
    await user.upload(
      within(section).getByLabelText('Lisans dosyası (.lic)'),
      licenceFile('big.lic', 65_537),
    );
    expect(within(section).getByTestId('license-file-hint')).toHaveTextContent(
      'Lisans dosyası çok büyük (en çok 64 KiB).',
    );
    expect(within(section).getByRole('button', { name: 'Yükle' })).toBeDisabled();
    expect(api.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('removes the licence after confirmation and re-authentication', async () => {
    const api = serve(licenseSample('licensed'));
    const user = userEvent.setup();
    await openPage();
    await user.click(screen.getByRole('button', { name: 'Lisansı kaldır' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lisans kaldırılsın mı?' });
    expect(dialog).toHaveTextContent('5651 işlevleri (log toplama, mühürleme, sorgu, kanıt');
    await user.click(within(dialog).getByRole('button', { name: 'Lisansı kaldır' }));
    await confirmPassword(user);
    expect(await screen.findByText('Lisans kaldırıldı.')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('license-state')).toHaveTextContent('Lisanssız');
    });
    expect(api.calls.filter((call) => call.method === 'DELETE')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Lisansı kaldır' })).not.toBeInTheDocument();
  });

  it('offers no removal without an installed licence', async () => {
    serve(licenseSample('trial_expired'));
    await openPage();
    expect(screen.queryByRole('button', { name: 'Lisansı kaldır' })).not.toBeInTheDocument();
  });

  it('an invalid installed licence can be removed', async () => {
    serve(licenseSample('invalid'));
    await openPage();
    expect(screen.getByRole('button', { name: 'Lisansı kaldır' })).toBeInTheDocument();
  });

  it.each([
    ['invalid', 'bu sürümün tanımadığı bir anahtarla'],
    ['revoked', 'iptal edilmiş'],
  ] as const)(
    '%s: an installed licence that does not verify is not "no licence installed"',
    async (name, reason) => {
      const status = licenseSample(name);
      expect(status.licensee).toBeNull();
      serve(status);
      const section = await openPage();
      const licensee = within(section).getByTestId('license-licensee');
      expect(licensee).toHaveTextContent('Kurulu lisans doğrulanamadı');
      expect(licensee).not.toHaveTextContent('Kurulu lisans yok');
      expect(within(section).getByTestId('license-problem')).toHaveTextContent(reason);
      expect(screen.getByRole('button', { name: 'Lisansı kaldır' })).toBeInTheDocument();
      act(() => {
        setLanguage('en');
      });
      expect(licensee).toHaveTextContent('The installed licence cannot be verified');
    },
  );

  it.each(['trial', 'trial_expired', 'trial_clock_inconsistent'] as const)(
    '%s: without an installed licence the licensee reads "no licence installed"',
    async (name) => {
      serve(licenseSample(name));
      const section = await openPage();
      expect(within(section).getByTestId('license-licensee')).toHaveTextContent(
        'Kurulu lisans yok',
      );
    },
  );
});

describe('Lisans — Geçmiş ve Hakkında', () => {
  it('lists the history, newest first, with readable kinds and sources', async () => {
    serve(licenseSample('licensed'));
    await openPage();
    const table = screen.getByTestId('license-history');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(
      rows.map((row) =>
        within(row)
          .getAllByRole('cell')
          .map((cell) => cell.textContent),
      ),
    ).toEqual([
      [
        '13.10.2026 07:05:00',
        'Durum değişti',
        'Arka plan işi',
        'system',
        '—',
        'Deneme sürümü → Lisanslı',
      ],
      ['13.10.2026 07:04:30', 'Yüklendi', 'Web arayüzü', 'tolga', LICENSE_ID, 'aynı lisans'],
      ['13.10.2026 07:04:00', 'Yüklendi', 'Web arayüzü', 'tolga', LICENSE_ID, ''],
      ...[
        ['13.10.2026 07:03:45', 'ARCI-L-20261001-TEST000E', 'license_version_not_covered'],
        ['13.10.2026 07:03:30', 'ARCI-L-20261001-TEST000D', 'license_expired'],
        ['13.10.2026 07:03:15', 'ARCI-L-20261001-TEST000C', 'license_other_server'],
        ['13.10.2026 07:03:00', 'ARCI-L-20261001-TEST0009', 'license_revoked'],
        ['13.10.2026 07:02:45', 'ARCI-L-20261001-TEST000B', 'license_signature_invalid'],
        ['13.10.2026 07:02:30', 'ARCI-L-20261001-TEST000A', 'license_unknown_key'],
        // Refused before the file could be read: no licence id.
        ['13.10.2026 07:02:15', '—', 'license_too_large'],
        ['13.10.2026 07:02:00', '—', 'license_malformed'],
      ].map(([at, id, code]) => [at, 'Reddedildi', 'Web arayüzü', 'tolga', id, code]),
      ['05.10.2026 03:15:00', 'Durum değişti', 'Arka plan işi', 'system', '—', '— → Deneme sürümü'],
    ]);
  });

  it('says so when there is no history', async () => {
    const status = licenseSample('trial');
    if (status.details) status.details.history = [];
    serve(status);
    await openPage();
    expect(screen.getByTestId('license-section-history')).toHaveTextContent(
      'Henüz lisans olayı yok.',
    );
  });

  it('names the version, release date, rights holder and the network-free licensing', async () => {
    serve(licenseSample('trial'));
    await openPage();
    const about = screen.getByTestId('license-section-about');
    expect(about).toHaveTextContent('ARCILOGGER sürümüARCILOGGER 0.1.0');
    expect(about).toHaveTextContent('Yayın tarihi01.10.2026');
    expect(within(about).getByTestId('license-rights-holder')).toHaveTextContent(
      'Arman Khadangan, arman@khadangan.com',
    );
    expect(about).toHaveTextContent('hiçbir ağ bağlantısı kurmaz');
  });

  it.each<[LicenseTimeSource, boolean, string]>([
    ['tsa', false, 'Kamu SM zaman damgası'],
    ['system', false, 'sistem saati'],
    ['vault_day', true, 'kasadaki son mühürlü gün (sistem saati geride)'],
    ['release_date', true, 'sürümün yayın tarihi (sistem saati geride)'],
    ['license_issued', false, 'lisansın düzenlenme tarihi'],
  ])('time source %s (clock behind: %s)', async (source, behind, text) => {
    const status = licenseSample('licensed');
    if (status.details)
      status.details.time = { ...status.details.time, source, clock_behind: behind };
    serve(status);
    await openPage();
    expect(screen.getByTestId('license-time-source')).toHaveTextContent(`Zaman kaynağı${text}`);
  });

  it('in English', async () => {
    serve(licenseSample('version_not_covered'));
    act(() => {
      setLanguage('en');
    });
    renderApp('/settings/license');
    expect(await screen.findByRole('heading', { level: 1, name: 'Licence' })).toBeInTheDocument();
    expect(await screen.findByTestId('license-state')).toHaveTextContent(
      'Version not covered by maintenance',
    );
    expect(screen.getByTestId('license-maintenance')).toHaveTextContent(
      '2026-09-30 (maintenance has ended)',
    );
    expect(screen.getByTestId('license-time-source')).toHaveTextContent('Time sourcesystem clock');
    expect(screen.getByTestId('license-rights-holder')).toHaveTextContent(
      'Rights holderArman Khadangan, arman@khadangan.com',
    );
  });
});
