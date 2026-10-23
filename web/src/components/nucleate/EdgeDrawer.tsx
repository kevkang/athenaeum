import type { CanvasNode, SemanticEdge } from '../../api/nucleate';
import { nodeLabel } from './labels';
import { EDGE_PROVENANCE_COPY, isUnreviewed, RELATION_LABELS } from './relations';
import RelationControls from './RelationControls';
import styles from './NodeDrawer.module.css';

/**
 * One relation, opened by clicking its arrow (D-N21): A → relation → B, the
 * sentence from A that shows it, and the review actions. The quote is A's own
 * words, verbatim; the rationale is the archivist's, and reads as AI text
 * until the relation is reviewed.
 */
export default function EdgeDrawer({
  edge,
  from,
  to,
  onClose,
  onChanged,
  onOpenNode,
}: {
  edge: SemanticEdge;
  from: CanvasNode;
  to: CanvasNode;
  onClose: () => void;
  onChanged: () => void;
  onOpenNode: (nodeId: string) => void;
}) {
  const ai = isUnreviewed(edge);
  return (
    <aside className={styles.drawer} aria-label="Relation">
      <header className={styles.head}>
        <div>
          <div className={styles.label}>Relation</div>
          <h2 className={styles.title}>
            <button className={styles.titleLink} onClick={() => onOpenNode(from.node_id)}>{nodeLabel(from)}</button>
            {' '}
            <span className={ai ? styles.relationAi : styles.relationHuman}>{RELATION_LABELS[edge.relation]}</span>
            {' '}
            <button className={styles.titleLink} onClick={() => onOpenNode(to.node_id)}>{nodeLabel(to)}</button>
          </h2>
        </div>
        <button className={styles.close} onClick={onClose} aria-label="Close">×</button>
      </header>

      <section className={styles.section}>
        <div className={styles.provenance}>
          {EDGE_PROVENANCE_COPY[edge.provenance]}
          {edge.provenance === 'ai_proposed_corrected' && edge.proposed_relation && (
            <> · proposed as “{RELATION_LABELS[edge.proposed_relation]}”</>
          )}
        </div>
        {edge.rejected && (
          <div className={styles.reason}>
            Rejected{edge.rejection_reason ? `: ${edge.rejection_reason}` : ''}. Hidden on the canvas, and not proposed again.
          </div>
        )}
        <RelationControls edge={edge} onChanged={onChanged} />
      </section>

      {edge.rationale && (
        <section className={styles.section}>
          <div className={styles.sectionLabel}>Why the archivist proposed it</div>
          <p className={ai ? styles.aiText : styles.humanText}>{edge.rationale}</p>
        </section>
      )}

      <section className={styles.section}>
        <div className={styles.sectionLabel}>Where {nodeLabel(from)} cites {nodeLabel(to)}</div>
        {edge.evidence.length === 0 && (
          <p className={styles.faint}>No quote: you set this relation yourself.</p>
        )}
        {edge.evidence.map((e) => (
          <figure key={e.id} className={styles.evidence}>
            <blockquote className={styles.quote}>
              {e.text_quote_selector.prefix && <span className={styles.context}>…{e.text_quote_selector.prefix}</span>}
              <mark className={styles.exact}>{e.verbatim_text}</mark>
              {e.text_quote_selector.suffix && <span className={styles.context}>{e.text_quote_selector.suffix}…</span>}
            </blockquote>
            <figcaption className={styles.source}>
              {nodeLabel(from)} · {e.source === 'abstract' ? 'Abstract' : 'Full text'}
            </figcaption>
          </figure>
        ))}
      </section>
    </aside>
  );
}
