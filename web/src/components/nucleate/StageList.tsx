import { useEffect, useState } from 'react';
import { STAGES, STAGE_LABELS, type Expansion, type ExpansionEvent } from '../../api/nucleate';
import { stageViews, type StageState } from './stages';
import styles from './NucleatePanel.module.css';

function elapsed(from: string, to: string | null, now: number): string {
  const seconds = Math.max(0, Math.round(((to ? Date.parse(to) : now) - Date.parse(from)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

const SYMBOL: Record<StageState, string> = {
  pending: '○',
  current: '●',
  completed: '✓',
  failed: '✕',
};

/**
 * The stage list. In full while a run works; afterwards one line that still
 * says, explicitly, that the next move is the researcher's — expandable to
 * the full list (D-N17).
 */
export default function StageList({
  run,
  events,
  onCancel,
}: {
  run: Expansion | null;
  events: ExpansionEvent[];
  onCancel: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const running = run?.status === 'running';

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);

  if (!run) return null;
  const views = stageViews(events);

  if (!running && !expanded) {
    const proposed = events.find((e) => e.stage === 'ranking' && e.status === 'completed')
      ?.counts?.selected;
    const failedAt = STAGES.find((s) => views[s].state === 'failed');
    const summary =
      run.status === 'failed'
        ? `Failed at ${STAGE_LABELS[failedAt ?? 'interpreting'].toLowerCase()}`
        : run.status === 'cancelled'
          ? 'Cancelled · completed papers kept'
          : `Awaiting review${proposed !== undefined ? ` · ${proposed} proposed` : ''}`;
    return (
      <button className={styles.stageSummary} data-state={run.status} onClick={() => setExpanded(true)}>
        <span>{run.status === 'done' ? '✓' : run.status === 'failed' ? '✕' : '■'}</span>
        <span>{summary}</span>
        <span className={styles.stageElapsed}>{elapsed(run.started_at, run.finished_at, now)}</span>
      </button>
    );
  }

  return (
    <div className={styles.stages} aria-live="polite">
      <ol className={styles.stageList}>
        {STAGES.map((stage) => {
          const view = views[stage];
          return (
            <li key={stage} className={styles.stage} data-state={view.state}>
              <span className={styles.stageSymbol} aria-hidden>{SYMBOL[view.state]}</span>
              <span className={styles.stageLabel}>{STAGE_LABELS[stage]}</span>
              {view.detail && (
                // Short counts sit beside the label; longer notes drop to
                // their own line rather than wrapping against it.
                <span className={view.detail.length > 18 ? styles.stageDetailBlock : styles.stageDetail}>
                  {view.detail}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <div className={styles.stageFooter}>
        <span className={styles.stageElapsed}>
          {elapsed(run.started_at, run.finished_at, now)} {running ? 'elapsed' : 'total'}
        </span>
        {running ? (
          <button className={styles.cancelBtn} onClick={onCancel}>Cancel</button>
        ) : (
          <button className={styles.linkBtn} onClick={() => setExpanded(false)}>Collapse</button>
        )}
      </div>
    </div>
  );
}
