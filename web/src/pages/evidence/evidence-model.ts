/**
 * The evidence package wizard: its values, the browser-side checks (mirroring
 * `arcilogger_api.routers.evidence.EvidenceCreate` and the scope rules of the API) and the
 * `POST /evidence` body; plus display helpers for package rows. Validators return i18n keys.
 */

import type { EvidenceCreate, EvidenceOut, RawLine, Scope } from '@/lib/api-types';
import { dayText, parseDay } from '@/lib/dates';
import type { HandoffPayloads } from '@/lib/handoff';
import { codePointLength, type FieldErrors } from '@/lib/validation';

/** Most days in one package (`routers.evidence.MAX_DAYS`). */
export const MAX_DAYS = 31;
/** Most raw lines in one extract (`routers.evidence.MAX_EXTRACT_LINES`). */
export const MAX_EXTRACT_LINES = 10_000;
export const REFERENCE_MAX = 200;
/** Spool folder / source names (`arcilogger_core.evidence.model.SOURCE_NAME`). */
export const SOURCE_NAME = /^[a-z0-9][a-z0-9.-]{0,80}$/;
const CONTROL = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const MAX_LINE = 2 ** 31 - 1;

export type ScopeKind = 'lookup' | 'range';
export type Content = 'full_days' | 'extract';

export interface WizardDraft {
  scope: ScopeKind;
  /** Selected 5651 lookup (decimal id), `''` when none. */
  lookupId: string;
  from: string;
  to: string;
  /**
   * Full days of every source (default), or only of `sources` — for a lookup, plus the sources of
   * the lines its result cites (the API adds them: the extract is checked against their files).
   */
  allSources: boolean;
  /** Source names separated by commas or spaces. */
  sources: string;
  content: Content;
  /** Raw lines for an extract of a day range, one per line: `2026-10-09 fortigate 17`. */
  lines: string;
  password: boolean;
  reference: string;
  /**
   * Add the days' ARCILOGGER audit log (user names, IP addresses, lookups made) — off by default
   * (KVKK data minimisation), administrators only, for internal audits.
   */
  includeAuditLog: boolean;
}

export type WizardField = keyof WizardDraft;

/**
 * The `POST /evidence` body. `include_audit_log` is sent only when chosen and only while the API
 * accepts it (`evidenceAuditLogAccepted`, `@/lib/api-features`); it is not in `EvidenceCreate`
 * (api-types.ts, checked against the API's OpenAPI document) until the API's `EvidenceCreate`
 * has it — then this alias is just `EvidenceCreate` and can go.
 */
export type EvidenceRequest = EvidenceCreate & { include_audit_log?: boolean };

export function emptyWizard(lookupId: number | null = null): WizardDraft {
  return {
    scope: lookupId === null ? 'range' : 'lookup',
    lookupId: lookupId === null ? '' : String(lookupId),
    from: '',
    to: '',
    allSources: true,
    sources: '',
    content: 'full_days',
    lines: '',
    password: false,
    reference: '',
    includeAuditLog: false,
  };
}

/** Days from `first` to `last` inclusive (both `YYYY-MM-DD`). */
export function daySpan(first: string, last: string): number {
  return (
    Math.round((Date.parse(`${last}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`)) / 86_400_000) +
    1
  );
}

