import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom';

import { setLanguage } from '@/lib/i18n';

import { SettingsFrame } from './settings-frame';

/*
 * The way back of a settings page (Wave 4 contract 2.8.4): to Ayarlar, or, opened from the
 * first-run wizard (`wizard=1`), to the wizard — also after a link of the page that replaces the
 * whole query (the Kamu SM steps), because wizard mode is kept for the tab. Without storage
 * only the URL counts.
 */

function Page() {
  return (
    <SettingsFrame title="Kamu SM" machine={false}>
      <Link to={{ search: '?step=production' }}>Üretime geç</Link>
    </SettingsFrame>
  );
}

function renderFrame(path: string) {
  const router = createMemoryRouter(
    [
      { path: '/settings/kamusm', element: <Page /> },
      { path: '/settings/setup', element: <Page /> },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const back = () => screen.getByTestId('settings-back');

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
  act(() => {
    setLanguage('tr');
  });
});

describe('SettingsFrame — the way back', () => {
  it('leads to Ayarlar without wizard=1', () => {
    renderFrame('/settings/kamusm?step=test');
    expect(back()).toHaveTextContent('Ayarlar');
    expect(back()).toHaveAttribute('href', '/settings');
  });

  it('leads to the wizard with wizard=1, in both languages', () => {
    renderFrame('/settings/kamusm?step=test&wizard=1');
    expect(back()).toHaveTextContent('Kurulum sihirbazına dön');
    expect(back()).toHaveAttribute('href', '/settings/setup');
    act(() => {
      setLanguage('en');
    });
    expect(back()).toHaveTextContent('Back to the first-run wizard');
  });

  it('keeps leading to the wizard after a link that drops the query', async () => {
    const router = renderFrame('/settings/kamusm?step=test&wizard=1');
    await userEvent.click(screen.getByRole('link', { name: 'Üretime geç' }));
    expect(router.state.location.search).toBe('?step=production');
    expect(back()).toHaveTextContent('Kurulum sihirbazına dön');
    expect(back()).toHaveAttribute('href', '/settings/setup');
  });

  it('is kept for this tab only: a page opened later in the tab leads to the wizard too', () => {
    window.sessionStorage.setItem('arci.setupWizard', '1');
    renderFrame('/settings/kamusm');
    expect(back()).toHaveAttribute('href', '/settings/setup');
  });

  it('on the wizard page itself leads to Ayarlar', () => {
    window.sessionStorage.setItem('arci.setupWizard', '1');
    renderFrame('/settings/setup?wizard=1');
    expect(back()).toHaveAttribute('href', '/settings');
  });

  it('on the wizard page wizard=1 does not start wizard mode', () => {
    renderFrame('/settings/setup?wizard=1');
    expect(back()).toHaveAttribute('href', '/settings');
    expect(window.sessionStorage.getItem('arci.setupWizard')).toBeNull();
  });

  it('without storage only the URL counts', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    renderFrame('/settings/kamusm?step=test&wizard=1');
    expect(back()).toHaveAttribute('href', '/settings/setup');
    await userEvent.click(screen.getByRole('link', { name: 'Üretime geç' }));
    expect(back()).toHaveTextContent('Ayarlar');
    expect(back()).toHaveAttribute('href', '/settings');
  });
});
