import { formatCitation } from '../../lib/format';
import { AiNote } from './FindResultCard';
import type { BacktraceCandidate } from '../../api/backtrace';
import wsStyles from '../../pages/Workspace.module.css';
import styles from './BacktraceTree.module.css';

/** A not-yet-committed candidate from a `/rank` call, rendered as its own
 * leaf card in the tree — title/authors/year/venue/reasoning are already
 * known for free; "Continue backtrace" is what actually spends the cost of
 * finding a verbatim quote (turning this into a real, resolved hop card). */
export default function BacktraceCandidateCard({
  candidate, rankModel, committing, onContinue,
}: {
  candidate: BacktraceCandidate;
  rankModel: string | null;
  committing: boolean;
  onContinue: () => void;
}) {
  const citation = formatCitation(candidate.authors[0] ?? null, candidate.year);

  return (
    <div className={`${wsStyles.evidenceCard} ${styles.pendingCard}`}>
      <div className={wsStyles.evidenceHeader}>
        <span className={wsStyles.evidencePaper}>
          {candidate.title}{citation !== 'Unknown source' ? `, ${citation}` : ''}
          {candidate.venue && <span className={wsStyles.evidenceVenue}> · {candidate.venue}</span>}
        </span>
        {!candidate.confident && <span className={styles.candidateUnsure}>uncertain</span>}
      </div>
      {candidate.reasoning && (
        rankModel
          ? <AiNote text={candidate.reasoning} model={rankModel} label="Match reasoning" />
          : <div className={wsStyles.searchHint}><strong>Match reasoning:</strong> {candidate.reasoning}</div>
      )}
      <div className={wsStyles.paperActionsRow}>
        {candidate.doi && (
          <a className={wsStyles.visitBtn} href={`https://doi.org/${candidate.doi}`} target="_blank" rel="noreferrer">
            visit
          </a>
        )}
        <button className={wsStyles.retryBtn} onClick={onContinue} disabled={committing}>
          {committing ? 'Resolving…' : 'Continue backtrace →'}
        </button>
      </div>
    </div>
  );
}
