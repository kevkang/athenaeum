import { useQuery } from '@tanstack/react-query';
import { statementsApi } from '../api/statements';
import { passagesApi } from '../api/passages';
import { trunc, formatCitation } from '../lib/format';
import styles from './EvidenceHoverTooltip.module.css';

/** Hovering a `statementLink`-marked span shows what it's actually backed
 * by, without requiring a click — the citation + verbatim quote for its
 * first passage-kind evidence, the same fields `EvidenceLinkPopover`'s
 * `EvidenceRow` shows once you've already opened that popover. */
export default function EvidenceHoverTooltip({
  projectId,
  statementId,
  x,
  y,
}: {
  projectId: string;
  statementId: string;
  x: number;
  y: number;
}) {
  const { data: statements } = useQuery({
    queryKey: ['statements', projectId],
    queryFn: () => statementsApi.list(projectId),
  });
  const statement = statements?.find((s) => s.id === statementId);

  const { data: evidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId),
  });
  const passageEvidence = evidence?.find((e) => e.target_kind === 'passage');

  const { data: passage } = useQuery({
    queryKey: ['passage-evidence', passageEvidence?.target_id],
    queryFn: () => passagesApi.get(passageEvidence!.target_id),
    enabled: !!passageEvidence,
  });

  return (
    <div className={styles.tooltip} style={{ left: x, top: y }}>
      {statement && <div className={styles.assertion}>{trunc(statement.assertion, 90)}</div>}
      {passage && (
        <>
          <div className={styles.quote}>&ldquo;{trunc(passage.verbatim_text, 220)}&rdquo;</div>
          <div className={styles.cite}>{formatCitation(passage.paper_first_author, passage.paper_year)}</div>
        </>
      )}
      {evidence?.length === 0 && <div className={styles.empty}>No evidence linked yet</div>}
    </div>
  );
}
