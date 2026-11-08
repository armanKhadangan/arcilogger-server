import type { SmtpSecurity } from '@/lib/settings-types';

/**
 * The e-mail provider presets of Ayarlar → E-posta (0.4.0 P6, contract 2.6). A preset fills the
 * server, port and security of a provider and shows what that provider needs; it is not stored:
 * the page infers it from the server name when it opens. Every preset connects with a user name
 * and password, or without a login (a relay that authorises the office by its IP address).
 *
 * The facts (S3, S4) follow the providers' pages read on 2026-11-08 (design/0.4.0-contract.md,
 * Deviations): Google "Transition from less secure apps to OAuth" (answer 176600) and "Route
 * outgoing SMTP relay messages through Google" (answer 2956491); Microsoft Learn "How to set up a
 * multifunction device or application to send email using Microsoft 365" (port 587 with STARTTLS;
 * 465 is not supported).
 */
export type SmtpPresetId =
  'google' | 'google-relay' | 'microsoft365' | 'hosting' | 'company' | 'other';

export interface SmtpPreset {
  id: SmtpPresetId;
  /** The provider's server; `null`: the administrator types it. */
  host: string | null;
  port: number | null;
  security: SmtpSecurity | null;
  /** `password`: user name and password; `none`: no login (the user name is cleared). */
  login: 'password' | 'none' | 'either' | null;
}

export const SMTP_PRESETS: readonly SmtpPreset[] = [
  { id: 'google', host: 'smtp.gmail.com', port: 587, security: 'starttls', login: 'password' },
  {
    id: 'google-relay',
    host: 'smtp-relay.gmail.com',
    port: 587,
    security: 'starttls',
    login: 'none',
  },
  {
    id: 'microsoft365',
    host: 'smtp.office365.com',
    port: 587,
    security: 'starttls',
    login: 'password',
  },
  { id: 'hosting', host: null, port: 587, security: 'starttls', login: 'password' },
  { id: 'company', host: null, port: 25, security: 'none', login: 'either' },
  { id: 'other', host: null, port: null, security: null, login: null },
];

export function smtpPreset(id: SmtpPresetId): SmtpPreset {
  const found = SMTP_PRESETS.find((preset) => preset.id === id);
  if (!found) throw new Error(`unknown SMTP preset ${id}`);
  return found;
}

/** The preset of a saved server: a provider's own name, `other` for any other, `null` for none. */
export function presetOf(host: string): SmtpPresetId | null {
  const name = host.trim().replace(/\.$/, '').toLowerCase();
  if (!name) return null;
  return SMTP_PRESETS.find((preset) => preset.host === name)?.id ?? 'other';
}

/** The fields a preset sets, as the form holds them. */
export interface PresetFields {
  host: string;
  port: string;
  security: SmtpSecurity;
  username: string;
}

export type PresetField = keyof PresetFields;

export interface PresetPlan {
  /** The values the preset sets (fills and overwrites). */
  next: Partial<PresetFields>;
  /** The filled fields it would change, with their values now and after: asked first. */
  overwrites: { field: PresetField; from: string; to: string }[];
}

/**
 * What choosing `preset` changes in `form`. An empty field is filled; a filled one that differs is
 * an overwrite the page asks about first. While no server is typed, the port and security are
 * the form's defaults rather than a choice, so they are filled without asking.
 */
export function planPreset(form: PresetFields, preset: SmtpPreset): PresetPlan {
  const target: Partial<PresetFields> = {};
  if (preset.host !== null) target.host = preset.host;
  if (preset.port !== null) target.port = String(preset.port);
  if (preset.security !== null) target.security = preset.security;
  if (preset.login === 'none') target.username = '';
  const fresh = form.host.trim() === '';
  const plan: PresetPlan = { next: {}, overwrites: [] };
  for (const field of Object.keys(target) as PresetField[]) {
    const to = target[field] ?? '';
    const from = field === 'security' ? form.security : form[field].trim();
    if (from === to) continue;
    plan.next = { ...plan.next, [field]: to };
    const blank = from === '' || (fresh && (field === 'port' || field === 'security'));
    if (!blank) plan.overwrites.push({ field, from, to });
  }
  return plan;
}
