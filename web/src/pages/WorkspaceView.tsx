import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import { api } from '../api/client';
import {
  statementsApi,
  type Statement,
  type StatementNode,
  type StatementEdge,
  type StatementRelation,
  type Evidence,
} from '../api/statements';
import { papersApi, type Paper, firstAuthor } from '../api/papers';
import { backtraceApi } from '../api/backtrace';
import { passagesApi } from '../api/passages';
import { historyApi, type EventOut } from '../api/history';
import ReaderPane, { PASSAGE_MIME } from '../components/ReaderPane';
import { trunc, stripHtml, formatDate, formatCitation, typeClass } from '../lib/format';
import styles from './Workspace.module.css';
import PapersSidebar, { type UploadQueueItem } from '../components/workspace/PapersSidebar';
import FindPanel from '../components/workspace/FindPanel';
import BacktracePanel from '../components/workspace/BacktracePanel';
import BacktraceTree from '../components/workspace/BacktraceTree';

cytoscape.use(dagre);

// ── Types ─────────────────────────────────────────────────────────────────────

interface StatementGraph { nodes: StatementNode[]; edges: StatementEdge[] }

type PanelMode = null | { kind: 'statement'; id: string } | { kind: 'new' } | { kind: 'history' };
type NodeFilter = 'all' | 'statements' | 'questions';

const RELATIONS: StatementRelation[] = ['relates_to', 'supports', 'tension'];
// W/E/R hold+drag-between-nodes shortcuts, per spec (not mnemonic).
const RELATION_KEYS: Record<string, StatementRelation> = { w: 'tension', e: 'relates_to', r: 'supports' };
const DROPPABLE_SUFFIXES = ['.pdf', '.txt'];
const READER_MIN_WIDTH = 320;
const READER_MAX_WIDTH = 900;
const READER_DEFAULT_WIDTH = 460;

function relationLabel(r: StatementRelation): string {
  return r === 'relates_to' ? 'relates to' : r === 'supports' ? 'supports' : 'in tension with';
}

function isDroppableFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return DROPPABLE_SUFFIXES.some((suf) => name.endsWith(suf));
}

// ── Canvas element builder ────────────────────────────────────────────────────

function buildElements(graph: StatementGraph, nodeFilter: NodeFilter) {
  const visible = graph.nodes.filter((n) => {
    if (nodeFilter === 'questions') return n.type === 'question';
    if (nodeFilter === 'statements') return n.type !== 'question';
    return true;
  });
  const visibleIds = new Set(visible.map((n) => n.id));

  return [
    ...visible.map((n) => ({
      data: {
        id: n.id, kind: 'statement', label: trunc(n.assertion, 62), type: n.type, status: n.status,
        evidence_count: n.evidence_count,
      },
    })),
    ...graph.edges
      .filter((e) => visibleIds.has(e.from_statement_id) && visibleIds.has(e.to_statement_id))
      .map((e) => ({
        data: {
          id: `edge-${e.from_statement_id}-${e.to_statement_id}`,
          source: e.from_statement_id,
          target: e.to_statement_id,
          kind: e.relation,
        },
      })),
  ];
}

