import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { create } from 'zustand';
import { createJSONStorage, persist, type StateStorage } from 'zustand/middleware';

import { setAccessToken, setUnauthorizedHandler } from '@/lib/api';
import { useTheme } from '@/store/theme';
import type { LoginResponse, Role, UserProfile } from '@/types';

const SESSION_KEY = 'travel-ops-session';

/**
 * Each tab keeps its own sign-in, so two tabs can be two different people - an
 * admin in one and a field account in the other. They used to share one, and
 * signing in as someone else in a second tab quietly turned the first tab into
 * that person too.
 *
 * sessionStorage holds this tab's session and survives a reload. localStorage
 * holds the last one written in any tab, only so that a brand-new tab starts
 * signed in rather than at the login screen. Tabs on the same session are kept
 * in step by `channel` below.
 */
const tabStorage: StateStorage = {
  getItem: (name) => sessionStorage.getItem(name) ?? localStorage.getItem(name),
  setItem: (name, value) => {
    sessionStorage.setItem(name, value);
    localStorage.setItem(name, value);
  },
  removeItem: (name) => {
    sessionStorage.removeItem(name);
    localStorage.removeItem(name);
  },
};

/** What one tab tells the others about a session it shares with them. */
type SessionMessage =
  /** That token was signed out (or rejected); every tab holding it ends too. */
  | { kind: 'signed-out'; token: string }
  /** A password change replaced this person's token; their tabs adopt it, or
   *  their next request would 401 on the old one. */
  | { kind: 'token'; userId: number; token: string; expiresAt: string };

const channel =
  typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(SESSION_KEY);

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
        const ended = get().token;
        setAccessToken(null);
        set({ token: null, expiresAt: null, user: null, notice: notice ?? null });
        if (ended) channel?.postMessage({ kind: 'signed-out', token: ended } satisfies SessionMessage);
      },

      replaceToken(token, expiresAt) {
        setAccessToken(token);
        set({ token, expiresAt });
        const userId = get().user?.id;
        if (userId !== undefined) {
          channel?.postMessage({ kind: 'token', userId, token, expiresAt } satisfies SessionMessage);
        }
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
      name: SESSION_KEY,
      storage: createJSONStorage(() => tabStorage),
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

// Another tab changed a session this tab may share. A sign-in elsewhere is that
// tab's own business and changes nothing here.
channel?.addEventListener('message', (event: MessageEvent<SessionMessage>) => {
  const message = event.data;
  const state = useAuth.getState();
  if (!state.token) return;
  if (message.kind === 'signed-out' && message.token === state.token) {
    // Set directly rather than through signOut(), which would announce it again.
    setAccessToken(null);
    useAuth.setState({ token: null, expiresAt: null, user: null, notice: null });
    toast('You signed out in another tab. Sign in again here - each tab can use its own account.', {
      id: 'session-ended',
    });
  } else if (message.kind === 'token' && message.userId === state.user?.id) {
    setAccessToken(message.token);
    useAuth.setState({ token: message.token, expiresAt: message.expiresAt });
  }
});

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
