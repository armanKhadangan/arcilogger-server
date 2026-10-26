import en from '@/locales/en.json';
import tr from '@/locales/tr.json';

import { ADMIN } from '@/test/samples';

import {
  cancellable,
  changeRows,
  changeValueText,
  editable,
  errorList,
  fieldProblems,
  inFlight,
  isAction,
  isTerminal,
  REQUEST_POLL_FAST_FOR_MS,
  REQUEST_POLL_FAST_MS,
  REQUEST_POLL_SLOW_MS,
  requestPollInterval,
  requestSectionState,
  revertible,
  sectionState,
  servicesNotReloaded,
  systemStepFailed,
  TERMINAL_STATES,
  type SectionInputs,
  type SectionState,
} from './admin-requests';
import { ApiError } from './api';
import type { AdminRequestOut, RequestKind, RequestState, RequestStep } from './settings-types';

/** `arcilogger_core.admin.contract.STATES`, in its order. */
const STATES: RequestState[] = [
  'queued',
  'waiting_for_agent',
  'expired',
  'received',
  'rejected',
  'conflict',
  'probing',
  'probe_failed',
  'pending',
  'cancelled',
  'applying',
  'applied',
  'active',
  'partially_active',
  'failed',
  'needs_attention',
  'dispatched',
  'running',
  'succeeded',
  'timed_out',
];
/** `contract.STEPS`. */
const STEPS: RequestStep[] = [
  'received',
  'validate',
  'probe',
  'hold',
  'backup',
  'write_secrets',
  'write_config',
  'install_files',
  'verify',
  'reload.collector',
  'reload.ingest',
  'reload.worker',
  'reload.api',
  'system.firewall',
  'system.time',
  'jar.promotion',
  'dispatch',
  'task',
  'journal',
  'rollback',
];
const KINDS: RequestKind[] = [
  'config.apply',
  'config.revert',
  'config.cancel',
  'action.seal',
  'action.credit',
  'action.tsa_test',
  'action.replica_test',
  'worker.test_email',
  'worker.lease_rebuild',
];

/** The captured site name change, applied (`ADMIN.request_active`), with `overrides`. */
function request(overrides: Partial<AdminRequestOut> = {}): AdminRequestOut {
  return { ...structuredClone(ADMIN.request_active), steps: [], ...overrides };
}

function lookup(tree: unknown, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], tree);
}

describe('following a request (contract 2.10)', () => {
  it('polls every second for 15 s, then every 3 s, and stops at a terminal state', () => {
    const start = 1_000_000;
    expect(requestPollInterval(undefined, start, start)).toBe(REQUEST_POLL_FAST_MS);
    expect(requestPollInterval(request({ state: 'queued' }), start, start + 14_999)).toBe(1_000);
    expect(
      requestPollInterval(request({ state: 'applying' }), start, start + REQUEST_POLL_FAST_FOR_MS),
    ).toBe(REQUEST_POLL_SLOW_MS);
    expect(REQUEST_POLL_SLOW_MS).toBe(3_000);
    for (const state of TERMINAL_STATES)
      expect(requestPollInterval(request({ state }), start, start)).toBe(false);
  });

  it('knows the terminal states of the contract', () => {
    expect([...TERMINAL_STATES].sort()).toEqual(
      [
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
      ].sort(),
    );
    expect(STATES.filter(isTerminal)).toHaveLength(11);
  });

  it('shows every request state as a section state', () => {
    const shown = Object.fromEntries(STATES.map((state) => [state, requestSectionState(state)]));
    expect(shown).toEqual({
      queued: 'queued',
      waiting_for_agent: 'waiting_for_agent',
      expired: 'expired',
      received: 'queued',
      rejected: 'rejected',
      conflict: 'conflict',
      probing: 'probing',
      probe_failed: 'failed',
      pending: 'pending',
      cancelled: 'cancelled',
      applying: 'applying',
      applied: 'applied',
      active: 'active',
      partially_active: 'partially_active',
      failed: 'failed',
      needs_attention: 'needs_attention',
      dispatched: 'applying',
      running: 'applying',
      succeeded: 'active',
      timed_out: 'failed',
    });
  });
});

describe('actions and configuration changes', () => {
  it('an action of a task or of the worker changes no setting', () => {
    expect(KINDS.filter(isAction)).toEqual([
      'action.seal',
      'action.credit',
      'action.tsa_test',
      'action.replica_test',
      'worker.test_email',
      'worker.lease_rebuild',
    ]);
  });
});

