import { leaseRangeErrors, siteToday } from './lease-range';
import { validateEmail, validateReplicaUser, validateUnc } from './settings-validation';

describe('browser checks of the SMTP, replica and lease rebuild forms (contract 2.6, 2.9)', () => {
  it('e-mail addresses as `EMAIL_ADDRESS` of the core', () => {
    expect(validateEmail('arcilogger@ornek.example', false)).toBeUndefined();
    expect(validateEmail('', true)).toBeUndefined();
    expect(validateEmail('', false)).toBe('validation.required');
    expect(validateEmail('arcilogger@ornek', false)).toBe('admin.errors.emailInvalid');
    expect(validateEmail('a b@ornek.example', false)).toBe('admin.errors.emailInvalid');
    expect(validateEmail(`${'a'.repeat(65)}@ornek.example`, false)).toBe(
      'admin.errors.emailInvalid',
    );
  });

  it('the replica target is \\\\host\\share[\\path], nothing else', () => {
    for (const ok of [
      '\\\\yedek-sunucu\\arcilogger-worm',
      '\\\\192.0.2.10\\worm$',
      '\\\\yedek.ornek.example\\arcilogger\\gunluk',
    ])
      expect(validateUnc(ok), ok).toBeUndefined();
    expect(validateUnc('')).toBe('validation.required');
    for (const bad of [
      'C:\\yedek',
      '\\\\?\\C:\\yedek',
      '\\\\.\\pipe\\x',
      '\\\\yedek-sunucu',
      '\\\\yedek-sunucu\\pay laşım',
      '\\\\yedek-sunucu\\worm\\..\\gizli',
      '\\\\yedek-sunucu\\worm\\a:b',
      '//yedek-sunucu/worm',
    ])
      expect(validateUnc(bad), bad).toBe('settings.replica.errors.target');
  });

  it('the replica user: SERVER\\user, DOMAIN\\user or user@domain', () => {
    expect(validateReplicaUser('YEDEK\\arcireplica')).toBeUndefined();
    expect(validateReplicaUser('arcireplica@ornek.example')).toBeUndefined();
    expect(validateReplicaUser('arcireplica')).toBe('settings.replica.errors.user');
    expect(validateReplicaUser('a\\b\\c')).toBe('settings.replica.errors.user');
    expect(validateReplicaUser('')).toBe('validation.required');
  });

  it('the days of a lease rebuild: typed in the site format, at most 31, none in the future', () => {
    const today = '2026-10-26';
    expect(leaseRangeErrors('20.10.2026', '25.10.2026', today)).toEqual({});
    expect(leaseRangeErrors('26.09.2026', '26.10.2026', today)).toEqual({});
    expect(leaseRangeErrors('25.09.2026', '26.10.2026', today)).toEqual({
      to: 'settings.leaseRebuild.errors.tooLong',
    });
    expect(leaseRangeErrors('20.10.2026', '27.10.2026', today)).toEqual({
      to: 'settings.leaseRebuild.errors.future',
    });
    expect(leaseRangeErrors('25.10.2026', '20.10.2026', today)).toEqual({
      to: 'settings.leaseRebuild.errors.order',
    });
    expect(leaseRangeErrors('', '31.02.2026', today)).toEqual({
      from: 'validation.required',
      to: 'settings.leaseRebuild.errors.day',
    });
  });

  it('today at the site is UTC+03:00', () => {
    expect(siteToday(new Date('2026-10-26T20:59:59Z'))).toBe('2026-10-26');
    expect(siteToday(new Date('2026-10-26T21:00:00Z'))).toBe('2026-10-27');
  });
});
