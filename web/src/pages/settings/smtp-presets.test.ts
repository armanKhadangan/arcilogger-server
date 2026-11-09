import { describe, expect, it } from 'vitest';

import { SMTP_PRESETS, planPreset, presetOf, smtpPreset, type PresetFields } from './smtp-presets';

/** A form with nothing typed yet (the page's defaults). */
const FRESH: PresetFields = { host: '', port: '587', security: 'starttls', username: '' };

describe('the e-mail provider presets (0.4.0 P6)', () => {
  it('pins the providers’ servers, ports and security (S3, S4)', () => {
    // A changed fact must come with the provider's page and date in the contract's Deviations.
    expect(
      SMTP_PRESETS.map(({ id, host, port, security, login }) => [id, host, port, security, login]),
    ).toEqual([
      ['google', 'smtp.gmail.com', 587, 'starttls', 'password'],
      ['google-relay', 'smtp-relay.gmail.com', 587, 'starttls', 'none'],
      ['microsoft365', 'smtp.office365.com', 587, 'starttls', 'password'],
      ['hosting', null, 587, 'starttls', 'password'],
      ['company', null, 25, 'none', 'either'],
      ['other', null, null, null, null],
    ]);
  });

  it('infers the preset from the saved server name', () => {
    expect(presetOf('smtp.gmail.com')).toBe('google');
    expect(presetOf(' SMTP-Relay.Gmail.com. ')).toBe('google-relay');
    expect(presetOf('smtp.office365.com')).toBe('microsoft365');
    expect(presetOf('smtp.ornek.example')).toBe('other');
    expect(presetOf('  ')).toBeNull();
  });

  it('fills an empty form without asking', () => {
    const plan = planPreset(FRESH, smtpPreset('company'));
    expect(plan.next).toEqual({ port: '25', security: 'none' });
    expect(plan.overwrites).toEqual([]);
    expect(planPreset(FRESH, smtpPreset('google')).next).toEqual({ host: 'smtp.gmail.com' });
  });

  it('asks before changing a filled field and leaves equal ones alone', () => {
    const filled: PresetFields = {
      host: 'smtp.ornek.example',
      port: '25',
      security: 'none',
      username: 'arcilogger',
    };
    const plan = planPreset(filled, smtpPreset('google-relay'));
    expect(plan.next).toEqual({
      host: 'smtp-relay.gmail.com',
      port: '587',
      security: 'starttls',
      username: '',
    });
    expect(plan.overwrites).toEqual([
      { field: 'host', from: 'smtp.ornek.example', to: 'smtp-relay.gmail.com' },
      { field: 'port', from: '25', to: '587' },
      { field: 'security', from: 'none', to: 'starttls' },
      { field: 'username', from: 'arcilogger', to: '' },
    ]);
    const same: PresetFields = {
      ...filled,
      host: 'smtp.office365.com',
      port: '587',
      security: 'starttls',
    };
    expect(planPreset(same, smtpPreset('microsoft365'))).toEqual({ next: {}, overwrites: [] });
  });

  it('keeps a typed server for the presets without one', () => {
    const typed: PresetFields = { ...FRESH, host: 'posta.ornek.example', username: 'arcilogger' };
    const plan = planPreset(typed, smtpPreset('hosting'));
    expect(plan).toEqual({ next: {}, overwrites: [] });
    expect(planPreset(typed, smtpPreset('other'))).toEqual({ next: {}, overwrites: [] });
  });
});
