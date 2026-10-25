/**
 * The person form (create and edit) of "Kişiler": its fields as typed, the browser-side checks
 * and the request bodies (no components here; unit-tested in `persons-model.test.ts`).
 */

import type { PersonCreate, PersonOut, PersonUpdate } from '@/lib/api-types';
import {
  codePointLength,
  validateDisplayName,
  validateEmail,
  type FieldErrors,
} from '@/lib/validation';

/** Select value of "no department" (a select item cannot have an empty value). */
export const NO_DEPARTMENT = 'none';
/** Control, format (bidi overrides, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;
/** The API's limits of `PersonCreate` / `PersonUpdate` (characters). */
export const PERSON_LIMITS = { name: 200, title: 200, employeeNo: 64, notes: 2000 } as const;

export type PersonField = 'name' | 'department' | 'title' | 'email' | 'employeeNo' | 'notes';

export interface PersonFormValues {
  name: string;
  /** A department id or {@link NO_DEPARTMENT}. */
  department: string;
  title: string;
  email: string;
  employeeNo: string;
  notes: string;
  active: boolean;
  /** "Kullanım raporlarından hariç tut" (`usage_excluded`). */
  usageExcluded: boolean;
}

export function personFormValues(person: PersonOut | null): PersonFormValues {
  return {
    name: person?.name ?? '',
    department: person?.department ? String(person.department.id) : NO_DEPARTMENT,
    title: person?.title ?? '',
    email: person?.email ?? '',
    employeeNo: person?.employee_no ?? '',
    notes: person?.notes ?? '',
    active: person?.active ?? true,
    usageExcluded: person?.usage_excluded ?? false,
  };
}

function optionalText(value: string, max: number): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (codePointLength(trimmed) > max) return 'validation.tooLong';
  if (FORBIDDEN.test(trimmed)) return 'validation.controlCharacters';
  return undefined;
}

/** Problems of the form as i18n keys (the server checks again). */
export function validatePerson(values: PersonFormValues): FieldErrors<PersonField> {
  const errors: FieldErrors<PersonField> = {};
  const name = validateDisplayName(values.name);
  if (name) errors.name = name;
  const title = optionalText(values.title, PERSON_LIMITS.title);
  if (title) errors.title = title;
  const email = validateEmail(values.email);
  if (email) errors.email = email;
  const employeeNo = optionalText(values.employeeNo, PERSON_LIMITS.employeeNo);
  if (employeeNo) errors.employeeNo = employeeNo;
  // Notes may have line breaks; other control characters are refused.
  if (codePointLength(values.notes.trim()) > PERSON_LIMITS.notes)
    errors.notes = 'validation.tooLong';
  else if (FORBIDDEN.test(values.notes.replace(/\r?\n/g, ' ')))
    errors.notes = 'validation.controlCharacters';
  return errors;
}

function departmentId(value: string): number | null {
  return value === NO_DEPARTMENT ? null : Number(value);
}

const orNull = (value: string): string | null => value.trim() || null;

/**
 * `POST /persons`: empty optional fields are left out (the person starts active and in the
 * usage reports).
 */
export function personCreateBody(values: PersonFormValues): PersonCreate {
  const body: PersonCreate = { name: values.name.trim() };
  const department = departmentId(values.department);
  if (department !== null) body.department_id = department;
  for (const [key, value] of [
    ['title', values.title],
    ['email', values.email],
    ['employee_no', values.employeeNo],
    ['notes', values.notes],
  ] as const) {
    const text = orNull(value);
    if (text !== null) body[key] = text;
  }
  if (values.usageExcluded) body.usage_excluded = true;
  return body;
}

/** `PATCH /persons/{id}`: only what changed; a cleared field is sent as `null`. */
export function personChanges(person: PersonOut, values: PersonFormValues): PersonUpdate {
  const changes: PersonUpdate = {};
  const name = values.name.trim();
  if (name !== person.name) changes.name = name;
  const department = departmentId(values.department);
  if (department !== (person.department?.id ?? null)) changes.department_id = department;
  if (orNull(values.title) !== person.title) changes.title = orNull(values.title);
  if (orNull(values.email) !== person.email) changes.email = orNull(values.email);
  if (orNull(values.employeeNo) !== person.employee_no)
    changes.employee_no = orNull(values.employeeNo);
  if (orNull(values.notes) !== person.notes) changes.notes = orNull(values.notes);
  if (values.active !== person.active) changes.active = values.active;
  if (values.usageExcluded !== person.usage_excluded) changes.usage_excluded = values.usageExcluded;
  return changes;
}
