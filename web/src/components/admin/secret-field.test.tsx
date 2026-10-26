import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { TFunction } from 'i18next';
import { useState } from 'react';

import i18n from '@/lib/i18n';
import type { SecretMeta } from '@/lib/settings-types';

import { pathLabel } from './labels';
import { SECRET_KEPT, secretChanged, type SecretEdit } from './secret-edit';
import { SecretField } from './secret-field';

/** The field with its edit state, as a form keeps it; `seen` records every change. */
function Form({
  meta,
  allowClear = false,
  seen,
}: {
  meta: SecretMeta | undefined;
  allowClear?: boolean;
  seen: SecretEdit[];
}) {
  const [edit, setEdit] = useState<SecretEdit>(SECRET_KEPT);
  return (
    <SecretField
      id="customer"
      label="Müşteri numarası"
      meta={meta}
      edit={edit}
      allowClear={allowClear}
      onChange={(next) => {
        seen.push(next);
        setEdit(next);
      }}
    />
  );
}

const STORED: SecretMeta = {
  set: true,
  changed_at: '2026-09-30T09:12:00+03:00',
  changed_by: 'web:ayse',
};

describe('SecretField (contract 2.17 "Field states")', () => {
  it('a stored secret is never shown nor pre-filled: "Kayıtlı" and who changed it', () => {
    render(<Form meta={STORED} seen={[]} />);
    expect(screen.getByText('Kayıtlı')).toBeInTheDocument();
    expect(screen.getByText(/web: ayse/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(document.querySelector('input')).toBeNull();
    expect(screen.getByRole('button', { name: 'Değiştir' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sil' })).toBeNull();
  });

  it('"Değiştir" opens an empty password field; "Vazgeç" keeps the stored value', async () => {
    const user = userEvent.setup();
    const seen: SecretEdit[] = [];
    render(<Form meta={STORED} seen={seen} />);
    await user.click(screen.getByRole('button', { name: 'Değiştir' }));
    const input = screen.getByLabelText('Müşteri numarası');
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('type', 'password');
    expect(input).toHaveAttribute('autocomplete', 'new-password');
    await user.type(input, '100200300');
    expect(seen.at(-1)).toEqual({ value: '100200300', editing: true, clear: false });
    expect(secretChanged(seen.at(-1) ?? SECRET_KEPT)).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Vazgeç' }));
    expect(seen.at(-1)).toEqual(SECRET_KEPT);
    expect(document.querySelector('input')).toBeNull();
  });

  it('"Sil" only where allowed and only for a stored secret', async () => {
    const user = userEvent.setup();
    const seen: SecretEdit[] = [];
    const { unmount } = render(<Form meta={STORED} allowClear seen={seen} />);
    await user.click(screen.getByRole('button', { name: 'Sil' }));
    expect(screen.getByText('Kaydedince silinecek')).toBeInTheDocument();
    expect(seen.at(-1)).toEqual({ value: '', editing: false, clear: true });
    unmount();
    render(<Form meta={{ set: false, changed_at: null, changed_by: null }} allowClear seen={[]} />);
    expect(screen.getByText('Kayıtlı değil')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gir' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sil' })).toBeNull();
  });
});

describe('labels of change paths', () => {
  const t = i18n.t.bind(i18n) as TFunction;

  it('names a source and its field, else the configuration path', () => {
    expect(pathLabel('sources.core-r1.heartbeat_minutes', t)).toBe('core-r1 · Sinyal aralığı');
    expect(pathLabel('sources.ap-1', t)).toBe('Kaynak ap-1');
    expect(pathLabel('kamusm.customer', t)).toBe('Kamu SM müşteri numarası');
    expect(pathLabel('tsa.server', t)).toBe('Zaman damgası sunucusu');
    expect(pathLabel('tsa.unknown_key', t)).toBe('tsa.unknown_key');
  });
});
