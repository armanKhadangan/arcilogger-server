import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingsOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';

/*
 * Ayarlar → Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi: the upper bounds of the
 * settings that widen a lookup's days (W5-G-19). The group is the captured `GET /settings` group
 * `lookup` (`settings.json`); each range is shown before saving, and a value above it is refused
 * in the form, with the numbers of the API's metadata, before anything is sent.
 */

/** `samples/settings.json`, read as `src/test/samples.ts` reads the samples (it does not load it). */
const SETTINGS = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'settings.json'), 'utf8'),
) as { list: SettingsOut };

/** The captured group `lookup`. */
function lookup(): SettingGroup {
  const group = structuredClone(SETTINGS.list.groups.find((item) => item.id === 'lookup'));
  if (!group) throw new Error('sample missing: settings group lookup');
  return group;
}

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

function serve() {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings' && request.method === 'GET')
      return jsonResponse(200, { groups: [lookup()] } satisfies SettingsOut);
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

async function field(key: string): Promise<HTMLElement> {
  renderApp('/settings/application?group=lookup');
  return screen.findByTestId(`setting-${key}`);
}

describe('the bounds of the lookup settings (W5-G-19)', () => {
  it('the captured metadata carries the bounds of the contract', () => {
    const bounds = Object.fromEntries(
      lookup().settings.map((setting) => [setting.key, [setting.min, setting.max]]),
    );
    expect(bounds).toMatchObject({
      'lookup.max_session_seconds': [60, 172_800],
      'lookup.dns_lookback_seconds': [0, 604_800],
      'lease.default_seconds': [60, 604_800],
      'lease.timeout_percent': [100, 125],
      'arp.interval_seconds': [60, 604_800],
    });
  });

  it('shows each range before saving, in Turkish and in English', async () => {
    serve();
    const session = await field('lookup.max_session_seconds');
    expect(session).toHaveTextContent('60–172800 arası.');
    expect(screen.getByTestId('setting-lease.timeout_percent')).toHaveTextContent('100–125 arası.');
    // The server rows: the API's own help names their range.
    expect(screen.getByTestId('setting-lease.server_seconds')).toHaveTextContent(
      'her biri 60 ile 604800 (7 gün) arasında',
    );
    act(() => {
      setLanguage('en');
    });
    expect(session).toHaveTextContent('Between 60 and 172800.');
    expect(screen.getByTestId('setting-arp.interval_seconds')).toHaveTextContent(
      'Between 60 and 604800.',
    );
    expect(screen.getByTestId('setting-lease.server_seconds')).toHaveTextContent(
      'each between 60 and 604800 (7 days)',
    );
  });

  it('refuses a value above the bound of an integer setting and sends nothing', async () => {
    const calls = serve();
    const session = await field('lookup.max_session_seconds');
    const user = userEvent.setup();
    const input = within(session).getByRole('textbox');
    await user.clear(input);
    await user.type(input, '172801');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(within(session).getByText('En çok 172800 olmalı.')).toBeVisible();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
  });

  it('refuses a server lease time above the bound of the default lease time and sends nothing', async () => {
    const calls = serve();
    const servers = await field('lease.server_seconds');
    const user = userEvent.setup();
    const seconds = within(servers).getByLabelText('Kira süresi (saniye)');
    expect(seconds).toHaveValue('3600');
    await user.clear(seconds);
    await user.type(seconds, '604801');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(
      within(servers).getByText(
        'Her DHCP sunucusu için 60 ile 604800 saniye arasında bir kira süresi gerekir.',
      ),
    ).toBeVisible();
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
  });
});
