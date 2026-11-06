/**
 * `[network] https_proxy` in Ayarlar → Genel (Wave 5 contract 2.5, D13; browser-editable with
 * re-authentication, C5): the outbound proxy the revocation-list fetch and the `rfc3161` and
 * `turktrust` time-stamp providers use, `http://host:port` or empty (no proxy). The proxy takes
 * no credentials (D13), and the server refuses any other form (C10); the browser checks the same
 * before a change is sent.
 *
 * Interim until W5-T adds `network` to `GeneralSection` and `GeneralBody` (`settings-types.ts`,
 * checked against the API's schema), which replace this: the section and the body carry it as
 * `network.https_proxy`, as they carry `clock.ntp_server`.
 */

import type { GeneralBody, GeneralSection } from '@/lib/settings-types';

import { validateHost } from './settings-validation';

export interface NetworkSection {
  https_proxy: string;
}

export type GeneralRequest = GeneralBody & { network?: NetworkSection };

/**
 * The proxy as the server reports it, `undefined` while it does not (an API before W5-T): the page
 * then shows no field for it, so it never shows a value the server did not give, and never sends
 * a field the API refuses.
 */
export function httpsProxy(general: GeneralSection): string | undefined {
  const network = (general as GeneralSection & { network?: Partial<Record<string, unknown>> })
    .network as Partial<Record<string, unknown>> | undefined;
  const value = network?.https_proxy;
  return typeof value === 'string' ? value : undefined;
}

/** `http://host:port`: the host and the port, no user information, no path. */
const PROXY = /^http:\/\/([^/:@\s]+):(\d{1,5})$/;

/** The proxy typed: `''` (no proxy) or `http://host:port` with a host name or IPv4 address. */
export function validateHttpsProxy(value: string): string | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const found = PROXY.exec(text);
  const port = Number(found?.[2] ?? 0);
  if (!found?.[1] || validateHost(found[1], false) || port < 1 || port > 65_535)
    return 'settings.general.network.errors.proxy';
  return undefined;
}

/** What a change sends of the proxy: nothing unless it differs from the server's. */
export function networkBody(
  general: GeneralSection,
  typed: string | undefined,
): Pick<GeneralRequest, 'network'> {
  const stored = httpsProxy(general);
  if (stored === undefined || typed === undefined) return {};
  const proxy = typed.trim();
  return proxy === stored ? {} : { network: { https_proxy: proxy } };
}
