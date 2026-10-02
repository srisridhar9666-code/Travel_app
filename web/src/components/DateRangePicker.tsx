import {
  endOfMonth,
  endOfYear,
  format,
  startOfMonth,
  startOfYear,
  subDays,
  subMonths,
} from 'date-fns';

import { Input, Select } from '@/components/ui';
import { todayInIndia } from '@/lib/time';
import { cn } from '@/lib/utils';

export type RangePreset =
  | 'this_month'
  | 'last_month'
  | 'last_7'
  | 'last_30'
  | 'last_90'
  | 'next_30'
  | 'this_year'
  | 'all'
  | 'custom';

export interface DateRange {
  preset: RangePreset;
  /** yyyy-MM-dd, India dates. Empty means unbounded on that side. */
  since: string;
  until: string;
}

export const PRESET_LABELS: Record<RangePreset, string> = {
  this_month: 'This month',
  last_month: 'Last month',
  last_7: 'Last 7 days',
  last_30: 'Last 30 days',
  last_90: 'Last 90 days',
  next_30: 'Next 30 days',
  this_year: 'This year',
  all: 'All time',
  custom: 'Custom dates',
};

const day = (d: Date) => format(d, 'yyyy-MM-dd');

/** The concrete dates a preset stands for, as of today. */
export function rangeFor(preset: RangePreset, custom?: { since: string; until: string }): DateRange {
  // Today in India, not on this device's clock: "last month" must mean the
  // same days for everyone, and match the server's idea of today.
  const today = new Date(`${todayInIndia()}T00:00:00`);
  switch (preset) {
    case 'this_month':
      return { preset, since: day(startOfMonth(today)), until: day(endOfMonth(today)) };
    case 'last_month': {
      const last = subMonths(today, 1);
      return { preset, since: day(startOfMonth(last)), until: day(endOfMonth(last)) };
    }
    case 'last_7':
      return { preset, since: day(subDays(today, 6)), until: day(today) };
    case 'last_30':
      return { preset, since: day(subDays(today, 29)), until: day(today) };
    case 'last_90':
      return { preset, since: day(subDays(today, 89)), until: day(today) };
    case 'next_30':
      return { preset, since: day(today), until: day(subDays(today, -29)) };
    case 'this_year':
      return { preset, since: day(startOfYear(today)), until: day(endOfYear(today)) };
    case 'all':
      return { preset, since: '', until: '' };
    case 'custom':
      return { preset, since: custom?.since ?? day(subDays(today, 29)), until: custom?.until ?? day(today) };
  }
}

/** "1 Sep – 30 Sep 2026", for headings over a filtered view. */
export function describeRange(range: DateRange): string {
  if (!range.since && !range.until) return 'All time';
  const fmt = (iso: string, withYear: boolean) =>
    format(new Date(`${iso}T00:00:00`), withYear ? 'd MMM yyyy' : 'd MMM');
  if (range.since && range.until) {
    const sameYear = range.since.slice(0, 4) === range.until.slice(0, 4);
    return `${fmt(range.since, !sameYear)} – ${fmt(range.until, true)}`;
  }
  return range.since ? `From ${fmt(range.since, true)}` : `Until ${fmt(range.until, true)}`;
}

interface DateRangePickerProps {
  value: DateRange;
  onChange: (next: DateRange) => void;
  presets?: RangePreset[];
  className?: string;
  id?: string;
}

/** A preset menu, with two date fields when "Custom dates" is chosen. */
export function DateRangePicker({
  value,
  onChange,
  presets = ['this_month', 'last_month', 'last_7', 'last_30', 'last_90', 'this_year', 'all', 'custom'],
  className,
  id = 'range',
}: DateRangePickerProps) {
  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      <Select
        id={id}
        aria-label="Date range"
        value={value.preset}
        onChange={(e) => onChange(rangeFor(e.target.value as RangePreset, value))}
        className="w-full sm:w-44"
      >
        {presets.map((preset) => (
          <option key={preset} value={preset}>
            {PRESET_LABELS[preset]}
          </option>
        ))}
      </Select>
      {value.preset === 'custom' && (
        <div className="flex w-full items-center gap-2 sm:w-auto">
          <Input
            type="date"
            aria-label="From"
            value={value.since}
            max={value.until || undefined}
            onChange={(e) => onChange({ ...value, since: e.target.value })}
            className="min-w-0 flex-1 sm:w-40"
          />
          <span className="text-xs text-text-subtle">to</span>
          <Input
            type="date"
            aria-label="To"
            value={value.until}
            min={value.since || undefined}
            onChange={(e) => onChange({ ...value, until: e.target.value })}
            className="min-w-0 flex-1 sm:w-40"
          />
        </div>
      )}
    </div>
  );
}
