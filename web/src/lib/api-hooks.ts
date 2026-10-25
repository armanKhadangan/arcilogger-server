/**
 * TanStack Query hooks, one per endpoint of the auth, users, audit, 5651 lookup, evidence,
 * sealing, credit, devices/persons, dashboard, security alert, scheduled report and usage API.
 *
 * Conventions:
 *  - Sign-in steps pass `silent`: their 401/403 answers are part of the flow (wrong code,
 *    locked account) and are shown in place instead of triggering the global redirect.
 *  - One-time secrets (TOTP setup secret, recovery codes, temporary passwords, evidence package
 *    passwords) are never kept in the query cache longer than the screen that shows them
 *    (`gcTime: 0`, mutation results).
 *  - User-management mutations invalidate the user list; a 403 `reauth_required` is handled by
 *    the caller with `useReauth()` (`withReauth(() => mutation.mutateAsync(...))`).
 *  - Reads that the server audits (`GET /lookup/{id}`: `lookup.view`, `GET /raw`: `raw.view`)
 *    are fetched once per screen (`staleTime: Infinity`), never refreshed in the background.
 *  - Widgets that refresh themselves use `usePollingQuery` (`X-Arci-Background: 1`).
 *  - Devices and persons: a change invalidates every list that shows it (a person's name is in
 *    the device list, a VLAN's department in a device's effective department, …).
 *  - Usage: whole lists are read in one request (`allPages`); answers whose period includes today
 *    refresh every 5 minutes in the background; a finished rebuild invalidates `['usage']`.
 *  - Licence: one shared `GET /license` (fresh for 60 s, refreshed every 5 minutes in the
 *    background); installing or removing a licence invalidates every query.
 */

import {
  hashKey,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from '@tanstack/react-query';
import { useCallback, useRef } from 'react';

import { api, type QueryValue } from './api';
import type {
  AlertDetail,
  AlertEvent,
  AlertFilters,
  AlertNoteRequest,
  AlertPage,
  AlertStateRequest,
  AlertSummary,
  AssignRequest,
  AssignResult,
  AuditFilters,
  AuditPage,
  Calendar,
  CategoryDetail,
  CategoryList,
  ChainStatus,
  CodeBody,
  CompanyUsage,
  DepartmentPage,
  CreatedUser,
  Credits,
  DashboardSummary,
  DepartmentCreate,
  DepartmentOut,
  DepartmentUpdate,
  DeviceDetail,
  DeviceFilters,
  DeviceOut,
  DevicePage,
  DeviceUpdate,
  EvidenceCreate,
  EvidenceCreated,
  EvidenceDetail,
  EvidencePage,
  ImportKind,
  ImportResult,
  LicenseOut,
  LoginBody,
  LookupFilters,
  LookupIn,
  LookupOut,
  LookupPage,
  Me,
  RawLineOut,
  PasswordBody,
  PersonCreate,
  PersonDetail,
  PersonFilters,
  PersonOut,
  PersonPage,
  PersonUpdate,
  PrivacyStatus,
  ReauthBody,
  Reauthenticated,
  RebuildJob,
  RebuildRequest,
  RecoveryCodes,
  Role,
  RunFilters,
  RunOut,
  RunPage,
  RunRequest,
  ScheduleCreate,
  ScheduleOut,
  ScheduleUpdate,
  SetupAdminBody,
  SetupAdminOut,
  SetupStatusOut,
  SignInState,
  SiteDetail,
  SitePage,
  SubjectDetail,
  SubjectPage,
  TemporaryPassword,
  TotpEnrolled,
  TotpSetup,
  UsageParams,
  UserCreate,
  UserOut,
  UserPage,
  UserUpdate,
  VerifyResult,
  VlanCreate,
  VlanOut,
  VlanUpdate,
} from './api-types';
import { siteToday } from './display';
import type { RawRef } from './lookup-types';
import type { SealDayDetail } from './seal-types';

export const AUDIT_PAGE_SIZE = 100;
export const USER_PAGE_SIZE = 500;
export const LOOKUP_PAGE_SIZE = 50;
/** Newest packages listed (they expire after `evidence.keep_days`, default 30 days). */
export const EVIDENCE_LIST_SIZE = 200;
/** Refresh while an evidence package is queued or being built. */
export const EVIDENCE_POLL_MS = 3_000;
export const SEAL_POLL_MS = 60_000;
export const CREDIT_POLL_MS = 300_000;
/** Package states that still change on their own. */
export const UNFINISHED_STATUSES: readonly string[] = ['queued', 'building'];

export const queryKeys = {
  me: ['auth', 'me'] as const,
  setupStatus: ['auth', 'setup-status'] as const,
  totpSetup: ['auth', 'totp-setup'] as const,
  users: (filters: UserFilters) => ['users', filters] as const,
  usersAll: ['users'] as const,
  audit: (filters: AuditFilters) => ['audit', 'entries', filters] as const,
  auditVerify: ['audit', 'verify'] as const,
  lookup: (id: number) => ['lookups', 'one', id] as const,
  lookups: (filters: LookupFilters) => ['lookups', 'list', filters] as const,
  lookupLists: ['lookups', 'list'] as const,
  raw: (ref: RawRef) => ['raw', ref.source, ref.day, ref.line, ref.offset ?? null] as const,
  evidenceList: ['evidence', 'list'] as const,
  evidence: (id: number) => ['evidence', 'one', id] as const,
  evidenceAll: ['evidence'] as const,
  sealCalendar: (month: string | null) => ['seals', 'calendar', month] as const,
  sealDay: (day: string) => ['seals', 'day', day] as const,
  sealsAll: ['seals'] as const,
  credits: ['credits'] as const,
  devices: (filters: DeviceFilters) => ['devices', 'list', filters] as const,
  device: (mac: string) => ['devices', 'one', mac] as const,
  devicesAll: ['devices'] as const,
  persons: (filters: PersonFilters) => ['persons', 'list', filters] as const,
  person: (id: number) => ['persons', 'one', id] as const,
  personsAll: ['persons'] as const,
  departments: ['departments'] as const,
  vlans: ['vlans'] as const,
  usage: (what: string, params: Record<string, QueryValue>) => ['usage', what, params] as const,
  usageAll: ['usage'] as const,
  rebuild: (jobId: number) => ['usage-rebuild', jobId] as const,
  dashboard: ['dashboard', 'summary'] as const,
  todayUsage: ['usage', 'company', 'today'] as const,
  alerts: (filters: AlertFilters) => ['alerts', 'list', filters] as const,
  alert: (id: number) => ['alerts', 'one', id] as const,
  alertSummary: ['alerts', 'summary'] as const,
  alertsAll: ['alerts'] as const,
  schedules: ['reports', 'schedules'] as const,
  runs: (filters: RunFilters) => ['reports', 'runs', filters] as const,
  run: (id: number) => ['reports', 'run', id] as const,
  reportsAll: ['reports'] as const,
  license: ['license'] as const,
};

// -- auth ---------------------------------------------------------------------------------------

/** The signed-in user; 401 (no session) is an answer here, not an error to react to. */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.get<Me>('/auth/me', { signal, silent: true }),
    retry: false,
    staleTime: 60_000,
  });
}

