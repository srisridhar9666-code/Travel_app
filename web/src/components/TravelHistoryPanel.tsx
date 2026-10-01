import { useQuery } from '@tanstack/react-query';
import {
  BedDouble,
  CalendarRange,
  Car,
  MapPin,
  Plane,
  Train,
  Users as UsersIcon,
} from 'lucide-react';
import { useState } from 'react';

import { Badge, EmptyState, Select, Skeleton } from '@/components/ui';
import { errorMessage, fetchTravelHistory } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  REQUEST_TYPE_LABELS,
  type RequestType,
  type TravelMode,
  type TravelMovement,
} from '@/types';

/** §5 asks for "minimum 2+ months". We keep everything and let the view widen. */
const WINDOWS = [
  { days: 90, label: 'Last 90 days' },
  { days: 180, label: 'Last 6 months' },
  { days: 365, label: 'Last year' },
  { days: 3650, label: 'Everything' },
];

function sinceDate(days: number) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function MovementIcon({ type, mode }: { type: RequestType; mode: TravelMode | null }) {
  const size = 14;
  if (type === 'HOTEL') return <BedDouble size={size} />;
  if (type === 'LOCAL_CAB') return <Car size={size} />;
  if (mode === 'TRAIN') return <Train size={size} />;
  if (mode === 'BUS') return <Car size={size} />;
  return <Plane size={size} />;
}

function formatDay(iso: string | null) {
  if (!iso) return 'Date not set';
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function Movement({ entry }: { entry: TravelMovement }) {
  const booked = entry.status === 'BOOKED';

  return (
    <li className="relative pl-7">
      {/* The timeline spine. The last item's line is clipped by the parent's
          overflow so it does not dangle past the final marker. */}
      <span
        aria-hidden
        className="absolute left-[9px] top-5 h-full w-px bg-border"
      />
      <span
        aria-hidden
        className={cn(
          'absolute left-0 top-1 grid h-[18px] w-[18px] place-items-center rounded-full ring-2 ring-surface',
          booked ? 'bg-success-soft text-success' : 'bg-surface-sunken text-text-subtle',
        )}
      >
        <MovementIcon type={entry.request_type} mode={entry.mode} />
      </span>

      <div className="pb-5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{entry.where}</span>
          {entry.nights != null && (
            <span className="text-xs text-text-muted">
              {entry.nights} night{entry.nights === 1 ? '' : 's'}
            </span>
          )}
          <Badge tone={booked ? 'success' : entry.status === 'PENDING' ? 'warning' : 'neutral'}>
            {entry.status.toLowerCase()}
          </Badge>
        </div>

        <div className="mt-0.5 text-xs text-text-muted">
          {formatDay(entry.started_on)}
          {entry.check_out && ` → ${formatDay(entry.check_out)}`}
          {' · '}
          {REQUEST_TYPE_LABELS[entry.request_type]}
          {entry.mode && entry.request_type === 'LONG_DISTANCE' && ` · ${entry.mode.toLowerCase()}`}
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-text-subtle">
          {entry.project_code && (
            <span className="font-mono">{entry.project_code}</span>
          )}
          {entry.booking_reference && (
            <span className="font-mono">PNR {entry.booking_reference}</span>
          )}
          {entry.cost_amount && (
            <span>
              {entry.cost_currency} {entry.cost_amount}
            </span>
          )}
          {entry.share_with_name && (
            <span className={entry.share_confirmed ? 'text-success' : undefined}>
              Sharing a room with {entry.share_with_name}
              {entry.share_confirmed ? '' : ' (unconfirmed)'}
            </span>
          )}
        </div>

        {/* The "cab companions" §5 asks for — who was actually on this trip. */}
        {entry.companions.length > 0 && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <UsersIcon size={11} className="text-text-subtle" />
            {entry.companions.map((person) => (
              <span
                key={person.user_id}
                className="rounded bg-surface-sunken px-1.5 py-0.5 text-2xs text-text-muted"
                title={person.designation ?? undefined}
              >
                {person.full_name}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  );
}

interface TravelHistoryPanelProps {
  userId: number;
  /** Trims the summary row when the surrounding context is already tight. */
  compact?: boolean;
}

/**
 * The per-employee timeline from SOW section 5.
 *
 * Built from traveller rows rather than requests, so a trip someone was *tagged
 * onto* appears here even though the request belonged to a colleague — which is
 * the whole point of asking "where has this person been".
 */
export function TravelHistoryPanel({ userId, compact = false }: TravelHistoryPanelProps) {
  const [days, setDays] = useState(90);

  const history = useQuery({
    queryKey: ['travel-history', userId, days],
    queryFn: () => fetchTravelHistory(userId, { since: sinceDate(days) }),
  });

  if (history.isPending) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (history.isError) {
    return (
      <EmptyState
        icon={<CalendarRange size={28} />}
        title="Could not load the timeline"
        description={errorMessage(history.error)}
      />
    );
  }

  const { entries, summary } = history.data;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        {!compact && (
          <div className="flex flex-wrap gap-4 text-xs">
            <span>
              <span className="font-semibold">{summary.movements}</span>{' '}
              <span className="text-text-muted">movements</span>
            </span>
            <span>
              <span className="font-semibold">{summary.nights_away}</span>{' '}
              <span className="text-text-muted">nights away</span>
            </span>
            <span>
              <span className="font-semibold">{summary.cities}</span>{' '}
              <span className="text-text-muted">
                {summary.cities === 1 ? 'city' : 'cities'}
              </span>
            </span>
            <span>
              <span className="font-semibold">{summary.travelled_with}</span>{' '}
              <span className="text-text-muted">colleagues</span>
            </span>
          </div>
        )}
        <Select
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
          aria-label="How far back"
          className="ml-auto w-40"
        >
          {WINDOWS.map((w) => (
            <option key={w.days} value={w.days}>
              {w.label}
            </option>
          ))}
        </Select>
      </div>

      {entries.length === 0 ? (
        <EmptyState
          icon={<MapPin size={28} />}
          title="No movements in this period"
          description="Try a longer window, or this person has not travelled yet."
        />
      ) : (
        // overflow-hidden clips the spine below the final marker.
        <ul className="overflow-hidden">
          {entries.map((entry) => (
            <Movement key={`${entry.request_id}-${entry.started_on}`} entry={entry} />
          ))}
        </ul>
      )}
    </div>
  );
}
