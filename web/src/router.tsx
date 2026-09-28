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
import ReadView from './pages/ReadView';
import NucleateView from './pages/NucleateView';
import BacktraceView from './pages/BacktraceView';
import AuditView from './pages/AuditView';
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

// Bare /projects/$projectId has no view of its own — land on Read by default.
const projectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId',
  beforeLoad({ params }) {
    throw redirect({ to: '/projects/$projectId/read', params });
  },
});

const readRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/read',
  component: ReadView,
  // Which paper to open, when arriving from elsewhere (Nucleate's "Open in Read").
  validateSearch: (search: Record<string, unknown>): { paper?: string } => ({
    paper: typeof search.paper === 'string' ? search.paper : undefined,
  }),
});

// Read was called Workspace until the statement graph was retired. Kept as a
// redirect so existing links and bookmarks still land somewhere sensible.
const workspaceRedirectRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/workspace',
  beforeLoad({ params }) {
    throw redirect({ to: '/projects/$projectId/read', params });
  },
});

const nucleateRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/nucleate',
  component: NucleateView,
  // The nucleus being viewed lives in the URL, so a refresh keeps your place.
  validateSearch: (search: Record<string, unknown>): { nucleus?: string } => ({
    nucleus: typeof search.nucleus === 'string' ? search.nucleus : undefined,
  }),
});

const backtraceRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/backtrace',
  component: BacktraceView,
});

const auditRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/audit',
  component: AuditView,
  // The run id lives in the URL so a finished audit survives a refresh and can
  // be linked to. An audit can take a while and its results are the product.
  validateSearch: (search: Record<string, unknown>): { run?: string } => ({
    run: typeof search.run === 'string' ? search.run : undefined,
  }),
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
    readRoute,
    nucleateRoute,
    workspaceRedirectRoute,
    backtraceRoute,
    auditRoute,
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
