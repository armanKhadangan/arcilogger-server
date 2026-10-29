import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { Toaster } from '@/components/ui/toast';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ApiError } from '@/lib/api';
import { AuthProvider } from '@/lib/auth';
import type { ComplianceStatus } from '@/lib/compliance-types';
import i18n, { setLanguage } from '@/lib/i18n';
import type { Me } from '@/lib/api-types';
import { makeMe } from '@/test/api-mock';
import {
  allOkCompliance,
  COMPLIANCE_CARD_IDS,
  COMPLIANCE_NOTICE,
  complianceStatus,
  W4_NOW,
} from '@/test/w4-fixtures';

import { ComplianceView, type ComplianceViewProps } from './compliance-view';

/*
 * Uyum Durumu (Wave 4 contract 2.8.1, tests of §3 W4-C): the ten cards with icon and text, the
 * notice, the links and actions by permission (the auditor of D15), the English switch, and an
 * error that keeps the cards shown. The view gets its data as props (W4-F adds the hooks).
 */

const ADMIN = makeMe('admin');
/** The auditor once W4-F grants `compliance.read` (D15). */
const AUDITOR: Me = {
  ...makeMe('auditor'),
  permissions: [...makeMe('auditor').permissions, 'compliance.read'],
};
/** `compliance.read` alone: no page a card links to may be opened. */
const READER: Me = { ...makeMe('viewer'), permissions: ['compliance.read'] };

const TITLES_TR = [
  'Log kaynakları veri gönderiyor',
  'Dünün kayıtları mühürlendi ve doğrulandı',
  'Mühür zinciri bütün',
  'Saklama süresi',
  'Kamu SM kontörü ve zaman damgası sertifikası',
  'Sunucu saati',
  'İçerik filtresi kayıtları',
  'DHCP ve ARP kayıtları (erişim kayıtları)',
  'İkinci kopya (replika)',
  'Yapılandırma ve veritabanı yedekleri',
];

/** The tone texts of the fixtures' story (contract 2.8.5 `compliance.tone.*`). */
const STORY_TR = [
  'Sorun',
  'Tamam',
  'Tamam',
  'Tamam',
  'Dikkat',
  'Tamam',
  'Dikkat',
  'Sorun',
  'Dikkat',
  'Bilgi yok',
];
const STORY_EN = [
  'Problem',
  'OK',
  'OK',
  'OK',
  'Attention',
  'OK',
  'Attention',
  'Problem',
  'Attention',
  'No data',
];

