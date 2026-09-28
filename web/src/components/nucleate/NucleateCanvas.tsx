import { useCallback, useMemo, useState } from 'react';
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getStraightPath,
  useInternalNode,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  NODE_RUN_STATUSES,
  type CanvasNode,
  type ExpansionEvent,
  type NucleusDetail,
  type SemanticEdge,
} from '../../api/nucleate';
import { layout, type Pos } from './layout';
import { nodeLabel } from './labels';
import { isUnreviewed, RELATION_LABELS } from './relations';
import styles from './NucleateCanvas.module.css';

/*
 * The Nucleate canvas (handoff Part IV): the nucleus pinned at the centre,
 * papers as document icons around it, lineage edges between them.
 *
 * Three kinds of text never look alike, at any zoom: the archivist's
 * unreviewed statements (blue italics), text a person owns (plain), and
 * verbatim quotes (upright, muted, in quotation marks with a rule).
 */

// Below this zoom a node shows author, year and a one-line relevance
// statement; at or above it, its verbatim evidence too.
const DETAIL_ZOOM = 0.95;

const SOURCE_NAMES: Record<string, string> = {
  library: 'your library', openalex: 'OpenAlex', web: 'the web',
};

type NucleusFlowNode = Node<{ question: string }, 'nucleus'>;
type PaperFlowNode = Node<{ node: CanvasNode; runStatus: string | null }, 'paper'>;
type LineageFlowEdge = Edge<{ title: string }, 'lineage'>;
type SemanticFlowEdge = Edge<{
  edge: SemanticEdge;
  /** How far this curve bows out: relations between the same two papers fan
   * out rather than drawing over each other. */
  bend: number;
  title: string;
  onSelect: (id: string) => void;
}, 'semantic'>;

function rootId(nucleusId: string) {
  return `nucleus:${nucleusId}`;
}

// ── Nodes ────────────────────────────────────────────────────────────────────

function CentreHandles() {
  // Edges run centre to centre, so both handles sit invisibly in the middle.
  return (
    <>
      <Handle type="target" position={Position.Top} className={styles.handle} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} className={styles.handle} isConnectable={false} />
    </>
  );
}

function NucleusNodeView({ data }: NodeProps<NucleusFlowNode>) {
  return (
    <div className={styles.nucleus}>
      <div className={styles.nucleusKicker}>Nucleus</div>
      <div className={styles.nucleusQuestion}>{data.question}</div>
      <CentreHandles />
    </div>
  );
}

const RUN_STATUS_COPY: Record<string, string> = {
  fetching: 'Fetching…',
  extracting: 'Extracting…',
  verifying: 'Verifying…',
  needs_full_text: 'Needs full text',
};

