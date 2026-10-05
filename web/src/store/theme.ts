import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type ThemePreference = 'light' | 'dark' | 'system';
type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'travel-ops-theme';

function systemTheme(): ResolvedTheme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function resolve(preference: ThemePreference): ResolvedTheme {
  return preference === 'system' ? systemTheme() : preference;
}

/** Paint the resolved theme onto <html> - at once. A cross-fade used to run on
 *  every element, which on a phone held the switch up for seconds; the hover
 *  fades a few buttons carry are switched off for the one frame too, so
 *  nothing animates and the new colours land in a single paint. */
function apply(resolved: ResolvedTheme) {
  const root = document.documentElement;
  if (root.dataset.theme === resolved) return;
  const still = document.createElement('style');
  still.textContent = '*,*::before,*::after{transition:none!important}';
  document.head.appendChild(still);
  root.dataset.theme = resolved;
  // Read a style so the switch is applied while transitions are off.
  void window.getComputedStyle(root).color;
  window.requestAnimationFrame(() => still.remove());
}

interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** Cycles light -> dark -> system, for the header toggle. */
  cycle: () => void;
  syncFromSystem: () => void;
}

export const useTheme = create<ThemeState>()(
  persist(
    (set, get) => ({
      preference: 'system',
      resolved: 'light',

      setPreference(preference) {
        const resolved = resolve(preference);
        apply(resolved);
        set({ preference, resolved });
      },

      cycle() {
        const order: ThemePreference[] = ['light', 'dark', 'system'];
        const next = order[(order.indexOf(get().preference) + 1) % order.length];
        get().setPreference(next);
      },

      syncFromSystem() {
        if (get().preference !== 'system') return;
        const resolved = systemTheme();
        apply(resolved);
        set({ resolved });
      },
    }),
    {
      name: STORAGE_KEY,
      partialize: (state) => ({ preference: state.preference }) as ThemeState,
      // Rehydration is when we learn the persisted preference, so that is the
      // moment to reconcile the DOM with it - without animating.
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        const resolved = resolve(state.preference);
        apply(resolved);
        state.resolved = resolved;
      },
    },
  ),
);

/** Keep 'system' honest when the OS theme changes while the app is open. */
export function watchSystemTheme() {
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const onChange = () => useTheme.getState().syncFromSystem();
  media.addEventListener('change', onChange);
  return () => media.removeEventListener('change', onChange);
}
