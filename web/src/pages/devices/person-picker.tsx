import { Command } from 'cmdk';
import { Check, ChevronsUpDown, Search } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { errorMessage } from '@/lib/api';
import { PERSON_PICKER_SIZE, usePersons } from '@/lib/api-hooks';
import type { PersonRef } from '@/lib/api-types';
import { visibleText } from '@/lib/display';
import { cn } from '@/lib/utils';

import { useDebounced } from './device-text';

/**
 * Searchable person picker: the server searches (`GET /persons?q=…&active=true`), so it works
 * with any number of persons; only active persons are offered for a new assignment.
 */
export function PersonPicker({
  id,
  value,
  onChange,
  invalid,
  describedBy,
}: {
  id: string;
  value: PersonRef | null;
  onChange: (person: PersonRef | null) => void;
  invalid?: boolean;
  describedBy?: string | undefined;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const q = useDebounced(search, 250);
  const persons = usePersons(
    { q: q.trim() || undefined, active: true, limit: PERSON_PICKER_SIZE },
    open,
  );
  const items = persons.data?.items ?? [];
  const more = (persons.data?.total ?? 0) > items.length;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setSearch('');
      }}
    >
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy}
          className={cn(
            'flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 text-left text-sm aria-invalid:border-danger',
            !value && 'text-muted',
          )}
        >
          <span className="truncate">
            {value ? visibleText(value.name) : t('devices.assign.choosePerson')}
          </span>
          <ChevronsUpDown aria-hidden="true" className="size-4 shrink-0 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] min-w-72 p-0">
        <Command label={t('devices.assign.person')} shouldFilter={false} className="flex flex-col">
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search aria-hidden="true" className="size-4 text-muted" />
            <Command.Input
              value={search}
              onValueChange={setSearch}
              placeholder={t('devices.assign.searchPerson')}
              className="h-9 w-full bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
          <Command.List className="max-h-64 overflow-y-auto p-1">
            {persons.isError ? (
              <p className="px-2 py-4 text-13 text-danger-ink">{errorMessage(persons.error)}</p>
            ) : persons.isPending ? (
              <Command.Loading>
                <p className="px-2 py-4 text-13 text-muted">{t('common.loading')}</p>
              </Command.Loading>
            ) : (
              <Command.Empty className="px-2 py-6 text-center text-13 text-muted">
                {t('devices.assign.noPerson')}
              </Command.Empty>
            )}
            {items.map((person) => (
              <Command.Item
                key={person.id}
                value={String(person.id)}
                onSelect={() => {
                  onChange({ id: person.id, name: person.name });
                  setOpen(false);
                  setSearch('');
                }}
                className="flex min-h-8 cursor-default items-center gap-2 rounded-md px-2 py-1 text-13 select-none data-[selected=true]:bg-subtle"
              >
                <Check
                  aria-hidden="true"
                  className={cn(
                    'size-4 shrink-0 text-primary',
                    value?.id === person.id ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="truncate">{visibleText(person.name)}</span>
                  {person.department || person.employee_no ? (
                    <span className="truncate text-xs text-muted-strong">
                      {[
                        person.department ? visibleText(person.department.name) : null,
                        person.employee_no
                          ? t('devices.persons.employeeNoShort', {
                              number: visibleText(person.employee_no),
                            })
                          : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  ) : null}
                </span>
              </Command.Item>
            ))}
            {more ? (
              <p className="px-2 py-1.5 text-xs text-muted">{t('devices.assign.morePersons')}</p>
            ) : null}
          </Command.List>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
