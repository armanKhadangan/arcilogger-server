import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { RecipientsInput } from './recipients-input';

/** The chip input with its own state, as the schedule form holds it. */
function Harness({ initial = [] as string[] }) {
  const [value, setValue] = useState({ recipients: initial, pending: '' });
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <>
      <label htmlFor="to">Alıcılar</label>
      <RecipientsInput
        id="to"
        recipients={value.recipients}
        pending={value.pending}
        onChange={setValue}
        onProblem={setProblem}
        invalid={problem !== null}
      />
      <output data-testid="problem">{problem ?? ''}</output>
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

function chips(): string[] {
  const list = screen.queryByRole('list', { name: 'Alıcılar' });
  return list
    ? within(list)
        .getAllByRole('listitem')
        .map((item) => item.textContent)
    : [];
}

describe('recipients chip input', () => {
  it('Enter, comma and leaving the field make chips; a wrong address stays to be fixed', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole('textbox', { name: 'Alıcılar' });
    await user.type(input, 'tolga@ornek.com.tr{Enter}');
    await user.type(input, 'muhasebe@ornek.com.tr,');
    expect(chips()).toEqual(['tolga@ornek.com.tr', 'muhasebe@ornek.com.tr']);
    expect(input).toHaveValue('');

    await user.type(input, 'ik@ornek{Enter}');
    expect(chips()).toHaveLength(2);
    expect(input).toHaveValue('ik@ornek');
    expect(screen.getByTestId('problem')).toHaveTextContent('validation.email');
    expect(input).toHaveAttribute('aria-invalid', 'true');

    await user.type(input, '.com.tr');
    expect(screen.getByTestId('problem')).toHaveTextContent('');
    await user.tab();
    expect(chips()).toEqual(['tolga@ornek.com.tr', 'muhasebe@ornek.com.tr', 'ik@ornek.com.tr']);
  });

  it('a pasted list becomes one chip per valid address, repeats dropped', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['tolga@ornek.com.tr']} />);
    const input = screen.getByRole('textbox', { name: 'Alıcılar' });
    await user.click(input);
    await user.paste('yonetim@ornek.com.tr; TOLGA@ornek.com.tr\nsatis@ornek.com.tr, hatali');
    expect(chips()).toEqual(['tolga@ornek.com.tr', 'yonetim@ornek.com.tr', 'satis@ornek.com.tr']);
    expect(input).toHaveValue('hatali');
    expect(screen.getByTestId('problem')).toHaveTextContent('validation.email');
  });

  it('Backspace in the empty field and the remove buttons take chips away', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['a@ornek.com.tr', 'b@ornek.com.tr', 'c@ornek.com.tr']} />);
    const input = screen.getByRole('textbox', { name: 'Alıcılar' });
    await user.click(input);
    await user.keyboard('{Backspace}');
    expect(chips()).toEqual(['a@ornek.com.tr', 'b@ornek.com.tr']);
    await user.click(screen.getByRole('button', { name: 'Kaldır: a@ornek.com.tr' }));
    expect(chips()).toEqual(['b@ornek.com.tr']);
    expect(input).toHaveFocus();
  });
});
