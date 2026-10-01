import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  AlertTriangle,
  ArrowRight,
  CalendarCheck,
  CheckSquare,
  Clock,
  IndianRupee,
  MapPin,
  Moon,
  Plane,
  Users,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { Combobox } from '@/components/Combobox';
import {
  DateRangePicker,
  describeRange,
  rangeFor,
  type DateRange,
} from '@/components/DateRangePicker';
import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
import { Columns, HorizontalBars, StatTile, formatMoney } from '@/components/charts';
import { Button, Card, CardHeader, Field, PageHeader, Select, Skeleton } from '@/components/ui';
import { fetchFilterOptions, fetchInsights, fetchQueueCounts } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import {
  REQUEST_TYPE_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type Insights,
  type RequestType,
  type TravelMode,
  type TravellerStatus,
} from '@/types';

const STATUS_BAR: Record<TravellerStatus, string> = {
  BOOKED: 'bg-success',
  APPROVED: 'bg-info',
  PENDING: 'bg-warning',
  REJECTED: 'bg-danger',
  CANCELLED: 'bg-border-strong',
};

function periodLabel(period: string, grain: Insights['grain']) {
  if (grain === 'month') return format(new Date(`${period}-01T00:00:00`), 'MMM yy');
  return format(new Date(`${period}T00:00:00`), 'd MMM');
}

