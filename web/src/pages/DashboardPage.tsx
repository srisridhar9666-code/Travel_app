import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { AlertTriangle, CheckSquare, IndianRupee, Users } from 'lucide-react';

import { TravelHistoryPanel } from '@/components/TravelHistoryPanel';
import { Card, CardHeader } from '@/components/ui';
import { fetchAnalytics, fetchQueueCounts } from '@/lib/api';
import { Columns, StatTile, formatMoney } from '@/components/charts';
import { useAuth } from '@/store/auth';

export default function DashboardPage() {
  const user = useAuth((s) => s.user);

  // The operational picture, for the people who act on it. Ground staff get
  // their own travel below instead - nothing in the admin half is theirs to do.
  const isAdmin = user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN';

  const queue = useQuery({
    queryKey: ['queue-counts'],
    queryFn: fetchQueueCounts,
    enabled: isAdmin,
  });
  const analytics = useQuery({
    queryKey: ['analytics', 90, 6],
    queryFn: () => fetchAnalytics({ days: 90, months: 6 }),
    enabled: isAdmin,
  });

  const firstName = user?.full_name.split(/\s+/)[0] ?? 'there';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Welcome back, {firstName}</h1>
        <p className="mt-1.5 text-sm text-text-muted">
          {isAdmin
            ? 'What is waiting on you, and what travel has cost.'
            : 'Raise a request from My requests — decisions and tickets arrive in your notifications.'}
        </p>
      </div>

      {isAdmin && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Awaiting a decision"
              value={queue.data ? String(queue.data.awaiting) : '—'}
              hint={
                queue.data && queue.data.with_conflicts > 0
                  ? `${queue.data.with_conflicts} with a calendar clash`
                  : 'Nothing clashing'
              }
              tone={queue.data && queue.data.awaiting > 0 ? 'warning' : 'default'}
              icon={<CheckSquare size={11} />}
            />
            <StatTile
              label="Spent (90 days)"
              value={analytics.data ? formatMoney(analytics.data.overview.spent) : '—'}
              hint={
                analytics.data
                  ? `${analytics.data.overview.booked_travellers} booked travellers`
                  : undefined
              }
              icon={<IndianRupee size={11} />}
            />
            <StatTile
              label="People travelling"
              value={analytics.data ? String(analytics.data.overview.people_travelling) : '—'}
              hint={analytics.data ? `across ${analytics.data.overview.trips} trips` : undefined}
              icon={<Users size={11} />}
            />
            <StatTile
              label="Missing a cost"
              value={analytics.data ? String(analytics.data.overview.uncosted) : '—'}
              hint={
                analytics.data && analytics.data.overview.uncosted > 0
                  ? 'Spend figures understate the truth'
                  : 'Every booking is costed'
              }
              tone={analytics.data && analytics.data.overview.uncosted > 0 ? 'warning' : 'default'}
              icon={<AlertTriangle size={11} />}
            />
          </div>

          {analytics.data && (
            <Card>
              <CardHeader
                title="Spend by month of travel"
                description="Half history, half already ticketed."
                action={
                  <Link to="/analytics" className="text-2xs text-brand-strong hover:underline">
                    Full reports
                  </Link>
                }
              />
              <div className="px-5 py-4">
                <Columns
                  data={analytics.data.by_month.map((row) => ({
                    label: row.month.slice(5) + '/' + row.month.slice(2, 4),
                    value: Number(row.spent),
                  }))}
                  format={(v) => formatMoney(v)}
                  caption="Spend by month of travel"
                />
              </div>
            </Card>
          )}
        </>
      )}

      {/* Ground staff see their own movements. Before this they got only the
          engineering panels, which told them nothing about their own job. */}
      {!isAdmin && user && (
        <Card>
          <CardHeader
            title="Your recent travel"
            description="Every trip you were on, including ones a colleague raised."
            action={
              <Link to="/requests" className="text-2xs text-brand-strong hover:underline">
                My requests
              </Link>
            }
          />
          <div className="px-5 py-4">
            <TravelHistoryPanel userId={user.id} compact />
          </div>
        </Card>
      )}
    </div>
  );
}
