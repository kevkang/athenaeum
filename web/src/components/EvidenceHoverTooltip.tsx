import type { CitationEvidence, CitationReference } from '../api/compose';
import { trunc, formatCitation } from '../lib/format';
import styles from './EvidenceHoverTooltip.module.css';

/** Hovering an individual citation marker (the small superscript sub-id
 * injected right after a statementLink span) shows exactly the one piece
 * of evidence that marker addresses — not the statement's evidence as a
 * whole, since a statement can carry more than one independent citation
 * (see domain/citations.py). `reference`/`evidence` come straight out of
 * the citations numbering ComposeView.tsx already has loaded, so this
 * component does no fetching of its own. */
export default function EvidenceHoverTooltip({
  reference,
  evidence,
  x,
  y,
}: {
  reference: CitationReference;
  evidence: CitationEvidence;
  x: number;
  y: number;
}) {
  return (
    <div className={styles.tooltip} style={{ left: x, top: y }}>
      {evidence.quote ? (
        <div className={styles.quote}>&ldquo;{trunc(evidence.quote, 220)}&rdquo;</div>
      ) : (
        <div className={styles.empty}>General citation — no specific passage</div>
      )}
      <div className={styles.cite}>{referenceShortCite(reference)}</div>
    </div>
  );
}

function referenceShortCite(reference: CitationReference): string {
  if (reference.kind === 'paper') {
    return formatCitation(reference.paper_first_author, reference.paper_year);
  }
  if (reference.kind === 'dataset') {
    return reference.dataset_title ?? 'Dataset';
  }
  return reference.statement_assertion ? trunc(reference.statement_assertion, 60) : 'Statement';
}
