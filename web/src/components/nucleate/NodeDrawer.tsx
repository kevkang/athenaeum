import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { annotationsApi } from '../../api/annotations';
import {
  nucleateApi,
  SEMANTIC_RELATIONS,
  type CanvasNode,
  type Nucleus,
  type RelationProposal,
  type ReviewUpdate,
  type SemanticEdge,
  type SemanticRelation,
} from '../../api/nucleate';
import { nodeLabel } from './labels';
import { isUnreviewed, OUTCOME_COPY, RELATION_HINTS, RELATION_LABELS } from './relations';
import RelationControls from './RelationControls';
import SupplyPaper from './SupplyPaper';
import styles from './NodeDrawer.module.css';

const FULLTEXT_COPY = {
  available: 'Full text available',
  abstract_only: 'Abstract only — weigh abstract-derived claims accordingly',
  user_must_supply: 'User must supply this paper',
} as const;

const STATUS_COPY = {
  frontier: 'Frontier',
  accepted: 'Accepted',
  pinned: 'Pinned',
  rejected: 'Rejected',
} as const;

/**
 * Everything about one node, opened on selection so the prompt stays visible
 * while inspecting (handoff Part IV). First, why the archivist chose it, in
 * its own (AI) voice, so you can judge relevance yourself (D-N22: no verdict
 * of the archivist's own). Then supplying its text if it has none, the review
 * actions and a way into Read (D-N23), its metadata and verbatim evidence,
 * its relations, and the nuclei it's in.
 */
