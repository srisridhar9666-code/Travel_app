import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  BedDouble,
  Car,
  Download,
  History,
  MapPin,
  Moon,
  Plane,
  Search,
  Train,
  Users,
  X,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { Combobox } from '@/components/Combobox';
import {
  DateRangePicker,
  PRESET_LABELS,
  describeRange,
  rangeFor,
  type DateRange,
  type RangePreset,
} from '@/components/DateRangePicker';
import { StatTile, formatMoney } from '@/components/charts';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Skeleton,
} from '@/components/ui';
import { errorMessage, fetchFilterOptions, fetchTravelLogs } from '@/lib/api';
import {
  REQUEST_TYPE_LABELS,
  TRAVELLER_STATUS_LABELS,
  TRAVEL_MODE_LABELS,
  type RequestType,
  type TravelLogEntry,
  type TravellerStatus,
} from '@/types';

/** Which traveller statuses each choice in the Status menu stands for. */
const STATUS_CHOICES: Record<string, { label: string; statuses: TravellerStatus[] | undefined }> = {
  travelled: { label: 'Travelled or going', statuses: undefined },
  BOOKED: { label: 'Booked', statuses: ['BOOKED'] },
  APPROVED: { label: 'Approved, not booked', statuses: ['APPROVED'] },
  PENDING: { label: 'Awaiting a decision', statuses: ['PENDING'] },
  REJECTED: { label: 'Rejected', statuses: ['REJECTED'] },
  CANCELLED: { label: 'Cancelled', statuses: ['CANCELLED'] },
  all: {
    label: 'Every status',
    statuses: ['PENDING', 'APPROVED', 'BOOKED', 'REJECTED', 'CANCELLED'],
  },
};

const STATUS_TONE: Record<TravellerStatus, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  BOOKED: 'success',
  APPROVED: 'info',
  PENDING: 'warning',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
};

function TypeIcon({ entry }: { entry: TravelLogEntry }) {
  const size = 15;
  if (entry.request_type === 'HOTEL') return <BedDouble size={size} />;
  if (entry.request_type === 'LOCAL_CAB') return <Car size={size} />;
  if (entry.mode === 'TRAIN') return <Train size={size} />;
  if (entry.mode === 'BUS') return <Car size={size} />;
  return <Plane size={size} />;
}

function when(entry: TravelLogEntry) {
  if (!entry.started_on) return 'Date not set';
  const start = new Date(`${entry.started_on}T00:00:00`);
  if (entry.request_type === 'HOTEL' && entry.check_out) {
    return `${format(start, 'd MMM')} – ${format(new Date(`${entry.check_out}T00:00:00`), 'd MMM yyyy')}`;
  }
  const time = entry.start_at ? format(new Date(entry.start_at), ', HH:mm') : '';
  return `${format(start, 'EEE d MMM yyyy')}${time}`;
}

function kind(entry: TravelLogEntry) {
  if (entry.request_type === 'LONG_DISTANCE' && entry.mode) return TRAVEL_MODE_LABELS[entry.mode];
  return REQUEST_TYPE_LABELS[entry.request_type];
}

function place(entry: TravelLogEntry) {
  if (entry.request_type === 'HOTEL') {
    return [entry.hotel_city, entry.hotel_state].filter(Boolean).join(', ') || 'Hotel';
  }
  return `${entry.origin ?? '?'} → ${entry.destination ?? '?'}`;
}

