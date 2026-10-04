import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BedDouble,
  Car,
  CheckSquare,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  Flag,
  History,
  IndianRupee,
  Plane,
  Ticket,
  Gavel,
  UserCheck,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import { ManagerReview } from '@/components/ManagerReview';
import { Modal } from '@/components/Modal';
import { PriorityBadge } from '@/components/PriorityBadge';
import { ConflictList } from '@/components/RequestForm';
import CostPanel from '@/components/CostPanel';
import TicketPanel from '@/components/TicketPanel';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from '@/components/ui';
import {
  decideBatch,
  errorMessage,
  exportQueue,
  fetchQueueCounts,
  fetchRequests,
  fetchRevisions,
  fetchTicketFile,
} from '@/lib/api';
import { downloadCsv, slug, type CsvCell } from '@/lib/csv';
import { openFileTab, showFile } from '@/lib/files';
import { campaignLabel, itinerary, revisionValue } from '@/lib/requests';
import { fileStamp, formatInstant, parseInstant, sheetInstant } from '@/lib/time';
import { cn } from '@/lib/utils';
import {
  DESIGNATION_LABELS,
  PRIORITY_LABELS,
  PRIORITY_ORDER,
  RECOMMENDATION_LABELS,
  REQUEST_STATUS_LABELS,
  REQUEST_TYPE_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type BatchDecisionItem,
  type RequestPriority,
  type RequestType,
  type RequestStatus,
  type RequestTraveller,
  type ReviewFilter,
  type TravelRequest,
  type TravellerStatus,
} from '@/types';

const TYPE_ICON = { LONG_DISTANCE: Plane, LOCAL_CAB: Car, HOTEL: BedDouble } as const;

const STATUS_TONE: Record<RequestStatus, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  DRAFT: 'neutral',
  SUBMITTED: 'info',
  PARTIALLY_APPROVED: 'warning',
  APPROVED: 'success',
  BOOKED: 'success',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
  EXPIRED: 'warning',
};

const TRAVELLER_TONE: Record<TravellerStatus, 'neutral' | 'success' | 'danger' | 'info'> = {
  PENDING: 'neutral',
  APPROVED: 'success',
  BOOKED: 'info',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
};

type TabKey = Exclude<RequestStatus, 'DRAFT'>;

/** The three kinds of booking, kept apart because each is booked differently:
 *  a ticket on a train or plane, a car for the day, a room for some nights. */
const KINDS: { key: RequestType | ''; label: string; icon: typeof Plane }[] = [
  { key: '', label: 'All', icon: CheckSquare },
  { key: 'LONG_DISTANCE', label: 'Flight · Train · Bus', icon: Plane },
  { key: 'LOCAL_CAB', label: 'Cab', icon: Car },
  { key: 'HOTEL', label: 'Hotel', icon: BedDouble },
];

/** The queue tabs, in the order an admin works through them. */
const TABS: { key: TabKey; label: string; countKey: keyof CountShape }[] = [
  { key: 'SUBMITTED', label: 'Awaiting', countKey: 'awaiting' },
  { key: 'PARTIALLY_APPROVED', label: 'Partly approved', countKey: 'partially_approved' },
  { key: 'APPROVED', label: 'Approved', countKey: 'approved' },
  { key: 'BOOKED', label: 'Booked', countKey: 'booked' },
  { key: 'EXPIRED', label: 'Expired', countKey: 'expired' },
  { key: 'REJECTED', label: 'Rejected', countKey: 'rejected' },
  // Nowhere else shows other people's cancelled requests: My requests is only
  // the admin's own, and the travel log leaves cancelled trips out.
  { key: 'CANCELLED', label: 'Cancelled', countKey: 'cancelled' },
];

/** The server caps a page at 100. */
const PAGE_SIZES = [25, 50, 100];
const DEFAULT_PAGE_SIZE = 25;

type CountShape = {
  awaiting: number;
  partially_approved: number;
  approved: number;
  booked: number;
  rejected: number;
  cancelled: number;
  expired: number;
};

// --- CSV export ------------------------------------------------------------
//
// One row per traveller, with the request's columns repeated and Request #
// first, so a sheet can be grouped back into requests. Decisions, PNRs and cost
// all live on the traveller, and one request can mix outcomes.

interface ExportColumn {
  header: string;
  value: (request: TravelRequest, traveller: RequestTraveller) => CsvCell;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** Whole days between two calendar dates ("2026-10-02"), as typed. */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);
}

/** Trip times are wall-clock values as the requester typed them, so they are
 *  cut from the string rather than converted through a time zone. */
const tripDate = (iso: string | null) => (iso ? iso.slice(0, 10) : '');
const tripTime = (iso: string | null) => (iso ? iso.slice(11, 16) : '');
const isCab = (r: TravelRequest) => r.request_type === 'LOCAL_CAB';

