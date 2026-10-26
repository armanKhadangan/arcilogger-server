import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';

import { AuthProvider } from '@/lib/auth';
import { ReauthProvider } from '@/lib/reauth';
import type { AdminPolicyOut, TsaSection } from '@/lib/settings-types';
import { ZamaneStep } from '@/pages/settings/kamusm-uploads';
import { makeMe } from '@/test/api-mock';
import { testQueryClient } from '@/test/render';

import { ConsoleCommand } from './console-command';
import { CONSOLE_ADMIN, approveJarCommand, restoreVersionCommand } from './console-commands';

/*
 * The console commands the administrator runs in an elevated prompt are built only from a value
 * of the expected form (R7): a value of a compromised API that carries more than a SHA-256 or a
 * version number gives no command line at all.
 */

const SHA = 'ab'.repeat(32);
const INJECTED = `${SHA} & powershell -c "Add-LocalGroupMember Administrators kotu"`;

const POLICY: AdminPolicyOut = {
  enabled: true,
  source_networks: ['172.20.0.0/16'],
  min_prefix_v4: 16,
  max_sources: 500,
  hold_hours: 0,
  jar_hold_hours: 24,
  tsa_test_per_day: 3,
};

function tsa(staged: string): TsaSection {
  return {
    provider: 'zamane',
    server: 'test',
    credit_alert_threshold: 500,
    timeout_seconds: 60,
    java: null,
    java_choices: [],
    zamane: {
      present: true,
      sha256: 'c'.repeat(64),
      release: null,
      trusted: 'installer',
      staged: [{ sha256: staged, effective_at: '2026-10-27T10:00:00+03:00' }],
    },
    roots: [],
    secrets: {},
    readiness: { production: false, missing: [] },
    seal_ready: { ok: true, causes: [] },
  };
}

function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={testQueryClient()}>
      <MemoryRouter>
        <AuthProvider me={makeMe('admin')}>
          <ReauthProvider>{children}</ReauthProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderZamane(staged: string) {
  render(
    <Providers>
      <ZamaneStep
        tsa={tsa(staged)}
        etag="tsa:1"
        writable
        policy={POLICY}
        requestId={null}
        onStarted={() => undefined}
      />
    </Providers>,
  );
}

describe('console commands', () => {
  it("runs the installation's interpreter by its full path (not on PATH)", () => {
    expect(CONSOLE_ADMIN).toBe(
      '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin',
    );
  });

  it('approve-jar only for 64 lowercase hexadecimal characters', () => {
    expect(approveJarCommand(SHA)).toBe(`${CONSOLE_ADMIN} approve-jar ${SHA}`);
    for (const value of [INJECTED, SHA.toUpperCase(), SHA.slice(1), `${SHA}\n`, '', `${SHA}0`])
      expect(approveJarCommand(value), JSON.stringify(value)).toBeNull();
  });

  it('versions restore only for a positive integer', () => {
    expect(restoreVersionCommand(17)).toBe(`${CONSOLE_ADMIN} versions restore 17`);
    for (const value of [0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53])
      expect(restoreVersionCommand(value), String(value)).toBeNull();
  });

  it('a refused value shows why and no command', () => {
    render(<ConsoleCommand label="Sunucu konsolu komutu" command={null} />);
    expect(screen.getByTestId('console-command-refused')).toHaveTextContent(
      'Sunucunun bildirdiği değer beklenen biçimde değil',
    );
    expect(screen.queryByRole('button', { name: 'Kopyala' })).not.toBeInTheDocument();
  });

  it('a staged client with a malformed SHA-256 renders no command', () => {
    renderZamane(INJECTED);
    const staged = screen.getByTestId('zamane-staged');
    expect(staged).not.toHaveTextContent('approve-jar');
    expect(staged).not.toHaveTextContent('powershell');
    expect(screen.getByTestId('console-command-refused')).toBeInTheDocument();
  });

  it('a staged client with a well-formed SHA-256 renders its command', () => {
    renderZamane(SHA);
    expect(screen.getByTestId('zamane-staged')).toHaveTextContent(
      `${CONSOLE_ADMIN} approve-jar ${SHA}`,
    );
    expect(screen.queryByTestId('console-command-refused')).not.toBeInTheDocument();
  });
});
