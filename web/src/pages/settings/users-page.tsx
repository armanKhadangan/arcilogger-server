import {
  ArrowLeft,
  CircleCheck,
  CircleSlash,
  Lock,
  MoreHorizontal,
  ShieldCheck,
  ShieldOff,
  TriangleAlert,
  UserPlus,
} from 'lucide-react';
import { useCallback, useId, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import {
  useLicense,
  useResetPassword,
  useResetTotp,
  useUnlockUser,
  useUpdateUser,
  useUsers,
  type UserFilters,
} from '@/lib/api-hooks';
import type { UserOut } from '@/lib/api-types';
import { useAuth } from '@/lib/auth';
import { formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { isRole, roleLabel, ROLE_IDS } from '@/lib/permissions';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { RoleMatrix } from './role-matrix';
import {
  CreateUserDialog,
  EditUserDialog,
  RoleDialog,
  TemporaryPasswordDialog,
} from './user-dialogs';

type ConfirmKind = 'deactivate' | 'activate' | 'resetPassword' | 'resetTotp' | 'unlock';

interface PendingConfirm {
  kind: ConfirmKind;
  user: UserOut;
}

const ALL = 'all';

function UserStatus({ user }: { user: UserOut }) {
  const { t } = useTranslation();
  return (
    <span className="flex flex-wrap items-center gap-1">
      {user.active ? (
        <Badge tone="success" icon={CircleCheck}>
          {t('users.active')}
        </Badge>
      ) : (
        <Badge tone="neutral" icon={CircleSlash}>
          {t('users.inactive')}
        </Badge>
      )}
      {user.locked ? (
        <Badge tone="danger" icon={Lock}>
          {t('users.locked')}
        </Badge>
      ) : null}
      {user.must_change_password ? (
        <Badge tone="warning" icon={TriangleAlert}>
          {t('users.mustChange')}
        </Badge>
      ) : null}
    </span>
  );
}

type UserAction = 'edit' | 'role' | ConfirmKind;

/**
 * The row's "İşlemler" menu. Each action hands over the menu's trigger button: the dialog it opens
 * gives the focus back to that button when it closes (the menu item itself is gone by then).
 */
function UserActions({
  row,
  self,
  triggerId,
  onAction,
}: {
  row: UserOut;
  self: boolean;
  /** Stable id: finds the button again if the table re-rendered it while a dialog was open. */
  triggerId: string;
  onAction: (action: UserAction, user: UserOut, trigger: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();
  const trigger = useRef<HTMLButtonElement>(null);
  const item = (action: UserAction, text: string) => (
    <DropdownMenuItem
      onSelect={() => {
        onAction(action, row, trigger.current);
      }}
    >
      {text}
    </DropdownMenuItem>
  );
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          ref={trigger}
          id={triggerId}
          variant="ghost"
          size="icon-sm"
          aria-label={t('users.actionsFor', { name: row.display_name })}
        >
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {item('edit', t('users.actions.edit'))}
        {self ? null : (
          <>
            {item('role', t('users.actions.role'))}
            {item('resetPassword', t('users.actions.resetPassword'))}
            {row.totp_enabled ? item('resetTotp', t('users.actions.resetTotp')) : null}
          </>
        )}
        {row.locked || row.failed_attempts > 0 ? item('unlock', t('users.actions.unlock')) : null}
        {self ? null : (
          <>
            <DropdownMenuSeparator />
            {row.active
              ? item('deactivate', t('users.actions.deactivate'))
              : item('activate', t('users.actions.activate'))}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Ayarlar → Kullanıcılar ve Roller (spec 12.3 page 15, 13; `users.manage`): the accounts, adding
 * one with a temporary password, role changes, deactivation, password and 2FA resets, unlocking —
 * each confirmed, audited by the server, and (except unlocking) only after a recent
 * re-authentication — and the read-only role matrix.
 */
export function UsersPage() {
  const { t } = useTranslation();
  const lang = currentLang();
  const { me } = useAuth();
  const { withReauth } = useReauth();
  const [roleFilter, setRoleFilter] = useState<string>(ALL);
  const [statusFilter, setStatusFilter] = useState<string>(ALL);
  const filters: UserFilters = {
    role: isRole(roleFilter) ? roleFilter : undefined,
    active: statusFilter === ALL ? undefined : statusFilter === 'active',
  };
  const users = useUsers(filters);
  // The licence's users limit (contract 6.6): the counter, and no "add" at the limit. The API
  // recounts and decides; its `license_limit` refusal is shown like any other error.
  const license = useLicense();
  const usersLimit = license.data?.limits.users ?? null;
  const usersFull = usersLimit?.full ?? false;
  const update = useUpdateUser();
  const resetPassword = useResetPassword();
  const resetTotp = useResetTotp();
  const unlock = useUnlockUser();

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<UserOut | null>(null);
  const [roleUser, setRoleUser] = useState<UserOut | null>(null);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [temporary, setTemporary] = useState<{ user: UserOut; password: string } | null>(null);

  const tableId = useId();
  /** The button that started the current action; its dialogs give the focus back to it. */
  const returnTo = useRef<HTMLElement | null>(null);

  const rows = useMemo(() => users.data?.pages.flatMap((page) => page.items) ?? [], [users.data]);

  const act = useCallback((action: UserAction, user: UserOut, trigger: HTMLElement | null) => {
    returnTo.current = trigger;
    if (action === 'edit') {
      setEditing(user);
    } else if (action === 'role') {
      setRoleUser(user);
    } else {
      setConfirmError(null);
      setConfirm({ kind: action, user });
    }
  }, []);

  async function runConfirmed() {
    if (!confirm) return;
    const { kind, user } = confirm;
    setBusy(true);
    setConfirmError(null);
    try {
      if (kind === 'deactivate' || kind === 'activate') {
        await withReauth(() => update.mutateAsync({ id: user.id, active: kind === 'activate' }));
      } else if (kind === 'resetPassword') {
        const result = await withReauth(() => resetPassword.mutateAsync(user.id));
        setTemporary({ user: result.user, password: result.temporary_password });
      } else if (kind === 'resetTotp') {
        await withReauth(() => resetTotp.mutateAsync(user.id));
      } else {
        await withReauth(() => unlock.mutateAsync(user.id));
      }
      if (kind !== 'resetPassword') {
        toast({ title: t(`users.done.${kind}`, { name: user.display_name }), variant: 'success' });
      }
      setConfirm(null);
    } catch (error) {
      if (!isReauthCancelled(error)) setConfirmError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const columns = useMemo<AnyColumnDef<UserOut>[]>(
    () => [
      {
        id: 'user',
        accessorFn: (row) => row.display_name,
        header: t('users.columns.user'),
        size: 260,
        meta: { label: t('users.columns.user') },
        cell: (info) => {
          const row = info.row.original;
          return (
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate font-medium">
                {row.display_name}
                {row.id === me.user.id ? (
                  <span className="ml-1 font-normal text-muted-strong">{t('users.you')}</span>
                ) : null}
              </span>
              <span className="truncate font-mono text-xs text-muted-strong">{row.username}</span>
            </span>
          );
        },
      },
      {
        id: 'role',
        accessorFn: (row) => roleLabel(row.role, lang),
        header: t('users.columns.role'),
        size: 150,
        meta: { label: t('users.columns.role') },
        cell: (info) => <Badge tone="primary">{info.getValue<string>()}</Badge>,
      },
      {
        id: 'status',
        accessorFn: (row) => (row.active ? 1 : 0),
        header: t('users.columns.status'),
        size: 280,
        meta: { label: t('users.columns.status') },
        cell: (info) => <UserStatus user={info.row.original} />,
      },
      {
        id: 'totp',
        accessorFn: (row) => row.totp_enabled,
        header: t('users.columns.totp'),
        size: 110,
        meta: { label: t('users.columns.totp') },
        cell: (info) =>
          info.getValue<boolean>() ? (
            <Badge tone="success" icon={ShieldCheck}>
              {t('users.totpOn')}
            </Badge>
          ) : (
            <Badge tone="neutral" icon={ShieldOff}>
              {t('users.totpOff')}
            </Badge>
          ),
      },
      {
        id: 'lastLogin',
        accessorFn: (row) => row.last_login ?? '',
        header: t('users.columns.lastLogin'),
        size: 170,
        meta: { label: t('users.columns.lastLogin') },
        cell: (info) => {
          const value = info.row.original.last_login;
          return (
            <span className="tabular">{value ? formatDateTime(value) : t('users.never')}</span>
          );
        },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">{t('users.columns.actions')}</span>,
        size: 64,
        enableSorting: false,
        enableHiding: false,
        meta: { label: t('users.columns.actions') },
        cell: (info) => {
          const row = info.row.original;
          return (
            <UserActions
              row={row}
              self={row.id === me.user.id}
              triggerId={`${tableId}-actions-${row.id}`}
              onAction={act}
            />
          );
        },
      },
    ],
    [t, lang, me.user.id, tableId, act],
  );

  const toolbar = (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <Label htmlFor="users-role-filter" className="text-xs text-muted-strong">
          {t('users.role')}
        </Label>
        <Select value={roleFilter} onValueChange={setRoleFilter}>
          <SelectTrigger id="users-role-filter" className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('users.allRoles')}</SelectItem>
            {ROLE_IDS.map((role) => (
              <SelectItem key={role} value={role}>
                {roleLabel(role, lang)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="users-status-filter" className="text-xs text-muted-strong">
          {t('users.columns.status')}
        </Label>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger id="users-status-filter" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t('users.allStatuses')}</SelectItem>
            <SelectItem value="active">{t('users.active')}</SelectItem>
            <SelectItem value="inactive">{t('users.inactive')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  );

  const confirmTexts = confirm
    ? {
        title: t(`users.confirm.${confirm.kind}.title`),
        description: t(`users.confirm.${confirm.kind}.body`, {
          name: confirm.user.display_name,
          username: confirm.user.username,
        }),
        action: t(`users.confirm.${confirm.kind}.action`),
      }
    : null;

  return (
    <div>
      <Button asChild variant="link" size="sm" className="mb-2">
        <Link to="/settings">
          <ArrowLeft aria-hidden="true" />
          {t('nav.settings')}
        </Link>
      </Button>
      <PageHeader
        title={t('users.title')}
        description={t('users.description')}
        actions={
          <>
            {usersLimit && usersLimit.max !== null ? (
              <span
                className="text-13 font-medium text-muted-strong tabular"
                data-testid="users-limit"
              >
                {t('license.page.usersCounter', {
                  used: formatNumber(usersLimit.used, 0, lang),
                  max: formatNumber(usersLimit.max, 0, lang),
                })}
              </span>
            ) : null}
            <Button
              disabled={usersFull}
              aria-describedby={usersFull ? 'users-limit-full' : undefined}
              onClick={(event) => {
                returnTo.current = event.currentTarget;
                setCreateOpen(true);
              }}
            >
              <UserPlus aria-hidden="true" />
              {t('users.add')}
            </Button>
          </>
        }
      />
      {usersFull && usersLimit !== null && usersLimit.max !== null ? (
        <Alert tone="info" id="users-limit-full" className="mb-4">
          {t('license.page.usersFull', {
            used: formatNumber(usersLimit.used, 0, lang),
            max: formatNumber(usersLimit.max, 0, lang),
          })}
        </Alert>
      ) : null}
      <Tabs defaultValue="users">
        <TabsList aria-label={t('users.title')}>
          <TabsTrigger value="users">{t('users.tabUsers')}</TabsTrigger>
          <TabsTrigger value="roles">{t('users.tabRoles')}</TabsTrigger>
        </TabsList>
        <TabsContent value="users" className="flex flex-col gap-3">
          {users.isError ? <Alert tone="danger">{errorMessage(users.error)}</Alert> : null}
          <DataTable
            data={rows}
            columns={columns}
            caption={t('users.caption')}
            getRowId={(row) => String(row.id)}
            toolbar={toolbar}
            height={Math.min(520, 40 + Math.max(rows.length, 4) * 44)}
            initialDensity="comfortable"
            emptyMessage={users.isPending ? t('common.loading') : t('users.empty')}
          />
          {users.hasNextPage ? (
            <Button
              variant="secondary"
              className="self-center"
              busy={users.isFetchingNextPage}
              onClick={() => void users.fetchNextPage()}
            >
              {users.isFetchingNextPage ? t('common.loading') : t('common.loadMore')}
            </Button>
          ) : null}
        </TabsContent>
        <TabsContent value="roles">
          <RoleMatrix />
        </TabsContent>
      </Tabs>

      <CreateUserDialog
        returnFocus={returnTo}
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(result) => {
          setCreateOpen(false);
          setTemporary({ user: result.user, password: result.temporary_password });
        }}
      />
      <EditUserDialog
        returnFocus={returnTo}
        user={editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      />
      <RoleDialog
        returnFocus={returnTo}
        user={roleUser}
        onOpenChange={(open) => {
          if (!open) setRoleUser(null);
        }}
      />
      <ConfirmDialog
        returnFocus={returnTo}
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={confirmTexts?.title}
        description={confirmTexts?.description}
        confirmLabel={confirmTexts?.action}
        danger={confirm?.kind === 'deactivate' || confirm?.kind === 'resetTotp'}
        busy={busy}
        error={confirmError}
        onConfirm={() => void runConfirmed()}
      />
      <TemporaryPasswordDialog
        returnFocus={returnTo}
        result={temporary}
        onClose={() => {
          setTemporary(null);
        }}
      />
    </div>
  );
}
