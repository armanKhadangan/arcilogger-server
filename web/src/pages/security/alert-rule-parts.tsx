import { FileText, UserRound } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { evidenceTableLabel, EVIDENCE_TABLES } from '@/components/evidence/evidence-labels';
import type { RawLineTarget } from '@/components/evidence/raw-line-sheet';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toast';
import type { AlertDetailWithRules, AlertEvidenceRow } from '@/lib/alert-rule-types';
import { errorMessage } from '@/lib/api';
import { useAlertEvidence, useAssignAlert, useUsers } from '@/lib/api-hooks';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { assignCandidates, assigneeOf } from './alerts-model';

const NOBODY = 'nobody';

/** "FortiGate IPS kaydı" for the tables the rules read beyond the lookup's evidence tables. */
function tableLabel(table: string, t: ReturnType<typeof useTranslation>['t']): string {
  if (EVIDENCE_TABLES.includes(table)) return evidenceTableLabel(table, t);
  return t(`security.evidence.tables.${table}`, { defaultValue: table });
}

/**
 * Who works on the alert (Wave 6 §2.6.2): a user whose role reads alerts, or nobody. With
 * `alerts.manage` it is chosen here (the list of users needs `users.manage`; without it only
 * "me" and "nobody" are offered); the server writes an `assign` event and the audit `alert.assign`.
 */
export function AssigneeControl({
  alert,
  manage,
}: {
  alert: AlertDetailWithRules;
  manage: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { me, can } = useAuth();
  const { withReauth } = useReauth();
  const assign = useAssignAlert();
  const listUsers = manage && can('users.manage');
  const users = useUsers({ active: true }, listUsers);
  const [failure, setFailure] = useState<string | null>(null);
  const current = assigneeOf(alert);

  useEffect(() => {
    setFailure(null);
  }, [alert.id]);

  if (!manage)
    return (
      <p className="flex items-center gap-1.5 text-13 text-foreground" data-testid="alert-assignee">
        <UserRound aria-hidden="true" className="size-4 text-muted-strong" />
        {current ? current.username : t('security.assign.nobody')}
      </p>
    );

  const options = new Map<number, string>();
  if (listUsers)
    for (const user of assignCandidates(users.data?.pages.flatMap((page) => page.items) ?? []))
      options.set(user.id, user.username);
  options.set(me.user.id, me.user.username);
  if (current) options.set(current.id, current.username);

  async function choose(value: string) {
    const userId = value === NOBODY ? null : Number(value);
    if (userId === (current?.id ?? null)) return;
    setFailure(null);
    try {
      await withReauth(() => assign.mutateAsync({ id: alert.id, user_id: userId }));
      toast({
        title:
          userId === null
            ? t('security.assign.cleared')
            : t('security.assign.done', { user: options.get(userId) ?? String(userId) }),
        variant: 'success',
      });
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-2" data-testid="alert-assignee">
      {failure ? (
        <Alert tone="danger" data-testid="assign-error">
          {failure}
        </Alert>
      ) : null}
      <Field id={`${id}-assignee`} label={t('security.assign.label')}>
        <Select
          value={current ? String(current.id) : NOBODY}
          disabled={assign.isPending}
          onValueChange={(value) => void choose(value)}
        >
          <SelectTrigger id={`${id}-assignee`} className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NOBODY}>{t('security.assign.nobody')}</SelectItem>
            {[...options.entries()].map(([userId, username]) => (
              <SelectItem key={userId} value={String(userId)}>
                {userId === me.user.id ? t('security.assign.me', { user: username }) : username}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    </div>
  );
}

/**
 * The person of a person-level rule's alert (R09, R13, R14), only when the API sends it: for
 * `usage.person`, after writing `alert.person_view` (D3). Nobody else sees a name here.
 */
export function AlertPersonRow({ alert }: { alert: AlertDetailWithRules }) {
  const { t } = useTranslation();
  if (!alert.person) return null;
  return (
    <div
      className="flex flex-col gap-1 rounded-lg border border-border bg-subtle/50 px-3 py-2"
      data-testid="alert-person"
    >
      <span className="flex items-center gap-1.5 text-13 font-medium text-foreground">
        <UserRound aria-hidden="true" className="size-4 text-muted-strong" />
        {t('security.person.label', { name: alert.person.name })}
      </span>
      <span className="text-xs text-muted">{t('security.person.audited')}</span>
    </div>
  );
}

function rawTarget(row: AlertEvidenceRow): RawLineTarget | null {
  if (!row.raw) return null;
  // The line alone: the alert's evidence has no summary text of the row (only the lookup's has).
  return { ref: { ...row.raw } };
}

/**
 * The rows that caused a rule's alert (`GET /alerts/{id}/evidence`), oldest first, each with
 * "Ham satırı aç": the raw line opens through `GET /raw` (its permission, `search.read` or
 * `lookup.run`, and its audit `raw.view`); the alert itself never holds the line (§0.1 item 4).
 */
export function AlertEvidenceList({
  alertId,
  total,
  onOpenRaw,
}: {
  alertId: number;
  total: number;
  onOpenRaw: (target: RawLineTarget) => void;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  const { canAny } = useAuth();
  const evidence = useAlertEvidence(alertId);
  const canRaw = canAny(['search.read', 'lookup.run']);
  const head = 'px-3 py-2 text-left text-xs font-medium text-muted-strong';

  if (evidence.isPending)
    return (
      <div role="status" aria-label={t('common.loading')}>
        <Skeleton className="h-32" />
      </div>
    );
  if (evidence.isError) return <Alert tone="danger">{errorMessage(evidence.error)}</Alert>;
  const items = evidence.data.items;
  if (items.length === 0)
    return <p className="text-13 text-muted">{t('security.evidence.empty')}</p>;
  const all = Math.max(total, evidence.data.total);
  return (
    <div className="flex flex-col gap-2" data-testid="alert-evidence">
      <p className="text-xs text-muted">
        {all > items.length
          ? t('security.evidence.kept', {
              shown: formatNumber(items.length, 0, lang),
              total: formatNumber(all, 0, lang),
            })
          : t('security.evidence.all', { count: items.length })}
      </p>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-13">
          <caption className="sr-only">{t('security.evidence.caption')}</caption>
          <thead className="bg-subtle">
            <tr>
              <th scope="col" className={head}>
                {t('security.evidence.columns.time')}
              </th>
              <th scope="col" className={head}>
                {t('security.evidence.columns.table')}
              </th>
              <th scope="col" className={head}>
                {t('security.evidence.columns.source')}
              </th>
              <th scope="col" className={head}>
                {t('security.evidence.columns.received')}
              </th>
              <th scope="col" className={head}>
                <span className="sr-only">{t('security.evidence.columns.raw')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => {
              const target = rawTarget(row);
              return (
                <tr key={row.n} className="border-t border-border" data-testid="evidence-row">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal tabular">
                    {formatDateTime(row.time)}
                  </th>
                  <td className="px-3 py-1.5">{tableLabel(row.tbl, t)}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">{row.source}</td>
                  <td className="px-3 py-1.5 tabular">{formatDateTime(row.time_received)}</td>
                  <td className="px-3 py-1.5 text-right">
                    {target && canRaw ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t('security.evidence.openRawOf', {
                          time: formatDateTime(row.time),
                        })}
                        onClick={() => {
                          onOpenRaw(target);
                        }}
                      >
                        <FileText aria-hidden="true" />
                        {t('security.evidence.openRaw')}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!canRaw ? <p className="text-xs text-muted">{t('security.evidence.noRaw')}</p> : null}
    </div>
  );
}
