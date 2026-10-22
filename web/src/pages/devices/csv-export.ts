/**
 * CSV export of the device and person lists (`GET /devices/export.csv`, `/persons/export.csv`;
 * `devices.read`, audited by the server with the row count and which filters were used).
 *
 * The exports take the lists' own filters: "İçe/Dışa aktar" downloads everything, the list tabs
 * download what they show (all pages of it). Paging is never sent — an export is the whole list.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { toast } from '@/components/ui/toast';
import { apiDownload, errorMessage, type QueryValue } from '@/lib/api';
import type { DeviceFilters, PersonFilters } from '@/lib/api-types';
import { siteToday } from '@/lib/display';
import { saveBlob } from '@/lib/download';
import { isReauthCancelled, useReauth } from '@/lib/reauth';

export type ExportList = 'devices' | 'persons';

const PATHS: Record<ExportList, string> = {
  devices: '/devices/export.csv',
  persons: '/persons/export.csv',
};

/** File names when the server sends none (it names them `arcilogger-cihazlar-<time>.csv`). */
const FALLBACK: Record<ExportList, string> = {
  devices: 'arcilogger-cihazlar',
  persons: 'arcilogger-kisiler',
};

/** The filters of a list without its paging (an export holds every matching row). */
export function exportParams(
  filters: DeviceFilters | PersonFilters | undefined,
): Record<string, QueryValue> | undefined {
  if (!filters) return undefined;
  const params: Record<string, QueryValue> = { ...filters };
  delete params.limit;
  delete params.offset;
  return Object.keys(params).length > 0 ? params : undefined;
}

/** Downloads one export (a failure is a toast); `busy` names the list being downloaded. */
export function useCsvExport() {
  const { t } = useTranslation();
  const { withReauth } = useReauth();
  const [busy, setBusy] = useState<ExportList | null>(null);

  async function download(list: ExportList, filters?: DeviceFilters | PersonFilters) {
    setBusy(list);
    try {
      const params = exportParams(filters);
      const file = await withReauth(() =>
        apiDownload(PATHS[list], {
          ...(params ? { params } : {}),
          fallbackName: `${FALLBACK[list]}-${siteToday()}.csv`,
        }),
      );
      saveBlob(file.blob, file.filename);
      toast({ title: t('devices.export.done', { file: file.filename }), variant: 'success' });
    } catch (failure) {
      if (!isReauthCancelled(failure))
        toast({
          title: t('devices.export.failed'),
          description: errorMessage(failure),
          variant: 'danger',
        });
    } finally {
      setBusy(null);
    }
  }

  return { busy, download };
}
