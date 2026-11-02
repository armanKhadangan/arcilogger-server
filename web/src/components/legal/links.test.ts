import { roleHas } from '@/lib/permissions';
import { LEGAL_REASONS } from '@/test/w5-fixtures';

import { LEGAL_LINKS, linkFor } from './links';

/*
 * A reason's link (contract 2.3.1 `link`): the sealing page or Ayarlar → Zaman damgası, shown
 * only to a role that may open the page (the role table of `generated/roles.json`).
 */

const as = (role: string) => (permission: string) => roleHas(role, permission);

describe('legal-status links', () => {
  it('point at the sealing page and the time-stamp settings with their pages’ permissions', () => {
    expect(LEGAL_LINKS.sealing).toMatchObject({ to: '/sealing', permission: 'seal.read' });
    expect(LEGAL_LINKS['settings-tsa']).toMatchObject({
      to: '/settings/kamusm',
      permission: 'settings.manage',
    });
  });

  it.each([
    ['admin', '/sealing', '/settings/kamusm'],
    ['auditor', '/sealing', null],
    ['hr', null, null],
    ['viewer', null, null],
  ])('%s: sealing %s, settings %s', (role, sealing, settings) => {
    expect(linkFor(LEGAL_REASONS.unstamped_days, as(role))?.to ?? null).toBe(sealing);
    expect(linkFor(LEGAL_REASONS.provider_not_legal, as(role))?.to ?? null).toBe(settings);
  });

  it('show nothing for a reason without a link or with a key this version does not know', () => {
    expect(linkFor(LEGAL_REASONS.crl_stale, () => true)).toBeNull();
    expect(linkFor({ link: 'reports' as 'sealing' }, () => true)).toBeNull();
  });
});
