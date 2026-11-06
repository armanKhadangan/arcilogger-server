import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setLanguage } from '@/lib/i18n';
import type { SettingGroup, SettingsOut, SettingValue } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import type { ErrorSample } from '@/test/samples';

/*
 * Ayarlar → Uygulama ayarları → Kullanım analizi: the work-hours editor (contract W5 §6 P1). A
 * week table with each day's hours and breaks, "Tüm günlere uygula" and a strip of each day; the
 * value saved keeps the hours most days share as the general ones and the rest as `by_day`. The
 * group is the captured `GET /settings` group `usage` (`settings.json`); the server's refusal is
 * the captured 422 of overlapping breaks.
 */

/** `samples/settings.json`, read as `src/test/samples.ts` reads the samples (it does not load it). */
const SETTINGS = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'settings.json'), 'utf8'),
) as { list: SettingsOut; errors: Record<string, ErrorSample> };
const STORED = { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' };

beforeAll(async () => {
  await import('@/pages/settings/application-page');
}, 120_000);

/** The captured group `usage`, the work hours stored as `hours`. */
function groups(hours: SettingValue): SettingGroup[] {
  const group = structuredClone(SETTINGS.list.groups.find((item) => item.id === 'usage'));
  if (!group) throw new Error('sample missing: settings group usage');
  for (const setting of group.settings)
    if (setting.key === 'usage.work_hours') setting.value = hours;
  return [group];
}

function serve(hours: SettingValue, patch?: (body: unknown) => Response) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/settings' && request.method === 'GET')
      return jsonResponse(200, { groups: groups(hours) } satisfies SettingsOut);
    if (request.path === '/api/v1/settings' && request.method === 'PATCH' && patch)
      return patch(request.body);
    return errorResponse(404, 'not_found');
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => {
    setLanguage('tr');
  });
});

async function editor() {
  renderApp('/settings/application?group=usage');
  return screen.findByTestId('setting-usage.work_hours');
}

describe('the work-hours editor', () => {
  it('shows the week with each day, its hours and its strip', async () => {
    serve(STORED);
    const field = await editor();
    expect(within(field).getAllByRole('row')).toHaveLength(8); // the header and seven days
    expect(within(field).getByRole('checkbox', { name: 'Pazartesi' })).toBeChecked();
    expect(within(field).getByRole('checkbox', { name: 'Cumartesi' })).not.toBeChecked();
    expect(within(field).getByLabelText('Pazartesi mesai başlangıcı')).toHaveValue('08:00');
    // A day off keeps the general hours, greyed out until it becomes a work day.
    expect(within(field).getByLabelText('Cumartesi mesai bitişi')).toBeDisabled();
    expect(
      within(field).getByRole('img', { name: 'Pazartesi: mesai 08:00–18:00, mola yok' }),
    ).toBeInTheDocument();
    expect(within(field).getByRole('img', { name: 'Pazar: mesai yok' })).toBeInTheDocument();
  });

  it('adds a lunch break, copies it to every day and keeps a shorter Friday', async () => {
    let sent: unknown = null;
    const calls = serve(STORED, (body) => {
      sent = body;
      return jsonResponse(200, { changed: [], unchanged: [] });
    });
    const field = await editor();
    const user = userEvent.setup();
    await user.click(within(field).getByRole('button', { name: 'Pazartesi için mola ekle' }));
    expect(within(field).getByLabelText('Pazartesi 1. mola başlangıcı')).toHaveValue('12:00');
    expect(within(field).getByLabelText('Pazartesi 1. mola bitişi')).toHaveValue('13:00');
    expect(
      within(field).getByRole('img', { name: 'Pazartesi: mesai 08:00–18:00, mola 12:00–13:00' }),
    ).toBeInTheDocument();
    await user.click(
      within(field).getByRole('button', {
        name: 'Pazartesi saatlerini tüm günlere uygula',
      }),
    );
    expect(within(field).getByLabelText('Salı 1. mola başlangıcı')).toHaveValue('12:00');
    // Friday ends at 15:00 and keeps the break.
    const friday = within(field).getByLabelText('Cuma mesai bitişi');
    await user.clear(friday);
    await user.type(friday, '15:00');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(1);
    });
    expect(sent).toEqual({
      changes: [
        {
          key: 'usage.work_hours',
          old: STORED,
          value: {
            ...STORED,
            breaks: [{ start: '12:00', end: '13:00' }],
            by_day: {
              '5': { start: '08:00', end: '15:00', breaks: [{ start: '12:00', end: '13:00' }] },
            },
          },
        },
      ],
    });
  });

  it("names the day of the browser's check and shows the server's refusal", async () => {
    const refusal = SETTINGS.errors.setting_invalid_work_hours;
    if (!refusal) throw new Error('sample missing: setting_invalid_work_hours');
    const calls = serve(STORED, () => jsonResponse(refusal.status, refusal.body));
    const field = await editor();
    const user = userEvent.setup();
    await user.click(within(field).getByRole('button', { name: 'Salı için mola ekle' }));
    const end = within(field).getByLabelText('Salı 1. mola bitişi');
    await user.clear(end);
    await user.type(end, '12:03');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    expect(within(field).getByText('Salı: her mola en az 5 dakika olmalı.')).toBeVisible();
    expect(calls.filter((call) => call.method === 'PATCH')).toHaveLength(0);
    // The browser accepts it; the server refuses (here: its captured overlap refusal).
    await user.clear(end);
    await user.type(end, '13:00');
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    const detail = (refusal.body as { error: { details: { message_tr: string }[] } }).error
      .details[0];
    expect(await within(field).findByText(detail?.message_tr ?? '')).toBeVisible();
    expect(within(field).getByRole('group', { name: 'Mesai saatleri' })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
  });

  it('keeps the focus in the row when a break goes, and names a day with invalid hours', async () => {
    serve(STORED);
    const field = await editor();
    const user = userEvent.setup();
    const add = within(field).getByRole('button', { name: 'Perşembe için mola ekle' });
    await user.click(add);
    await user.click(within(field).getByRole('button', { name: 'Perşembe 1. molayı kaldır' }));
    // The remove button went with its break: the focus moves to the row's "Mola ekle".
    await waitFor(() => {
      expect(add).toHaveFocus();
    });
    // A work day whose hours cannot be read is not "mesai yok".
    const end = within(field).getByLabelText('Perşembe mesai bitişi');
    await user.clear(end);
    await user.type(end, '07:00');
    expect(
      within(field).getByRole('img', { name: 'Perşembe: saatler geçersiz' }),
    ).toBeInTheDocument();
    act(() => {
      setLanguage('en');
    });
    // English: the accessible name starts with the visible text (WCAG 2.5.3).
    expect(
      within(field).getByRole('button', { name: "Apply to all days (Monday's hours)" }),
    ).toHaveTextContent('Apply to all days');
  });

  it('reads stored breaks and per-day hours back without changing them', async () => {
    const stored = {
      days: [1, 2, 3, 4, 5, 6],
      start: '08:00',
      end: '18:00',
      breaks: [{ start: '12:30', end: '13:30' }],
      by_day: { '6': { start: '09:00', end: '13:00' } },
    };
    serve(stored);
    const field = await editor();
    expect(within(field).getByLabelText('Cumartesi mesai bitişi')).toHaveValue('13:00');
    expect(within(field).queryByLabelText('Cumartesi 1. mola başlangıcı')).not.toBeInTheDocument();
    expect(within(field).getByLabelText('Çarşamba 1. mola bitişi')).toHaveValue('13:30');
    expect(screen.queryByText(/^\d+ değişiklik$/)).not.toBeInTheDocument();
  });
});
