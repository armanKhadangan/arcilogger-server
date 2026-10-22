/**
 * The CSV import of persons and device assignments as a state machine (no components here;
 * unit-tested in `import-model.test.ts`):
 *
 *     choose ──check──▶ checking ──checked──▶ preview ──apply──▶ applying ──applied──▶ done
 *        ▲                  │                    │                   │
 *        └──── kind/file/reset (any settled step) └── failed ◀───────┘
 *
 * "Kontrol et" sends the file with `dry_run=true`: the server parses it and reports what would
 * change (counts, refused lines, a preview) without writing anything. "Uygula" sends the same
 * file again with `dry_run=false`; the server writes every line or — when a line has an error —
 * none. A file with refused lines is therefore not offered for applying: the user fixes them and
 * checks again. When the data changed between the check and the apply so that a line is refused
 * now, the apply answers with those errors (nothing written) and the import returns to `preview`.
 * Problems of the whole file (encoding, header, size) are API errors (`failed`).
 */

import type { ImportKind, ImportResult } from '@/lib/api-types';

/** `arcilogger_api.csvfiles`: at most 5 MB (and 10 000 rows, which only the server counts). */
export const IMPORT_MAX_BYTES = 5 * 1024 * 1024;
export const IMPORT_MAX_ROWS = 10_000;

export type ImportState =
  | { step: 'choose'; kind: ImportKind; file: File | null; problem: string | null }
  | { step: 'checking'; kind: ImportKind; file: File }
  | { step: 'preview'; kind: ImportKind; file: File; result: ImportResult }
  | { step: 'applying'; kind: ImportKind; file: File; result: ImportResult }
  | { step: 'done'; kind: ImportKind; result: ImportResult }
  | { step: 'failed'; kind: ImportKind; file: File; error: string; during: 'check' | 'apply' };

export type ImportAction =
  | { type: 'kind'; kind: ImportKind }
  | { type: 'file'; file: File | null }
  | { type: 'check' }
  | { type: 'checked'; result: ImportResult }
  | { type: 'apply' }
  | { type: 'applied'; result: ImportResult }
  | { type: 'failed'; error: string }
  | { type: 'reset' };

export function initialImport(kind: ImportKind = 'persons'): ImportState {
  return { step: 'choose', kind, file: null, problem: null };
}

/** Why a file cannot be sent (i18n key), `null` when it may be checked. */
export function fileProblem(file: File): string | null {
  if (file.size === 0) return 'devices.import.errors.empty';
  if (file.size > IMPORT_MAX_BYTES) return 'devices.import.errors.tooLarge';
  if (!/\.(csv|txt)$/i.test(file.name)) return 'devices.import.errors.notCsv';
  return null;
}

/** Whether "Uygula" may be offered: a clean dry run that would change something. */
export function canApply(state: ImportState): boolean {
  if (state.step !== 'preview') return false;
  const { result } = state;
  return result.dry_run && result.errors.length === 0 && result.created + result.updated > 0;
}

/** The server lists at most this many refused lines, then one line "… and n more errors". */
export const IMPORT_MAX_ERRORS = 200;

/** Refused lines of a result; `more`: the list was cut (the last entry counts the rest). */
export function refusedLines(result: ImportResult): { count: number; more: boolean } {
  const more = result.errors.length > IMPORT_MAX_ERRORS;
  return { count: more ? IMPORT_MAX_ERRORS : result.errors.length, more };
}

/** True for the answer of an apply that wrote the file (all lines; never a part of it). */
export function wasWritten(result: ImportResult): boolean {
  return !result.dry_run && result.errors.length === 0;
}

/** True while a request is running (the form is locked). */
export function isBusy(state: ImportState): boolean {
  return state.step === 'checking' || state.step === 'applying';
}

function choose(kind: ImportKind, file: File | null): ImportState {
  return { step: 'choose', kind, file, problem: file ? fileProblem(file) : null };
}

export function importReducer(state: ImportState, action: ImportAction): ImportState {
  switch (action.type) {
    case 'kind':
      // Another kind reads the file differently: its preview is no longer valid.
      if (isBusy(state) || action.kind === state.kind) return state;
      return choose(action.kind, state.step === 'done' ? null : state.file);
    case 'file':
      if (isBusy(state)) return state;
      return choose(state.kind, action.file);
    case 'check':
      if (state.step === 'choose' && state.file && state.problem === null)
        return { step: 'checking', kind: state.kind, file: state.file };
      if (state.step === 'failed' || state.step === 'preview')
        return { step: 'checking', kind: state.kind, file: state.file };
      return state;
    case 'checked':
      if (state.step !== 'checking') return state;
      return { step: 'preview', kind: state.kind, file: state.file, result: action.result };
    case 'apply':
      if (!canApply(state) || state.step !== 'preview') return state;
      return { step: 'applying', kind: state.kind, file: state.file, result: state.result };
    case 'applied':
      if (state.step !== 'applying') return state;
      // The data changed after the check and a line is refused now: the server wrote nothing.
      // Back to the preview with the new errors (fix them, check again).
      if (!wasWritten(action.result))
        return { step: 'preview', kind: state.kind, file: state.file, result: action.result };
      return { step: 'done', kind: state.kind, result: action.result };
    case 'failed':
      if (state.step === 'checking')
        return { ...state, step: 'failed', error: action.error, during: 'check' };
      if (state.step === 'applying')
        return {
          step: 'failed',
          kind: state.kind,
          file: state.file,
          error: action.error,
          during: 'apply',
        };
      return state;
    case 'reset':
      if (isBusy(state)) return state;
      return initialImport(state.kind);
  }
}

/** The CSV columns of each import (`arcilogger_api.csvimport`), in the template's order. */
export const IMPORT_COLUMNS: Record<ImportKind, readonly string[]> = {
  persons: ['name', 'department', 'title', 'email', 'employee_no', 'active', 'notes'],
  assignments: ['mac', 'employee_no', 'person_name', 'valid_from', 'note'],
};

/**
 * The preview table's columns of each kind (the API's preview rows `PersonPreview` and
 * `AssignmentPreview`); ids are left out — the names say who.
 */
export const PREVIEW_COLUMNS: Record<ImportKind, readonly string[]> = {
  persons: ['line', 'action', 'name', 'employee_no', 'changes', 'duplicate_name'],
  assignments: ['line', 'action', 'mac', 'person_name', 'previous_person_name', 'valid_from'],
};

/**
 * Column order of the preview table: the kind's known columns that occur, then any other key of
 * the rows in first-seen order except ids (JSON objects may arrive with sorted keys).
 */
export function previewColumns(
  rows: readonly Record<string, unknown>[],
  kind: ImportKind,
): string[] {
  const present = new Set(rows.flatMap((row) => Object.keys(row)));
  const columns = PREVIEW_COLUMNS[kind].filter((key) => present.has(key));
  for (const row of rows) {
    for (const key of Object.keys(row))
      if (!columns.includes(key) && key !== 'id' && !key.endsWith('_id')) columns.push(key);
  }
  return columns;
}

/** A preview cell as text (`null`/missing: empty; lists joined; objects as JSON). */
export function previewCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(previewCell).join(', ');
  return JSON.stringify(value);
}
