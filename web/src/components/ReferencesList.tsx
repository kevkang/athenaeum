import { useQuery } from '@tanstack/react-query';
import { composeApi, type CitationReference } from '../api/compose';
import { formatCitation } from '../lib/format';
import styles from './ReferencesList.module.css';

function referenceHeading(ref: CitationReference): string {
  if (ref.kind === 'paper') {
    const citation = formatCitation(ref.paper_first_author, ref.paper_year);
    return ref.paper_title ? `${ref.paper_title} — ${citation}` : citation;
  }
  if (ref.kind === 'dataset') {
    return ref.dataset_title ?? 'Untitled dataset';
  }
  return ref.statement_assertion ?? 'Linked statement';
}

/** The reference-list content shared by the Sources panel's "References" tab
 * and the inline, auto-generated References section at the end of a Compose
 * document — `variant` swaps typography only (sidebar UI chrome vs.
 * document-prose styling); the data and structure are identical either way. */
export default function ReferencesList({
  projectId,
  variant = 'sidebar',
}: {
  projectId: string;
  variant?: 'sidebar' | 'prose';
}) {
  const { data: citations } = useQuery({
    queryKey: ['compose-citations', projectId],
    queryFn: () => composeApi.citations(projectId),
  });

  const references = citations?.references ?? [];

  return (
    <div className={`${styles.root} ${variant === 'prose' ? styles.prose : styles.sidebar}`}>
      {references.length === 0 && (
        <div className={styles.empty}>
          No references cited yet — link a piece of evidence to a statement in the document to see
          it listed here.
        </div>
      )}

      <div className={styles.refList}>
        {references.map((ref) => (
          <div key={ref.number} className={styles.refGroup}>
            <div className={styles.refHead}>
              <span className={styles.refNumber}>{ref.number}.</span>
              <span className={styles.refTitle}>{referenceHeading(ref)}</span>
            </div>
            {ref.kind === 'paper' && ref.paper_doi && (
              <a
                className={styles.refDoi}
                href={`https://doi.org/${ref.paper_doi}`}
                target="_blank"
                rel="noreferrer"
              >
                doi.org/{ref.paper_doi}
              </a>
            )}
            {ref.evidence.length > 0 && (
              <div className={styles.evidenceList}>
                {ref.evidence.map((e) => (
                  <div key={e.sub_id} className={styles.evidenceRow}>
                    <span className={styles.subId}>{e.sub_id}</span>
                    {e.quote ? (
                      <span className={styles.quote}>&ldquo;{e.quote}&rdquo;</span>
                    ) : (
                      <span className={styles.noQuote}>general reference, no specific quote</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
