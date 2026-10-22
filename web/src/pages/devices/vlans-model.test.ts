import type { VlanOut } from '@/lib/api-types';
import { DEVICES, need } from '@/test/samples';

import { NO_DEPARTMENT } from './persons-model';
import {
  checkSubnet,
  validateVlan,
  vlanChanges,
  vlanCreateBody,
  vlanFormValues,
  vlanLabel,
} from './vlans-model';

// Captured: VLAN 70 "Laboratuvar" (172.20.70.0/24, dhcp70, department "Araştırma ve
// Geliştirme" = 4, "Kat 2").
const lab: VlanOut = DEVICES.vlan_created;
// The example plan's transit network has no VLAN id.
const transit: VlanOut = need(
  DEVICES.vlans.find((vlan) => vlan.vlan_id === null),
  'transit network',
);

describe('VLAN form', () => {
  it('starts empty for a new VLAN, filled for an existing one', () => {
    expect(vlanFormValues(null)).toEqual({
      subnet: '',
      vlanId: '',
      name: '',
      purpose: '',
      dhcpServer: '',
      department: NO_DEPARTMENT,
      guest: false,
      notes: '',
    });
    expect(vlanFormValues(lab)).toEqual({
      subnet: '172.20.70.0/24',
      vlanId: '70',
      name: 'Laboratuvar',
      purpose: 'Test bench PCs',
      dhcpServer: 'dhcp70',
      department: '4',
      guest: false,
      notes: 'Kat 2',
    });
    expect(vlanFormValues(transit).vlanId).toBe('');
  });

  it('names a VLAN by its id, a network without one by its subnet', () => {
    expect(vlanLabel(lab)).toBe('VLAN 70');
    expect(vlanLabel(transit)).toBe(transit.subnet);
  });

  it('needs the network and the name', () => {
    expect(validateVlan(vlanFormValues(null))).toEqual({
      subnet: 'validation.required',
      name: 'validation.required',
    });
    expect(validateVlan(vlanFormValues(lab))).toEqual({});
  });

  it('checks the other fields like the server', () => {
    const base = vlanFormValues(lab);
    expect(validateVlan({ ...base, vlanId: '0' })).toEqual({
      vlanId: 'devices.vlans.errors.vlanId',
    });
    expect(validateVlan({ ...base, vlanId: '4095' }).vlanId).toBe('devices.vlans.errors.vlanId');
    expect(validateVlan({ ...base, vlanId: '20a' }).vlanId).toBe('devices.vlans.errors.vlanId');
    expect(validateVlan({ ...base, vlanId: ' 4094 ' })).toEqual({});
    expect(validateVlan({ ...base, vlanId: '' })).toEqual({});
    expect(validateVlan({ ...base, dhcpServer: 'dhcp 20' }).dhcpServer).toBe(
      'devices.vlans.errors.dhcpServer',
    );
    expect(validateVlan({ ...base, dhcpServer: 'dhcp:20' }).dhcpServer).toBe(
      'devices.vlans.errors.dhcpServer',
    );
    expect(validateVlan({ ...base, dhcpServer: 'd'.repeat(65) }).dhcpServer).toBe(
      'validation.tooLong',
    );
    expect(validateVlan({ ...base, purpose: 'p'.repeat(501) }).purpose).toBe('validation.tooLong');
    expect(validateVlan({ ...base, purpose: 'a‮b' }).purpose).toBe('validation.controlCharacters');
    expect(validateVlan({ ...base, notes: 'Kat 2\nOda 5' })).toEqual({});
    expect(validateVlan({ ...base, notes: 'a\u0007' }).notes).toBe('validation.controlCharacters');
    expect(validateVlan({ ...base, name: 'n'.repeat(201) }).name).toBe('validation.tooLong');
  });
});

