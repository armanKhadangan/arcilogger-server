import { SECRET_KEPT } from '@/components/admin/secret-edit';
import type { SnmpOut, SourceOut } from '@/lib/settings-types';

import { fromSnmpForm, NEW_SNMP, snmpIn, snmpPaths, toSnmpForm, validateSnmp } from './snmp-model';
import { fromSourceForm, sourceBody, sourceConfig, toSourceForm } from './sources-model';

const STORED: SnmpOut = {
  enabled: true,
  port: 161,
  user: 'arcilogger',
  auth: 'sha256',
  priv: 'aes128',
  interfaces: ['wan1', 'wan2'],
  capacity_mbps: { wan1: { down: 100, up: 20 } },
  interval_seconds: 15,
  auth_key: 'set',
  priv_key: 'set',
};

function row(snmp: SnmpOut | null): SourceOut {
  return {
    name: 'fw-merkez',
    type: 'fortigate',
    address: '172.20.10.1',
    heartbeat_minutes: 15,
    expected: true,
    transport: 'any',
    enabled: true,
    snmp,
    firewall: 'open',
    last_seen: null,
  };
}

const ON = { ...toSnmpForm(STORED) };
const typed = (value: string) => ({ value, editing: true, clear: false });

describe('[sources.snmp] as the browser edits it (Wave 5 §6 N1, P4)', () => {
  it('a source without SNMP is sent as before: no snmp, no keys', () => {
    const form = toSourceForm(row(null));
    expect(form.snmp).toEqual(NEW_SNMP);
    expect(fromSourceForm(form)).not.toHaveProperty('snmp');
    expect(sourceBody(form, 'e1')).toEqual({ etag: 'e1', source: fromSourceForm(form) });
  });

  it('round-trips a stored table, the keys only as set or unset', () => {
    const form = toSnmpForm(STORED);
    expect(form).toMatchObject({
      stored: true,
      enabled: true,
      port: '161',
      lines: [
        { name: 'wan1', down: '100', up: '20' },
        { name: 'wan2', down: '', up: '' },
      ],
      authKeySet: true,
      privKeySet: true,
      authKey: SECRET_KEPT,
    });
    expect(fromSnmpForm(form)).toEqual(snmpIn(STORED));
    expect(snmpIn(STORED)).not.toHaveProperty('auth_key');
  });

  it("keeps the stored table on a row's switch and in the edit dialog (W5-N: no snmp deletes it)", () => {
    expect(sourceConfig({ ...row(STORED), enabled: false })).toEqual({
      name: 'fw-merkez',
      type: 'fortigate',
      address: '172.20.10.1',
      heartbeat_minutes: 15,
      expected: true,
      transport: 'any',
      enabled: false,
      snmp: snmpIn(STORED),
    });
    const form = { ...toSourceForm(row(STORED)), heartbeat: '30' };
    expect(fromSourceForm(form).snmp).toEqual(snmpIn(STORED));
    // Switched off, the table stays with its values.
    const off = { ...form, snmp: { ...form.snmp, enabled: false } };
    expect(fromSourceForm(off).snmp).toEqual({ ...snmpIn(STORED), enabled: false });
  });

  it('sends a typed key once and never a stored one', () => {
    const form = toSourceForm(row(STORED));
    expect(sourceBody(form, 'e1')).not.toHaveProperty('snmp_auth_key');
    const body = sourceBody(
      { ...form, snmp: { ...form.snmp, authKey: typed('anahtar-ğüş1'), privKey: typed('') } },
      'e1',
    );
    expect(body.snmp_auth_key).toBe('anahtar-ğüş1');
    expect(body).not.toHaveProperty('snmp_priv_key');
  });

  it('sends typed keys only with a table: none for a source whose SNMP stays off', () => {
    const form = toSourceForm(row(null));
    const off = {
      ...form,
      snmp: { ...form.snmp, authKey: typed('örnek-anahtar-1'), privKey: typed('örnek-anahtar-2') },
    };
    expect(sourceBody(off, 'e1')).toEqual({ etag: 'e1', source: fromSourceForm(off) });
    // Switched off, a stored table still goes, and so do keys typed for it.
    const stored = toSourceForm(row(STORED));
    const kept = {
      ...stored,
      snmp: { ...stored.snmp, enabled: false, authKey: typed('örnek-anahtar-1') },
    };
    expect(sourceBody(kept, 'e1').snmp_auth_key).toBe('örnek-anahtar-1');
  });

  it('trims the names, leaves out empty lines and a capacity given in one direction', () => {
    const form = {
      ...NEW_SNMP,
      enabled: true,
      user: ' arcilogger ',
      lines: [
        { name: ' wan1 ', down: '100', up: '' },
        { name: '', down: '', up: '' },
        { name: 'ppp0', down: '50', up: '10' },
      ],
    };
    expect(fromSnmpForm(form)).toEqual({
      enabled: true,
      port: 161,
      user: 'arcilogger',
      auth: 'sha256',
      priv: 'aes128',
      interfaces: ['wan1', 'ppp0'],
      capacity_mbps: { ppp0: { down: 50, up: 10 } },
      interval_seconds: 15,
    });
  });

  it('checks an enabled table only: user, lines, bounds and both keys', () => {
    expect(validateSnmp(NEW_SNMP, true)).toEqual({});
    expect(validateSnmp(ON, false)).toEqual({});
    expect(validateSnmp({ ...NEW_SNMP, enabled: true }, false)).toEqual({
      user: 'validation.required',
      'line-0': 'snmp.form.errors.noLine',
      auth_key: 'snmp.form.errors.keyMissing',
      priv_key: 'snmp.form.errors.keyMissing',
    });
    expect(validateSnmp(ON, true)).toEqual({ snmp: 'snmp.form.errors.network' });
    expect(validateSnmp({ ...ON, port: '0', interval: '61', user: 'u'.repeat(65) }, false)).toEqual(
      {
        port: 'admin.errors.min',
        interval: 'admin.errors.max',
        user: 'snmp.form.errors.tooLong',
      },
    );
    expect(validateSnmp({ ...ON, interval: '9', port: 'x' }, false)).toEqual({
      interval: 'admin.errors.min',
      port: 'admin.errors.integer',
    });
  });

  it('checks the lines: distinct names, both capacities in 1–1 000 000 Mbit/s', () => {
    const lines = (...items: [string, string, string][]) => ({
      ...ON,
      lines: items.map(([name, down, up]) => ({ name, down, up })),
    });
    expect(validateSnmp(lines(['wan1', '', ''], [' wan1 ', '', '']), false)).toEqual({
      'line-1': 'snmp.form.errors.lineTwice',
    });
    expect(validateSnmp(lines(['wan1', '100', ''], ['', '5', '5']), false)).toEqual({
      'up-0': 'snmp.form.errors.capacityBoth',
      'line-1': 'validation.required',
    });
    expect(validateSnmp(lines(['wan1', '0', '1000001']), false)).toEqual({
      'down-0': 'admin.errors.min',
      'up-0': 'admin.errors.max',
    });
    expect(validateSnmp(lines(['w'.repeat(65), '', '']), false)).toEqual({
      'line-0': 'snmp.form.errors.tooLong',
    });
    expect(
      validateSnmp(
        lines(['a', '', ''], ['b', '', ''], ['c', '', ''], ['d', '', ''], ['e', '', '']),
        false,
      ),
    ).toEqual({ snmp: 'snmp.form.errors.tooManyLines' });
  });

  it('checks a typed key: 8–64 characters counted as the server counts them', () => {
    expect(validateSnmp({ ...ON, authKey: typed('kısa') }, false)).toEqual({
      auth_key: 'snmp.form.errors.keyLength',
    });
    expect(validateSnmp({ ...ON, privKey: typed('ğ'.repeat(64)) }, false)).toEqual({});
    expect(validateSnmp({ ...ON, privKey: typed('ğ'.repeat(65)) }, false)).toEqual({
      priv_key: 'snmp.form.errors.keyLength',
    });
    expect(validateSnmp({ ...ON, authKey: typed('') }, false)).toEqual({
      auth_key: 'validation.required',
    });
    // Off, only a typed key is checked: the server would refuse it all the same.
    expect(validateSnmp({ ...ON, enabled: false, authKey: typed('kısa') }, true)).toEqual({
      auth_key: 'snmp.form.errors.keyLength',
    });
  });

  it("names the server's paths of the table and of each key (W5-N-6, W5-N-8)", () => {
    expect(snmpPaths(' fw-merkez ')).toEqual({
      snmp: ['sources.fw-merkez.snmp'],
      auth_key: ['snmp.fw-merkez.auth_key'],
      priv_key: ['snmp.fw-merkez.priv_key'],
    });
  });
});
