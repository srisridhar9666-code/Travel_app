import axios, { AxiosError } from 'axios';

import type {
  AppNotification,
  AnalyticsBundle,
  AuditRow,
  BatchDecisionItem,
  CampaignSpend,
  ChainVerification,
  Colleague,
  CoStayMatch,
  CostPreview,
  EmailStatus,
  EmailTestResult,
  FilterOptions,
  Insights,
  InsightFilters,
  TravelLog,
  TravellerStatus,
  IdProof,
  ImportPreview,
  ImportResult,
  InviteLink,
  JobResult,
  LedgerRow,
  LoginResponse,
  NotificationPreferences,
  NotificationLedger,
  Paginated,
  DecisionBody,
  Project,
  QueueCounts,
  RequestConflict,
  RequestRevision,
  RequestType,
  RetentionStatus,
  TravelHistory,
  SchedulerStatus,
  RoomSharingChoice,
  ThemePreference,
  Ticket,
  TokenPreview,
  TravelMode,
  TravelRequest,
  UncostedRow,
  UserProfile,
  UserRow,
} from '@/types';

/**
 * Single axios instance for the whole app. In dev, Vite proxies `/api` to the
 * FastAPI process, so the browser only ever sees one origin.
 */
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 30_000,
});

/** Set by the auth store; kept out of it to avoid an import cycle. Called
 *  with the server's reason, e.g. "Your account is deactivated...". */
let onUnauthorized: ((detail?: string) => void) | null = null;

export function setUnauthorizedHandler(handler: (detail?: string) => void) {
  onUnauthorized = handler;
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    // Downloads ask for a Blob, so their error body arrives as one too. The
    // server still sent JSON; read it so the reason reaches the person instead
    // of "Something went wrong."
    const body = error.response?.data;
    if (body instanceof Blob && body.type.includes('json')) {
      try {
        error.response!.data = JSON.parse(await body.text());
      } catch {
        // Keep the blob; errorMessage falls back.
      }
    }

    // A 401 means the session is gone - expired, revoked, a password changed
    // elsewhere, or the account was deactivated. Only a request that carried
    // the token in use now counts: one sent just before a password change
    // (with the old token) must not sign out the person who changed it.
    const sent = error.config?.headers?.Authorization;
    if (
      error.response?.status === 401 &&
      onUnauthorized &&
      (!accessToken || !sent || sent === `Bearer ${accessToken}`)
    ) {
      const detail = (error.response.data as { detail?: unknown } | undefined)?.detail;
      onUnauthorized(typeof detail === 'string' ? detail : undefined);
    }
    return Promise.reject(error);
  },
);

/** "full_name" -> "Full name", for naming the field a 422 is about. */
function fieldLabel(loc: unknown): string | null {
  if (!Array.isArray(loc) || loc.length === 0) return null;
  const last = loc[loc.length - 1];
  if (typeof last !== 'string' || last === 'body' || last === 'query') return null;
  const words = last.replace(/_id$/, '').replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : null;
}

/** Pull a readable message out of a FastAPI error body. */
export function errorMessage(error: unknown, fallback = 'Something went wrong.'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    // 422 bodies are a list of per-field validation errors. Pydantic's wording
    // ("Value error, ...", no field name) is for developers; say which field
    // and what is wrong with it.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as {
        msg?: unknown;
        type?: unknown;
        loc?: unknown;
        ctx?: Record<string, unknown>;
      };
      if (typeof first?.msg === 'string') {
        const message = first.msg.replace(/^(Value error|Assertion failed), /, '');
        const label = fieldLabel(first.loc);
        if (!label) return message;
        switch (first.type) {
          case 'missing':
            return `${label} is required.`;
          case 'string_too_short':
            return `${label} must be at least ${first.ctx?.min_length} characters.`;
          case 'string_too_long':
            return `${label} must be at most ${first.ctx?.max_length} characters.`;
          case 'value_error':
            return message;
          default:
            return `${label}: ${message}`;
        }
      }
    }
    if (error.code === 'ECONNABORTED') {
      return 'The server took too long to answer. Please try again.';
    }
    if (!error.response) return 'Cannot reach the server. Check your connection and try again.';
  }
  return fallback;
}

// --- health ---------------------------------------------------------------

export interface HealthResponse {
  status: string;
  app: string;
  environment: string;
  database: string;
}

export interface GeminiHealthResponse {
  ok: boolean;
  model?: string;
  reply?: string;
  detail?: string;
}

export const fetchHealth = () => api.get<HealthResponse>('/health').then((r) => r.data);
export const fetchGeminiHealth = () =>
  api.get<GeminiHealthResponse>('/health/gemini').then((r) => r.data);

