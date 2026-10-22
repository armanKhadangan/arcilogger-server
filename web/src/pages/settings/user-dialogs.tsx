import { KeyRound } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import type { FormEvent, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { CodeBlock } from '@/components/ui/code-block';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { errorMessage, isApiError } from '@/lib/api';
import { useCreateUser, useUpdateUser } from '@/lib/api-hooks';
import type { CreatedUser, Role, UserOut } from '@/lib/api-types';
import { currentLang } from '@/lib/i18n';
import { isRole, roleLabel, ROLE_IDS } from '@/lib/permissions';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import {
  focusFirstError,
  hasErrors,
  normalizeUsername,
  validateDisplayName,
  validateEmail,
  validateUserForm,
  type FieldErrors,
} from '@/lib/validation';

/** Role picker with the server's role labels (`aria-required` comes from a required `Field`). */
export function RoleSelect({
  id,
  value,
  onChange,
  invalid,
  describedBy,
  'aria-required': ariaRequired,
}: {
  id: string;
  value: string;
  onChange: (role: Role) => void;
  invalid?: boolean;
  describedBy?: string | undefined;
  'aria-required'?: boolean;
}) {
  const { t } = useTranslation();
  const lang = currentLang();
  return (
    <Select
      value={value}
      onValueChange={(next) => {
        if (isRole(next)) onChange(next);
      }}
    >
      <SelectTrigger
        id={id}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy}
        aria-required={ariaRequired}
      >
        <SelectValue placeholder={t('common.selectPlaceholder')} />
      </SelectTrigger>
      <SelectContent>
        {ROLE_IDS.map((role) => (
          <SelectItem key={role} value={role}>
            {roleLabel(role, lang)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * Where the focus goes when a dialog closes: these dialogs are opened from a row's menu (gone by
 * then) or follow one another, so the page passes the button that started the action.
 */
type FocusTarget = RefObject<HTMLElement | null>;

type UserField = 'username' | 'displayName' | 'email' | 'role';

function serverField(code: string): UserField | null {
  if (code === 'username_taken' || code === 'username_invalid') return 'username';
  return null;
}

/** "Kullanıcı ekle": the server creates a temporary password, shown once afterwards. */
export function CreateUserDialog({
  open,
  onOpenChange,
  onCreated,
  returnFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (result: CreatedUser) => void;
  returnFocus?: FocusTarget;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const create = useCreateUser();
  const empty = { username: '', displayName: '', email: '', role: '' };
  const [values, setValues] = useState<Record<UserField, string>>(empty);
  const [errors, setErrors] = useState<FieldErrors<UserField>>({});
  const [failure, setFailure] = useState<{ field: UserField | null; text: string } | null>(null);

  useEffect(() => {
    if (open) {
      setValues({ username: '', displayName: '', email: '', role: '' });
      setErrors({});
      setFailure(null);
    }
  }, [open]);

  function set(field: UserField, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateUserForm(values);
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    if (!isRole(values.role)) return;
    const role = values.role;
    const email = values.email.trim();
    try {
      const result = await withReauth(() =>
        create.mutateAsync({
          username: normalizeUsername(values.username) ?? values.username,
          display_name: values.displayName.trim(),
          email: email || null,
          role,
        }),
      );
      onCreated(result);
    } catch (error) {
      if (isReauthCancelled(error)) return;
      setFailure({
        field: isApiError(error) ? serverField(error.code) : null,
        text: errorMessage(error),
      });
    }
  }

  const message = (field: UserField) =>
    failure?.field === field ? failure.text : errors[field] && t(errors[field]);
  const describedBy = (field: UserField, hint = false) =>
    message(field) ? `${id}-${field}-error` : hint ? `${id}-${field}-hint` : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{t('users.createTitle')}</DialogTitle>
            <DialogDescription>{t('users.createDescription')}</DialogDescription>
          </DialogHeader>
          {failure && failure.field === null ? <Alert tone="danger">{failure.text}</Alert> : null}
          <Field
            id={`${id}-username`}
            label={t('users.username')}
            hint={t('users.usernameHint')}
            error={message('username')}
            required
          >
            <Input
              id={`${id}-username`}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              value={values.username}
              aria-invalid={message('username') ? true : undefined}
              aria-describedby={describedBy('username', true)}
              onChange={(event) => {
                set('username', event.target.value);
              }}
              autoFocus
            />
          </Field>
          <Field
            id={`${id}-displayName`}
            label={t('users.displayName')}
            error={message('displayName')}
            required
          >
            <Input
              id={`${id}-displayName`}
              autoComplete="off"
              value={values.displayName}
              aria-invalid={message('displayName') ? true : undefined}
              aria-describedby={describedBy('displayName')}
              onChange={(event) => {
                set('displayName', event.target.value);
              }}
            />
          </Field>
          <Field
            id={`${id}-email`}
            label={t('users.email')}
            hint={t('users.emailHint')}
            error={message('email')}
          >
            <Input
              id={`${id}-email`}
              type="email"
              autoComplete="off"
              value={values.email}
              aria-invalid={message('email') ? true : undefined}
              aria-describedby={describedBy('email', true)}
              onChange={(event) => {
                set('email', event.target.value);
              }}
            />
          </Field>
          <Field id={`${id}-role`} label={t('users.role')} error={message('role')} required>
            <RoleSelect
              id={`${id}-role`}
              value={values.role}
              invalid={Boolean(message('role'))}
              describedBy={describedBy('role')}
              onChange={(role) => {
                set('role', role);
              }}
            />
          </Field>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={create.isPending}>
              {create.isPending ? t('common.working') : t('users.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** "Düzenle": display name and e-mail (`PATCH /users/{id}`). */
export function EditUserDialog({
  user,
  onOpenChange,
  returnFocus,
}: {
  user: UserOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: FocusTarget;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const update = useUpdateUser();
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [errors, setErrors] = useState<FieldErrors<'displayName' | 'email'>>({});
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (user) {
      setDisplayName(user.display_name);
      setEmail(user.email ?? '');
      setErrors({});
      setFailure(null);
    }
  }, [user]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!user) return;
    const found: FieldErrors<'displayName' | 'email'> = {};
    const nameProblem = validateDisplayName(displayName);
    if (nameProblem) found.displayName = nameProblem;
    const emailProblem = validateEmail(email);
    if (emailProblem) found.email = emailProblem;
    setErrors(found);
    setFailure(null);
    if (hasErrors(found)) {
      focusFirstError(found, (field) => `${id}-${field}`);
      return;
    }
    try {
      await withReauth(() =>
        update.mutateAsync({
          id: user.id,
          display_name: displayName.trim(),
          email: email.trim() || null,
        }),
      );
      toast({ title: t('users.saved'), variant: 'success' });
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  const nameError = errors.displayName && t(errors.displayName);
  const emailError = errors.email && t(errors.email);
  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4" noValidate>
          <DialogHeader>
            <DialogTitle>{t('users.editTitle')}</DialogTitle>
            <DialogDescription>
              {t('users.editDescription', { username: user?.username ?? '' })}
            </DialogDescription>
          </DialogHeader>
          {failure ? <Alert tone="danger">{failure}</Alert> : null}
          <Field id={`${id}-displayName`} label={t('users.displayName')} error={nameError} required>
            <Input
              id={`${id}-displayName`}
              value={displayName}
              aria-invalid={nameError ? true : undefined}
              aria-describedby={nameError ? `${id}-displayName-error` : undefined}
              onChange={(event) => {
                setDisplayName(event.target.value);
              }}
              autoFocus
            />
          </Field>
          <Field
            id={`${id}-email`}
            label={t('users.email')}
            hint={t('users.emailHint')}
            error={emailError}
          >
            <Input
              id={`${id}-email`}
              type="email"
              value={email}
              aria-invalid={emailError ? true : undefined}
              aria-describedby={emailError ? `${id}-email-error` : `${id}-email-hint`}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
          </Field>
          <DialogFooter>
            <Button
              variant="secondary"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={update.isPending}>
              {update.isPending ? t('common.working') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** "Rolü değiştir": the user's sessions end, the new rights apply at the next sign-in. */
export function RoleDialog({
  user,
  onOpenChange,
  returnFocus,
}: {
  user: UserOut | null;
  onOpenChange: (open: boolean) => void;
  returnFocus?: FocusTarget;
}) {
  const { t } = useTranslation();
  const id = useId();
  const { withReauth } = useReauth();
  const update = useUpdateUser();
  const [role, setRole] = useState<string>('');
  const [failure, setFailure] = useState<string | null>(null);
  const lang = currentLang();

  useEffect(() => {
    if (user) {
      setRole(user.role);
      setFailure(null);
    }
  }, [user]);

  async function submit() {
    if (!user || !isRole(role)) return;
    setFailure(null);
    try {
      await withReauth(() => update.mutateAsync({ id: user.id, role }));
      toast({
        title: t('users.roleChanged', { name: user.display_name, role: roleLabel(role, lang) }),
        variant: 'success',
      });
      onOpenChange(false);
    } catch (error) {
      if (!isReauthCancelled(error)) setFailure(errorMessage(error));
    }
  }

  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent returnFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle>{t('users.roleTitle')}</DialogTitle>
          <DialogDescription>
            {t('users.roleDescription', { name: user?.display_name ?? '' })}
          </DialogDescription>
        </DialogHeader>
        {failure ? <Alert tone="danger">{failure}</Alert> : null}
        <Field id={`${id}-role`} label={t('users.role')} required>
          <RoleSelect id={`${id}-role`} value={role} onChange={setRole} />
        </Field>
        <DialogFooter>
          <Button
            variant="secondary"
            autoFocus
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t('common.cancel')}
          </Button>
          <Button
            disabled={!user || role === user.role}
            busy={update.isPending}
            onClick={() => void submit()}
          >
            {update.isPending ? t('common.working') : t('users.roleSubmit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The temporary password of a new or reset account, shown exactly once. */
export function TemporaryPasswordDialog({
  result,
  onClose,
  returnFocus,
}: {
  result: { user: UserOut; password: string } | null;
  onClose: () => void;
  returnFocus?: FocusTarget;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={result !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent returnFocus={returnFocus}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound aria-hidden="true" className="size-5 text-primary" />
            {t('users.tempTitle')}
          </DialogTitle>
          <DialogDescription>
            {t('users.tempDescription', {
              name: result?.user.display_name ?? '',
              username: result?.user.username ?? '',
            })}
          </DialogDescription>
        </DialogHeader>
        <Alert tone="warning" assertive={false}>
          {t('users.tempOnce')}
        </Alert>
        {result ? <CodeBlock label={t('users.tempLabel')} code={result.password} /> : null}
        <DialogFooter>
          <Button onClick={onClose}>{t('common.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