export function useSetupStatus() {
  return useQuery({
    queryKey: queryKeys.setupStatus,
    queryFn: ({ signal }) =>
      api.get<SetupStatusOut>('/auth/setup/status', { signal, silent: true }),
    retry: false,
  });
}

/** After a sign-in step: read `/auth/me` again (the step decides what comes next). */
function useRefreshMe() {
  const client = useQueryClient();
  return useCallback(() => client.invalidateQueries({ queryKey: queryKeys.me }), [client]);
}

export function useLogin() {
  const refreshMe = useRefreshMe();
  return useMutation({
    mutationFn: (body: LoginBody) => api.post<SignInState>('/auth/login', body, { silent: true }),
    onSuccess: refreshMe,
  });
}

export function useTotpStep() {
  const refreshMe = useRefreshMe();
  return useMutation({
    mutationFn: (body: CodeBody) => api.post<SignInState>('/auth/totp', body, { silent: true }),
    onSuccess: refreshMe,
  });
}

/** QR code and secret of a pending TOTP enrolment (dropped from the cache when unused). */
export function useTotpSetup(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.totpSetup,
    queryFn: ({ signal }) => api.get<TotpSetup>('/auth/totp/setup', { signal, silent: true }),
    enabled,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });
}

/** Confirms the enrolment; the recovery codes are in the result only (shown once). */
export function useTotpConfirm() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CodeBody) =>
      api.post<TotpEnrolled>('/auth/totp/confirm', body, { silent: true }),
    onSuccess: () => {
      client.removeQueries({ queryKey: queryKeys.totpSetup });
    },
  });
}

export function useChangePassword() {
  const refreshMe = useRefreshMe();
  return useMutation({
    mutationFn: (body: PasswordBody) =>
      api.post<SignInState>('/auth/password', body, { silent: true }),
    onSuccess: refreshMe,
  });
}

/** `POST /auth/reauth`. Not silent: a 401 (account locked, session closed) leaves the page. */
export function useReauthMutation() {
  const refreshMe = useRefreshMe();
  return useMutation({
    mutationFn: (body: ReauthBody) => api.post<Reauthenticated>('/auth/reauth', body),
    onSuccess: refreshMe,
  });
}

/** New recovery codes (the old ones stop working); needs a recent re-authentication. */
export function useRegenerateRecoveryCodes() {
  const refreshMe = useRefreshMe();
  return useMutation({
    mutationFn: () => api.post<RecoveryCodes>('/auth/recovery-codes'),
    onSuccess: refreshMe,
  });
}

export function useAcceptPrivacyNotice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (version: string) => api.post<PrivacyStatus>('/auth/privacy-notice', { version }),
    onSuccess: (status) => {
      client.setQueryData<Me>(queryKeys.me, (me) =>
        me
          ? { ...me, privacy_notice: status, next: me.next === 'privacy_notice' ? 'done' : me.next }
          : me,
      );
      // Everything: requests refused with 403 `privacy_notice_required` are asked again.
      void client.invalidateQueries();
    },
    // 409 `notice_outdated`: a newer version exists; /auth/me brings its number.
    onError: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  });
}

