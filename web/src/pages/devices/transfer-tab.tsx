import { Download, FileDown, FileUp, ListChecks, RotateCcw } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useId, useReducer, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ModuleLockedCard } from '@/components/license/module-locked';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { toast } from '@/components/ui/toast';
import { apiDownload, errorMessage } from '@/lib/api';
import { useImportCsv, useLicense } from '@/lib/api-hooks';
import type { ImportKind, ImportResult } from '@/lib/api-types';
import { saveBlob } from '@/lib/download';
import { visibleText } from '@/lib/display';
import { formatBytes, formatDateTime, formatNumber } from '@/lib/format';
import { currentLang } from '@/lib/i18n';
import { moduleEnabled, type ModuleKey } from '@/lib/license';
import { isReauthCancelled, useReauth } from '@/lib/reauth';
import { cn } from '@/lib/utils';

import { useCsvExport } from './csv-export';
import { PrivacyWait } from './device-parts';
import {
  canApply,
  IMPORT_COLUMNS,
  importReducer,
  initialImport,
  IMPORT_MAX_BYTES,
  IMPORT_MAX_ROWS,
  isBusy,
  previewCell,
  previewColumns,
  refusedLines,
  wasWritten,
} from './import-model';

const KINDS: readonly ImportKind[] = ['persons', 'assignments'];
/** `POST /imports/{kind}` and the templates belong to this licence module (contract 1.3). */
const IMPORT_MODULE: ModuleKey = 'bulk_import';

/** A preview cell: actions, changed fields, yes/no and times in words, else as the API sent it. */
function shownCell(column: string, value: unknown, t: TFunction): string {
  if (column === 'action' && typeof value === 'string')
    return t(`devices.import.actions.${value}`, { defaultValue: value });
  if (column === 'changes' && Array.isArray(value))
    return value
      .map((field) =>
        typeof field === 'string'
          ? t(`devices.import.fields.${field}`, { defaultValue: field })
          : previewCell(field),
      )
      .join(', ');
  if (column === 'valid_from' && typeof value === 'string' && !Number.isNaN(Date.parse(value)))
    return formatDateTime(value);
  if (typeof value === 'boolean') return value ? t('devices.yes') : t('devices.no');
  return visibleText(previewCell(value));
}