describe('the section state machine (contract 2.17)', () => {
  const base: SectionInputs = {
    loading: false,
    canEdit: true,
    agent: 'ok',
    phase: 'edit',
    dirty: false,
    invalid: false,
    request: null,
    conflict: false,
    restartRequired: false,
  };
  const cases: [SectionState, Partial<SectionInputs>][] = [
    ['loading', { loading: true }],
    ['read_only', { canEdit: false }],
    ['agent_unavailable', { agent: 'missing' }],
    ['agent_unavailable', { agent: 'disabled', dirty: true }],
    ['ready', {}],
    ['ready', { agent: 'stale' }],
    ['ready', { agent: null }],
    ['dirty', { dirty: true }],
    ['invalid', { dirty: true, invalid: true }],
    ['submitting', { phase: 'checking' }],
    ['confirm', { phase: 'confirm' }],
    ['reauth', { phase: 'reauth' }],
    ['submitting', { phase: 'submitting' }],
    ['submitting', { phase: 'tracking' }],
    ['queued', { phase: 'tracking', request: { state: 'queued' } }],
    ['waiting_for_agent', { phase: 'tracking', request: { state: 'waiting_for_agent' } }],
    ['probing', { phase: 'tracking', request: { state: 'probing' } }],
    ['pending', { phase: 'tracking', request: { state: 'pending' } }],
    ['applying', { phase: 'tracking', request: { state: 'applying' } }],
    ['applied', { phase: 'tracking', request: { state: 'applied' } }],
    ['active', { phase: 'tracking', request: { state: 'active' } }],
    ['partially_active', { phase: 'tracking', request: { state: 'partially_active' } }],
    ['failed', { phase: 'tracking', request: { state: 'failed' } }],
    ['needs_attention', { phase: 'tracking', request: { state: 'needs_attention' } }],
    ['conflict', { conflict: true, dirty: true }],
    ['conflict', { phase: 'tracking', request: { state: 'conflict' } }],
    ['rejected', { phase: 'tracking', request: { state: 'rejected' } }],
    ['expired', { phase: 'tracking', request: { state: 'expired' } }],
    ['cancelled', { phase: 'tracking', request: { state: 'cancelled' } }],
    ['restart_required', { restartRequired: true }],
    ['dirty', { restartRequired: true, dirty: true }],
    // No permission beats everything, a running request beats a missing heartbeat.
    [
      'read_only',
      { canEdit: false, agent: 'missing', phase: 'tracking', request: { state: 'active' } },
    ],
    ['applying', { agent: 'missing', phase: 'tracking', request: { state: 'applying' } }],
  ];

  it.each(cases)('%s', (expected, input) => {
    expect(sectionState({ ...base, ...input })).toBe(expected);
  });

  it('covers every state of the contract', () => {
    const reached = new Set(cases.map(([state]) => state));
    const all: SectionState[] = [
      'loading',
      'read_only',
      'agent_unavailable',
      'ready',
      'dirty',
      'invalid',
      'confirm',
      'reauth',
      'submitting',
      'queued',
      'waiting_for_agent',
      'probing',
      'pending',
      'applying',
      'applied',
      'active',
      'partially_active',
      'failed',
      'needs_attention',
      'conflict',
      'rejected',
      'expired',
      'cancelled',
      'restart_required',
    ];
    expect(all.filter((state) => !reached.has(state))).toEqual([]);
  });

  it('locks the form while a request is under way, frees it at the end', () => {
    for (const state of [
      'submitting',
      'queued',
      'probing',
      'pending',
      'applying',
      'applied',
    ] as const) {
      expect(inFlight(state)).toBe(true);
      expect(editable(state)).toBe(false);
    }
    for (const state of ['ready', 'dirty', 'invalid', 'active', 'failed', 'expired'] as const) {
      expect(inFlight(state)).toBe(false);
      expect(editable(state)).toBe(true);
    }
    for (const state of [
      'loading',
      'read_only',
      'agent_unavailable',
      'conflict',
      'needs_attention',
    ] as const)
      expect(editable(state)).toBe(false);
  });
});