function renderView(props: Partial<ComplianceViewProps> = {}, me: Me = ADMIN) {
  const handlers = {
    onRefresh: vi.fn(),
    onExport: vi.fn(),
    onConfirmBackup: vi.fn(() => Promise.resolve({})),
  };
  const all: ComplianceViewProps = {
    status: complianceStatus(),
    error: null,
    loading: false,
    updatedAt: new Date(W4_NOW).getTime(),
    exporting: false,
    confirming: false,
    ...handlers,
    ...props,
  };
  const view = render(
    <MemoryRouter>
      <AuthProvider me={me}>
        <TooltipProvider>
          <ComplianceView {...all} />
        </TooltipProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
  return { ...view, ...handlers, props: all };
}

function cards() {
  return within(screen.getByRole('list', { name: /^(Denetimler|Checks)$/ })).getAllByRole('group');
}

function card(id: string) {
  return screen.getByTestId(`compliance-card-${id}`);
}

/** Every link target on the page. */
function hrefs(): string[] {
  return screen.getAllByRole('link').map((link) => link.getAttribute('href') ?? '');
}

afterEach(() => {
  act(() => {
    setLanguage('tr');
  });
});

describe('Uyum Durumu', () => {
  it('shows the ten cards with number, title and a badge of icon and text', () => {
    renderView();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Uyum Durumu');
    expect(
      screen.getByText('Kayıt toplama, mühürleme ve yedeklemenin teknik denetimleri.'),
    ).toBeInTheDocument();
    const shown = cards();
    expect(shown).toHaveLength(10);
    shown.forEach((item, index) => {
      expect(within(item).getByRole('heading', { level: 2 })).toHaveTextContent(
        `${index + 1}. ${TITLES_TR[index] ?? ''}`,
      );
      const badge = within(item).getByText(STORY_TR[index] ?? '', { selector: 'span' });
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    });
    expect(shown.map((item) => item.dataset.testid)).toEqual(
      COMPLIANCE_CARD_IDS.map((id) => `compliance-card-${id}`),
    );
  });

  it('shows a card’s reason, facts as a description list, document, checked time and note', () => {
    renderView();
    const tsa = card('tsa');
    const status = complianceStatus();
    const data = status.cards[4];
    if (!data) throw new Error('ten cards');
    expect(tsa).toHaveTextContent(data.reason_tr);
    const terms = within(tsa)
      .getAllByRole('term')
      .map((term) => term.textContent);
    expect(terms).toEqual(data.facts.map((fact) => fact.label_tr));
    const values = within(tsa)
      .getAllByRole('definition')
      .map((value) => value.textContent);
    expect(values).toEqual(data.facts.map((fact) => fact.value_tr));
    expect(tsa).toHaveTextContent('Belge: UYUM-DURUMU.md bölüm 2.5');
    expect(tsa).toHaveTextContent('Denetlendi 13.10.2026 07:00:00');
    const note = within(tsa).getByTestId('verify-on-site');
    expect(note).toHaveTextContent(`Sahada doğrulayın: ${data.verify_on_site_tr ?? ''}`);
    expect(note.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(within(card('sources')).queryByTestId('verify-on-site')).toBeNull();
  });

  it('shows the notice in an info alert and the summary line', () => {
    renderView();
    const notice = screen.getByTestId('compliance-notice');
    expect(notice).toHaveTextContent(COMPLIANCE_NOTICE.tr);
    expect(notice).toHaveAttribute('role', 'status');
    const summary = screen.getByTestId('compliance-summary');
    expect(summary).toHaveTextContent('10 denetimden 4 tanesi tamam');
    expect(summary).toHaveTextContent('Sorun: 2');
    expect(summary).toHaveTextContent('Dikkat: 3');
    expect(summary).toHaveTextContent('Bilgi yok: 1');
  });

  it('says when it was updated, refreshes and exports the PDF', async () => {
    const user = userEvent.setup();
    const { onRefresh, onExport } = renderView();
    const updated = screen.getByText('Güncellendi 07:00:00');
    expect(updated).toHaveAttribute('aria-live', 'polite');
    await user.click(screen.getByRole('button', { name: 'Yenile' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' }));
    expect(onExport).toHaveBeenCalledTimes(1);
  });

  it('gives the administrator every link, the wizard and the backup confirmation', () => {
    renderView();
    expect(
      within(card('sources')).getByRole('link', { name: 'Kaynaklar sayfasını aç' }),
    ).toHaveAttribute('href', '/sources');
    expect(within(card('sources')).getByRole('link', { name: 'Açık uyarı: 1' })).toHaveAttribute(
      'href',
      '/security?alert=41',
    );
    expect(
      within(card('clock')).getByRole('link', { name: 'Sistem sayfasını aç' }),
    ).toHaveAttribute('href', '/system');
    expect(screen.getByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toHaveAttribute(
      'href',
      '/settings/setup',
    );
    expect(screen.getAllByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toHaveLength(1);
    expect(
      within(card('backup')).getByRole('button', { name: 'Yapılandırma yedeğini aldım' }),
    ).toBeInTheDocument();
  });

  it('confirms the configuration copy in a dialog and closes it once recorded', async () => {
    const user = userEvent.setup();
    render(<Toaster />);
    const { onConfirmBackup } = renderView();
    await user.click(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }));
    const dialog = screen.getByRole('dialog', { name: 'Yapılandırma yedeğini aldım' });
    expect(dialog).toHaveTextContent(i18n.getFixedT('tr')('compliance.confirmBackupBody'));
    await user.click(within(dialog).getByRole('button', { name: 'İptal' }));
    expect(onConfirmBackup).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }));
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Onaylıyorum' }),
    );
    expect(onConfirmBackup).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    // The focus goes back to the button, and a message says the entry was written.
    expect(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toHaveFocus();
    expect(
      screen.getByText('Yapılandırma kopyasının onayı denetim kaydına yazıldı.'),
    ).toBeInTheDocument();
  });

  it('keeps the dialog open with the error of a refused confirmation', async () => {
    const user = userEvent.setup();
    const refused = new ApiError({
      status: 409,
      code: 'config_backup_unreadable',
      messageTr: 'Yapılandırma dosyası okunamadı.',
      messageEn: 'The configuration file could not be read.',
    });
    renderView({ onConfirmBackup: vi.fn(() => Promise.reject(refused)) });
    await user.click(screen.getByRole('button', { name: 'Yapılandırma yedeğini aldım' }));
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Onaylıyorum' }),
    );
    expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent(
      'Yapılandırma dosyası okunamadı.',
    );
  });

  it('hides the wizard link when every card is ok', () => {
    renderView({ status: allOkCompliance() });
    expect(screen.getByTestId('compliance-summary')).toHaveTextContent(
      '10 denetimden 10 tanesi tamam',
    );
    expect(screen.queryByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toBeNull();
  });

  it('hides every link the user may not open; the cards stay', () => {
    renderView({}, READER);
    expect(cards()).toHaveLength(10);
    expect(screen.queryAllByRole('link')).toEqual([]);
    expect(within(card('sources')).getByText('Açık uyarı: 1')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toBeNull();
  });

  it('gives the auditor the cards and the PDF, links to Mühürleme and Arama only (D15)', () => {
    renderView({}, AUDITOR);
    expect(cards()).toHaveLength(10);
    expect(screen.getByRole('button', { name: 'Uyum Durumu Raporu (PDF)' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Yapılandırma yedeğini aldım' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'İlk kurulum sihirbazını aç' })).toBeNull();
    const targets = hrefs();
    expect(new Set(targets)).toEqual(new Set(['/sealing', '/search']));
    for (const forbidden of ['/sources', '/security', '/settings', '/system'])
      expect(
        targets.filter((href) => href.startsWith(forbidden)),
        forbidden,
      ).toEqual([]);
  });

  it('switches to English: the server’s English texts and the English labels', () => {
    renderView();
    act(() => {
      setLanguage('en');
    });
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Compliance');
    expect(screen.getByTestId('compliance-notice')).toHaveTextContent(COMPLIANCE_NOTICE.en);
    expect(screen.getByTestId('compliance-summary')).toHaveTextContent('4 of 10 checks OK');
    const shown = cards();
    shown.forEach((item, index) => {
      const data = complianceStatus().cards[index];
      expect(within(item).getByRole('heading', { level: 2 })).toHaveTextContent(
        `${index + 1}. ${data?.title_en ?? ''}`,
      );
      expect(item).toHaveTextContent(data?.reason_en ?? '');
      expect(
        within(item).getByText(STORY_EN[index] ?? '', { selector: 'span' }),
      ).toBeInTheDocument();
    });
    expect(card('tsa')).toHaveTextContent('Document: COMPLIANCE-STATUS.md section 2.5');
    expect(card('tsa')).toHaveTextContent('Verify on site:');
    expect(card('sources')).toHaveTextContent('Open alerts: 1');
    // Named as the Panel cards' links (`dashboard.openPage`).
    expect(
      within(card('sources')).getByRole('link', { name: 'Open the Sources page' }),
    ).toHaveAttribute('href', '/sources');
    expect(
      screen.getByRole('button', { name: 'Compliance status report (PDF)' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'I have copied the configuration' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the first-run wizard' })).toBeInTheDocument();
    expect(screen.queryByText(TITLES_TR[0] ?? '')).toBeNull();
  });

  it('keeps the shown cards under the error of a failed refresh, with "Tekrar dene"', async () => {
    const user = userEvent.setup();
    const { onRefresh } = renderView({ error: new TypeError('Failed to fetch') });
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Denetim sonuçları okunamadı');
    await user.click(within(alert).getByRole('button', { name: 'Tekrar dene' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(cards()).toHaveLength(10);
    expect(screen.getByText('Güncellendi 07:00:00')).toBeInTheDocument();
  });

  it('shows a loading state before the first answer, and the error alone when it failed', () => {
    const { unmount } = renderView({ status: undefined, loading: true, updatedAt: 0 });
    expect(screen.getByRole('status', { name: 'Yükleniyor…' })).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Denetimler' })).toBeNull();
    expect(screen.queryByText(/Güncellendi/)).toBeNull();
    unmount();

    renderView({ status: undefined, error: new TypeError('Failed to fetch'), updatedAt: 0 });
    expect(screen.getByRole('alert')).toHaveTextContent('Denetim sonuçları okunamadı');
    expect(screen.queryByRole('list', { name: 'Denetimler' })).toBeNull();
    expect(screen.queryByRole('status', { name: 'Yükleniyor…' })).toBeNull();
  });

  it('shows the export as busy while the PDF is made', () => {
    renderView({ exporting: true });
    expect(screen.getByRole('button', { name: /Uyum Durumu Raporu \(PDF\)/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

/** The fixed texts of contract 2.8.5 (`compliance.*`), verbatim in both languages. */
describe('the fixed texts of Uyum Durumu', () => {
  const FIXED: Record<string, [tr: string, en: string]> = {
    'compliance.description': [
      'Kayıt toplama, mühürleme ve yedeklemenin teknik denetimleri.',
      'Technical checks of log collection, sealing and backups.',
    ],
    'compliance.summary': [
      '{{total}} denetimden {{ok}} tanesi tamam',
      '{{ok}} of {{total}} checks OK',
    ],
    'compliance.tone.ok': ['Tamam', 'OK'],
    'compliance.tone.warning': ['Dikkat', 'Attention'],
    'compliance.tone.error': ['Sorun', 'Problem'],
    'compliance.tone.neutral': ['Bilgi yok', 'No data'],
    'compliance.export': ['Uyum Durumu Raporu (PDF)', 'Compliance status report (PDF)'],
    'compliance.document': ['Belge: {{doc}}', 'Document: {{doc}}'],
    'compliance.verifyOnSite': ['Sahada doğrulayın', 'Verify on site'],
    'compliance.openAlerts': ['Açık uyarı: {{count}}', 'Open alerts: {{count}}'],
    'compliance.confirmBackup': ['Yapılandırma yedeğini aldım', 'I have copied the configuration'],
    'compliance.confirmBackupBody': [
      'arcilogger.toml, secret.key ve Caddy CA klasörünün (özel anahtarıyla) şifreli, çevrim ' +
        'dışı ve yedeklerden ayrı saklanan bir ortama kopyalandığını onaylıyorsunuz ' +
        '(secrets.json isteğe bağlı; YEDEKLEME.md bölüm 3). Onay, yapılandırmanın şimdiki ' +
        'SHA-256 değeriyle denetim kaydına yazılır.',
      'You confirm that arcilogger.toml, secret.key and the Caddy CA folder (with its private ' +
        'key) were copied to encrypted offline media kept apart from the backups (secrets.json ' +
        'optional; BACKUP.md section 3). The confirmation is written to the audit log with the ' +
        "configuration's current SHA-256.",
    ],
    'compliance.openWizard': ['İlk kurulum sihirbazını aç', 'Open the first-run wizard'],
  };

  it.each(Object.entries(FIXED))('%s', (key, [tr, en]) => {
    expect(i18n.getResource('tr', 'translation', key)).toBe(tr);
    expect(i18n.getResource('en', 'translation', key)).toBe(en);
  });
});

/** Fixtures used across the tests above: the story's alerts and statuses stay as assumed. */
describe('the fixtures behind these tests', () => {
  it('tell the story of 4 ok, 3 warning, 2 error and 1 neutral card', () => {
    const status: ComplianceStatus = complianceStatus();
    expect(status.counts).toEqual({ ok: 4, warning: 3, error: 2, neutral: 1 });
    expect(status.cards.find((item) => item.id === 'sources')?.alerts.map((a) => a.id)).toEqual([
      41,
    ]);
  });
});
