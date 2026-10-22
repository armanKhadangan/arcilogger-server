/**
 * "Kişiye ata" / "Atamayı kaldır" for one or more devices: the form's draft, its checks and the
 * request body (`POST /devices/assign`). No components here; unit-tested in
 * `assign-model.test.ts`.
 *
 * The start of validity ("geçerlilik başlangıcı") is "now" unless the user chooses a moment: then
 * the body carries `valid_from`, typed as a site-local day and 24-hour time in the Turkish format
 * (`src/lib/dates.ts`, like the 5651 form) and sent with the site's UTC offset. The history is
 * appended to, never rewritten: the server refuses (409) a start in the future or before a
 * device's current assignment (or the end of its last one); the form checks what it knows (the
 * current assignment's start), so the user sees the reason before sending, and shows the
 * server's answer for the rest. The note is kept on the new assignment, so only "Kişiye ata" has
 * one: removing an assignment opens none, and the server refuses a note there (422).
 */

import type { AssignRequest, DeviceOut } from '@/lib/api-types';
import { dayText, parseDay, parseTime } from '@/lib/dates';
import { siteDateTime } from '@/lib/display';
import { codePointLength, type FieldErrors } from '@/lib/validation';

/** The API's limit of MAC addresses per request. */
export const ASSIGN_MAX_DEVICES = 500;
/** `AssignRequest.note` (characters). */
export const ASSIGN_NOTE_MAX = 500;

export type AssignMode = 'assign' | 'unassign';
export type AssignField = 'devices' | 'person' | 'day' | 'time' | 'note';

export interface AssignDraft {
  personId: number | null;
  /** `now`: no `valid_from` (the server's clock); `at`: from the typed day and time. */
  when: 'now' | 'at';
  /** `dd.MM.yyyy` (site day). */
  day: string;
  /** `HH:mm` or `HH:mm:ss` (24 hours, site time). */
  time: string;
  note: string;
}

/** A new draft whose day and time fields show `now` (site time, to the minute). */
export function initialDraft(now: Date): AssignDraft {
  const { date, time } = siteDateTime(now);
  return { personId: null, when: 'now', day: dayText(date), time: time.slice(0, 5), note: '' };
}

/** Milliseconds of the site zone's (Europe/Istanbul) offset from UTC at the moment `utc`. */
function siteOffsetAt(utc: number): number {
  const { date, time } = siteDateTime(new Date(utc));
  const [year, month, day] = date.split('-').map(Number);
  const [hours, minutes, seconds] = time.split(':').map(Number);
  const wall = Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1, hours, minutes, seconds);
  return wall - Math.floor(utc / 1000) * 1000;
}

/**
 * A site-local day (`2026-10-12`) and time (`09:00:00`) as an ISO 8601 moment with the site's
 * offset (`2026-10-12T09:00:00+03:00`), computed for that moment (no hard-coded `+03:00`).
 */
export function siteLocalToIso(day: string, time: string): string {
  const [year, month, date] = day.split('-').map(Number);
  const [hours, minutes, seconds] = time.split(':').map(Number);
  const wall = Date.UTC(year ?? 1970, (month ?? 1) - 1, date ?? 1, hours, minutes, seconds);
  // Twice: the offset at the guessed instant decides the instant (daylight saving edges).
  let utc = wall - siteOffsetAt(wall);
  utc = wall - siteOffsetAt(utc);
  const offset = Math.round((wall - utc) / 60_000);
  const sign = offset < 0 ? '-' : '+';
  const absolute = Math.abs(offset);
  const hh = String(Math.floor(absolute / 60)).padStart(2, '0');
  const mm = String(absolute % 60).padStart(2, '0');
  return `${day}T${time}${sign}${hh}:${mm}`;
}

/** The latest start of an open assignment among `devices` (a new start must not be earlier). */
export function latestAssignedSince(
  devices: readonly Pick<DeviceOut, 'assigned_since'>[],
): string | null {
  let latest: string | null = null;
  for (const device of devices) {
    const since = device.assigned_since;
    if (since && (latest === null || Date.parse(since) > Date.parse(latest))) latest = since;
  }
  return latest;
}

export interface AssignCheck {
  errors: FieldErrors<AssignField>;
  /** The request to send, `null` while there are errors. */
  body: AssignRequest | null;
}

/**
 * Checks the draft for `devices` and builds the body. Error values are i18n keys. `now` is the
 * browser's clock: a start a few seconds ahead of the server's is still refused by the server.
 */
export function checkAssign(
  mode: AssignMode,
  draft: AssignDraft,
  devices: readonly Pick<DeviceOut, 'mac' | 'assigned_since' | 'person'>[],
  now: Date,
): AssignCheck {
  const errors: FieldErrors<AssignField> = {};
  if (devices.length === 0) errors.devices = 'devices.assign.errors.noDevices';
  else if (devices.length > ASSIGN_MAX_DEVICES) errors.devices = 'devices.assign.errors.tooMany';
  else if (mode === 'unassign' && devices.every((device) => device.person === null))
    errors.devices = 'devices.assign.errors.nothingToUnassign';
  if (mode === 'assign' && draft.personId === null) errors.person = 'devices.assign.errors.person';

  let validFrom: string | null = null;
  if (draft.when === 'at') {
    const day = draft.day.trim() ? parseDay(draft.day) : null;
    const time = draft.time.trim() ? parseTime(draft.time) : null;
    if (!draft.day.trim()) errors.day = 'validation.required';
    else if (day === null) errors.day = 'lookup.validation.date';
    if (!draft.time.trim()) errors.time = 'validation.required';
    else if (time === null) errors.time = 'lookup.validation.time';
    if (day !== null && time !== null) {
      validFrom = siteLocalToIso(day, time);
      const start = Date.parse(validFrom);
      const latest = latestAssignedSince(devices);
      if (start > now.getTime()) errors.day = 'devices.assign.errors.future';
      else if (latest !== null && start < Date.parse(latest))
        errors.day = 'devices.assign.errors.beforeCurrent';
    }
  }

  // The note goes on the assignment opened; removing one opens none (the server refuses a note).
  const note = mode === 'assign' ? draft.note.trim() : '';
  if (codePointLength(note) > ASSIGN_NOTE_MAX) errors.note = 'validation.tooLong';
  if (Object.values(errors).some(Boolean)) return { errors, body: null };
  return {
    errors,
    body: {
      macs: devices.map((device) => device.mac),
      person_id: mode === 'assign' ? draft.personId : null,
      ...(validFrom ? { valid_from: validFrom } : {}),
      ...(note ? { note } : {}),
    },
  };
}