/** A spreadsheet of exactly what is on screen, for whoever asked. */
function exportCsv(entries: TravelLogEntry[], label: string) {
  const header = [
    'Date', 'Employee', 'Employee code', 'Type', 'From', 'From state', 'To', 'To state',
    'Hotel', 'Hotel state', 'Nights', 'Campaign', 'Status', 'PNR / booking ref',
    'Travelled with', 'Cost (INR)', 'Reason',
  ];
  const rows = entries.map((e) => [
    e.started_on ?? '', e.full_name, e.employee_code ?? '', kind(e), e.origin ?? '',
    e.origin_state ?? '', e.destination ?? '', e.destination_state ?? '', e.hotel_city ?? '',
    e.hotel_state ?? '', e.nights ?? '', e.project_name ?? e.project_code ?? '',
    TRAVELLER_STATUS_LABELS[e.status], e.booking_reference ?? '', e.companions.join('; '),
    e.cost_amount ?? '', e.travel_reason ?? '',
  ]);
  const escape = (value: unknown) => {
    const text = String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const csv = [header, ...rows].map((row) => row.map(escape).join(',')).join('\n');
  const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `travel-log-${label.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function TravelLogsPage() {
  const [params, setParams] = useSearchParams();

  // Filters live in the URL, so a filtered log can be bookmarked or pasted to
  // a colleague and opens exactly as it was.
  const preset = (params.get('range') as RangePreset) || 'last_month';
  const range: DateRange =
    preset === 'custom'
      ? rangeFor('custom', { since: params.get('since') ?? '', until: params.get('until') ?? '' })
      : rangeFor(PRESET_LABELS[preset] ? preset : 'last_month');
  const userId = params.get('user') ? Number(params.get('user')) : undefined;
  const projectId = params.get('campaign') ? Number(params.get('campaign')) : undefined;
  const requestType = (params.get('type') as RequestType) || undefined;
  const state = params.get('state') || '';
  const statusKey = params.get('status') || 'travelled';
  const search = params.get('q') || '';
  const [searchDraft, setSearchDraft] = useState(search);

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };

  const setRange = (next: DateRange) =>
    update({
      range: next.preset,
      since: next.preset === 'custom' ? next.since : undefined,
      until: next.preset === 'custom' ? next.until : undefined,
    });

  const options = useQuery({ queryKey: ['filter-options'], queryFn: fetchFilterOptions });

  const people = useMemo(() => {
    const byLabel = new Map<string, number>();
    for (const person of options.data?.people ?? []) {
      const label = person.employee_code
        ? `${person.full_name} (${person.employee_code})`
        : person.full_name;
      byLabel.set(label, person.id);
    }
    return byLabel;
  }, [options.data]);
  const personLabel = [...people.entries()].find(([, id]) => id === userId)?.[0] ?? '';

  const log = useQuery({
    queryKey: ['travel-logs', range.since, range.until, userId, projectId, requestType, state, statusKey, search],
    queryFn: () =>
      fetchTravelLogs({
        since: range.since || undefined,
        until: range.until || undefined,
        user_id: userId,
        project_id: projectId,
        request_type: requestType,
        state: state || undefined,
        status: STATUS_CHOICES[statusKey]?.statuses,
        search: search || undefined,
        limit: 5000,
      }),
    placeholderData: keepPreviousData,
  });

  const filtersActive = Boolean(userId || projectId || requestType || state || search || statusKey !== 'travelled');
  const rangeLabel = describeRange(range);
  const heading = personLabel ? `${personLabel.replace(/ \(.*\)$/, '')} · ${rangeLabel}` : rangeLabel;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Travel logs"
        description="Where every employee went and when. Pick a person and a month, or search the whole team."
        actions={
          <Button
            variant="secondary"
            disabled={!log.data || log.data.entries.length === 0}
            onClick={() => log.data && exportCsv(log.data.entries, heading)}
          >
            <Download size={16} />
            Export CSV
          </Button>
        }
      />

      <Card className="p-4 sm:p-5">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Employee" htmlFor="log-person">
            <Combobox
              id="log-person"
              value={personLabel}
              options={[...people.keys()]}
              loading={options.isPending}
              placeholder="All employees"
              emptyText="Nobody by that name."
              onChange={(label) => update({ user: String(people.get(label) ?? '') })}
              action={
                userId
                  ? { label: 'All employees', icon: <Users size={16} className="mt-0.5 shrink-0 text-text-subtle" />, onSelect: () => update({ user: undefined }) }
                  : undefined
              }
            />
          </Field>
          <Field label="When" htmlFor="log-range" className="xl:col-span-1">
            <DateRangePicker id="log-range" value={range} onChange={setRange} />
          </Field>
          <Field label="Campaign" htmlFor="log-campaign">
            <Select
              id="log-campaign"
              value={projectId ? String(projectId) : ''}
              onChange={(e) => update({ campaign: e.target.value })}
            >
              <option value="">All campaigns</option>
              {(options.data?.projects ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Search" htmlFor="log-search">
            <form
              className="relative"
              onSubmit={(e) => {
                e.preventDefault();
                update({ q: searchDraft.trim() });
              }}
            >
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle" />
              <Input
                id="log-search"
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.target.value)}
                onBlur={() => searchDraft.trim() !== search && update({ q: searchDraft.trim() })}
                placeholder="Place, PNR, reason…"
                className="pl-9"
              />
            </form>
          </Field>
          <Field label="Type" htmlFor="log-type">
            <Select
              id="log-type"
              value={requestType ?? ''}
              onChange={(e) => update({ type: e.target.value })}
            >
              <option value="">All types</option>
              {(Object.keys(REQUEST_TYPE_LABELS) as RequestType[]).map((t) => (
                <option key={t} value={t}>
                  {REQUEST_TYPE_LABELS[t]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="State" htmlFor="log-state">
            <Select id="log-state" value={state} onChange={(e) => update({ state: e.target.value })}>
              <option value="">All states</option>
              {(options.data?.states ?? []).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Status" htmlFor="log-status">
            <Select
              id="log-status"
              value={statusKey}
              onChange={(e) => update({ status: e.target.value === 'travelled' ? undefined : e.target.value })}
            >
              {Object.entries(STATUS_CHOICES).map(([key, choice]) => (
                <option key={key} value={key}>
                  {choice.label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="flex items-end">
            {filtersActive && (
              <Button
                variant="ghost"
                onClick={() => {
                  setSearchDraft('');
                  setParams(new URLSearchParams({ range: preset }), { replace: true });
                }}
              >
                <X size={16} />
                Clear filters
              </Button>
            )}
          </div>
        </div>
      </Card>

      <div>
        <h2 className="mb-3 text-base font-semibold tracking-tight">{heading}</h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <StatTile label="Movements" value={log.data ? String(log.data.summary.movements) : '—'} icon={<History size={15} />} />
          <StatTile label="People" value={log.data ? String(log.data.summary.people) : '—'} icon={<Users size={15} />} />
          <StatTile label="Places" value={log.data ? String(log.data.summary.places) : '—'} icon={<MapPin size={15} />} />
          <StatTile label="Hotel nights" value={log.data ? String(log.data.summary.nights) : '—'} icon={<Moon size={15} />} />
          <div className="col-span-2 lg:col-span-1">
            <StatTile
              label="Booked spend"
              value={log.data?.summary.spent ? formatMoney(log.data.summary.spent) : '—'}
              icon={<span className="text-xs font-semibold">₹</span>}
            />
          </div>
        </div>
      </div>

      <Card className="overflow-hidden">
        {log.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : log.isError ? (
          <EmptyState icon={<History size={28} />} title="Could not load the log" description={errorMessage(log.error)} />
        ) : log.data.entries.length === 0 ? (
          <EmptyState
            icon={<MapPin size={28} />}
            title="No travel in this period"
            description="Widen the dates or clear a filter."
          />
        ) : (
          <>
            {/* Phones get cards; a nine-column table does not fit a hand. */}
            <ul className="divide-y divide-border md:hidden">
              {log.data.entries.map((entry) => (
                <li key={entry.traveller_id} className="space-y-1.5 px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <button
                      type="button"
                      className="text-left text-sm font-semibold hover:underline"
                      onClick={() => update({ user: String(entry.user_id) })}
                    >
                      {entry.full_name}
                    </button>
                    <Badge tone={STATUS_TONE[entry.status]}>{TRAVELLER_STATUS_LABELS[entry.status]}</Badge>
                  </div>
                  <p className="flex items-center gap-2 text-sm">
                    <span className="text-text-subtle"><TypeIcon entry={entry} /></span>
                    {place(entry)}
                  </p>
                  <p className="text-xs text-text-muted">
                    {when(entry)} · {kind(entry)}
                    {entry.nights != null && ` · ${entry.nights} night${entry.nights === 1 ? '' : 's'}`}
                  </p>
                  <p className="text-xs text-text-subtle">
                    {entry.project_name ?? entry.project_code}
                    {entry.booking_reference && ` · PNR ${entry.booking_reference}`}
                    {entry.cost_amount && ` · ${formatMoney(entry.cost_amount)}`}
                  </p>
                  {entry.companions.length > 0 && (
                    <p className="text-xs text-text-subtle">With {entry.companions.join(', ')}</p>
                  )}
                </li>
              ))}
            </ul>

            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface-sunken text-left text-xs font-medium text-text-muted">
                    <th className="px-4 py-3 font-medium">Date</th>
                    <th className="px-4 py-3 font-medium">Employee</th>
                    <th className="px-4 py-3 font-medium">Where</th>
                    <th className="px-4 py-3 font-medium">Type</th>
                    <th className="px-4 py-3 font-medium">Campaign</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {log.data.entries.map((entry) => (
                    <tr key={entry.traveller_id} className="align-top hover:bg-surface-sunken/60">
                      <td className="whitespace-nowrap px-4 py-3 tabular-nums text-text-muted">{when(entry)}</td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          className="text-left font-medium hover:underline"
                          title="Show only this person"
                          onClick={() => update({ user: String(entry.user_id) })}
                        >
                          {entry.full_name}
                        </button>
                        {entry.employee_code && (
                          <span className="block font-mono text-xs text-text-subtle">{entry.employee_code}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="block">{place(entry)}</span>
                        {entry.companions.length > 0 && (
                          <span className="block text-xs text-text-subtle">With {entry.companions.join(', ')}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-text-muted">
                        <span className="inline-flex items-center gap-1.5">
                          <TypeIcon entry={entry} />
                          {kind(entry)}
                        </span>
                        {entry.nights != null && (
                          <span className="block text-xs text-text-subtle">
                            {entry.nights} night{entry.nights === 1 ? '' : 's'}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-text-muted">
                        <span className="font-mono text-xs">{entry.project_code}</span>
                        {entry.project_name && <span className="block text-xs text-text-subtle">{entry.project_name}</span>}
                      </td>
                      <td className="px-4 py-3">
                        <Badge tone={STATUS_TONE[entry.status]}>{TRAVELLER_STATUS_LABELS[entry.status]}</Badge>
                        {entry.booking_reference && (
                          <span className="mt-1 block font-mono text-xs text-text-subtle">PNR {entry.booking_reference}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                        {entry.cost_amount ? formatMoney(entry.cost_amount) : <span className="text-text-subtle">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {log.data.truncated && (
              <p className="border-t border-border px-4 py-3 text-xs text-text-subtle">
                Showing the newest {log.data.entries.length} of {log.data.total}. Narrow the dates to see the rest.
              </p>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
