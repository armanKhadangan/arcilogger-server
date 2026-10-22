/**
 * Form checks done in the browser before a request is sent.
 *
 * They mirror the server's rules (`arcilogger_api.auth.schemas`, `.accounts`, `.passwords`,
 * `.totp`) only as far as the browser can know them, to give immediate feedback; the server
 * stays the authority (its password policy also checks blocklists, the user name and a raised
 * `auth.password_min_length`, and its answer is shown as it comes). Validators return i18n keys.
 */

/** Spec 13 minimum; `auth.password_min_length` on the server can only raise it. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 256;
/** Same rule as the `users.username` check constraint (lower-case ASCII). */
export const USERNAME_PATTERN = /^[a-z0-9][a-z0-9._-]{1,63}$/;
export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const DISPLAY_NAME_MAX = 200;
export const EMAIL_MAX = 254;
/** Control, format (bidi overrides, zero-width) and surrogate characters, refused by the API. */
const FORBIDDEN_CHARACTERS = /[\p{Cc}\p{Cf}\p{Cs}]/u;
/** Crockford base32 as used for recovery codes (no i, l, o, u). */
const RECOVERY_ALPHABET = /^[0-9abcdefghjkmnpqrstvwxyz]{16}$/;

export type FieldErrors<K extends string> = Partial<Record<K, string>>;

export function hasErrors(errors: FieldErrors<string>): boolean {
  return Object.values(errors).some(Boolean);
}

/**
 * After a refused submit, moves the focus to the first control (in page order) whose field has a
 * problem: keyboard users land there and screen readers announce its error, which the control
 * names in `aria-describedby` (WCAG 3.3.1, 4.1.3). `idOf` gives a field's control id. Runs after
 * the next paint, when the error texts (and a step the form switched to) are on the page.
 */
export function focusFirstError<K extends string>(
  errors: FieldErrors<K>,
  idOf: (field: K) => string,
): void {
  const fields = (Object.keys(errors) as K[]).filter((field) => errors[field]);
  if (fields.length === 0) return;
  window.requestAnimationFrame(() => {
    const controls = fields
      .map((field) => document.getElementById(idOf(field)))
      .filter((element): element is HTMLElement => element !== null)
      .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    controls[0]?.focus();
  });
}

/** Length in Unicode code points, as Python's `len()` counts it on the server. */
export function codePointLength(text: string): number {
  let count = 0;
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- only counting
  for (const _ of text) count += 1;
  return count;
}

/** The user name as the server stores it, or `null` when it breaks the rule. */
export function normalizeUsername(text: string): string | null {
  const value = text.trim().toLowerCase();
  // eslint-disable-next-line no-control-regex -- ASCII check
  if (!/^[\x00-\x7f]*$/.test(value) || !USERNAME_PATTERN.test(value)) return null;
  return value;
}

/** Six ASCII digits (spaces ignored), else `null` (Arabic-Indic digits are not codes). */
export function normalizeTotp(text: string): string | null {
  const value = text.replace(/\s+/g, '');
  return /^[0-9]{6}$/.test(value) ? value : null;
}

/**
 * A recovery code in canonical form (16 characters, dashes/spaces/case ignored, i/l read as 1
 * and o as 0 — like the server), else `null`.
 */
export function normalizeRecoveryCode(text: string): string | null {
  const compact = text
    .replace(/[\s-]+/g, '')
    .toLowerCase()
    .replace(/[il]/g, '1')
    .replace(/o/g, '0');
  return RECOVERY_ALPHABET.test(compact) ? compact : null;
}

export function validateLogin(values: {
  username: string;
  password: string;
}): FieldErrors<'username' | 'password'> {
  return {
    ...(values.username.trim() ? {} : { username: 'validation.required' }),
    ...(values.password ? {} : { password: 'validation.required' }),
  };
}

export function validateSecondFactor(
  value: string,
  mode: 'totp' | 'recovery',
): FieldErrors<'code'> {
  if (!value.trim()) return { code: 'validation.required' };
  if (mode === 'totp' && normalizeTotp(value) === null) return { code: 'validation.totpFormat' };
  if (mode === 'recovery' && normalizeRecoveryCode(value) === null)
    return { code: 'validation.recoveryFormat' };
  return {};
}

/** New password + confirmation (+ the current one when it must be typed). */
export function validateNewPassword(values: {
  current?: string;
  password: string;
  confirm: string;
}): FieldErrors<'current' | 'password' | 'confirm'> {
  const errors: FieldErrors<'current' | 'password' | 'confirm'> = {};
  const length = codePointLength(values.password);
  if (values.current !== undefined && !values.current) errors.current = 'validation.required';
  if (!values.password) errors.password = 'validation.required';
  else if (length < PASSWORD_MIN_LENGTH) errors.password = 'validation.passwordTooShort';
  else if (length > PASSWORD_MAX_LENGTH) errors.password = 'validation.passwordTooLong';
  else if (values.current !== undefined && values.password === values.current)
    errors.password = 'validation.passwordReused';
  if (!values.confirm) errors.confirm = 'validation.required';
  else if (values.confirm !== values.password) errors.confirm = 'validation.passwordMismatch';
  return errors;
}

export function validateDisplayName(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return 'validation.required';
  if (trimmed.length > DISPLAY_NAME_MAX) return 'validation.tooLong';
  if (FORBIDDEN_CHARACTERS.test(trimmed)) return 'validation.controlCharacters';
  return undefined;
}

export function validateEmail(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined; // optional
  if (trimmed.length > EMAIL_MAX) return 'validation.tooLong';
  if (FORBIDDEN_CHARACTERS.test(trimmed) || !EMAIL_PATTERN.test(trimmed)) return 'validation.email';
  return undefined;
}

export function validateUsername(value: string): string | undefined {
  if (!value.trim()) return 'validation.required';
  return normalizeUsername(value) === null ? 'validation.username' : undefined;
}

export function validateUserForm(values: {
  username: string;
  displayName: string;
  email: string;
  role: string;
}): FieldErrors<'username' | 'displayName' | 'email' | 'role'> {
  const errors: FieldErrors<'username' | 'displayName' | 'email' | 'role'> = {};
  const username = validateUsername(values.username);
  if (username) errors.username = username;
  const displayName = validateDisplayName(values.displayName);
  if (displayName) errors.displayName = displayName;
  const email = validateEmail(values.email);
  if (email) errors.email = email;
  if (!values.role) errors.role = 'validation.required';
  return errors;
}

export function validateSetup(values: {
  token: string;
  username: string;
  displayName: string;
  password: string;
  confirm: string;
}): FieldErrors<'token' | 'username' | 'displayName' | 'password' | 'confirm'> {
  const errors: FieldErrors<'token' | 'username' | 'displayName' | 'password' | 'confirm'> = {
    ...validateNewPassword({ password: values.password, confirm: values.confirm }),
  };
  if (!values.token.trim()) errors.token = 'validation.required';
  const username = validateUsername(values.username);
  if (username) errors.username = username;
  const displayName = validateDisplayName(values.displayName);
  if (displayName) errors.displayName = displayName;
  return errors;
}