const COMMON_COLUMNS: ExportColumn[] = [
  { header: 'Request #', value: (r) => r.id },
  // Seeded and very old rows have no submitted_at; created_at is the honest
  // stand-in.
  { header: 'Raised on (IST)', value: (r) => sheetInstant(r.submitted_at ?? r.created_at) },
  { header: 'Raised by', value: (r) => r.requester_name },
  { header: 'Priority', value: (r) => PRIORITY_LABELS[r.priority] ?? r.priority },
  { header: 'Campaign code', value: (r) => r.project_code },
  { header: 'Campaign', value: (r) => campaignLabel(r) },
  {
    header: 'Type',
    value: (r) =>
      r.request_type === 'LONG_DISTANCE' && r.mode
        ? TRAVEL_MODE_LABELS[r.mode]
        : REQUEST_TYPE_LABELS[r.request_type],
  },
  { header: 'From', value: (r) => r.origin },
  { header: 'From city', value: (r) => (isCab(r) ? r.pickup_city : r.origin) },
  { header: 'From state', value: (r) => r.origin_state },
  { header: 'To', value: (r) => r.destination },
  { header: 'To city', value: (r) => (isCab(r) ? r.drop_city : r.destination) },
  { header: 'To state', value: (r) => r.destination_state },
  { header: 'Hotel', value: (r) => r.hotel_city },
  { header: 'Hotel state', value: (r) => r.hotel_state },
  { header: 'Departs or check-in date', value: (r) => r.check_in ?? tripDate(r.start_at) },
  { header: 'Departs time', value: (r) => tripTime(r.start_at) },
  {
    header: 'Arrives or check-out',
    value: (r) =>
      r.request_type === 'HOTEL'
        ? r.check_out
        : r.end_at
          ? `${tripDate(r.end_at)} ${tripTime(r.end_at)}`
          : '',
  },
  {
    header: 'Nights',
    value: (r) => (r.check_in && r.check_out ? daysBetween(r.check_in, r.check_out) : ''),
  },
  { header: 'Reason for travel', value: (r) => r.travel_reason },
  { header: 'Notes', value: (r) => r.notes },
  { header: 'Traveller', value: (_, t) => t.full_name },
  { header: 'Traveller email', value: (_, t) => t.email },
  { header: 'Designation', value: (_, t) => (t.designation ? DESIGNATION_LABELS[t.designation] : '') },
  { header: 'Traveller status', value: (_, t) => TRAVELLER_STATUS_LABELS[t.status] },
  { header: 'Manager', value: (_, t) => t.manager_reviewed_by_name ?? t.manager_name },
  {
    header: 'Manager recommendation',
    value: (_, t) =>
      t.manager_recommendation
        ? `${RECOMMENDATION_LABELS[t.manager_recommendation]}${t.manager_comment ? ` - ${t.manager_comment}` : ''}`
        : t.manager_name
          ? 'Not given'
          : '',
  },
  { header: 'Request status', value: (r) => REQUEST_STATUS_LABELS[r.status] },
  { header: 'Times edited', value: (r) => r.edit_count },
];

const WAITING_DAYS: ExportColumn = {
  header: 'Waiting (days)',
  value: (r) =>
    Math.max(0, Math.floor((Date.now() - parseInstant(r.submitted_at ?? r.created_at).getTime()) / DAY_MS)),
};
const CLASHES: ExportColumn = {
  header: 'Clash warnings',
  value: (r, t) =>
    r.conflicts
      .filter((c) => c.user_id === t.user_id)
      .map((c) => c.message)
      .join('; '),
};
// "Decided", not "Approved": each traveller keeps only their latest decision,
// so on a booked row this is whoever marked it booked.
const DECIDED: ExportColumn[] = [
  { header: 'Decided by', value: (_, t) => t.decided_by_name },
  { header: 'Decided on (IST)', value: (_, t) => sheetInstant(t.decided_at) },
  { header: 'Decision reason', value: (_, t) => t.decision_reason },
];
const PNR: ExportColumn = { header: 'PNR or booking ref', value: (_, t) => t.booking_reference };
const COST: ExportColumn = { header: 'Cost (INR)', value: (_, t) => t.cost_amount };
const COST_NOTE: ExportColumn = { header: 'Cost note', value: (_, t) => t.cost_note };
const COST_BY: ExportColumn = { header: 'Cost entered by', value: (_, t) => t.cost_entered_by_name };

/** What each tab adds to the common columns: what an admin on that tab would
 *  want next to the trip. */
const TAB_COLUMNS: Record<TabKey, ExportColumn[]> = {
  SUBMITTED: [WAITING_DAYS, CLASHES],
  PARTIALLY_APPROVED: [CLASHES, ...DECIDED, PNR, COST],
  APPROVED: [...DECIDED, COST, COST_NOTE],
  BOOKED: [...DECIDED, PNR, COST, COST_NOTE, COST_BY],
  EXPIRED: [WAITING_DAYS],
  REJECTED: DECIDED,
  CANCELLED: [{ header: 'Cancel reason', value: (r) => r.cancel_reason }, ...DECIDED],
};

