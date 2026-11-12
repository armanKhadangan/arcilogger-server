import { licenseSample } from '@/test/samples';
import { WEBHOOKS } from '@/test/webhook-samples';

import { formatVerified, sortWebhooks, testLocked, testResultOf } from './webhooks-model';

describe("Ayarlar → Webhook'lar (Wave 6 §2.4.4)", () => {
  it('marks Teams and Slack "sahada doğrula" (V8)', () => {
    expect(formatVerified('generic')).toBe(true);
    expect(formatVerified('teams')).toBe(false);
    expect(formatVerified('slack')).toBe(false);
  });

  it('lists the webhooks by name', () => {
    expect(sortWebhooks(WEBHOOKS.webhooks).map((item) => item.name)).toEqual([
      'siem',
      'slack-bt',
      'teams-guvenlik',
    ]);
  });

  it('locks "Test gönder" only when the licence names the module and it is off', () => {
    const license = licenseSample('trial');
    expect(testLocked(license)).toBe(false);
    expect(testLocked(undefined)).toBe(false);
    const off = {
      ...license,
      modules: [
        ...license.modules,
        { ...license.modules[0], key: 'security_rules', enabled: false, licensed: false },
      ],
    } as typeof license;
    expect(testLocked(off)).toBe(true);
  });

  it("reads the test's outcome from its request", () => {
    expect(testResultOf({ result: { ok: true, status: 204, error_text: null } })).toEqual({
      ok: true,
      status: 204,
      error_text: null,
    });
    expect(testResultOf({ result: { ok: false, error_text: 'timeout' } })).toEqual({
      ok: false,
      status: null,
      error_text: 'timeout',
    });
    expect(testResultOf({ result: null })).toBeNull();
    expect(testResultOf({ result: { status: 200 } })).toBeNull();
  });
});
