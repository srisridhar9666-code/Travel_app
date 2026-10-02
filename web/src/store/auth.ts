import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { setAccessToken, setUnauthorizedHandler } from '@/lib/api';
import { useTheme } from '@/store/theme';
import type { LoginResponse, Role, UserProfile } from '@/types';

interface AuthState {
  token: string | null;
  expiresAt: string | null;
  user: UserProfile | null;
  /** Why the last session ended, for the sign-in screen. Not persisted. */
  notice: string | null;

  signIn: (result: LoginResponse) => void;
  signOut: (notice?: string) => void;
  /** Swap in a fresh token without touching anything else - after a password
   *  change, which signs out every other device but keeps this one. */
  replaceToken: (token: string, expiresAt: string) => void;
  clearNotice: () => void;
  setUser: (user: UserProfile) => void;
  isAuthenticated: () => boolean;
  hasRole: (...roles: Role[]) => boolean;
}

export const useAuth = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      expiresAt: null,
      user: null,
      notice: null,

      signIn(result) {
        setAccessToken(result.access_token);
        set({
          token: result.access_token,
          expiresAt: result.expires_at,
          user: result.user,
          notice: null,
        });

        // Adopt the saved preference so it follows them from another device -
        // but only if they have not just made a deliberate choice on the login
        // screen. Overriding an explicit click reads as the app ignoring them;
        // the shell's sync effect pushes the local choice up instead.
        const theme = useTheme.getState();
        if (theme.preference === 'system') {
          theme.setPreference(result.user.theme_preference);
        }
      },

      signOut(notice) {
        setAccessToken(null);
        set({ token: null, expiresAt: null, user: null, notice: notice ?? null });
      },

      replaceToken(token, expiresAt) {
        setAccessToken(token);
        set({ token, expiresAt });
      },

      clearNotice() {
        set({ notice: null });
      },

      setUser(user) {
        set({ user });
      },

      isAuthenticated() {
        const { token, expiresAt } = get();
        if (!token) return false;
        // Treat a token that expires within the minute as already gone, rather
        // than letting the user start something that will 401 halfway through.
        if (expiresAt && new Date(expiresAt).getTime() - Date.now() < 60_000) return false;
        return true;
      },

      hasRole(...roles) {
        const role = get().user?.role;
        return role ? roles.includes(role) : false;
      },
    }),
    {
      name: 'travel-ops-session',
      partialize: (state) =>
        ({ token: state.token, expiresAt: state.expiresAt, user: state.user }) as AuthState,
      // Rehydration is the moment the axios client learns about the token.
      // Note this fires during `create()`, while `useAuth` is still in its
      // temporal dead zone - so it must not reference the store by name.
      onRehydrateStorage: () => (state) => {
        if (state?.token) setAccessToken(state.token);
      },
    },
  ),
);

/**
 * Whether the persisted session has been read back from storage yet.
 *
 * Until it has, we genuinely do not know if this person is signed in, and
 * redirecting would bounce them to the login screen on every refresh. This uses
 * the persist API rather than a flag in the store, because a flag would have to
 * be set from inside `onRehydrateStorage`, which runs too early to touch it.
 */
export function useHasHydrated(): boolean {
  const [hydrated, setHydrated] = useState(() => useAuth.persist.hasHydrated());

  useEffect(() => {
    const unsubscribe = useAuth.persist.onFinishHydration(() => setHydrated(true));
    // Hydration may already have finished between the initial render and here.
    if (useAuth.persist.hasHydrated()) setHydrated(true);
    return unsubscribe;
  }, []);

  return hydrated;
}

// Another tab in this browser changed the session - a password change swapped
// the token, or a sign-in or sign-out. Adopt it here at once: otherwise this
// tab's next request carries the old token, 401s, and its sign-out is written
// back to the shared storage, signing out the tab that changed the password.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key !== null && event.key !== useAuth.persist.getOptions().name) return;
    void Promise.resolve(useAuth.persist.rehydrate()).then(() => {
      setAccessToken(useAuth.getState().token);
    });
  });
}

// Any 401 from anywhere drops the session, so a revoked or expired token cannot
// leave the UI showing a signed-in shell it can no longer populate. Every query
// in flight fails at once, so the toast has a fixed id and shows once; and a
// stale token cleared on the sign-in page says nothing at all.
setUnauthorizedHandler((detail) => {
  const state = useAuth.getState();
  if (!state.token) return;
  const wasLive = state.isAuthenticated();
  // The generic "not signed in" answers say nothing a person can act on; an
  // account-specific one ("Your account is deactivated...") does.
  const generic = !detail || /not authenticated|not signed in|session has expired/i.test(detail);
  const message = generic ? undefined : detail;
  state.signOut(message);
  if (wasLive) {
    toast.error(message ?? 'Your session has ended. Please sign in again.', {
      id: 'session-ended',
    });
  }
});