// --- auth -----------------------------------------------------------------

export const login = (email: string, password: string) =>
  api.post<LoginResponse>('/auth/login', { email, password }).then((r) => r.data);

export const logout = () => api.post('/auth/logout').then((r) => r.data);

export const fetchMe = () => api.get<UserProfile>('/auth/me').then((r) => r.data);

export const saveThemePreference = (theme_preference: ThemePreference) =>
  api.patch<UserProfile>('/auth/me/theme', { theme_preference }).then((r) => r.data);

export const changePassword = (current_password: string, new_password: string) =>
  api.post('/auth/change-password', { current_password, new_password }).then((r) => r.data);

export const previewToken = (token: string) =>
  api.get<TokenPreview>(`/auth/token/${token}`).then((r) => r.data);

export const setPassword = (token: string, password: string) =>
  api.post<{ detail: string }>('/auth/set-password', { token, password }).then((r) => r.data);

export const forgotPassword = (email: string) =>
  api.post<{ detail: string }>('/auth/forgot-password', { email }).then((r) => r.data);

// --- users ----------------------------------------------------------------

export interface UserQuery {
  search?: string;
  role?: string;
  is_active?: boolean;
  page?: number;
  page_size?: number;
}

export const fetchUsers = (params: UserQuery) =>
  api.get<Paginated<UserRow>>('/users', { params }).then((r) => r.data);

export interface UserPayload {
  email: string;
  full_name: string;
  role: string;
  designation?: string | null;
  gender?: string;
  phone?: string | null;
  employee_code?: string | null;
  base_location?: string | null;
}

export const createUser = (payload: UserPayload) =>
  api.post<InviteLink>('/users', payload).then((r) => r.data);

export const updateUser = (id: number, payload: Partial<UserPayload> & { is_active?: boolean }) =>
  api.patch<UserRow>(`/users/${id}`, payload).then((r) => r.data);

export const reinviteUser = (id: number) =>
  api.post<InviteLink>(`/users/${id}/reinvite`).then((r) => r.data);

export const unlockUser = (id: number) =>
  api.post<UserRow>(`/users/${id}/unlock`).then((r) => r.data);

// --- audit ----------------------------------------------------------------

export interface AuditQuery {
  action?: string;
  entity_type?: string;
  actor_user_id?: number;
  page?: number;
  page_size?: number;
}

export const fetchAudit = (params: AuditQuery) =>
  api.get<Paginated<AuditRow>>('/audit', { params }).then((r) => r.data);

export const verifyAuditChain = () =>
  api.get<ChainVerification>('/audit/verify').then((r) => r.data);

// --- projects -------------------------------------------------------------

export interface ProjectQuery {
  search?: string;
  status?: string;
  page?: number;
  page_size?: number;
}

export interface ProjectPayload {
  name: string;
  code: string;
  description?: string | null;
  client_name?: string | null;
  location?: string | null;
  status?: string;
  start_date?: string | null;
  end_date?: string | null;
}

export const fetchProjects = (params: ProjectQuery) =>
  api.get<Paginated<Project>>('/projects', { params }).then((r) => r.data);

export const createProject = (payload: ProjectPayload) =>
  api.post<Project>('/projects', payload).then((r) => r.data);

export const updateProject = (id: number, payload: Partial<ProjectPayload>) =>
  api.patch<Project>(`/projects/${id}`, payload).then((r) => r.data);

export const archiveProject = (id: number) =>
  api.post<Project>(`/projects/${id}/archive`).then((r) => r.data);

export const restoreProject = (id: number) =>
  api.post<Project>(`/projects/${id}/restore`).then((r) => r.data);

// --- identity documents ---------------------------------------------------

export const fetchIdProofs = (userId: number) =>
  api.get<IdProof[]>(`/users/${userId}/id-proofs`).then((r) => r.data);

export function addIdProof(
  userId: number,
  fields: {
    proof_type: string;
    number: string;
    label?: string;
    issued_on?: string;
    expires_on?: string;
  },
  file?: File | null,
) {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => {
    if (value) form.append(key, value);
  });
  if (file) form.append('file', file);
  return api.post<IdProof>(`/users/${userId}/id-proofs`, form).then((r) => r.data);
}

/** The one call that decrypts. Every use writes a VIEW_SENSITIVE audit row. */
export const revealIdProof = (proofId: number) =>
  api
    .get<{ id: number; proof_type: string; number: string }>(`/id-proofs/${proofId}/reveal`)
    .then((r) => r.data);