/** Where the money and the movements stand by status, as one stacked bar. */
function StatusStrip({ data }: { data: Insights['by_status'] }) {
  const total = data.reduce((sum, row) => sum + row.count, 0);
  if (total === 0) return <p className="py-6 text-center text-xs text-text-subtle">No requests in this period.</p>;
  return (
    <div>
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-surface-sunken">
        {data
          .filter((row) => row.count > 0)
          .map((row) => (
            <div
              key={row.status}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', STATUS_BAR[row.status])}
              style={{ width: `${(row.count / total) * 100}%` }}
              title={`${TRAVELLER_STATUS_LABELS[row.status]}: ${row.count}`}
            />
          ))}
      </div>
      <ul className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5">
        {data.map((row) => (
          <li key={row.status} className="flex items-center gap-2 text-sm">
            <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', STATUS_BAR[row.status])} />
            <span className="text-text-muted">{TRAVELLER_STATUS_LABELS[row.status]}</span>
            <span className="ml-auto font-semibold tabular-nums">{row.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AdminDashboard() {
  const [range, setRange] = useState<DateRange>(() => rangeFor('last_30'));
  const [projectId, setProjectId] = useState('');
  const [userId, setUserId] = useState<number | undefined>();
  const [state, setState] = useState('');
  const [requestType, setRequestType] = useState<RequestType | ''>('');
  const [trendMetric, setTrendMetric] = useState<'movements' | 'spent'>('movements');

  const queue = useQuery({ queryKey: ['queue-counts'], queryFn: fetchQueueCounts });
  const options = useQuery({ queryKey: ['filter-options'], queryFn: fetchFilterOptions });

  const filters = {
    since: range.since || '2000-01-01',
    until: range.until || undefined,
    project_id: projectId ? Number(projectId) : undefined,
    user_id: userId,
    state: state || undefined,
    request_type: requestType || undefined,
  };
  const insights = useQuery({
    queryKey: ['insights', filters],
    queryFn: () => fetchInsights(filters),
    placeholderData: keepPreviousData,
  });

  const people = new Map(
    (options.data?.people ?? []).map((p) => [
      p.employee_code ? `${p.full_name} (${p.employee_code})` : p.full_name,
      p.id,
    ]),
  );
  const personLabel = [...people.entries()].find(([, id]) => id === userId)?.[0] ?? '';
  const sliced = Boolean(projectId || userId || state || requestType);
  const data = insights.data;
  const k = data?.kpis;

  // Deep link into the log with the same window, for "who exactly?".
  const logLink = (extra: Record<string, string> = {}) => {
    const search = new URLSearchParams({
      range: range.preset,
      ...(range.preset === 'custom' ? { since: range.since, until: range.until } : {}),
      ...(projectId ? { campaign: projectId } : {}),
      ...(state ? { state } : {}),
      ...(requestType ? { type: requestType } : {}),
      ...(userId ? { user: String(userId) } : {}),
      ...extra,
    });
    return `/travel-logs?${search.toString()}`;
  };

  return (
    <div className="space-y-6">
      <Card className="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="When" htmlFor="dash-range" className="sm:col-span-2 lg:col-span-1">
            <DateRangePicker
              id="dash-range"
              value={range}
              onChange={setRange}
              presets={['this_month', 'last_month', 'last_7', 'last_30', 'last_90', 'next_30', 'this_year', 'all', 'custom']}
            />
          </Field>
          <Field label="Campaign" htmlFor="dash-campaign">
            <Select id="dash-campaign" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">All campaigns</option>
              {(options.data?.projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Employee" htmlFor="dash-person">
            <Combobox
              id="dash-person"
              value={personLabel}
              options={[...people.keys()]}
              placeholder="Everyone"
              emptyText="Nobody by that name."
              onChange={(label) => setUserId(people.get(label))}
              action={
                userId
                  ? { label: 'Everyone', icon: <Users size={16} className="mt-0.5 shrink-0 text-text-subtle" />, onSelect: () => setUserId(undefined) }
                  : undefined
              }
            />
          </Field>
          <Field label="State" htmlFor="dash-state">
            <Select id="dash-state" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">All states</option>
              {(options.data?.states ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Type" htmlFor="dash-type">
            <Select
              id="dash-type"
              value={requestType}
              onChange={(e) => setRequestType(e.target.value as RequestType | '')}
            >
              <option value="">All types</option>
              {(Object.keys(REQUEST_TYPE_LABELS) as RequestType[]).map((t) => (
                <option key={t} value={t}>
                  {REQUEST_TYPE_LABELS[t]}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border pt-4 text-sm text-text-muted">
          <span>
            Showing <span className="font-medium text-text">{describeRange(range)}</span>
            {insights.isFetching && !insights.isPending && <span className="ml-2 text-text-subtle">Updating…</span>}
          </span>
          {sliced && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setProjectId('');
                setUserId(undefined);
                setState('');
                setRequestType('');
              }}
            >
              <X size={14} />
              Clear filters
            </Button>
          )}
          <Link to={logLink()} className="ml-auto inline-flex items-center gap-1 font-medium text-brand-strong hover:underline">
            Open in travel logs <ArrowRight size={14} />
          </Link>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link to="/approvals" className="rounded-xl focus-visible:outline-offset-4">
          <StatTile
            label="Awaiting a decision"
            value={queue.data ? String(queue.data.awaiting) : '—'}
            hint={
              queue.data && queue.data.with_conflicts > 0
                ? `${queue.data.with_conflicts} with a calendar clash`
                : 'Right now, all dates'
            }
            tone={queue.data && queue.data.awaiting > 0 ? 'warning' : 'default'}
            icon={<CheckSquare size={15} />}
          />
        </Link>
        <StatTile
          label="Booked spend"
          value={k ? formatMoney(k.spent) : '—'}
          hint={k ? `${formatMoney(k.committed)} approved, not yet booked` : undefined}
          icon={<IndianRupee size={15} />}
        />
        <StatTile
          label="People travelling"
          value={k ? String(k.people) : '—'}
          hint={k ? `${k.movements} movements on ${k.requests} requests` : undefined}
          icon={<Users size={15} />}
        />
        <StatTile
          label="Hotel nights"
          value={k ? String(k.nights) : '—'}
          hint={k ? `Average booking ${formatMoney(k.average_per_booking)}` : undefined}
          icon={<Moon size={15} />}
        />
      </div>

      {k && k.uncosted > 0 && (
        <Link
          to="/analytics"
          className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning hover:underline"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {k.uncosted} booked {k.uncosted === 1 ? 'trip has' : 'trips have'} no cost recorded, so spend reads low. Add them in cost analytics.
        </Link>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title={trendMetric === 'movements' ? 'Movements over time' : 'Booked spend over time'}
            description={data ? `By ${data.grain}, on the day each trip starts.` : undefined}
            action={
              <div className="inline-flex rounded-lg border border-border bg-surface-sunken p-0.5 text-xs">
                {(['movements', 'spent'] as const).map((metric) => (
                  <button
                    key={metric}
                    type="button"
                    onClick={() => setTrendMetric(metric)}
                    className={cn(
                      'rounded-md px-3 py-1.5 font-medium transition-colors',
                      trendMetric === metric ? 'bg-surface text-text shadow-sm' : 'text-text-muted hover:text-text',
                    )}
                  >
                    {metric === 'movements' ? 'Trips' : 'Spend'}
                  </button>
                ))}
              </div>
            }
          />
          <div className="px-4 py-5 sm:px-5">
            {data ? (
              <Columns
                data={data.trend.map((row) => ({
                  label: periodLabel(row.period, data.grain),
                  value: trendMetric === 'movements' ? row.movements : Number(row.spent),
                }))}
                format={trendMetric === 'movements' ? (v) => String(Math.round(v)) : (v) => formatMoney(v)}
                caption={trendMetric === 'movements' ? 'Movements' : 'Booked spend'}
              />
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Where requests stand" description="Every traveller on a request in this window." />
          <div className="px-4 py-5 sm:px-5">
            {data ? <StatusStrip data={data.by_status} /> : <Skeleton className="h-24 w-full" />}
          </div>
        </Card>
      </div>

      <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader title="Top destination states" description="Click one to filter the dashboard." />
          <div className="px-4 py-5 sm:px-5">
            {data ? (
              data.top_states.length ? (
                <ul className="space-y-1">
                  {data.top_states.map((row) => (
                    <li key={row.label}>
                      <button
                        type="button"
                        onClick={() => setState(state === row.label ? '' : row.label)}
                        className={cn(
                          'w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-sunken',
                          state === row.label && 'bg-surface-sunken',
                        )}
                      >
                        <HorizontalBars data={[{ label: row.label, value: row.count }]} max={data.top_states[0].count} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="py-6 text-center text-xs text-text-subtle">No travel in this period.</p>
              )
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Top places" description="Destinations and hotel cities." />
          <div className="px-4 py-5 sm:px-5">
            {data ? (
              <HorizontalBars
                data={data.top_places.map((row) => ({ label: row.label, value: row.count }))}
                empty="No travel in this period."
              />
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="How people travel" />
          <div className="space-y-5 px-4 py-5 sm:px-5">
            {data ? (
              <>
                <HorizontalBars
                  data={data.by_type.map((row) => ({
                    label: REQUEST_TYPE_LABELS[row.request_type],
                    value: row.count,
                    detail: Number(row.spent) > 0 ? `${formatMoney(row.spent)} booked` : undefined,
                  }))}
                />
                {data.by_mode.length > 0 && (
                  <div className="flex flex-wrap gap-2 border-t border-border pt-4">
                    {data.by_mode.map((row) => (
                      <span key={row.label} className="inline-flex items-center gap-1.5 rounded-full bg-surface-sunken px-3 py-1 text-xs">
                        <Plane size={12} className="text-text-subtle" />
                        {TRAVEL_MODE_LABELS[row.label as TravelMode] ?? row.label}
                        <span className="font-semibold tabular-nums">{row.count}</span>
                      </span>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <Skeleton className="h-40 w-full" />
            )}
          </div>
        </Card>

        {/* self-start: a short campaign list should not stretch to the
            height of the people list beside it. */}
        <Card className="self-start xl:col-span-2">
          <CardHeader title="Campaigns" description="Movements and booked spend in this window." />
          {data ? (
            data.by_campaign.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-text-muted">
                      <th className="px-4 py-2.5 font-medium sm:px-5">Campaign</th>
                      <th className="px-4 py-2.5 text-right font-medium">Trips</th>
                      <th className="hidden px-4 py-2.5 text-right font-medium sm:table-cell">People</th>
                      <th className="px-4 py-2.5 text-right font-medium sm:px-5">Spend</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.by_campaign.map((row) => (
                      <tr
                        key={row.project_id}
                        className="cursor-pointer hover:bg-surface-sunken/60"
                        onClick={() => setProjectId(projectId === String(row.project_id) ? '' : String(row.project_id))}
                      >
                        <td className="px-4 py-3 sm:px-5">
                          <span className="font-medium">{row.name}</span>
                          <span className="block font-mono text-xs text-text-subtle sm:ml-2 sm:inline">{row.code}</span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums">{row.count}</td>
                        <td className="hidden px-4 py-3 text-right tabular-nums sm:table-cell">{row.people}</td>
                        <td className="px-4 py-3 text-right tabular-nums sm:px-5">{formatMoney(row.spent)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="px-5 py-8 text-center text-xs text-text-subtle">No campaign travel in this period.</p>
            )
          ) : (
            <div className="p-5"><Skeleton className="h-32 w-full" /></div>
          )}
        </Card>

        <Card>
          <CardHeader title="Most travelled" description="Open a person's log for the same dates." />
          <div className="px-2 py-2">
            {data ? (
              data.top_travellers.length ? (
                <ul>
                  {data.top_travellers.map((row) => (
                    <li key={row.user_id}>
                      <Link
                        to={logLink({ user: String(row.user_id) })}
                        className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-surface-sunken"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-sunken text-xs font-semibold text-text-muted">
                          {row.full_name.split(/\s+/).slice(0, 2).map((p) => p[0]).join('')}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{row.full_name}</span>
                          <span className="block text-xs text-text-subtle">
                            {row.count} trip{row.count === 1 ? '' : 's'}
                            {row.nights > 0 && ` · ${row.nights} night${row.nights === 1 ? '' : 's'}`}
                          </span>
                        </span>
                        <span className="text-sm tabular-nums text-text-muted">{formatMoney(row.spent)}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-3 py-8 text-center text-xs text-text-subtle">No travel in this period.</p>
              )
            ) : (
              <div className="p-3"><Skeleton className="h-32 w-full" /></div>
            )}
          </div>
        </Card>
      </div>

      <div className="flex flex-wrap gap-3 text-sm">
        <Link to="/approvals" className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <Clock size={15} /> Approvals queue
        </Link>
        <Link to="/analytics" className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <IndianRupee size={15} /> Cost analytics
        </Link>
        <Link to={logLink()} className="inline-flex items-center gap-1.5 font-medium text-brand-strong hover:underline">
          <MapPin size={15} /> Travel logs
        </Link>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const user = useAuth((s) => s.user);

  // The operational picture, for the people who act on it. Ground staff get
  // their own travel below instead - nothing in the admin half is theirs to do.
  const isAdmin = user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN';
  const firstName = user?.full_name.split(/\s+/)[0] ?? 'there';

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Welcome back, ${firstName}`}
        description={
          isAdmin
            ? 'Filter by dates, campaign, person or place - every number below follows.'
            : 'Raise a request from My requests. Decisions and tickets arrive in your notifications.'
        }
        actions={
          !isAdmin && (
            <Link to="/requests">
              <Button>
                <CalendarCheck size={16} />
                My requests
              </Button>
            </Link>
          )
        }
      />

      {isAdmin && <AdminDashboard />}

      {/* Ground staff see their own movements. */}
      {!isAdmin && user && (
        <Card>
          <CardHeader
            title="Your travel"
            description="Every trip you were on, including ones a colleague raised."
          />
          <div className="px-4 py-4 sm:px-5">
            <TravelHistoryPanel userId={user.id} compact />
          </div>
        </Card>
      )}
    </div>
  );
}
