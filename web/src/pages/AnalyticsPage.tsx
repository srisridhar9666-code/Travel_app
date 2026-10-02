import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CalendarRange,
  IndianRupee,
  MapPin,
  Plane,
  Users,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { DEFAULT_PRESETS } from '@/components/DateRangePicker';
import { ReportFilterBar, stateChange, useReportFilters } from '@/components/ReportFilters';
import { Columns, HorizontalBars, StatTile, formatMoney } from '@/components/charts';
import { Badge, Card, CardHeader, EmptyState, Skeleton } from '@/components/ui';
import { errorMessage, fetchAnalytics, fetchFilterOptions } from '@/lib/api';
import { periodLabel } from '@/lib/time';
import { cn } from '@/lib/utils';
import { REQUEST_TYPE_LABELS, type PlaceSpend, type RequestType } from '@/types';

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "3 travellers, 4 trips · 1 uncosted": the line under a spend bar. */
function spendDetail(row: { travellers?: number; trips: number; uncosted: number }) {
  const parts = [
    row.travellers !== undefined ? plural(row.travellers, 'traveller') : null,
    plural(row.trips, 'trip'),
  ].filter(Boolean);
  return parts.join(', ') + (row.uncosted > 0 ? ` · ${row.uncosted} uncosted` : '');
}

/** A card whose body is a skeleton until the first load lands, so the filter
 *  bar above never disappears while a new slice loads. */
function Section({
  title,
  description,
  action,
  loading,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  loading: boolean;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader title={title} description={description} action={action} />
      <div className="px-5 py-4">{loading ? <Skeleton className="h-40 w-full" /> : children}</div>
    </Card>
  );
}

