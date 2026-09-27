import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from '@tanstack/react-router';
import { useAuthStore } from './store/auth';
import LoginPage from './pages/LoginPage';
import HomePage from './pages/HomePage';
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

// Where the API sends the browser after ORCID sign-in, carrying the session
// token in the query string. Handled in beforeLoad rather than in an effect
// inside AuthCallbackPage: React StrictMode runs effects twice in development,
// and the old effect erased the query string on its first run, so its second
// run found no token and sent the user back to /login — a login loop that
// only ever appeared locally. beforeLoad runs once per navigation, outside
// React's effect lifecycle, and nothing here mutates the URL it reads.
const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth/callback',
  beforeLoad({ location }) {
    // The raw query string, not the router's parsed `search`: the parsed form
    // JSON-decodes values, which could reinterpret an ORCID display name.
    const params = new URLSearchParams(location.searchStr);
    const token = params.get('token');
    const orcid = params.get('orcid');
    const userId = params.get('user_id');

    if (token && orcid && userId) {
      useAuthStore.getState().login(token, {
        id: userId,
        orcid,
        name: params.get('name') || orcid,
      });
      // replace, so the URL carrying the token never becomes a history entry
      // the back button could return to.
      throw redirect({ to: '/projects', replace: true });
    }
    throw redirect({ to: '/login', replace: true });
  },
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
    // Logged-in visitors land on their actual dashboard, unchanged.
    // Logged-out visitors get the public home page (rendered PRINCIPLES.athmd)
    // instead of being bounced straight to /login, which is still reachable
    // separately via the page's own "Sign in" link.
    const { isAuthenticated } = useAuthStore.getState();
    if (isAuthenticated) throw redirect({ to: '/projects' });
  },
  component: HomePage,
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