/** Fetched as a blob rather than linked, so the bearer token is still attached
 *  and the download is audited like any other read. */
export const fetchIdProofFile = (proofId: number) =>
  api.get(`/id-proofs/${proofId}/file`, { responseType: 'blob' }).then((r) => r.data as Blob);

export const deleteIdProof = (proofId: number) =>
  api.delete(`/id-proofs/${proofId}`).then((r) => r.data);

export const fetchRetentionStatus = () =>
  api.get<RetentionStatus>('/id-proofs/retention').then((r) => r.data);

export const runRetentionPurge = () =>
  api
    .post<{ purged: number; files_removed: number; cutoff: string }>('/id-proofs/retention/purge')
    .then((r) => r.data);

// --- bulk import ----------------------------------------------------------

export const fetchImportTemplate = () =>
  api.get('/users/import/template', { responseType: 'blob' }).then((r) => r.data as Blob);

export function previewImport(file: File) {
  const form = new FormData();
  form.append('file', file);
  return api.post<ImportPreview>('/users/import/preview', form).then((r) => r.data);
}

export function commitImport(file: File) {
  const form = new FormData();
  form.append('file', file);
  return api.post<ImportResult>('/users/import', form).then((r) => r.data);
}

// --- requests -------------------------------------------------------------

export interface RequestQuery {
  mine?: boolean;
  status?: string;
  type?: string;
  project_id?: number;
  search?: string;
  page?: number;
  page_size?: number;
}

/** The body the API takes for a create, an edit or a dry-run check. It is a full
 *  replacement rather than a patch: a revision has to say what a field was as
 *  well as what it became, which a partial body cannot. */
export interface RequestPayload {
  request_type: RequestType;
  project_id: number;
  traveller_ids: number[];
  mode?: TravelMode | null;
  origin?: string | null;
  destination?: string | null;
  origin_state?: string | null;
  destination_state?: string | null;
  hotel_state?: string | null;
  /** A cab's city or constituency; null for anything else. */
  pickup_city?: string | null;
  drop_city?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  hotel_city?: string | null;
  check_in?: string | null;
  check_out?: string | null;
  notes?: string | null;
  is_draft?: boolean;
}

export const fetchRequests = (params: RequestQuery) =>
  api.get<Paginated<TravelRequest>>('/requests', { params }).then((r) => r.data);

export const fetchRequest = (id: number) =>
  api.get<TravelRequest>(`/requests/${id}`).then((r) => r.data);

export const createRequest = (payload: RequestPayload) =>
  api.post<TravelRequest>('/requests', payload).then((r) => r.data);

export const editRequest = (id: number, payload: RequestPayload) =>
  api.put<TravelRequest>(`/requests/${id}`, payload).then((r) => r.data);

export const submitRequest = (id: number) =>
  api.post<TravelRequest>(`/requests/${id}/submit`).then((r) => r.data);

export const cancelRequest = (id: number, reason: string) =>
  api.post<TravelRequest>(`/requests/${id}/cancel`, { reason }).then((r) => r.data);

export const fetchRevisions = (id: number) =>
  api.get<RequestRevision[]>(`/requests/${id}/revisions`).then((r) => r.data);

/** Dry-run the conflict and co-stay checks while the form is still being typed.
 *  Saves nothing; the same checks run again on the real write. */
export const checkRequest = (payload: RequestPayload & { request_id?: number }) =>
  api
    .post<{ conflicts: RequestConflict[]; costay_matches: CoStayMatch[] }>('/requests/check', payload)
    .then((r) => r.data);

export const setRoomSharing = (
  id: number,
  body: { traveller_id: number; choice: RoomSharingChoice; share_with_user_id?: number | null },
) => api.post<TravelRequest>(`/requests/${id}/room-sharing`, body).then((r) => r.data);

export const confirmShare = (id: number, travellerId: number) =>
  api
    .post<TravelRequest>(`/requests/${id}/travellers/${travellerId}/confirm-share`)
    .then((r) => r.data);

/** The signed-in person's own in-app notices. My requests reads the room-share
 *  asks out of these. */
export const fetchNotifications = () =>
  api.get<AppNotification[]>('/notifications/mine').then((r) => r.data);

/** A thin colleague list for the co-traveller picker. Ground staff cannot read
 *  /users, and tagging someone does not need their whole employee record. */
export const fetchColleagues = () =>
  api.get<Colleague[]>('/requests/colleagues').then((r) => r.data);

// --- admin decisions ------------------------------------------------------

export const fetchQueueCounts = () =>
  api.get<QueueCounts>('/requests/queue/counts').then((r) => r.data);

