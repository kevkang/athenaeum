import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { synthesisApi, type Synthesis, type EvidenceFull, type Evaluation } from '../api/syntheses';
import { projectsApi } from '../api/projects';
import { MentionTextarea } from '../components/MentionTextarea';
import { LinksPanel } from '../components/LinksPanel';
import styles from './SynthesisView.module.css';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function roleClass(role: string): string {
  if (role === 'opposing') return styles.opposing;
  if (role === 'data') return styles.data;
  return styles.default;
}

function stepLabel(kind: string): string {
  const map: Record<string, string> = {
    query_interp: 'interpreting claim',
    retrieval: 'retrieving passages',
    extract: 'extracting evidence',
    signal: 'identifying signals',
    verify: 'verifying citations',
  };
  return map[kind] ?? kind;
}

// ── LiveTrace — WebSocket step stream ────────────────────────────────────────

interface TraceEvent { kind: string; ord?: number; payload?: Record<string, unknown> }

function LiveTrace({ synthesisId, onDone }: { synthesisId: string; onDone: () => void }) {
  const [steps, setSteps] = useState<TraceEvent[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new WebSocket(synthesisApi.streamUrl(synthesisId));

    ws.onmessage = (e) => {
      const event: TraceEvent = JSON.parse(e.data);
      if (event.kind === 'done' || event.kind === 'failed') {
        onDone();
        ws.close();
        return;
      }
      setSteps((prev) => {
        // De-duplicate by ord
        if (event.ord !== undefined && prev.some((s) => s.ord === event.ord)) return prev;
        return [...prev, event];
      });
    };

    return () => { if (ws.readyState < 2) ws.close(); };
  }, [synthesisId, onDone]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [steps]);

  return (
    <div className={styles.liveTrace}>
      {steps.map((s, i) => (
        <div key={i} className={styles.traceStep}>
          <span className={styles.kind}>{stepLabel(s.kind)}</span>
          <span className={styles.text}>
            {typeof s.payload === 'object' && s.payload !== null
              ? Object.entries(s.payload)
                  .filter(([k]) => ['query', 'n_hits', 'n_passages', 'rate'].includes(k))
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                  .join(' ')
              : ''}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

// ── EvidenceCard ─────────────────────────────────────────────────────────────

function EvidenceCard({
  evidence,
  existingEval,
  synthesisId,
}: {
  evidence: EvidenceFull;
  existingEval: Evaluation | undefined;
  synthesisId: string;
}) {
  const queryClient = useQueryClient();
  const [stance, setStance] = useState<string | null>(existingEval?.stance ?? null);

  const mutation = useMutation({
    mutationFn: (s: string) => synthesisApi.upsertEvaluation(synthesisId, evidence.passage_id, s),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluations', synthesisId] });
    },
  });

  const handleStance = (s: string) => {
    const next = stance === s ? null : s;
    setStance(next);
    if (next) mutation.mutate(next);
  };

  // Sync when existingEval loads
  useEffect(() => {
    if (existingEval) setStance(existingEval.stance);
  }, [existingEval]);

  return (
    <div className={`${styles.evidenceCard} ${roleClass(evidence.role)}`}>
      <div className={styles.cardHeader}>
        <span className={styles.cardPaper}>{evidence.paper_title}</span>
        {evidence.paper_year && <span className={styles.cardYear}>{evidence.paper_year}</span>}
        <span className={styles.cardRole}>{evidence.role}</span>
        {evidence.verified && (
          <span className={styles.verifiedBadge}>
            <span className={styles.verifiedDot} />
            verified
          </span>
        )}
      </div>

      <div className={styles.cardQuote}>{evidence.verbatim_text}</div>

      <div className={styles.stanceRow}>
        <span className={styles.stanceLabel}>Your read:</span>
        {(['agree', 'partial', 'disagree'] as const).map((s) => (
          <button
            key={s}
            className={`${styles.stanceChip} ${styles[s]} ${stance === s ? styles.selected : ''}`}
            onClick={() => handleStance(s)}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── SynthesisTextEditor — autosave on debounce ────────────────────────────────

function SynthesisTextEditor({ synthesis }: { synthesis: Synthesis }) {
  const [text, setText] = useState(synthesis.synthesis_text ?? '');
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutation = useMutation({
    mutationFn: (t: string) => synthesisApi.updateText(synthesis.id, t),
    onSettled: () => setSaving(false),
  });

  const handleChange = useCallback((val: string) => {
    setText(val);
    setSaving(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => mutation.mutate(val), 1000);
  }, [mutation]);

  // Sync if synthesis changes (e.g., re-selecting)
  useEffect(() => {
    setText(synthesis.synthesis_text ?? '');
  }, [synthesis.id, synthesis.synthesis_text]);

  return (
    <div className={styles.synthTextSection}>
      <div className={styles.sectionHeader}>Your synthesis</div>
      <MentionTextarea
        className={styles.synthTextArea}
        value={text}
        onChange={handleChange}
        projectId={synthesis.project_id}
        fromType="synthesis"
        fromId={synthesis.id}
        placeholder="Write your synthesis here — your own reading of what the evidence establishes. This is your science, not the AI's."
        rows={8}
      />
      <div className={`${styles.synthTextHint} ${saving ? styles.synthTextSaving : ''}`}>
        {saving ? 'Saving…' : 'Saved automatically'}
      </div>
    </div>
  );
}

// ── SynthesisDetail ───────────────────────────────────────────────────────────

function SynthesisDetail({
  synthesis,
  onRefresh,
  onDeleted,
}: {
  synthesis: Synthesis;
  onRefresh: () => void;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const isRunning = synthesis.status === 'running';

  const deleteMutation = useMutation({
    mutationFn: () => synthesisApi.delete(synthesis.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['syntheses', synthesis.project_id] });
      onDeleted();
    },
  });

  const { data: evidence } = useQuery({
    queryKey: ['evidence-full', synthesis.id],
    queryFn: () => synthesisApi.evidenceFull(synthesis.id),
    enabled: synthesis.status === 'done',
  });

  const { data: evaluations } = useQuery({
    queryKey: ['evaluations', synthesis.id],
    queryFn: () => synthesisApi.evaluations(synthesis.id),
    enabled: synthesis.status === 'done',
  });

  const evalMap = new Map((evaluations ?? []).map((e) => [e.passage_id, e]));

  const handleDone = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['synthesis', synthesis.id] });
    queryClient.invalidateQueries({ queryKey: ['syntheses', synthesis.project_id] });
    onRefresh();
  }, [queryClient, synthesis.id, synthesis.project_id, onRefresh]);

  return (
    <div className={styles.synthDetail}>
      {/* Status banner + delete */}
      <div className={styles.synthDetailHeader}>
        <div className={`${styles.statusBanner} ${styles[synthesis.status]}`}>
          <span className={`${styles.statusDot} ${styles[synthesis.status]}`} />
          {synthesis.status === 'running' && 'Running synthesis…'}
          {synthesis.status === 'done' && 'Synthesis complete'}
          {synthesis.status === 'failed' && 'Synthesis failed'}
          {synthesis.verification_rate !== null && (
            <span className={styles.vr}>
              {(synthesis.verification_rate * 100).toFixed(0)}% citations verified
            </span>
          )}
        </div>
        <button
          className={styles.deleteBtn}
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
        >
          {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
        </button>
      </div>

      {/* Claim */}
      <div className={styles.claimDisplay}>{synthesis.claim_text}</div>

      {/* Live trace while running */}
      {isRunning && (
        <LiveTrace synthesisId={synthesis.id} onDone={handleDone} />
      )}

      {/* Evidence cards */}
      {evidence && evidence.length > 0 && (
        <div className={styles.evidenceList}>
          <div className={styles.sectionHeader}>
            Evidence · {evidence.length} passage{evidence.length !== 1 ? 's' : ''}
          </div>
          {evidence.map((ev) => (
            <EvidenceCard
              key={ev.passage_id}
              evidence={ev}
              existingEval={evalMap.get(ev.passage_id)}
              synthesisId={synthesis.id}
            />
          ))}
        </div>
      )}

      {/* Scientist's own synthesis text */}
      {synthesis.status === 'done' && (
        <SynthesisTextEditor synthesis={synthesis} />
      )}

      <LinksPanel
        projectId={synthesis.project_id}
        refType="synthesis"
        refId={synthesis.id}
      />
    </div>
  );
}

// ── NewSynthesisForm ──────────────────────────────────────────────────────────

function NewSynthesisForm({
  projectId,
  onCreated,
}: {
  projectId: string;
  onCreated: (s: Synthesis) => void;
}) {
  const [claim, setClaim] = useState('');
  const [corpus, setCorpus] = useState('project');

  const mutation = useMutation({
    mutationFn: () => synthesisApi.create(claim.trim(), projectId, corpus),
    onSuccess: (s) => {
      setClaim('');
      onCreated(s);
    },
  });

  return (
    <div className={styles.newForm}>
      <div className={styles.formLabel}>New synthesis — state your claim</div>
      <textarea
        className={styles.claimTextarea}
        value={claim}
        onChange={(e) => setClaim(e.target.value)}
        placeholder="What relationship, mechanism, or pattern are you trying to establish? Be specific."
      />
      <div className={styles.formRow}>
        <select
          className={styles.corpusSelect}
          value={corpus}
          onChange={(e) => setCorpus(e.target.value)}
        >
          <option value="project">Project papers</option>
          <option value="global">Global corpus</option>
        </select>
        <button
          className={styles.runBtn}
          disabled={claim.trim().length < 10 || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Starting…' : 'Run synthesis'}
        </button>
      </div>
      {mutation.error && (
        <div className={styles.formError}>{(mutation.error as Error).message}</div>
      )}
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function SynthesisView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/synthesis' });
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const { data: syntheses } = useQuery({
    queryKey: ['syntheses', projectId],
    queryFn: () => synthesisApi.listForProject(projectId),
    refetchInterval: (q) =>
      q.state.data?.some((s: Synthesis) => s.status === 'running') ? 3000 : false,
  });

  // If selected synthesis is still running, keep it fresh
  const { data: selectedSynth } = useQuery({
    queryKey: ['synthesis', selectedId],
    queryFn: () => synthesisApi.get(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  });

  const activeSynth = selectedSynth ?? syntheses?.find((s) => s.id === selectedId) ?? null;

  const handleCreated = (s: Synthesis) => {
    queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
    setSelectedId(s.id);
  };

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['synthesis', selectedId] });
    queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
    queryClient.invalidateQueries({ queryKey: ['evidence-full', selectedId] });
  }, [queryClient, selectedId, projectId]);

  return (
    <div className={styles.layout}>
      {/* ── Sidebar ── */}
      <aside className={styles.sidebar}>
        <div className={styles.sidebarTop}>
          <div className={styles.projectLabel}>Active project</div>
          <div className={styles.projectName}>{project?.title ?? '…'}</div>
        </div>

        <div className={styles.sidebarListHeader}>
          Past syntheses{syntheses ? ` · ${syntheses.length}` : ''}
        </div>

        <div className={styles.sidebarList}>
          {syntheses?.map((s) => (
            <div
              key={s.id}
              className={`${styles.synthItem} ${s.id === selectedId ? styles.synthItemActive : ''}`}
              onClick={() => setSelectedId(s.id)}
            >
              <div className={styles.synthItemClaim}>{s.claim_text}</div>
              <div className={styles.synthItemMeta}>
                <span className={`${styles.statusDot} ${styles[s.status]}`} />
                <span>{s.status}</span>
                <span>{formatDate(s.run_at)}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* ── Main ── */}
      <div className={styles.main}>
        <div className={styles.mainScroll}>
          <NewSynthesisForm projectId={projectId} onCreated={handleCreated} />

          {activeSynth ? (
            <SynthesisDetail
              key={activeSynth.id}
              synthesis={activeSynth}
              onRefresh={handleRefresh}
              onDeleted={() => setSelectedId(null)}
            />
          ) : (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>No synthesis selected</div>
              <div className={styles.emptyDesc}>
                Run a synthesis above, or click a past one from the sidebar.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
