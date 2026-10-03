import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import axios from 'axios';
import toast from 'react-hot-toast';

import { errorMessage } from '@/lib/api';

/**
 * How the app tells people what happened.
 *
 * Every failed action shows a red toast with the server's reason, from here,
 * so no screen can forget to. A mutation opts out with
 * `meta: { errorToast: false }` - only where it already has a better error UI
 * (sign-in, setting a password, the approvals clash flow) - and can name a
 * fallback with `meta: { errorFallback }` for when the server gave no reason.
 *
 * Success stays with each call (`toast.success(...)` in its onSuccess),
 * because the good messages name the thing: "Monsoon audit created".
 *
 * Reads that fail on first load are drawn on the page itself; a toast is only
 * added when a refresh fails behind data already on screen, or when a query
 * asks for one with `meta: { errorToast: true }`.
 */
declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: { errorToast?: boolean; errorFallback?: string };
    queryMeta: { errorToast?: boolean };
  }
}

/** A 401 ends the session; the auth store says so once, not once per call. */
function endsSession(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 401;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.meta?.errorToast === false || endsSession(error)) return;
      const message = errorMessage(error, mutation.meta?.errorFallback);
      // Same text, same toast: a double click shows one message, not two.
      toast.error(message, { id: message });
    },
  }),
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (endsSession(error)) return;
      if (query.state.data !== undefined || query.meta?.errorToast === true) {
        const message = errorMessage(error, 'Could not refresh this page.');
        // Same text, same toast. Keyed by query, a page whose panels all fail
        // for one reason - the API down, the database behind - stacked one
        // identical toast per panel: three on the dashboard.
        toast.error(message, { id: message });
      }
    },
  }),
});
