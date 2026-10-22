import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { Button } from './button';

/** A button that starts a "request" and stays busy until `finish` is clicked. */
function Request({ onRun }: { onRun: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Button
        busy={busy}
        onClick={() => {
          onRun();
          setBusy(true);
        }}
      >
        {busy ? 'Hazırlanıyor…' : 'Rapor'}
      </Button>
      <button
        type="button"
        onClick={() => {
          setBusy(false);
        }}
      >
        finish
      </button>
    </>
  );
}

describe('Button busy', () => {
  it('keeps keyboard focus while busy and ignores further activations', async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    render(<Request onRun={onRun} />);
    const button = screen.getByRole('button', { name: 'Rapor' });
    button.focus();
    await user.keyboard('{Enter}');
    const busy = screen.getByRole('button', { name: 'Hazırlanıyor…' });
    expect(busy).toBe(button);
    expect(busy).toHaveFocus();
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    expect(busy).not.toHaveAttribute('disabled');
    await user.keyboard('{Enter}');
    await user.keyboard(' ');
    fireEvent.click(busy);
    expect(onRun).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'finish' }));
    expect(button).not.toHaveAttribute('aria-disabled');
    fireEvent.click(button);
    expect(onRun).toHaveBeenCalledTimes(2);
  });

  it('a busy submit button does not submit its form', () => {
    const onSubmit = vi.fn((event: React.FormEvent) => {
      event.preventDefault();
    });
    const { rerender } = render(
      <form onSubmit={onSubmit}>
        <Button type="submit" busy>
          Giriş yap
        </Button>
      </form>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(onSubmit).not.toHaveBeenCalled();
    rerender(
      <form onSubmit={onSubmit}>
        <Button type="submit">Giriş yap</Button>
      </form>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Giriş yap' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('a disabled (unavailable) button stays disabled', () => {
    render(<Button disabled>Doğrula</Button>);
    expect(screen.getByRole('button', { name: 'Doğrula' })).toBeDisabled();
  });
});