/**
 * Creates the first administrator. Call `markSetupDone()` on success: the cached status then says
 * so at once, so the sign-in page does not send the browser back to /setup. The setup page must
 * test `isSuccess` before the cached status (it renders the redirect with the "created" notice);
 * otherwise the status check's redirect to a bare /login can overtake it.
 */
export function useSetupAdmin() {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body: SetupAdminBody) =>
      api.post<SetupAdminOut>('/auth/setup/admin', body, { silent: true }),
  });
  const markSetupDone = useCallback(() => {
    // The setup page read the status before; keep its idle minutes.
    client.setQueryData<SetupStatusOut>(queryKeys.setupStatus, (current) =>
      current ? { ...current, needs_setup: false, token_active: false } : current,
    );
  }, [client]);
  return { ...mutation, markSetupDone };
}

// -- users --------------------------------------------------------------------------------------

export interface UserFilters {
  role?: Role | undefined;
  active?: boolean | undefined;
}

export function useUsers(filters: UserFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.users(filters),
    queryFn: ({ signal, pageParam }) =>
      api.get<UserPage>('/users', {
        signal,
        params: {
          limit: USER_PAGE_SIZE,
          cursor: pageParam,
          role: filters.role,
          active: filters.active,
        },
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });
}

function useUserMutation<TVariables, TResult>(fn: (variables: TVariables) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    // The licence's users counter (`limits.users.used`) follows creates and (de)activations.
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.usersAll }),
        client.invalidateQueries({ queryKey: queryKeys.license }),
      ]),
  });
}

export function useCreateUser() {
  return useUserMutation((body: UserCreate) => api.post<CreatedUser>('/users', body));
}

export function useUpdateUser() {
  return useUserMutation(({ id, ...body }: UserUpdate & { id: number }) =>
    api.patch<UserOut>(`/users/${id}`, body),
  );
}

export function useResetPassword() {
  return useUserMutation((id: number) =>
    api.post<TemporaryPassword>(`/users/${id}/reset-password`),
  );
}

export function useResetTotp() {
  return useUserMutation((id: number) => api.post<UserOut>(`/users/${id}/reset-totp`));
}

export function useUnlockUser() {
  return useUserMutation((id: number) => api.post<UserOut>(`/users/${id}/unlock`));
}

// -- audit --------------------------------------------------------------------------------------

export function useAuditEntries(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: ({ signal, pageParam }) =>
      api.get<AuditPage>('/audit', {
        signal,
        params: { ...filters, limit: AUDIT_PAGE_SIZE, cursor: pageParam },
      }),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  });
}

/** Chain verification of the whole log (spec 8.5: "verifies the chain on load"). */
export function useAuditVerify() {
  return useQuery({
    queryKey: queryKeys.auditVerify,
    queryFn: ({ signal }) => api.get<ChainStatus>('/audit/verify', { signal }),
    staleTime: 0,
    retry: false,
  });
}

// -- polling ------------------------------------------------------------------------------------

interface PollingOptions<T> {
  queryKey: QueryKey;
  path: string;
  params?: Record<string, QueryValue>;
  /**
   * Refetch interval in ms (e.g. 30 000 for live dashboard widgets), or a function of the data
   * on screen (`false`: stop, e.g. once no evidence package is being built any more).
   */
  intervalMs: number | ((data: T | undefined) => number | false);
  enabled?: boolean;
  /**
   * `path` answers pages (`{items, total, limit, offset, …}`): read all of them
   * ({@link fetchAllPages}) and answer one page with every item.
   */
  allPages?: boolean;
  /** Keep the previous answer on screen while another key (page, filter) loads. */
  keepPrevious?: boolean;
}

/**
 * Rows per request when a whole list is read: the largest page of `/usage/subjects` and
 * `/usage/sites` (`MAX_LIMIT` in `routers/usage.py`), so a list comes in one answer and the
 * server aggregates the period once instead of once per page.
 */
export const ALL_PAGES_LIMIT = 100_000;
/** At most this many requests for one list (100 000 rows). */
export const ALL_PAGES_MAX = 1;

interface AnyPage {
  items: unknown[];
  total: number;
  limit: number;
  offset: number;
}

/** Every page of a paged endpoint, as one page (`limit` = the number of items). */
export async function fetchAllPages<T extends AnyPage>(
  path: string,
  params: Record<string, QueryValue> | undefined,
  options: { signal?: AbortSignal; background?: boolean },
): Promise<T> {
  const items: unknown[] = [];
  let first: T | null = null;
  for (let request = 0; request < ALL_PAGES_MAX; request += 1) {
    const page = await api.get<T>(path, {
      ...options,
      params: { ...params, limit: ALL_PAGES_LIMIT, offset: items.length },
    });
    first ??= page;
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) break;
  }
  if (!first) throw new Error(`no answer from ${path}`);
  return { ...first, items, limit: items.length, offset: 0 };
}