describe('network addresses', () => {
  it('takes IPv4 and IPv6 networks with their prefix length', () => {
    for (const subnet of [
      '10.0.20.0/24',
      ' 172.20.70.0/24 ',
      '10.30.0.0/30',
      '10.0.0.1/32',
      '0.0.0.0/0',
      'fd00:0:0:20::/64',
      'FD00::/8',
      '2001:db8::/32',
      '::/0',
      '1:2:3:4:5:6:7::/128',
      '::/80',
      '::fffe:a00:0/104',
    ])
      expect(checkSubnet(subnet), subnet).toEqual({ ok: true });
  });

  it('asks for the prefix length', () => {
    expect(checkSubnet('10.0.20.0')).toEqual({ ok: false, error: 'devices.vlans.errors.prefix' });
    expect(checkSubnet('  ')).toEqual({ ok: false, error: 'validation.required' });
  });

  it('refuses what is not a network', () => {
    for (const subnet of [
      'nope/24',
      '10.0.20/24',
      '10.0.20.256/24',
      '010.0.20.0/24',
      '10.0.20.0/33',
      '10.0.20.0/24/1',
      '10.0.20.0/',
      '10.0.20.0/255.255.255.0',
      'fe80::%1/64',
      '1::2::3/64',
      '1:2:3:4:5:6:7:8:9/64',
      'fd00::/129',
      'gggg::/16',
    ])
      expect(checkSubnet(subnet), subnet).toEqual({
        ok: false,
        error: 'devices.vlans.errors.subnet',
      });
  });

  it('names the IPv4 network meant when host bits are set', () => {
    expect(checkSubnet('10.0.20.5/24')).toEqual({
      ok: false,
      error: 'devices.vlans.errors.hostBits',
      network: '10.0.20.0/24',
    });
    expect(checkSubnet('172.20.70.130/25')).toMatchObject({ network: '172.20.70.128/25' });
    expect(checkSubnet('fd00::1/64')).toEqual({
      ok: false,
      error: 'devices.vlans.errors.hostBits',
      network: null,
    });
  });

  it('asks for the IPv4 network instead of one written as IPv6', () => {
    expect(checkSubnet('::ffff:10.99.0.0/120')).toEqual({
      ok: false,
      error: 'devices.vlans.errors.ipv4Mapped',
      network: '10.99.0.0/24',
    });
    expect(checkSubnet('::FFFF:a63:0/112')).toMatchObject({ network: '10.99.0.0/16' });
    expect(checkSubnet('0:0:0:0:0:ffff:10.0.0.0/104')).toMatchObject({ network: '10.0.0.0/8' });
    expect(checkSubnet('::ffff:0.0.0.0/96')).toMatchObject({ network: '0.0.0.0/0' });
    expect(checkSubnet('::ffff:10.99.0.1/128')).toMatchObject({ network: '10.99.0.1/32' });
    // Host bits first: the IPv4 network meant is not certain yet.
    expect(checkSubnet('::ffff:10.99.0.1/120')).toMatchObject({
      error: 'devices.vlans.errors.hostBits',
    });
  });
});

describe('request bodies', () => {
  it('leaves empty optional fields out of a new VLAN', () => {
    expect(
      vlanCreateBody({ ...vlanFormValues(null), subnet: ' 10.30.0.0/30 ', name: ' Hat 2 ' }),
    ).toEqual({ subnet: '10.30.0.0/30', name_tr: 'Hat 2' });
    expect(vlanCreateBody({ ...vlanFormValues(lab), guest: true })).toEqual({
      subnet: '172.20.70.0/24',
      vlan_id: 70,
      name_tr: 'Laboratuvar',
      purpose: 'Test bench PCs',
      dhcp_server: 'dhcp70',
      department_id: 4,
      guest: true,
      notes: 'Kat 2',
    });
  });

  it('sends only what changed, a cleared field as null and a new network as new_subnet', () => {
    const values = vlanFormValues(lab);
    expect(vlanChanges(lab, values)).toBeNull();
    expect(vlanChanges(lab, { ...values, subnet: '172.20.70.0/24 ', name: 'Laboratuvar ' })).toBe(
      null,
    );
    expect(
      vlanChanges(lab, {
        ...values,
        subnet: '172.20.71.0/24',
        name: 'Ar-Ge Laboratuvarı',
        guest: true,
      }),
    ).toEqual({
      subnet: '172.20.70.0/24',
      new_subnet: '172.20.71.0/24',
      name_tr: 'Ar-Ge Laboratuvarı',
      guest: true,
    });
    expect(
      vlanChanges(lab, {
        ...values,
        vlanId: '',
        purpose: ' ',
        dhcpServer: '',
        department: NO_DEPARTMENT,
        notes: '',
      }),
    ).toEqual({
      subnet: '172.20.70.0/24',
      vlan_id: null,
      purpose: null,
      dhcp_server: null,
      department_id: null,
      notes: null,
    });
    expect(vlanChanges(transit, { ...vlanFormValues(transit), vlanId: '19' })).toEqual({
      subnet: transit.subnet,
      vlan_id: 19,
    });
  });
});
