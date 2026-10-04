import { useMutation, useQuery } from '@tanstack/react-query';
import {
  BarChart3,
  Bell,
  Building2,
  CalendarCheck,
  CheckSquare,
  ChevronDown,
  FolderKanban,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';

import { CompanyCredit, CreatorCredit, Logo, LogoLockup } from '@/components/Logo';
import NotificationBell from '@/components/NotificationBell';
import { PageErrorBoundary, ServerStatusBanner } from '@/components/ServerStatus';
import { ThemeToggle } from '@/components/ThemeToggle';
import { Spinner } from '@/components/ui';
import {
  fetchMe,
  fetchQueueCounts,
  fetchUnreadCount,
  logout,
  saveThemePreference,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useTheme } from '@/store/theme';
import { ROLE_LABELS, type Role } from '@/types';

type Group = 'Workspace' | 'Operations' | 'Manage';

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  roles?: Role[];
  /** Which section it sits under in the sidebar. */
  group: Group;
  /** A live count beside it: unread notices, or requests waiting on a decision. */
  badge?: 'unread' | 'queue';
  /** On a phone, the first three of these (lowest first) get the bottom bar. */
  bottom?: number;
}

const ADMINS: Role[] = ['ADMIN', 'SYSTEM_ADMIN'];

// My profile is not here: the name card at the foot of the sidebar opens it.
const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, group: 'Workspace', bottom: 1 },
  { to: '/requests', label: 'My requests', icon: CalendarCheck, group: 'Workspace', bottom: 2 },
  { to: '/notifications', label: 'Notifications', icon: Bell, group: 'Workspace', badge: 'unread', bottom: 3 },
  { to: '/approvals', label: 'Approvals', icon: CheckSquare, roles: ADMINS, group: 'Operations', badge: 'queue', bottom: 0 },
  { to: '/travel-logs', label: 'Travel logs', icon: History, roles: ADMINS, group: 'Operations' },
  { to: '/analytics', label: 'Cost analytics', icon: BarChart3, roles: ADMINS, group: 'Operations' },
  { to: '/projects', label: 'Campaigns', icon: FolderKanban, roles: ADMINS, group: 'Manage' },
  { to: '/team', label: 'Team', icon: Users, roles: ADMINS, group: 'Manage' },
  { to: '/departments', label: 'Departments', icon: Building2, roles: ADMINS, group: 'Manage' },
  { to: '/audit', label: 'Activity log', icon: ScrollText, roles: ['SYSTEM_ADMIN'], group: 'Manage' },
];

const GROUPS: Group[] = ['Workspace', 'Operations', 'Manage'];

// Per-browser conveniences, so storage that is blocked or cleared costs only
// the remembered layout.
const RAIL_KEY = 'sriyatra.sidebar.rail';
const CLOSED_KEY = 'sriyatra.sidebar.closed';

function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode or blocked storage: the choice lasts until reload.
  }
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

function badgeText(count: number) {
  return count > 99 ? '99+' : String(count);
}

