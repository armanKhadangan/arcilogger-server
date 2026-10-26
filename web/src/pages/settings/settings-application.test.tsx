import { act, screen, within } from '@testing-library/react';

import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingOut, SettingsOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

/*
 * Ayarlar → Uygulama ayarları renders what `GET /settings` lists (contract 2.13): every group as
 * a tab, every setting with the server's label in the interface language — none is left out and
 * none is named by the page itself.
 */

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

function setting(
  key: string,
  type: SettingOut['type'],
  value: SettingOut['value'],
  extra: Partial<SettingOut> = {},
): SettingOut {
  return {
    key,
    value,
    default: value,
    is_default: true,
    type,
    min: type === 'integer' ? 0 : null,
    max: null,
    reauth: false,
    locked: false,
    label_tr: `Etiket ${key}`,
    label_en: `Label ${key}`,
    help_tr: `Yardım ${key}`,
    help_en: `Help ${key}`,
    updated_by: ':seed',
    updated_at: '2026-09-30T09:12:00+03:00',
    ...extra,
  };
}

const GROUPS: SettingGroup[] = [
  {
    id: 'alerts',
    label_tr: 'Uyarılar ve e-posta',
    label_en: 'Alerts and e-mail',
    settings: [
      setting('alerts.email_recipients', 'string_list', [], { reauth: true }),
      setting('alerts.email_min_severity', 'string', 'high', { reauth: true }),
      setting('alerts.email_resolved', 'boolean', true),
    ],
  },
  {
    id: 'lookup',
    label_tr: '5651 sorgu ve kira zaman çizelgesi',
    label_en: '5651 lookup and lease timeline',
    settings: [
      setting('lookup.tolerance_seconds', 'integer', 60, { max: 3600 }),
      setting('lease.server_seconds', 'object', {}),
      setting('devices.oui_file', 'string', '', { locked: true }),
    ],
  },
  {
    id: 'usage',
    label_tr: 'Kullanım analizi',
    label_en: 'Usage analytics',
    settings: [
      setting('usage.work_hours', 'object', {
        days: [1, 2, 3, 4, 5],
        start: '08:00',
        end: '18:00',
      }),
      setting('usage.holidays', 'string_list', ['10-29']),
      setting('usage.background_ports', 'integer_list', [53]),
    ],
  },
  {
    id: 'reports',
    label_tr: 'Kullanım raporları',
    label_en: 'Usage reports',
    settings: [setting('reports.keep_days', 'integer', 400)],
  },
  {
    id: 'evidence',
    label_tr: 'Kanıt paketleri',
    label_en: 'Evidence packages',
    settings: [setting('evidence.keep_days', 'integer', 30)],
  },
  {
    id: 'security',
    label_tr: 'Oturum, parola ve gizlilik',
    label_en: 'Sessions, passwords and privacy',
    settings: [
      setting('auth.totp_required_roles', 'string_list', ['admin', 'auditor'], { reauth: true }),
      setting('privacy.notice_version', 'string', '1'),
    ],
  },
  {
    id: 'database',
    label_tr: 'Veritabanı',
    label_en: 'Database',
    settings: [setting('db.retention_days', 'integer', 400)],
  },
];

function serve() {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings')
      return jsonResponse(200, { groups: GROUPS } satisfies SettingsOut);
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

describe('Uygulama ayarları', () => {
  it('shows a label for every setting the API lists, in every group', async () => {
    serve();
    renderApp('/settings/application');
    expect(await screen.findByRole('tab', { name: 'Uyarılar ve e-posta' })).toBeInTheDocument();
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(
      GROUPS.map((group) => group.label_tr),
    );
    for (const group of GROUPS) {
      // Every group stays mounted (hidden when not chosen): what was typed survives a tab change.
      const panel = screen.getByTestId(`settings-group-${group.id}`);
      for (const item of group.settings) {
        const field = within(panel).getByTestId(`setting-${item.key}`);
        expect(field).toHaveTextContent(item.label_tr);
        expect(field).toHaveTextContent(item.help_tr);
      }
    }
  });

  it('takes the English labels for the English interface', async () => {
    serve();
    act(() => {
      setLanguage('en');
    });
    renderApp('/settings/application');
    expect(await screen.findByRole('tab', { name: 'Alerts and e-mail' })).toBeInTheDocument();
    for (const group of GROUPS)
      for (const item of group.settings)
        expect(screen.getByTestId(`setting-${item.key}`)).toHaveTextContent(item.label_en);
    expect(screen.queryByText(/^Etiket /)).not.toBeInTheDocument();
  });

  it('a locked setting has no editor; one needing re-authentication says so', async () => {
    serve();
    renderApp('/settings/application');
    const locked = await screen.findByTestId('setting-devices.oui_file');
    expect(within(locked).queryByRole('textbox')).not.toBeInTheDocument();
    expect(locked).toHaveTextContent('yalnızca sunucu konsolundan değiştirilebilir');
    expect(screen.getByTestId('setting-alerts.email_recipients')).toHaveTextContent(
      'Parola onayı gerekir',
    );
    expect(screen.getByTestId('setting-alerts.email_resolved')).not.toHaveTextContent(
      'Parola onayı gerekir',
    );
  });
});
