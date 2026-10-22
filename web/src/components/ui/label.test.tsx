import { render, screen } from '@testing-library/react';

import { Input } from './input';
import { Field } from './label';

describe('Field', () => {
  it('exposes a required field as required to screen readers (the asterisk is hidden)', () => {
    render(
      <>
        <Field id="authority" label="Makam" required>
          <Input id="authority" />
        </Field>
        <Field id="wan" label="Genel / WAN IP">
          <Input id="wan" />
        </Field>
      </>,
    );
    const required = screen.getByRole('textbox', { name: 'Makam' });
    expect(required).toBeRequired();
    expect(required).toHaveAttribute('aria-required', 'true');
    // Not the native attribute: the forms validate themselves, with Turkish messages.
    expect(required).not.toHaveAttribute('required');
    expect(screen.getByRole('textbox', { name: 'Genel / WAN IP' })).not.toBeRequired();
  });

  it('leaves other children (hints, lists) alone', () => {
    render(
      <Field id="sources" label="Kaynaklar" required>
        <Input id="sources" list="known" />
        <datalist id="known" data-testid="list" />
      </Field>,
    );
    expect(screen.getByRole('combobox', { name: 'Kaynaklar' })).toBeRequired();
    expect(screen.getByTestId('list')).not.toHaveAttribute('aria-required');
  });
});