/** Counts of a check or an import (rows, new, changed, unchanged, refused lines). */
function ImportCounts({ result }: { result: ImportResult }) {
  const { t } = useTranslation();
  const lang = currentLang();
  // A refused apply wrote nothing: its counts are what would have changed.
  const when = wasWritten(result) ? 'done' : 'dry';
  const refused = refusedLines(result);
  const count = (value: number) => formatNumber(value, 0, lang);
  const items: [string, string, string?][] = [
    [t('devices.import.counts.rows'), count(result.rows)],
    [t(`devices.import.counts.${when}.created`), count(result.created)],
    [t(`devices.import.counts.${when}.updated`), count(result.updated)],
    [t(`devices.import.counts.${when}.unchanged`), count(result.unchanged)],
    [
      t('devices.import.counts.errors'),
      refused.more ? `${count(refused.count)}+` : count(refused.count),
      refused.count ? 'danger' : undefined,
    ],
  ];
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5" data-testid="import-counts">
      {items.map(([label, value, tone]) => (
        <div
          key={label}
          className={cn(
            'flex flex-col gap-0.5 rounded-lg border border-border px-3 py-2',
            tone === 'danger' && 'border-danger/40 bg-danger-soft',
          )}
        >
          <dt className={cn('text-xs text-muted-strong', tone === 'danger' && 'text-danger-ink')}>
            {label}
          </dt>
          <dd
            className={cn(
              'text-lg font-semibold text-foreground tabular',
              tone === 'danger' && 'text-danger-ink',
            )}
          >
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ImportErrors({ result }: { result: ImportResult }) {
  const { t } = useTranslation();
  const english = currentLang() === 'en';
  const refused = refusedLines(result);
  if (result.errors.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <Alert
        tone="danger"
        title={
          refused.more
            ? t('devices.import.errorsTitleMore', { count: refused.count })
            : t('devices.import.errorsTitle', { count: refused.count })
        }
      >
        {t('devices.import.errorsBody')}
      </Alert>
      <div className="max-h-72 overflow-auto rounded-lg border border-border">
        <table className="w-full border-separate border-spacing-0 text-13">
          <caption className="sr-only">{t('devices.import.errorsCaption')}</caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky top-0 w-20 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
              >
                {t('devices.import.line')}
              </th>
              <th
                scope="col"
                className="sticky top-0 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium text-muted-strong"
              >
                {t('devices.import.problem')}
              </th>
            </tr>
          </thead>
          <tbody>
            {result.errors.map((error, index) => (
              <tr key={`${error.line}-${index}`}>
                <td className="border-b border-border px-3 py-1.5 tabular">{error.line}</td>
                <td className="border-b border-border px-3 py-1.5">
                  {visibleText(english ? error.message_en : error.message_tr)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ImportPreview({ result }: { result: ImportResult }) {
  const { t } = useTranslation();
  const columns = previewColumns(result.preview, result.kind);
  const title = wasWritten(result)
    ? t('devices.import.changedTitle')
    : t('devices.import.previewTitle');
  if (result.preview.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-13 font-semibold text-foreground">{title}</h4>
      <div className="max-h-96 overflow-auto rounded-lg border border-border">
        <table className="w-full border-separate border-spacing-0 text-13">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="sticky top-0 border-b border-border bg-subtle px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted-strong"
                >
                  {t(`devices.import.fields.${column}`, { defaultValue: column })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.preview.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column} className="border-b border-border px-3 py-1.5 whitespace-nowrap">
                    {shownCell(column, row[column], t)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.rows > result.preview.length ? (
        <p className="text-xs text-muted">
          {t('devices.import.previewLimited', { count: result.preview.length })}
        </p>
      ) : null}
    </div>
  );
}

/** CSV export of the whole device and person lists (the list tabs export their filtered view). */
function ExportCard() {
  const { t } = useTranslation();
  const { busy, download } = useCsvExport();

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-1">
          <CardTitle>{t('devices.export.title')}</CardTitle>
          <CardDescription>{t('devices.export.description')}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            busy={busy === 'devices'}
            onClick={() => void download('devices')}
          >
            <FileDown aria-hidden="true" />
            {t('devices.export.devices')}
          </Button>
          <Button
            variant="secondary"
            busy={busy === 'persons'}
            onClick={() => void download('persons')}
          >
            <FileDown aria-hidden="true" />
            {t('devices.export.persons')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * CSV import (`devices.manage`): choose what to import, download the template, choose the file,
 * check it (dry run: nothing is written) and apply it once the check found no refused line.
 */
function ImportCard() {
  const { t } = useTranslation();
  const lang = currentLang();
  const id = useId();
  const { withReauth } = useReauth();
  const run = useImportCsv();
  const [state, dispatch] = useReducer(importReducer, undefined, () => initialImport());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [fileInput, setFileInput] = useState(0);
  const busy = isBusy(state);
  const file = state.step === 'done' ? null : state.file;

  async function send(dryRun: boolean) {
    const start = { type: dryRun ? ('check' as const) : ('apply' as const) };
    // Only from a step that allows it (a file without problems, a clean check).
    if (!file || importReducer(state, start) === state) return;
    dispatch(start);
    try {
      const result = await withReauth(() => run.mutateAsync({ kind: state.kind, file, dryRun }));
      dispatch({ type: dryRun ? 'checked' : 'applied', result });
      if (wasWritten(result)) toast({ title: t('devices.import.applied'), variant: 'success' });
    } catch (error) {
      dispatch({
        type: 'failed',
        error: isReauthCancelled(error) ? t('devices.import.cancelled') : errorMessage(error),
      });
    }
  }

  async function template(kind: ImportKind) {
    setTemplateError(null);
    try {
      const downloaded = await apiDownload(`/imports/${kind}/template.csv`, {
        fallbackName: `arcilogger-${kind === 'persons' ? 'kisiler' : 'atamalar'}-sablon.csv`,
      });
      saveBlob(downloaded.blob, downloaded.filename);
    } catch (error) {
      setTemplateError(errorMessage(error));
    }
  }

  const result =
    state.step === 'preview' || state.step === 'applying' || state.step === 'done'
      ? state.result
      : null;
  const applicable = canApply(state);
  const written = result !== null && wasWritten(result);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-1">
          <CardTitle>{t('devices.import.title')}</CardTitle>
          <CardDescription>
            {t('devices.import.description', {
              size: formatBytes(IMPORT_MAX_BYTES, lang),
              rows: formatNumber(IMPORT_MAX_ROWS, 0, lang),
            })}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <fieldset className="flex flex-col gap-2" disabled={busy}>
          <legend className="mb-1 text-13 font-medium text-foreground">
            {t('devices.import.kind')}
          </legend>
          <div className="grid gap-2 md:grid-cols-2">
            {KINDS.map((kind) => (
              <label
                key={kind}
                className={cn(
                  'flex cursor-pointer gap-3 rounded-lg border border-border p-3 text-13',
                  state.kind === kind && 'border-primary ring-1 ring-primary',
                )}
              >
                <input
                  type="radio"
                  name={`${id}-kind`}
                  value={kind}
                  checked={state.kind === kind}
                  onChange={() => {
                    dispatch({ type: 'kind', kind });
                  }}
                  className="mt-0.5 size-4 accent-[var(--primary)]"
                />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="font-medium text-foreground">
                    {t(`devices.import.kinds.${kind}.title`)}
                  </span>
                  <span className="text-muted">
                    {t(`devices.import.kinds.${kind}.description`)}
                  </span>
                  <code className="font-mono text-xs wrap-anywhere text-muted-strong">
                    {IMPORT_COLUMNS[kind].join(';')}
                  </code>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => void template(state.kind)}>
            <Download aria-hidden="true" />
            {t('devices.import.template')}
          </Button>
          <span className="text-xs text-muted">{t('devices.import.templateHint')}</span>
        </div>
        {templateError ? <Alert tone="danger">{templateError}</Alert> : null}

        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${id}-file`} className="text-13 font-medium text-foreground">
            {t('devices.import.file')}
          </label>
          <input
            key={fileInput}
            id={`${id}-file`}
            type="file"
            accept=".csv,text/csv"
            disabled={busy}
            aria-describedby={
              state.step === 'choose' && state.problem ? `${id}-file-error` : `${id}-file-hint`
            }
            aria-invalid={state.step === 'choose' && state.problem ? true : undefined}
            onChange={(event) => {
              dispatch({ type: 'file', file: event.target.files?.[0] ?? null });
            }}
            className="block w-full max-w-xl text-13 text-foreground file:mr-3 file:h-8 file:rounded-lg file:border file:border-border file:bg-surface file:px-3 file:text-13 file:font-medium file:text-foreground hover:file:bg-subtle"
          />
          {state.step === 'choose' && state.problem ? (
            <p id={`${id}-file-error`} className="text-xs text-danger-ink">
              {t(state.problem, { size: formatBytes(IMPORT_MAX_BYTES, lang) })}
            </p>
          ) : (
            <p id={`${id}-file-hint`} className="text-xs text-muted">
              {file
                ? t('devices.import.chosen', {
                    name: file.name,
                    size: formatBytes(file.size, lang),
                  })
                : t('devices.import.fileHint')}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            busy={state.step === 'checking'}
            disabled={
              !file ||
              state.step === 'applying' ||
              (state.step === 'choose' && state.problem !== null)
            }
            onClick={() => void send(true)}
          >
            <ListChecks aria-hidden="true" />
            {state.step === 'checking' ? t('devices.import.checking') : t('devices.import.check')}
          </Button>
          {state.step !== 'choose' || file ? (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                dispatch({ type: 'reset' });
                setFileInput((value) => value + 1);
              }}
            >
              <RotateCcw aria-hidden="true" />
              {t('devices.import.reset')}
            </Button>
          ) : null}
        </div>

        {state.step === 'failed' ? (
          <Alert
            tone="danger"
            title={
              state.during === 'check'
                ? t('devices.import.checkFailed')
                : t('devices.import.applyFailed')
            }
          >
            {state.error}
          </Alert>
        ) : null}

        {result ? (
          <section
            className="flex flex-col gap-3"
            aria-labelledby={`${id}-result`}
            data-testid="import-result"
          >
            <h3 id={`${id}-result`} className="text-sm font-semibold text-foreground">
              {result.dry_run
                ? t('devices.import.dryRunTitle')
                : written
                  ? t('devices.import.doneTitle')
                  : t('devices.import.refusedTitle')}
            </h3>
            {result.dry_run ? (
              <Alert tone="info" assertive={false}>
                {t('devices.import.dryRunNote')}
              </Alert>
            ) : written ? (
              <Alert tone="success">{t('devices.import.doneNote')}</Alert>
            ) : (
              <Alert tone="danger">{t('devices.import.refusedNote')}</Alert>
            )}
            <ImportCounts result={result} />
            <ImportErrors result={result} />
            <ImportPreview result={result} />
            {!written ? (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  disabled={!applicable && state.step !== 'applying'}
                  busy={state.step === 'applying'}
                  onClick={() => {
                    setConfirmOpen(true);
                  }}
                >
                  <FileUp aria-hidden="true" />
                  {state.step === 'applying' ? t('common.working') : t('devices.import.apply')}
                </Button>
                {!applicable && state.step === 'preview' ? (
                  <span className="text-13 text-muted">
                    {result.errors.length > 0
                      ? t('devices.import.fixFirst')
                      : t('devices.import.nothingToDo')}
                  </span>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}

        <ConfirmDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          title={t('devices.import.confirmTitle')}
          description={
            result
              ? t('devices.import.confirmBody', {
                  created: formatNumber(result.created, 0, lang),
                  updated: formatNumber(result.updated, 0, lang),
                })
              : ''
          }
          confirmLabel={t('devices.import.apply')}
          onConfirm={() => {
            setConfirmOpen(false);
            void send(false);
          }}
        />
      </CardContent>
    </Card>
  );
}

/**
 * "İçe/Dışa aktar": CSV export (`devices.read`) and import (`devices.manage`). The import is the
 * licence module "Toplu içe aktarma (CSV)": while it is off a locked card takes its place (the
 * exports stay; manual entry of persons and assignments always works).
 */
export function TransferTab({
  manage,
  privacyPending,
}: {
  manage: boolean;
  privacyPending: boolean;
}) {
  const license = useLicense();
  if (privacyPending) return <PrivacyWait />;
  const importLocked = license.data !== undefined && !moduleEnabled(license.data, IMPORT_MODULE);
  return (
    <div className="flex flex-col gap-6">
      <ExportCard />
      {manage ? (
        importLocked ? (
          <ModuleLockedCard
            moduleKey={IMPORT_MODULE}
            status={license.data}
            data-testid="import-locked"
          />
        ) : (
          <ImportCard />
        )
      ) : null}
    </div>
  );
}
