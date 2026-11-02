import type { AdminPolicyOut, SourceConfig } from '@/lib/settings-types';

import {
  NEW_SOURCE,
  fromSourceForm,
  parseIpv4,
  parseNetwork,
  sourceConfig,
  toSourceForm,
  validateSource,
} from './sources-model';

const POLICY: AdminPolicyOut = {
  enabled: true,
  source_networks: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'],
  min_prefix_v4: 16,
  max_sources: 500,
  hold_hours: 0,
  jar_hold_hours: 24,
  tsa_test_per_day: 3,
};

function source(name: string, address: string): SourceConfig {
  return {
    name,
    type: 'mikrotik',
    address,
    heartbeat_minutes: 15,
    expected: true,
    transport: 'any',
    enabled: true,
  };
}

const SOURCES = [source('core-r1', '172.20.10.1'), source('wifi', '172.20.50.0/24')];

describe('a source as the browser edits it (contract 2.6 "sources")', () => {
  it('reads IPv4 addresses and networks', () => {
    expect(parseIpv4('172.20.10.1')).toBe(((172 * 256 + 20) * 256 + 10) * 256 + 1);
    expect(parseIpv4('172.20.10')).toBeNull();
    expect(parseIpv4('172.20.10.256')).toBeNull();
    expect(parseIpv4('01.2.3.4')).toBeNull();
    expect(parseNetwork('172.20.10.1')).toMatchObject({ prefix: 32, hostBits: false });
    expect(parseNetwork('172.20.10.0/24')).toMatchObject({ prefix: 24, hostBits: false });
    expect(parseNetwork('172.20.10.5/24')).toMatchObject({ prefix: 24, hostBits: true });
    expect(parseNetwork('172.20.10.0/33')).toBeNull();
    expect(parseNetwork('172.20.10.0/24/1')).toBeNull();
  });

  it('round-trips the form; the table row keeps only the configuration', () => {
    const row = { ...source('ap-1', '172.20.10.30'), firewall: 'open' as const, last_seen: null };
    expect(sourceConfig(row)).toEqual(source('ap-1', '172.20.10.30'));
    expect(fromSourceForm({ ...toSourceForm(row), name: ' ap-1 ', heartbeat: '30' })).toEqual({
      ...source('ap-1', '172.20.10.30'),
      heartbeat_minutes: 30,
    });
  });

  it('a new source: its name, its address and the heartbeat', () => {
    const check = (name: string, address: string, heartbeat = '15') =>
      validateSource({ ...NEW_SOURCE, name, address, heartbeat }, SOURCES, POLICY, null);
    expect(check('ap-1', '172.20.10.30')).toEqual({});
    expect(check('', '')).toEqual({ name: 'validation.required', address: 'validation.required' });
    expect(check('Ap 1', '172.20.10.30').name).toBe('settings.sources.errors.name');
    expect(check('-ap', '172.20.10.30').name).toBe('settings.sources.errors.name');
    expect(check('unknown-1', '172.20.10.30').name).toBe('settings.sources.errors.nameUnknown');
    expect(check('core-r1', '172.20.10.30').name).toBe('settings.sources.errors.nameTaken');
    expect(check('ap-1', 'fe80::1').address).toBe('settings.sources.errors.ipv6');
    expect(check('ap-1', 'ap.ornek').address).toBe('settings.sources.errors.address');
    expect(check('ap-1', '172.20.10.5/24').address).toBe('settings.sources.errors.hostBits');
    for (const reserved of ['127.0.0.1', '0.0.0.0', '169.254.1.1', '224.0.0.5', '255.255.255.255'])
      expect(check('ap-1', reserved).address).toBe('settings.sources.errors.reserved');
    expect(check('ap-1', '172.16.0.0/12').address).toBe('settings.sources.errors.prefix');
    expect(check('ap-1', '198.51.100.7').address).toBe('settings.sources.errors.notAllowed');
    expect(check('ap-1', '172.20.10.1').address).toBe('settings.sources.errors.addressTaken');
    expect(check('ap-1', '172.20.48.0/21').address).toBe('settings.sources.errors.overlap');
    // A host inside another source's network stays allowed (the exact address wins).
    expect(check('ap-1', '172.20.50.7')).toEqual({});
    expect(check('ap-1', '172.20.10.30', '0').heartbeat).toBe('admin.errors.min');
    expect(check('ap-1', '172.20.10.30', '1441').heartbeat).toBe('admin.errors.max');
  });

  it('a changed source is not compared with itself; its name is not checked again', () => {
    const form = { ...toSourceForm(source('core-r1', '172.20.10.1')), heartbeat: '30' };
    expect(validateSource(form, SOURCES, POLICY, 'core-r1')).toEqual({});
    expect(
      validateSource({ ...form, address: '172.20.50.0/24' }, SOURCES, POLICY, 'core-r1'),
    ).toEqual({
      address: 'settings.sources.errors.addressTaken',
    });
  });

  it('without a policy only the address itself is checked', () => {
    expect(
      validateSource({ ...NEW_SOURCE, name: 'ap-1', address: '198.51.100.7' }, [], undefined, null),
    ).toEqual({});
  });
});
