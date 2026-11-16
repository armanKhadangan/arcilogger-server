import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';
import { WEBHOOKS } from '@/test/webhook-samples';

describe("Ayarlar → Webhook'lar — the test refused (C10)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('a webhook that turned not ready after the list was read: the refusal is said', async () => {
    const user = userEvent.setup();
    const calls = installFetch((request) => {
      switch (`${request.method} ${request.path}`) {
        case 'GET /api/v1/auth/me':
          return jsonResponse(200, makeMe('admin'));
        case 'GET /api/v1/license':
          return jsonResponse(200, licenseSample('trial'));
        case 'GET /api/v1/webhooks':
          return jsonResponse(200, WEBHOOKS);
        case 'POST /api/v1/webhooks/siem/test':
          return errorResponse(409, 'webhook_not_ready', {
            tr: 'Bu webhook gönderime hazır değil; durumuna bakın.',
            en: 'This webhook is not ready to send; see its state.',
          });
        default:
          return errorResponse(404, 'not_found');
      }
    });
    renderApp('/settings/webhooks');
    const row = (await screen.findAllByTestId('webhook-row')).find(
      (item) => item.dataset.name === 'siem',
    );
    if (!row) throw new Error('siem row missing');
    await user.click(within(row).getByRole('button', { name: 'Test gönder: siem' }));
    expect(await screen.findByTestId('webhook-test-error')).toHaveTextContent(
      'Bu webhook gönderime hazır değil; durumuna bakın.',
    );
    expect(screen.queryByTestId('webhook-test')).not.toBeInTheDocument();
    expect(calls.filter((call) => call.path === '/api/v1/webhooks/siem/test')).toHaveLength(1);
  });
});
