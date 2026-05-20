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
import PapersView from './pages/PapersView';
import SynthesisView from './pages/SynthesisView';
import KnowledgeView from './pages/KnowledgeView';
import GraphView from './pages/GraphView';
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

const papersRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/papers',
  component: PapersView,
});

const knowledgeRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/knowledge',
  component: KnowledgeView,
});

const graphRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/graph',
  component: GraphView,
});

const synthesisRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/projects/$projectId/synthesis',
  component: SynthesisView,
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
    papersRoute,
    knowledgeRoute,
    graphRoute,
    synthesisRoute,
    exportRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