export const decideTraveller = (requestId: number, travellerId: number, body: DecisionBody) =>
  api
    .post<TravelRequest>(`/requests/${requestId}/travellers/${travellerId}/decide`, body)
    .then((r) => r.data);

/** Several travellers on one request in a single transaction — one failure rolls
 *  the whole set back, so the queue never shows a half-applied decision. */
export const decideBatch = (requestId: number, decisions: BatchDecisionItem[]) =>
  api.post<TravelRequest>(`/requests/${requestId}/decide`, { decisions }).then((r) => r.data);

// --- tickets and extraction -----------------------------------------------

export const fetchTickets = (requestId: number) =>
  api.get<Ticket[]>(`/requests/${requestId}/tickets`).then((r) => r.data);

export const fetchPendingTickets = () =>
  api.get<Ticket[]>('/tickets/pending').then((r) => r.data);

/** Upload runs extraction inline — a ticket takes a few seconds and the admin
 *  who uploaded it is waiting to review it. */
export function uploadTicket(requestId: number, travellerId: number, file: File) {
  const form = new FormData();
  form.append('file', file);
  return api
    .post<Ticket>(`/requests/${requestId}/tickets`, form, {
      params: { traveller_id: travellerId },
      timeout: 120_000,
    })
    .then((r) => r.data);
}

export const reextractTicket = (ticketId: number) =>
  api.post<Ticket>(`/tickets/${ticketId}/extract`, null, { timeout: 120_000 }).then((r) => r.data);

/** The only path to BOOKED. Nothing reaches a traveller before this. */
export const confirmTicket = (
  ticketId: number,
  body: { booking_reference: string; carrier?: string | null; service_number?: string | null; notify?: boolean },
) => api.post<Ticket>(`/tickets/${ticketId}/confirm`, body).then((r) => r.data);

export const discardTicket = (ticketId: number) =>
  api.post<Ticket>(`/tickets/${ticketId}/discard`).then((r) => r.data);

/** Fetched as a blob rather than linked, so the bearer token is still attached —
 *  the document carries a PNR and a passenger name. */
export const fetchTicketFile = (ticketId: number) =>
  api.get(`/tickets/${ticketId}/file`, { responseType: 'blob' }).then((r) => r.data as Blob);

// --- the notification ledger ----------------------------------------------

export const fetchLedger = (params: {
  status?: string;
  channel?: string;
  search?: string;
  page_size?: number;
}) =>
  api.get<NotificationLedger>('/notifications/ledger', { params }).then((r) => r.data);

export const retryFailedEmail = () =>
  api
    .post<{ attempted: number; sent: number; still_failing: number }>('/notifications/retry')
    .then((r) => r.data);

export interface EmailHealthResponse {
  ok: boolean;
  host?: string;
  from?: string;
  restricted_to?: string[] | null;
  detail?: string;
}

export const fetchEmailHealth = () =>
  api.get<EmailHealthResponse>('/health/email').then((r) => r.data);

/** What the running API is using for email, and what is missing. No
 *  connection to the mail server, so cheap to load. */
export const fetchEmailStatus = () =>
  api.get<EmailStatus>('/notifications/email/status').then((r) => r.data);

/** Send one real message and report how far it got. Slow when the mail server
 *  is unreachable: the server waits out its own connection timeout first. */
export const sendTestEmail = (to?: string) =>
  api
    .post<EmailTestResult>('/notifications/email/test', { to: to || null }, { timeout: 60_000 })
    .then((r) => r.data);

// --- notifications: inbox, preferences, scheduler -------------------------

export const fetchMyNotices = () =>
  api.get<LedgerRow[]>('/notifications/mine').then((r) => r.data);

export const fetchUnreadCount = () =>
  api.get<{ unread: number }>('/notifications/unread-count').then((r) => r.data);

export const markNoticeRead = (id: number) =>
  api.post<{ marked: number }>(`/notifications/${id}/read`).then((r) => r.data);

export const markAllRead = () =>
  api.post<{ marked: number }>('/notifications/read').then((r) => r.data);

export const fetchPreferences = () =>
  api.get<NotificationPreferences>('/notifications/preferences').then((r) => r.data);

export const setPreference = (category: string, enabled: boolean) =>
  api
    .patch<NotificationPreferences>('/notifications/preferences', { category, enabled })
    .then((r) => r.data);

export const fetchSchedulerStatus = () =>
  api.get<SchedulerStatus>('/notifications/scheduler').then((r) => r.data);

/** Safe to press twice: every notice the jobs write is deduplicated by event. */
export const runReminderJobs = () =>
  api.post<JobResult[]>('/notifications/run-jobs', null, { timeout: 120_000 }).then((r) => r.data);

