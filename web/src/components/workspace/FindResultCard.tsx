import { formatCitation, formatModelName } from '../../lib/format';
import styles from '../../pages/Workspace.module.css';

export type FindImportState = 'importable' | 'already_in_project' | 'imported' | 'no_doi';

/** Renders LLM-generated commentary (a relevance note, match reasoning, etc.)
 * distinctly from verbatim paper text: labeled by kind so the reader knows
 * which question it's answering, quoted, normal-cased, gray-blue italic,
 * with an attribution to the actual model that produced it — never plain
 * uppercase label styling, which is for short static UI headers only. */
export function AiNote({ text, model, label }: { text: string; model: string; label: string }) {
  return (
    <div className={styles.aiNote}>
      <span className={styles.aiNoteLabel}>{label}:</span>
      <span className={styles.aiNoteText}>&ldquo;{text}&rdquo;</span>
      <span className={styles.aiNoteAttribution}>&mdash; {formatModelName(model)}</span>
    </div>
  );
}

export default function FindResultCard({
  title, authors, year, venue, visitUrl, quote, relevanceNote, model,
  roleLabel, verified, importState, onImport, importing, onPromote, promoting,
}: {
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  visitUrl: string | null;
  quote: string;
  relevanceNote: string | null;
  model: string | null;
  roleLabel?: string;
  verified?: boolean;
  importState: FindImportState;
  onImport?: () => void;
  importing?: boolean;
  onPromote?: () => void;
  promoting?: boolean;
}) {
  const citation = formatCitation(authors[0] ?? null, year);
  const importTitle =
    importState === 'no_doi' ? 'Not an importable paper'
    : importState === 'already_in_project' ? 'Already in project'
    : importState === 'imported' ? 'Imported'
    : 'Import into project';
  const importLabel =
    importState === 'importable' ? '+ import'
    : importState === 'no_doi' ? '—'
    : '✓ imported';

  return (
    <div className={`${styles.evidenceCard} ${roleLabel ? styles[roleLabel] ?? '' : ''}`}>
      <div className={styles.evidenceHeader}>
        <span className={styles.evidencePaper}>
          {title}{citation ? `, ${citation}` : ''}
          {venue && <span className={styles.evidenceVenue}> · {venue}</span>}
        </span>
        {roleLabel && <span className={styles.evidenceRole}>{roleLabel}</span>}
        {verified && <span className={styles.verifiedDot} title="verified" />}
        <div className={styles.findCardActions}>
          {visitUrl && (
            <a className={styles.visitBtn} href={visitUrl} target="_blank" rel="noreferrer">
              visit
            </a>
          )}
          <button
            className={styles.evidenceCreateBtn}
            disabled={importState !== 'importable' || importing}
            title={importTitle}
            onClick={() => onImport?.()}
          >
            {importLabel}
          </button>
          {onPromote && (
            <button
              className={styles.evidenceCreateBtn}
              disabled={promoting}
              onClick={onPromote}
              title="Promote to statement"
            >→ statement</button>
          )}
        </div>
      </div>
      <div className={styles.evidenceQuote}>&ldquo;{quote}&rdquo;</div>
      {relevanceNote && (
        model
          ? <AiNote text={relevanceNote} model={model} label="Relevance" />
          : <div className={styles.searchHint}><strong>Relevance:</strong> {relevanceNote}</div>
      )}
    </div>
  );
}
