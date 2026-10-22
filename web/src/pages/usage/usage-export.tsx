import { Download, FileSpreadsheet, FileText } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { toast } from '@/components/ui/toast';
import { apiDownload, errorMessage, type QueryValue } from '@/lib/api';
import type { UsageParams } from '@/lib/api-types';
import { saveBlob } from '@/lib/download';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

import { exportParams, type ExportView } from './usage-model';

type Format = 'csv' | 'xlsx';

/**
 * "Dışa aktar" of a usage table: `GET /usage/export.csv|xlsx?view=…` with the view's own
 * parameters and the period — the same rows as the view, audited by the server (`usage.export`).
 * The CSV holds only the view's main table, the Excel file one sheet per section of the view and
 * the method note ("Yöntem"); a table that is not the main one offers only the Excel file.
 */
export function ExportMenu({
  view,
  params,
  extra,
  disabled = false,
  label,
  csv = true,
}: {
  view: ExportView;
  params: UsageParams;
  /** The view's own parameters (`kind`, `key`, `domain`, `category`, `q`, …). */
  extra?: Record<string, QueryValue>;
  disabled?: boolean;
  /** What is exported, for the button's accessible name (e.g. "Siteler"). */
  label?: string;
  /** The table is the view's main table (its CSV is this table). */
  csv?: boolean;
}) {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const [busy, setBusy] = useState(false);

  async function download(format: Format) {
    setBusy(true);
    try {
      const query = exportParams(view, params, extra);
      // The API names its files the same way (`…-company-20261012-20261012.csv`).
      const fallbackName = `arcilogger-kullanim-${view}-${params.from.replace(/-/g, '')}-${params.to.replace(/-/g, '')}.${format}`;
      const file = await withReauth(() =>
        format === 'xlsx'
          ? apiDownload('/usage/export.xlsx', { params: query, fallbackName })
          : apiDownload('/usage/export.csv', { params: query, fallbackName }),
      );
      saveBlob(file.blob, file.filename);
      toast({ title: t('usage.export.done', { file: file.filename }), variant: 'success' });
    } catch (failure) {
      if (!isReauthCancelled(failure))
        toast({
          title: t('usage.export.failed'),
          description: errorMessage(failure),
          variant: 'danger',
        });
    } finally {
      setBusy(false);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="secondary"
          size="sm"
          busy={busy}
          disabled={disabled}
          aria-label={label ? t('usage.export.labelOf', { what: label }) : t('usage.export.label')}
        >
          <Download aria-hidden="true" />
          <span aria-hidden="true">{t('usage.export.label')}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel className="max-w-72 font-normal text-muted-strong">
          {t(csv ? 'usage.export.formats' : 'usage.export.sheetOnly')}
        </DropdownMenuLabel>
        {csv ? (
          <DropdownMenuItem onSelect={() => void download('csv')}>
            <FileText aria-hidden="true" />
            {t('usage.export.csv')}
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem onSelect={() => void download('xlsx')}>
          <FileSpreadsheet aria-hidden="true" />
          {t('usage.export.xlsx')}
        </DropdownMenuItem>
        <DropdownMenuLabel className="max-w-72 font-normal text-muted-strong">
          {t('usage.export.hint')}
        </DropdownMenuLabel>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
