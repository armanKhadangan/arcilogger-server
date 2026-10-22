import { ApiError } from '@/lib/api';
import i18n from '@/lib/i18n';
import { REPORTS } from '@/test/samples';

import { engineMissing, reportProblem, retryPending, shownStatus } from './report-errors';

const t = i18n.getFixedT('tr');

function apiError(status: number, code: string): ApiError {
  return new ApiError({ status, code, messageTr: `sunucu: ${code}`, messageEn: code });
}

describe('report refusals in words', () => {
  it('names the missing usage engine, e-mail switched off and missing recipients', () => {
    expect(reportProblem(apiError(503, 'usage_engine_unavailable'), t)).toEqual({
      kind: 'engine',
      title: 'Kullanım motoru henüz hazır değil',
      text: expect.stringContaining('kullanım motoru kurulunca') as string,
    });
    const smtp = reportProblem(apiError(409, 'report_smtp_disabled'), t);
    expect(smtp.kind).toBe('smtp');
    expect(smtp.title).toBe('E-posta gönderimi kapalı');
    expect(smtp.text).toContain('UYARILAR.md bölüm 4');
    expect(smtp.text).not.toContain('send=false');
    expect(reportProblem(apiError(422, 'report_recipients_required'), t).kind).toBe('recipients');
  });

  it('shows any other refusal as the API words it', () => {
    expect(reportProblem(apiError(410, 'report_expired'), t)).toEqual({
      kind: 'other',
      text: 'sunucu: report_expired',
    });
  });
});

describe('run states', () => {
  it('a run that e-mailed nobody is done, not sent', () => {
    const files = REPORTS.runs.items.find((run) => run.kind === 'custom');
    if (!files) throw new Error('sample missing');
    expect(files.deliveries.every((item) => item.status === 'skipped')).toBe(true);
    expect(shownStatus(files)).toBe('done');
    expect(shownStatus(REPORTS.run_partial)).toBe('partial');
    expect(shownStatus({ status: 'sent', deliveries: [] })).toBe('done');
  });

  it('a failed delivery of a running run waits for its retry', () => {
    const [delivery] = REPORTS.run_partial.deliveries;
    if (!delivery) throw new Error('sample missing');
    const failed = { ...delivery, status: 'failed' as const };
    expect(retryPending({ status: 'running' }, failed)).toBe(true);
    expect(retryPending({ status: 'partial' }, failed)).toBe(false);
  });

  it('knows the runner error of a missing usage engine', () => {
    expect(
      engineMissing({
        status: 'failed',
        error: 'kullanım motoru yok / usage engine not installed: x',
      }),
    ).toBe(true);
    expect(engineMissing({ status: 'failed', error: 'SMTP down' })).toBe(false);
    expect(engineMissing({ status: 'sent', error: null })).toBe(false);
  });
});
