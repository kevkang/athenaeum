import { useState } from 'react';
import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useAuthStore } from '../store/auth';
import ProjectSettingsModal from './ProjectSettingsModal';
import styles from './AppShell.module.css';

const VIEWS = [
  { label: 'Papers', path: 'papers' },
  { label: 'Knowledge', path: 'knowledge' },
  { label: 'Graph', path: 'graph' },
  { label: 'Synthesis', path: 'synthesis' },
  { label: 'Export', path: 'export' },
] as const;

export default function AppShell() {
  const user = useAuthStore((s) => s.user);
  const location = useRouterState({ select: (s) => s.location });
  const [showSettings, setShowSettings] = useState(false);

  const projectMatch = location.pathname.match(/\/projects\/([^/]+)/);
  const projectId = projectMatch?.[1];

  const activeView = VIEWS.find((v) => location.pathname.includes(`/${v.path}`));

  return (
    <div className={styles.app}>
      <header className={styles.chrome}>
        <Link to="/projects" className={styles.brand}>
          <div className={styles.brandMark} />
          <span>Athenaeum</span>
        </Link>

        <div className={styles.chromeCenter}>
          {projectId && activeView && (
            <>
              <Link to="/projects" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>
                Projects
              </Link>
              <span className={styles.sep}>/</span>
              <span className={styles.current}>{activeView.label}</span>
            </>
          )}
        </div>

        <div className={styles.chromeRight}>
          {projectId && (
            <div className={styles.viewSwitcher}>
              {VIEWS.map((v) => (
                <Link
                  key={v.path}
                  to={`/projects/$projectId/${v.path}` as '/projects/$projectId/papers'}
                  params={{ projectId: projectId! }}
                  className={`${styles.viewBtn} ${activeView?.path === v.path ? styles.viewBtnActive : ''}`}
                >
                  {v.label}
                </Link>
              ))}
            </div>
          )}
          {projectId && (
            <button
              className={styles.settingsBtn}
              onClick={() => setShowSettings(true)}
              title="Project settings"
            >
              Settings
            </button>
          )}
          {user && (
            <div className={styles.orcidChip}>
              <div className={styles.orcidDot} />
              <span className="mono">{user.orcid}</span>
            </div>
          )}
        </div>
      </header>

      <div className={styles.body}>
        <Outlet />
      </div>

      {showSettings && projectId && (
        <ProjectSettingsModal
          projectId={projectId}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  );
}
