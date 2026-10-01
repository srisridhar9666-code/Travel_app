import { Monitor, Moon, Sun } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useTheme, type ThemePreference } from '@/store/theme';

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
];

/**
 * A three-way segmented control rather than a binary switch, because "follow the
 * OS" is a real preference and a two-state toggle silently drops it.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const preference = useTheme((state) => state.preference);
  const setPreference = useTheme((state) => state.setPreference);

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className={cn(
        'inline-flex items-center gap-0.5 rounded-lg border border-border bg-surface-sunken p-0.5',
        className,
      )}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = preference === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => setPreference(value)}
            className={cn(
              'grid h-7 w-7 place-items-center rounded-md transition-colors',
              active
                ? 'bg-surface text-text shadow-sm'
                : 'text-text-subtle hover:text-text-muted',
            )}
          >
            <Icon size={14} strokeWidth={2.25} />
          </button>
        );
      })}
    </div>
  );
}
