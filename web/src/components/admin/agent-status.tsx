import { CircleCheck, CircleMinus, CircleX, Clock, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { localized } from '@/lib/admin-requests';
import { formatDateTime } from '@/lib/format';
import type { AdminStatus, AgentState, SetupProblem } from '@/lib/settings-types';

const CHIP: Record<AgentState, { tone: NonNullable<BadgeProps['tone']>; icon: LucideIcon }> = {
  ok: { tone: 'success', icon: CircleCheck },
  stale: { tone: 'warning', icon: Clock },
  missing: { tone: 'danger', icon: CircleX },
  disabled: { tone: 'neutral', icon: CircleMinus },
};

/**
 * "Yönetim hizmeti: çalışıyor / gecikiyor / çalışmıyor / kapalı" (contract 2.17): the heartbeat
 * of the administration service (ArciAdmin) from `GET /admin/status`, refreshed every 10 s. The
 * last heartbeat is in the chip's title.
 */
export function AgentStatusChip({ status }: { status: AdminStatus | undefined }) {
  const { t } = useTranslation();
  if (!status) return null;
  const agent = status.agent;
  const style = CHIP[agent.state];
  return (
    <Badge
      tone={style.tone}
      icon={style.icon}
      data-testid="agent-status"
      title={
        agent.heartbeat_at
          ? t('admin.agent.heartbeat', { time: formatDateTime(agent.heartbeat_at) })
          : undefined
      }
    >
      {t('admin.agent.chip', { state: t(`admin.agent.states.${agent.state}`) })}
    </Badge>
  );
}

/**
 * Why nothing can be saved from the browser now, and where to do it instead (the server
 * console). A late service only delays a change: a polite warning.
 */
export function AgentBanner({ status }: { status: AdminStatus | undefined }) {
  const { t } = useTranslation();
  const agent = status?.agent.state;
  if (agent === 'missing' || agent === 'disabled')
    return (
      <Alert
        tone={agent === 'missing' ? 'danger' : 'warning'}
        assertive={false}
        title={t(`admin.unavailable.${agent}.title`)}
        data-testid="agent-unavailable"
      >
        <p>{t(`admin.unavailable.${agent}.body`)}</p>
        <p className="mt-1">{t('admin.unavailable.console')}</p>
      </Alert>
    );
  if (agent === 'stale')
    return (
      <Alert tone="warning" assertive={false} data-testid="agent-stale">
        {t('admin.unavailable.stale')}
      </Alert>
    );
  return null;
}

/**
 * A hand edit of `arcilogger.toml` that the services take only at a restart (contract 2.4.12
 * `restart_keys`), or a configuration written by someone other than the administration service.
 */
export function RestartBanner({ status }: { status: AdminStatus | undefined }) {
  const { t } = useTranslation();
  if (!status?.restart_required) return null;
  const keys = Object.entries(status.services).flatMap(([service, block]) =>
    block.restart_keys.map((key) => `${service}: ${key}`),
  );
  return (
    <Alert
      tone="warning"
      assertive={false}
      title={t('admin.restart.title')}
      data-testid="restart-required"
    >
      <p>{t('admin.restart.body')}</p>
      {keys.length > 0 ? <p className="mt-1 font-mono text-xs">{keys.join(', ')}</p> : null}
    </Alert>
  );
}

/**
 * The problems of the configuration in force (`AdminConfig.problems`, `setup_problems`; an
 * `arcilogger.toml` that does not load is an error and nothing can be saved until it is fixed).
 */
export function ConfigProblems({
  problems,
  levels = ['error', 'warning'],
}: {
  problems: readonly SetupProblem[] | undefined;
  levels?: readonly SetupProblem['level'][];
}) {
  const { t } = useTranslation();
  const shown = (problems ?? []).filter((item) => levels.includes(item.level));
  if (shown.length === 0) return null;
  const error = shown.some((item) => item.level === 'error');
  return (
    <Alert
      tone={error ? 'danger' : 'warning'}
      assertive={false}
      title={t('admin.problems.title')}
      data-testid="config-problems"
    >
      <ul className="flex flex-col gap-1">
        {shown.map((item) => (
          <li key={`${item.key}:${item.message_en}`}>
            <span className="font-mono text-xs">{item.key}</span> — {localized(item)}
          </li>
        ))}
      </ul>
    </Alert>
  );
}
