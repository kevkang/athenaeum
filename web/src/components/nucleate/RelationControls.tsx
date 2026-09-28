import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  nucleateApi,
  SEMANTIC_RELATIONS,
  type EdgeUpdate,
  type SemanticEdge,
  type SemanticRelation,
} from '../../api/nucleate';
import { RELATION_HINTS, RELATION_LABELS } from './relations';
import styles from './NodeDrawer.module.css';

/**
 * The review actions for one relation (D-N21), the same wherever it's shown:
 * Accept an archivist's proposal, Change it to another relation, Reject it
 * (kept, so it isn't proposed again), or Restore a rejected one. A relation
 * you set yourself can be changed or deleted instead.
 */
export default function RelationControls({
  edge,
  onChanged,
}: {
  edge: SemanticEdge;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<'idle' | 'changing' | 'rejecting'>('idle');
  const [reason, setReason] = useState('');
  const mine = edge.provenance === 'user_set';

  const act = useMutation({
    mutationFn: (update: EdgeUpdate) => nucleateApi.updateRelation(edge.id, update),
    onSuccess: () => {
      setMode('idle');
      setReason('');
      onChanged();
    },
  });
  const remove = useMutation({
    mutationFn: () => nucleateApi.deleteRelation(edge.id),
    onSuccess: onChanged,
  });
  const busy = act.isPending || remove.isPending;
  const failed = act.error ?? remove.error;

  if (edge.rejected) {
    return (
      <div className={styles.inline}>
        <button className={styles.btnQuiet} disabled={busy} onClick={() => act.mutate({ action: 'restore' })}>
          Restore
        </button>
      </div>
    );
  }

  return (
    <>
      {mode === 'idle' && (
        <div className={styles.inline}>
          {edge.provenance === 'ai_proposed' && (
            <button className={styles.btn} disabled={busy} onClick={() => act.mutate({ action: 'accept' })}>
              Accept
            </button>
          )}
          <button className={styles.btnQuiet} disabled={busy} onClick={() => setMode('changing')}>
            Change
          </button>
          {mine ? (
            <button className={styles.btnQuiet} disabled={busy} onClick={() => remove.mutate()}>
              Delete
            </button>
          ) : (
            <button className={styles.btnQuiet} disabled={busy} onClick={() => setMode('rejecting')}>
              Reject
            </button>
          )}
        </div>
      )}
      {mode === 'changing' && (
        <div className={styles.relationPicker} role="group" aria-label="Change relation">
          {SEMANTIC_RELATIONS.map((r: SemanticRelation) => (
            <button
              key={r}
              className={styles.relationOption}
              data-current={r === edge.relation}
              disabled={busy || r === edge.relation}
              title={RELATION_HINTS[r]}
              onClick={() => act.mutate({ action: 'correct', relation: r })}
            >
              {RELATION_LABELS[r]}
            </button>
          ))}
          <button className={styles.btnQuiet} onClick={() => setMode('idle')}>Cancel</button>
        </div>
      )}
      {mode === 'rejecting' && (
        <form
          className={styles.inline}
          onSubmit={(e) => {
            e.preventDefault();
            act.mutate({ action: 'reject', ...(reason.trim() ? { rejection_reason: reason.trim() } : {}) });
          }}
        >
          <input
            className={styles.input}
            value={reason}
            maxLength={500}
            placeholder="Reason (optional, one line)"
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
          <button type="submit" className={styles.btn} disabled={busy}>Reject</button>
          <button type="button" className={styles.btnQuiet} onClick={() => setMode('idle')}>Cancel</button>
        </form>
      )}
      {failed && (
        <div className={styles.error}>
          {(failed as { response?: { data?: { detail?: string } } }).response?.data?.detail
            ?? 'Could not save that change.'}
        </div>
      )}
    </>
  );
}
