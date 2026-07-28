import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';
import { useAuthStore } from './store/auth';
import LoginPage from './pages/LoginPage';
import AuthCallbackPage from './pages/AuthCallbackPage';
import ProjectListPage from './pages/ProjectListPage';
import WorkspaceView from './pages/WorkspaceView';
import ComposeView from './pages/ComposeView';
import ExportView from './pages/ExportView';
import AppShell from './components/AppShell';

const rootRoute = createRootRoute({ component: Outlet });

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  component: LoginPage,
});

const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth/callback',
  component: AuthCallbackPage,
});

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  beforeLoad() {
    const { isAuthenticated } = useAuthStore.getState();
    if (!isAuthenticated) throw redirect({ to: '/login' });
  },
  component: AppShell,
});

const projectsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects',
  component: ProjectListPage,
});

// Bare /projects/$projectId has no view of its own — land on Workspace by default.
const projectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId',
  beforeLoad({ params }) {
    throw redirect({ to: '/projects/$projectId/workspace', params });
  },
});

const workspaceRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/workspace',
  component: WorkspaceView,
});

const composeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/compose',
  component: ComposeView,
});

const exportRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/export',
  component: ExportView,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad() {
    const { isAuthenticated } = useAuthStore.getState();
    throw redirect({ to: isAuthenticated ? '/projects' : '/login' });
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  authCallbackRoute,
  appRoute.addChildren([
    projectsRoute,
    projectRoute,
    workspaceRoute,
    composeRoute,
    exportRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
