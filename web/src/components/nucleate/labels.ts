import type { CanvasNode } from '../../api/nucleate';

/** "Koch 2018" — first author's surname and year, how papers are named on
 * chips, in the chat, and on nodes at overview zoom. */
export function nodeLabel(node: CanvasNode): string {
  const first = node.paper.authors[0] ?? '';
  const surname = first.trim().split(/\s+/).pop() || node.paper.title.split(/\s+/).slice(0, 3).join(' ');
  return node.paper.year ? `${surname} ${node.paper.year}` : surname;
}

/**
 * Papers the archivist found relevant but has no usable text for — gathered
 * into the needs-full-text queue rather than interrupting per paper (handoff
 * Part V). Each shows its DOI
 * and what the Unpaywall check found. Supplying the PDF triggers ingestion,
 * after which the node gains full-text evidence on its own (D-N15).
 *
 * A paper whose open-access PDF is already being fetched is not here: it
 * isn't waiting on the researcher.
 */
export function needsFullText(nodes: CanvasNode[]): CanvasNode[] {
  return nodes.filter(
    (n) =>
      n.fulltext === 'user_must_supply' &&
      n.review.status !== 'rejected' &&
      !n.paper.oa_pdf_url,
  );
}
