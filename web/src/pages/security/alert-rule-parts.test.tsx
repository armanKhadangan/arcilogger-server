import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { AlertDetailWithRules } from '@/lib/alert-rule-types';
import type { Role } from '@/lib/api-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import {
  PERSON_ALERT,
  RULE_ALERT,
  RULE_EVIDENCE,
  SUMMARY_ADDITIONS,
} from '@/test/alert-rule-samples';
import { renderApp } from '@/test/render';
import { ALERTS, licenseSample } from '@/test/samples';

function serve(
  role: Role,
  {
    alert = RULE_ALERT,
    assign = () => jsonResponse(200, { ...RULE_ALERT, assignee: { id: 1, username: 'tolga' } }),
    grant = [],
  }: { alert?: AlertDetailWithRules; assign?: () => Response; grant?: string[] } = {},
) {
  const me = makeMe(role);
  me.permissions = [...me.permissions, ...grant];
  return installFetch((request) => {
    switch (`${request.method} ${request.path}`) {
      case 'GET /api/v1/auth/me':
        return jsonResponse(200, me);
      case 'GET /api/v1/license':
        return jsonResponse(200, licenseSample('trial'));
      case 'GET /api/v1/alerts':
        return jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 });
      case 'GET /api/v1/alerts/summary':
        return jsonResponse(200, { ...ALERTS.summary, ...SUMMARY_ADDITIONS });
      case `GET /api/v1/alerts/${alert.id}`:
        return jsonResponse(200, alert);
      case `GET /api/v1/alerts/${alert.id}/evidence`:
        return jsonResponse(200, RULE_EVIDENCE);
      case `POST /api/v1/alerts/${alert.id}/assign`:
        return assign();
      case 'GET /api/v1/users':
        return jsonResponse(200, { items: [], next_cursor: null });
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Güvenlik — an alert of a rule (Wave 6 §2.6.2)', () => {
  it('a refused assignment says why', async () => {
    const user = userEvent.setup();
    const calls = serve('admin', {
      assign: () =>
        errorResponse(422, 'alert_assignee_invalid', {
          tr: 'Bu kullanıcıya uyarı atanamaz.',
          en: 'Alerts cannot be assigned to this user.',
        }),
    });
    renderApp('/security?alert=9001');
    const sheet = await screen.findByRole('dialog');
    await user.click(await within(sheet).findByLabelText('Uyarıyı kim inceliyor'));
    await user.click(await screen.findByRole('option', { name: /\(ben\)$/ }));
    expect(await within(sheet).findByTestId('assign-error')).toHaveTextContent(
      'Bu kullanıcıya uyarı atanamaz.',
    );
    expect(calls.filter((call) => call.path === '/api/v1/alerts/9001/assign')).toHaveLength(1);
  });

  it('without alerts.manage the assignee is only shown', async () => {
    serve('auditor', {
      alert: { ...RULE_ALERT, assignee: { id: 1, username: 'tolga' } },
      grant: ['alerts.read'],
    });
    renderApp('/security?alert=9001');
    const sheet = await screen.findByRole('dialog');
    expect(await within(sheet).findByTestId('alert-assignee')).toHaveTextContent('tolga');
    expect(within(sheet).queryByLabelText('Uyarıyı kim inceliyor')).not.toBeInTheDocument();
  });

  it('evidence without the raw-line permission: the rows, no button', async () => {
    const user = userEvent.setup();
    serve('hr', { grant: ['alerts.read'] });
    renderApp('/security?alert=9001');
    const sheet = await screen.findByRole('dialog');
    await user.click(await within(sheet).findByRole('tab', { name: 'Kanıt (47)' }));
    const evidence = await within(sheet).findByTestId('alert-evidence');
    expect(within(evidence).getAllByTestId('evidence-row')).toHaveLength(3);
    expect(within(evidence).queryByRole('button', { name: /Ham satırı aç/ })).toBeNull();
    expect(evidence).toHaveTextContent(
      'Ham satırı açmak için arama ya da 5651 sorgu yetkisi gerekir.',
    );
  });

  it('an alert without kept evidence has no tabs; the person only when sent', async () => {
    serve('admin', { alert: PERSON_ALERT });
    renderApp('/security?alert=9002');
    const sheet = await screen.findByRole('dialog');
    expect(await within(sheet).findByTestId('alert-person')).toHaveTextContent('Kişi: Deniz Örnek');
    expect(within(sheet).queryByRole('tab')).not.toBeInTheDocument();
  });

  it('the summary gains the 30 days and the top lists', async () => {
    serve('admin');
    renderApp('/security');
    const trends = await screen.findByTestId('security-trends');
    expect(within(trends).getByRole('heading', { name: 'Son 30 gün' })).toBeInTheDocument();
    expect(
      within(screen.getByTestId('top-rules')).getByRole('link', { name: 'R01' }),
    ).toHaveAttribute('href', '/security?rule=R01');
    expect(screen.getByTestId('top-keys')).toHaveTextContent('ip:172.20.30.41');
  });
});
