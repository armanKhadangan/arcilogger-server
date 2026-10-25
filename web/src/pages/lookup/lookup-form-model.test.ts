import { ApiError } from '@/lib/api';
import en from '@/locales/en.json';
import tr from '@/locales/tr.json';
import { LOOKUPS, need, type ErrorSample } from '@/test/samples';

import {
  EMPTY_DRAFT,
  draftFrom,
  isIpAddress,
  normalizeMac,
  serverFieldErrors,
  toLookupBody,
  validateLookup,
  type LookupDraft,
} from './lookup-form-model';

const TODAY = '2026-10-13';

const VALID: LookupDraft = {
  ...EMPTY_DRAFT,
  authority: 'İzmir Cumhuriyet Başsavcılığı',
  fileNo: '2026/48213',
  requestDate: '10.10.2026',
  reason: '5651 kapsamında IP bilgisi talebi\r\n(müzekkere 2026/771)',
  publicIp: ' 100.64.12.9 ',
  publicPort: '51001',
  protocol: '6',
  date: '09.10.2026',
  time: '11:23:41',
};

function apiError(sample: ErrorSample | undefined): ApiError {
  const body = need(sample, 'error sample').body.error;
  return new ApiError({
    status: need(sample, 'error sample').status,
    code: body.code,
    messageTr: body.message_tr,
    messageEn: body.message_en,
    details: body.details,
  });
}

describe('validateLookup', () => {
  it('accepts a complete request', () => {
    expect(validateLookup(VALID, TODAY)).toEqual({});
  });

  it('requires the request details, a day, a time and something to look for', () => {
    expect(validateLookup(EMPTY_DRAFT, TODAY)).toEqual({
      authority: 'validation.required',
      fileNo: 'validation.required',
      reason: 'validation.required',
      requestDate: 'validation.required',
      date: 'validation.required',
      time: 'validation.required',
      target: 'lookup.validation.target',
    });
  });

  it('refuses what the API would refuse', () => {
    const errors = validateLookup(
      {
        ...VALID,
        authority: 'Savcılık\u202e',
        requestDate: '14.10.2026',
        publicIp: '100.64.12.300',
        publicPort: '70000',
        destIp: 'fe80::1%eth0',
        destDomain: 'user@example',
        mac: '02:00:5e:00:53',
        date: '31.09.2026',
        time: '7:30 PM',
        timeZone: 'Europe/Berlin',
        tolerance: '3601',
      },
      TODAY,
    );
    expect(errors).toEqual({
      authority: 'validation.controlCharacters',
      requestDate: 'lookup.validation.futureRequest',
      publicIp: 'lookup.validation.ipv4',
      publicPort: 'lookup.validation.port',
      destIp: 'lookup.validation.ip',
      destDomain: 'lookup.validation.domain',
      mac: 'lookup.validation.mac',
      date: 'lookup.validation.date',
      time: 'lookup.validation.time',
      timeZone: 'lookup.validation.timeZone',
      tolerance: 'lookup.validation.tolerance',
    });
  });

  it('allows line breaks in the reason but no other control characters', () => {
    expect(validateLookup({ ...VALID, reason: 'satır 1\nsatır 2\tson' }, TODAY)).toEqual({});
    expect(validateLookup({ ...VALID, reason: 'satır\u0007' }, TODAY).reason).toBe(
      'validation.controlCharacters',
    );
  });

  it('a MAC address, an internal IP or a destination alone is enough to look for', () => {
    const base = { ...VALID, publicIp: '', publicPort: '', protocol: '' };
    expect(validateLookup(base, TODAY).target).toBe('lookup.validation.target');
    expect(validateLookup({ ...base, mac: '02-00-5E-00-53-64' }, TODAY)).toEqual({});
    expect(validateLookup({ ...base, internalIp: '172.20.42.57' }, TODAY)).toEqual({});
    expect(validateLookup({ ...base, destDomain: 'example.com.tr' }, TODAY)).toEqual({});
  });

  it("refuses an IPv6 public or internal address before sending, in the API's own words", () => {
    const errors = validateLookup(
      {
        ...VALID,
        publicIp: ' 2001:db8::44 ',
        internalIp: '::ffff:172.20.42.57',
        destIp: '2001:db8:1::10',
      },
      TODAY,
    );
    // The destination is only a filter: an IPv6 destination stays allowed.
    expect(errors).toEqual({
      publicIp: 'lookup.validation.ipv6',
      internalIp: 'lookup.validation.ipv6',
    });
    // A malformed public or internal address is asked for as IPv4, never as "IPv4 or IPv6".
    expect(validateLookup({ ...VALID, publicIp: '2001:db8::44%eth0' }, TODAY).publicIp).toBe(
      'lookup.validation.ipv4',
    );
    expect(validateLookup({ ...VALID, internalIp: '172.20.42' }, TODAY).internalIp).toBe(
      'lookup.validation.ipv4',
    );
    expect(tr.lookup.validation.ipv4).not.toContain('IPv6');
    expect(en.lookup.validation.ipv4).not.toContain('IPv6');
    const refusal = need(LOOKUPS.errors.ipv6_not_supported, 'ipv6_not_supported sample');
    expect(refusal.status).toBe(422);
    expect(tr.lookup.validation.ipv6).toBe(refusal.body.error.message_tr);
    expect(en.lookup.validation.ipv6).toBe(refusal.body.error.message_en);
  });
});

