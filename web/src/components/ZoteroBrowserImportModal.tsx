import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { papersApi } from '../api/papers';
import {
  zoteroBrowserImportApi,
  type ZoteroBrowserImportItem,
} from '../api/zoteroBrowserImport';
import type { ZoteroCollection } from '../api/zotero';
import {
  isDirectoryPickerSupported,
  isPickerCancelled,
  pickZoteroFolder,
  type ZoteroFolderHandle,
} from '../lib/zoteroFolderAccess';
import styles from './ZoteroBrowserImportModal.module.css';

type Phase = 'idle' | 'parsing' | 'review' | 'importing' | 'done';
type PdfStatus = 'checking' | 'found' | 'missing' | 'n/a';

interface ImportRow {
  item: ZoteroBrowserImportItem;
  selected: boolean;
  pdfStatus: PdfStatus;
}

interface ImportOutcome {
  item_key: string;
  title: string;
  status: 'done' | 'error';
  error?: string;
}

const PDF_CHECK_CONCURRENCY = 6;
const IMPORT_CONCURRENCY = 3;

async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const item = items[next++];
      await fn(item);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
}

function extractErrorMessage(err: unknown, fallback: string): string {
  const maybeAxios = err as { response?: { data?: { detail?: string } }; code?: string };
  if (maybeAxios?.response?.data?.detail) return maybeAxios.response.data.detail;
  // A request that got no response at all (blocked, reset, or rejected by a
  // proxy before it reached the app — which also means it never picked up
  // CORS headers) shows up as axios's generic "Network Error", which for a
  // multi-hundred-MB zotero.sqlite upload usually just means the file hit a
  // size limit somewhere between the browser and the app.
  if (maybeAxios?.code === 'ERR_NETWORK') {
    return 'Could not reach the server — if your Zotero library is large, its ' +
      'zotero.sqlite (which includes the full-text search index) may be too ' +
      'large to upload. Try again, or ask whoever runs this deployment to ' +
      'check the upload size limit.';
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

function isImportable(item: ZoteroBrowserImportItem): boolean {
  return item.attachment_relative_path !== null || item.doi !== null;
}

export default function ZoteroBrowserImportModal({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();

  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [folderHandle, setFolderHandle] = useState<ZoteroFolderHandle | null>(null);
  const [collections, setCollections] = useState<ZoteroCollection[]>([]);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [collectionFilter, setCollectionFilter] = useState<string>('');
  const [importProgress, setImportProgress] = useState({ done: 0, total: 0 });
  const [outcomes, setOutcomes] = useState<ImportOutcome[]>([]);

  function updateRow(itemKey: string, patch: Partial<ImportRow>) {
    setRows((prev) => prev.map((r) => (r.item.item_key === itemKey ? { ...r, ...patch } : r)));
  }

  async function checkPdfAvailability(handle: ZoteroFolderHandle, initialRows: ImportRow[]) {
    const withAttachment = initialRows.filter((r) => r.item.attachment_relative_path !== null);
    await runWithConcurrency(withAttachment, PDF_CHECK_CONCURRENCY, async (row) => {
      const path = row.item.attachment_relative_path!;
      const file = await handle.getAttachmentFile(path).catch(() => null);
      updateRow(row.item.item_key, { pdfStatus: file ? 'found' : 'missing' });
    });
  }

  async function handlePick() {
    setError(null);
    try {
      const handle = await pickZoteroFolder();
      setFolderHandle(handle);
      setPhase('parsing');
      const parsed = await zoteroBrowserImportApi.parse(handle.sqliteFile, projectId);
      setCollections(parsed.collections);
      const initialRows: ImportRow[] = parsed.items.map((item) => ({
        item,
        selected: !item.already_in_project && isImportable(item),
        pdfStatus: item.attachment_relative_path ? 'checking' : 'n/a',
      }));
      setRows(initialRows);
      setPhase('review');
      void checkPdfAvailability(handle, initialRows);
    } catch (err) {
      if (isPickerCancelled(err)) return;
      setError(extractErrorMessage(err, 'Could not read that folder.'));
      setPhase('idle');
    }
  }

  const filteredRows = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return rows.filter((r) => {
      if (collectionFilter && !r.item.collection_keys.includes(collectionFilter)) return false;
      if (!q) return true;
      const haystack = `${r.item.title} ${r.item.creators.join(' ')}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [rows, searchQuery, collectionFilter]);

  const selectedCount = rows.filter((r) => r.selected).length;

  function selectVisible(select: boolean) {
    const visibleKeys = new Set(
      filteredRows.filter((r) => isImportable(r.item)).map((r) => r.item.item_key),
    );
    setRows((prev) =>
      prev.map((r) => (visibleKeys.has(r.item.item_key) ? { ...r, selected: select } : r)),
    );
  }

  async function importSelected(selected: ImportRow[]) {
    setPhase('importing');
    setImportProgress({ done: 0, total: selected.length });
    const results: ImportOutcome[] = [];

    await runWithConcurrency(selected, IMPORT_CONCURRENCY, async (row) => {
      try {
        if (row.item.attachment_relative_path && row.pdfStatus === 'found' && folderHandle) {
          const file = await folderHandle.getAttachmentFile(row.item.attachment_relative_path);
          if (!file) throw new Error('PDF file went missing during import');
          await papersApi.upload(file, projectId, {
            title: row.item.title,
            doi: row.item.doi ?? undefined,
          });
        } else if (row.item.doi) {
          await papersApi.ingest(row.item.doi, projectId);
        } else {
          throw new Error('No PDF or DOI to import');
        }
        results.push({ item_key: row.item.item_key, title: row.item.title, status: 'done' });
      } catch (err) {
        results.push({
          item_key: row.item.item_key,
          title: row.item.title,
          status: 'error',
          error: extractErrorMessage(err, 'Import failed'),
        });
      }
      setImportProgress((p) => ({ ...p, done: p.done + 1 }));
    });

    setOutcomes(results);
    setPhase('done');
    queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
  }

  function handleImportClick() {
    void importSelected(rows.filter((r) => r.selected));
  }

  function handleRetryFailed() {
    const failedKeys = new Set(outcomes.filter((o) => o.status === 'error').map((o) => o.item_key));
    void importSelected(rows.filter((r) => failedKeys.has(r.item.item_key)));
  }

  const failedCount = outcomes.filter((o) => o.status === 'error').length;
  const doneCount = outcomes.filter((o) => o.status === 'done').length;

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>Import from Zotero folder</div>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>

        {phase === 'idle' && (
          <div className={styles.pickerStep}>
            <div className={styles.pickerHint}>
              Point Athenaeum at your Zotero data directory (Zotero: Preferences → Advanced →
              &ldquo;Show Data Directory&rdquo;). Everything is read in your browser — only item
              metadata and the PDFs you choose to import ever leave your machine.
            </div>
            <button className={styles.pickerBtn} onClick={() => void handlePick()}>
              Choose Zotero Folder…
            </button>
            {error && <div className={styles.pickerError}>{error}</div>}
            {!isDirectoryPickerSupported() && (
              <div className={styles.pickerNote}>
                Your browser doesn&rsquo;t support persistent folder access — you&rsquo;ll need to
                re-select the folder each time you import (nothing is saved between visits).
              </div>
            )}
          </div>
        )}

        {phase === 'parsing' && (
          <div className={styles.progressStep}>
            <div className={styles.progressLabel}>Reading zotero.sqlite…</div>
          </div>
        )}

        {phase === 'review' && (
          <>
            <div className={styles.toolbar}>
              <select
                className={styles.collectionSelect}
                value={collectionFilter}
                onChange={(e) => setCollectionFilter(e.target.value)}
              >
                <option value="">All collections</option>
                {collections.map((c) => (
                  <option key={c.key} value={c.key}>{c.name}</option>
                ))}
              </select>
              <input
                className={styles.filterInput}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by title or author…"
              />
              <button className={styles.selectLink} onClick={() => selectVisible(true)}>Select all</button>
              <button className={styles.selectLink} onClick={() => selectVisible(false)}>Select none</button>
            </div>

            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.th} />
                    <th className={styles.th}>Title</th>
                    <th className={styles.th}>Authors</th>
                    <th className={styles.th}>Year</th>
                    <th className={styles.th}>PDF</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((row) => {
                    const importable = isImportable(row.item);
                    return (
                      <tr
                        key={row.item.item_key}
                        className={`${styles.row} ${row.item.already_in_project ? styles.rowDupe : ''}`}
                      >
                        <td>
                          <input
                            type="checkbox"
                            checked={row.selected}
                            disabled={!importable}
                            title={!importable ? 'No PDF or DOI found — nothing to import' : undefined}
                            onChange={(e) => updateRow(row.item.item_key, { selected: e.target.checked })}
                          />
                        </td>
                        <td className={styles.rowTitle}>
                          {row.item.title}
                          {row.item.already_in_project && (
                            <span className={styles.rowBadge}>already in project</span>
                          )}
                        </td>
                        <td className={styles.rowCreators}>{row.item.creators.join(', ') || '—'}</td>
                        <td className={styles.rowMuted}>{row.item.year ?? '—'}</td>
                        <td className={styles.rowMuted}>
                          {row.pdfStatus === 'checking' && <span className={styles.pdfPending}>…</span>}
                          {row.pdfStatus === 'found' && <span className={styles.pdfYes}>✅</span>}
                          {row.pdfStatus === 'missing' && <span className={styles.pdfNo}>❌</span>}
                          {row.pdfStatus === 'n/a' && <span className={styles.pdfNo}>—</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {filteredRows.length === 0 && (
                <div className={styles.emptyHint}>No items match that filter.</div>
              )}
            </div>

            <div className={styles.footer}>
              <span>{rows.length} items found · {selectedCount} selected</span>
              <button
                className={styles.importBtn}
                disabled={selectedCount === 0}
                onClick={handleImportClick}
              >
                Import {selectedCount} paper{selectedCount === 1 ? '' : 's'}
              </button>
            </div>
          </>
        )}

        {phase === 'importing' && (
          <div className={styles.progressStep}>
            <div className={styles.progressLabel}>
              Importing… {importProgress.done} / {importProgress.total}
            </div>
            <div className={styles.progressBarTrack}>
              <div
                className={styles.progressBarFill}
                style={{
                  width: `${importProgress.total ? (importProgress.done / importProgress.total) * 100 : 0}%`,
                }}
              />
            </div>
          </div>
        )}

        {phase === 'done' && (
          <div className={styles.doneStep}>
            <div className={styles.doneSummary}>
              Imported {doneCount} paper{doneCount === 1 ? '' : 's'}
              {failedCount > 0 && `, ${failedCount} failed`}.
            </div>
            {failedCount > 0 && (
              <>
                <div className={styles.errorList}>
                  {outcomes.filter((o) => o.status === 'error').map((o) => (
                    <div key={o.item_key} className={styles.errorRow}>
                      {o.title}: {o.error}
                    </div>
                  ))}
                </div>
                <button className={styles.doneBtn} onClick={handleRetryFailed}>Retry failed</button>
              </>
            )}
            <button className={styles.doneBtn} onClick={onClose}>Done</button>
          </div>
        )}
      </div>
    </div>
  );
}
