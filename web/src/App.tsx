import type { ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import AppShell from '@/components/AppShell';
import { Spinner } from '@/components/ui';
import AuditPage from '@/pages/AuditPage';
import DashboardPage from '@/pages/DashboardPage';
import DepartmentsPage from '@/pages/DepartmentsPage';
import LoginPage from '@/pages/LoginPage';
import AnalyticsPage from '@/pages/AnalyticsPage';
import ApprovalsPage from '@/pages/ApprovalsPage';
import NotificationsPage from '@/pages/NotificationsPage';
import ProfilePage from '@/pages/ProfilePage';
import ProjectsPage from '@/pages/ProjectsPage';
import RequestsPage from '@/pages/RequestsPage';
import SetPasswordPage from '@/pages/SetPasswordPage';
import MyTeamPage from '@/pages/MyTeamPage';
import TeamPage from '@/pages/TeamPage';
import TravelLogsPage from '@/pages/TravelLogsPage';
import { useAuth, useHasHydrated } from '@/store/auth';
import { ADMIN_ROLES, type Role } from '@/types';

const ADMINS_AND_MANAGERS: Role[] = [...ADMIN_ROLES, 'MANAGER'];

function RequireAuth({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const location = useLocation();
  const hydrated = useHasHydrated();
  const authenticated = useAuth((s) => s.isAuthenticated());
  const user = useAuth((s) => s.user);

  // Until the persisted session has been read back, we genuinely do not know
  // whether this person is signed in. Redirecting now would bounce them to the
  // login screen on every refresh.
  if (!hydrated) {
    return (
      <div className="grid min-h-dvh place-items-center bg-canvas">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (roles && user && !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/set-password" element={<SetPasswordPage />} />

        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="requests" element={<RequestsPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="profile" element={<ProfilePage />} />
          <Route
            path="approvals"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <ApprovalsPage />
              </RequireAuth>
            }
          />
          <Route
            path="travel-logs"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <TravelLogsPage />
              </RequireAuth>
            }
          />
          <Route
            path="analytics"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <AnalyticsPage />
              </RequireAuth>
            }
          />
          <Route
            path="projects"
            element={
              <RequireAuth roles={ADMINS_AND_MANAGERS}>
                <ProjectsPage />
              </RequireAuth>
            }
          />
          <Route
            path="team"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <TeamPage />
              </RequireAuth>
            }
          />
          <Route
            path="my-team"
            element={
              <RequireAuth roles={['MANAGER']}>
                <MyTeamPage />
              </RequireAuth>
            }
          />
          <Route
            path="departments"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <DepartmentsPage />
              </RequireAuth>
            }
          />
          <Route
            path="audit"
            element={
              <RequireAuth roles={ADMIN_ROLES}>
                <AuditPage />
              </RequireAuth>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
