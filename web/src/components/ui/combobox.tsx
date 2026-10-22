import { Command } from 'cmdk';
import { Check, ChevronsUpDown, Search } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { Popover, PopoverContent, PopoverTrigger } from './popover';

export interface ComboboxOption {
  value: string;
  label: string;
  /** Secondary text, e.g. MAC address or VLAN. */
  hint?: string;
}

interface ComboboxProps {
  id?: string;
  options: ComboboxOption[];
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  className?: string;
  'aria-label'?: string;
}

/** Searchable select (persons, devices, sources). */
export function Combobox({
  id,
  options,
  value,
  onChange,
  placeholder,
  searchPlaceholder,
  className,
  'aria-label': ariaLabel,
}: ComboboxProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          id={id}
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          className={cn(
            'flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 text-left text-sm',
            !selected && 'text-muted',
            className,
          )}
        >
          <span className="truncate">
            {selected?.label ?? placeholder ?? t('common.selectPlaceholder')}
          </span>
          <ChevronsUpDown aria-hidden="true" className="size-4 shrink-0 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] min-w-64 p-0">
        <Command
          label={ariaLabel ?? placeholder ?? t('common.selectPlaceholder')}
          className="flex flex-col"
        >
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search aria-hidden="true" className="size-4 text-muted" />
            <Command.Input
              placeholder={searchPlaceholder ?? t('common.searchPlaceholder')}
              className="h-9 w-full bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
          <Command.List className="max-h-64 overflow-y-auto p-1">
            <Command.Empty className="px-2 py-6 text-center text-13 text-muted">
              {t('common.noOptions')}
            </Command.Empty>
            {options.map((option) => (
              <Command.Item
                key={option.value}
                value={`${option.label} ${option.hint ?? ''} ${option.value}`}
                onSelect={() => {
                  onChange(option.value === value ? null : option.value);
                  setOpen(false);
                }}
                className="flex h-8 cursor-default items-center gap-2 rounded-md px-2 text-13 select-none data-[selected=true]:bg-subtle"
              >
                <Check
                  aria-hidden="true"
                  className={cn(
                    'size-4 text-primary',
                    option.value === value ? 'opacity-100' : 'opacity-0',
                  )}
                />
                <span className="truncate">{option.label}</span>
                {option.hint ? (
                  <span className="ml-auto font-mono text-xs text-muted">{option.hint}</span>
                ) : null}
              </Command.Item>
            ))}
          </Command.List>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