export default function AnalyticsPage() {
  const f = useReportFilters('this_year');
  const options = useQuery({ queryKey: ['filter-options'], queryFn: fetchFilterOptions });
  const analytics = useQuery({
    // Starts with 'analytics': entering a cost invalidates that prefix.
    queryKey: ['analytics', f.apiFilters],
    queryFn: () => fetchAnalytics(f.apiFilters),
    // Hold the previous render while a new slice loads. Dropping back to
    // skeletons on every filter change flashes the whole page and jumps the
    // layout out from under the reader.
    placeholderData: keepPreviousData,
  });

  const data = analytics.data;
  const loading = !data;
  const overview = data?.overview;
  // A city bar is keyed by state and city: two states can have a place of one name.
  const cityKey = (row: PlaceSpend) => `${row.state ?? ''}|${row.city ?? ''}`;
  const selectedCity = f.city
    ? data?.by_city
        .filter((c) => c.city?.toLowerCase() === f.city.toLowerCase() && (!f.state || c.state === f.state))
        .map(cityKey)[0]
    : undefined;
  const deployed = data?.deployment.reduce((sum, row) => sum + row.people, 0) ?? 0;
  const placeBar = (row: PlaceSpend, id: string | null) => ({
    label: row.label,
    value: Number(row.spent),
    detail: spendDetail(row) + (row.city && row.state ? ` · ${row.state}` : ''),
    id: id ?? undefined,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Cost analytics</h1>
        <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
          What travel has cost. Filter by dates, campaign, person or destination, and every number
          below follows. Only booked trips count as spend; approved but not yet ticketed is shown as
          committed.
        </p>
      </div>

      <ReportFilterBar
        filters={f}
        options={options.data}
        optionsLoading={options.isPending}
        presets={DEFAULT_PRESETS}
        idPrefix="cost"
        fetching={analytics.isFetching && !analytics.isPending}
        footerAction={
          <Link
            to={f.link('/travel-logs', { status: 'BOOKED' })}
            className="inline-flex items-center gap-1 font-medium text-brand-strong hover:underline"
          >
            See these bookings in travel logs <ArrowRight size={14} />
          </Link>
        }
      />

      {analytics.isError && !data ? (
        <Card>
          <EmptyState
            icon={<BarChart3 size={28} />}
            title="Could not load the reports"
            description={errorMessage(analytics.error)}
          />
        </Card>
      ) : (
        <div className={cn('space-y-6 transition-opacity', analytics.isPlaceholderData && 'opacity-60')}>
          {/* A KPI row of stat tiles, not a grouped bar chart of four numbers. */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Spent"
              value={overview ? formatMoney(overview.spent) : '—'}
              hint={overview ? plural(overview.booked_travellers, 'booked traveller') : undefined}
              icon={<IndianRupee size={15} />}
            />
            <StatTile
              label="Committed"
              value={overview ? formatMoney(overview.committed) : '—'}
              hint="Approved, not yet ticketed"
              icon={<CalendarRange size={15} />}
            />
            <StatTile
              label="Average per traveller"
              value={overview ? formatMoney(overview.average_per_traveller) : '—'}
              hint="Costed bookings only"
              icon={<Plane size={15} />}
            />
            <StatTile
              label="Missing a cost"
              value={overview ? String(overview.uncosted) : '—'}
              hint={
                !overview
                  ? undefined
                  : overview.uncosted === 0
                    ? 'Every booking is costed'
                    : 'These figures understate the real spend'
              }
              tone={overview && overview.uncosted > 0 ? 'warning' : 'default'}
              icon={<AlertTriangle size={15} />}
            />
          </div>

          {data && data.overview.uncosted > 0 && (
            <Card className="border-warning/40 bg-warning-soft">
              <div className="flex items-start gap-2.5 px-5 py-3.5">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" />
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-warning">
                    {data.overview.uncosted} booked{' '}
                    {data.overview.uncosted === 1 ? 'traveller has' : 'travellers have'} no cost recorded
                  </p>
                  <p className="mt-0.5 text-xs text-text-muted">
                    Every figure on this page is lower than the truth until these are filled in.
                  </p>
                  <ul className="mt-2 space-y-0.5">
                    {data.uncosted.slice(0, 5).map((row) => (
                      <li key={row.traveller_id} className="text-2xs text-text-muted">
                        <span className="font-medium text-text">{row.traveller_name}</span> ·{' '}
                        {row.project_code} · {REQUEST_TYPE_LABELS[row.request_type]}
                        {row.booking_reference && ` · ${row.booking_reference}`}
                        {row.trip_date && ` · ${row.trip_date}`}
                      </li>
                    ))}
                    {data.uncosted.length > 5 && (
                      <li className="text-2xs text-text-subtle">and {data.uncosted.length - 5} more</li>
                    )}
                  </ul>
                </div>
              </div>
            </Card>
          )}

          <Section
            title="Spend over time"
            description={
              data
                ? `By ${data.grain}, on the day each trip starts (hotels: check-in day). Not when the ticket was bought.`
                : undefined
            }
            loading={loading}
          >
            {data && (
              <Columns
                data={data.trend.map((row) => ({
                  label: periodLabel(row.period, data.grain),
                  value: Number(row.spent),
                }))}
                format={(v) => formatMoney(v)}
                caption="Booked spend over time"
              />
            )}
          </Section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section
              title="Campaign financials"
              description="Biggest spend first. Archived campaigns keep their history. Click one to filter."
              loading={loading}
            >
              {data && (
                <HorizontalBars
                  data={data.by_campaign.slice(0, 8).map((row) => ({
                    label: `${row.code} · ${row.name}`,
                    value: Number(row.spent),
                    detail: spendDetail(row),
                    id: String(row.project_id),
                  }))}
                  onSelect={(row) => f.update({ campaign: f.projectId === Number(row.id) ? undefined : row.id })}
                  selected={f.projectId ? String(f.projectId) : undefined}
                  format={(v) => formatMoney(v)}
                  empty="No campaign spend for these filters."
                />
              )}
            </Section>

            <Section title="Spend by employee" description="Top 10 by booked spend. Click one to filter." loading={loading}>
              {data && (
                <HorizontalBars
                  data={data.by_person.slice(0, 10).map((row) => ({
                    label: row.employee_code ? `${row.full_name} (${row.employee_code})` : row.full_name,
                    value: Number(row.spent),
                    detail:
                      plural(row.trips, 'booked trip') +
                      (Number(row.committed) > 0 ? ` · ${formatMoney(row.committed)} committed` : '') +
                      (row.uncosted > 0 ? ` · ${row.uncosted} uncosted` : ''),
                    id: String(row.user_id),
                  }))}
                  onSelect={(row) => f.update({ user: f.userId === Number(row.id) ? undefined : row.id })}
                  selected={f.userId ? String(f.userId) : undefined}
                  format={(v) => formatMoney(v)}
                  empty="Nobody has booked or approved travel for these filters."
                />
              )}
            </Section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section
              title="Spend by state"
              description="Where the trip was headed (hotels: the hotel's state). Click one to filter."
              loading={loading}
            >
              {data && (
                <HorizontalBars
                  data={data.by_state.map((row) => placeBar(row, row.state))}
                  onSelect={(row) =>
                    f.update(stateChange(options.data, f.city, f.state === row.id ? '' : (row.id ?? '')))
                  }
                  selected={f.state || undefined}
                  format={(v) => formatMoney(v)}
                  empty="No booked spend for these filters."
                />
              )}
            </Section>

            <Section
              title="Spend by city"
              description="Top 10. A cab counts by its drop city, a hotel by its city. Click one to filter."
              loading={loading}
            >
              {data && (
                <HorizontalBars
                  data={data.by_city.slice(0, 10).map((row) => placeBar(row, row.city && cityKey(row)))}
                  onSelect={(row) => {
                    const picked = data.by_city.find((c) => c.city && cityKey(c) === row.id);
                    if (!picked?.city) return;
                    f.update(
                      row.id === selectedCity
                        ? { city: undefined }
                        : { city: picked.city, state: picked.state ?? undefined },
                    );
                  }}
                  selected={selectedCity}
                  format={(v) => formatMoney(v)}
                  empty="No booked spend for these filters."
                />
              )}
            </Section>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Where the money goes" description="By request type. Click one to filter." loading={loading}>
              {data && (
                <HorizontalBars
                  data={data.by_type.map((row) => ({
                    label: REQUEST_TYPE_LABELS[row.request_type as RequestType] ?? row.request_type,
                    value: Number(row.spent),
                    detail: plural(row.travellers, 'traveller'),
                    id: row.request_type,
                  }))}
                  onSelect={(row) => f.update({ type: f.requestType === row.id ? undefined : row.id })}
                  selected={f.requestType}
                  format={(v) => formatMoney(v)}
                />
              )}
            </Section>

            <Section
              title="Deployed staff by location"
              description="Upcoming trips (from today) within these filters, by the state they are headed to."
              loading={loading}
              action={
                data && (
                  <Badge tone="neutral">
                    <Users size={11} />
                    {deployed} {deployed === 1 ? 'person' : 'people'}
                  </Badge>
                )
              }
            >
              {data && (
                <>
                  <HorizontalBars
                    data={data.deployment.map((row) => ({
                      label: row.location,
                      value: row.people,
                      detail: plural(row.trips, 'upcoming trip'),
                    }))}
                    format={(v) => `${v}`}
                    empty="No upcoming travel in this range. Try Next 30 days or This year."
                  />
                  {data.deployment.length > 0 && (
                    <p className="mt-3 flex items-center gap-1.5 text-2xs text-text-subtle">
                      <MapPin size={11} />
                      Upcoming trips only. Past travel is in the chart above.
                    </p>
                  )}
                </>
              )}
            </Section>
          </div>
        </div>
      )}
    </div>
  );
}