/**
 * A query that refreshes itself every `intervalMs`. The first load (the user opened the page),
 * the first load after the user changed the query (another day, another filter: a new key) and
 * `refresh()` (the user clicked) count as activity; only the refetches of the key already shown
 * are sent with `X-Arci-Background: 1`, so an unattended dashboard does not keep the session alive.
 */
export function usePollingQuery<T>({
  queryKey,
  path,
  params,
  intervalMs,
  enabled = true,
  allPages = false,
  keepPrevious = false,
}: PollingOptions<T>) {
  /** Hash of the key whose answer is on screen; a fetch for any other key is the user's doing. */
  const shownKey = useRef<string | null>(null);
  const userAsked = useRef(false);
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal, queryKey: key }) => {
      const hash = hashKey(key);
      const background = shownKey.current === hash && !userAsked.current;
      userAsked.current = false;
      try {
        return allPages
          ? ((await fetchAllPages<AnyPage>(path, params, { signal, background })) as T)
          : await api.get<T>(path, { signal, params, background });
      } finally {
        // An error is an answer on screen too: retrying it on its own is not the user's doing
        // (else a widget whose endpoint keeps failing would keep the session alive).
        shownKey.current = hash;
      }
    },
    refetchInterval:
      typeof intervalMs === 'function' ? (current) => intervalMs(current.state.data) : intervalMs,
    // A hidden tab does not poll (and so does not keep the server busy for nobody).
    refetchIntervalInBackground: false,
    enabled,
    ...(keepPrevious ? { placeholderData: keepPreviousData } : {}),
  });
  const { refetch } = query;
  const refresh = useCallback(() => {
    userAsked.current = true;
    return refetch();
  }, [refetch]);
  return { ...query, refresh };
}

// -- 5651 lookup ----------------------------------------------------------------------------------

/** `POST /lookup/5651`: the answer (with the outcome) becomes the cached lookup of its id. */
export function useRunLookup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: LookupIn) => api.post<LookupOut>('/lookup/5651', body),
    onSuccess: (lookup) => {
      client.setQueryData(queryKeys.lookup(lookup.id), lookup);
      void client.invalidateQueries({ queryKey: queryKeys.lookupLists });
    },
  });
}

/** One stored lookup. The server audits every read (`lookup.view`): fetched once per screen. */
export function useLookup(id: number | null) {
  return useQuery({
    queryKey: queryKeys.lookup(id ?? 0),
    queryFn: ({ signal }) => api.get<LookupOut>(`/lookup/${String(id)}`, { signal }),
    enabled: id !== null,
    staleTime: Infinity,
    retry: false,
  });
}

/** Earlier lookups, newest first (administrators: everyone's unless `mine`). */
export function useLookups(filters: LookupFilters, enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.lookups(filters),
    queryFn: ({ signal, pageParam }) =>
      api.get<LookupPage>('/lookups', {
        signal,
        params: { ...filters, limit: LOOKUP_PAGE_SIZE, cursor: pageParam },
      }),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** One raw log line (audited `raw.view` on the server: read once, kept while the page lives). */
export function useRawLine(ref: RawRef | null) {
  return useQuery({
    queryKey: ref ? queryKeys.raw(ref) : ['raw', 'none'],
    queryFn: ({ signal }) =>
      api.get<RawLineOut>('/raw', {
        signal,
        params: {
          source: ref?.source,
          day: ref?.day,
          line: ref?.line,
          offset: ref?.offset ?? undefined,
        },
      }),
    enabled: ref !== null,
    staleTime: Infinity,
    retry: false,
  });
}

// -- evidence packages ----------------------------------------------------------------------------

function unfinished(statuses: readonly string[]): boolean {
  return statuses.some((status) => UNFINISHED_STATUSES.includes(status));
}

/** The newest packages; refreshed every few seconds while one is queued or being built. */
export function useEvidencePackages() {
  return usePollingQuery<EvidencePage>({
    queryKey: queryKeys.evidenceList,
    path: '/evidence',
    params: { limit: EVIDENCE_LIST_SIZE },
    intervalMs: (page) =>
      page && unfinished(page.items.map((item) => item.status)) ? EVIDENCE_POLL_MS : false,
  });
}

/** One package with its downloads; refreshed while it is queued or being built. */
export function useEvidencePackage(id: number | null) {
  return usePollingQuery<EvidenceDetail>({
    queryKey: queryKeys.evidence(id ?? 0),
    path: `/evidence/${String(id)}`,
    enabled: id !== null,
    intervalMs: (detail) => (detail && unfinished([detail.status]) ? EVIDENCE_POLL_MS : false),
  });
}

/**
 * `POST /evidence` (re-authentication within 5 minutes: wrap in `withReauth`). The one-time
 * password is in the mutation result only, which is dropped as soon as nobody shows it.
 */
export function useCreateEvidence() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: EvidenceCreate) => api.post<EvidenceCreated>('/evidence', body),
    gcTime: 0,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.evidenceList }),
  });
}

// -- sealing --------------------------------------------------------------------------------------

