import { Users, X } from 'lucide-react';
import type { ReactNode } from 'react';
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
import { Button, Card, Field, Select } from '@/components/ui';
import {
  REQUEST_TYPE_LABELS,
  USER_STATUS_LABELS,
  type FilterOptions,
  type InsightFilters,
  type RequestType,
} from '@/types';

/**
 * The filters every report page shares: dates, campaign, employee,
 * destination state and city, and type.
 *
 * They live in the URL with the same keys the travel log reads (range, since,
 * until, campaign, user, state, city, type), so a filtered view can be
 * bookmarked or sent to a colleague, and a link from one report to another
 * opens on the same slice.
 */

type Changes = Record<string, string | undefined>;

export interface ReportFilterState {
  range: DateRange;
  projectId?: number;
  userId?: number;
  state: string;
  city: string;
  requestType?: RequestType;
  /** What the API takes. No dates means all time; never a made-up start. */
  apiFilters: InsightFilters;
  /** Any filter other than the dates is set. */
  sliced: boolean;
  update: (changes: Changes) => void;
  setRange: (next: DateRange) => void;
  /** Clears everything but the dates. */
  clear: () => void;
  /** `path` with this slice as its query, plus `extra`. */
  link: (path: string, extra?: Changes) => string;
}

export function useReportFilters(defaultPreset: RangePreset): ReportFilterState {
  const [params, setParams] = useSearchParams();

  const asked = params.get('range') as RangePreset | null;
  const preset = asked && PRESET_LABELS[asked] ? asked : defaultPreset;
  const range =
    preset === 'custom'
      ? rangeFor('custom', { since: params.get('since') ?? '', until: params.get('until') ?? '' })
      : rangeFor(preset);
  const projectId = Number(params.get('campaign')) || undefined;
  const userId = Number(params.get('user')) || undefined;
  const state = params.get('state') ?? '';
  const city = params.get('city') ?? '';
  const type = params.get('type') as RequestType | null;
  const requestType = type && REQUEST_TYPE_LABELS[type] ? type : undefined;

  const update = (changes: Changes) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  };

  const link = (path: string, extra: Changes = {}) => {
    const search = new URLSearchParams();
    const all: Changes = {
      range: range.preset,
      since: range.preset === 'custom' ? range.since : undefined,
      until: range.preset === 'custom' ? range.until : undefined,
      campaign: projectId ? String(projectId) : undefined,
      user: userId ? String(userId) : undefined,
      state: state || undefined,
      city: city || undefined,
      type: requestType,
      ...extra,
    };
    for (const [key, value] of Object.entries(all)) if (value) search.set(key, value);
    return `${path}?${search.toString()}`;
  };

  return {
    range,
    projectId,
    userId,
    state,
    city,
    requestType,
    apiFilters: {
      since: range.since || undefined,
      until: range.until || undefined,
      project_id: projectId,
      user_id: userId,
      state: state || undefined,
      city: city || undefined,
      request_type: requestType,
    },
    sliced: Boolean(projectId || userId || state || city || requestType),
    update,
    setRange: (next) =>
      update({
        range: next.preset,
        since: next.preset === 'custom' ? next.since : undefined,
        until: next.preset === 'custom' ? next.until : undefined,
      }),
    clear: () =>
      update({ campaign: undefined, user: undefined, state: undefined, city: undefined, type: undefined }),
    link,
  };
}

/** "Ravi Kumar (E104)", with "· Left" when they are no longer active, so an
 *  admin can tell a former colleague's history from a current one's. */
export function personLabel(person: FilterOptions['people'][number]): string {
  const name = person.employee_code ? `${person.full_name} (${person.employee_code})` : person.full_name;
  return person.status && person.status !== 'ACTIVE' ? `${name} · ${USER_STATUS_LABELS[person.status]}` : name;
}

/** The employee picker's label-to-id map, and the label for the chosen id. */
export function peopleChoices(options: FilterOptions | undefined, userId: number | undefined) {
  const byLabel = new Map((options?.people ?? []).map((p) => [personLabel(p), p.id]));
  const chosen = (options?.people ?? []).find((p) => p.id === userId);
  return { byLabel, label: chosen ? personLabel(chosen) : '', name: chosen?.full_name ?? '' };
}

/**
 * A typeable picker over the destination cities actually in use.
 *
 * With a state chosen it lists that state's cities; with none it lists them
 * all as "Pune, Maharashtra" (two states can have a place of one name), and
 * picking one sets its state as well.
 */
