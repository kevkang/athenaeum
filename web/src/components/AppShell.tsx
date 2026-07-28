import { useState } from 'react';
import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useAuthStore } from '../store/auth';
import { useUIStore } from '../store/ui';
import ProjectSettingsModal from './ProjectSettingsModal';
import AccountSettingsModal from './AccountSettingsModal';
import styles from './AppShell.module.css';

export default function AppShell() {
  const user = useAuthStore((s) => s.user);
  const location = useRouterState({ select: (s) => s.location });
  const [showSettings, setShowSettings] = useState(false);
  const accountModalOpen = useUIStore((s) => s.accountModalOpen);
  const openAccountModal = useUIStore((s) => s.openAccountModal);
  const closeAccountModal = useUIStore((s) => s.closeAccountModal);
  const theme = useUIStore((s) => s.theme);
  const toggleTheme = useUIStore((s) => s.toggleTheme);

  const projectMatch = location.pathname.match(/\/projects\/([^/]+)/);
  const projectId = projectMatch?.[1];

  return (
    <div className={styles.app}>
      <header className={styles.chrome}>
        <Link to="/projects" className={styles.brand}>
          <div className={styles.brandMark} />
          <span>Athenaeum</span>
        </Link>

        <div className={styles.chromeCenter}>
          {projectId && (
            <div className={styles.viewSwitcher}>
              <Link
                to="/projects/$projectId/workspace"
                params={{ projectId }}
                className={styles.viewBtn}
                activeProps={{ className: styles.viewBtnActive }}
              >
                Workspace
              </Link>
              <Link
                to="/projects/$projectId/compose"
                params={{ projectId }}
                className={styles.viewBtn}
                activeProps={{ className: styles.viewBtnActive }}
              >
                Compose
              </Link>
              <Link
                to="/projects/$projectId/export"
                params={{ projectId }}
                className={styles.viewBtn}
                activeProps={{ className: styles.viewBtnActive }}
              >
                Export
              </Link>
            </div>
          )}
        </div>

        <div className={styles.chromeRight}>
          <button
            className={styles.themeToggle}
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? '☀' : '☾'}
          </button>
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
            <button
              className={styles.orcidChip}
              onClick={() => openAccountModal()}
              title="Account settings"
            >
              <div className={styles.orcidDot} />
              <span className="mono">{user.orcid}</span>
            </button>
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

      {accountModalOpen && (
        <AccountSettingsModal onClose={closeAccountModal} />
      )}
    </div>
  );
}
