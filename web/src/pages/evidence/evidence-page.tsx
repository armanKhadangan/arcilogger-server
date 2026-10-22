import { KeyRound, PackagePlus } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { PageHeader } from '@/components/layout/page-header';
import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { DataTable, type AnyColumnDef } from '@/components/ui/data-table';
import { toast } from '@/components/ui/toast';
import { errorMessage } from '@/lib/api';
import { EVIDENCE_LIST_SIZE, useEvidencePackages } from '@/lib/api-hooks';
import type { EvidenceOut } from '@/lib/api-types';
import { parseId } from '@/lib/display';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/format';

import { EvidenceDetailSheet } from './evidence-detail-sheet';
import { count, packageSize, scopeText } from './evidence-format';
import { shownStatus } from './evidence-model';
import { PackageStatus } from './evidence-status';
import { EvidenceWizard } from './evidence-wizard';
import { usePackageDownload } from './use-package-download';

/**
 * Kanıt Paketleri (spec 9.6, 12.3 page 10; `evidence.read`): every package with who made it,
 * when, its scope, size, state (refreshed while one is being built), downloads and expiry; the
 * detail drawer; the create wizard (`evidence.create`, administrators) and the download
 * (`evidence.download`, re-authentication). `/evidence?lookup=17` opens the wizard for lookup 17.
 *
 * Focus (WCAG 2.4.3): a dialog opened without an opener on this page — the wizard from the lookup
 * page's "Kanıt Paketi", the new package's drawer that replaces the wizard — gives the focus back
 * to "Yeni paket" when it closes; a drawer opened from a row gives it back to that row.
 */
export function EvidencePage() {
  const { t } = useTranslation();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const packages = useEvidencePackages();
  const [selected, setSelected] = useState<number | null>(null);
  const [wizard, setWizard] = useState<{ lookupId: number | null } | null>(null);
  const { state: download, download: startDownload } = usePackageDownload();
  const rows = useMemo(() => packages.data?.items ?? [], [packages.data]);
  const mayCreate = can('evidence.create');
  const createButton = useRef<HTMLButtonElement>(null);
  /** Focus target of the drawer when its opener is gone (`null`: the row that opened it). */
  const sheetReturn = useRef<HTMLElement | null>(null);

  // "Kanıt Paketi" on the lookup page: open the wizard once, then forget the parameter.
  const fromLookup = parseId(params.get('lookup'));
  useEffect(() => {
    if (fromLookup !== null && mayCreate) setWizard({ lookupId: fromLookup });
    if (params.has('lookup')) setParams({}, { replace: true });
  }, [fromLookup, mayCreate, params, setParams]);

  const columns = useMemo<AnyColumnDef<EvidenceOut>[]>(
    () => [
      {
        id: 'name',
        accessorFn: (row) => row.package_name ?? `#${row.id}`,
        header: t('evidence.columns.package'),
        size: 220,
        meta: { label: t('evidence.columns.package'), mono: true },
      },
      {
        id: 'created_by',
        accessorKey: 'created_by',
        header: t('evidence.columns.createdBy'),
        size: 100,
        meta: { label: t('evidence.columns.createdBy') },
      },
      {
        id: 'created_at',
        accessorKey: 'created_at',
        header: t('evidence.columns.createdAt'),
        size: 160,
        meta: { label: t('evidence.columns.createdAt') },
        cell: (info) => <span className="tabular">{formatDateTime(info.getValue<string>())}</span>,
      },
      {
        id: 'scope',
        accessorFn: (row) => scopeText(row, t),
        header: t('evidence.columns.scope'),
        // Takes what the other columns leave (spec 12.3 page 10: all eight columns on screen
        // from 1440 px, or 1280 px with the menu collapsed); the full text is in its tooltip
        // and in the detail drawer. Its minimum keeps the table (1 005 px of fixed columns +
        // 110) inside the 1 150 px the page has at 1440 px even when the list scrolls and
        // Windows draws a classic vertical scrollbar (about 15-17 px): e2e/evidence-columns.spec.ts.
        size: 110,
        minSize: 110,
        meta: { label: t('evidence.columns.scope'), grow: true },
        cell: (info) => <span title={info.getValue<string>()}>{info.getValue<string>()}</span>,
      },
      {
        id: 'size',
        accessorFn: (row) => row.size ?? row.scope.estimated_bytes ?? 0,
        header: t('evidence.columns.size'),
        size: 125,
        meta: { label: t('evidence.columns.size'), numeric: true },
        cell: (info) => packageSize(info.row.original, t),
      },
      {
        id: 'status',
        accessorFn: (row) => shownStatus(row),
        header: t('evidence.columns.status'),
        size: 150,
        meta: { label: t('evidence.columns.status') },
        cell: (info) => (
          <span className="flex items-center gap-1.5">
            <PackageStatus item={info.row.original} />
            {info.row.original.password_protected ? (
              <KeyRound
                aria-label={t('evidence.passwordProtected')}
                role="img"
                className="size-3.5 text-muted-strong"
              />
            ) : null}
          </span>
        ),
      },
      {
        id: 'downloads',
        accessorKey: 'download_count',
        header: t('evidence.columns.downloads'),
        size: 90,
        meta: { label: t('evidence.columns.downloads'), numeric: true },
        cell: (info) => count(info.getValue<number>()),
      },
      {
        id: 'expires_at',
        accessorKey: 'expires_at',
        header: t('evidence.columns.expiresAt'),
        size: 160,
        meta: { label: t('evidence.columns.expiresAt') },
        cell: (info) => <span className="tabular">{formatDateTime(info.getValue<string>())}</span>,
      },
    ],
    [t],
  );

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t('nav.evidence')}
        description={t('evidence.description')}
        actions={
          mayCreate ? (
            <Button
              ref={createButton}
              onClick={() => {
                setWizard({ lookupId: null });
              }}
            >
              <PackagePlus aria-hidden="true" />
              {t('evidence.create')}
            </Button>
          ) : undefined
        }
      />
      {mayCreate ? null : (
        <Alert tone="info" assertive={false}>
          {t('evidence.readOnly')}
        </Alert>
      )}
      {packages.isError ? <Alert tone="danger">{errorMessage(packages.error)}</Alert> : null}
      <DataTable
        data={rows}
        columns={columns}
        caption={t('evidence.caption')}
        getRowId={(row) => String(row.id)}
        onRowClick={(row) => {
          sheetReturn.current = null;
          setSelected(row.id);
        }}
        height={480}
        emptyMessage={packages.isPending ? t('common.loading') : t('evidence.empty')}
      />
      {packages.data?.next_cursor ? (
        <p className="text-xs text-muted">
          {t('evidence.newestOnly', { count: EVIDENCE_LIST_SIZE })}
        </p>
      ) : null}
      <EvidenceDetailSheet
        packageId={selected}
        onClose={() => {
          setSelected(null);
        }}
        download={download}
        onDownload={(item) => void startDownload(item)}
        returnFocus={sheetReturn}
      />
      {mayCreate ? (
        <EvidenceWizard
          open={wizard !== null}
          lookupId={wizard?.lookupId ?? null}
          returnFocus={createButton}
          onClose={() => {
            setWizard(null);
          }}
          onCreated={(packageId) => {
            setWizard(null);
            toast({ title: t('evidence.requested'), variant: 'success' });
            // The wizard's buttons are gone when the drawer closes: back to "Yeni paket".
            sheetReturn.current = createButton.current;
            setSelected(packageId);
          }}
        />
      ) : null}
    </div>
  );
}
