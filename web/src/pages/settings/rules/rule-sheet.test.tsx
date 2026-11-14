import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Role } from '@/lib/api-types';
import type { RuleList } from '@/lib/rules-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { licenseSample } from '@/test/samples';
import { DRY_RUN, RULE_ERRORS, RULES, RULES_LOCKED } from '@/test/rules-samples';

function serve(
  role: Role,
  {
    list = RULES,
    put = () => jsonResponse(200, RULES.rules[0]),
  }: { list?: RuleList; put?: () => Response } = {},
) {
  return installFetch((request) => {
    switch (`${request.method} ${request.path}`) {
      case 'GET /api/v1/auth/me':
        return jsonResponse(200, makeMe(role));
      case 'GET /api/v1/license':
        return jsonResponse(200, licenseSample('trial'));
      case 'GET /api/v1/rules':
        return jsonResponse(200, list);
      case 'PUT /api/v1/rules/R08':
      case 'PUT /api/v1/rules/R01':
        return put();
      case 'POST /api/v1/rules/R01/dry-run':
        return jsonResponse(200, DRY_RUN);
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a rule in the editor (Wave 6 §2.3)', () => {
  it('a refusal naming a parameter is shown at that parameter, focused', async () => {
    const user = userEvent.setup();
    const calls = serve('admin', {
      put: () =>
        jsonResponse(422, {
          error: { ...RULE_ERRORS.rule_param_invalid?.error },
        }),
    });
    renderApp('/settings/rules?rule=R08');
    const sheet = await screen.findByTestId('rule-sheet');
    const servers = within(sheet).getByLabelText('İzin verilen DNS sunucuları');
    await user.type(servers, '172.20.0.10');
    await user.click(within(sheet).getByRole('button', { name: 'Kaydet' }));
    expect(
      await within(sheet).findByText('Kural ayarı geçersiz: params.allowed_dns_servers.'),
    ).toBeInTheDocument();
    expect(servers).toHaveAttribute('aria-invalid', 'true');
    // After the next paint (`focusFirstError`).
    await waitFor(() => {
      expect(servers).toHaveFocus();
    });
    expect(calls.filter((call) => call.method === 'PUT').at(-1)?.body).toEqual({
      params: { allowed_dns_servers: ['172.20.0.10'] },
    });
  });

  it('any other refusal above the buttons', async () => {
    const user = userEvent.setup();
    serve('admin', { put: () => jsonResponse(403, RULE_ERRORS.module_not_licensed) });
    renderApp('/settings/rules?rule=R01');
    const sheet = await screen.findByTestId('rule-sheet');
    await user.clear(within(sheet).getByLabelText('Farklı port sayısı'));
    await user.type(within(sheet).getByLabelText('Farklı port sayısı'), '31');
    await user.click(within(sheet).getByRole('button', { name: 'Kaydet' }));
    expect(await within(sheet).findByTestId('rule-save-error')).toHaveTextContent(
      'Bu özellik lisansınızda yok: Güvenlik kuralları.',
    );
  });

  it('every parameter by its kind, with its default', async () => {
    serve('admin');
    renderApp('/settings/rules?rule=R17');
    const sheet = await screen.findByTestId('rule-sheet');
    expect(within(sheet).getByLabelText('İşlemci kullanımı')).toHaveValue('80');
    expect(within(sheet).getByText('Yüzde (%). Varsayılan: %80')).toBeInTheDocument();
    expect(within(sheet).getByText('Saniye: 10 dk. Varsayılan: 10 dk')).toBeInTheDocument();
    const phrases = within(sheet).getByLabelText('Koruma kipi ifadeleri (logdesc)');
    expect(phrases.tagName).toBe('TEXTAREA');
    expect(phrases).toHaveValue('conserve');
    expect(within(sheet).getByTestId('rule-last-error')).toHaveTextContent(
      'Son çalışma hatası: canceling statement due to statement timeout',
    );
    expect(within(sheet).getByTestId('rule-verify')).toHaveTextContent('Sahada doğrula');
    expect(
      within(sheet).getByRole('heading', { name: /R17 · Güvenlik duvarı sağlığı/ }),
    ).toBeVisible();
    expect(
      within(sheet).getByText(/Durum kuralı: koşul ortadan kalkınca uyarı kendiliğinden kapanır/),
    ).toBeInTheDocument();
  });

  it('the trial run: the count, the first keys and how long it took', async () => {
    const user = userEvent.setup();
    serve('admin');
    renderApp('/settings/rules?rule=R01');
    const sheet = await screen.findByTestId('rule-sheet');
    await user.click(within(sheet).getByRole('button', { name: 'Son 24 saatte dene' }));
    const result = await within(sheet).findByTestId('rule-dry-run-result');
    expect(result).toHaveTextContent('Son 24 saatte bu ayarlarla 3 uyarı açılırdı');
    expect(
      within(result)
        .getAllByRole('rowheader')
        .map((cell) => cell.textContent),
    ).toEqual(['172.20.41.17', '172.20.10.23', '172.20.62.5']);
    expect(result).toHaveTextContent('Deneme 812 ms sürdü.');
  });

  it('locked by the licence: readable, nothing to change, no trial', async () => {
    serve('admin', { list: RULES_LOCKED });
    renderApp('/settings/rules?rule=R01');
    const sheet = await screen.findByTestId('rule-sheet');
    expect(within(sheet).getByLabelText('Farklı port sayısı')).toBeDisabled();
    expect(within(sheet).getByLabelText('Kural açık')).toBeDisabled();
    expect(within(sheet).queryByRole('button', { name: 'Kaydet' })).not.toBeInTheDocument();
    expect(
      within(sheet).queryByRole('button', { name: 'Son 24 saatte dene' }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId('rules-locked')).toBeInTheDocument();
  });
});