function exportTab(tab: TabKey, label: string, items: TravelRequest[]): number {
  const columns = [...COMMON_COLUMNS, ...TAB_COLUMNS[tab]];
  const rows = items.flatMap((request) =>
    request.travellers.map((traveller) => columns.map((c) => c.value(request, traveller))),
  );
  downloadCsv(
    `approvals-${slug(label)}-${fileStamp()}.csv`,
    columns.map((c) => c.header),
    rows,
  );
  return rows.length;
}

function Pager({
  page,
  pages,
  total,
  pageSize,
  shown,
  busy,
  onPage,
  onPageSize,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  shown: number;
  busy: boolean;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = (page - 1) * pageSize + shown;
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-border px-4 py-3 sm:px-5">
      <p className="text-sm text-text-muted tabular-nums" aria-live="polite">
        Showing <span className="font-medium text-text">{first}–{last}</span> of{' '}
        <span className="font-medium text-text">{total}</span>
      </p>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <label className="flex items-center gap-2 text-sm text-text-muted">
          <span className="hidden sm:inline">Rows per page</span>
          <span className="sm:hidden">Rows</span>
          <Select
            aria-label="Rows per page"
            value={String(pageSize)}
            onChange={(e) => onPageSize(Number(e.target.value))}
            className="h-9 w-20"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </Select>
        </label>
        <span className="text-sm text-text-muted tabular-nums">
          Page {page} of {pages}
        </span>
        <div className="flex gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            disabled={page <= 1 || busy}
            onClick={() => onPage(page - 1)}
            aria-label="Previous page"
          >
            <ChevronLeft size={16} />
            <span className="hidden sm:inline">Previous</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= pages || busy}
            onClick={() => onPage(page + 1)}
            aria-label="Next page"
          >
            <span className="hidden sm:inline">Next</span>
            <ChevronRight size={16} />
          </Button>
        </div>
      </div>
    </div>
  );
}