/** The month's seal states (`null`: the server's current month). */
export function useSealCalendar(month: string | null) {
  return usePollingQuery<Calendar>({
    queryKey: queryKeys.sealCalendar(month),
    path: '/seals/calendar',
    params: { month },
    intervalMs: SEAL_POLL_MS,
  });
}

export function useSealDay(day: string | null) {
  return usePollingQuery<SealDayDetail>({
    queryKey: queryKeys.sealDay(day ?? ''),
    path: `/seals/${String(day)}`,
    enabled: day !== null,
    intervalMs: SEAL_POLL_MS,
  });
}

/** "Doğrula": the core day check on the server (up to 300 s). */
export function useVerifySeal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ day, deep }: { day: string; deep: boolean }) =>
      api.post<VerifyResult>(`/seals/${day}/verify`, undefined, { params: { deep } }),
    onSettled: () => client.invalidateQueries({ queryKey: queryKeys.sealsAll }),
  });
}

/** Kamu SM credit history of the scheduled Credit task. */
export function useCredits() {
  return usePollingQuery<Credits>({
    queryKey: queryKeys.credits,
    path: '/credits',
    intervalMs: CREDIT_POLL_MS,
  });
}

// -- devices and persons --------------------------------------------------------------------------

/** Rows per page of the device and person tables (the API allows up to 500). */
export const DEVICE_PAGE_SIZE = 50;
/** Persons the person picker offers per search. */
export const PERSON_PICKER_SIZE = 20;

/**
 * One page of devices (filtered, sorted and paged by the server). Pass `enabled: false` while the
 * KVKK notice is not accepted: the list holds owners' names (403 `privacy_notice_required`).
 */
export function useDevices(filters: DeviceFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.devices(filters),
    queryFn: ({ signal }) => api.get<DevicePage>('/devices', { signal, params: { ...filters } }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** One device with its assignment history and newest leases. */
export function useDevice(mac: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.device(mac ?? ''),
    queryFn: ({ signal }) =>
      api.get<DeviceDetail>(`/devices/${encodeURIComponent(mac ?? '')}`, { signal }),
    enabled: enabled && mac !== null,
    retry: false,
  });
}

/** Lists that show devices or persons (an assignment changes both). */
const DEVICE_KEYS: readonly QueryKey[] = [queryKeys.devicesAll, queryKeys.personsAll];
/** And departments and VLANs (person counts, a VLAN's department in the effective one). */
const ALL_DEVICE_KEYS: readonly QueryKey[] = [
  ...DEVICE_KEYS,
  queryKeys.departments,
  queryKeys.vlans,
];
/** And the Kullanım answers: a person's `usage_excluded` applies as soon as it is read. */
const PERSON_KEYS: readonly QueryKey[] = [...ALL_DEVICE_KEYS, queryKeys.usageAll];

function useDeviceDataMutation<TVariables, TResult>(
  fn: (variables: TVariables) => Promise<TResult>,
  keys: readonly QueryKey[],
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })));
    },
  });
}

export function useUpdateDevice() {
  return useDeviceDataMutation(
    ({ mac, ...body }: DeviceUpdate & { mac: string }) =>
      api.patch<DeviceOut>(`/devices/${encodeURIComponent(mac)}`, body),
    DEVICE_KEYS,
  );
}

/** "Kişiye ata" / "Atamayı kaldır" for one or more devices (atomic on the server). */
export function useAssignDevices() {
  return useDeviceDataMutation(
    (body: AssignRequest) => api.post<AssignResult>('/devices/assign', body),
    DEVICE_KEYS,
  );
}