export default function AppShell() {
  const location = useLocation();
  const user = useAuth((s) => s.user);
  const signOut = useAuth((s) => s.signOut);
  const preference = useTheme((s) => s.preference);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Desktop only: the sidebar folded down to an icon rail.
  const [rail, setRail] = useState(() => readPref(RAIL_KEY) === '1');
  const [closed, setClosed] = useState<Group[]>(() => {
    try {
      return JSON.parse(readPref(CLOSED_KEY) ?? '[]');
    } catch {
      return [];
    }
  });

  const toggleRail = () => {
    setRail((value) => {
      writePref(RAIL_KEY, value ? '0' : '1');
      return !value;
    });
  };
  const toggleGroup = (group: Group) => {
    setClosed((current) => {
      const next = current.includes(group) ? current.filter((g) => g !== group) : [...current, group];
      writePref(CLOSED_KEY, JSON.stringify(next));
      return next;
    });
  };

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

  const isAdmin = !!user && ADMINS.includes(user.role);
  // The same queries the bell and the approvals page read, so the counts agree.
  const unread = useQuery({ queryKey: ['unread-count'], queryFn: fetchUnreadCount });
  const queue = useQuery({
    queryKey: ['queue-counts'],
    queryFn: () => fetchQueueCounts(),
    enabled: isAdmin,
  });
  const countFor = (item: NavItem) =>
    item.badge === 'unread'
      ? (unread.data?.unread ?? 0)
      : item.badge === 'queue' && queue.data
        ? queue.data.awaiting + queue.data.partially_approved
        : 0;

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
  const isCurrent = (item: NavItem) =>
    item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to);
  const currentLabel =
    visible.find(isCurrent)?.label ?? (location.pathname.startsWith('/profile') ? 'My profile' : undefined);
  const bottomItems = visible
    .filter((item) => item.bottom !== undefined)
    .sort((a, b) => (a.bottom ?? 0) - (b.bottom ?? 0))
    .slice(0, 3);
  // Ground staff see one short list, so section headings would only add noise.
  const sectioned = visible.length > 4;

  return (
    <div className="min-h-dvh bg-canvas">
      {/* The first thing a keyboard user reaches. Without it, every page starts
          with the whole menu before the content. Visually hidden until focused. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-fg"
      >
        Skip to content
      </a>

      {drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={() => setDrawerOpen(false)}
          aria-hidden
        />
      )}

      <aside
        // A drawer below lg and a permanent sidebar above it. Deliberately not
        // aria-hidden when closed: the same element is the desktop navigation,
        // and hiding it on that breakpoint would remove the nav entirely.
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col bg-sidebar text-white shadow-xl transition-[transform,width] duration-300 ease-out lg:shadow-none',
          drawerOpen ? 'translate-x-0' : 'max-lg:-translate-x-full',
          rail ? 'lg:w-[76px]' : 'lg:w-64',
        )}
      >
        <div className={cn('flex h-16 shrink-0 items-center justify-between gap-2 px-4', rail && 'lg:justify-center lg:px-0')}>
          <Link to="/" aria-label="Sriyatra home" className="min-w-0 rounded-lg focus-visible:outline-offset-2">
            <LogoLockup onDark className={cn(rail && 'lg:hidden')} />
            {rail && <LogoLockup onDark markOnly className="hidden lg:flex" />}
          </Link>
          <button
            type="button"
            onClick={() => setDrawerOpen(false)}
            className="rounded-md p-1.5 text-white/70 hover:bg-white/10 hover:text-white lg:hidden"
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>

        <nav aria-label="Main" className="flex-1 space-y-4 overflow-y-auto px-3 pb-4 pt-2">
          {GROUPS.map((group, index) => {
            const items = visible.filter((item) => item.group === group);
            if (items.length === 0) return null;
            // The section holding the current page never hides it.
            const open = !closed.includes(group) || items.some(isCurrent);
            const listId = `nav-${group.toLowerCase()}`;
            return (
              <div key={group}>
                {sectioned && (
                  <button
                    type="button"
                    onClick={() => toggleGroup(group)}
                    aria-expanded={open}
                    aria-controls={listId}
                    className={cn(
                      'mb-1.5 flex w-full items-center justify-between rounded-md px-3 py-1 text-2xs font-semibold uppercase tracking-[0.12em] text-white/55 transition-colors hover:text-white/85',
                      rail && 'lg:hidden',
                    )}
                  >
                    {group}
                    <ChevronDown size={14} className={cn('transition-transform', !open && '-rotate-90')} />
                  </button>
                )}
                {rail && index > 0 && <div className="mx-3 mb-3 hidden border-t border-white/10 lg:block" />}
                <ul
                  id={listId}
                  // The rail has no headings to reopen a section from, so it
                  // always shows every item.
                  className={cn('space-y-1', !open && 'hidden', !open && rail && 'lg:block')}
                >
                  {items.map((item) => {
                    const Icon = item.icon;
                    const count = countFor(item);
                    return (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          end={item.to === '/'}
                          title={rail ? item.label : undefined}
                          className={({ isActive }) =>
                            cn(
                              'relative flex h-10 items-center gap-3 rounded-xl px-3 text-sm transition-colors',
                              isActive
                                ? 'bg-white/[0.12] font-semibold text-white'
                                : 'text-white/70 hover:bg-white/[0.06] hover:text-white',
                              rail && 'lg:justify-center lg:px-0',
                            )
                          }
                        >
                          {({ isActive }) => (
                            <>
                              {isActive && (
                                <span className="absolute inset-y-2.5 left-0 w-[3px] rounded-full bg-brand" />
                              )}
                              <Icon size={19} strokeWidth={isActive ? 2.25 : 2} className="shrink-0" />
                              <span className={cn('truncate', rail && 'lg:sr-only')}>{item.label}</span>
                              {count > 0 && (
                                <span
                                  aria-label={`${count} waiting`}
                                  className={cn(
                                    'ml-auto min-w-[1.375rem] rounded-full bg-white px-1.5 text-center text-2xs font-bold leading-5 text-sidebar tabular-nums',
                                    rail && 'lg:absolute lg:right-1.5 lg:top-1 lg:ml-0 lg:min-w-[1.125rem] lg:px-1 lg:leading-4',
                                  )}
                                >
                                  {badgeText(count)}
                                </span>
                              )}
                            </>
                          )}
                        </NavLink>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 border-t border-white/10 p-3">
          <div className={cn('flex items-center gap-1', rail && 'lg:flex-col lg:gap-2')}>
            <NavLink
              to="/profile"
              title="My profile"
              className={({ isActive }) =>
                cn(
                  'flex min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-white/[0.06]',
                  isActive && 'bg-white/[0.12]',
                  rail && 'lg:flex-none lg:justify-center lg:p-1',
                )
              }
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/15 text-xs font-semibold text-white ring-1 ring-white/20">
                {user ? initials(user.full_name) : '?'}
              </span>
              <span className={cn('min-w-0 flex-1', rail && 'lg:hidden')}>
                <span className="block truncate text-sm font-medium text-white">{user?.full_name}</span>
                <span className="block truncate text-xs text-white/60">
                  {user ? ROLE_LABELS[user.role] : ''}
                </span>
              </span>
            </NavLink>
            <button
              type="button"
              title="Sign out"
              aria-label="Sign out"
              disabled={signOutMutation.isPending}
              onClick={() => signOutMutation.mutate()}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-white/70 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-60"
            >
              {signOutMutation.isPending ? <Spinner className="h-4 w-4" /> : <LogOut size={16} />}
            </button>
          </div>

          <div className={cn('mt-2 flex items-center justify-between gap-2 pl-2', rail && 'lg:justify-center lg:pl-0')}>
            <div className={cn('min-w-0 space-y-0.5', rail && 'lg:hidden')}>
              <CompanyCredit onDark short />
              <CreatorCredit onDark />
            </div>
            <button
              type="button"
              onClick={toggleRail}
              aria-label={rail ? 'Expand the sidebar' : 'Collapse the sidebar'}
              title={rail ? 'Expand the sidebar' : 'Collapse the sidebar'}
              className="hidden h-8 w-8 shrink-0 place-items-center rounded-lg text-white/60 transition-colors hover:bg-white/10 hover:text-white lg:grid"
            >
              {rail ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </div>
        </div>
      </aside>

      <div className={cn('transition-[padding] duration-300 ease-out', rail ? 'lg:pl-[76px]' : 'lg:pl-64')}>
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-border bg-surface/80 px-4 backdrop-blur-md sm:px-6 lg:px-8">
          <Link to="/" aria-label="Sriyatra home" className="shrink-0 lg:hidden">
            <Logo variant="mark" className="h-8 w-8" />
          </Link>
          <span className="truncate text-base font-semibold">{currentLabel ?? 'Sriyatra'}</span>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <NotificationBell />
            <ThemeToggle />
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          // Bottom padding on phones clears the tab bar.
          className="mx-auto w-full max-w-7xl animate-fade-in px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:px-8 lg:pb-8 focus:outline-none"
        >
          <ServerStatusBanner isAdmin={isAdmin} />
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

      {/* Phones: the pages people open most, one thumb away, and the full
          menu behind More. */}
      <nav
        aria-label="Quick links"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur-md lg:hidden"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <ul className="grid grid-cols-4">
          {bottomItems.map((item) => {
            const Icon = item.icon;
            const count = countFor(item);
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    cn(
                      'flex h-16 flex-col items-center justify-center gap-1 text-[0.6875rem] font-medium transition-colors',
                      isActive ? 'text-brand-strong' : 'text-text-muted hover:text-text',
                    )
                  }
                >
                  <span className="relative">
                    <Icon size={21} />
                    {count > 0 && (
                      <span
                        aria-label={`${count} waiting`}
                        className="absolute -right-3 -top-1.5 min-w-[1.125rem] rounded-full bg-[rgb(200_30_30)] px-1 text-center text-[0.625rem] font-bold leading-4 text-white tabular-nums"
                      >
                        {badgeText(count)}
                      </span>
                    )}
                  </span>
                  <span className="max-w-full truncate px-1">{item.label}</span>
                </NavLink>
              </li>
            );
          })}
          <li>
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open the full menu"
              aria-expanded={drawerOpen}
              className="flex h-16 w-full flex-col items-center justify-center gap-1 text-[0.6875rem] font-medium text-text-muted hover:text-text"
            >
              <Menu size={21} />
              More
            </button>
          </li>
        </ul>
      </nav>
    </div>
  );
}
