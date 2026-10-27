import { Bookmark, BookmarkPlus, ChevronDown, Pencil, Settings2, Trash2 } from 'lucide-react';
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Field } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import {
  useCreateSavedSearch,
  useDeleteSavedSearch,
  useSavedSearches,
  useUpdateSavedSearch,
} from '@/lib/search-hooks';
import type { SavedSearch } from '@/lib/search-types';
import { rangeLabel, type TimeRange } from '@/lib/time-range';

import { SAVED_NAME_MAX, savedNameProblem } from './search-model';

/** "Bu aramayı kaydet" / "Yeniden adlandır": a name, and what is saved with it. */
function NameDialog({
  open,
  title,
  description,
  initial,
  submitLabel,
  busy,
  error,
  onSubmit,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  initial: string;
  submitLabel: string;
  busy: boolean;
  error: unknown;
  onSubmit: (name: string) => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const [shownFor, setShownFor] = useState(open);
  if (open !== shownFor) {
    // Opened again: start from the initial name.
    setShownFor(open);
    if (open) {
      setName(initial);
      setProblem(null);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            const found = savedNameProblem(name);
            setProblem(found);
            if (found) {
              document.getElementById(`${id}-name`)?.focus();
              return;
            }
            onSubmit(name.trim());
          }}
        >
          <Field
            id={`${id}-name`}
            label={t('search.saved.name')}
            error={problem ? t(problem, { max: SAVED_NAME_MAX }) : undefined}
            required
          >
            <Input
              id={`${id}-name`}
              value={name}
              autoComplete="off"
              maxLength={SAVED_NAME_MAX}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-name-error` : undefined}
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </Field>
          {children}
          {error ? <Alert tone="danger">{errorMessage(error)}</Alert> : null}
          <DialogFooter>
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" busy={busy}>
              {busy ? t('common.working') : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** "Kayıtlı aramaları yönet": rename or delete one's own saved searches. */
function ManageDialog({
  open,
  items,
  onClose,
}: {
  open: boolean;
  items: SavedSearch[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const update = useUpdateSavedSearch();
  const remove = useDeleteSavedSearch();
  const [renaming, setRenaming] = useState<SavedSearch | null>(null);
  const [deleting, setDeleting] = useState<SavedSearch | null>(null);

  return (
    <>
      <Dialog
        open={open && renaming === null && deleting === null}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('search.saved.manageTitle')}</DialogTitle>
            <DialogDescription>{t('search.saved.manageDescription')}</DialogDescription>
          </DialogHeader>
          {items.length === 0 ? (
            <p className="text-13 text-muted">{t('search.saved.none')}</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border">
              {items.map((item) => (
                <li key={item.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-13 font-medium text-foreground">
                      {item.name}
                    </span>
                    <span className="truncate font-mono text-xs text-muted-strong">
                      {item.q || '—'}
                    </span>
                    <span className="text-xs text-muted">{rangeLabel(item.range, t)}</span>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={t('search.saved.renameNamed', { name: item.name })}
                    onClick={() => {
                      update.reset();
                      setRenaming(item);
                    }}
                  >
                    <Pencil aria-hidden="true" />
                    {t('search.saved.rename')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={t('search.saved.deleteNamed', { name: item.name })}
                    onClick={() => {
                      remove.reset();
                      setDeleting(item);
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                    {t('search.saved.delete')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      </Dialog>
      <NameDialog
        open={renaming !== null}
        title={t('search.saved.renameTitle')}
        description={renaming ? t('search.saved.renameDescription', { name: renaming.name }) : ''}
        initial={renaming?.name ?? ''}
        submitLabel={t('common.save')}
        busy={update.isPending}
        error={update.error}
        onClose={() => {
          setRenaming(null);
        }}
        onSubmit={(name) => {
          if (!renaming) return;
          update.mutate(
            { id: renaming.id, body: { name, q: renaming.q, range: renaming.range } },
            {
              onSuccess: () => {
                setRenaming(null);
              },
            },
          );
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        title={t('search.saved.deleteTitle')}
        description={deleting ? t('search.saved.deleteDescription', { name: deleting.name }) : ''}
        confirmLabel={t('search.saved.delete')}
        danger
        busy={remove.isPending}
        error={remove.error ? errorMessage(remove.error) : null}
        onConfirm={() => {
          if (!deleting) return;
          remove.mutate(deleting.id, {
            onSuccess: () => {
              setDeleting(null);
            },
          });
        }}
      />
    </>
  );
}

/**
 * "Kayıtlı aramalar" (personal, owner decision D10): run one (`POST /search` with its id), save
 * the current query with the current time range (a preset is resolved again at every run), and
 * rename or delete them.
 */
export function SavedMenu({
  query,
  range,
  onRun,
  enabled = true,
}: {
  /** The query to save (`null`: nothing ran yet). */
  query: string | null;
  range: TimeRange;
  onRun: (saved: SavedSearch) => void;
  /** Read the list (not while the KVKK notice waits: it is personal). */
  enabled?: boolean;
}) {
  const { t } = useTranslation();
  const saved = useSavedSearches(enabled);
  const create = useCreateSavedSearch();
  const [saving, setSaving] = useState(false);
  const [managing, setManaging] = useState(false);
  const items = saved.data?.items ?? [];

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="secondary">
            <Bookmark aria-hidden="true" />
            {t('search.saved.menu')}
            <ChevronDown aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel>{t('search.saved.menu')}</DropdownMenuLabel>
          {saved.isError ? (
            <p className="px-2 py-1.5 text-13 text-danger-ink">{errorMessage(saved.error)}</p>
          ) : items.length === 0 ? (
            <p className="px-2 py-1.5 text-13 text-muted">
              {saved.isPending ? t('common.loading') : t('search.saved.none')}
            </p>
          ) : (
            items.map((item) => (
              <DropdownMenuItem
                key={item.id}
                onSelect={() => {
                  onRun(item);
                }}
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate">{item.name}</span>
                  <span className="truncate text-xs text-muted">{rangeLabel(item.range, t)}</span>
                </span>
              </DropdownMenuItem>
            ))
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={query === null}
            onSelect={() => {
              create.reset();
              setSaving(true);
            }}
          >
            <BookmarkPlus aria-hidden="true" />
            {t('search.saved.save')}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setManaging(true);
            }}
          >
            <Settings2 aria-hidden="true" />
            {t('search.saved.manage')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <NameDialog
        open={saving}
        title={t('search.saved.saveTitle')}
        description={t('search.saved.saveDescription')}
        initial=""
        submitLabel={t('common.save')}
        busy={create.isPending}
        error={create.error}
        onClose={() => {
          setSaving(false);
        }}
        onSubmit={(name) => {
          create.mutate(
            { name, q: query ?? '', range },
            {
              onSuccess: () => {
                setSaving(false);
              },
            },
          );
        }}
      >
        <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1 text-13">
          <dt className="text-muted">{t('search.saved.query')}</dt>
          <dd className="font-mono text-xs break-all text-foreground">{query || '—'}</dd>
          <dt className="text-muted">{t('search.saved.range')}</dt>
          <dd className="text-foreground">
            {rangeLabel(range, t)}
            <span className="block text-xs text-muted">
              {'preset' in range ? t('search.saved.presetNote') : t('search.saved.fixedNote')}
            </span>
          </dd>
        </dl>
      </NameDialog>
      <ManageDialog
        open={managing}
        items={items}
        onClose={() => {
          setManaging(false);
        }}
      />
    </>
  );
}
