import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { zoteroHighlightsApi, type ZoteroHighlightCandidate } from '../api/zoteroHighlights';
import styles from './ZoteroHighlightImportModal.module.css';

type Phase = 'loading' | 'review' | 'importing' | 'done' | 'error';

interface Row {
  candidate: ZoteroHighlightCandidate;
  selected: boolean;
}

function isImportable(c: ZoteroHighlightCandidate): boolean {
  return c.matched && c.sentence_ids !== null && c.verbatim_text !== null;
}

function extractErrorMessage(err: unknown, fallback: string): string {
  const maybeAxios = err as { response?: { data?: { detail?: string } } };
  return maybeAxios?.response?.data?.detail ?? fallback;
}

export default function ZoteroHighlightImportModal({
  paperId,
  projectId,
  onClose,
}: {
  paperId: string;
  projectId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>('loading');
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [importedCount, setImportedCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const candidates = await zoteroHighlightsApi.preview(paperId, projectId);
        if (cancelled) return;
        setRows(
          candidates.map((candidate) => ({
            candidate,
            selected: isImportable(candidate) && !candidate.already_imported,
          })),
        );
        setPhase('review');
      } catch (err) {
        if (!cancelled) {
          setError(extractErrorMessage(err, 'Could not fetch highlights from Zotero.'));
          setPhase('error');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [paperId, projectId]);

  function toggleRow(itemKey: string) {
    setRows((prev) =>
      prev.map((r) =>
        r.candidate.annotation_item_key === itemKey ? { ...r, selected: !r.selected } : r,
      ),
    );
  }

  const selected = useMemo(() => rows.filter((r) => r.selected), [rows]);
  const matchedCount = rows.filter((r) => isImportable(r.candidate)).length;
  const unmatchedCount = rows.length - matchedCount;

  async function handleImport() {
    setPhase('importing');
    try {
      const items = selected.map((r) => ({
        sentence_ids: r.candidate.sentence_ids!,
        verbatim_text: r.candidate.verbatim_text!,
        // Identifies which Zotero annotation this row came from. The server
        // looks the annotation's page and position up itself rather than
        // trusting coordinates sent from here.
        annotation_item_key: r.candidate.annotation_item_key,
      }));
      await zoteroHighlightsApi.import(paperId, projectId, items);
      setImportedCount(items.length);
      queryClient.invalidateQueries({ queryKey: ['highlights', projectId, paperId] });
      setPhase('done');
    } catch (err) {
      setError(extractErrorMessage(err, 'Import failed.'));
      setPhase('error');
    }
  }

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>Import Zotero highlights</div>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>

        {phase === 'loading' && (
          <div className={styles.centerStep}>
            <div className={styles.centerLabel}>Reading annotations from Zotero…</div>
          </div>
        )}

        {phase === 'error' && (
          <div className={styles.centerStep}>
            <div className={styles.errorText}>{error}</div>
            <button className={styles.doneBtn} onClick={onClose}>Close</button>
          </div>
        )}

        {(phase === 'review' || phase === 'importing') && (
          <>
            {rows.length === 0 ? (
              <div className={styles.emptyHint}>
                No highlight or underline annotations found on this paper in Zotero.
              </div>
            ) : (
              <>
                <div className={styles.hint}>
                  {matchedCount} of {rows.length} annotation{rows.length === 1 ? '' : 's'} matched
                  against this paper&rsquo;s parsed text
                  {unmatchedCount > 0 && ` — ${unmatchedCount} couldn't be located verbatim`}.
                  Zotero&rsquo;s own PDF text extraction differs slightly from Athenaeum&rsquo;s, so
                  a few misses are expected.
                </div>
                <div className={styles.list}>
                  {rows.map((row) => {
                    const c = row.candidate;
                    const importable = isImportable(c);
                    return (
                      <label
                        key={c.annotation_item_key}
                        className={`${styles.row} ${!importable ? styles.rowDisabled : ''}`}
                      >
                        <input
                          type="checkbox"
                          checked={row.selected}
                          disabled={!importable || phase === 'importing'}
                          onChange={() => toggleRow(c.annotation_item_key)}
                        />
                        <div className={styles.rowBody}>
                          <div className={styles.zoteroText}>&ldquo;{c.zotero_text}&rdquo;</div>
                          {c.comment && <div className={styles.comment}>Note: {c.comment}</div>}
                          {importable ? (
                            <div className={styles.matchInfo}>
                              matched at {Math.round((c.match_ratio ?? 0) * 100)}% confidence
                              {c.already_imported && (
                                <span className={styles.alreadyBadge}>already imported</span>
                              )}
                            </div>
                          ) : (
                            <div className={styles.noMatch}>
                              Couldn&rsquo;t locate this text in the paper&rsquo;s parsed body.
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
                <div className={styles.footer}>
                  <span>{selected.length} selected</span>
                  <button
                    className={styles.importBtn}
                    disabled={selected.length === 0 || phase === 'importing'}
                    onClick={() => void handleImport()}
                  >
                    {phase === 'importing'
                      ? 'Importing…'
                      : `Import ${selected.length} highlight${selected.length === 1 ? '' : 's'}`}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        {phase === 'done' && (
          <div className={styles.centerStep}>
            <div className={styles.centerLabel}>
              Imported {importedCount} highlight{importedCount === 1 ? '' : 's'}.
            </div>
            <button className={styles.doneBtn} onClick={onClose}>Done</button>
          </div>
        )}
      </div>
    </div>
  );
}
