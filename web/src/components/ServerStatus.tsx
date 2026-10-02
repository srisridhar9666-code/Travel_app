import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { Component, type ErrorInfo, type ReactNode } from 'react';

import { Button, Card, EmptyState } from '@/components/ui';
import { API_VERSION, compareVersions, fetchHealth } from '@/lib/api';

/**
 * Says when the API process and this page disagree about what exists.
 *
 * The API runs without auto-reload, so after an update it keeps serving the
 * old code until someone restarts it. Without this, that looks like a dozen
 * unrelated bugs: "Method Not Allowed" on saving a profile, "Not Found" on an
 * export, a tile stuck on a dash, a report page gone blank.
 */
export function ServerStatusBanner({ isAdmin }: { isAdmin: boolean }) {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    staleTime: 5 * 60 * 1000,
    // Coming back to the tab after restarting the API clears the warning.
    refetchOnWindowFocus: true,
  });
  const data = health.data;
  if (!data) return null;

  const order = data.version ? compareVersions(data.version, API_VERSION) : -1;
  let message: string | null = null;
  if (order > 0) {
    // Everyone can act on this one.
    message = 'A newer version of this app is available. Reload the page to use it.';
  } else if (!isAdmin) {
    return null;
  } else if (order < 0) {
    message =
      `The API server is running older code (${data.version ?? 'before 0.9.0'}) than this page ` +
      `(${API_VERSION}), so some actions will fail with "Not found" or "Method not allowed" ` +
      'and some numbers will be missing. Stop the API, run "alembic upgrade head" in backend/, ' +
      'and start it again.';
  } else if (data.migrations_pending) {
    message =
      'The database has not been migrated to match this code, so some pages will fail. ' +
      'Stop the API, run "alembic upgrade head" in backend/, and start it again.';
  }
  if (!message) return null;

  return (
    <div
      role="alert"
      className="mb-6 flex flex-col gap-2 rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger sm:flex-row sm:items-center sm:justify-between"
    >
      <span className="flex min-w-0 items-start gap-2.5">
        <AlertTriangle size={17} className="mt-0.5 shrink-0" />
        <span className="break-words">{message}</span>
      </span>
      {order > 0 && (
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="shrink-0 font-semibold underline underline-offset-4 hover:no-underline"
        >
          Reload
        </button>
      )}
    </div>
  );
}

/**
 * A page that throws while rendering shows this instead of taking the whole
 * app down to a blank screen. The shell keys it by route, so moving to
 * another page starts clean.
 */
export class PageErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Card>
        <EmptyState
          icon={<AlertTriangle size={28} />}
          title="This page could not be shown"
          description={`${error.message.replace(/\.$/, '')}. If the API was updated recently, restart it - a server older than this page sends data the page cannot read.`}
          action={
            <Button variant="secondary" onClick={() => this.setState({ error: null })}>
              <RotateCcw size={15} />
              Try again
            </Button>
          }
        />
      </Card>
    );
  }
}
