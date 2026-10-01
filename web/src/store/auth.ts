import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

import { setAccessToken, setUnauthorizedHandler } from '@/lib/api';
import { useTheme } from '@/store/theme';
import type { LoginResponse, Role, UserProfile } from '@/types';

interface AuthState {
  token: string | null;
  expiresAt: string | null;
  user: UserProfile | null;

  signIn: (result: LoginResponse) => void;
  signOut: () => void;
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

      signIn(result) {
        setAccessToken(result.access_token);
        set({
          token: result.access_token,
          expiresAt: result.expires_at,
          user: result.user,
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

      signOut() {
        setAccessToken(null);
        set({ token: null, expiresAt: null, user: null });
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

// Any 401 from anywhere drops the session, so a revoked or expired token cannot
// leave the UI showing a signed-in shell it can no longer populate.
setUnauthorizedHandler(() => {
  if (useAuth.getState().token) useAuth.getState().signOut();
});
