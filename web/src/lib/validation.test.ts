import { waitFor } from '@testing-library/react';

import {
  codePointLength,
  focusFirstError,
  hasErrors,
  normalizeRecoveryCode,
  normalizeTotp,
  normalizeUsername,
  validateEmail,
  validateDisplayName,
  validateLogin,
  validateNewPassword,
  validateSecondFactor,
  validateSetup,
  validateUserForm,
} from './validation';

describe('normalizeUsername (same rule as the server)', () => {
  it.each([
    ['  Tolga ', 'tolga'],
    ['ayse.yilmaz', 'ayse.yilmaz'],
    ['a1', 'a1'],
    ['x_y-z.0', 'x_y-z.0'],
  ])('%j → %j', (input, expected) => {
    expect(normalizeUsername(input)).toBe(expected);
  });

  it.each(['a', '.tolga', '-x', 'ayşe', 'TOLGA!', 'a b', 'x'.repeat(65), 'ı'.repeat(3)])(
    'refuses %j',
    (input) => {
      expect(normalizeUsername(input)).toBeNull();
    },
  );
});

describe('second factor', () => {
  it('accepts six ASCII digits, spaces ignored', () => {
    expect(normalizeTotp('123 456')).toBe('123456');
    expect(normalizeTotp(' 000000 ')).toBe('000000');
  });

  it.each(['12345', '1234567', '١٢٣٤٥٦', '１２３４５６', '12345a'])('refuses %j', (input) => {
    expect(normalizeTotp(input)).toBeNull();
  });

  it('reads recovery codes like the server (case, dashes, i/l/o look-alikes)', () => {
    expect(normalizeRecoveryCode('ABCD-EFGH-JKMN-PQRS')).toBe('abcdefghjkmnpqrs');
    expect(normalizeRecoveryCode('0o1i l234 5678 9abc')).toBe('0011123456789abc');
    expect(normalizeRecoveryCode('abcd-efgh-jkmn-pqr')).toBeNull(); // 15 characters
    expect(normalizeRecoveryCode('uuuu-uuuu-uuuu-uuuu')).toBeNull(); // u is not Crockford
  });

  it('validates the field for each mode', () => {
    expect(validateSecondFactor('', 'totp')).toEqual({ code: 'validation.required' });
    expect(validateSecondFactor('12', 'totp')).toEqual({ code: 'validation.totpFormat' });
    expect(validateSecondFactor('123456', 'totp')).toEqual({});
    expect(validateSecondFactor('123456', 'recovery')).toEqual({
      code: 'validation.recoveryFormat',
    });
    expect(validateSecondFactor('abcd-efgh-jkmn-pqrs', 'recovery')).toEqual({});
  });
});

describe('login form', () => {
  it('requires both fields but does not judge the password', () => {
    expect(validateLogin({ username: ' ', password: '' })).toEqual({
      username: 'validation.required',
      password: 'validation.required',
    });
    expect(validateLogin({ username: 'tolga', password: 'x' })).toEqual({});
  });
});

describe('new password', () => {
  it('counts code points like Python len()', () => {
    expect(codePointLength('şifre😀')).toBe(6);
    expect('şifre😀'.length).toBe(7);
  });

  it('needs at least 12 characters (spec 13) and a matching confirmation', () => {
    expect(validateNewPassword({ password: 'kısa', confirm: 'kısa' })).toEqual({
      password: 'validation.passwordTooShort',
    });
    expect(
      validateNewPassword({ password: 'çok uzun parola 1', confirm: 'çok uzun parola 2' }),
    ).toEqual({
      confirm: 'validation.passwordMismatch',
    });
    expect(validateNewPassword({ password: 'a'.repeat(257), confirm: 'a'.repeat(257) })).toEqual({
      password: 'validation.passwordTooLong',
    });
    expect(validateNewPassword({ password: '😀'.repeat(12), confirm: '😀'.repeat(12) })).toEqual(
      {},
    );
  });

  it('wants the current password when asked and a different new one', () => {
    expect(
      validateNewPassword({
        current: '',
        password: 'yeni parola 2026',
        confirm: 'yeni parola 2026',
      }),
    ).toEqual({ current: 'validation.required' });
    expect(
      validateNewPassword({
        current: 'aynı parola 2026',
        password: 'aynı parola 2026',
        confirm: 'aynı parola 2026',
      }),
    ).toEqual({ password: 'validation.passwordReused' });
  });
});

describe('user and setup forms', () => {
  it('checks names and e-mail like the API schemas', () => {
    expect(validateDisplayName('  ')).toBe('validation.required');
    expect(validateDisplayName('Ayşe\u202eYılmaz')).toBe('validation.controlCharacters');
    expect(validateDisplayName('x'.repeat(201))).toBe('validation.tooLong');
    expect(validateDisplayName('Ayşe Yılmaz')).toBeUndefined();
    expect(validateEmail('')).toBeUndefined();
    expect(validateEmail('ayşe@örnek.com.tr')).toBeUndefined();
    expect(validateEmail('ayse@ornek')).toBe('validation.email');
    expect(validateEmail('ay\u0000se@ornek.com')).toBe('validation.email');
  });

  it('collects every problem of the create-user form', () => {
    const errors = validateUserForm({ username: 'Ayşe', displayName: '', email: 'x', role: '' });
    expect(errors).toEqual({
      username: 'validation.username',
      displayName: 'validation.required',
      email: 'validation.email',
      role: 'validation.required',
    });
    expect(hasErrors(errors)).toBe(true);
    expect(
      hasErrors(validateUserForm({ username: 'ayse', displayName: 'Ayşe', email: '', role: 'hr' })),
    ).toBe(false);
  });

  it('checks the setup form including the password rules', () => {
    expect(
      validateSetup({
        token: '',
        username: 'tolga',
        displayName: 'Tolga',
        password: 'x',
        confirm: 'y',
      }),
    ).toEqual({
      token: 'validation.required',
      password: 'validation.passwordTooShort',
      confirm: 'validation.passwordMismatch',
    });
  });
});

describe('focusFirstError', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('focuses the first control with a problem in page order, whatever the key order', async () => {
    document.body.innerHTML =
      '<input id="f-username"><input id="f-new"><input id="f-confirm"><button>Kaydet</button>';
    focusFirstError({ confirm: 'x', password: 'y', username: undefined }, (field) =>
      field === 'password' ? 'f-new' : `f-${field}`,
    );
    await waitFor(() => {
      expect(document.activeElement?.id).toBe('f-new');
    });
  });

  it('does nothing without problems or controls', async () => {
    document.body.innerHTML = '<button id="b">Kaydet</button>';
    const button = document.getElementById('b') as HTMLButtonElement;
    button.focus();
    focusFirstError({}, (field) => field);
    focusFirstError({ missing: 'x' }, (field) => field);
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    expect(document.activeElement).toBe(button);
  });
});
