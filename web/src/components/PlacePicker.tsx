import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, PencilLine, RotateCcw } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Combobox } from '@/components/Combobox';
import { Field, Input } from '@/components/ui';
import { errorMessage, fetchLocations } from '@/lib/api';

interface PlacePickerProps {
  label: string;
  /** Field id prefix, so the two pickers get distinct ids on one form. */
  id: string;
  state: string;
  city: string;
  onChange: (next: { state: string; city: string }) => void;
  required?: boolean;
  hint?: string;
  className?: string;
}

/**
 * State first, then a place in it - a city, district or assembly constituency.
 *
 * Places used to be free text, which made `HYD`, `hyd`, `Hyd` and `Hyderabad`
 * four different cities. That is not a cosmetic problem: conflict detection
 * compares cities as strings and co-stay matching looks for colleagues in the
 * same one. So the list comes first, searchable because a state can have
 * several hundred places on it.
 *
 * When a place genuinely is not on the list, "Other" switches the field to
 * plain text. The server still matches what is typed against the list before
 * saving, so a typed "hyd" is stored as Hyderabad.
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

  const byState = useMemo(() => locations.data ?? {}, [locations.data]);
  const states = useMemo(() => Object.keys(byState).sort(), [byState]);
  const places = useMemo(() => (state ? (byState[state] ?? []) : []), [byState, state]);

  // A value the list does not have - typed under "Other", or recorded before
  // the list existed - opens in the text box so it reads correctly and can be
  // edited, rather than being silently blanked.
  const notOnList = Boolean(city) && locations.isSuccess && !places.includes(city);
  const [typing, setTyping] = useState(false);
  const showText = typing || notOnList;

  return (
    <div className={className}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={`${label} state`} htmlFor={`${id}_state`} required={required}>
          <Combobox
            id={`${id}_state`}
            required={required}
            value={state}
            options={states}
            loading={locations.isPending}
            placeholder={locations.isPending ? 'Loading…' : 'Search a state'}
            emptyText="No state by that name."
            onChange={(next) => {
              // A place belongs to its state. Keep a typed one, since it may
              // simply be the state that was wrong.
              const keep = showText || (byState[next] ?? []).includes(city);
              onChange({ state: next, city: keep ? city : '' });
            }}
          />
        </Field>

        <Field
          label={`${label} city / constituency`}
          htmlFor={`${id}_city`}
          required={required}
          hint={
            showText
              ? 'Not on the list? Type it as it is usually written.'
              : hint
          }
        >
          {showText ? (
            <div className="flex gap-2">
              <Input
                id={`${id}_city`}
                required={required}
                autoFocus={typing}
                value={city}
                maxLength={120}
                placeholder="Type the place name"
                onChange={(e) => onChange({ state, city: e.target.value })}
              />
              <button
                type="button"
                onClick={() => {
                  setTyping(false);
                  onChange({ state, city: '' });
                }}
                title="Pick from the list instead"
                aria-label="Pick from the list instead"
                className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border text-text-muted hover:bg-surface-sunken hover:text-text"
              >
                <RotateCcw size={16} />
              </button>
            </div>
          ) : (
            <Combobox
              id={`${id}_city`}
              required={required}
              disabled={!state}
              value={city}
              options={places}
              allowCustom
              placeholder={state ? `Search ${places.length} places` : 'Pick a state first'}
              emptyText="Not on the list - choose Other below to type it."
              onChange={(next) => onChange({ state, city: next })}
              action={{
                label: 'Other - type a place',
                hint: 'For a town or village not on the list',
                icon: <PencilLine size={16} className="mt-0.5 shrink-0 text-text-subtle" />,
                onSelect: () => {
                  setTyping(true);
                  onChange({ state, city: '' });
                },
              }}
            />
          )}
        </Field>
      </div>

      {locations.isError && (
        <p
          role="alert"
          className="mt-2 flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2 text-xs text-warning"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            Could not load the place list ({errorMessage(locations.error)}).{' '}
            <button type="button" className="font-medium underline" onClick={() => locations.refetch()}>
              Try again
            </button>
          </span>
        </p>
      )}
    </div>
  );
}
