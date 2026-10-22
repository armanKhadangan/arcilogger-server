import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SealCalendar } from './seal-calendar';

describe('SealCalendar', () => {
  const today = new Date(2026, 9, 12, 18, 0);

  it('labels days with their status and disables future days', () => {
    render(
      <SealCalendar
        month={new Date(2026, 9, 1)}
        days={{ '2026-10-11': 'sealed', '2026-10-07': 'failed' }}
        today={today}
      />,
    );
    expect(
      screen.getByRole('button', { name: '11 Ekim 2026: Mühürlendi ve doğrulandı' }),
    ).toBeEnabled();
    expect(screen.getByRole('button', { name: '7 Ekim 2026: Başarısız' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '20 Ekim 2026: Veri yok' })).toBeDisabled();
    expect(screen.getByText('Ekim 2026')).toBeInTheDocument();
  });

  it('reports the selected day and month navigation', async () => {
    const onSelect = vi.fn();
    const onMonthChange = vi.fn();
    render(
      <SealCalendar
        month={new Date(2026, 9, 1)}
        days={{}}
        today={today}
        onSelect={onSelect}
        onMonthChange={onMonthChange}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: '3 Ekim 2026: Veri yok' }));
    expect(onSelect).toHaveBeenCalledWith('2026-10-03');
    await userEvent.click(screen.getByRole('button', { name: 'Önceki ay' }));
    expect(onMonthChange).toHaveBeenCalledWith(new Date(2026, 8, 1));
  });
});
