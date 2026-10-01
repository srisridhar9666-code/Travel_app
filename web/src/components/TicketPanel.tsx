import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  FileWarning,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react';
import { useRef, useState } from 'react';
import toast from 'react-hot-toast';

import { Badge, Button, Field, Input, Skeleton } from '@/components/ui';
import {
  confirmTicket,
  discardTicket,
  errorMessage,
  fetchTicketFile,
  fetchTickets,
  reextractTicket,
  uploadTicket,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  REVIEW_THRESHOLD,
  TICKET_FIELD_LABELS,
  TICKET_STATUS_LABELS,
  type RequestTraveller,
  type Ticket,
  type TicketStatus,
} from '@/types';

const STATUS_TONE: Record<TicketStatus, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  UPLOADED: 'neutral',
  EXTRACTING: 'info',
  EXTRACTED: 'warning',
  CONFIRMED: 'success',
  FAILED: 'danger',
  DISCARDED: 'neutral',
};

/** Fields worth showing for this kind of document. A flight ticket has no
 *  check-in date and a hotel confirmation has no flight number; rendering the
 *  empty ones would bury the six that matter. */
function presentFields(ticket: Ticket): [string, string][] {
  const raw: [string, unknown][] = [
    ['booking_reference', ticket.booking_reference],
    ['carrier', ticket.carrier],
    ['service_number', ticket.service_number],
    ['passenger_name', ticket.passenger_name],
    ['origin', ticket.origin],
    ['destination', ticket.destination],
    ['depart_at', ticket.depart_at],
    ['arrive_at', ticket.arrive_at],
    ['hotel_name', ticket.hotel_name],
    ['check_in', ticket.check_in],
    ['check_out', ticket.check_out],
  ];
  return raw
    .filter(([, value]) => value != null && value !== '')
    .map(([key, value]) => {
      const text =
        key === 'depart_at' || key === 'arrive_at'
          ? new Date(String(value)).toLocaleString(undefined, {
              day: '2-digit',
              month: 'short',
              year: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })
          : String(value);
      return [key, text] as [string, string];
    });
}

