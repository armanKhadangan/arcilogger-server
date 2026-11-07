import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Field } from './label';
import { PasswordInput } from './password-input';

function renderField() {
  render(
    <Field id="pw" label="Parola" required>
      <PasswordInput id="pw" autoComplete="current-password" />
    </Field>,
  );
  return screen.getByLabelText('Parola');
}

describe('PasswordInput', () => {
  it('hides the password until "Parolayı göster" is pressed, and keeps the field required', async () => {
    const user = userEvent.setup();
    const field = renderField();
    expect(field).toHaveAttribute('type', 'password');
    expect(field).toHaveAttribute('aria-required', 'true');
    const show = screen.getByRole('button', { name: 'Parolayı göster' });
    expect(show).toHaveAttribute('aria-pressed', 'false');
    expect(show).toHaveAttribute('aria-controls', 'pw');
    await user.click(show);
    expect(field).toHaveAttribute('type', 'text');
    expect(show).toHaveAttribute('aria-pressed', 'true');
    await user.click(show);
    expect(field).toHaveAttribute('type', 'password');
  });

  it('says "Caps Lock açık" while Caps Lock is on, until the field is left', () => {
    const field = renderField();
    const status = screen.getByRole('status');
    expect(status).toBeEmptyDOMElement();
    fireEvent.keyDown(field, { key: 'A', modifierCapsLock: true });
    expect(status).toHaveTextContent('Caps Lock açık');
    fireEvent.keyUp(field, { key: 'CapsLock', modifierCapsLock: false });
    expect(status).toBeEmptyDOMElement();
    fireEvent.keyDown(field, { key: 'B', modifierCapsLock: true });
    expect(status).toHaveTextContent('Caps Lock açık');
    fireEvent.blur(field);
    expect(status).toBeEmptyDOMElement();
  });
});