// --- cost and analytics ---------------------------------------------------

export const fetchAnalytics = (params: InsightFilters = {}) =>
  api.get<AnalyticsBundle>('/analytics', { params: repeatParams({ ...params }) }).then((r) => r.data);

export const fetchCampaignSpend = () =>
  api.get<CampaignSpend[]>('/analytics/campaigns').then((r) => r.data);

export const fetchUncosted = () =>
  api.get<UncostedRow[]>('/analytics/uncosted').then((r) => r.data);

export const setCosts = (
  requestId: number,
  amounts: { traveller_id: number; amount: string | null; note?: string | null }[],
) => api.post<TravelRequest>(`/requests/${requestId}/costs`, { amounts }).then((r) => r.data);

/** What an even split comes to, before saving. Shown so the odd paisa on the
 *  first row does not look like a bug the first time someone divides by three. */
export const previewSplit = (
  requestId: number,
  body: { total_amount: string; traveller_ids: number[] },
) => api.post<CostPreview>(`/requests/${requestId}/costs/preview`, body).then((r) => r.data);

export const splitCost = (
  requestId: number,
  body: { total_amount: string; traveller_ids: number[]; note?: string | null },
) => api.post<TravelRequest>(`/requests/${requestId}/costs/split`, body).then((r) => r.data);

// --- audit viewer, hardening ----------------------------------------------

export interface LedgerGrants {
  checked: boolean;
  append_only?: boolean;
  update_refused?: boolean;
  delete_refused?: boolean;
  detail: string;
  grants?: string[];
}

export interface AuditSummary {
  total: number;
  by_action: Record<string, number>;
  by_entity: Record<string, number>;
  oldest: string | null;
  newest: string | null;
}

/** Probes an actual UPDATE inside a rolled-back transaction rather than parsing
 *  grant text — see services/ledger_guard.py for why. */
export const fetchLedgerGrants = () =>
  api.get<LedgerGrants>('/audit/grants').then((r) => r.data);

export const fetchAuditSummary = () =>
  api.get<AuditSummary>('/audit/summary').then((r) => r.data);

export const fetchEntityHistory = (entityType: string, entityId: number) =>
  api.get<AuditRow[]>(`/audit/entity/${entityType}/${entityId}`).then((r) => r.data);

/** Fetched as a blob so the bearer token is attached — and because exporting
 *  the log writes its own VIEW_SENSITIVE row. */
export const exportAudit = (params: { action?: string; entity_type?: string; limit?: number }) =>
  api.get('/audit/export', { params, responseType: 'blob' }).then((r) => r.data as Blob);

// --- travel history -------------------------------------------------------

/** Anyone may read their own; only an admin may read someone else's. */
export const fetchTravelHistory = (userId: number, params?: { since?: string; until?: string }) =>
  api.get<TravelHistory>(`/users/${userId}/travel-history`, { params }).then((r) => r.data);

// --- locations ------------------------------------------------------------

/** Cities grouped by state, which is the shape the cascading picker wants. */
export const fetchLocations = () =>
  api.get<Record<string, string[]>>('/locations').then((r) => r.data);

export const addLocation = (state: string, city: string) =>
  api.post<{ id: number; state: string; city: string }>('/locations', { state, city })
    .then((r) => r.data);

/** What a free-text place name probably meant, for rows written before the
 *  picker existed. */
export const resolvePlace = (typed: string) =>
  api
    .get<{ typed: string; matched: boolean; city: string | null; state: string | null }>(
      '/locations/resolve',
      { params: { typed } },
    )
    .then((r) => r.data);

// --- travel logs and the dashboard ------------------------------------------

/** Axios would send an array as `status[]=`; FastAPI wants the key repeated. */
function repeatParams(params: Record<string, unknown>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, String(v)));
    else search.append(key, String(value));
  }
  return search;
}

/** The most rows one request may ask for - what an export asks for. */
export const MAX_LOG_ROWS = 5000;

export const fetchTravelLogs = (
  params: InsightFilters & {
    status?: TravellerStatus[];
    search?: string;
    page?: number;
    page_size?: number;
  },
) =>
  api
    .get<TravelLog>('/travel-logs', { params: repeatParams({ ...params }) })
    .then((r) => r.data);

export const fetchInsights = (params: InsightFilters) =>
  api.get<Insights>('/analytics/insights', { params: repeatParams({ ...params }) }).then((r) => r.data);

export const fetchFilterOptions = () =>
  api.get<FilterOptions>('/analytics/filter-options').then((r) => r.data);
