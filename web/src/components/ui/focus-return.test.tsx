import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useRef, useState } from 'react';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from './dialog';
import { focusBack } from './focus-return';

const hosts: HTMLElement[] = [];

function mount(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  document.body.append(host);
  hosts.push(host);
  return host;
}

afterEach(() => {
  for (const host of hosts.splice(0)) host.remove();
});

describe('focusBack', () => {
  it('has nothing to do without an element', () => {
    expect(focusBack(null)).toBe(false);
    expect(focusBack(undefined)).toBe(false);
  });

  it('focuses a connected element', () => {
    const host = mount('<button id="a">A</button>');
    const button = host.querySelector('button');
    expect(focusBack(button)).toBe(true);
    expect(document.activeElement).toBe(button);
  });

  it('finds a re-rendered element by its id', () => {
    const host = mount('<button id="row-7-actions">İşlemler</button>');
    const old = host.querySelector('button');
    host.innerHTML = '<button id="row-7-actions">İşlemler</button>';
    const fresh = host.querySelector('button');
    expect(old?.isConnected).toBe(false);
    expect(focusBack(old)).toBe(true);
    expect(document.activeElement).toBe(fresh);
  });

  it('gives up on a removed element without an id', () => {
    const host = mount('<button>A</button>');
    const old = host.querySelector('button');
    host.innerHTML = '';
    expect(focusBack(old)).toBe(false);
  });

  it('keeps the focus in the dialog of a busy (disabled) button', () => {
    const host = mount(
      '<div role="dialog" tabindex="-1"><button disabled>Kullanıcıyı oluştur</button></div>',
    );
    expect(focusBack(host.querySelector('button'))).toBe(true);
    expect(document.activeElement).toBe(host.querySelector('[role="dialog"]'));
  });

  it('reports a disabled button outside any dialog', () => {
    const host = mount('<button disabled>A</button>');
    expect(focusBack(host.querySelector('button'))).toBe(false);
  });
});

/** A dialog opened from state (no `Dialog.Trigger`), like the users and audit pages do. */
function Harness({ removeOpener = false }: { removeOpener?: boolean }) {
  const [open, setOpen] = useState(false);
  const fallback = useRef<HTMLButtonElement>(null);
  return (
    <>
      {removeOpener && open ? null : (
        <button
          onClick={() => {
            setOpen(true);
          }}
        >
          Aç
        </button>
      )}
      <button ref={fallback}>Yedek</button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent returnFocus={removeOpener ? fallback : undefined}>
          <DialogTitle>Başlık</DialogTitle>
          <DialogDescription>Açıklama</DialogDescription>
          <input aria-label="Alan" autoFocus />
        </DialogContent>
      </Dialog>
    </>
  );
}

describe('DialogContent focus return', () => {
  it('gives the focus back to the element that opened it, despite autoFocus inside', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Alan' })).toHaveFocus();
    });
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Aç' })).toHaveFocus();
    });
  });

  it('uses returnFocus when the opener is gone', async () => {
    const user = userEvent.setup();
    render(<Harness removeOpener />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Yedek' })).toHaveFocus();
    });
  });
});
