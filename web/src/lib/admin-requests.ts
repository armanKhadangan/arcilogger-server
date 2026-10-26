/**
 * Change requests of the browser administration (ADR-0014, contract 2.10 and 2.17).
 *
 * A change of the machine configuration (or an action such as "Şimdi mühürle") is answered with
 * 202 and a request id; the administration service (ArciAdmin) applies it on the server and
 * reports every step. The page follows it here:
 *
 *  - {@link requestPollInterval}: `GET /admin/requests/{id}` every second for 15 s, then every 3 s,
 *    always with `X-Arci-Background: 1` (following a request is not user activity), until a
 *    terminal state;
 *  - {@link sectionState}: the one state machine of every settings section (2.17 "Section
 *    states"), from the loaded configuration, the agent's heartbeat, the form and the request;
 *  - helpers for the API's answers: field problems of a 422, change lists (secrets only as
 *    "set"/"unset"), the services a change reloads.
 *
 * The server decides every state; the browser only shows it.
 */

import { isApiError } from './api';
import { currentLang } from './i18n';
import type {
  AdminRequestOut,
  AgentState,
  Change,
  CodeMessage,
  FieldProblem,
  RequestKind,
  RequestState,
  RequestStep,
  SecretChange,
  ServiceName,
} from './settings-types';

/** Every second while the request is young … */
export const REQUEST_POLL_FAST_MS = 1_000;
/** … for this long after it was accepted … */
export const REQUEST_POLL_FAST_FOR_MS = 15_000;
/** … then every 3 seconds. */
export const REQUEST_POLL_SLOW_MS = 3_000;
/** The administration service's status in the header chip. */
export const AGENT_STATUS_POLL_MS = 10_000;

/** States after which nothing changes any more (`contract.TERMINAL_STATES`). */
export const TERMINAL_STATES: ReadonlySet<RequestState> = new Set<RequestState>([
  'expired',
  'rejected',
  'conflict',
  'probe_failed',
  'cancelled',
  'active',
  'partially_active',
  'failed',
  'needs_attention',
  'succeeded',
  'timed_out',
]);

export function isTerminal(state: RequestState): boolean {
  return TERMINAL_STATES.has(state);
}

/** An action of the agent's tasks or of the worker (`action.*`, `worker.*`): no setting changes. */
export function isAction(kind: RequestKind): boolean {
  return kind.startsWith('action.') || kind.startsWith('worker.');
}

/**
 * How long until the next `GET /admin/requests/{id}`: `false` once the request is terminal,
 * else 1 s during the first 15 s after `startedAt` (ms since epoch), then 3 s.
 */
export function requestPollInterval(
  request: Pick<AdminRequestOut, 'state'> | undefined,
  startedAt: number,
  now: number = Date.now(),
): number | false {
  if (request && isTerminal(request.state)) return false;
  return now - startedAt < REQUEST_POLL_FAST_FOR_MS ? REQUEST_POLL_FAST_MS : REQUEST_POLL_SLOW_MS;
}

// -- the section state machine (2.17) -------------------------------------------------------------

/** What a settings section shows (contract 2.17 "Section states"). */
export type SectionState =
  | 'loading'
  | 'read_only'
  | 'agent_unavailable'
  | 'ready'
  | 'dirty'
  | 'invalid'
  | 'confirm'
  | 'reauth'
  | 'submitting'
  | 'queued'
  | 'waiting_for_agent'
  | 'probing'
  | 'pending'
  | 'applying'
  | 'applied'
  | 'active'
  | 'partially_active'
  | 'failed'
  | 'needs_attention'
  | 'conflict'
  | 'rejected'
  | 'expired'
  | 'cancelled'
  | 'restart_required';

/** Where the form is in its save flow (the page's own part of the state). */
export type FlowPhase = 'edit' | 'checking' | 'confirm' | 'reauth' | 'submitting' | 'tracking';

export interface SectionInputs {
  /** The configuration (and the permission) is still loading. */
  loading: boolean;
  /** The user may change this section (`settings.manage`, `sources.manage` for sources). */
  canEdit: boolean;
  /** The administration service's heartbeat; `null` while unknown. */
  agent: AgentState | null;
  phase: FlowPhase;
  dirty: boolean;
  /** Client checks or the API's 422 found a problem. */
  invalid: boolean;
  /** The request being followed (phase `tracking`). */
  request: Pick<AdminRequestOut, 'state'> | null;
  /** 409 `config_conflict`: the file changed while the form was edited. */
  conflict: boolean;
  /** A hand edit on the server waits for a service restart. */
  restartRequired: boolean;
}

/** A request state as the section shows it. */
export function requestSectionState(state: RequestState): SectionState {
  switch (state) {
    case 'queued':
    case 'received':
      return 'queued';
    case 'waiting_for_agent':
      return 'waiting_for_agent';
    case 'probing':
      return 'probing';
    case 'pending':
      return 'pending';
    case 'applying':
    case 'dispatched':
    case 'running':
      return 'applying';
    case 'applied':
      return 'applied';
    case 'active':
    case 'succeeded':
      return 'active';
    case 'partially_active':
      return 'partially_active';
    case 'failed':
    case 'probe_failed':
    case 'timed_out':
      return 'failed';
    case 'needs_attention':
      return 'needs_attention';
    case 'conflict':
      return 'conflict';
    case 'rejected':
      return 'rejected';
    case 'expired':
      return 'expired';
    case 'cancelled':
      return 'cancelled';
  }
}

