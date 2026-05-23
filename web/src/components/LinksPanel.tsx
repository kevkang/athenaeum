import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { linksApi, type RefType, type RelType, REL_LABELS } from '../api/links';
import styles from './LinksPanel.module.css';

const REL_OPTIONS: RelType[] = ['supports', 'contradicts', 'extends', 'cites', 'related'];

interface Props {
  projectId: string;
  refType: RefType;
  refId: string;
}

export function LinksPanel({ projectId, refType, refId }: Props) {
  const queryClient = useQueryClient();
  const qKey = ['links', projectId, refType, refId];

  const { data: links = [] } = useQuery({
    queryKey: qKey,
    queryFn: () => linksApi.listForRef(projectId, refType, refId),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => linksApi.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qKey }),
  });

  const [adding, setAdding] = useState(false);
  const [addQuery, setAddQuery] = useState('');
  const [addRef, setAddRef] = useState<{ id: string; ref_type: string; title: string } | null>(null);
  const [addRel, setAddRel] = useState<RelType>('related');

  const { data: searchResults = [] } = useQuery({
    queryKey: ['refs', projectId, addQuery],
    queryFn: () => linksApi.searchRefs(projectId, addQuery),
    enabled: adding && addRef === null,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      linksApi.create({
        project_id: projectId,
        from_type: refType,
        from_id: refId,
        to_type: addRef!.ref_type as RefType,
        to_id: addRef!.id,
        rel: addRel,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qKey });
      setAdding(false);
      setAddQuery('');
      setAddRef(null);
      setAddRel('related');
    },
  });

  // Group links by direction
  const outgoing = links.filter((l) => l.from_id === refId);
  const incoming = links.filter((l) => l.to_id === refId);

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.headerTitle}>Links{links.length > 0 ? ` · ${links.length}` : ''}</span>
        {!adding && (
          <button className={styles.addBtn} onClick={() => setAdding(true)}>
            + Add link
          </button>
        )}
      </div>

      {/* Add link form */}
      {adding && (
        <div className={styles.addForm}>
          {!addRef ? (
            <>
              <input
                className={styles.searchInput}
                value={addQuery}
                onChange={(e) => setAddQuery(e.target.value)}
                placeholder="Search papers and syntheses…"
                autoFocus
              />
              {searchResults.map((ref) => (
                <button
                  key={`${ref.ref_type}:${ref.id}`}
                  className={styles.searchRow}
                  onClick={() => setAddRef(ref)}
                >
                  <span className={`${styles.badge} ${styles[ref.ref_type]}`}>
                    {ref.ref_type === 'paper' ? 'P' : 'S'}
                  </span>
                  <span className={styles.searchTitle}>{ref.title}</span>
                  {ref.subtitle && <span className={styles.searchSub}>{ref.subtitle}</span>}
                </button>
              ))}
              <button className={styles.cancelSmall} onClick={() => setAdding(false)}>Cancel</button>
            </>
          ) : (
            <>
              <div className={styles.selectedRef}>
                <span className={`${styles.badge} ${styles[addRef.ref_type]}`}>
                  {addRef.ref_type === 'paper' ? 'P' : 'S'}
                </span>
                <span className={styles.selectedTitle}>{addRef.title}</span>
                <button className={styles.clearRef} onClick={() => setAddRef(null)}>✕</button>
              </div>
              <div className={styles.relRow}>
                {REL_OPTIONS.map((rel) => (
                  <button
                    key={rel}
                    className={`${styles.relChip} ${addRel === rel ? styles.relChipActive : ''}`}
                    onClick={() => setAddRel(rel)}
                  >
                    {REL_LABELS[rel]}
                  </button>
                ))}
              </div>
              <div className={styles.formActions}>
                <button className={styles.cancelSmall} onClick={() => { setAdding(false); setAddRef(null); }}>
                  Cancel
                </button>
                <button
                  className={styles.saveBtn}
                  onClick={() => createMutation.mutate()}
                  disabled={createMutation.isPending}
                >
                  {createMutation.isPending ? 'Saving…' : 'Save link'}
                </button>
              </div>
              {createMutation.error && (
                <div className={styles.error}>{(createMutation.error as Error).message}</div>
              )}
            </>
          )}
        </div>
      )}

      {/* Outgoing links */}
      {outgoing.length > 0 && (
        <div className={styles.group}>
          <div className={styles.groupLabel}>This {refType}</div>
          {outgoing.map((l) => (
            <div key={l.id} className={styles.linkRow}>
              <span className={`${styles.relTag} ${styles[l.rel]}`}>{l.rel}</span>
              <span className={`${styles.badge} ${styles[l.to_type]}`}>
                {l.to_type === 'paper' ? 'P' : 'S'}
              </span>
              <span className={styles.linkTitle}>{l.to_title}</span>
              <button
                className={styles.deleteLink}
                onClick={() => deleteMutation.mutate(l.id)}
                disabled={deleteMutation.isPending}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Incoming links */}
      {incoming.length > 0 && (
        <div className={styles.group}>
          <div className={styles.groupLabel}>Referenced by</div>
          {incoming.map((l) => (
            <div key={l.id} className={styles.linkRow}>
              <span className={`${styles.badge} ${styles[l.from_type]}`}>
                {l.from_type === 'paper' ? 'P' : 'S'}
              </span>
              <span className={styles.linkTitle}>{l.from_title}</span>
              <span className={`${styles.relTag} ${styles[l.rel]}`}>{l.rel}</span>
              <span className={styles.arrow}>this</span>
              <button
                className={styles.deleteLink}
                onClick={() => deleteMutation.mutate(l.id)}
                disabled={deleteMutation.isPending}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {links.length === 0 && !adding && (
        <div className={styles.empty}>No links yet — type [[ in the text above or click Add link.</div>
      )}
    </div>
  );
}