const CY_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      label: 'data(label)', color: '#e6e1d6', 'font-family': 'Inter, sans-serif',
      'font-size': '10.5px', 'text-wrap': 'wrap', 'text-valign': 'center',
      'text-halign': 'center', padding: '8px',
    },
  },
  {
    selector: 'node[kind = "statement"]',
    style: {
      shape: 'roundrectangle', width: '180px', height: '52px',
      'background-color': '#1f1d18', 'border-width': 1, 'border-color': '#8a7a5c',
      'font-style': 'italic', 'text-max-width': '164px',
    },
  },
  {
    selector: 'node[kind = "statement"][status = "locked"]',
    style: { 'background-color': '#1a221a', 'border-color': '#8aa57c', 'border-width': 1.5 },
  },
  {
    selector: 'node[kind = "statement"][type = "question"]',
    style: {
      'background-color': '#1a1a26', 'border-color': '#5c6b8a', 'font-style': 'normal',
    },
  },
  {
    selector: 'node[kind = "statement"][type = "question"][status = "resolved"]',
    style: { 'border-color': '#5a8a6a', 'background-color': '#1a231a' },
  },
  {
    selector: 'node:selected',
    style: { 'background-color': '#2a2a1e', 'border-color': '#c9a876', 'border-width': 2.5 },
  },
  {
    selector: 'node.edge-drag-source',
    style: { 'border-color': '#c9a876', 'border-width': 3, 'border-style': 'dashed' },
  },
  {
    // relates_to: neutral / white
    selector: 'edge[kind = "relates_to"]',
    style: {
      width: 1.5, 'line-color': '#a8a29a', 'target-arrow-color': '#a8a29a',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    // supports: positive / green
    selector: 'edge[kind = "supports"]',
    style: {
      width: 1.5, 'line-color': '#8aa57c', 'target-arrow-color': '#8aa57c',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    // tension: negative / red
    selector: 'edge[kind = "tension"]',
    style: {
      width: 1.5, 'line-style': 'dashed', 'line-color': '#c67a5c', 'target-arrow-color': '#c67a5c',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    selector: 'edge:selected',
    style: { 'line-color': '#c9a876', 'target-arrow-color': '#c9a876' },
  },
];

// ── New statement quick-create ────────────────────────────────────────────────

function NewStatementForm({ projectId, onCreated }: { projectId: string; onCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [type, setType] = useState('claim');
  const [text, setText] = useState('');

  const createMutation = useMutation({
    mutationFn: () => statementsApi.create(projectId, type, text.trim()),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setText('');
      onCreated(s.id);
    },
  });

  return (
    <div className={styles.sidebarSection}>
      <div className={styles.sidebarSectionHeader}>New statement</div>
      <select
        className={styles.typeSelect}
        value={type}
        onChange={(e) => setType(e.target.value)}
      >
        {['claim', 'observation', 'question', 'foundation', 'methods', 'synthesis', 'other'].map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
      <textarea
        className={styles.statementTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={type === 'question' ? 'State your question…' : 'State the claim or finding…'}
        rows={3}
      />
      <button
        className={styles.createBtn}
        disabled={text.trim().length < 3 || createMutation.isPending}
        onClick={() => createMutation.mutate()}
      >
        {createMutation.isPending ? 'Creating…' : 'Create'}
      </button>
    </div>
  );
}

// ── Statement detail panel ─────────────────────────────────────────────────────

function AssertionEditor({ statement }: { statement: Statement }) {
  const [text, setText] = useState(statement.assertion);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutation = useMutation({
    mutationFn: (val: string) => statementsApi.update(statement.id, { assertion: val }),
    onSettled: () => setSaving(false),
  });

  // Reset the local draft when a different statement (or its server-side
  // assertion) changes — done during render via state (not a ref: refs can't
  // be read or written during render), avoiding a stale-then-corrected
  // double render (see React's "adjusting state when a prop changes" guidance).
  const currentKey = `${statement.id}:${statement.assertion}`;
  const [prevStatementKey, setPrevStatementKey] = useState(currentKey);
  if (currentKey !== prevStatementKey) {
    setPrevStatementKey(currentKey);
    if (text !== statement.assertion) setText(statement.assertion);
  }

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    setSaving(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => mutation.mutate(val), 1000);
  }, [mutation]);

  const isLocked = statement.status === 'locked';

  return (
    <div className={styles.assertionSection}>
      <textarea
        className={styles.assertionTextarea}
        value={text}
        onChange={handleChange}
        disabled={isLocked}
        placeholder={statement.type === 'question' ? 'State your question…' : 'State the claim or finding…'}
        rows={4}
      />
      <div className={`${styles.saveHint} ${saving ? styles.saveHintSaving : ''}`}>
        {saving ? 'Saving…' : isLocked ? 'Locked — edit to unlock' : 'Auto-saved'}
      </div>
    </div>
  );
}

function StatementDetailPanel({
  statementId, projectId, allStatements, papers,
}: {
  statementId: string;
  projectId: string;
  allStatements: StatementNode[];
  papers: Paper[];
}) {
  const queryClient = useQueryClient();

  const { data: statement } = useQuery({
    queryKey: ['statement', statementId],
    queryFn: () => api.get<Statement>(`/statements/${statementId}`).then((r) => r.data),
  });

  const { data: evidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId),
  });

  const { data: edges } = useQuery({
    queryKey: ['edges', statementId],
    queryFn: () => statementsApi.edges(statementId),
  });

  const [targetKind, setTargetKind] = useState<'paper' | 'statement' | 'passage' | 'dataset'>('paper');
  const [targetId, setTargetId] = useState('');
  const [role, setRole] = useState('');
  const [relationTargetId, setRelationTargetId] = useState('');
  const [relation, setRelation] = useState<StatementRelation>('relates_to');

  const [prevTargetKind, setPrevTargetKind] = useState(targetKind);
  if (targetKind !== prevTargetKind) {
    setPrevTargetKind(targetKind);
    if (targetId !== '') setTargetId('');
  }

  const lockMutation = useMutation({
    mutationFn: () => statementsApi.lock(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['statement', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const unlockMutation = useMutation({
    mutationFn: () => statementsApi.unlock(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['statement', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => statementsApi.delete(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const statusMutation = useMutation({
    mutationFn: (status: string) => statementsApi.update(statementId, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['statement', statementId] }),
  });

  const addEvidenceMutation = useMutation({
    mutationFn: () => statementsApi.addEvidence(statementId, targetKind, targetId, role || 'evidence'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evidence', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      setTargetId('');
    },
  });

  const removeEvidenceMutation = useMutation({
    mutationFn: ({ kind, id }: { kind: string; id: string }) => statementsApi.removeEvidence(statementId, kind, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evidence', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
    },
  });

  const addEdgeMutation = useMutation({
    mutationFn: () => statementsApi.addEdge(statementId, relationTargetId, relation),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edges', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setRelationTargetId('');
    },
  });

  const removeEdgeMutation = useMutation({
    mutationFn: (otherId: string) => statementsApi.removeEdge(statementId, otherId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edges', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  if (!statement) return <div className={styles.panelLoading}>Loading…</div>;

  const linkedKeys = new Set((evidence ?? []).map((e: Evidence) => `${e.target_kind}:${e.target_id}`));
  const availablePapers = papers.filter((p) => !linkedKeys.has(`paper:${p.id}`));
  const availableStatements = allStatements.filter((s) => s.id !== statementId && !linkedKeys.has(`statement:${s.id}`));

  const relatedIds = new Set((edges ?? []).map((e) => (e.from_statement_id === statementId ? e.to_statement_id : e.from_statement_id)));
  const relationTargetOptions = allStatements.filter((s) => s.id !== statementId && !relatedIds.has(s.id));

  const isQuestion = statement.type === 'question';
  const needsUUIDInput = targetKind === 'passage' || targetKind === 'dataset';
  const canAddEvidence = needsUUIDInput
    ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetId)
    : targetId.trim().length > 0;

  function evidenceLabel(e: Evidence) {
    if (e.target_kind === 'paper') {
      const found = papers.find((p) => p.id === e.target_id);
      return found ? trunc(found.title, 50) : e.target_id;
    }
    if (e.target_kind === 'statement') {
      const found = allStatements.find((s) => s.id === e.target_id);
      return found ? trunc(found.assertion, 50) : e.target_id;
    }
    return `${e.target_kind} · ${e.target_id.slice(0, 8)}…`;
  }

  return (
    <div className={styles.panelContent}>
      <div className={styles.statementPanelHeader}>
        <span className={`${styles.typeBadge} ${styles[typeClass(statement.type)]}`}>{statement.type}</span>
        <span className={`${styles.statusDot} ${styles[statement.status]}`} />
        <span className={styles.statementStatus}>{statement.status}</span>

        {!isQuestion && statement.status === 'draft' && (
          <button
            className={styles.lockBtn}
            onClick={() => lockMutation.mutate()}
            disabled={lockMutation.isPending || statement.assertion.trim().length < 5}
          >
            Lock
          </button>
        )}
        {!isQuestion && statement.status === 'locked' && (
          <button
            className={styles.lockBtn}
            onClick={() => unlockMutation.mutate()}
            disabled={unlockMutation.isPending}
          >
            Unlock
          </button>
        )}
        {isQuestion && (
          <select
            className={styles.questionStatusSelect}
            value={statement.status}
            onChange={(e) => statusMutation.mutate(e.target.value)}
          >
            <option value="open">open</option>
            <option value="resolved">resolved</option>
            <option value="wontfix">won't fix</option>
          </select>
        )}
        {statement.status !== 'locked' && (
          <button
            className={styles.deleteBtn}
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            ×
          </button>
        )}
      </div>

      <AssertionEditor statement={statement} />

      {/* Evidence */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Evidence</div>
        {(evidence ?? []).map((e: Evidence) => (
          <div key={`${e.target_kind}:${e.target_id}`} className={styles.edgeChip}>
            <span className={styles.edgeChipKind}>{e.target_kind}</span>
            <span className={styles.edgeChipText}>{evidenceLabel(e)}</span>
            <span className={styles.edgeChipRole}>{e.role}</span>
            <button
              className={styles.edgeRemoveBtn}
              onClick={() => removeEvidenceMutation.mutate({ kind: e.target_kind, id: e.target_id })}
            >×</button>
          </div>
        ))}
        <div className={styles.edgeAddRow}>
          <select className={styles.kindSelect} value={targetKind} onChange={(e) => setTargetKind(e.target.value as typeof targetKind)}>
            <option value="paper">paper</option>
            <option value="statement">statement</option>
            <option value="passage">passage</option>
            <option value="dataset">dataset</option>
          </select>
          {targetKind === 'paper' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availablePapers.map((p) => (
                <option key={p.id} value={p.id}>{trunc(p.title.replace(/<[^>]*>/g, ''), 40)}</option>
              ))}
            </select>
          ) : targetKind === 'statement' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availableStatements.map((s) => (
                <option key={s.id} value={s.id}>{trunc(s.assertion, 40)}</option>
              ))}
            </select>
          ) : (
            <input className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="UUID" />
          )}
          <input className={styles.roleInput} value={role} onChange={(e) => setRole(e.target.value)} placeholder="role" list="statement-roles" />
          <datalist id="statement-roles">
            {['evidence', 'supports', 'contradicts', 'data', 'methodology', 'context'].map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
          <button
            className={styles.edgeAddBtn}
            disabled={!canAddEvidence || addEvidenceMutation.isPending}
            onClick={() => addEvidenceMutation.mutate()}
          >+</button>
        </div>
      </div>

      {/* Relations */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Relations</div>
        {(edges ?? []).map((e) => {
          const otherId = e.from_statement_id === statementId ? e.to_statement_id : e.from_statement_id;
          const other = allStatements.find((s) => s.id === otherId);
          const isOutgoing = e.from_statement_id === statementId;
          return (
            <div key={otherId} className={styles.edgeChip}>
              <span className={styles.edgeChipKind}>{isOutgoing ? relationLabel(e.relation) : `← ${relationLabel(e.relation)}`}</span>
              <span className={styles.edgeChipText}>{other ? trunc(other.assertion, 45) : otherId.slice(0, 8)}</span>
              <button className={styles.edgeRemoveBtn} onClick={() => removeEdgeMutation.mutate(otherId)}>×</button>
            </div>
          );
        })}
        <div className={styles.edgeAddRow}>
          <select className={styles.kindSelect} value={relation} onChange={(e) => setRelation(e.target.value as StatementRelation)}>
            {RELATIONS.map((r) => (
              <option key={r} value={r}>{relationLabel(r)}</option>
            ))}
          </select>
          <select className={styles.targetSelect} style={{ flex: 1 }} value={relationTargetId} onChange={(e) => setRelationTargetId(e.target.value)}>
            <option value="">— select statement —</option>
            {relationTargetOptions.map((s) => (
              <option key={s.id} value={s.id}>[{s.type}] {trunc(s.assertion, 40)}</option>
            ))}
          </select>
          <button
            className={styles.edgeAddBtn}
            disabled={!relationTargetId || addEdgeMutation.isPending}
            onClick={() => addEdgeMutation.mutate()}
          >+</button>
        </div>
      </div>

      <div className={styles.statementMeta}>
        Drafted {formatDate(statement.drafted_at)}
        {statement.locked_at && ` · Locked ${formatDate(statement.locked_at)}`}
        {statement.built_from_synthesis_id && ' · From synthesis'}
      </div>
    </div>
  );
}

// ── Evidence-preview popover (anchored to a graph node, separate from the
// fuller detail panel — a quick glance at quotes + citations, not for editing) ─

function EvidencePreviewRow({
  evidence, papers, statements,
}: {
  evidence: Evidence;
  papers: Paper[];
  statements: StatementNode[];
}) {
  const isPassage = evidence.target_kind === 'passage';
  const { data: passage } = useQuery({
    queryKey: ['passage-evidence', evidence.target_id],
    queryFn: () => passagesApi.get(evidence.target_id),
    enabled: isPassage,
    staleTime: 5 * 60_000,
  });

  if (isPassage) {
    if (!passage) return <div className={styles.evidencePopoverRow}>Loading…</div>;
    return (
      <div className={styles.evidencePopoverRow}>
        <div className={styles.evidencePopoverQuote}>&ldquo;{trunc(passage.verbatim_text, 160)}&rdquo;</div>
        <div className={styles.evidencePopoverCite}>
          {formatCitation(passage.paper_first_author, passage.paper_year)}
        </div>
      </div>
    );
  }

  if (evidence.target_kind === 'paper') {
    const paper = papers.find((p) => p.id === evidence.target_id);
    return (
      <div className={styles.evidencePopoverRow}>
        <div className={styles.evidencePopoverQuote}>{paper ? stripHtml(paper.title) : 'Paper'}</div>
        <div className={styles.evidencePopoverCite}>
          {paper ? formatCitation(firstAuthor(paper.authors_json), paper.year) : evidence.target_id.slice(0, 8)}
        </div>
      </div>
    );
  }

  if (evidence.target_kind === 'statement') {
    const target = statements.find((s) => s.id === evidence.target_id);
    return (
      <div className={styles.evidencePopoverRow}>
        <div className={styles.evidencePopoverQuote}>{target ? trunc(target.assertion, 160) : 'Statement'}</div>
        {target && <div className={styles.evidencePopoverCite}>{target.type}</div>}
      </div>
    );
  }

  return (
    <div className={styles.evidencePopoverRow}>
      <div className={styles.evidencePopoverQuote}>{evidence.target_kind} · {evidence.target_id.slice(0, 8)}…</div>
    </div>
  );
}

function EvidencePopover({
  statementId, x, y, papers, statements, onClose,
}: {
  statementId: string;
  x: number;
  y: number;
  papers: Paper[];
  statements: StatementNode[];
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const { data: evidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId),
  });

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={popoverRef} className={styles.evidencePopover} style={{ left: x, top: y }}>
      <div className={styles.evidencePopoverHeader}>
        Evidence {evidence ? `· ${evidence.length}` : ''}
      </div>
      {evidence && evidence.length === 0 && (
        <div className={styles.evidencePopoverRow}>No evidence linked yet.</div>
      )}
      {(evidence ?? []).map((e) => (
        <EvidencePreviewRow key={`${e.target_kind}:${e.target_id}`} evidence={e} papers={papers} statements={statements} />
      ))}
    </div>
  );
}

// ── Double-click-to-create popup (also reached via the context menu) ───────────

function QuickCreatePopup({
  x, y, onCreate, onClose, isPending,
}: {
  x: number;
  y: number;
  onCreate: (text: string) => void;
  onClose: () => void;
  isPending: boolean;
}) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={ref} className={styles.quickCreatePopup} style={{ left: x, top: y }}>
      <textarea
        className={styles.quickCreateTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="State the claim or finding…"
        rows={2}
        autoFocus
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim().length >= 3) onCreate(text);
          if (e.key === 'Escape') onClose();
        }}
      />
      <button
        className={styles.quickCreateBtn}
        disabled={text.trim().length < 3 || isPending}
        onClick={() => onCreate(text)}
      >
        {isPending ? 'Creating…' : 'Create statement'}
      </button>
    </div>
  );
}

// ── Right-click context menu (background: create; node/edge: delete) ──────────

function CanvasContextMenu({
  x, y, kind, onCreateHere, onDelete, onClose,
}: {
  x: number;
  y: number;
  kind: 'background' | 'node' | 'edge';
  onCreateHere: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={ref} className={styles.contextMenu} style={{ left: x, top: y }}>
      {kind === 'background' && (
        <button className={styles.contextMenuItem} onClick={onCreateHere}>+ Create statement here</button>
      )}
      {kind === 'node' && (
        <button className={`${styles.contextMenuItem} ${styles.contextMenuItemDanger}`} onClick={onDelete}>
          Delete statement
        </button>
      )}
      {kind === 'edge' && (
        <button className={`${styles.contextMenuItem} ${styles.contextMenuItemDanger}`} onClick={onDelete}>
          Delete relation
        </button>
      )}
    </div>
  );
}

// ── History panel ─────────────────────────────────────────────────────────────

function formatRelative(iso: string) {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function eventSummary(ev: EventOut): string {
  const p = ev.payload_json as Record<string, unknown>;
  switch (ev.kind) {
    case 'statement.created':
      return `created ${typeof p.type === 'string' ? p.type : 'statement'}: ${trunc(String(p.assertion ?? ''), 60)}`;
    case 'statement.edited': {
      const undoes = p.undoes_seq;
      if (typeof undoes === 'number') return `undid #${undoes}: ${trunc(String(p.assertion ?? ''), 60)}`;
      return `edited: ${trunc(String(p.assertion ?? ''), 60)}`;
    }
    case 'statement.locked':
      return `locked: ${trunc(String(p.assertion_preview ?? ''), 60)}`;
    case 'statement.deleted':
      return `deleted: ${trunc(String(p.assertion_preview ?? ''), 60)}`;
    default:
      return ev.kind;
  }
}

function HistoryPanel({
  projectId, onSelectStatement,
}: {
  projectId: string;
  onSelectStatement: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [undoError, setUndoError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['history', projectId],
    queryFn: () => historyApi.list(projectId, { limit: 50 }),
    refetchInterval: 3000,
  });

  const undoMutation = useMutation({
    mutationFn: (seq: number) => historyApi.undo(projectId, seq),
    onSuccess: () => {
      setUndoError(null);
      queryClient.invalidateQueries({ queryKey: ['history', projectId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'statement' });
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } }; message?: string };
      setUndoError(e.response?.data?.detail ?? e.message ?? 'Undo failed');
    },
  });

  const events = data?.events ?? [];
  const undoneSeqs = new Set(
    events
      .map((e) => e.payload_json.undoes_seq)
      .filter((s): s is number => typeof s === 'number'),
  );

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>history</div>

      {undoError && (
        <div className={styles.formError} style={{ marginBottom: 8 }}>{undoError}</div>
      )}

      {isLoading && <div className={styles.panelLoading}>Loading…</div>}

      {!isLoading && events.length === 0 && (
        <div className={styles.panelLoading}>No events yet.</div>
      )}

      <div className={styles.historyList}>
        {events.map((ev) => {
          const isUndone = undoneSeqs.has(ev.project_seq);
          const isUndoable = ev.kind === 'statement.edited' && !isUndone;
          return (
            <div
              key={ev.id}
              className={`${styles.historyItem} ${isUndone ? styles.historyItemUndone : ''}`}
            >
              <div className={styles.historyMeta}>
                <span className={styles.historySeq}>#{ev.project_seq}</span>
                <span className={styles.historyKind}>{ev.kind}</span>
                <span className={styles.historyActor}>
                  {ev.actor_display_name ?? 'unknown'}
                </span>
                <span className={styles.historyTime}>{formatRelative(ev.created_at)}</span>
              </div>
              <div className={styles.historySummary}>
                {ev.entity_kind === 'statement' && ev.entity_id ? (
                  <button
                    className={styles.historyLink}
                    onClick={() => onSelectStatement(ev.entity_id!)}
                  >
                    {eventSummary(ev)}
                  </button>
                ) : (
                  <span>{eventSummary(ev)}</span>
                )}
                {isUndoable && (
                  <button
                    className={styles.historyUndoBtn}
                    disabled={undoMutation.isPending}
                    onClick={() => undoMutation.mutate(ev.project_seq)}
                  >
                    Undo
                  </button>
                )}
                {isUndone && <span className={styles.historyUndoneLabel}>undone</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────

function RightPanel({
  panelMode, onClose, projectId, papers, allStatements, onStatementCreated,
}: {
  panelMode: Exclude<PanelMode, null>;
  onClose: () => void;
  projectId: string;
  papers: Paper[];
  allStatements: StatementNode[];
  onStatementCreated: (id: string) => void;
}) {
  return (
    <div className={styles.rightPanel}>
      <div className={styles.rightPanelClose}>
        <button className={styles.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div className={styles.rightPanelScroll}>
        {panelMode.kind === 'statement' && (
          <StatementDetailPanel
            statementId={panelMode.id}
            projectId={projectId}
            allStatements={allStatements}
            papers={papers}
          />
        )}
        {panelMode.kind === 'new' && (
          <NewStatementForm projectId={projectId} onCreated={onStatementCreated} />
        )}
        {panelMode.kind === 'history' && (
          <HistoryPanel
            projectId={projectId}
            onSelectStatement={(id) => onStatementCreated(id)}
          />
        )}
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function WorkspaceView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/workspace' });
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const pendingSelectRef = useRef<string | null>(null);

  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);
  const [readerMode, setReaderMode] = useState<'read' | 'find' | 'backtrace'>('read');
  const [activeBacktrace, setActiveBacktrace] = useState<{ chainId: string } | null>(null);

  // Right-pane view: the knowledge graph (default, always available) or the
  // backtrace tree — an always-visible toggle, not gated on a chain
  // existing yet (unlike the left pane's reader-mode "Backtrace" tab, which
  // only appears once one has). treeChainId is deliberately independent of
  // activeBacktrace.chainId: the history browser can load a past chain into
  // the tree without disturbing whatever the left panel's linear stepper is
  // currently doing.
  const [graphViewMode, setGraphViewMode] = useState<'graph' | 'backtrace'>('graph');
  const [treeChainId, setTreeChainId] = useState<string | null>(null);

  const startBacktraceMutation = useMutation({
    mutationFn: ({ sentenceId, text }: { sentenceId: string; text: string }) =>
      backtraceApi.start(projectId, selectedPaperId!, sentenceId, text),
    onSuccess: (chain) => {
      setActiveBacktrace({ chainId: chain.id });
      setReaderMode('backtrace');
      setTreeChainId(chain.id);
      setGraphViewMode('backtrace');
    },
  });
  const [panelMode, setPanelMode] = useState<PanelMode>(null);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>('all');
  const [readerWidth, setReaderWidth] = useState(READER_DEFAULT_WIDTH);
  const resizingRef = useRef(false);

  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepthRef = useRef(0);
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);

  // ── Graph canvas interaction state ──────────────────────────────────────────
  const [renderTick, setRenderTick] = useState(0);
  const [badgePositions, setBadgePositions] = useState<Record<string, { x: number; y: number }>>({});
  const [evidencePopover, setEvidencePopover] = useState<{ statementId: string; x: number; y: number } | null>(null);
  const [contextMenu, setContextMenu] = useState<
    | { x: number; y: number; kind: 'background' }
    | { x: number; y: number; kind: 'node'; nodeId: string }
    | { x: number; y: number; kind: 'edge'; edgeFrom: string; edgeTo: string }
    | null
  >(null);
  const [quickCreate, setQuickCreate] = useState<{ x: number; y: number } | null>(null);
  const [heldRelationKey, setHeldRelationKey] = useState<StatementRelation | null>(null);
  const heldRelationKeyRef = useRef<StatementRelation | null>(null);
  const edgeDragSourceRef = useRef<{ id: string; relation: StatementRelation } | null>(null);

  const { data: papers = [], isLoading: papersLoading } = useQuery({
    queryKey: ['papers', projectId],
    queryFn: () => papersApi.listForProject(projectId),
    refetchInterval: (q) => {
      const data = q.state.data as Paper[] | undefined;
      if (!data) return false;
      const tenMinAgo = Date.now() - 10 * 60 * 1000;
      return data.some(
        (p) => p.status === 'ghost' && new Date(p.created_at).getTime() > tenMinAgo,
      ) ? 3000 : false;
    },
  });

  const { data: graph, isLoading: graphLoading } = useQuery({
    queryKey: ['graph', projectId],
    queryFn: () => api.get<StatementGraph>(`/projects/${projectId}/graph`).then((r) => r.data),
  });

  const handleStatementCreated = useCallback((id: string) => {
    pendingSelectRef.current = id;
    setPanelMode({ kind: 'statement', id });
  }, []);

  const createFromPassageMutation = useMutation({
    mutationFn: async ({ text, sentenceId }: { text: string; sentenceId: string }) => {
      const statement = await statementsApi.create(projectId, 'claim', text);
      await statementsApi.addEvidence(statement.id, 'passage', sentenceId, 'evidence');
      return statement;
    },
    onSuccess: (statement) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      handleStatementCreated(statement.id);
    },
  });

  // Dropping a dragged passage directly onto an existing statement node nests
  // it there as evidence, instead of spawning a new statement.
  const nestEvidenceMutation = useMutation({
    mutationFn: ({ statementId, sentenceId }: { statementId: string; sentenceId: string }) =>
      statementsApi.addEvidence(statementId, 'passage', sentenceId, 'evidence'),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ queryKey: ['evidence', variables.statementId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      handleStatementCreated(variables.statementId);
    },
  });

  function findStatementNodeAtPoint(clientX: number, clientY: number): cytoscape.NodeSingular | null {
    const cy = cyRef.current;
    const container = containerRef.current;
    if (!cy || !container) return null;
    const rect = container.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let found: cytoscape.NodeSingular | null = null;
    cy.nodes().forEach((n) => {
      if (found || n.data('kind') !== 'statement') return;
      const bb = n.renderedBoundingBox();
      if (x >= bb.x1 && x <= bb.x2 && y >= bb.y1 && y <= bb.y2) found = n;
    });
    return found;
  }

  // ── Canvas-driven create / delete / connect ─────────────────────────────────

  const quickCreateMutation = useMutation({
    mutationFn: (text: string) => statementsApi.create(projectId, 'claim', text.trim()),
    onSuccess: (statement) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setQuickCreate(null);
      handleStatementCreated(statement.id);
    },
  });

  const deleteStatementFromCanvasMutation = useMutation({
    mutationFn: (id: string) => statementsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setPanelMode((m) => (m?.kind === 'statement' ? null : m));
      setEvidencePopover(null);
    },
  });

  const removeEdgeFromCanvasMutation = useMutation({
    mutationFn: ({ from, other }: { from: string; other: string }) => statementsApi.removeEdge(from, other),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['graph', projectId] }),
  });

  const addEdgeFromCanvasMutation = useMutation({
    mutationFn: ({ from, to, relation }: { from: string; to: string; relation: StatementRelation }) =>
      statementsApi.addEdge(from, to, relation),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['graph', projectId] }),
  });

  function deleteSelectedElements() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$('node:selected').forEach((n) => {
      if (n.data('status') !== 'locked') deleteStatementFromCanvasMutation.mutate(n.id());
    });
    cy.$('edge:selected').forEach((e) => {
      removeEdgeFromCanvasMutation.mutate({ from: e.data('source'), other: e.data('target') });
    });
  }

  // ── File drag/drop (PDFs anywhere on the page) ──────────────────────────────

  async function processUploadQueue(files: File[]) {
    const items: UploadQueueItem[] = files.map((f) => ({
      id: `${f.name}-${f.size}-${Date.now()}-${Math.random()}`,
      name: f.name,
      status: 'pending',
    }));
    setUploadQueue((q) => [...q, ...items]);

    for (let i = 0; i < files.length; i++) {
      const item = items[i];
      setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'uploading' } : x)));
      try {
        await papersApi.upload(files[i], projectId, {});
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'done' } : x)));
        queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      } catch (err) {
        const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? 'Upload failed';
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'error', error: message } : x)));
      }
    }
  }

  // Safety net: the browser's default action for a dropped file (anywhere in
  // the viewport, not just our drop zone) is to navigate to/open it. Blocking
  // dragover/drop at the window level guarantees that never happens while
  // this page is mounted, regardless of exactly which pane the drop lands on.
  useEffect(() => {
    const preventDefault = (e: DragEvent) => e.preventDefault();
    window.addEventListener('dragover', preventDefault);
    window.addEventListener('drop', preventDefault);
    return () => {
      window.removeEventListener('dragover', preventDefault);
      window.removeEventListener('drop', preventDefault);
    };
  }, []);

  function handlePageDragEnter(e: React.DragEvent) {
    e.preventDefault();
    if (!e.dataTransfer.types.includes('Files')) return;
    dragDepthRef.current += 1;
    setIsDragOver(true);
  }

  function handlePageDragOver(e: React.DragEvent) {
    e.preventDefault();
  }

  function handlePageDragLeave(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragOver(false);
  }

  function handlePageDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter(isDroppableFile);
    if (files.length > 0) void processUploadQueue(files);
  }

  // ── Passage drag/drop (reader pane → graph pane) ────────────────────────────

  function handleCanvasDragOver(e: React.DragEvent) {
    if (e.dataTransfer.types.includes(PASSAGE_MIME)) e.preventDefault();
  }

  function handleCanvasDrop(e: React.DragEvent) {
    const raw = e.dataTransfer.getData(PASSAGE_MIME);
    if (!raw) return; // an OS file drop, or something else — let it bubble to the page handler
    e.preventDefault();
    e.stopPropagation();
    const { sentenceId, text } = JSON.parse(raw) as { sentenceId: string; text: string };
    const targetNode = findStatementNodeAtPoint(e.clientX, e.clientY);
    if (targetNode) {
      nestEvidenceMutation.mutate({ statementId: targetNode.id(), sentenceId });
    } else {
      createFromPassageMutation.mutate({ text, sentenceId });
    }
  }

  // ── Reader/graph resize divider ─────────────────────────────────────────────

  function handleDividerMouseDown(e: React.MouseEvent) {
    e.preventDefault();
    resizingRef.current = true;
    const startX = e.clientX;
    const startWidth = readerWidth;

    function onMove(ev: MouseEvent) {
      if (!resizingRef.current) return;
      const next = startWidth + (ev.clientX - startX);
      setReaderWidth(Math.max(READER_MIN_WIDTH, Math.min(READER_MAX_WIDTH, next)));
    }
    function onUp() {
      resizingRef.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  // ── Keyboard shortcuts: W/E/R hold-to-connect, Delete/Backspace to remove ───
  // Must ignore keys while typing in a text input/textarea — this page puts a
  // graph canvas right next to editable text (assertion textarea, reader
  // pane), so a stray "we ran an experiment" must not trigger tension-drawing.
  useEffect(() => {
    function isTypingTarget(el: EventTarget | null): boolean {
      if (!(el instanceof HTMLElement)) return false;
      return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable;
    }

    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(e.target)) return;
      const relation = RELATION_KEYS[e.key.toLowerCase()];
      if (relation && !heldRelationKeyRef.current) {
        heldRelationKeyRef.current = relation;
        setHeldRelationKey(relation);
        cyRef.current?.autoungrabify(true);
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && !heldRelationKeyRef.current) {
        deleteSelectedElements();
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      const relation = RELATION_KEYS[e.key.toLowerCase()];
      if (relation && heldRelationKeyRef.current === relation) {
        heldRelationKeyRef.current = null;
        setHeldRelationKey(null);
        cyRef.current?.autoungrabify(false);
        cyRef.current?.nodes().removeClass('edge-drag-source');
        edgeDragSourceRef.current = null;
      }
    }
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cytoscape lifecycle ──────────────────────────────────────────────────────

  // Create cytoscape once on mount; event handlers are stable (setPanelMode is a stable setter)
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: CY_STYLE,
      layout: { name: 'preset' },
      wheelSensitivity: 0.3,
      minZoom: 0.08,
      maxZoom: 3,
    });
    cy.on('tap', 'node', (e: cytoscape.EventObject) => {
      const { id, kind } = e.target.data() as { id: string; kind: string };
      if (kind !== 'statement') return;
      setPanelMode({ kind: 'statement', id });
      setContextMenu(null);
      const rp = e.target.renderedPosition();
      setEvidencePopover({ statementId: id, x: rp.x, y: rp.y });
    });
    cy.on('tap', (e: cytoscape.EventObject) => {
      if (e.target === cy) {
        setPanelMode(null);
        setEvidencePopover(null);
        setContextMenu(null);
      }
    });
    cy.on('dbltap', (e: cytoscape.EventObject) => {
      if (e.target !== cy) return; // only empty-canvas double-clicks create
      setContextMenu(null);
      setQuickCreate({ x: e.renderedPosition.x, y: e.renderedPosition.y });
    });
    cy.on('cxttap', (e: cytoscape.EventObject) => {
      setEvidencePopover(null);
      setQuickCreate(null);
      const { x, y } = e.renderedPosition;
      if (e.target === cy) {
        setContextMenu({ x, y, kind: 'background' });
      } else if (e.target.isNode?.() && e.target.data('kind') === 'statement') {
        setContextMenu({ x, y, kind: 'node', nodeId: e.target.id() });
      } else if (e.target.isEdge?.()) {
        setContextMenu({ x, y, kind: 'edge', edgeFrom: e.target.data('source'), edgeTo: e.target.data('target') });
      }
    });
    // W/E/R hold + drag between two statement nodes draws a typed edge —
    // autoungrabify (toggled by the keydown/keyup effect) stops the source
    // node from just being dragged around while a relation key is held.
    cy.on('mousedown', 'node', (e: cytoscape.EventObject) => {
      const relation = heldRelationKeyRef.current;
      if (!relation || e.target.data('kind') !== 'statement') return;
      edgeDragSourceRef.current = { id: e.target.id(), relation };
      e.target.addClass('edge-drag-source');
    });
    cy.on('mouseup', (e: cytoscape.EventObject) => {
      const source = edgeDragSourceRef.current;
      if (!source) return;
      edgeDragSourceRef.current = null;
      cy.nodes().removeClass('edge-drag-source');
      const target = e.target;
      if (target?.isNode?.() && target.data('kind') === 'statement' && target.id() !== source.id) {
        addEdgeFromCanvasMutation.mutate({ from: source.id, to: target.id(), relation: source.relation });
      }
    });
    cy.on('render', () => setRenderTick((t) => t + 1));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Incrementally sync elements; only re-layout when nodes are added or removed
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;

    const next = buildElements(graph, nodeFilter);
    const nextIds = new Set(next.map((e) => e.data.id as string));
    const toRemove = cy.elements().filter((e) => !nextIds.has(e.id()));
    const toAdd = next.filter((e) => !cy.$(`#${e.data.id}`).length);
    const prevNodeCount = cy.nodes().length;

    cy.batch(() => {
      toRemove.remove();
      cy.add(toAdd);
      next.forEach((e) => {
        const el = cy.$(`#${e.data.id}`);
        if (el.length) el.data(e.data);
      });
    });

    const newNodeCount = cy.nodes().length;
    if (newNodeCount !== prevNodeCount) {
      const isFirst = prevNodeCount === 0;
      const saved = isFirst ? null : { zoom: cy.zoom(), pan: { ...cy.pan() } };
      cy.layout({ name: 'dagre', rankDir: 'BT', nodeSep: 40, rankSep: 70, padding: 50 } as cytoscape.LayoutOptions).run();
      if (isFirst) {
        cy.fit(undefined, 50);
      } else if (saved && !pendingSelectRef.current) {
        cy.zoom(saved.zoom);
        cy.pan(saved.pan);
      }
    }

    const pendingId = pendingSelectRef.current;
    if (pendingId) {
      const el = cy.$(`#${pendingId}`);
      if (el.length) {
        cy.elements().unselect();
        el.select();
        cy.animate({ fit: { eles: el, padding: 80 }, duration: 300 });
        pendingSelectRef.current = null;
      }
    }
  }, [graph, nodeFilter]);

  // Evidence-count badge positions — recomputed (via refs, inside an effect,
  // never during render) whenever the graph data changes or cytoscape fires
  // its own 'render' tick (pan/zoom/drag/layout).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) { setBadgePositions({}); return; }
    const positions: Record<string, { x: number; y: number }> = {};
    graph.nodes.filter((n) => n.evidence_count > 0).forEach((n) => {
      const el = cy.$id(n.id);
      if (!el.length) return;
      const bb = el.renderedBoundingBox();
      positions[n.id] = { x: bb.x2 - 8, y: bb.y1 - 6 };
    });
    setBadgePositions(positions);
  }, [graph, renderTick]);

  const nodeCount = graph
    ? { all: graph.nodes.length, questions: graph.nodes.filter((n) => n.type === 'question').length, statements: graph.nodes.filter((n) => n.type !== 'question').length }
    : null;

  const edgeCount = graph ? graph.edges.length : 0;

  return (
    <div
      className={styles.workspace}
      onDragEnter={handlePageDragEnter}
      onDragOver={handlePageDragOver}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
    >
      {/* ── Toolbar ── */}
      <div className={styles.toolbar}>
        <span className={styles.toolbarLabel}>Show</span>
        {(['all', 'statements', 'questions'] as NodeFilter[]).map((f) => (
          <button
            key={f}
            className={`${styles.filterChip} ${nodeFilter === f ? styles.active : ''}`}
            onClick={() => setNodeFilter(f)}
          >
            {f} {nodeCount ? `(${nodeCount[f]})` : ''}
          </button>
        ))}

        <div className={styles.toolbarSep} />

        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.fit(undefined, 40)}>
          Fit
        </button>
        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.reset()}>
          Reset zoom
        </button>

        <div className={styles.toolbarSep} />

        <div className={styles.graphModeToggle}>
          <button
            className={`${styles.graphModeBtn} ${graphViewMode === 'graph' ? styles.graphModeBtnActive : ''}`}
            onClick={() => setGraphViewMode('graph')}
          >
            Knowledge graph
          </button>
          <button
            className={`${styles.graphModeBtn} ${graphViewMode === 'backtrace' ? styles.graphModeBtnActive : ''}`}
            onClick={() => setGraphViewMode('backtrace')}
          >
            Backtrace
          </button>
        </div>

        <span className={styles.countBadge}>{edgeCount} edge{edgeCount !== 1 ? 's' : ''}</span>

        <div className={styles.toolbarSep} />

        <span className={styles.relationLegend}>
          {(['w', 'e', 'r'] as const).map((k) => (
            <span
              key={k}
              className={`${styles.relationLegendKey} ${heldRelationKey === RELATION_KEYS[k] ? styles.relationLegendKeyActive : ''}`}
            >
              {k.toUpperCase()} {relationLabel(RELATION_KEYS[k])}
            </span>
          ))}
          <span className={styles.relationLegendHint}>hold + drag between statements</span>
        </span>

        <div style={{ marginLeft: 'auto' }} />
        <button
          className={`${styles.toolbarBtn} ${panelMode?.kind === 'history' ? styles.active : ''}`}
          onClick={() => setPanelMode(panelMode?.kind === 'history' ? null : { kind: 'history' })}
        >
          History
        </button>
        <button
          className={`${styles.toolbarBtn} ${panelMode?.kind === 'new' ? styles.active : ''}`}
          onClick={() => setPanelMode(panelMode?.kind === 'new' ? null : { kind: 'new' })}
        >
          + New statement
        </button>
      </div>

      {/* ── Body: papers | reader | graph ── */}
      <div className={styles.workspaceBody}>
        <PapersSidebar
          projectId={projectId}
          papers={papers}
          papersLoading={papersLoading}
          selectedPaperId={selectedPaperId}
          onSelectPaper={setSelectedPaperId}
          isDragOver={isDragOver}
          uploadQueue={uploadQueue}
          onClearUploadQueue={() => setUploadQueue((q) => q.filter((x) => x.status === 'uploading' || x.status === 'pending'))}
        />

        <div className={styles.readerPane} style={{ width: readerWidth }}>
          <div className={styles.readerModeToggle}>
            <button
              className={`${styles.readerModeBtn} ${readerMode === 'read' ? styles.readerModeBtnActive : ''}`}
              onClick={() => setReaderMode('read')}
            >
              Read
            </button>
            <button
              className={`${styles.readerModeBtn} ${readerMode === 'find' ? styles.readerModeBtnActive : ''}`}
              onClick={() => setReaderMode('find')}
            >
              Find
            </button>
            {activeBacktrace && (
              <button
                className={`${styles.readerModeBtn} ${readerMode === 'backtrace' ? styles.readerModeBtnActive : ''}`}
                onClick={() => setReaderMode('backtrace')}
              >
                Backtrace
              </button>
            )}
          </div>
          {isDragOver ? (
            <div className={styles.emptyState} style={{ outline: '2px dashed var(--accent)', outlineOffset: -8 }}>
              <div className={styles.emptyTitle}>Drop to add</div>
              <div className={styles.emptyDesc}>Release to upload these PDFs to this project.</div>
            </div>
          ) : readerMode === 'find' ? (
            <FindPanel projectId={projectId} onStatementCreated={handleStatementCreated} />
          ) : readerMode === 'backtrace' && activeBacktrace ? (
            <BacktracePanel key={activeBacktrace.chainId} chainId={activeBacktrace.chainId} projectId={projectId} />
          ) : selectedPaperId ? (
            <ReaderPane
              paperId={selectedPaperId}
              projectId={projectId}
              onBacktrace={(sentenceId, text) => startBacktraceMutation.mutate({ sentenceId, text })}
              backtracePending={startBacktraceMutation.isPending}
              backtraceError={
                startBacktraceMutation.isError
                  ? ((startBacktraceMutation.error as { response?: { data?: { detail?: string } } })?.response?.data
                      ?.detail ?? 'Could not start backtrace')
                  : null
              }
            />
          ) : (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>Read</div>
              <div className={styles.emptyDesc}>
                Select a paper from the sidebar to read it, then drag passages onto the graph — or
                drag PDFs in anywhere on this page to add them.
              </div>
            </div>
          )}
        </div>

        <div
          className={styles.resizeDivider}
          onMouseDown={handleDividerMouseDown}
          role="separator"
          aria-orientation="vertical"
        />

        <div
          className={styles.canvasWrapper}
          onDragOver={handleCanvasDragOver}
          onDrop={handleCanvasDrop}
          onContextMenu={(e) => e.preventDefault()}
        >
          {/* Both layers stay mounted at all times — Cytoscape's creation
              effect only runs once, tied to its container existing at
              mount, so conditionally unmounting either canvas would break
              re-initialization on toggling back. Visibility is CSS-only. */}
          <div className={styles.canvasLayer} style={{ display: graphViewMode === 'graph' ? 'block' : 'none' }}>
          <div ref={containerRef} className={styles.cytoscapeContainer} />

          {/* Evidence-count badges — corner overlay per node, repositioned on every cytoscape render tick */}
          <div className={styles.badgeLayer}>
            {graph && graph.nodes
              .filter((n) => n.evidence_count > 0 && badgePositions[n.id])
              .map((n) => (
                <div
                  key={`badge-${n.id}`}
                  className={styles.evidenceBadge}
                  style={{ left: badgePositions[n.id].x, top: badgePositions[n.id].y }}
                  title={`${n.evidence_count} piece${n.evidence_count !== 1 ? 's' : ''} of evidence`}
                >
                  {n.evidence_count}
                </div>
              ))}
          </div>

          {graphLoading && (
            <div className={styles.loadingOverlay}>Loading graph…</div>
          )}

          {!graphLoading && graph && graph.nodes.length === 0 && (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>No statements yet</div>
              <div className={styles.emptyDesc}>
                Create one from the sidebar, double-click empty canvas, or drag a passage here from
                the paper you're reading.
              </div>
            </div>
          )}

          {evidencePopover && (
            <EvidencePopover
              statementId={evidencePopover.statementId}
              x={evidencePopover.x}
              y={evidencePopover.y}
              papers={papers}
              statements={graph?.nodes ?? []}
              onClose={() => setEvidencePopover(null)}
            />
          )}

          {quickCreate && (
            <QuickCreatePopup
              x={quickCreate.x}
              y={quickCreate.y}
              isPending={quickCreateMutation.isPending}
              onCreate={(text) => quickCreateMutation.mutate(text)}
              onClose={() => setQuickCreate(null)}
            />
          )}

          {contextMenu && (
            <CanvasContextMenu
              x={contextMenu.x}
              y={contextMenu.y}
              kind={contextMenu.kind}
              onClose={() => setContextMenu(null)}
              onCreateHere={() => {
                setQuickCreate({ x: contextMenu.x, y: contextMenu.y });
                setContextMenu(null);
              }}
              onDelete={() => {
                if (contextMenu.kind === 'node') {
                  deleteStatementFromCanvasMutation.mutate(contextMenu.nodeId);
                } else if (contextMenu.kind === 'edge') {
                  removeEdgeFromCanvasMutation.mutate({ from: contextMenu.edgeFrom, other: contextMenu.edgeTo });
                }
                setContextMenu(null);
              }}
            />
          )}

          {panelMode && (
            <RightPanel
              panelMode={panelMode}
              onClose={() => setPanelMode(null)}
              projectId={projectId}
              papers={papers}
              allStatements={graph?.nodes ?? []}
              onStatementCreated={handleStatementCreated}
            />
          )}
          </div>

          <div className={styles.canvasLayer} style={{ display: graphViewMode === 'backtrace' ? 'block' : 'none' }}>
            <BacktraceTree
              projectId={projectId}
              chainId={treeChainId}
              visible={graphViewMode === 'backtrace'}
              onChainLoaded={setTreeChainId}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
