import { ChevronRight, Lock, RefreshCw, UserRound } from 'lucide-react';
import { useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { SettingsCard, SettingsFrame } from '@/components/admin/settings-frame';
import { PageSkeleton } from '@/components/layout/route-frames';
import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps, SeverityBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useRules } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import type { RuleDataStatus, RuleOut } from '@/lib/rules-types';

import { VerifyBadge } from './rule-badges';
import { RuleSheet } from './rule-sheet';
import { dataStatusKey, ruleName, ruleState, sortRules, type RuleState } from './rules-model';

/** Who changes a rule (§2.3); the list needs `alerts.read` (the route's gate). */
export const RULES_MANAGE_PERMISSION = 'settings.manage';

const STATE_TONE: Record<RuleState, NonNullable<BadgeProps['tone']>> = {
  on: 'success',
  off: 'neutral',
  setup: 'warning',
};

const DATA_TONE: Record<RuleDataStatus, NonNullable<BadgeProps['tone']>> = {
  ok: 'success',
  no_data: 'warning',
  never: 'neutral',
};

function RuleRow({ rule, onOpen }: { rule: RuleOut; onOpen: (id: string) => void }) {
  const { t } = useTranslation();
  const lang = currentLang();
  const state = ruleState(rule);
  return (
    <tr
      className="cursor-pointer border-t border-border align-middle hover:bg-subtle"
      data-testid="rule-row"
      data-rule={rule.id}
      onClick={() => {
        onOpen(rule.id);
      }}
    >
      <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-medium">
        <button
          type="button"
          className="rounded-sm text-accent-ink hover:underline"
          aria-label={t('rules.open', { id: rule.id })}
          onClick={(event) => {
            event.stopPropagation();
            onOpen(rule.id);
          }}
        >
          {rule.id}
        </button>
      </th>
      <td className="px-3 py-2">
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-foreground">{ruleName(rule, lang)}</span>
          {!rule.format_verified ? <VerifyBadge /> : null}
          {rule.person_level ? (
            <Badge tone="info" icon={UserRound} title={t('rules.personLevelHint')}>
              {t('rules.personLevel')}
            </Badge>
          ) : null}
        </span>
      </td>
      <td className="px-3 py-2">
        <SeverityBadge severity={rule.settings.severity} />
      </td>
      <td className="px-3 py-2">
        <Badge tone={STATE_TONE[state]} data-testid="rule-state">
          {t(`rules.state.${state}`)}
        </Badge>
      </td>
      <td className="px-3 py-2">
        <span className="flex flex-col gap-0.5">
          <Badge tone={DATA_TONE[rule.data_status]} className="self-start" data-testid="rule-data">
            {t(dataStatusKey(rule))}
          </Badge>
          {rule.last_input_at ? (
            <span className="text-xs text-muted tabular">
              {t('rules.lastInput', { time: formatDateTime(rule.last_input_at) })}
            </span>
          ) : null}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-danger-ink">
        {rule.last_error ? t('rules.lastError', { error: rule.last_error }) : null}
      </td>
      <td className="px-2 py-2">
        <ChevronRight aria-hidden="true" className="size-4 text-muted" />
      </td>
    </tr>
  );
}

/**
 * Ayarlar → Güvenlik kuralları (Wave 6 contract §2.3; `alerts.read` to view, `settings.manage` to
 * change): every rule with its severity, whether it is on, whether its data arrives, and the marks
 * "Sahada doğrula" (an unconfirmed log format) and "Ayar gerekli" (idle until configured). A row
 * opens the rule's sheet (`?rule=R01`, so a rule can be linked): its settings, the trial run over
 * the last 24 hours and Save. Without the `security_rules` module the list stays readable and
 * every change is locked (D1); the 5651 Temel alerts do not depend on it.
 */
export function RulesPage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const rules = useRules();
  const [params, setParams] = useSearchParams();
  const opener = useRef<HTMLElement | null>(null);
  const openId = params.get('rule');
  const list = useMemo(() => sortRules(rules.data?.rules ?? []), [rules.data]);
  const locked = rules.data?.module_enabled === false;
  const manage = can(RULES_MANAGE_PERMISSION);
  const open = list.find((rule) => rule.id === openId) ?? null;

  function setOpen(id: string | null) {
    if (id !== null && document.activeElement instanceof HTMLElement)
      opener.current = document.activeElement;
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (id === null) next.delete('rule');
        else next.set('rule', id);
        return next;
      },
      { replace: true },
    );
  }

  if (rules.isPending) return <PageSkeleton />;

  return (
    <SettingsFrame title={t('rules.title')} description={t('rules.description')} machine={false}>
      {rules.isError ? (
        <Alert tone="danger">
          <span className="flex flex-wrap items-center gap-2">
            {t('rules.loadFailed')} {errorMessage(rules.error)}
            <Button size="sm" variant="secondary" onClick={() => void rules.refetch()}>
              <RefreshCw aria-hidden="true" />
              {t('common.retry')}
            </Button>
          </span>
        </Alert>
      ) : null}
      {locked ? (
        <Alert tone="warning" assertive={false} data-testid="rules-locked">
          <span className="flex items-start gap-2">
            <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {t('rules.locked')}
          </span>
        </Alert>
      ) : null}
      {!manage && rules.data ? (
        <Alert tone="info" assertive={false} data-testid="rules-read-only">
          {t('rules.readOnly')}
        </Alert>
      ) : null}
      {rules.data ? (
        <SettingsCard title={t('rules.caption')} testId="rules-table">
          {list.length === 0 ? (
            <p className="text-13 text-muted">{t('rules.empty')}</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-13">
                <caption className="sr-only">{t('rules.caption')}</caption>
                <thead className="bg-subtle text-left text-xs text-muted-strong">
                  <tr>
                    {(['code', 'name', 'severity', 'state', 'data', 'notes'] as const).map(
                      (column) => (
                        <th
                          key={column}
                          scope="col"
                          className="px-3 py-2 font-medium whitespace-nowrap"
                        >
                          {t(`rules.columns.${column}`)}
                        </th>
                      ),
                    )}
                    <td className="px-2 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {list.map((rule) => (
                    <RuleRow key={rule.id} rule={rule} onOpen={setOpen} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SettingsCard>
      ) : null}
      <RuleSheet
        rule={open}
        editable={manage && !locked}
        locked={locked}
        returnFocus={opener}
        onClose={() => {
          setOpen(null);
        }}
      />
    </SettingsFrame>
  );
}
