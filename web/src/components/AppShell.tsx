import { useMutation } from '@tanstack/react-query';
import {
  BarChart3,
  Bell,
  CalendarCheck,
  CheckSquare,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';

import { LogoLockup } from '@/components/Logo';
import NotificationBell from '@/components/NotificationBell';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Badge, Button } from '@/components/ui';
import { logout, saveThemePreference } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useTheme } from '@/store/theme';
import { ROLE_LABELS, type Role } from '@/types';

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  roles?: Role[];
  /** Shown greyed with a "Soon" chip until the phase that builds it lands. */
  phase?: number;
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/requests', label: 'My requests', icon: CalendarCheck },
  { to: '/approvals', label: 'Approvals', icon: CheckSquare, roles: ['ADMIN', 'SYSTEM_ADMIN'] },
  { to: '/analytics', label: 'Cost analytics', icon: BarChart3, roles: ['ADMIN', 'SYSTEM_ADMIN'] },
  { to: '/projects', label: 'Projects', icon: FolderKanban, roles: ['ADMIN', 'SYSTEM_ADMIN'] },
  { to: '/team', label: 'Team', icon: Users, roles: ['ADMIN', 'SYSTEM_ADMIN'] },
  { to: '/notifications', label: 'Notifications', icon: Bell },
  { to: '/audit', label: 'Activity log', icon: ScrollText, roles: ['SYSTEM_ADMIN'] },
];

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

  const signOutMutation = useMutation({
    mutationFn: logout,
    // The session ends locally regardless - a failed call must not trap
    // someone in a session they have asked to leave.
    onSettled: () => signOut(),
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
          'fixed inset-y-0 left-0 z-40 flex w-64 max-w-[85vw] flex-col border-r border-border bg-surface transition-transform duration-300 ease-out',
          drawerOpen ? 'translate-x-0' : 'max-lg:-translate-x-full',
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
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

        <nav aria-label="Main" className="flex-1 space-y-0.5 overflow-y-auto p-3">
          {visible.map(({ to, label, icon: Icon, phase }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
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
                    <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-brand" />
                  )}
                  <Icon size={16} strokeWidth={2} className="shrink-0" />
                  <span className="truncate">{label}</span>
                  {phase && (
                    <span className="ml-auto text-2xs font-medium text-text-subtle">
                      Phase {phase}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="shrink-0 border-t border-border p-3">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-2">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface-sunken text-2xs font-semibold text-text-muted">
              {user ? initials(user.full_name) : '?'}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">{user?.full_name}</p>
              <p className="truncate text-2xs text-text-subtle">
                {user ? ROLE_LABELS[user.role] : ''}
              </p>
            </div>
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
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur-md sm:px-6">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="rounded-md p-1.5 text-text-muted hover:bg-surface-sunken hover:text-text lg:hidden"
            aria-label="Open navigation"
          >
            <Menu size={18} />
          </button>

          <span className="text-sm font-medium">{currentLabel ?? 'Travel Ops'}</span>

          <div className="ml-auto flex items-center gap-3">
            <Badge tone="neutral" className="hidden sm:inline-flex">
              Phase 7
            </Badge>
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          className="mx-auto max-w-6xl animate-fade-in px-4 py-6 sm:px-6 sm:py-8 focus:outline-none"
        >
          <Outlet />
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
