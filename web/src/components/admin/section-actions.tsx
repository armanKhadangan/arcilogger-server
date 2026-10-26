import { RefreshCw, RotateCcw, Save } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { editable, inFlight, type SectionState } from '@/lib/admin-requests';
import { errorMessage } from '@/lib/api';
import type { AdminRequestOut } from '@/lib/settings-types';

import { RequestProgress } from './request-progress';
import type { SectionFlow } from './use-section-flow';

/**
 * The bottom of a section (contract 2.17): "Kaydet" and "Vazgeç" while it may be changed, why
 * not otherwise (no permission, the service is not running), a refusal of the API, the conflict
 * with "Sayfayı yenile", and the request being followed.
 */
export function SectionActions<B>({
  state,
  flow,
  dirty,
  onSave,
  onRestore,
  onReload,
  saveLabel,
  probingText,
  renderResult,
  onTerminal,
  extra,
  failureDetails,
}: {
  state: SectionState;
  flow: SectionFlow<B>;
  dirty: boolean;
  onSave: () => void;
  onRestore: () => void;
  /** Reload the configuration (after a conflict). */
  onReload: () => void;
  saveLabel?: string;
  probingText?: string;
  renderResult?: (request: AdminRequestOut) => ReactNode;
  onTerminal?: (request: AdminRequestOut) => void;
  /** More buttons next to "Kaydet" (e.g. "Bağlantıyı test et"). */
  extra?: ReactNode;
  /** More about a refusal (e.g. the causes of `tsa_would_break`), inside its banner. */
  failureDetails?: ReactNode;
}) {
  const { t } = useTranslation();
  const busy = state === 'submitting' || state === 'reauth' || state === 'confirm';
  const canSave = dirty && editable(state) && state !== 'read_only';
  const reload = () => {
    flow.reset();
    onReload();
  };
  return (
    <div className="flex flex-col gap-3" data-testid="section-actions" data-section-state={state}>
      {state === 'read_only' ? (
        <p className="text-13 text-muted">{t('admin.section.readOnly')}</p>
      ) : null}
      {flow.conflict ? (
        <Alert
          tone="warning"
          title={t('admin.section.conflictTitle')}
          data-testid="section-conflict"
        >
          <span className="flex flex-wrap items-center gap-2">
            {t('admin.section.conflict')}
            <Button size="sm" variant="secondary" onClick={reload}>
              <RefreshCw aria-hidden="true" />
              {t('admin.progress.reload')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {flow.failure ? (
        <Alert tone="danger">
          {errorMessage(flow.failure)}
          {failureDetails}
        </Alert>
      ) : null}
      {flow.problems.length > 0 ? <Alert tone="danger">{t('admin.section.invalid')}</Alert> : null}
      {state !== 'read_only' ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={onSave}
            disabled={!canSave && !busy}
            busy={busy}
            aria-describedby={state === 'agent_unavailable' ? 'agent-unavailable-hint' : undefined}
          >
            <Save aria-hidden="true" />
            {busy ? t('common.working') : (saveLabel ?? t('common.save'))}
          </Button>
          {dirty && !inFlight(state) ? (
            <Button variant="ghost" onClick={onRestore}>
              <RotateCcw aria-hidden="true" />
              {t('admin.section.restore')}
            </Button>
          ) : null}
          {extra}
          {state === 'agent_unavailable' ? (
            <span id="agent-unavailable-hint" className="text-xs text-muted">
              {t('admin.section.agentUnavailable')}
            </span>
          ) : null}
        </div>
      ) : null}
      {flow.requestId ? (
        <RequestProgress
          requestId={flow.requestId}
          probingText={probingText}
          onRetry={onSave}
          onReload={reload}
          renderResult={renderResult}
          onTerminal={onTerminal}
        />
      ) : null}
    </div>
  );
}