export default function NodeDrawer({
  node,
  nodes,
  edges,
  projectId,
  nucleusId,
  nuclei,
  onClose,
  onChanged,
  onExpandFrom,
  onOpenNucleus,
  onOpenEdge,
}: {
  node: CanvasNode;
  /** Every node in the nucleus, to name the other end of a relation. */
  nodes: CanvasNode[];
  /** Every relation between papers in the nucleus. */
  edges: SemanticEdge[];
  projectId: string;
  nucleusId: string;
  nuclei: Nucleus[];
  onClose: () => void;
  onChanged: () => void;
  onExpandFrom: () => void;
  onOpenNucleus: (id: string) => void;
  onOpenEdge: (edgeId: string) => void;
}) {
  const { review, paper } = node;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');

  const act = useMutation({
    mutationFn: (update: ReviewUpdate) => nucleateApi.review(review.id, update),
    onSuccess: () => {
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
  const busy = act.isPending;

  // Opening a paper in Read puts it in the library, so it's listed there (D-N23).
  const openInRead = useMutation({
    mutationFn: () => nucleateApi.addToLibrary(nucleusId, node.node_id),
    onSuccess: () => navigate({
      to: '/projects/$projectId/read', params: { projectId }, search: { paper: paper.id },
    }),
  });
  const supply = (prominent: boolean) => (
    <SupplyPaper
      node={node}
      projectId={projectId}
      nucleusId={nucleusId}
      prominent={prominent}
      onSupplied={onChanged}
    />
  );

  return (
    <aside className={styles.drawer} aria-label={`Details for ${nodeLabel(node)}`}>
      <header className={styles.head}>
        <div className={styles.label}>{nodeLabel(node)}</div>
        <button className={styles.close} onClick={onClose} aria-label="Close">×</button>
      </header>

      <section className={styles.why}>
        <div className={styles.sectionLabel}>Why the archivist chose this</div>
        {review.selection_reasoning ? (
          <p className={styles.aiText}>{review.selection_reasoning}</p>
        ) : (
          <p className={styles.faint}>No reason was recorded for this paper.</p>
        )}
      </section>

      {node.fulltext === 'user_must_supply' && supply(true)}

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
        <div className={styles.inline}>
          <button
            className={styles.btnQuiet}
            disabled={openInRead.isPending}
            onClick={() => openInRead.mutate()}
          >
            Open in Read
          </button>
        </div>
        {openInRead.isError && <div className={styles.error}>Could not open it in Read.</div>}
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

      <section className={styles.meta}>
        <h2 className={styles.title}>{paper.title}</h2>
        {paper.authors.length > 0 && <div>{paper.authors.join(', ')}</div>}
        <div>{[paper.venue, paper.year].filter(Boolean).join(' · ')}</div>
        {paper.doi && (
          <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">doi:{paper.doi}</a>
        )}
        <div className={styles.fulltext} data-state={node.fulltext}>{FULLTEXT_COPY[node.fulltext]}</div>
      </section>

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

      {node.fulltext === 'abstract_only' && supply(false)}

      <Relations
        node={node}
        nodes={nodes}
        edges={edges}
        nucleusId={nucleusId}
        onChanged={onChanged}
        onOpenEdge={onOpenEdge}
      />

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

/**
 * This paper's relations to others in the nucleus, both ways (D-N21), with
 * the same review actions as the relation drawer. "Propose relations" asks
 * the archivist to read where this paper cites the others (D-N19); the
 * result names every other paper, so an empty result still says why.
 */
function Relations({
  node,
  nodes,
  edges,
  nucleusId,
  onChanged,
  onOpenEdge,
}: {
  node: CanvasNode;
  nodes: CanvasNode[];
  edges: SemanticEdge[];
  nucleusId: string;
  onChanged: () => void;
  onOpenEdge: (edgeId: string) => void;
}) {
  const [report, setReport] = useState<RelationProposal | null>(null);
  const [adding, setAdding] = useState(false);
  const [relation, setRelation] = useState<SemanticRelation>('uses_method_of');
  const [target, setTarget] = useState('');

  const byId = new Map(nodes.map((n) => [n.node_id, n]));
  const mine = edges.filter((e) => e.from_node_id === node.node_id || e.to_node_id === node.node_id);
  const shown = mine.filter((e) => !e.rejected);
  const rejected = mine.filter((e) => e.rejected);
  const others = nodes.filter((n) => n.node_id !== node.node_id && n.review.status !== 'rejected');

  const propose = useMutation({
    mutationFn: () => nucleateApi.proposeRelations(nucleusId, node.node_id),
    onSuccess: (r) => {
      setReport(r);
      onChanged();
    },
  });
  const add = useMutation({
    mutationFn: () => nucleateApi.addRelation(nucleusId, node.node_id, target, relation),
    onSuccess: () => {
      setAdding(false);
      setTarget('');
      onChanged();
    },
  });

  const label = (id: string) => {
    const n = byId.get(id);
    return n ? nodeLabel(n) : 'a paper';
  };
  const row = (e: SemanticEdge) => {
    const outgoing = e.from_node_id === node.node_id;
    const verb = (
      <span className={isUnreviewed(e) ? styles.relationAi : styles.relationHuman}>
        {RELATION_LABELS[e.relation]}
      </span>
    );
    return (
      <li key={e.id} className={styles.relationRow}>
        <button className={styles.relationText} onClick={() => onOpenEdge(e.id)}>
          {outgoing
            ? <>This paper {verb} {label(e.to_node_id)}</>
            : <>{label(e.from_node_id)} {verb} this paper</>}
        </button>
        <RelationControls edge={e} onChanged={onChanged} />
      </li>
    );
  };

  const errorOf = (err: unknown) =>
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;

  return (
    <section className={styles.section}>
      <div className={styles.sectionLabel}>Relations ({shown.length})</div>
      {shown.length === 0 && <p className={styles.faint}>No relations to other papers here yet.</p>}
      <ul className={styles.relationList}>{shown.map(row)}</ul>
      {rejected.length > 0 && (
        <details>
          <summary className={styles.faint}>{rejected.length} rejected</summary>
          <ul className={styles.relationList}>{rejected.map(row)}</ul>
        </details>
      )}

      <div className={styles.inline}>
        <button
          className={styles.btn}
          disabled={propose.isPending || others.length === 0 || node.fulltext !== 'available'}
          title={node.fulltext !== 'available'
            ? 'Relations are read from where this paper cites the others, which needs its full text.'
            : undefined}
          onClick={() => propose.mutate()}
        >
          {propose.isPending ? 'Reading…' : 'Propose relations from this paper'}
        </button>
        <button className={styles.btnQuiet} disabled={others.length === 0} onClick={() => setAdding((a) => !a)}>
          Add relation…
        </button>
      </div>
      {node.fulltext !== 'available' && (
        <div className={styles.faint}>Proposing needs this paper’s full text.</div>
      )}
      {propose.isError && (
        <div className={styles.error}>{errorOf(propose.error) ?? 'Could not propose relations.'}</div>
      )}
      {report && (
        <ul className={styles.report} aria-label="What proposing found">
          {report.pairs.map((p) => (
            <li key={p.to_node_id}>
              {label(p.to_node_id)} — {p.outcome === 'proposed' && p.proposed > 1
                ? `${p.proposed} relations proposed` : OUTCOME_COPY[p.outcome]}
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form
          className={styles.addRelation}
          onSubmit={(e) => {
            e.preventDefault();
            if (target) add.mutate();
          }}
        >
          <span>This paper</span>
          <select
            className={styles.input}
            value={relation}
            onChange={(e) => setRelation(e.target.value as SemanticRelation)}
            title={RELATION_HINTS[relation]}
          >
            {SEMANTIC_RELATIONS.map((r) => <option key={r} value={r}>{RELATION_LABELS[r]}</option>)}
          </select>
          <select className={styles.input} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose a paper…</option>
            {others.map((n) => <option key={n.node_id} value={n.node_id}>{nodeLabel(n)}</option>)}
          </select>
          <div className={styles.faint}>{RELATION_HINTS[relation]}</div>
          <div className={styles.inline}>
            <button type="submit" className={styles.btn} disabled={!target || add.isPending}>Add</button>
            <button type="button" className={styles.btnQuiet} onClick={() => setAdding(false)}>Cancel</button>
          </div>
          {add.isError && <div className={styles.error}>{errorOf(add.error) ?? 'Could not add that relation.'}</div>}
        </form>
      )}
    </section>
  );
}