describe("the API's answers", () => {
  it('reads the refused fields of a 422 and the lists of a 409', () => {
    const invalid = new ApiError({
      status: 422,
      code: 'config_invalid',
      messageTr: 'Ayarlar geçersiz; işaretli alanları düzeltin.',
      messageEn: 'The settings are invalid; correct the marked fields.',
      details: [
        {
          field: 'site_name',
          code: 'site_name_required',
          message_tr: 'Kuruluş adı boş olamaz.',
          message_en: 'x',
        },
        { loc: ['body'], msg: 'not a field problem' },
      ],
    });
    expect(fieldProblems(invalid).map((problem) => problem.field)).toEqual(['site_name']);
    const busy = new ApiError({
      status: 409,
      code: 'tsa_would_break',
      messageTr: 'x',
      messageEn: 'x',
      details: { causes: ['credentials', 3] },
    });
    expect(errorList(busy, 'causes')).toEqual(['credentials']);
    expect(errorList(new Error('x'), 'causes')).toEqual([]);
    expect(fieldProblems(busy)).toEqual([]);
  });

  it('names the services that have not loaded a change and the failed system steps', () => {
    const partly = request({
      state: 'partially_active',
      steps: [
        { step: 'reload.collector', state: 'done' },
        { step: 'reload.ingest', state: 'pending' },
        { step: 'reload.worker', state: 'skipped' },
        { step: 'system.firewall', state: 'failed' },
      ],
    });
    expect(servicesNotReloaded(partly)).toEqual(['ArciIngest']);
    expect(systemStepFailed(partly)).toEqual(['system.firewall']);
  });

  it('offers "Geri al" for applied configuration changes, "İptal et" before they start', () => {
    expect(revertible(request())).toBe(true);
    expect(revertible(request({ state: 'partially_active' }))).toBe(true);
    expect(revertible(request({ version: null }))).toBe(false);
    expect(revertible(request({ state: 'failed' }))).toBe(false);
    expect(revertible(request({ kind: 'action.seal' }))).toBe(false);
    expect(cancellable(request({ state: 'queued' }))).toBe(true);
    expect(cancellable(request({ state: 'waiting_for_agent' }))).toBe(true);
    expect(cancellable(request({ state: 'pending' }))).toBe(true);
    expect(cancellable(request({ state: 'applying' }))).toBe(false);
    expect(cancellable(request({ state: 'received' }))).toBe(false);
    // A cancellation itself is not cancelled.
    expect(cancellable(request({ kind: 'config.cancel', state: 'queued' }))).toBe(false);
  });

  it("lists a source's changed fields one by one (the API sends its whole table)", () => {
    // The captured dry run of a heartbeat change, an addition and a removal.
    expect(changeRows(ADMIN.sources.check_update.changes)).toEqual([
      ['sources.erisim-noktalari.heartbeat_minutes', { old: 60, new: 30 }],
    ]);
    const added = changeRows(ADMIN.sources.check_add.changes);
    expect(added.map(([path]) => path)).toEqual([
      'sources.erisim-noktalari.address',
      'sources.erisim-noktalari.enabled',
      'sources.erisim-noktalari.expected',
      'sources.erisim-noktalari.heartbeat_minutes',
      'sources.erisim-noktalari.transport',
      'sources.erisim-noktalari.type',
    ]);
    expect(added[0]?.[1]).toEqual({ old: null, new: '172.20.20.0/24' });
    expect(changeRows(ADMIN.sources.remove_accepted.changes)[0]?.[1]).toEqual({
      old: '172.20.20.0/24',
      new: null,
    });
    // Every other change stays as it is.
    expect(changeRows(ADMIN.check_general.changes)).toEqual(
      Object.entries(ADMIN.check_general.changes),
    );
  });

  it('writes values of a change list as text', () => {
    expect(changeValueText(null)).toBe('—');
    expect(changeValueText(['a', 'b'])).toBe('a, b');
    expect(changeValueText([])).toBe('—');
    expect(changeValueText(true)).toBe('✓');
    expect(changeValueText(760)).toBe('760');
    expect(changeValueText({ days: [1] })).toBe('{"days":[1]}');
  });
});

describe('texts of the requests (Turkish and English)', () => {
  it.each([
    ['admin.steps', STEPS],
    ['admin.kinds', KINDS],
    ['admin.stepStates', ['pending', 'running', 'done', 'skipped', 'warning', 'failed']],
    ['admin.sections', ['general', 'tsa', 'smtp', 'replica', 'retention', 'backup', 'sources']],
    ['admin.agent.states', ['ok', 'stale', 'missing', 'disabled']],
  ] as const)('%s has a label for every name', (prefix, names) => {
    for (const locale of [tr, en])
      for (const name of names)
        expect(typeof lookup(locale, `${prefix}.${name}`), `${prefix}.${name}`).toBe('string');
  });
});