function PaperNodeView({ data, selected }: NodeProps<PaperFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const { node, runStatus } = data;
  const { review, paper } = node;
  const detailed = zoom >= DETAIL_ZOOM;

  const ghost = node.fulltext === 'user_must_supply';
  const arriving = runStatus !== null && runStatus !== 'ready';
  const aiText = review.relevance_provenance === 'ai_proposed';
  const abstractOnly = node.evidence.length > 0 && node.evidence.every((e) => e.source === 'abstract');

  if (review.status === 'rejected') {
    // Collapsed, and kept: a rejection is steering signal, not a deletion.
    return (
      <div className={styles.rejected} data-selected={selected} title={review.rejection_reason ?? 'Rejected'}>
        {nodeLabel(node)}
        <CentreHandles />
      </div>
    );
  }

  return (
    <div
      className={styles.doc}
      data-status={review.status}
      data-ghost={ghost}
      data-arriving={arriving}
      data-selected={selected}
    >
      <div className={styles.docHeader}>
        <span className={styles.docLabel}>{nodeLabel(node)}</span>
        {review.status === 'pinned' && <span className={styles.tagPinned}>Pinned</span>}
        {node.other_nucleus_ids.length > 0 && (
          <span
            className={styles.tagCross}
            title={`Also in ${node.other_nucleus_ids.length} other nucle${node.other_nucleus_ids.length === 1 ? 'us' : 'i'}`}
          >
            +{node.other_nucleus_ids.length} nucle{node.other_nucleus_ids.length === 1 ? 'us' : 'i'}
          </span>
        )}
        {arriving && runStatus && RUN_STATUS_COPY[runStatus] && (
          <span className={styles.tagRun}>{RUN_STATUS_COPY[runStatus]}</span>
        )}
      </div>

      {review.relevance_text ? (
        <p className={`${styles.relevance} ${aiText ? styles.aiText : styles.humanText}`}
           data-clamped={!detailed}>
          {review.relevance_text}
        </p>
      ) : (
        <p className={styles.noStatement}>
          {ghost ? 'Needs full text — no evidence yet.' : arriving ? 'Working on it…' : 'No relevance statement.'}
        </p>
      )}

      {abstractOnly && (
        <div className={styles.docFlags}>
          <span className={styles.flagAbstract}>From abstract</span>
        </div>
      )}

      {detailed && node.evidence.map((e) => (
        <blockquote key={e.id} className={styles.quote}>
          “{e.verbatim_text}”
          <span className={styles.quoteSource}>{e.source === 'abstract' ? 'abstract' : 'full text'}</span>
        </blockquote>
      ))}
      {detailed && <div className={styles.docTitle}>{paper.title}</div>}
      <CentreHandles />
    </div>
  );
}

// ── Edges ────────────────────────────────────────────────────────────────────

function LineageEdgeView({ sourceX, sourceY, targetX, targetY, data, id }: EdgeProps<LineageFlowEdge>) {
  const [path] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  return (
    <>
      <BaseEdge id={id} path={path} className={styles.lineage} />
      {/* A wide invisible stroke so the edge is easy to hover, carrying the
          relation and its provenance as a native tooltip. */}
      <path d={path} className={styles.edgeHit}>
        <title>{data?.title}</title>
      </path>
    </>
  );
}

/** Where a line from a node's centre towards `toward` leaves its box. */
function boxExit(cx: number, cy: number, w: number, h: number, toward: Pos, pad = 4): Pos {
  const dx = toward.x - cx;
  const dy = toward.y - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const t = Math.min(
    dx !== 0 ? (w / 2 + pad) / Math.abs(dx) : Infinity,
    dy !== 0 ? (h / 2 + pad) / Math.abs(dy) : Infinity,
  );
  return { x: cx + dx * t, y: cy + dy * t };
}

/**
 * A relation A → B (D-N21): a curved arrow, bowed to one side so it never sits
 * on the straight lineage line, ending at B's edge rather than its centre so
 * the arrowhead isn't hidden under the card. Unreviewed: dashed, AI blue,
 * italic label. Reviewed or yours: solid and neutral.
 */
