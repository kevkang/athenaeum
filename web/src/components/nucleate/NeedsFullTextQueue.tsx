import { useState } from 'react';
import { papersApi } from '../../api/papers';
import { nucleateApi, type CanvasNode } from '../../api/nucleate';
import { needsFullText, nodeLabel } from './labels';
import styles from './NucleatePanel.module.css';

/** The needs-full-text queue: see needsFullText() for what belongs in it.
 * Supplying a PDF uploads it to the existing paper, which re-ingests it, and
 * puts the paper in the library (D-N23). */
export default function NeedsFullTextQueue({
  nodes,
  nucleusId,
  onSupplied,
}: {
  nodes: CanvasNode[];
  nucleusId: string;
  onSupplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queue = needsFullText(nodes);
  if (queue.length === 0) return null;

  async function supply(node: CanvasNode, file: File) {
    setBusy(node.node_id);
    setError(null);
    try {
      await nucleateApi.addToLibrary(nucleusId, node.node_id);
      await papersApi.reupload(node.paper.id, file);
      onSupplied();
    } catch (e) {
      setError(`Could not upload for ${nodeLabel(node)}: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.queue}>
      <button className={styles.queueHeader} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>{open ? '▾' : '▸'}</span>
        <span>Needs full text</span>
        <span className={styles.queueCount}>{queue.length}</span>
      </button>
      {open && (
        <ul className={styles.queueList}>
          {queue.map((n) => (
            <li key={n.node_id} className={styles.queueItem}>
              <div className={styles.queueTitle}>{nodeLabel(n)} — {n.paper.title}</div>
              <div className={styles.queueNote}>User must supply this paper.</div>
              <div className={styles.queueMeta}>
                {n.paper.doi ? (
                  <a href={`https://doi.org/${n.paper.doi}`} target="_blank" rel="noreferrer">
                    doi:{n.paper.doi}
                  </a>
                ) : (
                  <span>No DOI</span>
                )}
                <span>
                  {n.paper.oa_checked_at
                    ? 'Unpaywall: no open-access copy'
                    : 'Unpaywall: not checked'}
                </span>
              </div>
              <label className={styles.supplyBtn}>
                {busy === n.node_id ? 'Uploading…' : 'Supply PDF'}
                <input
                  type="file"
                  accept=".pdf,.txt"
                  hidden
                  disabled={busy !== null}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void supply(n, f);
                    e.target.value = '';
                  }}
                />
              </label>
            </li>
          ))}
        </ul>
      )}
      {error && <div className={styles.errorLine}>{error}</div>}
    </div>
  );
}
