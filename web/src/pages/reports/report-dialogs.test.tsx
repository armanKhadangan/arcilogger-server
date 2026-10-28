import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { firstFrame } from '@/test/first-frame';
import { renderApp } from '@/test/render';
import { licenseSample, REPORTS } from '@/test/samples';

/*
 * The reports page's dialogs show what they were opened for from their very first frame: the
 * chosen schedule's values for "Düzenle", an empty form for "Yeni zamanlama" after an edit, and
 * the schedule's own period (and sending to its recipients) when "Önizle" and "Şimdi çalıştır"
 * open again (#12).
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

const FULL_DAY = 'Tam gün raporu';

function serve() {
  installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
    if (request.path === '/api/v1/license') return jsonResponse(200, licenseSample('licensed'));
    if (request.path === '/api/v1/reports/schedules') return jsonResponse(200, REPORTS.schedules);
    if (request.path === '/api/v1/reports/runs') return jsonResponse(200, REPORTS.runs);
    return errorResponse(404, 'not_found');
  });
}

/** Opens the row menu of `name`; the returned item opens its dialog with a plain click. */
async function rowAction(name: string, action: string): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: `İşlemler: ${name}` }));
  return screen.findByRole('menuitem', { name: action });
}

function nameValue(dialog: HTMLElement): string | undefined {
  return within(dialog).queryByRole<HTMLInputElement>('textbox', { name: 'Ad' })?.value;
}

/** Whether "Özel dönem" is chosen, and its start day when it is shown. */
function period(dialog: HTMLElement): [boolean | undefined, string | undefined] {
  return [
    within(dialog).queryByRole<HTMLInputElement>('radio', { name: /^Özel dönem/ })?.checked,
    within(dialog).queryByLabelText<HTMLInputElement>('Başlangıç günü')?.value,
  ];
}

/** Chooses "Özel dönem" and types a start day in the open dialog `name`; returns its `close`. */
async function chooseCustomPeriod(name: string, close: string) {
  const user = userEvent.setup();
  const dialog = await screen.findByRole('dialog', { name });
  await user.click(within(dialog).getByRole('radio', { name: /^Özel dönem/ }));
  await user.type(within(dialog).getByLabelText('Başlangıç günü'), '12.10.2026');
  return { dialog, close: within(dialog).getByRole('button', { name: close }) };
}

describe('Raporlar — dialogs from their first frame', () => {
  it('"Düzenle" shows the chosen schedule from its very first frame', async () => {
    serve();
    renderApp('/reports');
    const edit = await rowAction(FULL_DAY, 'Düzenle');
    const seen = await firstFrame(
      'Zamanlanmış raporu düzenle',
      () => {
        edit.click();
      },
      nameValue,
    );
    expect(seen).toEqual([FULL_DAY]);
  });

  it('"Yeni zamanlama" opens with an empty form after an edit', async () => {
    serve();
    renderApp('/reports');
    const user = userEvent.setup();
    await user.click(await rowAction(FULL_DAY, 'Düzenle'));
    const dialog = await screen.findByRole('dialog', { name: 'Zamanlanmış raporu düzenle' });
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));

    const add = screen.getByRole('button', { name: 'Yeni zamanlama' });
    const seen = await firstFrame(
      'Yeni zamanlanmış rapor',
      () => {
        add.click();
      },
      nameValue,
    );
    expect(seen).toEqual(['']);
  });

  it('"Önizle" opens again with the schedule\'s own period', async () => {
    serve();
    renderApp('/reports');
    const title = `Önizle: ${FULL_DAY}`;
    const user = userEvent.setup();
    await user.click(await rowAction(FULL_DAY, 'Önizle'));
    const { close } = await chooseCustomPeriod(title, 'Kapat');
    await user.click(close);
    expect(screen.queryByRole('dialog', { name: title })).not.toBeInTheDocument();

    const preview = await rowAction(FULL_DAY, 'Önizle');
    const seen = await firstFrame(
      title,
      () => {
        preview.click();
      },
      period,
    );
    expect(seen).toEqual([[false, undefined]]);
  });

  it('"Şimdi çalıştır" opens again with its own period and sending on', async () => {
    serve();
    renderApp('/reports');
    const title = 'Şimdi çalıştır';
    const user = userEvent.setup();
    await user.click(await rowAction(FULL_DAY, title));
    const { dialog, close } = await chooseCustomPeriod(title, 'İptal');
    await user.click(within(dialog).getByRole('checkbox', { name: /e-postayla gönder/ }));
    await user.click(close);
    expect(screen.queryByRole('dialog', { name: title })).not.toBeInTheDocument();

    const run = await rowAction(FULL_DAY, title);
    const seen = await firstFrame(
      title,
      () => {
        run.click();
      },
      (opened) => [
        ...period(opened),
        within(opened)
          .queryByRole('checkbox', { name: /e-postayla gönder/ })
          ?.getAttribute('aria-checked'),
      ],
    );
    expect(seen).toEqual([[false, undefined, 'true']]);
  });
});
