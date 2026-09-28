import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { annotationsApi } from '../../api/annotations';
import { nucleateApi, type CanvasNode, type Nucleus, type ReviewUpdate } from '../../api/nucleate';
import { nodeLabel } from './labels';
import styles from './NodeDrawer.module.css';

const FULLTEXT_COPY = {
  available: 'Full text available',
  abstract_only: 'Abstract only — weigh abstract-derived claims accordingly',
  user_must_supply: 'User must supply this paper',
} as const;

const PROVENANCE_COPY = {
  ai_proposed: 'Written by the archivist · not yet reviewed',
  ai_proposed_accepted: 'Written by the archivist · accepted by you',
  ai_proposed_corrected: 'Written by the archivist · edited by you',
  user_set: 'Written by you',
} as const;

const STATUS_COPY = {
  frontier: 'Frontier',
  accepted: 'Accepted',
  pinned: 'Pinned',
  rejected: 'Rejected',
} as const;

/**
 * Everything about one node, opened on selection so the prompt stays visible
 * while inspecting (handoff Part IV): metadata, the relevance statement with
 * its provenance and automatic-audit result, why it was selected, all its
 * verbatim evidence in context, the review actions, and the nuclei it's in.
 */
export default function NodeDrawer({
  node,
  projectId,
  nucleusId,
  nuclei,
  onClose,
  onChanged,
  onExpandFrom,
  onOpenNucleus,
}: {
  node: CanvasNode;
  projectId: string;
  nucleusId: string;
  nuclei: Nucleus[];
  onClose: () => void;
  onChanged: () => void;
  onExpandFrom: () => void;
  onOpenNucleus: (id: string) => void;
}) {
  const { review, paper } = node;
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(review.relevance_text ?? '');
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  const act = useMutation({
    mutationFn: (update: ReviewUpdate) => nucleateApi.review(review.id, update),
    onSuccess: () => {
      setEditing(false);
      setRejecting(false);
      setReason('');
      onChanged();
    },
  });

  const notesKey = ['annotations', projectId, paper.id];
  const { data: notes = [] } = useQuery({
    queryKey: notesKey,
    queryFn: () => annotationsApi.forPaper(projectId, paper.id),
  });
  const addNote = useMutation({
    mutationFn: (body: string) => annotationsApi.addToPaper(projectId, paper.id, body),
    onSuccess: () => {
      setNote('');
      void queryClient.invalidateQueries({ queryKey: notesKey });
    },
  });

  const questionOf = new Map(nuclei.map((n) => [n.id, n.question]));
  const ai = review.relevance_provenance === 'ai_proposed';
  const busy = act.isPending;

  return (
    <aside className={styles.drawer} aria-label={`Details for ${nodeLabel(node)}`}>
      <header className={styles.head}>
        <div>
          <div className={styles.label}>{nodeLabel(node)}</div>
          <h2 className={styles.title}>{paper.title}</h2>
        </div>
        <button className={styles.close} onClick={onClose} aria-label="Close">×</button>
      </header>

      <section className={styles.meta}>
        {paper.authors.length > 0 && <div>{paper.authors.join(', ')}</div>}
        <div>{[paper.venue, paper.year].filter(Boolean).join(' · ')}</div>
        {paper.doi && (
          <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">doi:{paper.doi}</a>
        )}
        <div className={styles.fulltext} data-state={node.fulltext}>{FULLTEXT_COPY[node.fulltext]}</div>
      </section>

      <section className={styles.section}>
        <div className={styles.statusRow}>
          <span className={styles.status} data-status={review.status}>{STATUS_COPY[review.status]}</span>
          {review.status === 'rejected' ? (
            <button className={styles.btn} disabled={busy} onClick={() => act.mutate({ status: 'frontier' })}>
              Restore
            </button>
          ) : (
            <>
              {review.status !== 'accepted' && review.status !== 'pinned' && (
                <button className={styles.btn} disabled={busy} onClick={() => act.mutate({ status: 'accepted' })}>
                  Accept
                </button>
              )}
              <button
                className={styles.btn}
                disabled={busy}
                onClick={() => act.mutate({ status: review.status === 'pinned' ? 'accepted' : 'pinned' })}
              >
                {review.status === 'pinned' ? 'Unpin' : 'Pin'}
              </button>
              <button className={styles.btnQuiet} disabled={busy} onClick={() => setRejecting(true)}>
                Reject
              </button>
            </>
          )}
        </div>
        {review.status === 'rejected' && review.rejection_reason && (
          <div className={styles.reason}>Reason: {review.rejection_reason}</div>
        )}
        {rejecting && (
          <form
            className={styles.inline}
            onSubmit={(e) => {
              e.preventDefault();
              act.mutate({ status: 'rejected', ...(reason.trim() ? { rejection_reason: reason.trim() } : {}) });
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
            <button type="button" className={styles.btnQuiet} onClick={() => setRejecting(false)}>Cancel</button>
          </form>
        )}
        {act.isError && <div className={styles.error}>Could not save that change.</div>}
      </section>

      <section className={styles.section}>
        <div className={styles.sectionLabel}>Relevance</div>
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act.mutate({ relevance_text: draft });
            }}
          >
            <textarea className={styles.textarea} rows={4} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <div className={styles.inline}>
              <button type="submit" className={styles.btn} disabled={busy}>Save</button>
              <button type="button" className={styles.btnQuiet} onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </form>
        ) : review.relevance_text ? (
          <>
            <p className={ai ? styles.aiText : styles.humanText}>{review.relevance_text}</p>
            {review.relevance_provenance && (
              <div className={styles.provenance}>{PROVENANCE_COPY[review.relevance_provenance]}</div>
            )}
          </>
        ) : (
          <p className={styles.faint}>No relevance statement.</p>
        )}
        {!editing && (
          <button
            className={styles.btnQuiet}
            onClick={() => {
              setDraft(review.relevance_text ?? '');
              setEditing(true);
            }}
          >
            {review.relevance_text ? 'Edit statement' : 'Write a statement'}
          </button>
        )}

        {node.audit ? (
          <div className={styles.audit} data-supported={node.audit.supported}>
            <strong>{node.audit.supported ? '✓ Supported by its evidence' : '✕ Not supported by its evidence'}</strong>
            <span>{node.audit.rationale}</span>
            <span className={styles.faint}>
              Automatic audit · {new Date(node.audit.checked_at).toLocaleString()}
            </span>
          </div>
        ) : review.relevance_text ? (
          <div className={styles.faint}>Not audited.</div>
        ) : null}
      </section>

      {review.selection_reasoning && (
        <details className={styles.section}>
          <summary className={styles.sectionLabel}>Why the archivist chose this</summary>
          <p className={styles.aiText}>{review.selection_reasoning}</p>
        </details>
      )}

      <section className={styles.section}>
        <div className={styles.sectionLabel}>Evidence ({node.evidence.length})</div>
        {node.evidence.length === 0 && <p className={styles.faint}>No verbatim evidence yet.</p>}
        {node.evidence.map((e) => (
          <figure key={e.id} className={styles.evidence}>
            <blockquote className={styles.quote}>
              {/* The selector's own context, so a quote is read where it sits. */}
              {e.text_quote_selector.prefix && <span className={styles.context}>…{e.text_quote_selector.prefix}</span>}
              <mark className={styles.exact}>{e.verbatim_text}</mark>
              {e.text_quote_selector.suffix && <span className={styles.context}>{e.text_quote_selector.suffix}…</span>}
            </blockquote>
            <figcaption className={styles.source}>{e.source === 'abstract' ? 'Abstract' : 'Full text'}</figcaption>
          </figure>
        ))}
      </section>

      <section className={styles.section}>
        <div className={styles.sectionLabel}>Nuclei</div>
        <ul className={styles.nuclei}>
          <li>{questionOf.get(nucleusId) ?? 'This nucleus'} <span className={styles.faint}>· here</span></li>
          {node.other_nucleus_ids.map((id) => (
            <li key={id}>
              <button className={styles.linkBtn} onClick={() => onOpenNucleus(id)}>
                {questionOf.get(id) ?? 'Another nucleus'}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionLabel}>Notes</div>
        {notes.map((n) => (
          <p key={n.id} className={styles.note}>{n.body}</p>
        ))}
        <form
          className={styles.inline}
          onSubmit={(e) => {
            e.preventDefault();
            if (note.trim()) addNote.mutate(note.trim());
          }}
        >
          <input className={styles.input} value={note} placeholder="Add a note…" onChange={(e) => setNote(e.target.value)} />
          <button type="submit" className={styles.btn} disabled={!note.trim() || addNote.isPending}>Add</button>
        </form>
      </section>

      <footer className={styles.foot}>
        <button className={styles.primary} onClick={onExpandFrom} disabled={review.status === 'rejected'}>
          Expand from this paper
        </button>
      </footer>
    </aside>
  );
}
