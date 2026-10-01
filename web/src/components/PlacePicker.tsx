import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { Field, Select } from '@/components/ui';
import { fetchLocations } from '@/lib/api';

interface PlacePickerProps {
  label: string;
  /** Field id prefix, so the two selects get distinct ids on one form. */
  id: string;
  state: string;
  city: string;
  onChange: (next: { state: string; city: string }) => void;
  required?: boolean;
  hint?: string;
  className?: string;
}

/**
 * State first, then the cities in it.
 *
 * Places used to be free text, which made `HYD`, `hyd`, `Hyd` and `Hyderabad`
 * four different cities. That is not a cosmetic problem: conflict detection
 * compares cities as strings and co-stay matching looks for colleagues in the
 * same one, so a single abbreviation hides a colleague who is already in that
 * hotel. Picking from a list is the only thing that actually prevents it.
 *
 * A value that predates the list is kept and shown rather than silently
 * dropped — an old request should still read correctly even if its city was
 * typed as "hyd".
 */
export function PlacePicker({
  label,
  id,
  state,
  city,
  onChange,
  required,
  hint,
  className,
}: PlacePickerProps) {
  const locations = useQuery({
    queryKey: ['locations'],
    queryFn: fetchLocations,
    // The list changes about once a quarter; refetching it per form is waste.
    staleTime: 10 * 60 * 1000,
  });

  const byState = locations.data ?? {};
  const states = useMemo(() => Object.keys(byState).sort(), [byState]);
  const cities = state ? (byState[state] ?? []) : [];

  // A city typed before the picker existed, or one whose state was not stored.
  // Offered as a selected option so editing an old request does not quietly
  // blank its destination.
  const [legacy] = useState(city);
  const cityIsKnown = cities.includes(city);
  const showLegacy = Boolean(legacy) && !cityIsKnown;

  // If the state changes to one that does not contain the chosen city, the city
  // is no longer true and has to go.
  useEffect(() => {
    if (state && city && !cities.includes(city) && city !== legacy) {
      onChange({ state, city: '' });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return (
    <div className={className}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`${label} state`} htmlFor={`${id}_state`} required={required}>
          <Select
            id={`${id}_state`}
            required={required}
            value={state}
            onChange={(e) => onChange({ state: e.target.value, city: '' })}
          >
            <option value="">
              {locations.isPending ? 'Loading…' : 'Choose a state'}
            </option>
            {states.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </Field>

        <Field label={`${label} city`} htmlFor={`${id}_city`} required={required} hint={hint}>
          <Select
            id={`${id}_city`}
            required={required}
            disabled={!state && !showLegacy}
            value={city}
            onChange={(e) => onChange({ state, city: e.target.value })}
          >
            <option value="">{state ? 'Choose a city' : 'Pick a state first'}</option>
            {showLegacy && (
              <option value={legacy}>{legacy} (recorded before the list)</option>
            )}
            {cities.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </div>
  );
}
