import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { firstFrame } from '@/test/first-frame';
import { renderApp } from '@/test/render';
import { DEVICES } from '@/test/samples';

/*
 * The devices page's dialogs show what they were opened for from their very first frame: the
 * chosen department, person or VLAN for an edit, a fresh form when "Kişiye ata" opens again (#12).
 */

// jsdom has no layout; give the tables' scroll containers a size so the virtualiser renders rows.
const sizes = { clientHeight: 480, clientWidth: 1024, offsetHeight: 480, offsetWidth: 1024 };
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
    else Reflect.deleteProperty(HTMLElement.prototype, key);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The devices API as the samples describe it (read only: no dialog here is submitted). */
function serve() {
  installFetch((request) => {
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, makeMe('admin'));
      case '/api/v1/devices':
        return jsonResponse(200, DEVICES.list);
      case '/api/v1/persons':
        return jsonResponse(200, DEVICES.persons);
      case '/api/v1/departments':
        return jsonResponse(200, DEVICES.departments);
      case '/api/v1/vlans':
        return jsonResponse(200, DEVICES.vlans);
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

/** Opens the row menu `menu`; the returned item opens its dialog with a plain click. */
async function rowAction(menu: string, action: string): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: menu }));
  return screen.findByRole('menuitem', { name: action });
}

function inputValue(dialog: HTMLElement, label: string): string | undefined {
  return within(dialog).queryByLabelText<HTMLInputElement | HTMLTextAreaElement>(label)?.value;
}

describe('Cihazlar ve Kişiler — dialogs from their first frame', () => {
  it('"Departmanı yeniden adlandır" shows the department from its very first frame', async () => {
    serve();
    renderApp('/devices?tab=departments');
    const rename = await screen.findByRole('button', { name: 'Yeniden adlandır: Planlama' });
    const seen = await firstFrame(
      'Departmanı yeniden adlandır',
      () => {
        rename.click();
      },
      (dialog) => inputValue(dialog, 'Departman adı'),
    );
    expect(seen).toEqual(['Planlama']);
  });

  it('"Kişiyi düzenle" shows the person from its very first frame', async () => {
    serve();
    renderApp('/devices?tab=persons');
    const edit = await rowAction('İşlemler: Selin Kaya', 'Düzenle');
    const seen = await firstFrame(
      'Kişiyi düzenle',
      () => {
        edit.click();
      },
      (dialog) => [
        inputValue(dialog, 'Ad soyad'),
        within(dialog).queryByRole('combobox', { name: 'Departman' })?.textContent,
      ],
    );
    expect(seen).toEqual([['Selin Kaya', 'Planlama']]);
  });

  it('"VLAN\'ı düzenle" shows the VLAN from its very first frame', async () => {
    serve();
    renderApp('/devices?tab=departments');
    const edit = await rowAction('İşlemler: VLAN 42', 'Düzenle');
    const seen = await firstFrame(
      "VLAN'ı düzenle",
      () => {
        edit.click();
      },
      (dialog) => [inputValue(dialog, 'Ağ'), inputValue(dialog, 'Ad')],
    );
    expect(seen).toEqual([['172.20.42.0/24', 'Planlama']]);
  });

  it('"Kişiye ata" opens again with a fresh form', async () => {
    serve();
    renderApp('/devices');
    const user = userEvent.setup();
    const table = await screen.findByRole('table', { name: 'Ağda görülen cihazlar' });
    await user.click(
      await within(table).findByRole('checkbox', { name: 'Seç: 3c:52:82:00:00:05' }),
    );
    const assign = within(screen.getByTestId('bulk-bar')).getByRole('button', {
      name: 'Kişiye ata',
    });
    await user.click(assign);
    const dialog = await screen.findByRole('dialog', { name: 'Kişiye ata' });
    await user.click(within(dialog).getByRole('radio', { name: /Belirli bir zamandan itibaren/ }));
    await user.type(within(dialog).getByLabelText('Not'), 'Zimmet formu');
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));

    const seen = await firstFrame(
      'Kişiye ata',
      () => {
        assign.click();
      },
      (opened) => [
        opened.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.value,
        inputValue(opened, 'Not'),
      ],
    );
    expect(seen).toEqual([['now', '']]);
  });
});
