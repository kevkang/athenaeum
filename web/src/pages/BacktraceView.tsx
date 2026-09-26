import { useState } from 'react';
import { useParams } from '@tanstack/react-router';
import BacktraceTree from '../components/workspace/BacktraceTree';
import styles from './Workspace.module.css';

/**
 * Backtrace: the citation-chain tree, full screen.
 *
 * Promoted out of the old Workspace's right-hand canvas slot, where it shared
 * space with the statement graph. It needs no surrounding chrome because it is
 * already self-contained — it loads the project's chain history itself, renders
 * its own candidate cards, and has its own "no backtrace selected" empty state.
 *
 * `chainId` is held here rather than inside the tree so that the selection
 * survives the tree's internal re-renders, which is the same contract the old
 * canvas had with it.
 */
export default function BacktraceView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/backtrace' });
  const [chainId, setChainId] = useState<string | null>(null);

  return (
    <div className={styles.workspace}>
      <div className={styles.workspaceBody}>
        <div className={styles.canvasWrapper}>
          <BacktraceTree
            projectId={projectId}
            chainId={chainId}
            visible
            onChainLoaded={setChainId}
          />
        </div>
      </div>
    </div>
  );
}
