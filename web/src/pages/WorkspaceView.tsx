import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import { api } from '../api/client';
import { bricksApi, type Brick, type BuildsOn, type RestsOn } from '../api/bricks';
import { papersApi, type Paper, type Section, type Sentence, firstAuthor } from '../api/papers';
import { synthesisApi, type Synthesis, type EvidenceFull } from '../api/syntheses';
import styles from './WorkspaceView.module.css';

cytoscape.use(dagre);

// ── Types ─────────────────────────────────────────────────────────────────────

interface BrickNode {
  id: string;
  project_id: string;
  type: string;
  status: string;
  assertion: string;
}

interface BuildsOnEdge { brick_id: string; parent_brick_id: string; ord: number }
interface RestsOnEdge  { brick_id: string; target_kind: string; target_id: string; role: string }
interface BrickGraph   { nodes: BrickNode[]; builds_on: BuildsOnEdge[]; rests_on: RestsOnEdge[] }

type PanelMode = null | { kind: 'paper'; id: string } | { kind: 'brick'; id: string } | { kind: 'synthesis' };
type NodeFilter = 'all' | 'questions' | 'bricks';

// ── Helpers ───────────────────────────────────────────────────────────────────

function trunc(s: string, n: number) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
function typeClass(type: string) {
  const known = ['observation', 'methods', 'foundation', 'synthesis', 'claim', 'question', 'other'];
  return known.includes(type) ? type : 'other';
}

// ── Canvas element builder ────────────────────────────────────────────────────

