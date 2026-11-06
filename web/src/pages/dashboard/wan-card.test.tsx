import { act, render, screen, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { MemoryRouter } from 'react-router-dom';

import type { WanLine, WanRates } from '@/lib/activity-types';
import type { Role } from '@/lib/api-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { setLanguage } from '@/lib/i18n';
import { renderApp } from '@/test/render';
import { DASHBOARD, licenseSample, USAGE } from '@/test/samples';

import { WanCard } from './wan-card';
import {
  capacityShare,
  formatBps,
  polledLines,
  shareText,
  sparkValues,
  wanCardState,
  wanRequested,
} from './wan-model';

/** `GET /activity/wan` as the real API answered it (`e2e/samples/activity-wan.json`, W5-N). */
const WAN = JSON.parse(
  readFileSync(join(process.cwd(), 'e2e', 'samples', 'activity-wan.json'), 'utf8'),
) as {
  hour: WanRates;
  day: WanRates;
  empty: WanRates;
};

const ADMIN = new Set(['dashboard.view', 'system.read', 'sources.manage']);
const VIEWER = new Set(['dashboard.view']);

describe('the Panel card "İnternet hattı" — its model (Wave 5 §6 N7)', () => {
  it('shows only the lines read over SNMP: a switched-off source is left out', () => {
    expect(polledLines(WAN.hour).map((line) => `${line.source}/${line.name}`)).toEqual([
      'fw-1/wan1',
      'fw-1/wan2',
    ]);
    expect(polledLines(undefined)).toEqual([]);
  });

  it('the card, the link to Kaynaklar for an administrator, or nothing', () => {
    expect(wanCardState(WAN.hour, VIEWER)).toBe('lines');
    expect(wanCardState(WAN.empty, ADMIN)).toBe('setup');
    expect(wanCardState(WAN.empty, VIEWER)).toBe('hidden');
    // Only the switched-off line: no source has SNMP on.
    const off = { ...WAN.hour, lines: WAN.hour.lines.filter((line) => line.status === 'disabled') };
    expect(wanCardState(off, ADMIN)).toBe('setup');
    // No answer (loading, refused, failed): nothing.
    expect(wanCardState(undefined, ADMIN)).toBe('hidden');
  });

  it('is asked for with dashboard.view or system.read only (N6)', () => {
    expect(wanRequested(VIEWER)).toBe(true);
    expect(wanRequested(new Set(['system.read']))).toBe(true);
    expect(wanRequested(new Set(['usage.person']))).toBe(false);
  });

  it('rates as text and a share of the capacity, as Canlı Durum writes them', () => {
    expect(formatBps(null, 'tr')).toBe('—');
    expect(formatBps(850_000, 'tr')).toBe('850 kbit/s');
    expect(formatBps(34_000_000, 'tr')).toBe('34,0 Mbit/s');
    expect(formatBps(1_250_000_000, 'en')).toBe('1.25 Gbit/s');
    expect(capacityShare(34_000_000, 100)).toBeCloseTo(0.34);
    expect(capacityShare(34_000_000, undefined)).toBeNull();
    expect(capacityShare(null, 100)).toBeNull();
    expect(shareText(0.34, 'tr')).toBe('%34');
    expect(shareText(1.05, 'en')).toBe('105%');
  });

  it('the sparkline in Mbit/s, oldest first', () => {
    const line = WAN.hour.lines.find((item) => item.name === 'wan1');
    if (!line) throw new Error('wan1 missing from the sample');
    const values = sparkValues(line, 'download');
    expect(values).toHaveLength(line.series.length);
    expect(values[0]).toBeCloseTo((line.series[0]?.download_bps ?? 0) / 1_000_000);
    expect(sparkValues(line, 'upload')[0]).toBeCloseTo(
      (line.series[0]?.upload_bps ?? 0) / 1_000_000,
    );
  });
});

function serve(role: Role, wan: () => Response = () => jsonResponse(200, WAN.hour)) {
  return installFetch((request) => {
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, makeMe(role));
      case '/api/v1/license':
        return jsonResponse(200, licenseSample('trial'));
      case '/api/v1/dashboard/summary': {
        const summary = role === 'hr' ? null : DASHBOARD[role];
        return summary ? jsonResponse(200, summary) : errorResponse(403, 'forbidden');
      }
      case '/api/v1/usage/company':
        return jsonResponse(200, USAGE.company);
      case '/api/v1/activity/wan':
        return wan();
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the Panel card "İnternet hattı" — on the Panel (Wave 5 §6 N7)', () => {
  it('each line now, its share of the capacity, a failing line said so; links to Canlı Durum', async () => {
    const calls = serve('admin');
    renderApp('/');
    const card = await screen.findByTestId('card-wan');
    expect(within(card).getByRole('heading', { name: 'İnternet hattı' })).toBeInTheDocument();
    const lines = within(card).getAllByTestId('wan-line');
    expect(lines.map((line) => line.dataset.line)).toEqual(['fw-1/wan1', 'fw-1/wan2']);
    const [wan1, wan2] = lines as [HTMLElement, HTMLElement];
    expect(wan1).toHaveTextContent('İndirme34,0 Mbit/skapasite: %34');
    expect(wan1).toHaveTextContent('Yükleme5,0 Mbit/skapasite: %25');
    expect(wan1).toHaveTextContent('Son okuma 07:01:18');
    expect(within(wan1).getAllByRole('img')).toHaveLength(2);
    // No answer from the device: no figure, the reason, the last good reading.
    expect(wan2).toHaveTextContent('Yanıt yok');
    expect(wan2).toHaveTextContent('İndirme—');
    expect(wan2).toHaveTextContent('Son okuma 06:49:18');
    expect(within(wan2).queryByRole('img')).not.toBeInTheDocument();
    expect(
      calls
        .filter((call) => call.path === '/api/v1/activity/wan')
        .map((call) => call.params.get('range')),
    ).toEqual(['60m']);
  });

  it('a line not read shows no rate, and an interface status in words', () => {
    const [off, wan1, wan2] = WAN.hour.lines as [WanLine, WanLine, WanLine];
    const lines = [
      off,
      { ...wan1, oper_status: 'lower_layer_down' as const },
      // Its last rates are kept by the API, up to an hour old: not shown as "now".
      { ...wan2, download_bps: 41_000_000, upload_bps: 6_000_000 },
    ];
    render(
      <MemoryRouter>
        <WanCard rates={{ ...WAN.hour, lines }} to={null} />
      </MemoryRouter>,
    );
    const [first, second] = screen.getAllByTestId('wan-line') as [HTMLElement, HTMLElement];
    expect(first).toHaveTextContent('Arayüz: alt katman kapalı');
    expect(first).not.toHaveTextContent('lower_layer_down');
    expect(second).toHaveTextContent('Yanıt yok');
    expect(second).toHaveTextContent('İndirme—');
    expect(second).toHaveTextContent('Yükleme—');
    expect(second).not.toHaveTextContent('41,0 Mbit/s');
  });

  it('links to Canlı Durum when the user may open it', () => {
    render(
      <MemoryRouter>
        <WanCard rates={WAN.hour} to="/activity" />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Canlı Durum sayfasını aç' })).toHaveAttribute(
      'href',
      '/activity',
    );
  });

  it('no source with SNMP on: the administrator gets the way to Kaynaklar, others nothing', async () => {
    serve('admin', () => jsonResponse(200, WAN.empty));
    const view = renderApp('/');
    const setup = await screen.findByTestId('card-wan-setup');
    expect(setup).toHaveTextContent('Hat hızı henüz okunmuyor.');
    expect(
      within(setup).getByRole('link', { name: "Ayarlar → Kaynaklar'da SNMP'yi aç" }),
    ).toHaveAttribute('href', '/settings/sources');
    expect(screen.queryByTestId('card-wan')).not.toBeInTheDocument();
    view.unmount();
    vi.unstubAllGlobals();

    serve('viewer', () => jsonResponse(200, WAN.empty));
    renderApp('/');
    await screen.findByTestId('card-usage');
    await settle();
    expect(screen.queryByTestId('card-wan-setup')).not.toBeInTheDocument();
    expect(screen.queryByTestId('card-wan')).not.toBeInTheDocument();
  });

  it('auditor and viewer see the lines; HR never asks for them', async () => {
    for (const role of ['auditor', 'viewer'] satisfies Role[]) {
      serve(role);
      const view = renderApp('/');
      expect(await screen.findByTestId('card-wan')).toBeInTheDocument();
      view.unmount();
      vi.unstubAllGlobals();
    }
    const calls = serve('hr');
    renderApp('/');
    await screen.findByTestId('card-usage');
    await settle();
    expect(screen.queryByTestId('card-wan')).not.toBeInTheDocument();
    expect(calls.filter((call) => call.path === '/api/v1/activity/wan')).toEqual([]);
  });

  it('a failed answer hides the card (Canlı Durum and Sistem say more)', async () => {
    serve('admin', () => errorResponse(500, 'internal_error'));
    renderApp('/');
    await screen.findByTestId('card-usage');
    await settle();
    expect(screen.queryByTestId('card-wan')).not.toBeInTheDocument();
    expect(screen.queryByTestId('card-wan-setup')).not.toBeInTheDocument();
  });

  it('in English', async () => {
    setLanguage('en');
    try {
      serve('admin');
      renderApp('/');
      const card = await screen.findByTestId('card-wan');
      expect(within(card).getByRole('heading', { name: 'Internet line' })).toBeInTheDocument();
      const [wan1, wan2] = within(card).getAllByTestId('wan-line') as [HTMLElement, HTMLElement];
      expect(wan1).toHaveTextContent('Download34.0 Mbit/s34% of capacity');
      expect(wan2).toHaveTextContent('No answer');
    } finally {
      setLanguage('tr');
    }
  });
});