function ExtractedFields({ ticket }: { ticket: Ticket }) {
  const fields = presentFields(ticket);
  if (fields.length === 0) return null;

  return (
    <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {fields.map(([key, text]) => {
        const score = ticket.confidence?.[key];
        const unsure = typeof score === 'number' && score < REVIEW_THRESHOLD;
        return (
          <div key={key} className="flex items-baseline justify-between gap-3">
            <dt className="text-2xs uppercase tracking-wide text-text-subtle">
              {TICKET_FIELD_LABELS[key] ?? key}
            </dt>
            <dd
              className={cn(
                'text-right text-xs font-medium',
                unsure && 'rounded bg-warning-soft px-1.5 py-0.5 text-warning',
              )}
              title={
                typeof score === 'number'
                  ? `The model reported ${Math.round(score * 100)}% confidence`
                  : undefined
              }
            >
              {text}
              {unsure && <span className="ml-1 font-normal">· check this</span>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function TicketCard({ ticket, onChanged }: { ticket: Ticket; onChanged: () => void }) {
  const [reference, setReference] = useState(ticket.booking_reference ?? '');
  const [notify, setNotify] = useState(true);

  const view = useMutation({
    mutationFn: () => fetchTicketFile(ticket.id),
    onSuccess: (blob) => {
      // Opened from a blob rather than linked: the endpoint needs the bearer
      // token, and the document carries a PNR and a passenger name.
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const confirm = useMutation({
    mutationFn: () =>
      confirmTicket(ticket.id, {
        booking_reference: reference.trim(),
        carrier: ticket.carrier,
        service_number: ticket.service_number,
        notify,
      }),
    onSuccess: () => {
      toast.success(notify ? 'Booked — the traveller has been told' : 'Booked, no notice sent');
      onChanged();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const reread = useMutation({
    mutationFn: () => reextractTicket(ticket.id),
    onSuccess: () => {
      toast.success('Read again');
      onChanged();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const discard = useMutation({
    mutationFn: () => discardTicket(ticket.id),
    onSuccess: () => {
      toast.success('Ticket discarded');
      onChanged();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const corrected =
    ticket.status === 'CONFIRMED' &&
    ticket.confirmed_reference !== ticket.booking_reference;

  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={STATUS_TONE[ticket.status]}>{TICKET_STATUS_LABELS[ticket.status]}</Badge>
        <span className="truncate text-xs text-text-muted">{ticket.file_name}</span>
        {ticket.model_id && (
          <span
            className="inline-flex items-center gap-1 text-2xs text-text-subtle"
            title={`Read by ${ticket.model_id}`}
          >
            <Sparkles size={10} />
            {ticket.model_id}
          </span>
        )}
        {ticket.file_name && ticket.status !== 'DISCARDED' && (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            loading={view.isPending}
            onClick={() => view.mutate()}
            title="Open the document"
          >
            <Eye size={13} />
            Document
          </Button>
        )}
      </div>

      {ticket.status === 'FAILED' && (
        <div className="mt-2 rounded-md border border-danger/40 bg-danger-soft px-3 py-2">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-danger">
            <FileWarning size={13} />
            Could not read this document
          </p>
          <p className="mt-1 text-2xs text-text-muted">{ticket.extraction_error}</p>
          <p className="mt-1.5 text-2xs text-text-subtle">
            Read it again, or discard it and mark the booking by hand from the traveller row.
          </p>
        </div>
      )}

      {ticket.mismatches.length > 0 && ticket.status !== 'DISCARDED' && (
        <div className="mt-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-warning">
            <AlertTriangle size={13} />
            This ticket does not match the request
          </p>
          <ul className="mt-1 space-y-0.5">
            {ticket.mismatches.map((note) => (
              <li key={note} className="text-2xs leading-relaxed text-text-muted">
                {note}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(ticket.status === 'EXTRACTED' || ticket.status === 'CONFIRMED') && (
        <div className="mt-2.5">
          <ExtractedFields ticket={ticket} />
          {ticket.needs_review.length > 0 && ticket.status === 'EXTRACTED' && (
            <p className="mt-2 text-2xs text-warning">
              The model was unsure about{' '}
              {ticket.needs_review.map((f) => TICKET_FIELD_LABELS[f] ?? f).join(', ')}. Read the
              document before confirming.
            </p>
          )}
        </div>
      )}

      {ticket.status === 'EXTRACTED' && (
        <div className="mt-3 border-t border-border pt-3">
          {/* Addendum B3: the extraction pre-fills this, it does not save it.
              A person types or accepts the reference that gets booked. */}
          <Field
            label="Confirm the reference to book"
            htmlFor={`ref-${ticket.id}`}
            required
            hint="Pre-filled from the document. Correct it if the model misread."
          >
            <Input
              id={`ref-${ticket.id}`}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              className="font-mono"
            />
          </Field>

          <label className="mt-2 flex items-center gap-2 text-2xs text-text-muted">
            <input
              type="checkbox"
              checked={notify}
              onChange={(e) => setNotify(e.target.checked)}
              className="h-3.5 w-3.5 accent-[rgb(var(--primary))]"
            />
            Email the traveller their confirmation
          </label>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              size="sm"
              loading={confirm.isPending}
              disabled={reference.trim().length < 2}
              onClick={() => confirm.mutate()}
            >
              <CheckCircle2 size={13} />
              Confirm and book
            </Button>
            <Button
              size="sm"
              variant="secondary"
              loading={reread.isPending}
              onClick={() => reread.mutate()}
            >
              <RefreshCw size={13} />
              Read again
            </Button>
            <Button
              size="sm"
              variant="ghost"
              loading={discard.isPending}
              onClick={() => discard.mutate()}
            >
              <Trash2 size={13} />
              Discard
            </Button>
          </div>
        </div>
      )}

      {ticket.status === 'FAILED' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" loading={reread.isPending} onClick={() => reread.mutate()}>
            <RefreshCw size={13} />
            Read again
          </Button>
          <Button size="sm" variant="ghost" loading={discard.isPending} onClick={() => discard.mutate()}>
            <Trash2 size={13} />
            Discard
          </Button>
        </div>
      )}

      {ticket.status === 'CONFIRMED' && (
        <p className="mt-2 text-2xs text-text-subtle">
          Booked as <span className="font-mono text-text">{ticket.confirmed_reference}</span> by{' '}
          {ticket.confirmed_by_name}
          {corrected && ' — corrected from what the model read'}.
        </p>
      )}
    </div>
  );
}

/**
 * Tickets for one request, with the review step that stands between an
 * extraction and a booking (addendum B3).
 */
export default function TicketPanel({
  requestId,
  travellers,
  onChanged,
}: {
  requestId: number;
  travellers: RequestTraveller[];
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadFor, setUploadFor] = useState<number | null>(null);

  const tickets = useQuery({
    queryKey: ['tickets', requestId],
    queryFn: () => fetchTickets(requestId),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['tickets', requestId] });
    onChanged();
  };

  const upload = useMutation({
    mutationFn: (vars: { travellerId: number; file: File }) =>
      uploadTicket(requestId, vars.travellerId, vars.file),
    onSuccess: (ticket) => {
      toast.success(
        ticket.status === 'FAILED'
          ? 'Uploaded, but the document could not be read'
          : 'Uploaded and read — check the fields before confirming',
      );
      refresh();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  // Only someone who has been approved can have a ticket attached; a rejected
  // or cancelled traveller has nothing to book.
  const bookable = travellers.filter(
    (t) => t.status === 'APPROVED' || t.status === 'BOOKED',
  );

  const rows = tickets.data ?? [];

  return (
    <div className="space-y-2.5">
      <input
        ref={fileInput}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/heic,application/pdf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && uploadFor !== null) upload.mutate({ travellerId: uploadFor, file });
          e.target.value = '';
        }}
      />

      {bookable.length === 0 ? (
        <p className="text-2xs text-text-subtle">
          Approve a traveller before attaching their ticket.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {bookable.map((traveller) => (
            <Button
              key={traveller.id}
              size="sm"
              variant="secondary"
              loading={upload.isPending && upload.variables?.travellerId === traveller.id}
              onClick={() => {
                setUploadFor(traveller.id);
                fileInput.current?.click();
              }}
            >
              <Upload size={13} />
              Ticket for {traveller.full_name.split(' ')[0]}
            </Button>
          ))}
        </div>
      )}

      {upload.isPending && (
        <p className="text-2xs text-text-subtle">Reading the document…</p>
      )}

      {tickets.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : (
        rows.map((ticket) => (
          <div key={ticket.id}>
            <p className="mb-1 text-2xs text-text-subtle">{ticket.traveller_name}</p>
            <TicketCard ticket={ticket} onChanged={refresh} />
          </div>
        ))
      )}
    </div>
  );
}
