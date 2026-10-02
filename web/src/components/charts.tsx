import { useEffect, useRef, useState, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Charts for the cost reports (SOW section 6).
 *
 * Every chart in this product compares magnitude - spend by campaign, by month,
 * by type - so every one of them is a **single sequential hue**. None needs a
 * categorical palette, which means none can suffer the failure that palette
 * would bring: two series a colourblind reader cannot tell apart. `--chart-1` is
 * one hue stepped separately for each surface, and it is not a colour already
 * spoken for elsewhere in the system (brand red is identity, danger red is
 * destructive, ink is the interactive primary).
 *
 * The rules these follow, which are easy to lose in a refactor:
 *
 * - marks are thin, with a rounded data-end and a square baseline;
 * - touching marks are separated by a gap in the surface colour, never a stroke;
 * - gridlines are hairline and recessive, and there is never a second y-axis;
 * - text wears text tokens - a bar carries the colour, its label does not;
 * - values are labelled selectively, and whatever is not labelled is reachable
 *   on hover and in the table view underneath.
 */

/** Indian digit grouping: 12,34,567 rather than 1,234,567. */
const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

const inrExact = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
});

export function formatMoney(value: string | number, exact = false): string {
  const amount = typeof value === 'string' ? Number(value) : value;
  if (!Number.isFinite(amount)) return '—';
  return (exact ? inrExact : inr).format(amount);
}

/* -------------------------------------------------------------------------
   Stat tiles — a single headline number is not a one-bar bar chart
   ------------------------------------------------------------------------- */

export function StatTile({
  label,
  value,
  hint,
  tone = 'default',
  icon,
  compact = false,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'default' | 'warning';
  icon?: ReactNode;
  /** A shorter tile, for a page that keeps its table in view below the totals. */
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        'rounded-xl border border-border bg-surface shadow-sm',
        compact ? 'px-4 py-3' : 'px-4 py-4 sm:px-5',
      )}
    >
      <div className="flex items-center gap-2 text-xs font-medium text-text-muted">
        {icon && (
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-surface-sunken text-text-subtle">
            {icon}
          </span>
        )}
        {/* Wraps rather than truncating: two tiles sit side by side on a
            phone, and "Awaiting a de…" says nothing. */}
        <span className="min-w-0 leading-snug">{label}</span>
      </div>
      {/* Proportional figures, not tabular-nums: equal-width digits make a
          standalone number look loose at display sizes. Tabular is for columns
          that align vertically - table rows and axis ticks. */}
      <p
        className={cn(
          compact
            ? 'mt-1.5 text-xl font-semibold leading-7 tracking-tight'
            : 'mt-2 text-2xl font-semibold tracking-tight sm:text-[1.75rem] sm:leading-9',
          tone === 'warning' && 'text-warning',
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-text-muted">{hint}</p>}
    </div>
  );
}

/* -------------------------------------------------------------------------
   Horizontal bars — magnitude with long category names
   ------------------------------------------------------------------------- */

export interface BarDatum {
  label: string;
  value: number;
  /** Shown in the tooltip and under the label. */
  detail?: string;
  /** What a click selects. A row without one is not clickable: a "not
   *  recorded" bucket is nothing to filter on. Also the row's key, since two
   *  cities in different states can share a label. */
  id?: string;
}