function buildElements(
  graph: BrickGraph,
  papers: Paper[],
  showAllPapers: boolean,
  nodeFilter: NodeFilter,
) {
  const visibleBricks = graph.nodes.filter((n) => {
    if (nodeFilter === 'questions') return n.type === 'question';
    if (nodeFilter === 'bricks')    return n.type !== 'question';
    return true;
  });
  const visibleBrickIds = new Set(visibleBricks.map((n) => n.id));

  const referencedPaperIds = new Set(
    graph.rests_on
      .filter((e) => e.target_kind === 'paper' && visibleBrickIds.has(e.brick_id))
      .map((e) => e.target_id),
  );
  const papersToShow = showAllPapers ? papers : papers.filter((p) => referencedPaperIds.has(p.id));
  const paperNodeIds = new Set(papersToShow.map((p) => p.id));

  return [
    ...papersToShow.map((p) => ({
      data: { id: p.id, kind: 'paper', label: trunc(p.title, 52), status: p.status },
    })),
    ...visibleBricks.map((n) => ({
      data: { id: n.id, kind: 'brick', label: trunc(n.assertion, 62), type: n.type, status: n.status },
    })),
    ...graph.builds_on
      .filter((e) => visibleBrickIds.has(e.brick_id) && visibleBrickIds.has(e.parent_brick_id))
      .map((e) => ({
        data: { id: `bo-${e.brick_id}-${e.parent_brick_id}`, source: e.brick_id, target: e.parent_brick_id, kind: 'builds_on' },
      })),
    ...graph.rests_on
      .filter((e) => e.target_kind === 'paper' && visibleBrickIds.has(e.brick_id) && paperNodeIds.has(e.target_id))
      .map((e) => ({
        data: { id: `ro-${e.brick_id}-${e.target_id}`, source: e.brick_id, target: e.target_id, kind: 'rests_on', role: e.role },
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
    selector: 'node[kind = "paper"]',
    style: {
      shape: 'rectangle', width: '210px', height: '58px',
      'background-color': '#1a1410', 'border-width': 1, 'border-color': '#5c4a30',
      color: '#c4a97a', 'font-size': '10px', 'text-max-width': '194px',
    },
  },
  {
    selector: 'node[kind = "paper"][status = "ghost"]',
    style: { 'border-style': 'dashed', 'border-color': '#3a3020', color: '#7a6a50' },
  },
  {
    selector: 'node[kind = "brick"]',
    style: {
      shape: 'roundrectangle', width: '180px', height: '52px',
      'background-color': '#1f1d18', 'border-width': 1, 'border-color': '#8a7a5c',
      'font-style': 'italic', 'text-max-width': '164px',
    },
  },
  {
    selector: 'node[kind = "brick"][status = "cemented"]',
    style: { 'background-color': '#1a221a', 'border-color': '#8aa57c', 'border-width': 1.5 },
  },
  {
    selector: 'node[kind = "brick"][type = "question"]',
    style: {
      'background-color': '#1a1a26', 'border-color': '#5c6b8a', 'font-style': 'normal',
    },
  },
  {
    selector: 'node[kind = "brick"][type = "question"][status = "resolved"]',
    style: { 'border-color': '#5a8a6a', 'background-color': '#1a231a' },
  },
  {
    selector: 'node:selected',
    style: { 'background-color': '#2a2a1e', 'border-color': '#c9a876', 'border-width': 2.5 },
  },
  {
    selector: 'edge[kind = "builds_on"]',
    style: {
      width: 1.5, 'line-color': '#3a3730', 'target-arrow-color': '#5f5c55',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    selector: 'edge[kind = "rests_on"]',
    style: {
      width: 1, 'line-style': 'dashed', 'line-color': '#3a3020',
      'target-arrow-color': '#5c4a30', 'target-arrow-shape': 'vee',
      'curve-style': 'bezier', 'arrow-scale': 0.8,
      label: 'data(role)', 'font-size': '9px', color: '#7a6050',
      'font-family': 'Inter, sans-serif', 'text-rotation': 'autorotate',
      'text-margin-y': -7, 'text-background-color': '#0f0e0b',
      'text-background-opacity': 1, 'text-background-padding': '2px',
    },
  },
  {
    selector: 'edge:selected',
    style: { 'line-color': '#8a7a5c', 'target-arrow-color': '#8a7a5c' },
  },
];

// ── Left sidebar ──────────────────────────────────────────────────────────────

function LeftSidebar({
  projectId, papers, papersLoading, onSelectPaper, onBrickCreated,
}: {
  projectId: string;
  papers: Paper[];
  papersLoading: boolean;
  onSelectPaper: (id: string) => void;
  onBrickCreated: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [addOpen, setAddOpen] = useState(false);
  const [doi, setDoi] = useState('');
  const [brickType, setBrickType] = useState('claim');
  const [brickText, setBrickText] = useState('');

  const ingestMutation = useMutation({
    mutationFn: () => papersApi.ingest(doi.trim(), projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setDoi('');
      setAddOpen(false);
    },
  });

  const createBrickMutation = useMutation({
    mutationFn: () => bricksApi.create(projectId, brickType, brickText.trim()),
    onSuccess: (brick) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setBrickText('');
      onBrickCreated(brick.id);
    },
  });

  return (
    <aside className={styles.sidebar}>
      <div className={styles.sidebarSection}>
        <div className={styles.sidebarSectionHeader}>
          <span>Papers</span>
          <span className={styles.sidebarCount}>{papers.length}</span>
          <button className={styles.sidebarAddBtn} onClick={() => setAddOpen((v) => !v)}>
            {addOpen ? '×' : '+'}
          </button>
        </div>

        {addOpen && (
          <div className={styles.addPaperForm}>
            <input
              className={styles.doiInput}
              value={doi}
              onChange={(e) => setDoi(e.target.value)}
              placeholder="DOI  e.g. 10.xxxx/xxxx"
              onKeyDown={(e) => e.key === 'Enter' && doi.trim() && ingestMutation.mutate()}
            />
            <button
              className={styles.ingestBtn}
              disabled={!doi.trim() || ingestMutation.isPending}
              onClick={() => ingestMutation.mutate()}
            >
              {ingestMutation.isPending ? '…' : 'Add'}
            </button>
            {ingestMutation.error && (
              <div className={styles.formError}>{String((ingestMutation.error as Error).message)}</div>
            )}
          </div>
        )}

        <div className={styles.paperList}>
          {papersLoading && <div className={styles.listHint}>Loading…</div>}
          {papers.map((p) => (
            <button key={p.id} className={styles.paperItem} onClick={() => onSelectPaper(p.id)}>
              <span className={`${styles.statusDot} ${styles[p.status]}`} />
              <span className={styles.paperTitle}>{trunc(p.title, 48)}</span>
            </button>
          ))}
          {!papersLoading && papers.length === 0 && (
            <div className={styles.listHint}>No papers yet — add one above.</div>
          )}
        </div>
      </div>

      <div className={styles.sidebarDivider} />

      <div className={styles.sidebarSection}>
        <div className={styles.sidebarSectionHeader}>New brick</div>
        <select
          className={styles.typeSelect}
          value={brickType}
          onChange={(e) => setBrickType(e.target.value)}
        >
          {['claim', 'observation', 'question', 'foundation', 'methods', 'synthesis', 'other'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <textarea
          className={styles.brickTextarea}
          value={brickText}
          onChange={(e) => setBrickText(e.target.value)}
          placeholder={brickType === 'question' ? 'State your question…' : 'State the claim or finding…'}
          rows={3}
        />
        <button
          className={styles.createBtn}
          disabled={brickText.trim().length < 3 || createBrickMutation.isPending}
          onClick={() => createBrickMutation.mutate()}
        >
          {createBrickMutation.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </aside>
  );
}

// ── Paper detail panel ────────────────────────────────────────────────────────

function PaperDetailPanel({ paperId, projectId }: { paperId: string; projectId: string }) {
  const queryClient = useQueryClient();
  const [expandAbstract, setExpandAbstract] = useState(false);
  const [openSection, setOpenSection] = useState<string | null>(null);

  const { data: paper } = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => papersApi.get(paperId),
  });

  const { data: sections } = useQuery({
    queryKey: ['sections', paperId],
    queryFn: () => papersApi.sections(paperId),
    enabled: paper?.status === 'full_text',
  });

  const { data: sentences } = useQuery({
    queryKey: ['sentences', paperId, openSection],
    queryFn: () => papersApi.sentences(paperId, openSection ?? undefined),
    enabled: !!openSection,
  });

  const retryMutation = useMutation({
    mutationFn: () => papersApi.retry(paperId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  if (!paper) return <div className={styles.panelLoading}>Loading…</div>;

  const author = firstAuthor(paper.authors_json);
  const meta = [author && `${author} et al.`, paper.year, paper.venue].filter(Boolean).join(' · ');

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>paper</div>
      <div className={styles.panelTitle}>{paper.title}</div>
      {meta && <div className={styles.panelMeta}>{meta}</div>}

      <div className={styles.paperStatusRow}>
        <span className={`${styles.statusDot} ${styles[paper.status]}`} />
        <span className={styles.paperStatusLabel}>{paper.status.replace('_', ' ')}</span>
        {paper.doi && <span className={styles.paperDoi}>{paper.doi}</span>}
      </div>

      {paper.status === 'failed' && (
        <button
          className={styles.retryBtn}
          onClick={() => retryMutation.mutate()}
          disabled={retryMutation.isPending}
        >
          {retryMutation.isPending ? 'Retrying…' : 'Retry ingestion'}
        </button>
      )}

      {paper.abstract && (
        <div className={styles.abstractSection}>
          <div className={styles.panelSectionLabel}>Abstract</div>
          <div className={`${styles.abstractText} ${expandAbstract ? '' : styles.abstractClamped}`}>
            {paper.abstract}
          </div>
          {paper.abstract.length > 280 && (
            <button className={styles.expandBtn} onClick={() => setExpandAbstract((v) => !v)}>
              {expandAbstract ? 'Collapse' : 'Expand'}
            </button>
          )}
        </div>
      )}

      {paper.status === 'full_text' && sections && sections.length > 0 && (
        <div className={styles.sectionsSection}>
          <div className={styles.panelSectionLabel}>Passages</div>
          {sections.map((sec: Section) => (
            <div key={sec.id} className={styles.sectionItem}>
              <button
                className={styles.sectionToggle}
                onClick={() => setOpenSection(openSection === sec.id ? null : sec.id)}
              >
                <span>{sec.label}</span>
                <span className={styles.sectionArrow}>{openSection === sec.id ? '▾' : '▸'}</span>
              </button>
              {openSection === sec.id && (
                <div className={styles.sentenceList}>
                  {sentences
                    ?.filter((s: Sentence) => s.section_id === sec.id)
                    .map((s: Sentence) => (
                      <div key={s.id} className={styles.sentence}>{s.text}</div>
                    ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {paper.status === 'ghost' && (
        <div className={styles.ghostNote}>
          Full text not available. Add a PDF to see passages.
        </div>
      )}
    </div>
  );
}

// ── Brick detail panel ────────────────────────────────────────────────────────

function AssertionEditor({ brick }: { brick: Brick }) {
  const [text, setText] = useState(brick.assertion);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutation = useMutation({
    mutationFn: (val: string) => bricksApi.update(brick.id, { assertion: val }),
    onSettled: () => setSaving(false),
  });

  useEffect(() => { setText(brick.assertion); }, [brick.id, brick.assertion]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    setSaving(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => mutation.mutate(val), 1000);
  }, [mutation]);

  const isLocked = brick.status === 'cemented';

  return (
    <div className={styles.assertionSection}>
      <textarea
        className={styles.assertionTextarea}
        value={text}
        onChange={handleChange}
        disabled={isLocked}
        placeholder={brick.type === 'question' ? 'State your question…' : 'State the claim or finding…'}
        rows={4}
      />
      <div className={`${styles.saveHint} ${saving ? styles.saveHintSaving : ''}`}>
        {saving ? 'Saving…' : isLocked ? 'Cemented — edit to reopen' : 'Auto-saved'}
      </div>
    </div>
  );
}

function BrickDetailPanel({
  brickId, projectId, allBricks, papers,
}: {
  brickId: string;
  projectId: string;
  allBricks: BrickNode[];
  papers: Paper[];
}) {
  const queryClient = useQueryClient();

  const { data: brick } = useQuery({
    queryKey: ['brick', brickId],
    queryFn: () => api.get<Brick>(`/bricks/${brickId}`).then((r) => r.data),
  });

  const { data: restsOn } = useQuery({
    queryKey: ['rests-on', brickId],
    queryFn: () => bricksApi.restsOn(brickId),
  });

  const { data: buildsOn } = useQuery({
    queryKey: ['builds-on', brickId],
    queryFn: () => bricksApi.buildsOn(brickId),
  });

  const [targetKind, setTargetKind] = useState<'paper' | 'brick' | 'passage' | 'dataset'>('paper');
  const [targetId, setTargetId] = useState('');
  const [role, setRole] = useState('');
  const [parentId, setParentId] = useState('');

  useEffect(() => { setTargetId(''); }, [targetKind]);

  const cementMutation = useMutation({
    mutationFn: () => bricksApi.cement(brickId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['brick', brickId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => bricksApi.delete(brickId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const statusMutation = useMutation({
    mutationFn: (status: string) => bricksApi.update(brickId, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['brick', brickId] }),
  });

  const addRestsOnMutation = useMutation({
    mutationFn: () => bricksApi.addRestsOn(brickId, targetKind, targetId, role || 'evidence'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rests-on', brickId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setTargetId('');
    },
  });

  const removeRestsOnMutation = useMutation({
    mutationFn: ({ kind, id }: { kind: string; id: string }) => bricksApi.removeRestsOn(brickId, kind, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rests-on', brickId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const addBuildsOnMutation = useMutation({
    mutationFn: () => bricksApi.addBuildsOn(brickId, parentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['builds-on', brickId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setParentId('');
    },
  });

  const removeBuildsOnMutation = useMutation({
    mutationFn: (pid: string) => bricksApi.removeBuildsOn(brickId, pid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['builds-on', brickId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  if (!brick) return <div className={styles.panelLoading}>Loading…</div>;

  const linkedKeys = new Set((restsOn ?? []).map((e: RestsOn) => `${e.target_kind}:${e.target_id}`));
  const availablePapers = papers.filter((p) => !linkedKeys.has(`paper:${p.id}`));
  const availableBricks = allBricks.filter((b) => b.id !== brickId && !linkedKeys.has(`brick:${b.id}`));
  const linkedBrickIds = new Set((buildsOn ?? []).map((e: BuildsOn) => e.parent_brick_id));
  const parentOptions = allBricks.filter((b) => b.id !== brickId && !linkedBrickIds.has(b.id));

  const isQuestion = brick.type === 'question';
  const needsUUIDInput = targetKind === 'passage' || targetKind === 'dataset';
  const canAddRestsOn = needsUUIDInput
    ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetId)
    : targetId.trim().length > 0;

  function restsOnLabel(e: RestsOn) {
    if (e.target_kind === 'paper') {
      const found = papers.find((p) => p.id === e.target_id);
      return found ? trunc(found.title, 50) : e.target_id;
    }
    if (e.target_kind === 'brick') {
      const found = allBricks.find((b) => b.id === e.target_id);
      return found ? trunc(found.assertion, 50) : e.target_id;
    }
    return `${e.target_kind} · ${e.target_id.slice(0, 8)}…`;
  }

  return (
    <div className={styles.panelContent}>
      <div className={styles.brickPanelHeader}>
        <span className={`${styles.typeBadge} ${styles[typeClass(brick.type)]}`}>{brick.type}</span>
        <span className={`${styles.statusDot} ${styles[brick.status]}`} />
        <span className={styles.brickStatus}>{brick.status}</span>

        {!isQuestion && brick.status === 'draft' && (
          <button
            className={styles.cementBtn}
            onClick={() => cementMutation.mutate()}
            disabled={cementMutation.isPending || brick.assertion.trim().length < 5}
          >
            Cement
          </button>
        )}
        {isQuestion && (
          <select
            className={styles.questionStatusSelect}
            value={brick.status}
            onChange={(e) => statusMutation.mutate(e.target.value)}
          >
            <option value="open">open</option>
            <option value="resolved">resolved</option>
            <option value="wontfix">won't fix</option>
          </select>
        )}
        {brick.status !== 'cemented' && (
          <button
            className={styles.deleteBtn}
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            ×
          </button>
        )}
      </div>

      <AssertionEditor brick={brick} />

      {/* Rests on */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Rests on</div>
        {(restsOn ?? []).map((e: RestsOn) => (
          <div key={`${e.target_kind}:${e.target_id}`} className={styles.edgeChip}>
            <span className={styles.edgeChipKind}>{e.target_kind}</span>
            <span className={styles.edgeChipText}>{restsOnLabel(e)}</span>
            <span className={styles.edgeChipRole}>{e.role}</span>
            <button
              className={styles.edgeRemoveBtn}
              onClick={() => removeRestsOnMutation.mutate({ kind: e.target_kind, id: e.target_id })}
            >×</button>
          </div>
        ))}
        <div className={styles.edgeAddRow}>
          <select className={styles.kindSelect} value={targetKind} onChange={(e) => setTargetKind(e.target.value as typeof targetKind)}>
            <option value="paper">paper</option>
            <option value="brick">brick</option>
            <option value="passage">passage</option>
            <option value="dataset">dataset</option>
          </select>
          {targetKind === 'paper' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availablePapers.map((p) => (
                <option key={p.id} value={p.id}>{trunc(p.title, 40)}</option>
              ))}
            </select>
          ) : targetKind === 'brick' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availableBricks.map((b) => (
                <option key={b.id} value={b.id}>{trunc(b.assertion, 40)}</option>
              ))}
            </select>
          ) : (
            <input className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="UUID" />
          )}
          <input className={styles.roleInput} value={role} onChange={(e) => setRole(e.target.value)} placeholder="role" />
          <button
            className={styles.edgeAddBtn}
            disabled={!canAddRestsOn || addRestsOnMutation.isPending}
            onClick={() => addRestsOnMutation.mutate()}
          >+</button>
        </div>
      </div>

      {/* Builds on */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Builds on</div>
        {(buildsOn ?? []).map((e: BuildsOn) => {
          const parent = allBricks.find((b) => b.id === e.parent_brick_id);
          return (
            <div key={e.parent_brick_id} className={styles.edgeChip}>
              <span className={styles.edgeChipKind}>brick</span>
              <span className={styles.edgeChipText}>{parent ? trunc(parent.assertion, 50) : e.parent_brick_id.slice(0, 8)}</span>
              <button className={styles.edgeRemoveBtn} onClick={() => removeBuildsOnMutation.mutate(e.parent_brick_id)}>×</button>
            </div>
          );
        })}
        <div className={styles.edgeAddRow}>
          <select className={styles.targetSelect} style={{ flex: 1 }} value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">— select parent brick —</option>
            {parentOptions.map((b) => (
              <option key={b.id} value={b.id}>[{b.type}] {trunc(b.assertion, 40)}</option>
            ))}
          </select>
          <button
            className={styles.edgeAddBtn}
            disabled={!parentId || addBuildsOnMutation.isPending}
            onClick={() => addBuildsOnMutation.mutate()}
          >+</button>
        </div>
      </div>

      <div className={styles.brickMeta}>
        Drafted {formatDate(brick.drafted_at)}
        {brick.cemented_at && ` · Cemented ${formatDate(brick.cemented_at)}`}
        {brick.built_from_synthesis_id && ' · From synthesis'}
      </div>
    </div>
  );
}

// ── Synthesis panel ───────────────────────────────────────────────────────────

interface TraceEvent { kind: string; ord?: number; payload?: Record<string, unknown> }

function stepLabel(kind: string): string {
  const map: Record<string, string> = {
    query_interp: 'interpreting claim', retrieval: 'retrieving passages',
    extract: 'extracting evidence', signal: 'identifying signals', verify: 'verifying citations',
  };
  return map[kind] ?? kind;
}

function LiveTrace({ synthesisId, onDone }: { synthesisId: string; onDone: () => void }) {
  const [steps, setSteps] = useState<TraceEvent[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new WebSocket(synthesisApi.streamUrl(synthesisId));
    ws.onmessage = (e) => {
      const event: TraceEvent = JSON.parse(e.data);
      if (event.kind === 'done' || event.kind === 'failed') { onDone(); ws.close(); return; }
      setSteps((prev) => {
        if (event.ord !== undefined && prev.some((s) => s.ord === event.ord)) return prev;
        return [...prev, event];
      });
    };
    return () => { if (ws.readyState < 2) ws.close(); };
  }, [synthesisId, onDone]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [steps]);

  return (
    <div className={styles.liveTrace}>
      {steps.map((s, i) => (
        <div key={i} className={styles.traceStep}>
          <span className={styles.traceKind}>{stepLabel(s.kind)}</span>
          {s.payload && (
            <span className={styles.traceText}>
              {Object.entries(s.payload)
                .filter(([k]) => ['query', 'n_hits', 'n_passages', 'rate'].includes(k))
                .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')}
            </span>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function SynthesisPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [claim, setClaim] = useState('');
  const [corpus, setCorpus] = useState('project');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: syntheses } = useQuery({
    queryKey: ['syntheses', projectId],
    queryFn: () => synthesisApi.listForProject(projectId),
    refetchInterval: (q) => q.state.data?.some((s: Synthesis) => s.status === 'running') ? 3000 : false,
  });

  const { data: activeSynth } = useQuery({
    queryKey: ['synthesis', selectedId],
    queryFn: () => synthesisApi.get(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (q) => q.state.data?.status === 'running' ? 2000 : false,
  });

  const { data: evidence } = useQuery({
    queryKey: ['evidence-full', selectedId],
    queryFn: () => synthesisApi.evidenceFull(selectedId!),
    enabled: !!selectedId && activeSynth?.status === 'done',
  });

  const createMutation = useMutation({
    mutationFn: () => synthesisApi.create(claim.trim(), projectId, corpus),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
      setClaim('');
      setSelectedId(s.id);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => synthesisApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
      setSelectedId(null);
    },
  });

  const handleDone = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['synthesis', selectedId] });
    queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
    queryClient.invalidateQueries({ queryKey: ['evidence-full', selectedId] });
  }, [queryClient, selectedId, projectId]);

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>synthesis</div>

      <div className={styles.synthForm}>
        <textarea
          className={styles.synthClaimInput}
          value={claim}
          onChange={(e) => setClaim(e.target.value)}
          placeholder="What relationship or mechanism are you trying to establish?"
          rows={3}
        />
        <div className={styles.synthFormRow}>
          <select className={styles.corpusSelect} value={corpus} onChange={(e) => setCorpus(e.target.value)}>
            <option value="project">Project papers</option>
            <option value="global">Global corpus</option>
          </select>
          <button
            className={styles.runBtn}
            disabled={claim.trim().length < 10 || createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? 'Starting…' : 'Run'}
          </button>
        </div>
        {createMutation.error && (
          <div className={styles.formError}>{(createMutation.error as Error).message}</div>
        )}
      </div>

      {syntheses && syntheses.length > 0 && (
        <div className={styles.synthList}>
          <div className={styles.panelSectionLabel}>Past syntheses</div>
          {syntheses.map((s: Synthesis) => (
            <button
              key={s.id}
              className={`${styles.synthItem} ${s.id === selectedId ? styles.synthItemActive : ''}`}
              onClick={() => setSelectedId(s.id === selectedId ? null : s.id)}
            >
              <span className={`${styles.statusDot} ${styles[s.status]}`} />
              <span className={styles.synthItemClaim}>{trunc(s.claim_text, 60)}</span>
            </button>
          ))}
        </div>
      )}

      {activeSynth && (
        <div className={styles.synthDetail}>
          <div className={styles.synthDetailHeader}>
            <span className={`${styles.statusDot} ${styles[activeSynth.status]}`} />
            <span className={styles.synthStatus}>
              {activeSynth.status === 'running' && 'Running…'}
              {activeSynth.status === 'done' && 'Done'}
              {activeSynth.status === 'failed' && 'Failed'}
            </span>
            {activeSynth.verification_rate !== null && (
              <span className={styles.vrBadge}>
                {(activeSynth.verification_rate * 100).toFixed(0)}% verified
              </span>
            )}
            <button
              className={styles.deleteBtn}
              onClick={() => deleteMutation.mutate(activeSynth.id)}
              disabled={deleteMutation.isPending}
            >×</button>
          </div>

          <div className={styles.synthClaim}>{activeSynth.claim_text}</div>

          {activeSynth.status === 'running' && (
            <LiveTrace synthesisId={activeSynth.id} onDone={handleDone} />
          )}

          {evidence && evidence.length > 0 && (
            <div className={styles.evidenceList}>
              <div className={styles.panelSectionLabel}>
                Evidence · {evidence.length} passage{evidence.length !== 1 ? 's' : ''}
              </div>
              {evidence.map((ev: EvidenceFull) => (
                <div key={ev.passage_id} className={`${styles.evidenceCard} ${styles[ev.role] ?? ''}`}>
                  <div className={styles.evidenceHeader}>
                    <span className={styles.evidencePaper}>{trunc(ev.paper_title, 40)}</span>
                    {ev.paper_year && <span className={styles.evidenceYear}>{ev.paper_year}</span>}
                    <span className={styles.evidenceRole}>{ev.role}</span>
                    {ev.verified && <span className={styles.verifiedDot} title="verified" />}
                  </div>
                  <div className={styles.evidenceQuote}>{ev.verbatim_text}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────

function RightPanel({
  panelMode, onClose, projectId, papers, allBricks,
}: {
  panelMode: Exclude<PanelMode, null>;
  onClose: () => void;
  projectId: string;
  papers: Paper[];
  allBricks: BrickNode[];
}) {
  return (
    <div className={styles.rightPanel}>
      <div className={styles.rightPanelClose}>
        <button className={styles.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div className={styles.rightPanelScroll}>
        {panelMode.kind === 'paper' && (
          <PaperDetailPanel paperId={panelMode.id} projectId={projectId} />
        )}
        {panelMode.kind === 'brick' && (
          <BrickDetailPanel
            brickId={panelMode.id}
            projectId={projectId}
            allBricks={allBricks}
            papers={papers}
          />
        )}
        {panelMode.kind === 'synthesis' && (
          <SynthesisPanel projectId={projectId} />
        )}
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function WorkspaceView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId' });
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const pendingSelectRef = useRef<{ kind: 'paper' | 'brick'; id: string } | null>(null);

  const [panelMode, setPanelMode] = useState<PanelMode>(null);
  const [showAllPapers, setShowAllPapers] = useState(false);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>('all');

  const { data: graph, isLoading: graphLoading } = useQuery({
    queryKey: ['graph', projectId],
    queryFn: () => api.get<BrickGraph>(`/projects/${projectId}/graph`).then((r) => r.data),
  });

  const { data: papers = [], isLoading: papersLoading } = useQuery({
    queryKey: ['papers', projectId],
    queryFn: () => papersApi.listForProject(projectId),
  });

  const handleSelectNode = useCallback((kind: 'paper' | 'brick', id: string) => {
    setPanelMode({ kind, id });
    const cy = cyRef.current;
    if (cy && cy.$(`#${id}`).length) {
      cy.elements().unselect();
      cy.$(`#${id}`).select();
      cy.animate({ fit: { eles: cy.$(`#${id}`), padding: 80 }, duration: 300 });
    }
  }, []);

  const handleBrickCreated = useCallback((id: string) => {
    pendingSelectRef.current = { kind: 'brick', id };
    setPanelMode({ kind: 'brick', id });
  }, []);

  // Build / rebuild cytoscape whenever data or filters change
  useEffect(() => {
    if (!containerRef.current || !graph) return;
    const elements = buildElements(graph, papers, showAllPapers, nodeFilter);

    if (cyRef.current) { cyRef.current.destroy(); cyRef.current = null; }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: CY_STYLE,
      layout: {
        name: 'dagre',
        rankDir: 'BT',
        nodeSep: 40,
        rankSep: 70,
        padding: 50,
      } as cytoscape.LayoutOptions,
      wheelSensitivity: 0.3,
      minZoom: 0.08,
      maxZoom: 3,
    });

    cy.on('tap', 'node', (e: cytoscape.EventObject) => {
      const { id, kind } = e.target.data() as { id: string; kind: 'paper' | 'brick' };
      if (kind === 'paper' || kind === 'brick') setPanelMode({ kind, id });
    });

    cy.on('tap', (e: cytoscape.EventObject) => {
      if (e.target === cy) setPanelMode(null);
    });

    cyRef.current = cy;

    // Restore or apply pending selection
    const pending = pendingSelectRef.current;
    const activeId = pending?.id ?? (panelMode && panelMode.kind !== 'synthesis' ? panelMode.id : null);
    if (activeId && cy.$(`#${activeId}`).length) {
      cy.$(`#${activeId}`).select();
      if (pending) {
        cy.animate({ fit: { eles: cy.$(`#${activeId}`), padding: 80 }, duration: 300 });
        pendingSelectRef.current = null;
      }
    }

    return () => { cy.destroy(); cyRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, papers, showAllPapers, nodeFilter]);

  const nodeCount = graph
    ? { all: graph.nodes.length, questions: graph.nodes.filter((n) => n.type === 'question').length, bricks: graph.nodes.filter((n) => n.type !== 'question').length }
    : null;

  const edgeCount = graph ? graph.builds_on.length + graph.rests_on.filter((e) => e.target_kind === 'paper').length : 0;

  return (
    <div className={styles.workspace}>
      {/* ── Toolbar ── */}
      <div className={styles.toolbar}>
        <span className={styles.toolbarLabel}>Show</span>
        {(['all', 'bricks', 'questions'] as NodeFilter[]).map((f) => (
          <button
            key={f}
            className={`${styles.filterChip} ${nodeFilter === f ? styles.active : ''}`}
            onClick={() => setNodeFilter(f)}
          >
            {f} {nodeCount ? `(${nodeCount[f]})` : ''}
          </button>
        ))}

        <div className={styles.toolbarSep} />

        <label className={styles.toggleLabel}>
          <input
            type="checkbox"
            checked={showAllPapers}
            onChange={(e) => setShowAllPapers(e.target.checked)}
          />
          All papers
        </label>

        <div className={styles.toolbarSep} />

        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.fit(undefined, 40)}>
          Fit
        </button>
        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.reset()}>
          Reset zoom
        </button>

        <span className={styles.countBadge}>{edgeCount} edge{edgeCount !== 1 ? 's' : ''}</span>

        <div style={{ marginLeft: 'auto' }} />
        <button
          className={`${styles.synthBtn} ${panelMode?.kind === 'synthesis' ? styles.synthBtnActive : ''}`}
          onClick={() => setPanelMode(panelMode?.kind === 'synthesis' ? null : { kind: 'synthesis' })}
        >
          Synthesis
        </button>
      </div>

      {/* ── Body ── */}
      <div className={styles.workspaceBody}>
        <LeftSidebar
          projectId={projectId}
          papers={papers}
          papersLoading={papersLoading}
          onSelectPaper={(id) => handleSelectNode('paper', id)}
          onBrickCreated={handleBrickCreated}
        />

        <div className={styles.canvasWrapper}>
          <div ref={containerRef} className={styles.cytoscapeContainer} />

          {(graphLoading || papersLoading) && (
            <div className={styles.loadingOverlay}>Loading workspace…</div>
          )}

          {!graphLoading && graph && graph.nodes.length === 0 && papers.length === 0 && (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>Workspace is empty</div>
              <div className={styles.emptyDesc}>Add papers and create bricks using the sidebar.</div>
            </div>
          )}

          {panelMode && (
            <RightPanel
              panelMode={panelMode}
              onClose={() => setPanelMode(null)}
              projectId={projectId}
              papers={papers}
              allBricks={graph?.nodes ?? []}
            />
          )}
        </div>
      </div>
    </div>
  );
}
