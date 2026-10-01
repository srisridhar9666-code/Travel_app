import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  BarChart3,
  CalendarRange,
  IndianRupee,
  MapPin,
  Plane,
  Users,
} from 'lucide-react';
import { useState } from 'react';

import {
  Columns,
  HorizontalBars,
  StatTile,
  formatMoney,
} from '@/components/charts';
import { Badge, Card, CardHeader, EmptyState, Select, Skeleton } from '@/components/ui';
import { errorMessage, fetchAnalytics } from '@/lib/api';
import { cn } from '@/lib/utils';
import { REQUEST_TYPE_LABELS, type RequestType } from '@/types';

/** "2027-03" → "Mar 27", which is what fits under a column. */
function monthLabel(key: string): string {
  const [year, month] = key.split('-');
  const date = new Date(Number(year), Number(month) - 1, 1);
  return `${date.toLocaleString(undefined, { month: 'short' })} ${year.slice(2)}`;
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(90);
  const [months, setMonths] = useState(6);

  const analytics = useQuery({
    queryKey: ['analytics', days, months],
    queryFn: () => fetchAnalytics({ days, months }),
    // Hold the previous render while a new window loads. Dropping back to
    // skeletons on every filter change flashes the whole page and jumps the
    // layout out from under the reader.
    placeholderData: keepPreviousData,
  });

  if (analytics.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (analytics.isError) {
    return (
      <Card>
        <EmptyState
          icon={<BarChart3 size={28} />}
          title="Could not load the reports"
          description={errorMessage(analytics.error)}
        />
      </Card>
    );
  }

  const { overview, by_campaign, by_type, by_month, deployment, uncosted } = analytics.data!;

  return (
    <div
      className={cn(
        'space-y-6 transition-opacity',
        analytics.isFetching && 'opacity-60',
      )}
    >
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cost analytics</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            What travel has cost, by campaign and over time. Only booked trips count as
            spend — approved but unticketed is shown separately as committed.
          </p>
        </div>
        {/* Filters in one row above the charts. */}
        <div className="flex gap-2">
          <Select
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
            aria-label="Activity window"
            className="w-36"
          >
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
            <option value="180">Last 6 months</option>
            <option value="365">Last year</option>
          </Select>
          <Select
            value={String(months)}
            onChange={(e) => setMonths(Number(e.target.value))}
            aria-label="Months charted"
            className="w-36"
          >
            <option value="6">6 months</option>
            <option value="12">12 months</option>
            <option value="24">24 months</option>
          </Select>
        </div>
      </div>

      {/* A KPI row of stat tiles, not a grouped bar chart of four numbers. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Spent"
          value={formatMoney(overview.spent)}
          hint={`${overview.booked_travellers} booked traveller${overview.booked_travellers === 1 ? '' : 's'}`}
          icon={<IndianRupee size={11} />}
        />
        <StatTile
          label="Committed"
          value={formatMoney(overview.committed)}
          hint="Approved, not yet ticketed"
          icon={<CalendarRange size={11} />}
        />
        <StatTile
          label="Average per traveller"
          value={formatMoney(overview.average_per_traveller)}
          hint="Costed bookings only"
          icon={<Plane size={11} />}
        />
        <StatTile
          label="Missing a cost"
          value={String(overview.uncosted)}
          hint={
            overview.uncosted === 0
              ? 'Every booking is costed'
              : 'These figures understate the real spend'
          }
          tone={overview.uncosted > 0 ? 'warning' : 'default'}
          icon={<AlertTriangle size={11} />}
        />
      </div>

      {overview.uncosted > 0 && (
        <Card className="border-warning/40 bg-warning-soft">
          <div className="flex items-start gap-2.5 px-5 py-3.5">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" />
            <div className="min-w-0">
              <p className="text-xs font-semibold text-warning">
                {overview.uncosted} booked{' '}
                {overview.uncosted === 1 ? 'traveller has' : 'travellers have'} no cost
                recorded
              </p>
              <p className="mt-0.5 text-xs text-text-muted">
                Every figure on this page is lower than the truth until these are filled in.
              </p>
              <ul className="mt-2 space-y-0.5">
                {uncosted.slice(0, 5).map((row) => (
                  <li key={row.traveller_id} className="text-2xs text-text-muted">
                    <span className="font-medium text-text">{row.traveller_name}</span> ·{' '}
                    {row.project_code} · {REQUEST_TYPE_LABELS[row.request_type]}
                    {row.booking_reference && ` · ${row.booking_reference}`}
                    {row.trip_date && ` · ${row.trip_date}`}
                  </li>
                ))}
                {uncosted.length > 5 && (
                  <li className="text-2xs text-text-subtle">
                    and {uncosted.length - 5} more
                  </li>
                )}
              </ul>
            </div>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Spend by month of travel"
          description="Bucketed by when the team is in the field, not when the ticket was bought."
        />
        <div className="px-5 py-4">
          <Columns
            data={by_month.map((row) => ({
              label: monthLabel(row.month),
              value: Number(row.spent),
            }))}
            format={(v) => formatMoney(v)}
            caption="Spend by month of travel"
          />
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Campaign financials"
            description="Biggest spend first. Archived campaigns keep their history."
          />
          <div className="px-5 py-4">
            <HorizontalBars
              data={by_campaign.slice(0, 8).map((row) => ({
                label: `${row.code} — ${row.name}`,
                value: Number(row.spent),
                detail:
                  `${row.travellers} traveller${row.travellers === 1 ? '' : 's'}, ` +
                  `${row.trips} trip${row.trips === 1 ? '' : 's'}` +
                  (row.uncosted > 0 ? ` · ${row.uncosted} uncosted` : ''),
              }))}
              format={(v) => formatMoney(v)}
              empty="No campaign has recorded spend yet."
            />
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Where the money goes"
            description="By request type."
          />
          <div className="px-5 py-4">
            <HorizontalBars
              data={by_type.map((row) => ({
                label: REQUEST_TYPE_LABELS[row.request_type as RequestType] ?? row.request_type,
                value: Number(row.spent),
                detail: `${row.travellers} traveller${row.travellers === 1 ? '' : 's'}`,
              }))}
              format={(v) => formatMoney(v)}
            />
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Deployed staff by location"
          description="Where people are going next, counted by the campaign's location rather than where they are based."
          action={
            <Badge tone="neutral">
              <Users size={11} />
              {deployment.reduce((sum, row) => sum + row.people, 0)} people
            </Badge>
          }
        />
        <div className="px-5 py-4">
          <HorizontalBars
            data={deployment.map((row) => ({
              label: row.location,
              value: row.people,
              detail: `${row.trips} upcoming trip${row.trips === 1 ? '' : 's'}`,
            }))}
            format={(v) => `${v}`}
            empty="Nobody is booked to travel yet."
          />
          {deployment.length > 0 && (
            <p className="mt-3 flex items-center gap-1.5 text-2xs text-text-subtle">
              <MapPin size={11} />
              Upcoming trips only — past travel is in the monthly chart above.
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