/**
 * The state of a section. Order matters: no permission beats everything; a conflict must be
 * resolved by reloading; a request being followed is shown until the user edits again; without
 * a running administration service nothing can be saved (the values stay readable).
 */
export function sectionState(input: SectionInputs): SectionState {
  if (input.loading) return 'loading';
  if (!input.canEdit) return 'read_only';
  if (input.conflict) return 'conflict';
  if (input.phase === 'tracking' && input.request) return requestSectionState(input.request.state);
  if (input.phase === 'tracking' || input.phase === 'submitting') return 'submitting';
  if (input.phase === 'reauth') return 'reauth';
  if (input.phase === 'confirm') return 'confirm';
  if (input.agent === 'missing' || input.agent === 'disabled') return 'agent_unavailable';
  if (input.invalid) return 'invalid';
  if (input.phase === 'checking') return 'submitting';
  if (input.dirty) return 'dirty';
  if (input.restartRequired) return 'restart_required';
  return 'ready';
}

/** The machine configuration can be saved: the service answers (late is still fine). */
export function agentWritable(agent: AgentState | null): boolean {
  return agent !== 'missing' && agent !== 'disabled';
}

/** States in which the form's fields may be edited. */
export function editable(state: SectionState): boolean {
  return [
    'ready',
    'dirty',
    'invalid',
    'restart_required',
    'active',
    'partially_active',
    'failed',
    'rejected',
    'expired',
    'cancelled',
  ].includes(state);
}

/** States with a request still under way (the form is locked meanwhile). */
export function inFlight(state: SectionState): boolean {
  return [
    'submitting',
    'queued',
    'waiting_for_agent',
    'probing',
    'pending',
    'applying',
    'applied',
  ].includes(state);
}

// -- answers ------------------------------------------------------------------------------------

function isFieldProblem(value: unknown): value is FieldProblem {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as FieldProblem).field === 'string' &&
    typeof (value as FieldProblem).message_tr === 'string' &&
    typeof (value as FieldProblem).message_en === 'string'
  );
}

/** The refused fields of a 422 (`details: [{field, code, message_tr, message_en}]`). */
export function fieldProblems(error: unknown): FieldProblem[] {
  if (!isApiError(error) || !Array.isArray(error.details)) return [];
  return (error.details as unknown[]).filter(isFieldProblem);
}

/** A detail of an error body (`details.causes`, `details.missing`, `details.current_etag`, …). */
export function errorDetail(error: unknown, key: string): unknown {
  if (!isApiError(error)) return undefined;
  const details = error.details;
  if (typeof details !== 'object' || details === null || Array.isArray(details)) return undefined;
  return (details as Record<string, unknown>)[key];
}

/** A string list detail (`details.causes`, `details.missing`), `[]` when absent. */
export function errorList(error: unknown, key: string): string[] {
  const value = errorDetail(error, key);
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

/** The text of a code with the API's texts in the interface language. */
export function localized(message: Pick<CodeMessage, 'message_tr' | 'message_en'>): string {
  return currentLang() === 'en' ? message.message_en : message.message_tr;
}

export function isSecretChange(change: Change): change is SecretChange {
  return 'secret' in change && change.secret;
}

/** A value of a change list as text (lists joined, `null`/empty as a dash). */
export function changeValueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.map(changeValueText).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'boolean') return value ? '✓' : '✗';
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '—';
}

/** The service whose reload a step reports. */
export const STEP_SERVICES: Partial<Record<RequestStep, ServiceName>> = {
  'reload.collector': 'ArciCollector',
  'reload.ingest': 'ArciIngest',
  'reload.worker': 'ArciWorker',
  'reload.api': 'ArciApi',
};

/** The reload steps that have not finished in a request (its "ArciIngest has not …" lines). */
export function servicesNotReloaded(request: Pick<AdminRequestOut, 'steps'>): ServiceName[] {
  return request.steps
    .filter((step) => step.state !== 'done' && step.state !== 'skipped')
    .map((step) => STEP_SERVICES[step.step])
    .filter((service): service is ServiceName => service !== undefined);
}

/** A system step (firewall, time, Zamane client) failed in a request. */
export function systemStepFailed(request: Pick<AdminRequestOut, 'steps'>): RequestStep[] {
  return request.steps
    .filter(
      (step) =>
        (step.step === 'system.firewall' ||
          step.step === 'system.time' ||
          step.step === 'jar.promotion') &&
        step.state === 'failed',
    )
    .map((step) => step.step);
}

/** Whether "Geri al" is offered: an applied configuration change whose version is known. */
export function revertible(request: AdminRequestOut): boolean {
  return (
    (request.kind === 'config.apply' || request.kind === 'config.revert') &&
    (request.state === 'active' || request.state === 'partially_active') &&
    request.version !== null
  );
}

/** Whether "İptal et" is offered: a request that has not started (queued or held). */
export function cancellable(request: AdminRequestOut): boolean {
  return request.state === 'queued' || request.state === 'pending';
}
