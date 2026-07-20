import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { zoteroLocalApi } from '../api/zoteroLocal';
import styles from './LocalLibraryPicker.module.css';

// Browsers never expose a real filesystem path from a native folder picker
// (a website could otherwise learn a user's local file layout) — this is the
// closest equivalent: the backend lists subfolders of the mounted home
// directory, and you click through them instead of typing a raw path.
export default function LocalLibraryPicker({
  onClose,
  onSelect,
  isSelecting,
  selectError,
}: {
  onClose: () => void;
  onSelect: (relativePath: string) => void;
  isSelecting: boolean;
  selectError?: string;
}) {
  const [path, setPath] = useState('');

  const { data, isFetching, error } = useQuery({
    queryKey: ['zotero-local-browse-dirs', path],
    queryFn: () => zoteroLocalApi.browseDirs(path),
  });

  const crumbs = path ? path.split('/') : [];

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>Select local Zotero library</div>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>

        <div className={styles.breadcrumbs}>
          <button className={styles.crumbBtn} onClick={() => setPath('')}>~</button>
          {crumbs.map((seg, i) => (
            <span key={i}>
              {' / '}
              <button
                className={styles.crumbBtn}
                onClick={() => setPath(crumbs.slice(0, i + 1).join('/'))}
              >
                {seg}
              </button>
            </span>
          ))}
        </div>

        {error && (
          <div className={styles.errorHint}>
            {String(
              (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
                'Could not list that folder.',
            )}
          </div>
        )}

        {!error && isFetching && <div className={styles.loadingHint}>Loading…</div>}

        {!error && data && (
          <div className={styles.dirList}>
            {data.parent !== null && (
              <button className={styles.dirItem} onClick={() => setPath(data.parent!)}>
                <span className={styles.dirName}>.. (up)</span>
              </button>
            )}
            {data.entries.map((entry) => (
              <button
                key={entry.name}
                className={styles.dirItem}
                onClick={() => setPath(path ? `${path}/${entry.name}` : entry.name)}
              >
                <span className={styles.dirName}>📁 {entry.name}</span>
                {entry.has_zotero_sqlite && (
                  <span className={styles.zoteroBadgeSmall}>zotero.sqlite here</span>
                )}
              </button>
            ))}
            {data.entries.length === 0 && (
              <div className={styles.emptyHint}>No subfolders here.</div>
            )}
          </div>
        )}

        <div className={styles.footer}>
          {selectError && <div className={styles.errorHint} style={{ padding: 0, marginRight: 'auto' }}>{selectError}</div>}
          <button
            className={styles.selectBtn}
            disabled={isSelecting}
            onClick={() => onSelect(path)}
          >
            {isSelecting ? 'Checking…' : `Select "${path || '~'}"`}
          </button>
        </div>
      </div>
    </div>
  );
}
