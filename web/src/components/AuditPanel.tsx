import { useState, useEffect, useCallback } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { auditApi } from '../api/audit';
import { statementsApi } from '../api/statements';
import styles from './AuditPanel.module.css';

type RunStatus = 'idle' | 'running' | 'done' | 'failed';

interface Finding {
  ord?: number;
  statement_id: string;
  sub_id: string;
  target_kind: string;
  target_id: string;
  quote: string | null;
  supported: boolean;
  rationale: string;
}

interface WsMessage { kind: string; ord?: number; payload?: Finding }

/** Subscribes to the live findings stream, appending via `onFinding` as
 * messages arrive — resetting the accumulated list on a new run is the
 * caller's job (done in a real event handler, createMutation's onSuccess,
 * not here) since setState-on-mount-of-effect is what react-hooks/set-state-
 * in-effect flags as a cascading-render risk. */
function useAuditStream(
  runId: string | null,
  active: boolean,
  onFinding: (finding: Finding) => void,
  onTerminal: (status: 'done' | 'failed') => void,
) {
  useEffect(() => {
    if (!runId || !active) return;
    const ws = new WebSocket(auditApi.streamUrl(runId));
    ws.onmessage = (e) => {
      const event: WsMessage = JSON.parse(e.data);
      if (event.kind === 'done' || event.kind === 'failed') {
        onTerminal(event.kind);
        ws.close();
        return;
      }
      if (event.kind === 'finding' && event.payload) {
        onFinding({ ...event.payload, ord: event.ord });
      }
    };
    return () => { if (ws.readyState < 2) ws.close(); };
  }, [runId, active, onFinding, onTerminal]);
}

function groupByStatement(items: Finding[]): Map<string, Finding[]> {
  const map = new Map<string, Finding[]>();
  for (const item of items) {
    const list = map.get(item.statement_id);
    if (list) list.push(item);
    else map.set(item.statement_id, [item]);
  }
  return map;
}

export default function AuditPanel({ projectId }: { projectId: string }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus>('idle');
  const [liveFindings, setLiveFindings] = useState<Finding[]>([]);

  const { data: statements } = useQuery({
    queryKey: ['statements', projectId],
    queryFn: () => statementsApi.list(projectId),
  });
  const assertionById = new Map((statements ?? []).map((s) => [s.id, s.assertion]));

  const createMutation = useMutation({
    mutationFn: () => auditApi.create(projectId),
    onSuccess: (run) => {
      setLiveFindings([]);
      setRunId(run.id);
      setStatus('running');
    },
  });

  const handleFinding = useCallback((finding: Finding) => {
    setLiveFindings((prev) => {
      if (finding.ord !== undefined && prev.some((f) => f.ord === finding.ord)) return prev;
      return [...prev, finding];
    });
  }, []);

  const handleTerminal = useCallback((finalStatus: 'done' | 'failed') => {
    setStatus(finalStatus);
  }, []);

  useAuditStream(runId, status === 'running', handleFinding, handleTerminal);

  // Once the run reaches a terminal state, re-fetch from the DB rather than
  // trusting the WS-streamed list as final — same reasoning as
  // FindPanel.tsx's LiveTrace: the persisted rows are the source of truth,
  // the live stream is just progress feedback while running.
  const { data: runDetail } = useQuery({
    queryKey: ['audit-run', runId],
    queryFn: () => auditApi.get(runId!),
    enabled: !!runId && status !== 'running',
  });

  const findings: Finding[] = status === 'running' ? liveFindings : (runDetail?.findings ?? []);
  // A skipped check leaves its citation marked "not supported" with nothing
  // having judged it, which on screen is indistinguishable from a real
  // rejection. Saying so is the only way a reader can tell them apart.
  const skipped = runDetail?.run.malformed_entries ?? 0;
  const grouped = groupByStatement(findings);
  const noEvidenceIds = status === 'running' ? [] : (runDetail?.statements_with_no_evidence ?? []);

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.title}>Audit</span>
        <button
          className={styles.runBtn}
          onClick={() => createMutation.mutate()}
          disabled={status === 'running' || createMutation.isPending}
        >
          {status === 'running' ? 'Running…' : runId ? 'Run again' : 'Run audit'}
        </button>
      </div>

      {!runId && (
        <div className={styles.empty}>
          Checks whether each piece of cited evidence actually supports the statement it&rsquo;s
          attached to — an independent LLM pass with no visibility into how the evidence was
          originally selected.
        </div>
      )}

      {status === 'failed' && (
        <div className={styles.failedBanner}>Audit failed to complete. Try running it again.</div>
      )}

      {skipped > 0 && (
        <div className={styles.skippedBanner}>
          {skipped} of {runDetail?.run.quotes_checked} evidence checks came back without a usable
          verdict. Those citations appear below as not supported, but nothing actually judged
          them — run the audit again before reading anything into them.
        </div>
      )}

      <div className={styles.findingsList}>
        {[...grouped.entries()].map(([statementId, items]) => (
          <div key={statementId} className={styles.statementGroup}>
            <div className={styles.statementText}>
              {assertionById.get(statementId) ?? statementId}
            </div>
            {items.map((f, i) => (
              <div
                key={i}
                className={`${styles.finding} ${f.supported ? styles.supported : styles.unsupported}`}
              >
                <div className={styles.findingHead}>
                  <span className={styles.subId}>[^{f.sub_id}]</span>
                  <span className={styles.verdict}>
                    {f.supported ? '✓ Supported' : '✗ Not supported'}
                  </span>
                </div>
                {f.quote && <div className={styles.quote}>&ldquo;{f.quote}&rdquo;</div>}
                <div className={styles.rationale}>{f.rationale}</div>
              </div>
            ))}
          </div>
        ))}

        {noEvidenceIds.length > 0 && (
          <div className={styles.statementGroup}>
            <div className={styles.noEvidenceHeading}>No evidence cited</div>
            {noEvidenceIds.map((sid) => (
              <div key={sid} className={`${styles.finding} ${styles.noEvidence}`}>
                <div className={styles.statementText}>{assertionById.get(sid) ?? sid}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
