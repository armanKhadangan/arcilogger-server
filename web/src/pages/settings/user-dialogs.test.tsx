import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UserOut } from '@/lib/api-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { firstFrame } from '@/test/first-frame';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';

/*
 * The users page's dialogs show what they were opened for from their very first frame: an empty
 * form when "Kullanıcı ekle" opens again, the chosen account's values for "Düzenle" and "Rolü
 * değiştir" (#12).
 */

// jsdom has no layout; give the table's scroll container a size so the virtualiser renders rows.
const sizes = { offsetHeight: 480, offsetWidth: 1024 };
const originals = Object.fromEntries(
  Object.keys(sizes).map((key) => [
    key,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, key),
  ]),
);
beforeAll(() => {
  for (const [key, value] of Object.entries(sizes))
    Object.defineProperty(HTMLElement.prototype, key, { configurable: true, get: () => value });
});
afterAll(() => {
  for (const [key, descriptor] of Object.entries(originals))
    if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function account(id: number, username: string, name: string, role: string, label: string): UserOut {
  return {
    id,
    username,
    display_name: name,
    role,
    role_label_tr: label,
    role_label_en: label,
    email: `${username}@example.com`,
    active: true,
    must_change_password: false,
    totp_enabled: false,
    locked: false,
    locked_until: null,
    failed_attempts: 0,
    last_login: null,
    password_changed_at: null,
    created_at: '2026-10-01T08:00:00Z',
  };
}

const USERS = [
  account(1, 'tolga', 'Tolga Yılmaz', 'admin', 'Yönetici'),
  account(2, 'selin', 'Selin Kaya', 'auditor', 'Denetçi'),
];

function serve() {
  installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/license') return jsonResponse(200, licenseSample('licensed'));
    if (request.path === '/api/v1/users')
      return jsonResponse(200, { items: USERS, next_cursor: null });
    return errorResponse(404, 'not_found');
  });
}

/** Opens the row menu of `name`; the returned item opens its dialog with a plain click. */
async function rowAction(name: string, action: string): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: `İşlemler: ${name}` }));
  return screen.findByRole('menuitem', { name: action });
}

function inputValue(dialog: HTMLElement, label: string): string | undefined {
  return within(dialog).queryByLabelText<HTMLInputElement>(label, { exact: false })?.value;
}

describe('Kullanıcılar — dialogs from their first frame', () => {
  it('"Kullanıcı ekle" opens again with an empty form', async () => {
    serve();
    renderApp('/settings/users');
    const user = userEvent.setup();
    const add = await screen.findByRole('button', { name: 'Kullanıcı ekle' });
    await user.click(add);
    let dialog = await screen.findByRole('dialog', { name: 'Kullanıcı ekle' });
    await user.type(within(dialog).getByLabelText('Kullanıcı adı', { exact: false }), 'deniz');
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));

    const seen = await firstFrame(
      'Kullanıcı ekle',
      () => {
        add.click();
      },
      (opened) => inputValue(opened, 'Kullanıcı adı'),
    );
    expect(seen).toEqual(['']);
    dialog = screen.getByRole('dialog', { name: 'Kullanıcı ekle' });
    expect(inputValue(dialog, 'Kullanıcı adı')).toBe('');
  });

  it('"Düzenle" shows the chosen account from its very first frame', async () => {
    serve();
    renderApp('/settings/users');
    const edit = await rowAction('Selin Kaya', 'Düzenle');
    const seen = await firstFrame(
      'Kullanıcıyı düzenle',
      () => {
        edit.click();
      },
      (dialog) => [inputValue(dialog, 'Ad soyad'), inputValue(dialog, 'E-posta')],
    );
    expect(seen).toEqual([['Selin Kaya', 'selin@example.com']]);
  });

  it('"Rolü değiştir" shows the account\'s role from its very first frame', async () => {
    serve();
    renderApp('/settings/users');
    const role = await rowAction('Selin Kaya', 'Rolü değiştir');
    const seen = await firstFrame(
      'Rolü değiştir',
      () => {
        role.click();
      },
      (dialog) => within(dialog).queryByRole('combobox')?.textContent,
    );
    expect(seen).toEqual(['Denetçi']);
  });
});
