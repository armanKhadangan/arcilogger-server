import { withAreas } from '@/lib/i18n';
import type { GeneralSection } from '@/lib/settings-types';
import en from '@/locales/en.json';
import tr from '@/locales/tr.json';

import { httpsProxy, networkBody, validateHttpsProxy } from './general-network';

/*
 * `[network] https_proxy` in Ayarlar → Genel (Wave 5 contract 2.5, D13, C10): read only when the
 * server reports it, `http://host:port` or empty, sent only when it was changed.
 */

// Built, not written out: the lint rule keeps address literals out of the source.
const http = (rest: string) => `http://${rest}`;
const https = (rest: string) => `https://${rest}`;

const GENERAL: GeneralSection = {
  site_name: 'Örnek A.Ş.',
  clock: {
    ntp_server: '172.20.0.1',
    fallback_server: 'pool.ornek.example',
    alert_offset_ms: 1000,
  },
};

/** The section as W5-T's API reports it. */
function w5t(proxy: unknown): GeneralSection {
  return { ...GENERAL, network: { https_proxy: proxy } } as GeneralSection;
}

const LOCALES = { tr: withAreas(tr, 'tr'), en: withAreas(en, 'en') };

describe('Ayarlar → Genel: the outbound proxy (Wave 5 contract 2.5)', () => {
  it('reads it only when the server reports it', () => {
    expect(httpsProxy(GENERAL)).toBeUndefined();
    expect(httpsProxy(w5t(null))).toBeUndefined();
    expect(httpsProxy(w5t(''))).toBe('');
    expect(httpsProxy(w5t(http('172.20.0.10:3128')))).toBe(http('172.20.0.10:3128'));
  });

  it('is http://host:port with a numeric port, or empty', () => {
    for (const ok of [
      '',
      '   ',
      http('172.20.0.10:3128'),
      ` ${http('vekil.ornek.example:8080')} `,
      http('203.0.113.5:1'),
      http('203.0.113.5:65535'),
    ])
      expect(validateHttpsProxy(ok)).toBeUndefined();
    for (const bad of [
      '172.20.0.10:3128',
      http('172.20.0.10'),
      http('172.20.0.10:'),
      http('172.20.0.10:0'),
      http('172.20.0.10:65536'),
      http('172.20.0.10:80a'),
      http('172.20.0.10:3128/'),
      http('172.20.0.10:3128/yol'),
      https('172.20.0.10:3128'),
      'socks5://172.20.0.10:1080',
      // No credentials (D13), as the server refuses them.
      http('ornek:deneme@172.20.0.10:3128'),
      http('ornek@172.20.0.10:3128'),
      http('172.20.0.300:3128'),
      http('-vekil:3128'),
      http('[2001:db8::1]:3128'),
    ])
      expect(validateHttpsProxy(bad)).toBe('settings.general.network.errors.proxy');
  });

  it('a change sends it only when it differs from the server, so an API before W5-T accepts it', () => {
    expect(networkBody(GENERAL, undefined)).toEqual({});
    // The server has no such key: nothing is sent whatever the form holds.
    expect(networkBody(GENERAL, http('172.20.0.10:3128'))).toEqual({});
    expect(networkBody(w5t(''), '')).toEqual({});
    expect(networkBody(w5t(''), '  ')).toEqual({});
    expect(networkBody(w5t(''), ` ${http('172.20.0.10:3128')} `)).toEqual({
      network: { https_proxy: http('172.20.0.10:3128') },
    });
    expect(networkBody(w5t(http('172.20.0.10:3128')), http('172.20.0.10:3128'))).toEqual({});
    // Emptied: no proxy any more.
    expect(networkBody(w5t(http('172.20.0.10:3128')), '')).toEqual({
      network: { https_proxy: '' },
    });
  });

  it('the texts: TR and EN, the "sahada doğrula" note of the Zamane client', () => {
    const at = (locale: object, key: string): unknown =>
      key
        .split('.')
        .reduce<unknown>(
          (node, part) =>
            node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
          locale,
        );
    expect(at(LOCALES.tr, 'settings.general.network.zamaneNoteTitle')).toBe('Sahada doğrula');
    expect(at(LOCALES.en, 'settings.general.network.zamaneNoteTitle')).toBe('Verify on site');
    expect(at(LOCALES.tr, 'admin.paths.network.https_proxy')).toBe('HTTPS vekil sunucusu');
    expect(at(LOCALES.en, 'admin.paths.network.https_proxy')).toBe('HTTPS proxy');
    for (const locale of Object.values(LOCALES)) {
      // The example is from the fictional network (172.20.x).
      expect(at(locale, 'settings.general.network.proxyPlaceholder')).toBe(
        http('172.20.0.10:3128'),
      );
      expect(
        validateHttpsProxy(String(at(locale, 'settings.general.network.proxyPlaceholder'))),
      ).toBeUndefined();
    }
  });
});