/** One page of persons; `enabled: false` while the KVKK notice is not accepted. */
export function usePersons(filters: PersonFilters, enabled = true) {
  return useQuery({
    queryKey: queryKeys.persons(filters),
    queryFn: ({ signal }) => api.get<PersonPage>('/persons', { signal, params: { ...filters } }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** One person with their devices and assignment history. */
export function usePerson(id: number | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.person(id ?? 0),
    queryFn: ({ signal }) => api.get<PersonDetail>(`/persons/${String(id)}`, { signal }),
    enabled: enabled && id !== null,
    retry: false,
  });
}

export function useCreatePerson() {
  return useDeviceDataMutation(
    (body: PersonCreate) => api.post<PersonOut>('/persons', body),
    PERSON_KEYS,
  );
}

/** Also deactivation (`active: false`): persons are never deleted, their history stays. */
export function useUpdatePerson() {
  return useDeviceDataMutation(
    ({ id, ...body }: PersonUpdate & { id: number }) =>
      api.patch<PersonOut>(`/persons/${id}`, body),
    PERSON_KEYS,
  );
}

export function useDepartments(enabled = true) {
  return useQuery({
    queryKey: queryKeys.departments,
    queryFn: ({ signal }) => api.get<DepartmentOut[]>('/departments', { signal }),
    enabled,
  });
}

export function useCreateDepartment() {
  return useDeviceDataMutation(
    (body: DepartmentCreate) => api.post<DepartmentOut>('/departments', body),
    ALL_DEVICE_KEYS,
  );
}

export function useUpdateDepartment() {
  return useDeviceDataMutation(
    ({ id, ...body }: DepartmentUpdate & { id: number }) =>
      api.patch<DepartmentOut>(`/departments/${id}`, body),
    ALL_DEVICE_KEYS,
  );
}

export function useVlans(enabled = true) {
  return useQuery({
    queryKey: queryKeys.vlans,
    queryFn: ({ signal }) => api.get<VlanOut[]>('/vlans', { signal }),
    enabled,
  });
}

/** "VLAN ekle": a VLAN/subnet of the site (ARCILOGGER ships none). */
export function useCreateVlan() {
  return useDeviceDataMutation(
    (body: VlanCreate) => api.post<VlanOut>('/vlans', body),
    ALL_DEVICE_KEYS,
  );
}

/** A VLAN's fields, only those sent; `{subnet, department_id}` changes just its department. */
export function useUpdateVlan() {
  return useDeviceDataMutation(
    (body: VlanUpdate) => api.patch<VlanOut>('/vlans', body),
    ALL_DEVICE_KEYS,
  );
}

/** "VLAN'ı sil": the devices of its network lose the VLAN's name and department. */
export function useDeleteVlan() {
  return useDeviceDataMutation(
    (subnet: string) => api.delete<undefined>('/vlans', { params: { subnet } }),
    ALL_DEVICE_KEYS,
  );
}

export interface ImportRequest {
  kind: ImportKind;
  file: File;
  /** `true`: only check and show what would change (nothing is written). */
  dryRun: boolean;
}

/** `POST /imports/{kind}`: the CSV file as the multipart field `file`. */
export function useImportCsv() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, file, dryRun }: ImportRequest) => {
      const form = new FormData();
      form.append('file', file, file.name);
      return api.post<ImportResult>(`/imports/${kind}`, form, { params: { dry_run: dryRun } });
    },
    onSuccess: async (result, { kind }) => {
      if (result.dry_run) return;
      const keys = kind === 'persons' ? PERSON_KEYS : ALL_DEVICE_KEYS;
      await Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })));
    },
  });
}

// -- dashboard ------------------------------------------------------------------------------------

/** Live widgets of the dashboard (ingest, seal, credit, clock, alerts): spec 12.3 item 2. */
export const DASHBOARD_POLL_MS = 30_000;
/** Usage figures change slowly (the rollups run every few minutes): spec 12.3 item 2. */
export const USAGE_POLL_MS = 300_000;

/** `GET /dashboard/summary` (`dashboard.view`); every block is `null` without its permission. */
export function useDashboardSummary(enabled = true) {
  return usePollingQuery<DashboardSummary>({
    queryKey: queryKeys.dashboard,
    path: '/dashboard/summary',
    intervalMs: DASHBOARD_POLL_MS,
    enabled,
  });
}

/** Today's company totals (`GET /usage/company` without a period: the server's today). */
export function useTodayCompanyUsage(enabled = true) {
  return usePollingQuery<CompanyUsage>({
    queryKey: queryKeys.todayUsage,
    path: '/usage/company',
    intervalMs: USAGE_POLL_MS,
    enabled,
  });
}

// -- security alerts ------------------------------------------------------------------------------

export const ALERT_PAGE_SIZE = 50;
export const ALERTS_POLL_MS = 30_000;

/** One page of alerts (filtered, sorted and paged by the server), refreshed like the dashboard. */
export function useAlerts(filters: AlertFilters) {
  return usePollingQuery<AlertPage>({
    queryKey: queryKeys.alerts(filters),
    path: '/alerts',
    params: { ...filters },
    intervalMs: ALERTS_POLL_MS,
    keepPrevious: true,
  });
}

/** "Güvenlik Özeti": open alerts by severity, being investigated, mean time to close. */
export function useAlertSummary(enabled = true) {
  return usePollingQuery<AlertSummary>({
    queryKey: queryKeys.alertSummary,
    path: '/alerts/summary',
    intervalMs: ALERTS_POLL_MS,
    enabled,
  });
}

/** One alert with its details, history, runbook and e-mails. */
export function useAlert(id: number | null) {
  return usePollingQuery<AlertDetail>({
    queryKey: queryKeys.alert(id ?? 0),
    path: `/alerts/${String(id)}`,
    intervalMs: ALERTS_POLL_MS,
    enabled: id !== null,
  });
}

/** A state change shows on the dashboard, in the lists and in the summary. */
function useAlertRefresh() {
  const client = useQueryClient();
  return useCallback(
    () =>
      Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.alertsAll }),
        client.invalidateQueries({ queryKey: queryKeys.dashboard }),
      ]),
    [client],
  );
}

/** `POST /alerts/{id}/state` (`alerts.manage`); the answer is the alert as it is now. */
export function useChangeAlertState() {
  const client = useQueryClient();
  const refresh = useAlertRefresh();
  return useMutation({
    mutationFn: ({ id, ...body }: AlertStateRequest & { id: number }) =>
      api.post<AlertDetail>(`/alerts/${id}/state`, body),
    onSuccess: async (detail) => {
      client.setQueryData(queryKeys.alert(detail.id), detail);
      await refresh();
    },
  });
}

