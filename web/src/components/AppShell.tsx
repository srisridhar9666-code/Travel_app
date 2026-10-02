import { useMutation, useQuery } from '@tanstack/react-query';
import {
  BarChart3,
  Bell,
  CalendarCheck,
  CheckSquare,
  FolderKanban,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  UserRound,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { CreatorCredit, LogoLockup } from '@/components/Logo';
import NotificationBell from '@/components/NotificationBell';
import { PageErrorBoundary, ServerStatusBanner } from '@/components/ServerStatus';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Button } from '@/components/ui';
import { fetchMe, logout, saveThemePreference } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useTheme } from '@/store/theme';
import { ROLE_LABELS, type Role } from '@/types';

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  roles?: Role[];
  /** Which heading it sits under in the sidebar. */
  group: 'Workspace' | 'Operations' | 'Admin';
}

const ADMINS: Role[] = ['ADMIN', 'SYSTEM_ADMIN'];

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, group: 'Workspace' },
  { to: '/requests', label: 'My requests', icon: CalendarCheck, group: 'Workspace' },
  { to: '/notifications', label: 'Notifications', icon: Bell, group: 'Workspace' },
  { to: '/profile', label: 'My profile', icon: UserRound, group: 'Workspace' },
  { to: '/approvals', label: 'Approvals', icon: CheckSquare, roles: ADMINS, group: 'Operations' },
  { to: '/travel-logs', label: 'Travel logs', icon: History, roles: ADMINS, group: 'Operations' },
  { to: '/analytics', label: 'Cost analytics', icon: BarChart3, roles: ADMINS, group: 'Operations' },
  { to: '/projects', label: 'Campaigns', icon: FolderKanban, roles: ADMINS, group: 'Admin' },
  { to: '/team', label: 'Team', icon: Users, roles: ADMINS, group: 'Admin' },
  { to: '/audit', label: 'Activity log', icon: ScrollText, roles: ['SYSTEM_ADMIN'], group: 'Admin' },
];

const GROUPS: NavItem['group'][] = ['Workspace', 'Operations', 'Admin'];

function initials(name: string) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export default function AppShell() {
  const location = useLocation();
  const user = useAuth((s) => s.user);
  const signOut = useAuth((s) => s.signOut);
  const preference = useTheme((s) => s.preference);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Navigating should never leave the mobile drawer covering the page.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  // Escape closes the drawer, and the page behind must not scroll while it is open.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDrawerOpen(false);
    window.addEventListener('keydown', onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  // Mirror the theme choice to the server so it follows the user to another
  // device. Failure here is deliberately silent - the local choice still applies.
  useEffect(() => {
    if (!user) return;
    if (user.theme_preference === preference) return;
    saveThemePreference(preference)
      .then((updated) => useAuth.getState().setUser(updated))
      .catch(() => undefined);
  }, [preference, user]);

  // The copy of the user saved at sign-in goes stale: an admin may rename
  // them, change their role or department, or switch the account off. Reading
  // it again on load and whenever the tab regains focus keeps the shell
  // honest, and a switched-off account is signed out there and then (401).
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, refetchOnWindowFocus: true });
  useEffect(() => {
    if (me.data) useAuth.getState().setUser(me.data);
  }, [me.data]);

  const signOutMutation = useMutation({
    mutationFn: logout,
    // The session ends locally regardless - a failed call must not trap
    // someone in a session they have asked to leave - so a failure is not
    // worth a red toast either.
    meta: { errorToast: false },
    onSettled: () => {
      signOut();
      toast.success('Signed out');
    },
  });

  const visible = NAV.filter((item) => !item.roles || (user && item.roles.includes(user.role)));

  const currentLabel = visible.find((item) =>
    item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to),
  )?.label;

  return (
    <div className="min-h-dvh bg-canvas">
      {/* The first thing a keyboard user reaches. Without it, every page starts
          with six nav links before the content. Visually hidden until focused. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-fg"
      >
        Skip to content
      </a>

      {drawerOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden
        />
      )}

      <aside
        // A drawer below lg and a permanent rail above it. Deliberately not
        // aria-hidden when closed: the same element is the desktop navigation,
        // and hiding it on that breakpoint would remove the nav entirely.
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col border-r border-border bg-surface transition-transform duration-300 ease-out lg:w-64',
          drawerOpen ? 'translate-x-0' : 'max-lg:-translate-x-full',
        )}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-4">
          <LogoLockup />
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            className="rounded-md p-1.5 text-text-subtle hover:bg-surface-sunken hover:text-text lg:hidden"
            aria-label="Close navigation"
          >
            <X size={16} />
          </button>
        </div>

        <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
          {GROUPS.map((group) => {
            const items = visible.filter((item) => item.group === group);
            if (items.length === 0) return null;
            return (
              <div key={group}>
                {/* Ground staff see one short list, so a heading would only
                    add noise there. */}
                {visible.length > 4 && (
                  <p className="mb-1.5 px-2.5 text-2xs font-semibold uppercase tracking-wider text-text-subtle">
                    {group}
                  </p>
                )}
                <div className="space-y-0.5">
                  {items.map(({ to, label, icon: Icon }) => (
                    <NavLink
                      key={to}
                      to={to}
                      end={to === '/'}
                      className={({ isActive }) =>
                        cn(
                          'group relative flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-sm transition-colors',
                          isActive
                            ? 'bg-surface-sunken font-medium text-text'
                            : 'text-text-muted hover:bg-surface-sunken hover:text-text',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {/* Brand red earns its place here: identity, not an action. */}
                          {isActive && (
                            <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-brand" />
                          )}
                          <Icon size={18} strokeWidth={2} className="shrink-0" />
                          <span className="truncate">{label}</span>
                        </>
                      )}
                    </NavLink>
                  ))}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 border-t border-border p-3">
          <div className="flex items-center gap-1">
            <NavLink
              to="/profile"
              title="My profile"
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-2 py-2 transition-colors hover:bg-surface-sunken"
            >
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand-strong">
                {user ? initials(user.full_name) : '?'}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{user?.full_name}</p>
                <p className="truncate text-xs text-text-subtle">
                  {user ? ROLE_LABELS[user.role] : ''}
                </p>
              </div>
            </NavLink>
            <Button
              variant="ghost"
              size="icon"
              title="Sign out"
              aria-label="Sign out"
              loading={signOutMutation.isPending}
              onClick={() => signOutMutation.mutate()}
            >
              {!signOutMutation.isPending && <LogOut size={15} />}
            </Button>
          </div>
          <CreatorCredit className="mt-1 px-2" />
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur-md sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="rounded-md p-1.5 text-text-muted hover:bg-surface-sunken hover:text-text lg:hidden"
            aria-label="Open navigation"
          >
            <Menu size={20} />
          </button>

          <span className="truncate text-base font-semibold">{currentLabel ?? 'Travel Ops'}</span>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          className="mx-auto w-full max-w-7xl animate-fade-in px-4 py-6 sm:px-6 sm:py-8 lg:px-8 focus:outline-none"
        >
          <ServerStatusBanner isAdmin={!!user && ADMINS.includes(user.role)} />
          <PageErrorBoundary key={location.pathname}>
            <Outlet />
          </PageErrorBoundary>
        </main>

        {/* Route changes are silent in a single-page app. This announces the
            new page so a screen-reader user knows the link did something. */}
        <p aria-live="polite" className="sr-only">
          {currentLabel ? `${currentLabel} page` : ''}
        </p>
      </div>
    </div>
  );
}
