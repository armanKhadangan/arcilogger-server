import { Mail, X } from 'lucide-react';
import { useRef } from 'react';
import type { ClipboardEvent, KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

import { addRecipients, removeRecipient } from './recipients-model';

/**
 * E-mail addresses as chips: Enter, comma, semicolon or leaving the field turns the typed address
 * into a chip; a pasted list becomes one chip per valid address; Backspace in the empty field
 * removes the last chip; each chip has its own remove button. An address that is not valid stays
 * in the field and its problem is reported (`onProblem`), so nothing typed is lost.
 */
export function RecipientsInput({
  id,
  recipients,
  pending,
  onChange,
  onProblem,
  invalid,
  describedBy,
}: {
  id: string;
  recipients: string[];
  /** Text in the field that is not a chip yet. */
  pending: string;
  onChange: (next: { recipients: string[]; pending: string }) => void;
  /** An i18n key of the last refused address, `null` once fine. */
  onProblem: (problem: string | null) => void;
  invalid?: boolean;
  describedBy?: string;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);

  function commit(text: string) {
    if (!text.trim()) {
      onChange({ recipients, pending: '' });
      onProblem(null);
      return;
    }
    const result = addRecipients(recipients, text);
    onChange({ recipients: result.recipients, pending: result.rest });
    onProblem(result.problem ?? null);
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',' || event.key === ';') {
      event.preventDefault();
      commit(pending);
    } else if (event.key === 'Backspace' && pending === '' && recipients.length > 0) {
      event.preventDefault();
      onChange({ recipients: recipients.slice(0, -1), pending: '' });
    }
  }

  function paste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData('text');
    if (!/[\s,;]/u.test(text.trim())) return; // one address: typed as usual
    event.preventDefault();
    commit(`${pending} ${text}`);
  }

  return (
    <div
      className={cn(
        'flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-lg border bg-surface px-2 py-1.5 focus-within:ring-2 focus-within:ring-focus',
        invalid ? 'border-danger' : 'border-border',
      )}
      onClick={() => input.current?.focus()}
    >
      {recipients.length > 0 ? (
        <ul className="flex max-w-full flex-wrap gap-1.5" aria-label={t('reports.recipients.list')}>
          {recipients.map((address, index) => (
            <li
              key={address}
              className="inline-flex h-6 max-w-full items-center gap-1 rounded-md bg-accent-soft pr-0.5 pl-2 text-xs font-medium text-accent-ink"
            >
              <Mail aria-hidden="true" className="size-3 shrink-0" />
              <span className="truncate">{address}</span>
              <button
                type="button"
                className="rounded-sm p-0.5 hover:bg-accent/15"
                aria-label={t('reports.recipients.remove', { address })}
                onClick={(event) => {
                  event.stopPropagation();
                  onChange({ recipients: removeRecipient(recipients, index), pending });
                  input.current?.focus();
                }}
              >
                <X aria-hidden="true" className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        ref={input}
        id={id}
        type="text"
        inputMode="email"
        autoComplete="off"
        spellCheck={false}
        className="h-6 min-w-40 flex-1 border-0 bg-transparent px-1 text-sm text-foreground outline-none placeholder:text-muted"
        placeholder={recipients.length === 0 ? t('reports.recipients.placeholder') : undefined}
        value={pending}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy}
        onChange={(event) => {
          onChange({ recipients, pending: event.target.value });
          onProblem(null);
        }}
        onKeyDown={keyDown}
        onPaste={paste}
        onBlur={() => {
          if (pending.trim()) commit(pending);
        }}
      />
    </div>
  );
}