/** `POST /alerts/{id}/notes` (`alerts.manage`). */
export function useAddAlertNote() {
  const refresh = useAlertRefresh();
  return useMutation({
    mutationFn: ({ id, ...body }: AlertNoteRequest & { id: number }) =>
      api.post<AlertEvent>(`/alerts/${id}/notes`, body),
    onSuccess: refresh,
  });
}

// -- scheduled reports ----------------------------------------------------------------------------

export const RUN_PAGE_SIZE = 25;
/** Refresh while a run is queued or running. */
export const RUN_POLL_MS = 3_000;
/** Otherwise the lists still pick up the scheduled runs. */
export const REPORTS_IDLE_POLL_MS = 60_000;
export const UNFINISHED_RUNS: readonly string[] = ['queued', 'running'];

function runsPending(runs: readonly (RunOut | null)[]): boolean {
  return runs.some((run) => run !== null && UNFINISHED_RUNS.includes(run.status));
}

export function useReportSchedules() {
  return usePollingQuery<ScheduleOut[]>({
    queryKey: queryKeys.schedules,
    path: '/reports/schedules',
    intervalMs: (schedules) =>
      schedules && runsPending(schedules.map((item) => item.last_run))
        ? RUN_POLL_MS
        : REPORTS_IDLE_POLL_MS,
  });
}

export function useReportRuns(filters: RunFilters) {
  return usePollingQuery<RunPage>({
    queryKey: queryKeys.runs(filters),
    path: '/reports/runs',
    params: { ...filters },
    intervalMs: (page) => (page && runsPending(page.items) ? RUN_POLL_MS : REPORTS_IDLE_POLL_MS),
    keepPrevious: true,
  });
}

/** One run with its deliveries and files; refreshed while it is queued or running. */
export function useReportRun(id: number | null) {
  return usePollingQuery<RunOut>({
    queryKey: queryKeys.run(id ?? 0),
    path: `/reports/runs/${String(id)}`,
    enabled: id !== null,
    intervalMs: (run) => (run && runsPending([run]) ? RUN_POLL_MS : false),
  });
}

function useReportMutation<TVariables, TResult>(fn: (variables: TVariables) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.reportsAll }),
  });
}

export function useCreateSchedule() {
  return useReportMutation((body: ScheduleCreate) =>
    api.post<ScheduleOut>('/reports/schedules', body),
  );
}

export function useUpdateSchedule() {
  return useReportMutation(({ id, ...body }: ScheduleUpdate & { id: number }) =>
    api.patch<ScheduleOut>(`/reports/schedules/${id}`, body),
  );
}

/** Deletes the schedule only: its runs and their files are kept. */
export function useDeleteSchedule() {
  return useReportMutation((id: number) => api.delete<undefined>(`/reports/schedules/${id}`));
}

/** "Şimdi çalıştır": 202 with the queued run. */
export function useRunSchedule() {
  return useReportMutation(({ id, ...body }: RunRequest & { id: number }) =>
    api.post<RunOut>(`/reports/schedules/${id}/run`, body),
  );
}

// -- usage analytics ------------------------------------------------------------------------------

/** Progress of a running "Yeniden hesapla". */
export const REBUILD_POLL_MS = 2_000;

function usageInterval(params: UsageParams): () => number | false {
  const live = params.to >= siteToday();
  return () => (live ? USAGE_POLL_MS : false);
}

/** A path segment: subject keys (`device:aa:bb:…`), domains, category names, job ids. */
function segment(value: string | number | null): string {
  return encodeURIComponent(value ?? '');
}

/** The common parameters (`department` only when given) plus the endpoint's own. */
export function usageQuery(
  params: UsageParams,
  extra: Record<string, QueryValue> = {},
): Record<string, QueryValue> {
  return {
    from: params.from,
    to: params.to,
    hours: params.hours,
    department: params.department,
    ...extra,
  };
}

/** Company totals, hourly series, top sites/categories/apps and departments (no names). */
export function useUsageCompany(params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<CompanyUsage>({
    queryKey: queryKeys.usage('company', query),
    path: '/usage/company',
    params: query,
    intervalMs: usageInterval(params),
    enabled,
  });
}

/**
 * Every person or device (bare addresses included) of the period (all pages; the tables search
 * and sort in the browser). Person-level data: `enabled: false` while the KVKK notice is not
 * accepted.
 */
export function useUsageSubjects(kind: 'person' | 'device', params: UsageParams, enabled = true) {
  const query = usageQuery(params, { kind, sort: 'active_seconds', order: 'desc' });
  return usePollingQuery<SubjectPage>({
    queryKey: queryKeys.usage('subjects', query),
    path: '/usage/subjects',
    params: query,
    intervalMs: usageInterval(params),
    enabled,
    allPages: true,
  });
}