export function HorizontalBars({
  data,
  format = (v) => String(v),
  empty = 'Nothing to show yet.',
  max: fixedMax,
  onSelect,
  selected,
}: {
  data: BarDatum[];
  format?: (value: number) => string;
  empty?: string;
  max?: number;
  /** Makes rows that have an `id` into buttons that filter the page. */
  onSelect?: (row: BarDatum) => void;
  /** The `id` the page is filtered by, drawn as pressed. */
  selected?: string;
}) {
  if (data.length === 0) {
    return <p className="px-5 py-8 text-center text-xs text-text-subtle">{empty}</p>;
  }

  // Scale to the largest bar rather than to the sum: the reader is comparing
  // rows against each other, not against a whole.
  const max = fixedMax ?? Math.max(...data.map((d) => d.value), 1);

  return (
    <ul className={onSelect ? 'space-y-1' : 'space-y-2.5'}>
      {data.map((row) => {
        const share = max > 0 ? Math.max((row.value / max) * 100, row.value > 0 ? 1.5 : 0) : 0;
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-xs font-medium">{row.label}</span>
              {/* Value at the tip, per row. Every value being visible is also
                  what makes this list its own table view. */}
              <span className="shrink-0 text-xs tabular-nums text-text-muted">
                {format(row.value)}
              </span>
            </div>
            {row.detail && <p className="text-2xs text-text-subtle">{row.detail}</p>}
            <div
              className="mt-1 h-2 w-full overflow-hidden rounded-sm bg-surface-sunken"
              role="img"
              aria-label={`${row.label}: ${format(row.value)}`}
            >
              <div
                // Rounded at the data end, square at the baseline.
                className="h-full rounded-r-[4px] bg-chart-1 transition-[width] duration-500"
                style={{ width: `${share}%` }}
              />
            </div>
          </>
        );
        return (
          <li key={row.id ?? row.label} className="group">
            {onSelect && row.id !== undefined ? (
              <button
                type="button"
                onClick={() => onSelect(row)}
                aria-pressed={selected === row.id}
                className={cn(
                  'w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-sunken',
                  selected === row.id && 'bg-surface-sunken',
                )}
              >
                {body}
              </button>
            ) : (
              // Same inset as a clickable row, so the bars line up.
              <div className={cn(onSelect && 'px-2 py-1.5')}>{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* -------------------------------------------------------------------------
   Columns over time — trend, with a crosshair tooltip
   ------------------------------------------------------------------------- */

export interface ColumnDatum {
  label: string;
  value: number;
}

/** Round a maximum up to something an axis tick can say out loud. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * magnitude) return step * magnitude;
  }
  return 10 * magnitude;
}

export function Columns({
  data,
  format = (v) => String(v),
  caption,
}: {
  data: ColumnDatum[];
  format?: (value: number) => string;
  caption?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  // Thirty daily columns fit a phone; thirty "12 Sep" labels do not. Label
  // every nth column, with n chosen from the width actually available.
  const plotRef = useRef<HTMLDivElement>(null);
  const [plotWidth, setPlotWidth] = useState(0);
  useEffect(() => {
    const el = plotRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setPlotWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const longest = Math.max(...data.map((d) => d.label.length), 1);
  const fits = plotWidth > 0 ? Math.max(1, Math.floor(plotWidth / (longest * 7 + 12))) : data.length;
  const every = Math.max(1, Math.ceil(data.length / fits));

  const max = niceMax(Math.max(...data.map((d) => d.value), 0));
  const peak = data.reduce(
    (best, row, i) => (row.value > (data[best]?.value ?? -1) ? i : best),
    0,
  );
  const ticks = [max, max / 2, 0];

  return (
    <div>
      <div className="flex gap-3">
        {/* Axis ticks carry the values that are not directly labelled. */}
        <div className="flex h-36 flex-col justify-between py-0.5 text-2xs tabular-nums text-text-subtle">
          {ticks.map((tick) => (
            <span key={tick}>{format(tick)}</span>
          ))}
        </div>

        <div ref={plotRef} className="relative min-w-0 flex-1">
          {/* Hairline, solid, recessive. Never dashed. */}
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between">
            {ticks.map((tick) => (
              <div key={tick} className="h-px w-full bg-chart-grid" />
            ))}
          </div>

          {/* gap-0.5 is the 2px surface gap between neighbouring columns. */}
          <div className="relative flex h-36 items-end gap-0.5">
            {data.map((row, index) => {
              const height = max > 0 ? (row.value / max) * 100 : 0;
              return (
                <div
                  key={row.label}
                  className="group relative flex h-full min-w-0 flex-1 items-end justify-center"
                  onMouseEnter={() => setHovered(index)}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered(index)}
                  onBlur={() => setHovered(null)}
                  tabIndex={0}
                  role="img"
                  aria-label={`${row.label}: ${format(row.value)}`}
                >
                  {hovered === index && (
                    <div className="pointer-events-none absolute bottom-full z-10 mb-1 whitespace-nowrap rounded-md border border-border bg-overlay px-2 py-1 text-2xs shadow-md">
                      <span className="font-medium">{row.label}</span>
                      <span className="ml-1.5 tabular-nums text-text-muted">
                        {format(row.value)}
                      </span>
                    </div>
                  )}
                  {/* Only the peak is labelled. A number on every column is
                      chaos and goes unread; the rest are on hover and in the
                      table below. */}
                  {index === peak && row.value > 0 && (
                    <span className="pointer-events-none absolute -top-0.5 text-2xs tabular-nums text-text-muted">
                      {format(row.value)}
                    </span>
                  )}
                  <div
                    className={cn(
                      // Capped rather than filling the slot, so the band keeps
                      // some air. Rounded cap, square baseline.
                      'w-full max-w-6 rounded-t-[4px] bg-chart-1 transition-[height,opacity] duration-500',
                      hovered !== null && hovered !== index && 'opacity-60',
                    )}
                    style={{ height: `${Math.max(height, row.value > 0 ? 2 : 0)}%` }}
                  />
                </div>
              );
            })}
          </div>

          <div className="mt-1.5 flex gap-0.5">
            {data.map((row, index) => (
              <span
                key={row.label}
                aria-hidden
                className="flex min-w-0 flex-1 justify-center whitespace-nowrap text-2xs text-text-subtle"
              >
                {index % every === 0 ? row.label : ''}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* The table view. Required so nothing is reachable by hover alone. */}
      <details className="mt-3">
        <summary className="cursor-pointer text-2xs text-text-subtle hover:text-text">
          Show as table
        </summary>
        <table className="mt-2 w-full text-2xs">
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr className="text-left text-text-subtle">
              <th className="py-1 font-semibold">Period</th>
              <th className="py-1 text-right font-semibold">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {data.map((row) => (
              <tr key={row.label}>
                <td className="py-1 text-text-muted">{row.label}</td>
                <td className="py-1 text-right tabular-nums">{format(row.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
