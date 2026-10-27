import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  setCsrfCookie,
  type RecordedRequest,
} from '@/test/api-mock';
import { handoff } from '@/lib/handoff';
import { renderApp } from '@/test/render';
import { EVIDENCE, need } from '@/test/samples';

const NOW = new Date('2026-10-13T12:00:00Z');

/** Whether the API accepts `include_audit_log` (`@/lib/api-features`), switched per test. */
const features = vi.hoisted(() => ({ auditLog: false }));
vi.mock('@/lib/api-features', () => ({ evidenceAuditLogAccepted: () => features.auditLog }));

const AUDIT_LOG_LABEL = 'ARCILOGGER denetim kaydını ekle (yalnızca iç denetim için)';

function fakeApi(create: (request: RecordedRequest) => Response, me = makeMe('admin')) {
  setCsrfCookie('csrf-1');
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me') return jsonResponse(200, me);
    if (request.path === '/api/v1/evidence' && request.method === 'POST') return create(request);
    if (request.path === '/api/v1/evidence')
      return jsonResponse(200, { items: [], total: 0, limit: 200, offset: 0 });
    if (request.path === '/api/v1/lookups')
      return jsonResponse(200, { items: [], next_cursor: null, all_users: true });
    return errorResponse(404, 'not_found');
  });
}

/** The wizard opened from the lookup page ("Kanıt Paketi" → `/evidence?lookup=2`). */
async function openFromLookup() {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
  renderApp('/evidence?lookup=2');
  const wizard = await screen.findByRole('dialog', { name: 'Yeni kanıt paketi' });
  return { user, wizard };
}

function posted(calls: RecordedRequest[]) {
  return calls.find((call) => call.method === 'POST' && call.path === '/api/v1/evidence')?.body;
}

/** The summary's value for `name`. */
function summaryValue(wizard: HTMLElement, name: string): string {
  const term = within(wizard)
    .getAllByRole('term')
    .find((item) => item.textContent === name);
  return term?.nextElementSibling?.textContent ?? '';
}

/**
 * What `read` finds in the first frame with the wizard on screen when the app opens at `path`: a
 * MutationObserver runs after each commit, before React's next scheduled work (an effect's state
 * update) can change it. A wizard that fills itself in an effect shows an empty frame first, and
 * a test reading it right after `findByRole` fails on a slow runner.
 */
async function firstFrame<T>(path: string, read: (wizard: HTMLElement) => T): Promise<T[]> {
  const seen: T[] = [];
  const observer = new MutationObserver(() => {
    const wizard = document.querySelector<HTMLElement>('[role="dialog"]');
    if (wizard !== null && seen.length === 0) seen.push(read(wizard));
  });
  observer.observe(document.body, { childList: true, subtree: true });
  try {
    renderApp(path);
    await screen.findByRole('dialog', { name: 'Yeni kanıt paketi' });
  } finally {
    observer.disconnect();
  }
  return seen;
}

async function openWizard() {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
  await user.click(await screen.findByRole('button', { name: 'Yeni paket' }));
  const wizard = await screen.findByRole('dialog', { name: 'Yeni kanıt paketi' });
  return { user, wizard };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  features.auditLog = false;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setCsrfCookie(null);
});