/** Every department of the period (`/usage/subjects?kind=department`, all pages). */
export function useUsageDepartments(params: UsageParams, enabled = true) {
  const query = usageQuery(params, { kind: 'department', sort: 'active_seconds', order: 'desc' });
  return usePollingQuery<DepartmentPage>({
    queryKey: queryKeys.usage('departments', query),
    path: '/usage/subjects',
    params: query,
    intervalMs: usageInterval(params),
    enabled,
    allPages: true,
  });
}

/** One person, device or IP address with every detail of the period. */
export function useUsageSubject(key: string | null, params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<SubjectDetail>({
    queryKey: queryKeys.usage(`subject:${key ?? ''}`, query),
    path: `/usage/subjects/${segment(key)}`,
    params: query,
    intervalMs: usageInterval(params),
    enabled: enabled && key !== null,
  });
}

/** Every site of the period (all pages). */
export function useUsageSites(params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<SitePage>({
    queryKey: queryKeys.usage('sites', query),
    path: '/usage/sites',
    params: query,
    intervalMs: usageInterval(params),
    enabled,
    allPages: true,
  });
}

/** One site: host names and who used it for how long. */
export function useUsageSite(domain: string | null, params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<SiteDetail>({
    queryKey: queryKeys.usage(`site:${domain ?? ''}`, query),
    path: `/usage/sites/${segment(domain)}`,
    params: query,
    intervalMs: usageInterval(params),
    enabled: enabled && domain !== null,
  });
}

export function useUsageCategories(params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<CategoryList>({
    queryKey: queryKeys.usage('categories', query),
    path: '/usage/categories',
    params: query,
    intervalMs: usageInterval(params),
    enabled,
  });
}

/** One category: its sites and users. */
export function useUsageCategory(category: string | null, params: UsageParams, enabled = true) {
  const query = usageQuery(params);
  return usePollingQuery<CategoryDetail>({
    queryKey: queryKeys.usage(`category:${category ?? ''}`, query),
    path: `/usage/categories/${segment(category)}`,
    params: query,
    intervalMs: usageInterval(params),
    enabled: enabled && category !== null,
  });
}

/** "Yeniden hesapla" (`settings.manage`): queues a rebuild job of the given site days. */
export function useStartRebuild() {
  return useMutation({
    mutationFn: (body: RebuildRequest) => api.post<RebuildJob>('/usage/rebuild', body),
  });
}

/** A rebuild job, polled until it is done or failed. */
export function useRebuildJob(jobId: number | null) {
  return usePollingQuery<RebuildJob>({
    queryKey: queryKeys.rebuild(jobId ?? 0),
    path: `/usage/rebuild/${segment(jobId)}`,
    enabled: jobId !== null,
    intervalMs: (job) =>
      job && (job.status === 'done' || job.status === 'failed') ? false : REBUILD_POLL_MS,
  });
}

// -- licence --------------------------------------------------------------------------------------

/** The licence state changes rarely; the API caches it for 30 s. */
export const LICENSE_STALE_MS = 60_000;
/** Refreshed every 5 minutes in the background (trial days, limits, a licence installed by CLI). */
export const LICENSE_POLL_MS = 300_000;

/**
 * `GET /license` (every signed-in user; `details` only with `settings.manage`): the state, the
 * modules with `enabled`, the limits. Many parts of the shell read it at once (banners, menu,
 * module gates), so the background header is decided from the shared cache, not per caller: the
 * first answer of a session counts as the user's request, every later refresh is background
 * (an open tab must not keep the session alive).
 */
export function useLicense() {
  const client = useQueryClient();
  return useQuery({
    queryKey: queryKeys.license,
    queryFn: ({ signal }) => {
      const state = client.getQueryState(queryKeys.license);
      const answered = state !== undefined && state.dataUpdatedAt + state.errorUpdatedAt > 0;
      return api.get<LicenseOut>('/license', { signal, background: answered });
    },
    staleTime: LICENSE_STALE_MS,
    refetchInterval: LICENSE_POLL_MS,
    refetchIntervalInBackground: false,
  });
}

/**
 * After an install or a removal the modules may have changed: the licence and every other answer
 * are read again (a page of a module that went off shows its locked page; one that came on loads).
 * A refusal is recorded in the licence history, so the licence is read again then too.
 */
function useLicenseChanged() {
  const client = useQueryClient();
  return {
    onSuccess: (result: LicenseOut | undefined) => {
      if (result) client.setQueryData(queryKeys.license, result);
      return client.invalidateQueries();
    },
    onError: () => client.invalidateQueries({ queryKey: queryKeys.license }),
  };
}

/**
 * `POST /license`: the `.lic` file as the multipart field `file`; the answer is the new state
 * (admin form). Needs a recent re-authentication: call it inside `withReauth`.
 */
export function useUploadLicense() {
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file, file.name);
      return api.post<LicenseOut>('/license', form);
    },
    ...useLicenseChanged(),
  });
}

/** `DELETE /license` (204; 404 `license_not_installed`), inside `withReauth`. */
export function useRemoveLicense() {
  return useMutation({
    mutationFn: () => api.delete<undefined>('/license'),
    ...useLicenseChanged(),
  });
}