function SemanticEdgeView({ id, source, target, data, selected }: EdgeProps<SemanticFlowEdge>) {
  const from = useInternalNode(source);
  const to = useInternalNode(target);
  if (!from || !to || !data) return null;
  const size = (n: typeof from) => ({ w: n.measured.width ?? 240, h: n.measured.height ?? 90 });
  const fs = size(from);
  const ts = size(to);
  const a = { x: from.internals.positionAbsolute.x + fs.w / 2, y: from.internals.positionAbsolute.y + fs.h / 2 };
  const b = { x: to.internals.positionAbsolute.x + ts.w / 2, y: to.internals.positionAbsolute.y + ts.h / 2 };

  // Control point: off the midpoint, perpendicular to A→B. The side follows
  // the direction, so B → A bows the other way from A → B.
  const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const nx = -(b.y - a.y) / len;
  const ny = (b.x - a.x) / len;
  const c = { x: (a.x + b.x) / 2 + nx * data.bend, y: (a.y + b.y) / 2 + ny * data.bend };

  const start = boxExit(a.x, a.y, fs.w, fs.h, c);
  const end = boxExit(b.x, b.y, ts.w, ts.h, c, 6);
  const path = `M ${start.x} ${start.y} Q ${c.x} ${c.y} ${end.x} ${end.y}`;

  // Arrowhead along the curve's final direction (control point → end).
  const ang = Math.atan2(end.y - c.y, end.x - c.x);
  const head = 9;
  const wing = (s: number) => `${end.x - head * Math.cos(ang + s)} ${end.y - head * Math.sin(ang + s)}`;
  const arrow = `M ${wing(0.45)} L ${end.x} ${end.y} L ${wing(-0.45)}`;

  // The label sits on the curve itself, at its midpoint.
  const mid = { x: (start.x + 2 * c.x + end.x) / 4, y: (start.y + 2 * c.y + end.y) / 4 };
  const ai = isUnreviewed(data.edge);

  return (
    <>
      <path d={path} className={styles.semantic} data-ai={ai} data-selected={selected} fill="none" />
      <path d={arrow} className={styles.semanticHead} data-ai={ai} data-selected={selected} fill="none" />
      <path d={path} className={styles.edgeHitClickable} onClick={() => data.onSelect(id)}>
        <title>{data.title}</title>
      </path>
      <EdgeLabelRenderer>
        <button
          // nodrag/nopan: a click on the label is a click, not the start of
          // a pan of the canvas underneath.
          className={`${styles.semanticLabel} nodrag nopan`}
          data-ai={ai}
          data-selected={selected}
          style={{ transform: `translate(-50%, -50%) translate(${mid.x}px, ${mid.y}px)` }}
          onClick={() => data.onSelect(id)}
          title={data.title}
        >
          {RELATION_LABELS[data.edge.relation]}
        </button>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes = { nucleus: NucleusNodeView, paper: PaperNodeView };
const edgeTypes = { lineage: LineageEdgeView, semantic: SemanticEdgeView };

// ── The canvas ───────────────────────────────────────────────────────────────

interface CanvasProps {
  detail: NucleusDetail;
  events: ExpansionEvent[];
  running: boolean;
  selected: string[];
  onSelectionChange: (ids: string[]) => void;
  selectedEdge: string | null;
  onEdgeSelect: (id: string) => void;
}

function Canvas({
  detail, events, running, selected, onSelectionChange, selectedEdge, onEdgeSelect,
}: CanvasProps) {
  const root = rootId(detail.nucleus.id);
  const ids = useMemo(() => [root, ...detail.nodes.map((n) => n.node_id)], [root, detail.nodes]);
  const links = useMemo(
    () => detail.lineage.map((l) => ({
      source: l.from_kind === 'nucleus' ? root : l.from_node_id!,
      target: l.to_node_id,
    })),
    [detail.lineage, root],
  );

  // Positions persist across data refreshes: only nodes new since the last
  // layout are placed, so arrivals never disturb what's already on screen.
  const idsKey = [...ids].sort().join(',');
  const [laid, setLaid] = useState(() => ({ key: idsKey, pos: layout(ids, links, new Map(), root) }));
  if (laid.key !== idsKey) {
    setLaid({ key: idsKey, pos: layout(ids, links, laid.pos, root) });
  }

  // Each node's own status during a run: its most recent node event.
  const runStatus = useMemo(() => {
    const m = new Map<string, string>();
    if (!running) return m;
    for (const e of events) {
      if (e.node_id && NODE_RUN_STATUSES.includes(e.status)) m.set(e.node_id, e.status);
    }
    return m;
  }, [events, running]);

  const labelOf = useMemo(() => {
    const m = new Map<string, string>(detail.nodes.map((n) => [n.node_id, nodeLabel(n)]));
    m.set(root, 'the nucleus question');
    return m;
  }, [detail.nodes, root]);
  const promptText = useMemo(
    () => new Map(detail.prompts.map((p) => [p.id, p.text])), [detail.prompts],
  );

  const center = (id: string, w: number, h: number): Pos => {
    const p = laid.pos.get(id) ?? { x: 0, y: 0 };
    return { x: p.x - w / 2, y: p.y - h / 2 };
  };

  const nodes: (NucleusFlowNode | PaperFlowNode)[] = [
    {
      id: root, type: 'nucleus', position: center(root, 260, 110), draggable: false,
      selectable: false, data: { question: detail.nucleus.question },
    },
    ...detail.nodes.map((n): PaperFlowNode => ({
      id: n.node_id, type: 'paper', position: center(n.node_id, 240, 90),
      selected: selected.includes(n.node_id),
      data: { node: n, runStatus: runStatus.get(n.node_id) ?? null },
    })),
  ];

  const lineageEdges: LineageFlowEdge[] = detail.lineage.map((l) => {
    const source = l.from_kind === 'nucleus' ? root : l.from_node_id!;
    const how = l.prompt_id
      ? `by “${promptText.get(l.prompt_id) ?? 'a prompt'}” · via ${SOURCE_NAMES[l.retrieval_source ?? ''] ?? 'the archivist'}`
      : 'added by you';
    return {
      id: l.id, source, target: l.to_node_id, type: 'lineage', selectable: false,
      data: { title: `Lineage — found from ${labelOf.get(source) ?? 'a paper'} ${how}` },
    };
  });

  // Relations: rejected ones are hidden (kept in the drawer), and so is any
  // touching a rejected paper, which is collapsed on the canvas.
  const visible = new Set(detail.nodes.filter((n) => n.review.status !== 'rejected').map((n) => n.node_id));
  const fan = new Map<string, number>();
  const semanticEdges: SemanticFlowEdge[] = detail.semantic_edges
    .filter((e) => !e.rejected && visible.has(e.from_node_id) && visible.has(e.to_node_id))
    .map((e) => {
      const pair = [e.from_node_id, e.to_node_id].sort().join('|');
      const k = fan.get(pair) ?? 0;
      fan.set(pair, k + 1);
      const who = isUnreviewed(e) ? 'proposed by the archivist, not yet reviewed' : 'reviewed';
      return {
        id: e.id, source: e.from_node_id, target: e.to_node_id, type: 'semantic',
        selected: e.id === selectedEdge, selectable: false,
        data: {
          edge: e, bend: 46 + 26 * k, onSelect: onEdgeSelect,
          title: `${labelOf.get(e.from_node_id)} ${RELATION_LABELS[e.relation]} ${labelOf.get(e.to_node_id)} — ${who}`,
        },
      };
    });
  const edges: (LineageFlowEdge | SemanticFlowEdge)[] = [...lineageEdges, ...semanticEdges];

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    let next: string[] | null = null;
    for (const c of changes) {
      if (c.type === 'position' && c.position && c.id !== root) {
        const { x, y } = c.position;
        setLaid((prev) => {
          const pos = new Map(prev.pos);
          pos.set(c.id, { x: x + 120, y: y + 45 });
          return { ...prev, pos };
        });
      } else if (c.type === 'select' && c.id !== root) {
        next ??= [...selected];
        next = c.selected ? [...new Set([...next, c.id])] : next.filter((id) => id !== c.id);
      }
    }
    if (next) onSelectionChange(next);
  }, [root, selected, onSelectionChange]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.2}
      maxZoom={1.8}
      multiSelectionKeyCode={['Meta', 'Shift', 'Control']}
      nodesConnectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={28} size={1} className={styles.background} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

/** The canvas for one nucleus. Remounted per nucleus, so switching gives a
 * fresh layout and a fitted view rather than the previous one's viewport. */
export default function NucleateCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider key={props.detail.nucleus.id}>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