function DecisionLog({ travellers }: { travellers: RequestTraveller[] }) {
  // Only people who have actually been decided on. A pending traveller has no
  // entry here, which is the honest answer rather than a blank row.
  const decided = travellers
    .filter((t) => t.decided_at)
    .sort((a, b) => (a.decided_at ?? '').localeCompare(b.decided_at ?? ''));

  if (decided.length === 0) {
    return <p className="text-xs text-text-subtle">Nobody has been decided on yet.</p>;
  }

  return (
    <ol className="space-y-2.5">
      {decided.map((traveller) => (
        <li key={traveller.id} className="border-l-2 border-border pl-3">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-xs font-medium">{traveller.full_name}</span>
            <Badge
              tone={
                traveller.status === 'REJECTED'
                  ? 'danger'
                  : traveller.status === 'BOOKED'
                    ? 'success'
                    : 'info'
              }
            >
              {traveller.status.toLowerCase()}
            </Badge>
            <span className="text-2xs text-text-subtle">
              {traveller.decided_by_name ?? 'system'} ·{' '}
              {formatInstant(traveller.decided_at)}
            </span>
          </div>
          {traveller.decision_reason && (
            <p className="mt-0.5 text-xs text-text-muted">{traveller.decision_reason}</p>
          )}
          {traveller.booking_reference && (
            <p className="mt-0.5 font-mono text-2xs text-text-subtle">
              Booking {traveller.booking_reference}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}


function RevisionHistory({ requestId }: { requestId: number }) {
  const revisions = useQuery({
    queryKey: ['revisions', requestId],
    queryFn: () => fetchRevisions(requestId),
  });

  if (revisions.isPending) return <Skeleton className="h-14 w-full" />;
  const rows = revisions.data ?? [];
  if (rows.length === 0) return <p className="text-xs text-text-subtle">No history yet.</p>;

  return (
    <ol className="space-y-2.5">
      {rows.map((revision) => (
        <li key={revision.revision_number} className="border-l-2 border-border pl-3">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-xs font-medium">
              #{revision.revision_number} {revision.summary}
            </span>
            <span className="text-2xs text-text-subtle">
              {revision.editor_name} · {formatInstant(revision.created_at)}
            </span>
          </div>
          {revision.changes && (
            <dl className="mt-1 space-y-0.5">
              {Object.entries(revision.changes).map(([field, change]) => (
                <div key={field} className="flex flex-wrap gap-x-1.5 text-2xs">
                  <dt className="text-text-subtle">{field.replace(/_/g, ' ')}</dt>
                  <dd className="text-text-muted">
                    <span className="line-through opacity-70">
                      {revisionValue(field, change.from)}
                    </span>
                    {' → '}
                    <span className="font-medium text-text">
                      {revisionValue(field, change.to)}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ol>
  );
}

/** What the admin is about to do, held until the reason (if one is needed) is typed. */
interface PendingDecision {
  request: TravelRequest;
  traveller: RequestTraveller;
  to: TravellerStatus;
  /** Set when the server came back asking for an override reason. */
  needsOverride: boolean;
}

export default function ApprovalsPage() {
  const queryClient = useQueryClient();

  const [tab, setTabState] = useState<TabKey>('SUBMITTED');
  const [kind, setKindState] = useState<RequestType | ''>('');
  const [search, setSearchState] = useState('');
  const [priority, setPriorityState] = useState<RequestPriority | ''>('');
  // Two-level approval: only requests still waiting on someone's manager.
  // Those can only be on the two waiting tabs, so it is offered there alone.
  const [review, setReviewState] = useState<ReviewFilter | ''>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [reason, setReason] = useState('');
  const [notify, setNotify] = useState(true);
  const [reference, setReference] = useState('');

  // Any change to what is being looked at starts again from page 1; page 3 of
  // a different tab is not a place anyone meant to go.
  const setTab = (next: TabKey) => {
    setTabState(next);
    if (next !== 'SUBMITTED' && next !== 'PARTIALLY_APPROVED') setReviewState('');
    setPage(1);
  };
  const setReview = (next: ReviewFilter | '') => {
    setReviewState(next);
    setPage(1);
  };
  const setKind = (next: RequestType | '') => {
    setKindState(next);
    setPage(1);
  };
  const setSearch = (next: string) => {
    setSearchState(next);
    setPage(1);
  };
  const setPriority = (next: RequestPriority | '') => {
    setPriorityState(next);
    setPage(1);
  };

  const tabLabel = TABS.find((item) => item.key === tab)?.label ?? REQUEST_STATUS_LABELS[tab];
  const filters = {
    status: tab,
    type: kind || undefined,
    search: search.trim() || undefined,
    priority: priority || undefined,
    review: review || undefined,
  };

  // The banners count the whole queue. The tab labels follow the search and
  // priority, as the list under them does: "Booked 12" over a high-priority
  // list of two read as the filter not working.
  const counts = useQuery({ queryKey: ['queue-counts'], queryFn: () => fetchQueueCounts() });
  const sliced = Boolean(filters.type || filters.search || filters.priority || filters.review);
  const slicedCounts = useQuery({
    queryKey: ['queue-counts', filters.type, filters.search, filters.priority, filters.review],
    queryFn: () =>
      fetchQueueCounts({
        type: filters.type,
        search: filters.search,
        priority: filters.priority,
        review: filters.review,
      }),
    enabled: sliced,
    placeholderData: keepPreviousData,
  });
  const tabCounts = sliced ? slicedCounts.data : counts.data;
  const requests = useQuery({
    queryKey: ['queue', tab, kind, search, priority, review, page, pageSize],
    queryFn: () =>
      fetchRequests({
        mine: false,
        ...filters,
        // High first on every tab, newest first within each.
        sort: 'priority',
        page,
        page_size: pageSize,
      }),
    placeholderData: keepPreviousData,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['queue'] });
    queryClient.invalidateQueries({ queryKey: ['queue-counts'] });
    queryClient.invalidateQueries({ queryKey: ['requests'] });
    queryClient.invalidateQueries({ queryKey: ['tickets'] });
    // A decision moves the dashboard, the travel log and cost analytics too.
    queryClient.invalidateQueries({ queryKey: ['insights'] });
    queryClient.invalidateQueries({ queryKey: ['travel-logs'] });
    queryClient.invalidateQueries({ queryKey: ['analytics'] });
  };

  // Every row of the tab that matches the search and priority, fetched afresh,
  // not just the page on screen.
  const exporting = useMutation({
    // The tab rides along with the result, so switching tabs while a big export
    // is loading cannot put one tab's rows under another tab's columns.
    mutationFn: (vars: { tab: TabKey; label: string }) =>
      exportQueue({ ...filters, status: vars.tab }).then((all) => ({ ...vars, all })),
    meta: { errorFallback: 'Could not export this tab.' },
    onSuccess: ({ tab: exported, label, all }) => {
      const written = exportTab(exported, label, all.items);
      if (all.truncated) {
        // Cut in the tab's own order, high priority first, so what is left
        // out is the oldest low-priority work.
        toast(
          `Exported the first ${all.items.length} of ${all.total} requests. Search or filter for the rest.`,
        );
      } else {
        toast.success(`Exported ${written} ${written === 1 ? 'row' : 'rows'}`);
      }
    },
  });

  // Straight from the traveller's row, so a booked ticket is one click away
  // rather than behind the row's expand button.
  const viewTicket = useMutation({
    mutationFn: (vars: { ticketId: number; tab: Window | null }) =>
      showFile(vars.tab, () => fetchTicketFile(vars.ticketId), `ticket-${vars.ticketId}`),
    meta: { errorFallback: 'Could not open the ticket.' },
  });

  const decide = useMutation({
    // The request and traveller travel with the mutation rather than being read
    // from state in the handlers: onError closes over the render that created
    // the mutation, so a decision fired straight from a button would otherwise
    // see a stale `pending` and never open the override dialog.
    mutationFn: (vars: {
      request: TravelRequest;
      traveller: RequestTraveller;
      to: TravellerStatus;
      items: BatchDecisionItem[];
    }) => decideBatch(vars.request.id, vars.items),
    // This flow has its own error handling below (it can turn a refusal into
    // the override dialog), so the global toast stays out of it.
    meta: { errorToast: false },
    onSuccess: (_, vars) => {
      toast.success(
        `${vars.traveller.full_name} ${TRAVELLER_STATUS_LABELS[vars.to].toLowerCase()}`,
      );
      close();
      refresh();
    },
    onError: (err, vars) => {
      const message = errorMessage(err);
      // The server recomputes conflicts at decision time, so an approval that
      // looked clear on screen can still come back needing a reason. Ask for
      // one rather than just reporting the refusal.
      if (message.includes('typed reason')) {
        setPending({ ...vars, needsOverride: true });
        setReason('');
        toast.error('That traveller has a clash — a reason is needed.');
        return;
      }
      close();
      toast.error(message);
    },
  });

  function close() {
    setPending(null);
    setReason('');
    setReference('');
  }

  /** Start a decision. Everything opens the dialog now: an approval needs a
   *  typed reason just as a rejection does, because an approval with nothing
   *  written against it is the one somebody asks about months later. */
  const start = (request: TravelRequest, traveller: RequestTraveller, to: TravellerStatus) => {
    setReason('');
    setReference('');
    setNotify(true);
    setPending({ request, traveller, to, needsOverride: false });
  };

  const confirm = () => {
    if (!pending) return;
    const item: BatchDecisionItem = {
      traveller_id: pending.traveller.id,
      to_status: pending.to,
      reason,
      notify_employee: notify,
    };
    if (pending.to === 'BOOKED') item.booking_reference = reference;
    // An override is recorded separately from the decision it justifies, so it
    // carries the same sentence rather than replacing it.
    if (pending.needsOverride) item.conflict_override_reason = reason;
    decide.mutate({
      request: pending.request,
      traveller: pending.traveller,
      to: pending.to,
      items: [item],
    });
  };

  const rows = requests.data?.items ?? [];
  const total = requests.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / (requests.data?.page_size ?? pageSize)));
  const countData = counts.data;
  const urgent = countData?.high_priority ?? 0;
  const onManager = tabCounts?.awaiting_manager ?? 0;
  const waitingTab = tab === 'SUBMITTED' || tab === 'PARTIALLY_APPROVED';

  // Deciding the last row on the last page moves it to another tab; step back
  // rather than leave the admin looking at an empty page with work behind it.
  useEffect(() => {
    if (requests.data && page > pages) setPage(pages);
  }, [requests.data, page, pages]);

  const dialogTitle = !pending
    ? ''
    : pending.needsOverride
      ? `Approve ${pending.traveller.full_name} over a clash`
      : pending.to === 'BOOKED'
        ? `Book ${pending.traveller.full_name}`
        : pending.to === 'APPROVED'
          ? `Approve ${pending.traveller.full_name}`
          : `${pending.to === 'REJECTED' ? 'Reject' : 'Cancel'} ${pending.traveller.full_name}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
          Every request awaiting fulfilment. Decisions are per person — a group request can be
          partly approved, and each traveller is told their own answer.
        </p>
      </div>

      {countData && (urgent > 0 || countData.expired > 0 || countData.with_conflicts > 0) && (
        <div className="flex flex-wrap gap-3">
          {urgent > 0 && (
            <Card className="flex-1 border-danger/40 bg-danger-soft">
              <button
                type="button"
                onClick={() => {
                  // Straight to the work: high only, on the waiting tab that
                  // holds some - the current one if it does, else awaiting,
                  // else partly approved - and with no search hiding it.
                  const here =
                    (tab === 'SUBMITTED' && countData.high_priority_awaiting > 0) ||
                    (tab === 'PARTIALLY_APPROVED' && countData.high_priority_partial > 0);
                  if (!here) {
                    setTab(countData.high_priority_awaiting > 0 ? 'SUBMITTED' : 'PARTIALLY_APPROVED');
                  }
                  setSearch('');
                  setPriority('HIGH');
                }}
                className="flex w-full items-center gap-2.5 px-4 py-3 text-left"
                title="Show the high-priority requests awaiting a decision"
              >
                <Flag size={15} className="shrink-0 text-danger" />
                <p className="text-xs text-text-muted">
                  <span className="font-semibold text-danger">{urgent}</span> high-priority{' '}
                  {urgent === 1 ? 'request is' : 'requests are'} waiting for a decision.
                </p>
              </button>
            </Card>
          )}
          {counts.data!.with_conflicts > 0 && (
            <Card className="flex-1 border-warning/40 bg-warning-soft">
              <div className="flex items-center gap-2.5 px-4 py-3">
                <AlertTriangle size={15} className="shrink-0 text-warning" />
                <p className="text-xs text-text-muted">
                  <span className="font-semibold text-warning">
                    {counts.data!.with_conflicts}
                  </span>{' '}
                  undecided {counts.data!.with_conflicts === 1 ? 'request has' : 'requests have'} a
                  calendar clash. Approving one needs a typed reason.
                </p>
              </div>
            </Card>
          )}
          {countData.expired > 0 && (
            <Card className="flex-1 border-warning/40 bg-warning-soft">
              <div className="flex items-center gap-2.5 px-4 py-3">
                <AlertTriangle size={15} className="shrink-0 text-warning" />
                <p className="text-xs text-text-muted">
                  <span className="font-semibold text-warning">{countData.expired}</span>{' '}
                  {countData.expired === 1 ? 'request' : 'requests'} passed their travel date with
                  nobody decided.
                </p>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* Kind first, then status: an admin booking cabs works down the cab
          queue, not a mixed list. */}
      <div
        role="group"
        aria-label="Kind of request"
        className="grid grid-cols-2 gap-2 sm:inline-grid sm:grid-cols-4"
      >
        {KINDS.map((item) => {
          const Icon = item.icon;
          const active = kind === item.key;
          return (
            <button
              key={item.key || 'all'}
              type="button"
              aria-pressed={active}
              onClick={() => setKind(item.key)}
              className={cn(
                'inline-flex items-center justify-center gap-2 rounded-lg border px-3.5 py-2.5 text-sm font-medium transition-colors',
                active
                  ? 'border-brand bg-brand-soft text-brand-strong'
                  : 'border-border bg-surface text-text-muted hover:bg-surface-sunken hover:text-text',
              )}
            >
              <Icon size={16} />
              {item.label}
            </button>
          );
        })}
      </div>

      <Card>
        <div className="flex flex-wrap gap-1 border-b border-border px-3 py-2">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
              aria-pressed={tab === item.key}
              className={cn(
                'rounded-md px-2.5 py-1.5 text-xs transition-colors',
                tab === item.key
                  ? 'bg-surface-sunken font-medium text-text'
                  : 'text-text-muted hover:bg-surface-sunken hover:text-text',
              )}
            >
              {item.label}
              {tabCounts && (
                <span className="ml-1.5 text-text-subtle">{tabCounts[item.countKey]}</span>
              )}
            </button>
          ))}
        </div>

        <CardHeader
          title={`${total} in this view`}
          action={
            <Button
              variant="secondary"
              size="sm"
              loading={exporting.isPending}
              disabled={!requests.data || total === 0}
              onClick={() =>
                exporting.mutate({
                  tab,
                  label: kind ? `${KINDS.find((k) => k.key === kind)?.label} ${tabLabel}` : tabLabel,
                })
              }
              title={`Download every request in ${tabLabel} that matches the search, not just this page`}
            >
              {!exporting.isPending && <Download size={15} />}
              Export CSV
            </Button>
          }
        />

        <div className="flex flex-wrap gap-2 border-b border-border px-5 py-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search place or notes"
            className="min-w-40 flex-1 sm:max-w-64"
            aria-label="Search the queue"
          />
          <Select
            value={priority}
            onChange={(e) => setPriority(e.target.value as RequestPriority | '')}
            aria-label="Filter by priority"
            className="w-40"
          >
            <option value="">All priorities</option>
            {PRIORITY_ORDER.map((value) => (
              <option key={value} value={value}>
                {PRIORITY_LABELS[value]} priority
              </option>
            ))}
          </Select>
          {waitingTab && (onManager > 0 || review) && (
            <button
              type="button"
              aria-pressed={review === 'waiting'}
              onClick={() => setReview(review === 'waiting' ? '' : 'waiting')}
              title="Requests where a traveller's manager has not recommended yet. You can still decide them."
              className={cn(
                'inline-flex h-10 items-center gap-1.5 rounded-md border px-3 text-sm transition-colors',
                review === 'waiting'
                  ? 'border-warning/50 bg-warning-soft font-medium text-warning'
                  : 'border-border bg-surface text-text-muted hover:bg-surface-sunken hover:text-text',
              )}
            >
              <UserCheck size={15} />
              Waiting for a manager
              <span className="tabular-nums">{onManager}</span>
            </button>
          )}
        </div>

        {requests.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
        ) : requests.isError ? (
          <EmptyState
            icon={<CheckSquare size={28} />}
            title="Could not load the queue"
            description={errorMessage(requests.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<CheckSquare size={28} />}
            title="Nothing here"
            description={
              review
                ? 'Nothing in this tab is waiting for a manager.'
                : search.trim() || priority
                  ? 'Nothing in this tab matches that search or priority.'
                  : tab === 'SUBMITTED'
                    ? 'No requests are waiting on a decision.'
                    : 'No requests in this state.'
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {rows.map((request) => {
              const Icon = TYPE_ICON[request.request_type];
              const isOpen = expanded === request.id;

              return (
                <li key={request.id} className="px-5 py-4">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface-sunken text-text-muted">
                      <Icon size={15} />
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{itinerary(request)}</span>
                        <Badge tone={STATUS_TONE[request.status]}>
                          {REQUEST_STATUS_LABELS[request.status]}
                        </Badge>
                        <PriorityBadge priority={request.priority} />
                        {request.edit_count > 0 && (
                          <button
                            type="button"
                            onClick={() => setExpanded(isOpen ? null : request.id)}
                            className="inline-flex items-center gap-1 rounded-full bg-surface-sunken px-2 py-0.5 text-2xs font-semibold text-text-muted ring-1 ring-inset ring-border hover:text-text"
                            title="An admin should never approve a version they have not read"
                          >
                            <History size={10} />
                            edited {request.edit_count}
                            {request.edit_count === 1 ? ' time' : ' times'}
                          </button>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-text-muted">
                        {campaignLabel(request)} · raised by {request.requester_name}
                        {request.mode && ` · ${TRAVEL_MODE_LABELS[request.mode]}`}
                        {request.notes && ` · ${request.notes}`}
                      </p>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      aria-expanded={isOpen}
                      title={isOpen ? 'Hide details' : 'Tickets, cost and history'}
                      onClick={() => setExpanded(isOpen ? null : request.id)}
                    >
                      <ChevronDown
                        size={14}
                        className={isOpen ? 'rotate-180 transition-transform' : 'transition-transform'}
                      />
                    </Button>
                  </div>

                  {request.conflicts.length > 0 && (
                    <div className="mt-3">
                      <ConflictList
                        conflicts={request.conflicts}
                        footnote="Approving anyone with a clash needs a typed reason, which is recorded."
                      />
                    </div>
                  )}

                  <div className="mt-3 space-y-1.5">
                    {request.travellers.map((traveller) => (
                      <div
                        key={traveller.id}
                        className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2"
                      >
                        <span className="text-sm font-medium">{traveller.full_name}</span>
                        <Badge tone={TRAVELLER_TONE[traveller.status]}>
                          {TRAVELLER_STATUS_LABELS[traveller.status]}
                        </Badge>
                        {traveller.booking_reference && (
                          <span className="inline-flex items-center gap-1 text-2xs text-text-muted">
                            <Ticket size={11} />
                            {traveller.booking_reference}
                          </span>
                        )}
                        {traveller.decision_reason && (
                          <span className="text-2xs text-text-subtle">
                            {traveller.decision_reason}
                          </span>
                        )}
                        {traveller.decided_by_name && (
                          <span className="text-2xs text-text-subtle">
                            by {traveller.decided_by_name}
                          </span>
                        )}
                        {/* The first level, where there is one: what the
                            traveller's manager said, or that they have not yet. */}
                        {(traveller.manager_recommendation || traveller.manager_name) && (
                          <div className="order-last basis-full">
                            <ManagerReview traveller={traveller} />
                          </div>
                        )}

                        <div className="ml-auto flex gap-1.5">
                          {traveller.ticket_id != null && (
                            <Button
                              size="sm"
                              variant="ghost"
                              loading={viewTicket.isPending && viewTicket.variables?.ticketId === traveller.ticket_id}
                              onClick={() =>
                                viewTicket.mutate({ ticketId: traveller.ticket_id!, tab: openFileTab() })
                              }
                              title={`Open the ticket uploaded for ${traveller.full_name}`}
                            >
                              <Eye size={13} />
                              View ticket
                            </Button>
                          )}
                          {traveller.status === 'PENDING' && (
                            <>
                              <Button
                                size="sm"
                                loading={decide.isPending && decide.variables?.traveller.id === traveller.id}
                                onClick={() => start(request, traveller, 'APPROVED')}
                              >
                                Approve
                              </Button>
                              <Button
                                size="sm"
                                variant="danger"
                                onClick={() => start(request, traveller, 'REJECTED')}
                              >
                                Reject
                              </Button>
                            </>
                          )}
                          {traveller.status === 'APPROVED' && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => start(request, traveller, 'BOOKED')}
                            >
                              <Ticket size={13} />
                              Mark booked
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  {isOpen && (
                    <div className="mt-3 space-y-3">
                      {/* Tickets sit beside the edit history: both are things an
                          admin reads before they commit to anything. */}
                      <div className="rounded-md border border-border bg-surface-sunken px-3 py-3">
                        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                          <Ticket size={12} />
                          Tickets
                        </p>
                        <TicketPanel
                          requestId={request.id}
                          travellers={request.travellers}
                          onChanged={refresh}
                        />
                      </div>

                      <div className="rounded-md border border-border bg-surface-sunken px-3 py-3">
                        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                          <IndianRupee size={12} />
                          Cost
                        </p>
                        <CostPanel
                          requestId={request.id}
                          travellers={request.travellers}
                          onChanged={refresh}
                        />
                      </div>

                      <div className="grid gap-3 lg:grid-cols-2">
                        <div className="rounded-md border border-border bg-surface-sunken px-3 py-3">
                          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                            <Gavel size={12} />
                            Approval log
                          </p>
                          <DecisionLog travellers={request.travellers} />
                        </div>

                        <div className="rounded-md border border-border bg-surface-sunken px-3 py-3">
                          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold">
                            <History size={12} />
                            Edit history
                          </p>
                          <RevisionHistory requestId={request.id} />
                        </div>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {requests.data && total > 0 && (
          <Pager
            page={requests.data.page}
            pages={pages}
            total={total}
            pageSize={requests.data.page_size}
            shown={rows.length}
            busy={requests.isFetching}
            onPage={setPage}
            onPageSize={(size) => {
              setPageSize(size);
              setPage(1);
            }}
          />
        )}
      </Card>

      <Modal
        open={pending !== null}
        onClose={close}
        title={dialogTitle}
        description={
          pending?.needsOverride
            ? 'This traveller already has something on these dates. Your reason is recorded in the activity log.'
            : pending?.to === 'BOOKED'
              ? 'The traveller is told once this is saved.'
              : 'The traveller is shown this reason.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={close}>
              Cancel
            </Button>
            <Button
              variant={pending?.to === 'REJECTED' ? 'danger' : 'primary'}
              loading={decide.isPending}
              disabled={
                reason.trim().length < 3 ||
                (pending?.to === 'BOOKED' && reference.trim().length < 2)
              }
              onClick={confirm}
            >
              {pending?.needsOverride
                ? 'Approve anyway'
                : pending?.to === 'BOOKED'
                  ? 'Save booking'
                  : pending?.to === 'APPROVED'
                    ? 'Approve traveller'
                    : pending?.to === 'REJECTED'
                      ? 'Reject traveller'
                      : 'Cancel traveller'}
            </Button>
          </>
        }
      >
        {pending?.needsOverride && (
          <div className="mb-4">
            <ConflictList
              conflicts={pending.request.conflicts.filter(
                (conflict) => conflict.user_id === pending.traveller.user_id,
              )}
              footnote={null}
            />
          </div>
        )}

        <div className="space-y-4">
          {/* The admin decides with the manager's view in front of them - or
              knowing it has not come yet, which never stops them deciding. */}
          {pending && (pending.traveller.manager_recommendation || pending.traveller.manager_name) && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Manager’s recommendation</p>
              <ManagerReview traveller={pending.traveller} />
              {!pending.traveller.manager_recommendation && pending.traveller.status === 'PENDING' && (
                <p className="text-2xs text-text-subtle">
                  You can decide now - the final decision is yours.
                </p>
              )}
            </div>
          )}

          {pending?.to === 'BOOKED' && (
            <Field
              label="Ticket or booking reference"
              htmlFor="reference"
              required
              hint="PNR, ticket number or hotel confirmation."
            >
              <Input
                id="reference"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="6E-4412 / PNR QK8T2M"
              />
            </Field>
          )}

          {/* Required on every decision now, approvals included. */}
          <Field
            label={pending?.needsOverride ? 'Why approve anyway?' : 'Reason'}
            htmlFor="decision-reason"
            required
            hint="Shown to the traveller and kept in the activity log."
          >
            <Input
              id="decision-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                pending?.needsOverride
                  ? 'The earlier booking was released'
                  : pending?.to === 'BOOKED'
                    ? 'Flight confirmed with the travel desk'
                    : pending?.to === 'APPROVED'
                      ? 'Needed on site for the client walkthrough'
                      : 'Covered by a colleague already in that city'
              }
            />
          </Field>

          {/* The record is never optional; only the email is. Someone told in
              person, or a batch being tidied up retrospectively, should not
              have an inbox filled on their behalf. */}
          <label className="flex cursor-pointer items-start gap-2.5 rounded-md bg-surface-sunken px-3 py-2.5">
            <input
              type="checkbox"
              checked={notify}
              onChange={(e) => setNotify(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--primary))]"
            />
            <span className="text-xs">
              <span className="font-medium">Email {pending?.traveller.full_name}</span>
              <span className="block text-text-muted">
                {notify
                  ? `They get an email with this decision and the reason.${
                      pending?.traveller.manager_name
                        ? ` ${pending.traveller.manager_name} is copied on it.`
                        : ''
                    }`
                  : 'No email. The in-app notice and the activity log are still written.'}
              </span>
            </span>
          </label>
        </div>
      </Modal>
    </div>
  );
}
