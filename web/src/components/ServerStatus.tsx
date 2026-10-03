import { useQuery, useQueryClient } from '@tanstack/react-query';
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
  const queryClient = useQueryClient();
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
  if (order > 0) {
    // Everyone can act on this one.
    return (
      <Banner
        message="A newer version of this app is available. Reload the page to use it."
        action={<BannerButton onClick={() => window.location.reload()}>Reload</BannerButton>}
      />
    );
  }
  if (order === 0 && !data.migrations_pending) return null;

  if (!isAdmin) {
    // Nothing they can do about it, but a page that will not load should not
    // look like their mistake.
    return (
      <Banner message="The system is being updated, so some pages may not load. Please try again in a few minutes." />
    );
  }

  const problem =
    order < 0
      ? `The API is running older code (${data.version ?? 'before 0.9.0'}) than this page ` +
        `(${API_VERSION}), so some actions fail with "Not found" or "Method not allowed" and some ` +
        'numbers are missing.'
      : 'The database has not been migrated to match this code, so pages will not load until it is.';
  return (
    <Banner
      message={problem}
      action={
        <BannerButton
          onClick={() => {
            // Everything, not just /health: the pages that failed should load
            // again without a reload once the fix is in.
            void queryClient.invalidateQueries();
          }}
        >
          Check again
        </BannerButton>
      }
    >
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-text">
        <li>Stop the API.</li>
        <li>
          Apply the migrations: in VS Code, <span className="font-medium">Terminal → Run Task → DB: apply migrations</span>.
          Or in a terminal in the <code className="font-mono">backend</code> folder:{' '}
          <code className="break-all rounded bg-surface px-1.5 py-0.5 font-mono">
            .venv\Scripts\python.exe -m alembic upgrade head
          </code>{' '}
          (macOS or Linux: <code className="font-mono">.venv/bin/python -m alembic upgrade head</code>).
        </li>
        <li>Start the API again, then press Check again.</li>
      </ol>
      <p className="mt-2 text-xs text-text-muted">
        If the migration stops with an error, its last lines say which step failed and why.
      </p>
    </Banner>
  );
}

/** The banner's frame: the message, an action beside it, details below. */
function Banner({
  message,
  action,
  children,
}: {
  message: string;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div role="alert" className="mb-6 rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span className="flex min-w-0 items-start gap-2.5">
          <AlertTriangle size={17} className="mt-0.5 shrink-0" />
          <span className="break-words">{message}</span>
        </span>
        {action}
      </div>
      {children && <div className="pl-[27px]">{children}</div>}
    </div>
  );
}

function BannerButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 font-semibold underline underline-offset-4 hover:no-underline"
    >
      {children}
    </button>
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