export function sourceList(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[\s,;]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

/**
 * `2026-10-09 fortigate 17` per line (also `fortigate 2026-10-09 17`); returns the lines and the
 * 1-based numbers of the text lines that could not be read.
 */
export function parseLines(text: string): { lines: RawLine[]; bad: number[] } {
  const lines: RawLine[] = [];
  const bad: number[] = [];
  const seen = new Set<string>();
  text.split(/\r?\n/).forEach((row, index) => {
    const parts = row
      .trim()
      .split(/[\s,;]+/)
      .filter(Boolean);
    if (parts.length === 0) return;
    const dayPart = parts.find((part) => parseDay(part) !== null);
    const day = dayPart === undefined ? null : parseDay(dayPart);
    const number = parts.find((part) => /^\d+$/.test(part));
    const source = parts.find((part) => part !== dayPart && part !== number);
    if (
      parts.length !== 3 ||
      !day ||
      !number ||
      !source ||
      !SOURCE_NAME.test(source) ||
      Number(number) < 1 ||
      Number(number) > MAX_LINE
    ) {
      bad.push(index + 1);
      return;
    }
    const key = `${day}|${source}|${number}`;
    if (seen.has(key)) return;
    seen.add(key);
    lines.push({ day, source, line: Number(number) });
  });
  return { lines, bad };
}

/** The extract as the line box shows it: `2026-10-09 fortigate 17`, one line each. */
export function extractText(lines: readonly RawLine[]): string {
  return lines.map((line) => `${line.day} ${line.source} ${line.line}`).join('\n');
}

/** What a search handed over ("Kanıt paketine aktar", Wave 2 contract 2.13). */
export type SearchExtract = HandoffPayloads['/evidence'];

/**
 * The wizard filled with a search's raw lines: scope = their days (first to last), content = an
 * extract of exactly these lines, still editable and checked by {@link parseLines} like typed
 * ones.
 */
export function wizardFromSearch(extract: SearchExtract): WizardDraft {
  return {
    ...emptyWizard(),
    scope: 'range',
    from: dayText(extract.from_day),
    to: dayText(extract.to_day),
    content: 'extract',
    lines: extractText(extract.extract),
  };
}

/**
 * The extract without the lines of its unsealed days (a package cannot hold them), the scope
 * narrowed to the days of the lines left; `null` when no line is on a sealed day. A search of the
 * last 24 hours almost always reaches today, which is never sealed yet.
 */
export function leaveOutUnsealed(
  extract: SearchExtract,
): { extract: SearchExtract; leftOut: number } | null {
  const unsealed = new Set(extract.unsealed_days);
  const kept = extract.extract.filter((line) => !unsealed.has(line.day));
  const days = kept.map((line) => line.day).sort();
  const first = days[0];
  const last = days.at(-1);
  if (first === undefined || last === undefined) return null;
  return {
    extract: { ...extract, extract: kept, from_day: first, to_day: last, unsealed_days: [] },
    leftOut: extract.extract.length - kept.length,
  };
}

/** Problems of the scope step (`today`: the site day, `YYYY-MM-DD`). */
export function validateScope(draft: WizardDraft, today: string): FieldErrors<WizardField> {
  const errors: FieldErrors<WizardField> = {};
  if (draft.scope === 'lookup') {
    if (!/^[1-9]\d*$/.test(draft.lookupId)) errors.lookupId = 'evidence.validation.lookup';
  } else {
    const from = parseDay(draft.from);
    const to = parseDay(draft.to);
    if (!draft.from.trim()) errors.from = 'validation.required';
    else if (from === null) errors.from = 'evidence.validation.date';
    else if (from >= today) errors.from = 'evidence.validation.notClosed';
    if (!draft.to.trim()) errors.to = 'validation.required';
    else if (to === null) errors.to = 'evidence.validation.date';
    else if (to >= today) errors.to = 'evidence.validation.notClosed';
    if (from !== null && to !== null && !errors.from && !errors.to) {
      if (to < from) errors.to = 'evidence.validation.order';
      else if (daySpan(from, to) > MAX_DAYS) errors.to = 'evidence.validation.tooManyDays';
    }
  }
  if (!draft.allSources) {
    const sources = sourceList(draft.sources);
    if (sources.length === 0) errors.sources = 'evidence.validation.sourcesRequired';
    else if (!sources.every((source) => SOURCE_NAME.test(source)))
      errors.sources = 'evidence.validation.sourceName';
  }
  const reference = draft.reference.trim();
  if (reference && codePointLength(reference) > REFERENCE_MAX)
    errors.reference = 'validation.tooLong';
  else if (reference && CONTROL.test(reference)) errors.reference = 'validation.controlCharacters';
  return errors;
}

/** Problems of the content step (an extract of a day range needs its raw lines). */
export function validateContent(draft: WizardDraft): FieldErrors<WizardField> {
  if (draft.scope !== 'range' || draft.content !== 'extract') return {};
  const { lines, bad } = parseLines(draft.lines);
  if (bad.length > 0) return { lines: 'evidence.validation.linesFormat' };
  if (lines.length === 0) return { lines: 'evidence.validation.linesRequired' };
  if (lines.length > MAX_EXTRACT_LINES) return { lines: 'evidence.validation.tooManyLines' };
  const from = parseDay(draft.from) ?? '';
  const to = parseDay(draft.to) ?? '';
  if (lines.some((line) => line.day < from || line.day > to))
    return { lines: 'evidence.validation.lineOutside' };
  return {};
}

/** The sources a full-day package is narrowed to; `null`: every source of the days. */
function chosenSources(draft: WizardDraft): string[] | null {
  // An extract takes its sources from its lines (the API refuses `sources` with it).
  return draft.content === 'extract' || draft.allSources ? null : sourceList(draft.sources);
}

/**
 * The `POST /evidence` body of a valid draft. `auditLogAllowed`: the API accepts
 * `include_audit_log` and the user is an administrator (the wizard decides); without it a ticked
 * `includeAuditLog` is never sent — the API would refuse the whole request.
 */
export function toEvidenceBody(draft: WizardDraft, auditLogAllowed = false): EvidenceRequest {
  const reference = draft.reference.trim() || null;
  const sources = chosenSources(draft);
  const auditLog = auditLogAllowed && draft.includeAuditLog ? { include_audit_log: true } : {};
  if (draft.scope === 'lookup') {
    return {
      lookup_id: Number(draft.lookupId),
      ...(sources ? { sources } : {}),
      content: draft.content,
      password: draft.password,
      reference,
      ...auditLog,
    };
  }
  const extract = draft.content === 'extract' ? parseLines(draft.lines).lines : [];
  return {
    from: parseDay(draft.from) ?? draft.from,
    to: parseDay(draft.to) ?? draft.to,
    sources,
    extract,
    content: draft.content,
    password: draft.password,
    reference,
    ...auditLog,
  };
}

/**
 * The "Kaynaklar" line of the wizard's summary: what the package will really hold, by the API's
 * scope rules (docs/KANIT-PAKETI.md section 2). A full-day package of a lookup holds the sealed
 * files of **every** source of its days unless sources are chosen; the chosen ones are joined by
 * the sources of the lines the lookup cites. An extract holds the files of its lines' sources.
 */
export function sourcesSummary(draft: WizardDraft): {
  key: string;
  values?: Record<string, string>;
} {
  const sources = chosenSources(draft);
  if (draft.scope === 'lookup') {
    if (draft.content === 'extract') return { key: 'evidence.wizard.sourcesFromLookupLines' };
    if (sources === null) return { key: 'evidence.wizard.sourcesLookupAll' };
    return { key: 'evidence.wizard.sourcesLookupChosen', values: { sources: sources.join(', ') } };
  }
  if (draft.content === 'extract') return { key: 'evidence.wizard.sourcesFromLines' };
  if (sources === null) return { key: 'evidence.wizard.sourcesAll' };
  return { key: 'evidence.wizard.sourcesChosen', values: { sources: sources.join(', ') } };
}

/** `12.10.2026` – `14.10.2026` of a scope's days. */
export function scopeDays(scope: Scope): { first: string; last: string; count: number } | null {
  const days = [...scope.days].sort();
  const first = days[0];
  const last = days.at(-1);
  return first && last ? { first, last, count: days.length } : null;
}

/** States whose package is still being made (the page refreshes itself). */
export function isUnfinished(item: Pick<EvidenceOut, 'status'>): boolean {
  return item.status === 'queued' || item.status === 'building';
}

/**
 * A package whose retention ran out before the nightly purge (worker, daily after 03:00) marked
 * it `expired`: the API still says `ready` but answers `downloadable: false` (and 410 to a
 * download). Shown as expired, never as "Hazır".
 */
export function isExpiredUnpurged(item: Pick<EvidenceOut, 'status' | 'downloadable'>): boolean {
  return item.status === 'ready' && !item.downloadable;
}

/** The state to show (`isExpiredUnpurged` → `expired`). */
export function shownStatus(item: Pick<EvidenceOut, 'status' | 'downloadable'>): string {
  return isExpiredUnpurged(item) ? 'expired' : item.status;
}

/** Why "İndir" is disabled (i18n key), `null` when the package can be downloaded. */
export function downloadBlockedReason(
  item: Pick<EvidenceOut, 'status' | 'downloadable'>,
): string | null {
  if (item.downloadable) return null;
  if (isUnfinished(item)) return 'evidence.blocked.unfinished';
  if (item.status === 'failed') return 'evidence.blocked.failed';
  if (item.status === 'deleted') return 'evidence.blocked.deleted';
  return 'evidence.blocked.expired';
}
