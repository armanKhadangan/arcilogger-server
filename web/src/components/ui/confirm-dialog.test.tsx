import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { ConfirmDialog } from './confirm-dialog';

/** A confirmation whose request stays running (busy) until the test says otherwise. */
function Confirmation({ onConfirm }: { onConfirm: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <ConfirmDialog
      open
      onOpenChange={() => undefined}
      title="Kullanıcıyı devre dışı bırak"
      description="Ayşe Yılmaz artık giriş yapamaz."
      confirmLabel="Devre dışı bırak"
      danger
      busy={busy}
      onConfirm={() => {
        onConfirm();
        setBusy(true);
      }}
    />
  );
}

describe('ConfirmDialog', () => {
  it('keeps keyboard focus on the confirm button while its request runs', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Confirmation onConfirm={onConfirm} />);
    const dialog = await screen.findByRole('dialog', { name: 'Kullanıcıyı devre dışı bırak' });
    const confirm = screen.getByRole('button', { name: 'Devre dışı bırak' });
    confirm.focus();
    await user.keyboard('{Enter}');
    expect(onConfirm).toHaveBeenCalledTimes(1);

    // Busy: looks disabled and says so, but is not `disabled` (that drops the focus to <body>).
    const busy = screen.getByRole('button', { name: 'İşleniyor…' });
    expect(busy).toBe(confirm);
    expect(busy).toHaveAttribute('aria-disabled', 'true');
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    expect(dialog).toContainElement(busy);
    await user.keyboard('{Enter}');
    await user.click(busy);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
