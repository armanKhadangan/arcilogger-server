import type { ImportResult } from '@/lib/api-types';
import { DEVICES } from '@/test/samples';

import {
  canApply,
  fileProblem,
  importReducer,
  initialImport,
  IMPORT_MAX_BYTES,
  isBusy,
  previewCell,
  previewColumns,
  wasWritten,
  type ImportAction,
  type ImportState,
} from './import-model';

const csv = (name = 'kisiler.csv', size = 120) =>
  new File(['x'.repeat(size)], name, { type: 'text/csv' });
// Captured dry runs: one person created and one updated; three refused lines.
const CLEAN = DEVICES.import_persons_preview;
const WITH_ERRORS = DEVICES.import_persons_errors;
// Applying answers with the same counts and `dry_run: false` (captured for assignments).
const APPLIED: ImportResult = { ...CLEAN, dry_run: false };

function run(...actions: ImportAction[]): ImportState {
  return actions.reduce(importReducer, initialImport());
}

describe('CSV import state machine', () => {
  it('choose → check → preview → apply → done', () => {
    const file = csv();
    const steps: ImportAction[] = [
      { type: 'file', file },
      { type: 'check' },
      { type: 'checked', result: CLEAN },
      { type: 'apply' },
      { type: 'applied', result: APPLIED },
    ];
    const seen = steps.map((_, index) => run(...steps.slice(0, index + 1)).step);
    expect(seen).toEqual(['choose', 'checking', 'preview', 'applying', 'done']);
    expect(run(...steps)).toEqual({ step: 'done', kind: 'persons', result: APPLIED });
  });

  it('an apply refused because the data changed meanwhile writes nothing: back to the preview', () => {
    const file = csv();
    const refused: ImportResult = { ...WITH_ERRORS, dry_run: false };
    const state = run(
      { type: 'file', file },
      { type: 'check' },
      { type: 'checked', result: CLEAN },
      { type: 'apply' },
      { type: 'applied', result: refused },
    );
    expect(state).toEqual({ step: 'preview', kind: 'persons', file, result: refused });
    expect(wasWritten(refused)).toBe(false);
    expect(wasWritten(APPLIED)).toBe(true);
    expect(wasWritten(CLEAN)).toBe(false);
    expect(canApply(state)).toBe(false);
    expect(importReducer(state, { type: 'check' })).toEqual({
      step: 'checking',
      kind: 'persons',
      file,
    });
  });

  it('cannot check without a file or with a file it refuses', () => {
    expect(run({ type: 'check' }).step).toBe('choose');
    const big = csv('buyuk.csv', IMPORT_MAX_BYTES + 1);
    const state = run({ type: 'file', file: big });
    expect(state).toMatchObject({ step: 'choose', problem: 'devices.import.errors.tooLarge' });
    expect(importReducer(state, { type: 'check' })).toBe(state);
  });

  it('a check with refused lines cannot be applied; nor one that changes nothing', () => {
    const errors = run(
      { type: 'file', file: csv() },
      { type: 'check' },
      { type: 'checked', result: WITH_ERRORS },
    );
    expect(errors.step).toBe('preview');
    expect(canApply(errors)).toBe(false);
    expect(importReducer(errors, { type: 'apply' })).toBe(errors);
    const nothing: ImportResult = { ...CLEAN, created: 0, updated: 0, unchanged: 2, preview: [] };
    expect(
      canApply(
        run({ type: 'file', file: csv() }, { type: 'check' }, { type: 'checked', result: nothing }),
      ),
    ).toBe(false);
    expect(
      canApply(
        run({ type: 'file', file: csv() }, { type: 'check' }, { type: 'checked', result: CLEAN }),
      ),
    ).toBe(true);
  });

  it('a failure keeps the file; checking again is possible', () => {
    const file = csv();
    const failed = run(
      { type: 'file', file },
      { type: 'check' },
      { type: 'failed', error: 'Sunucuya ulaşılamadı.' },
    );
    expect(failed).toEqual({
      step: 'failed',
      kind: 'persons',
      file,
      error: 'Sunucuya ulaşılamadı.',
      during: 'check',
    });
    expect(importReducer(failed, { type: 'check' })).toEqual({
      step: 'checking',
      kind: 'persons',
      file,
    });
    const applyFailed = run(
      { type: 'file', file },
      { type: 'check' },
      { type: 'checked', result: CLEAN },
      { type: 'apply' },
      { type: 'failed', error: 'x' },
    );
    expect(applyFailed).toMatchObject({ step: 'failed', during: 'apply', file });
  });

  it('another kind or file drops the preview; nothing changes while a request runs', () => {
    const file = csv();
    const preview = run(
      { type: 'file', file },
      { type: 'check' },
      { type: 'checked', result: CLEAN },
    );
    expect(importReducer(preview, { type: 'kind', kind: 'assignments' })).toEqual({
      step: 'choose',
      kind: 'assignments',
      file,
      problem: null,
    });
    const other = csv('baska.csv');
    expect(importReducer(preview, { type: 'file', file: other })).toMatchObject({
      step: 'choose',
      file: other,
    });
    const checking = run({ type: 'file', file }, { type: 'check' });
    expect(isBusy(checking)).toBe(true);
    for (const action of [
      { type: 'kind', kind: 'assignments' },
      { type: 'file', file: other },
      { type: 'reset' },
      { type: 'apply' },
      { type: 'applied', result: APPLIED },
    ] as ImportAction[])
      expect(importReducer(checking, action)).toBe(checking);
    expect(importReducer(preview, { type: 'reset' })).toEqual(initialImport('persons'));
  });

  it('refuses empty, oversized and non-CSV files before sending them', () => {
    expect(fileProblem(csv('a.csv', 0))).toBe('devices.import.errors.empty');
    expect(fileProblem(csv('a.csv', IMPORT_MAX_BYTES))).toBeNull();
    expect(fileProblem(csv('a.csv', IMPORT_MAX_BYTES + 1))).toBe('devices.import.errors.tooLarge');
    expect(fileProblem(csv('liste.xlsx'))).toBe('devices.import.errors.notCsv');
    expect(fileProblem(csv('LISTE.CSV'))).toBeNull();
  });
});

describe('import preview table', () => {
  it('line, action and the file columns first, then other keys in first-seen order', () => {
    expect(
      previewColumns(
        [
          { title: 'B', line: 2, extra: 1, name: 'A', person_id: 4 },
          { line: 3, other: 'x', name: 'C', id: 9 },
        ],
        'persons',
      ),
    ).toEqual(['line', 'name', 'title', 'extra', 'other']);
    // The samples' keys arrive sorted (`action`, `changes`, …): the table still reads naturally,
    // and the ids are left out (the names say who).
    expect(previewColumns(CLEAN.preview, 'persons')).toEqual([
      'line',
      'action',
      'name',
      'employee_no',
      'changes',
      'duplicate_name',
    ]);
    expect(previewColumns(DEVICES.import_assignments_preview.preview, 'assignments')).toEqual([
      'line',
      'action',
      'mac',
      'person_name',
      'previous_person_name',
      'valid_from',
    ]);
  });

  it('writes cells as text', () => {
    expect(previewCell(null)).toBe('');
    expect(previewCell(undefined)).toBe('');
    expect(previewCell(true)).toBe('true');
    expect(previewCell(2002)).toBe('2002');
    expect(previewCell({ id: 1 })).toBe('{"id":1}');
    expect(previewCell(['title', 'email'])).toBe('title, email');
    expect(previewCell([])).toBe('');
  });
});
