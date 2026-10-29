import { act, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import type { Role } from '@/lib/api-types';
import { AuthProvider } from '@/lib/auth';
import { setLanguage } from '@/lib/i18n';
import type { SystemDisk } from '@/lib/system-types';
import { makeMe } from '@/test/api-mock';
import { driveInfo, systemDisk } from '@/test/w4-fixtures';

import { DiskCard } from './disk-card';

/*
 * The Panel's disk card (Wave 4 contract 2.8.3): each drive's used share with icon and text, the
 * forecast in one line, and the link to Sistem by the Panel's link rule (W4-F puts it on the Panel).
 */

function renderCard(disk: SystemDisk = systemDisk(), me = makeMe('admin')) {
  return render(
    <MemoryRouter>
      <AuthProvider me={me}>
        <DiskCard disk={disk} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  act(() => {
    setLanguage('tr');
  });
});

describe('DiskCard', () => {
  it('shows each drive’s used percent with icon and text', () => {
    renderCard();
    const card = screen.getByTestId('card-disk');
    expect(within(card).getByRole('heading', { name: 'Disk' })).toBeInTheDocument();
    const meters = within(card).getAllByRole('meter');
    expect(meters.map((meter) => meter.getAttribute('aria-valuenow'))).toEqual(['54', '84']);
    const drives = within(card).getAllByTestId('drive-meter');
    expect(drives[0]).toHaveTextContent('D:Veri sürücüsü%54 doluOlağan');
    expect(drives[1]).toHaveTextContent('C:Sistem sürücüsü%84 doluUyarı eşiğinin üstünde');
    for (const drive of drives) {
      const badge = within(drive).getByText(/Olağan|Uyarı eşiğinin üstünde/);
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    }
    // The Panel's card leaves the bytes to the screen reader and to the Sistem page.
    expect(card).not.toHaveTextContent('kullanılıyor');
    expect(meters[0]).toHaveAttribute(
      'aria-valuetext',
      expect.stringContaining('kullanılıyor') as string,
    );
  });

  it('gives each drive’s forecast in one line', () => {
    renderCard(
      systemDisk([
        driveInfo('data'),
        driveInfo('system'),
        { ...driveInfo('data', { forecast: 'not_growing' }), path: 'E:' },
      ]),
    );
    expect(screen.getAllByTestId('disk-card-forecast').map((line) => line.textContent)).toEqual([
      'Bu hızla %80 eşiğine 30.12.2026, dolmaya 02.03.2027 (~140 gün).',
      'Tahmin için en az 7 günlük ölçüm gerekiyor (4/7).',
      'Son 30 günde disk kullanımı artmadı.',
    ]);
    act(() => {
      setLanguage('en');
    });
    expect(screen.getAllByTestId('disk-card-forecast')[0]).toHaveTextContent(
      'At this rate the 80% mark is reached on 30.12.2026, full on 02.03.2027 (~140 days).',
    );
    expect(screen.getAllByTestId('drive-meter')[1]).toHaveTextContent('84% used');
  });

  it('marks a critical drive with its own icon and text', () => {
    renderCard(systemDisk([driveInfo('data', { usedPercent: 96 })]));
    expect(screen.getByTestId('drive-meter')).toHaveTextContent('%96 doluKritik');
  });

  it('links to Sistem only for a user who may open it', () => {
    const { unmount } = renderCard();
    const link = within(screen.getByTestId('card-disk')).getByRole('link', {
      name: 'Sistem sayfasını aç',
    });
    expect(link).toHaveAttribute('href', '/system');
    unmount();

    for (const role of ['auditor', 'viewer'] satisfies Role[]) {
      const view = renderCard(systemDisk(), makeMe(role));
      const card = screen.getByTestId('card-disk');
      expect(within(card).queryByRole('link')).toBeNull();
      // The card itself is still shown (contract D13).
      expect(within(card).getAllByRole('meter')).toHaveLength(2);
      view.unmount();
    }
  });

  it('says so when no drive could be read', () => {
    renderCard(systemDisk([]));
    expect(screen.getByTestId('card-disk')).toHaveTextContent('Okunabilen sürücü yok.');
  });
});
