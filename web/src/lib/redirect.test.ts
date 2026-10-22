import { loginUrl, reasonFor, safeNext } from './redirect';

describe('safeNext', () => {
  it.each([
    ['/audit', '/audit'],
    ['/audit?actor=tolga#x', '/audit?actor=tolga#x'],
    ['/settings/users', '/settings/users'],
  ])('keeps the same-origin path %j', (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });

  it.each([
    null,
    '',
    'audit',
    'ftp://evil.example/',
    '//evil.example/x',
    '/\\evil.example',
    'javascript:alert(1)',
    '/audit\n',
    '/login',
    '/login?next=/audit',
    '/setup',
    '/setup/x',
  ])('falls back to / for %j', (input) => {
    expect(safeNext(input)).toBe('/');
  });
});

describe('loginUrl', () => {
  it('keeps target and reason, drops the default target', () => {
    expect(loginUrl('/audit?x=1', 'session_idle')).toBe(
      '/login?next=%2Faudit%3Fx%3D1&reason=session_idle',
    );
    expect(loginUrl('/', null)).toBe('/login');
    expect(loginUrl('//evil.example', 'session_expired')).toBe('/login?reason=session_expired');
  });

  it('explains only the codes the sign-in page knows', () => {
    expect(reasonFor('session_idle')).toBe('session_idle');
    expect(reasonFor('account_locked')).toBe('account_locked');
    expect(reasonFor('not_authenticated')).toBeNull();
    expect(reasonFor('<script>')).toBeNull();
  });
});