describe('EvidenceWizard', () => {
  it('walks scope → content → protection → summary, checking each step', async () => {
    const created = need(EVIDENCE.created_password, 'created package');
    const calls = fakeApi(() => jsonResponse(202, created));
    renderApp('/evidence');
    const { user, wizard } = await openWizard();
    const steps = within(wizard).getByRole('list', { name: 'Paket oluşturma adımları' });
    expect(within(steps).getByText('Kapsam').closest('li')).toHaveAttribute('aria-current', 'step');

    // Scope: required days, then a day that is not closed yet.
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getAllByText('Bu alan zorunludur.')).toHaveLength(2);
    await user.type(within(wizard).getByLabelText('İlk gün'), '04.10.2026');
    await user.type(within(wizard).getByLabelText('Son gün'), '13.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(
      within(wizard).getByText('Yalnızca bitmiş (mühürlenmiş) günler seçilebilir.'),
    ).toBeInTheDocument();
    await user.clear(within(wizard).getByLabelText('Son gün'));
    await user.type(within(wizard).getByLabelText('Son gün'), '05.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));

    // Content: an extract needs its lines.
    expect(within(steps).getByText('İçerik').closest('li')).toHaveAttribute('aria-current', 'step');
    await user.click(within(wizard).getByRole('radio', { name: /^Alıntı/ }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getByText('Alıntı için en az bir satır girin.')).toBeInTheDocument();
    await user.click(within(wizard).getByRole('radio', { name: /Tam günler/ }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));

    // Protection, then the summary of what will be asked.
    await user.click(within(wizard).getByLabelText('Paketi parola ile koru (AES-256)'));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getByText('04.10.2026 – 05.10.2026')).toBeInTheDocument();
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));

    // The password is shown once; the dialog stays until it was saved.
    const shown = await screen.findByRole('dialog', { name: 'Paket parolası' });
    expect(within(shown).getByText(need(created.password, 'password'))).toBeInTheDocument();
    const finish = within(shown).getByRole('button', { name: 'Bitir' });
    expect(finish).toBeDisabled();
    expect(within(shown).queryByRole('button', { name: /Kapat/ })).not.toBeInTheDocument();
    await user.click(within(shown).getByLabelText('Parolayı güvenli bir yere kaydettim.'));
    expect(finish).toBeEnabled();

    const post = calls.find((call) => call.method === 'POST' && call.path === '/api/v1/evidence');
    expect(post?.body).toEqual({
      from: '2026-10-04',
      to: '2026-10-05',
      sources: null,
      extract: [],
      content: 'full_days',
      password: true,
      reference: null,
    });
    // Never in browser storage.
    const password = need(created.password, 'password');
    for (const storage of [window.localStorage, window.sessionStorage]) {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index) ?? '';
        expect(storage.getItem(key) ?? '').not.toContain(password);
      }
    }
  });

  it('shows a refusal of the server with its details', async () => {
    const refusal = need(EVIDENCE.errors.days_not_sealed, 'days_not_sealed sample');
    fakeApi(() => jsonResponse(refusal.status, refusal.body));
    renderApp('/evidence');
    const { user, wizard } = await openWizard();
    await user.type(within(wizard).getByLabelText('İlk gün'), '11.10.2026');
    await user.type(within(wizard).getByLabelText('Son gün'), '12.10.2026');
    for (let step = 0; step < 3; step += 1)
      await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(within(wizard).getByText(refusal.body.error.message_tr)).toBeInTheDocument();
    });
    expect(within(wizard).getByText('Günler: 11.10.2026, 12.10.2026')).toBeInTheDocument();
  });

  it('a full-day package of a lookup says it holds every source of the days (the default)', async () => {
    const calls = fakeApi(() => jsonResponse(202, need(EVIDENCE.created, 'created package')));
    const { user, wizard } = await openFromLookup();
    expect(within(wizard).getByRole('radio', { name: /5651 sorgusundan/ })).toBeChecked();
    expect(within(wizard).getByLabelText('Tüm kaynaklar')).toBeChecked();
    expect(within(wizard).getByLabelText('Tüm kaynaklar')).toHaveAccessibleDescription(
      /yalnızca sorgunun dayandığı kaynaklarınki değil/,
    );
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getByRole('radio', { name: /Tam günler/ })).toHaveAccessibleName(
      /kayıt gönderen her cihazın/,
    );
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));

    const sources = summaryValue(wizard, 'Kaynaklar');
    expect(sources).toMatch(/^Tüm kaynaklar/);
    expect(sources).toContain('yalnızca sorgunun dayandığı kaynaklarınki değil');
    expect(within(wizard).queryByText('sorgunun dayandığı kaynaklar')).not.toBeInTheDocument();
    expect(summaryValue(wizard, 'İçerik')).toBe('Tam günler');
    expect(summaryValue(wizard, 'Denetim kaydı')).toMatch(/^Dahil değil/);
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toBeDefined();
    });
    // No `sources`: the API packs every source of the days (docs/KANIT-PAKETI.md section 2).
    expect(posted(calls)).toEqual({
      lookup_id: 2,
      content: 'full_days',
      password: false,
      reference: null,
    });
  });

  it('a lookup package can be narrowed to chosen sources (plus the cited ones)', async () => {
    const calls = fakeApi(() => jsonResponse(202, need(EVIDENCE.created, 'created package')));
    const { user, wizard } = await openFromLookup();
    await user.click(within(wizard).getByLabelText('Tüm kaynaklar'));
    const field = within(wizard).getByLabelText('Kaynaklar', { exact: true });
    expect(field).toHaveAttribute('aria-required', 'true');
    expect(field).toHaveAccessibleDescription(/satırların kaynakları ayrıca eklenir/);
    await user.type(field, 'fortigate');
    for (let step = 0; step < 3; step += 1)
      await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(summaryValue(wizard, 'Kaynaklar')).toBe(
      'fortigate ve sorgunun dayandığı satırların kaynakları',
    );
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toBeDefined();
    });
    expect(posted(calls)).toEqual({
      lookup_id: 2,
      sources: ['fortigate'],
      content: 'full_days',
      password: false,
      reference: null,
    });
  });

  it('the summary speaks of the password in the future; no audit log option the API refuses', async () => {
    // The API's EvidenceCreate does not accept include_audit_log (yet): an administrator gets no
    // option, the summary has no line for it and the body never carries the field.
    const calls = fakeApi(() => jsonResponse(202, need(EVIDENCE.created, 'created package')));
    const { user, wizard } = await openFromLookup();
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).queryByLabelText(AUDIT_LOG_LABEL)).not.toBeInTheDocument();
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByLabelText('Paketi parola ile koru (AES-256)'));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(summaryValue(wizard, 'Parola koruması')).toBe(
      'Var (AES-256) — parola, paket oluşturulunca bir kez gösterilecek',
    );
    expect(within(wizard).queryByText(/gösterildi/)).not.toBeInTheDocument();
    expect(summaryValue(wizard, 'Denetim kaydı')).toMatch(/^Dahil değil/);
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toBeDefined();
    });
    expect(posted(calls)).not.toHaveProperty('include_audit_log');
  });

  it('once the API accepts it, an administrator may add the audit log by choice', async () => {
    features.auditLog = true;
    const calls = fakeApi(() => jsonResponse(202, need(EVIDENCE.created, 'created package')));
    const { user, wizard } = await openFromLookup();
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    const auditLog = within(wizard).getByLabelText(AUDIT_LOG_LABEL);
    expect(auditLog).not.toBeChecked();
    expect(auditLog).toHaveAccessibleDescription(/KVKK: veri en aza indirme/);
    await user.click(auditLog);
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(summaryValue(wizard, 'Denetim kaydı')).toMatch(/^Dahil —/);
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toBeDefined();
    });
    expect(posted(calls)).toMatchObject({ lookup_id: 2, include_audit_log: true });
  });

  it('offers the audit log to administrators only, even when the API accepts it', async () => {
    features.auditLog = true;
    // A role that may create packages without being an administrator (none has today).
    const auditor = makeMe('auditor');
    const me = { ...auditor, permissions: [...auditor.permissions, 'evidence.create'] };
    const calls = fakeApi(() => jsonResponse(202, need(EVIDENCE.created, 'created package')), me);
    const { user, wizard } = await openFromLookup();
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).queryByLabelText(AUDIT_LOG_LABEL)).not.toBeInTheDocument();
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(summaryValue(wizard, 'Denetim kaydı')).toMatch(/^Dahil değil/);
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toBeDefined();
    });
    expect(posted(calls)).not.toHaveProperty('include_audit_log');
  });

  it('marks required fields for screen readers and moves focus to the first problem', async () => {
    fakeApi(() => errorResponse(500, 'unexpected'));
    renderApp('/evidence');
    const { user, wizard } = await openWizard();
    const first = within(wizard).getByLabelText('İlk gün');
    const last = within(wizard).getByLabelText('Son gün');
    expect(first).toHaveAttribute('aria-required', 'true');
    expect(last).toHaveAttribute('aria-required', 'true');
    expect(within(wizard).getByLabelText('Talep referansı (isteğe bağlı)')).not.toHaveAttribute(
      'aria-required',
    );

    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await waitFor(() => {
      expect(first).toHaveFocus();
    });
    expect(first).toHaveAccessibleDescription('Bu alan zorunludur.');
    await user.type(first, '04.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await waitFor(() => {
      expect(last).toHaveFocus();
    });

    // A lookup scope without a lookup: the (required) picker gets the focus.
    await user.click(within(wizard).getByRole('radio', { name: /5651 sorgusundan/ }));
    const picker = within(wizard).getByRole('combobox', { name: '5651 sorgusu' });
    expect(picker).toHaveAttribute('aria-required', 'true');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await waitFor(() => {
      expect(picker).toHaveFocus();
    });
    expect(picker).toHaveAccessibleDescription('Bir 5651 sorgusu seçin.');
  });

  it('an extract with bad lines puts the focus on the lines', async () => {
    fakeApi(() => errorResponse(500, 'unexpected'));
    renderApp('/evidence');
    const { user, wizard } = await openWizard();
    await user.type(within(wizard).getByLabelText('İlk gün'), '04.10.2026');
    await user.type(within(wizard).getByLabelText('Son gün'), '05.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('radio', { name: /^Alıntı/ }));
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    const lines = within(wizard).getByLabelText('Alıntılanacak satırlar');
    await waitFor(() => {
      expect(lines).toHaveFocus();
    });
    expect(lines).toHaveAccessibleDescription('Alıntı için en az bir satır girin.');
  });

  it('shows the chosen lookup from its very first frame', async () => {
    fakeApi(() => errorResponse(500, 'unexpected'));
    const seen = await firstFrame(
      '/evidence?lookup=2',
      (wizard) =>
        within(wizard).queryByRole<HTMLInputElement>('radio', { name: /5651 sorgusundan/ })
          ?.checked,
    );
    expect(seen).toEqual([true]);
  });

  it('shows the handed-over days from its very first frame', async () => {
    fakeApi(() => errorResponse(500, 'unexpected'));
    handoff.put('/evidence', {
      extract: [{ day: '2026-10-09', source: 'fortigate', line: 27 }],
      from_day: '2026-10-09',
      to_day: '2026-10-09',
      truncated: false,
      complete: true,
      unsealed_days: [],
      query: 'srcip:172.20.42.57',
    });
    const seen = await firstFrame(
      '/evidence',
      (wizard) => within(wizard).queryByLabelText<HTMLInputElement>('İlk gün')?.value,
    );
    expect(seen).toEqual(['09.10.2026']);
  });

  it('opens by itself with the lines a search handed over, and warns before the API refuses', async () => {
    const created = need(EVIDENCE.created, 'created package');
    const calls = fakeApi(() => jsonResponse(202, created));
    handoff.put('/evidence', {
      extract: [
        { day: '2026-10-09', source: 'fortigate', line: 27 },
        { day: '2026-10-12', source: 'core-r1', line: 5 },
      ],
      from_day: '2026-10-09',
      to_day: '2026-10-12',
      truncated: true,
      complete: false,
      unsealed_days: ['2026-10-12'],
      query: 'srcip:172.20.42.57',
    });
    renderApp('/evidence');
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    const wizard = await screen.findByRole('dialog', { name: 'Yeni kanıt paketi' });
    expect(within(wizard).getByText('Arama sonucundan 2 satır aktarıldı')).toBeInTheDocument();
    expect(within(wizard).getByText('Sorgu: srcip:172.20.42.57')).toBeInTheDocument();
    expect(
      within(wizard).getByText(
        'Arama 10.000 satırdan fazlasını buldu; yalnızca ilk 10.000 satır aktarıldı.',
      ),
    ).toBeInTheDocument();
    expect(
      within(wizard).getByText(
        'Arama aralığın tamamını süresi içinde tarayamadı; bazı satırlar eksik olabilir.',
      ),
    ).toBeInTheDocument();
    expect(
      within(wizard).getByText(/^Şu günler henüz mühürlenmedi: 12\.10\.2026\./),
    ).toBeInTheDocument();
    expect(within(wizard).getByLabelText('İlk gün')).toHaveValue('09.10.2026');
    expect(within(wizard).getByLabelText('Son gün')).toHaveValue('12.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getByLabelText('Alıntılanacak satırlar')).toHaveValue(
      '2026-10-09 fortigate 27\n2026-10-12 core-r1 5',
    );
    expect(
      within(wizard).getByText('Arama sayfasında bir arama yapıp “Kanıt paketine aktar”a basın'),
    ).toBeInTheDocument();
    // One button leaves out the unsealed day's lines: the scope then ends on the sealed day.
    await user.click(
      within(wizard).getByRole('button', { name: 'Mühürlenmemiş günlerin satırlarını çıkar' }),
    );
    expect(
      within(wizard).getByText(
        'Mühürlenmemiş günlerin 1 satırı çıkarıldı; kapsam mühürlü günlerle sınırlandı.',
      ),
    ).toBeInTheDocument();
    expect(within(wizard).queryByText(/^Şu günler henüz mühürlenmedi/)).not.toBeInTheDocument();
    expect(within(wizard).getByText('Arama sonucundan 1 satır aktarıldı')).toBeInTheDocument();
    expect(within(wizard).getByLabelText('İlk gün')).toHaveValue('09.10.2026');
    expect(within(wizard).getByLabelText('Son gün')).toHaveValue('09.10.2026');
    await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    expect(within(wizard).getByLabelText('Alıntılanacak satırlar')).toHaveValue(
      '2026-10-09 fortigate 27',
    );
    // The entry was taken once: cancelling closes the wizard for good.
    await user.click(within(wizard).getByRole('button', { name: 'İptal' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Yeni kanıt paketi' })).not.toBeInTheDocument();
    });
    expect(handoff.take('/evidence')).toBeNull();
    expect(posted(calls)).toBeUndefined();
  });

  it('sends the handed-over lines as the extract', async () => {
    const created = need(EVIDENCE.created, 'created package');
    const calls = fakeApi(() => jsonResponse(202, created));
    handoff.put('/evidence', {
      extract: [{ day: '2026-10-09', source: 'fortigate', line: 27 }],
      from_day: '2026-10-09',
      to_day: '2026-10-09',
      truncated: false,
      complete: true,
      unsealed_days: [],
      query: 'type:traffic',
    });
    renderApp('/evidence');
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime.bind(vi) });
    const wizard = await screen.findByRole('dialog', { name: 'Yeni kanıt paketi' });
    for (let step = 0; step < 3; step += 1)
      await user.click(within(wizard).getByRole('button', { name: 'İleri' }));
    await user.click(within(wizard).getByRole('button', { name: 'Paketi oluştur' }));
    await waitFor(() => {
      expect(posted(calls)).toMatchObject({
        from: '2026-10-09',
        to: '2026-10-09',
        content: 'extract',
        extract: [{ day: '2026-10-09', source: 'fortigate', line: 27 }],
      });
    });
  });
});
