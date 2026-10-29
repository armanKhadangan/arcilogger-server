/**
 * The first-run wizard (Wave 4 contract 2.8.4, D11): a guided checklist over the Ayarlar pages.
 * Each step's state is derived from the server's configuration (`GET /admin/config`) and the
 * stamp tests of the request history; nothing is stored, so a skipped step simply stays open
 * (owner question Q18). The server decides again on every page the wizard opens.
 */

import type {
  AdminConfig,
  AdminRequestOut,
  FirewallState,
  SetupProblem,
  SourceOut,
  TsaTestResult,
} from '@/lib/settings-types';
import type { SourceHealth } from '@/lib/sources-types';

import { causeKey } from '../settings/kamusm-model';

import { wizardLink } from './wizard-mode';

export const WIZARD_STEPS = [
  'admin',
  'site',
  'sources',
  'kamusm_test',
  'kamusm_production',
  'replica',
  'smtp',
  'summary',
] as const;
export type WizardStepId = (typeof WIZARD_STEPS)[number];
/** The steps with a state ("Özet" has none of its own). */
export type CheckedStepId = Exclude<WizardStepId, 'summary'>;
export const CHECKED_STEPS = WIZARD_STEPS.filter(
  (step): step is CheckedStepId => step !== 'summary',
);

/** The Ayarlar page a step opens, in wizard mode (`admin` and `summary` open none). */
export const STEP_LINKS: Readonly<Partial<Record<WizardStepId, string>>> = {
  site: wizardLink('/settings/general'),
  sources: wizardLink('/settings/sources'),
  kamusm_test: wizardLink('/settings/kamusm?step=test'),
  kamusm_production: wizardLink('/settings/kamusm?step=production'),
  replica: wizardLink('/settings/replica'),
  smtp: wizardLink('/settings/email'),
};

export function isWizardStep(value: string | null): value is WizardStepId {
  return value !== null && (WIZARD_STEPS as readonly string[]).includes(value);
}

/**
 * What a step still lacks: a text key with its values (`setupWizard.missing.*`, or a sealing
 * cause's text of Ayarlar → Kamu SM).
 */
export interface Missing {
  key: string;
  values?: Record<string, string>;
}

export interface StepState {
  done: boolean;
  missing: Missing[];
}

/** What the states are derived from. */
export interface WizardInput {
  config: AdminConfig;
  /** A stamp test succeeded and was verified ({@link verifiedStampTest}). */
  tested: boolean;
}

/**
 * A "Damga testi" of the history that succeeded and was verified, as Ayarlar → Kamu SM counts it
 * (`useTested` of kamusm-page.tsx).
 */
export function verifiedStampTest(requests: readonly AdminRequestOut[]): boolean {
  return requests.some(
    (item) =>
      item.kind === 'action.tsa_test' &&
      item.state === 'succeeded' &&
      (item.result as TsaTestResult | null)?.verified === true,
  );
}

/** The problems of the site step: the site name and the time servers (`site_name`, `clock.*`). */
export function siteProblems(problems: readonly SetupProblem[]): SetupProblem[] {
  return problems.filter((item) => item.key === 'site_name' || item.key.startsWith('clock.'));
}

const missing = (key: string): Missing => ({ key: `setupWizard.missing.${key}` });

/** A sealing cause in the words of Ayarlar → Kamu SM (an unknown one shows its code). */
const missingCause = (cause: string): Missing => ({
  key: causeKey(cause),
  values: { defaultValue: cause },
});

const filled = (value: string | null): boolean => value !== null && value.trim() !== '';

function state(found: Missing[]): StepState {
  return { done: found.length === 0, missing: found };
}

/** A step's state (contract 2.8.4, "Done when"). */
export function stepState(step: CheckedStepId, { config, tested }: WizardInput): StepState {
  const { sections } = config;
  switch (step) {
    case 'admin':
      // Whoever opens the wizard is a signed-in administrator with two-step verification.
      return state([]);
    case 'site':
      return state(
        siteProblems(config.problems).some((item) => item.level === 'error')
          ? [missing('siteProblems')]
          : [],
      );
    case 'sources':
      if (sections.sources.length === 0) return state([missing('noSource')]);
      return state(sections.sources.some((item) => item.enabled) ? [] : [missing('noEnabled')]);
    case 'kamusm_test':
      return state(tested || sections.tsa.server === 'production' ? [] : [missing('noTest')]);
    case 'kamusm_production': {
      const tsa = sections.tsa;
      return state([
        ...(tsa.server === 'production' ? [] : [missing('notProduction')]),
        ...(tsa.seal_ready.ok
          ? []
          : tsa.seal_ready.causes.length > 0
            ? tsa.seal_ready.causes.map(missingCause)
            : [missing('sealNotReady')]),
      ]);
    }
    case 'replica':
      return state(sections.replica.enabled ? [] : [missing('replicaOff')]);
    case 'smtp':
      // Only switched-on sending counts: while it is off no alert or report e-mail goes out.
      return state([
        ...(sections.smtp.enabled ? [] : [missing('smtpOff')]),
        ...(filled(sections.smtp.host) ? [] : [missing('smtpServer')]),
        ...(filled(sections.smtp.from_address) ? [] : [missing('smtpSender')]),
      ]);
  }
}

export function wizardStates(input: WizardInput): Record<CheckedStepId, StepState> {
  return Object.fromEntries(CHECKED_STEPS.map((step) => [step, stepState(step, input)])) as Record<
    CheckedStepId,
    StepState
  >;
}

/** The step the wizard opens without `?step=`: the first one not done, else the summary. */
export function firstOpenStep(states: Record<CheckedStepId, StepState>): WizardStepId {
  return CHECKED_STEPS.find((step) => !states[step].done) ?? 'summary';
}

/** "Bu adımı şimdilik atla": the next step (the skipped one keeps its state). */
export function nextStep(step: WizardStepId): WizardStepId {
  return WIZARD_STEPS[Math.min(WIZARD_STEPS.indexOf(step) + 1, WIZARD_STEPS.length - 1)] ?? step;
}

/** The firewall notes of the sources step: one per firewall state of the enabled sources. */
export type FirewallNote = 'managed' | 'not_managed';

const NOTES: Record<FirewallState, FirewallNote | null> = {
  open: 'managed',
  pending: 'managed',
  not_managed: 'not_managed',
  // Only a switched-off source is closed: it needs no port.
  closed: null,
};

export function firewallNotes(sources: readonly SourceOut[]): FirewallNote[] {
  const notes = new Set(sources.filter((item) => item.enabled).map((item) => NOTES[item.firewall]));
  return (['managed', 'not_managed'] as const).filter((note) => notes.has(note));
}

/**
 * Whether a configured source's data arrived (`GET /sources`, a network sums its members):
 * `null` while the overview is not there (not loaded, refused or failed).
 */
export interface Arrival {
  name: string;
  enabled: boolean;
  arrived: boolean | null;
  lastMessage: string | null;
}

export function arrivals(
  sources: readonly SourceOut[],
  overview: readonly SourceHealth[] | undefined,
): Arrival[] {
  return sources.map((source) => {
    const health = overview?.find((item) => item.name === source.name);
    const last = health?.last_message ?? null;
    return {
      name: source.name,
      enabled: source.enabled,
      arrived: overview ? last !== null : null,
      lastMessage: last,
    };
  });
}