export function CityField({
  id,
  options,
  state,
  city,
  loading,
  onChange,
}: {
  id: string;
  options: FilterOptions | undefined;
  state: string;
  city: string;
  loading?: boolean;
  onChange: (next: { state?: string; city?: string }) => void;
}) {
  const cities = (options?.cities ?? []).filter((c) => !state || c.state === state);
  const labelOf = (c: { state: string | null; city: string }) =>
    state || !c.state ? c.city : `${c.city}, ${c.state}`;
  const byLabel = new Map(cities.map((c) => [labelOf(c), c]));
  const current = cities.find((c) => c.city.toLowerCase() === city.toLowerCase());

  return (
    <Combobox
      id={id}
      value={current ? labelOf(current) : city}
      options={[...byLabel.keys()]}
      loading={loading}
      clearable
      placeholder="All cities"
      emptyText={state ? `No trips to a city in ${state}.` : 'No trips to a city by that name.'}
      onChange={(label) => {
        const picked = byLabel.get(label);
        if (!picked) onChange({ state: state || undefined, city: undefined });
        else onChange({ state: picked.state ?? (state || undefined), city: picked.city });
      }}
    />
  );
}

/** What a state choice does to the city: a city outside the new state goes. */
export function stateChange(options: FilterOptions | undefined, city: string, next: string) {
  const keeps =
    !city ||
    !next ||
    (options?.cities ?? []).some((c) => c.state === next && c.city.toLowerCase() === city.toLowerCase());
  return { state: next || undefined, city: keeps ? city || undefined : undefined };
}

export function ReportFilterBar({
  filters,
  options,
  optionsLoading,
  presets,
  idPrefix,
  fetching,
  footerAction,
}: {
  filters: ReportFilterState;
  options: FilterOptions | undefined;
  optionsLoading?: boolean;
  presets?: RangePreset[];
  idPrefix: string;
  /** A refresh is in flight behind data already on screen. */
  fetching?: boolean;
  footerAction?: ReactNode;
}) {
  const f = filters;
  const people = peopleChoices(options, f.userId);
  const ids = (name: string) => `${idPrefix}-${name}`;

  return (
    <Card className="p-4 sm:p-5">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Field label="When" htmlFor={ids('range')} className="sm:col-span-2 lg:col-span-1">
          <DateRangePicker id={ids('range')} value={f.range} onChange={f.setRange} presets={presets} />
        </Field>
        <Field label="Campaign" htmlFor={ids('campaign')}>
          <Select
            id={ids('campaign')}
            value={f.projectId ? String(f.projectId) : ''}
            onChange={(e) => f.update({ campaign: e.target.value })}
          >
            <option value="">All campaigns</option>
            {(options?.projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} · {p.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Employee" htmlFor={ids('person')}>
          <Combobox
            id={ids('person')}
            value={people.label}
            options={[...people.byLabel.keys()]}
            loading={optionsLoading}
            placeholder="Everyone"
            emptyText="Nobody by that name."
            onChange={(label) => f.update({ user: String(people.byLabel.get(label) ?? '') })}
            action={
              f.userId
                ? {
                    label: 'Everyone',
                    icon: <Users size={16} className="mt-0.5 shrink-0 text-text-subtle" />,
                    onSelect: () => f.update({ user: undefined }),
                  }
                : undefined
            }
          />
        </Field>
        <Field label="Destination state" htmlFor={ids('state')}>
          <Select
            id={ids('state')}
            value={f.state}
            onChange={(e) => f.update(stateChange(options, f.city, e.target.value))}
          >
            <option value="">All states</option>
            {(options?.states ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Destination city" htmlFor={ids('city')}>
          <CityField
            id={ids('city')}
            options={options}
            state={f.state}
            city={f.city}
            loading={optionsLoading}
            onChange={(next) => f.update(next)}
          />
        </Field>
        <Field label="Type" htmlFor={ids('type')}>
          <Select id={ids('type')} value={f.requestType ?? ''} onChange={(e) => f.update({ type: e.target.value })}>
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
          Showing <span className="font-medium text-text">{describeRange(f.range)}</span>
          {fetching && <span className="ml-2 text-text-subtle">Updating…</span>}
        </span>
        {f.sliced && (
          <Button variant="ghost" size="sm" onClick={f.clear}>
            <X size={14} />
            Clear filters
          </Button>
        )}
        {footerAction && <span className="ml-auto">{footerAction}</span>}
      </div>
    </Card>
  );
}