describe('addresses', () => {
  it('reads IPv4 and IPv6 like Python ipaddress', () => {
    expect(isIpAddress('172.31.255.1')).toBe(true);
    expect(isIpAddress('2001:db8::1')).toBe(true);
    expect(isIpAddress('::ffff:100.64.12.9')).toBe(true);
    expect(isIpAddress('100.064.12.9')).toBe(false);
    expect(isIpAddress('256.1.1.1')).toBe(false);
    expect(isIpAddress('fe80::1%eth0')).toBe(false);
    expect(isIpAddress('example.com')).toBe(false);
  });

  it('normalizes MAC addresses', () => {
    expect(normalizeMac('02-00-5E-00-53-64')).toBe('02:00:5e:00:53:64');
    expect(normalizeMac('0200.5e00.5364')).toBe('02:00:5e:00:53:64');
    expect(normalizeMac('02:00:5e:00:53')).toBeNull();
  });
});

describe('toLookupBody', () => {
  it('sends ISO dates, a local time with its zone, numbers and nulls', () => {
    expect(toLookupBody({ ...VALID, mac: '02-00-5E-00-53-64', tolerance: '' })).toEqual({
      request: {
        authority: 'İzmir Cumhuriyet Başsavcılığı',
        file_no: '2026/48213',
        request_date: '2026-10-10',
        reason: '5651 kapsamında IP bilgisi talebi\n(müzekkere 2026/771)',
      },
      query: {
        at: '2026-10-09T11:23:41',
        time_zone: 'Europe/Istanbul',
        public_ip: '100.64.12.9',
        public_port: 51001,
        protocol: 6,
        dest_ip: null,
        dest_port: null,
        dest_domain: null,
        tolerance_seconds: null,
        internal_ip: null,
        mac: '02:00:5e:00:53:64',
      },
    });
  });

  it('adds seconds to a time typed without them', () => {
    expect(toLookupBody({ ...VALID, time: '8.05' }).query.at).toBe('2026-10-09T08:05:00');
  });
});

describe('draftFrom', () => {
  it('fills the form with a stored lookup as it was entered', () => {
    const draft = draftFrom(LOOKUPS.exact);
    expect(draft).toMatchObject({
      authority: LOOKUPS.exact.request.authority,
      requestDate: LOOKUPS.exact.request.request_date.split('-').reverse().join('.'),
      publicIp: '100.64.12.9',
      publicPort: '51001',
      protocol: '6',
      date: '09.10.2026',
      time: '11:23:41',
      timeZone: 'Europe/Istanbul',
      tolerance: '60',
    });
    // The same lookup again: the body the API stored comes back.
    expect(validateLookup(draft, TODAY)).toEqual({});
    expect(toLookupBody(draft).query.at).toBe('2026-10-09T11:23:41');
  });

  it('every captured lookup refills a valid form', () => {
    for (const name of ['candidates', 'conflict', 'internal', 'none', 'vault_only'] as const) {
      expect(validateLookup(draftFrom(LOOKUPS[name]), TODAY), name).toEqual({});
    }
  });
});

describe('serverFieldErrors', () => {
  it('maps the API 422 details back to the fields', () => {
    expect(serverFieldErrors(apiError(LOOKUPS.errors.bad_address))).toEqual({
      publicIp: 'Value error, not an IP address',
    });
    expect(serverFieldErrors(apiError(LOOKUPS.errors.blank_authority))).toEqual({
      authority: 'String should have at least 1 character',
    });
    expect(serverFieldErrors(apiError(LOOKUPS.errors.future_request_date))).toEqual({
      requestDate: 'the request date is in the future',
    });
    expect(serverFieldErrors(apiError(LOOKUPS.errors.nothing_to_look_for))).toEqual({
      target: 'a lookup needs a public IP, an internal IP, a MAC address or a destination',
    });
  });

  it('ignores other errors', () => {
    expect(serverFieldErrors(apiError(LOOKUPS.errors.privacy_notice_required))).toEqual({});
    expect(serverFieldErrors(new Error('x'))).toEqual({});
  });
});
