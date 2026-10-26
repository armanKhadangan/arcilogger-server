import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fieldProblems,
  isTerminal,
  sectionState,
  type FlowPhase,
  type SectionState,
} from '@/lib/admin-requests';
import { isApiError } from '@/lib/api';
import { useAdminRequest } from '@/lib/api-hooks';
import { useReauth } from '@/lib/reauth';
import type {
  AdminAccepted,
  AdminCheck,
  AdminRequestOut,
  AgentState,
  FieldProblem,
} from '@/lib/settings-types';

export interface SectionFlowOptions<B> {
  /** `POST /admin/config/{section}/check`; `null`: the change is confirmed without a dry run. */
  check: ((body: B) => Promise<AdminCheck>) | null;
  /** The change itself (202 with the request to follow). */
  submit: (body: B) => Promise<AdminAccepted>;
  /**
   * The configuration paths the form shows at one of its fields (`fieldMessage`) for a body; a
   * refusal that names none of them (`sources`, `tsa.provider`, …) is shown above the form.
   */
  fields?: (body: B) => readonly string[];
}

export interface SectionFlow<B> {
  phase: FlowPhase;
  /** The dry run's answer while the change waits for its confirmation. */
  check: AdminCheck | null;
  /** The body waiting for its confirmation. */
  body: B | null;
  requestId: string | null;
  request: AdminRequestOut | undefined;
  /** Fields the API refused (422 `details`). */
  problems: FieldProblem[];
  /** Any other refusal of the check or the change (shown above the form). */
  failure: unknown;
  /** 409 `config_conflict`: someone else changed the section meanwhile. */
  conflict: boolean;
  /** Run the dry run, then ask for the confirmation. */
  start: (body: B) => Promise<void>;
  /** Send the confirmed change (`extra`: fields typed in the dialog); re-auth when asked. */
  confirm: (extra?: Partial<B>) => Promise<void>;
  /** Close the confirmation without sending. */
  dismiss: () => void;
  /** The user edits again: a finished request is no longer the section's state. */
  edit: () => void;
  /** Forget the conflict and the last request (after the configuration was reloaded). */
  reset: () => void;
}

/**
 * The save flow of a settings section (contract 2.17): check → confirm → (re-auth →) submit →
 * follow the request until it is terminal. The form keeps its own values; the flow only knows
 * the body it sends.
 */
export function useSectionFlow<B>({
  check,
  submit,
  fields: shownFields,
}: SectionFlowOptions<B>): SectionFlow<B> {
  const { confirm: reauthenticate } = useReauth();
  const [phase, setPhase] = useState<FlowPhase>('edit');
  const [waiting, setWaiting] = useState<{ body: B; check: AdminCheck | null } | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [problems, setProblems] = useState<FieldProblem[]>([]);
  const [failure, setFailure] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);
  const request = useAdminRequest(requestId).data;

  const refuse = useCallback(
    (error: unknown, body: B) => {
      setWaiting(null);
      setPhase('edit');
      if (isApiError(error, 'config_conflict')) {
        setConflict(true);
        return;
      }
      const fields = fieldProblems(error);
      setProblems(fields);
      // A field problem is shown at its field; the general text when none names a field shown.
      const shown = shownFields?.(body) ?? [];
      setFailure(fields.some((item) => shown.includes(item.field)) ? null : error);
    },
    [shownFields],
  );

  const start = useCallback(
    async (body: B) => {
      setProblems([]);
      setFailure(null);
      setRequestId(null);
      if (!check) {
        setWaiting({ body, check: null });
        setPhase('confirm');
        return;
      }
      setPhase('checking');
      try {
        const result = await check(body);
        setWaiting({ body, check: result });
        setPhase('confirm');
      } catch (error) {
        refuse(error, body);
      }
    },
    [check, refuse],
  );

  const confirm = useCallback(
    async (extra?: Partial<B>) => {
      if (!waiting) return;
      const body = { ...waiting.body, ...extra };
      setPhase('submitting');
      try {
        let accepted: AdminAccepted;
        try {
          accepted = await submit(body);
        } catch (error) {
          if (!isApiError(error, 'reauth_required')) throw error;
          setPhase('reauth');
          if (!(await reauthenticate())) {
            setPhase('confirm');
            return;
          }
          setPhase('submitting');
          accepted = await submit(body);
        }
        setWaiting(null);
        setRequestId(accepted.request_id);
        setPhase('tracking');
      } catch (error) {
        refuse(error, body);
      }
    },
    [waiting, submit, reauthenticate, refuse],
  );

  const dismiss = useCallback(() => {
    setWaiting(null);
    setPhase('edit');
  }, []);

  const finished = request !== undefined && isTerminal(request.state);
  const edit = useCallback(() => {
    if (phase === 'tracking' && finished) {
      setPhase('edit');
      setRequestId(null);
    }
    setProblems([]);
  }, [phase, finished]);

  const reset = useCallback(() => {
    setConflict(false);
    setFailure(null);
    setProblems([]);
    setRequestId(null);
    setPhase('edit');
  }, []);

  return {
    phase,
    check: waiting?.check ?? null,
    body: waiting?.body ?? null,
    requestId,
    request,
    problems,
    failure,
    conflict,
    start,
    confirm,
    dismiss,
    edit,
    reset,
  };
}

/** The section state of a flow (contract 2.17), with the page's own facts. */
export function flowState<B>(
  flow: SectionFlow<B>,
  facts: {
    loading: boolean;
    canEdit: boolean;
    agent: AgentState | null;
    dirty: boolean;
    invalid: boolean;
    restartRequired: boolean;
  },
): SectionState {
  return sectionState({
    ...facts,
    invalid: facts.invalid || flow.problems.length > 0,
    phase: flow.phase,
    request: flow.request ?? null,
    conflict: flow.conflict,
  });
}

/**
 * The form's values, filled from the server's and kept while the user edits: a new answer of
 * the server (a reload, the value someone else saved) replaces them only when nothing is
 * changed. `dirty`: the values differ from the server's.
 */
export function useServerValues<T>(server: T | undefined, empty: T) {
  const [values, setValues] = useState<T>(server ?? empty);
  const shown = useRef<string>(JSON.stringify(server ?? empty));
  const serverText = JSON.stringify(server ?? empty);
  const dirty = JSON.stringify(values) !== serverText;
  useEffect(() => {
    if (server === undefined) return;
    if (JSON.stringify(values) === shown.current) setValues(server);
    shown.current = JSON.stringify(server);
    // Only a new answer of the server is a reason to look again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverText]);
  const restore = useCallback(() => {
    if (server !== undefined) setValues(server);
  }, [server]);
  return { values, setValues, dirty: server !== undefined && dirty, restore };
}
