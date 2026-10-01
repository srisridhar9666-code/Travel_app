import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BedDouble,
  Car,
  CheckSquare,
  ChevronDown,
  History,
  IndianRupee,
  Plane,
  Ticket,
  Gavel,
} from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
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
  Skeleton,
} from '@/components/ui';
import {
  decideBatch,
  errorMessage,
  fetchQueueCounts,
  fetchRequests,
  fetchRevisions,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  REQUEST_STATUS_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type BatchDecisionItem,
  type RequestStatus,
  type RequestTraveller,
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

/** The queue tabs, in the order an admin works through them. */
const TABS: { key: string; label: string; countKey: keyof CountShape }[] = [
  { key: 'SUBMITTED', label: 'Awaiting', countKey: 'awaiting' },
  { key: 'PARTIALLY_APPROVED', label: 'Partly approved', countKey: 'partially_approved' },
  { key: 'APPROVED', label: 'Approved', countKey: 'approved' },
  { key: 'BOOKED', label: 'Booked', countKey: 'booked' },
  { key: 'EXPIRED', label: 'Expired', countKey: 'expired' },
  { key: 'REJECTED', label: 'Rejected', countKey: 'rejected' },
];

type CountShape = {
  awaiting: number;
  partially_approved: number;
  approved: number;
  booked: number;
  rejected: number;
  cancelled: number;
  expired: number;
};

const dayMonth = (iso: string) =>
  new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, {
    day: '2-digit',
    month: 'short',
  });

const dayTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });

function itinerary(request: TravelRequest): string {
  if (request.request_type === 'HOTEL') {
    const nights = request.check_out
      ? `${dayMonth(request.check_in!)} – ${dayMonth(request.check_out)}`
      : dayMonth(request.check_in!);
    return `${request.hotel_city} · ${nights}`;
  }
  return `${request.origin} → ${request.destination} · ${
    request.start_at ? dayTime(request.start_at) : ''
  }`;
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
              {traveller.decided_at ? new Date(traveller.decided_at).toLocaleString() : ''}
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
              {revision.editor_name} · {new Date(revision.created_at).toLocaleString()}
            </span>
          </div>
          {revision.changes && (
            <dl className="mt-1 space-y-0.5">
              {Object.entries(revision.changes).map(([field, change]) => (
                <div key={field} className="flex flex-wrap gap-x-1.5 text-2xs">
                  <dt className="text-text-subtle">{field.replace(/_/g, ' ')}</dt>
                  <dd className="text-text-muted">
                    <span className="line-through opacity-70">{String(change.from ?? '—')}</span>
                    {' → '}
                    <span className="font-medium text-text">{String(change.to ?? '—')}</span>
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

  const [tab, setTab] = useState('SUBMITTED');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [reason, setReason] = useState('');
  const [notify, setNotify] = useState(true);
  const [reference, setReference] = useState('');

  const counts = useQuery({ queryKey: ['queue-counts'], queryFn: fetchQueueCounts });
  const requests = useQuery({
    queryKey: ['queue', tab, search],
    queryFn: () =>
      fetchRequests({
        mine: false,
        status: tab,
        search: search.trim() || undefined,
        page_size: 100,
      }),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['queue'] });
    queryClient.invalidateQueries({ queryKey: ['queue-counts'] });
    queryClient.invalidateQueries({ queryKey: ['requests'] });
    queryClient.invalidateQueries({ queryKey: ['tickets'] });
  };

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
    onSuccess: () => {
      toast.success('Decision recorded');
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
  const countData = counts.data;

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

      {countData && (countData.expired > 0 || (counts.data?.with_conflicts ?? 0) > 0) && (
        <div className="flex flex-wrap gap-3">
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
              {countData && (
                <span className="ml-1.5 text-text-subtle">{countData[item.countKey]}</span>
              )}
            </button>
          ))}
        </div>

        <CardHeader title={`${requests.data?.total ?? 0} in this view`} />

        <div className="border-b border-border px-5 py-3">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search place or notes"
            className="w-full sm:max-w-64"
            aria-label="Search the queue"
          />
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
              tab === 'SUBMITTED'
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
                        {request.project_code} · raised by {request.requester_name}
                        {request.mode && ` · ${TRAVEL_MODE_LABELS[request.mode]}`}
                        {request.notes && ` · ${request.notes}`}
                      </p>
                    </div>

                    <Button
                      variant="ghost"
                      size="sm"
                      aria-expanded={isOpen}
                      title={isOpen ? 'Hide history' : 'Show history'}
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

                        <div className="ml-auto flex gap-1.5">
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
                  ? 'They get an email with this decision and the reason.'
                  : 'No email. The in-app notice and the activity log are still written.'}
              </span>
            </span>
          </label>
        </div>
      </Modal>
    </div>
  );
}
