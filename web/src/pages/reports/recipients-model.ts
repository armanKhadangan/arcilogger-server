/**
 * The recipients of a scheduled report: e-mail addresses typed or pasted into a chip input
 * (no components here; unit-tested in `recipients-model.test.ts`). The server checks them again.
 */

import { codePointLength, EMAIL_MAX, EMAIL_PATTERN } from '@/lib/validation';

/** Most addresses one schedule may have (a report is not a mailing list). */
export const RECIPIENTS_MAX = 50;

/** Control, format (bidi, zero-width) and surrogate characters. */
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;

/** Addresses in a typed or pasted text: separated by commas, semicolons, spaces or lines. */
export function splitAddresses(text: string): string[] {
  return text
    .split(/[\s,;]+/u)
    .map((part) => part.replace(/^<|>$/g, '').trim())
    .filter(Boolean);
}

/** Same mailbox (the domain is case-insensitive; local parts are compared alike in practice). */
export function sameAddress(a: string, b: string): boolean {
  return a.toLocaleLowerCase('en') === b.toLocaleLowerCase('en');
}

/** Problem of one address as an i18n key, `undefined` when it may be added. */
export function addressProblem(address: string, existing: readonly string[]): string | undefined {
  if (codePointLength(address) > EMAIL_MAX) return 'validation.tooLong';
  if (FORBIDDEN.test(address) || !EMAIL_PATTERN.test(address)) return 'validation.email';
  if (existing.some((item) => sameAddress(item, address))) return 'reports.recipients.duplicate';
  return undefined;
}

export interface AddResult {
  recipients: string[];
  /** The text that could not be added (left in the input for correction), `''` when none. */
  rest: string;
  /** Why the first rejected address was refused (an i18n key). */
  problem?: string;
}

/**
 * Add every valid address of `text` to `current`; the invalid ones stay in `rest` with the first
 * problem. Nothing beyond {@link RECIPIENTS_MAX}.
 */
export function addRecipients(current: readonly string[], text: string): AddResult {
  const recipients = [...current];
  const rejected: string[] = [];
  let problem: string | undefined;
  const addresses = splitAddresses(text);
  for (const address of addresses) {
    let found = addressProblem(address, recipients);
    if (!found && recipients.length >= RECIPIENTS_MAX) found = 'reports.recipients.tooMany';
    // A pasted list drops repeated addresses quietly; one typed address is told it is there.
    if (found === 'reports.recipients.duplicate' && addresses.length > 1) continue;
    if (found) {
      rejected.push(address);
      problem ??= found;
      continue;
    }
    recipients.push(address);
  }
  return { recipients, rest: rejected.join(', '), ...(problem ? { problem } : {}) };
}

/** `recipients` without the address at `index`. */
export function removeRecipient(recipients: readonly string[], index: number): string[] {
  return recipients.filter((_, position) => position !== index);
}
