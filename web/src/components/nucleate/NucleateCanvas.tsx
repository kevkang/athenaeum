import { useCallback, useMemo, useState } from 'react';
import {
  Background,
  BaseEdge,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getStraightPath,
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
} from '../../api/nucleate';
import { layout, type Pos } from './layout';
import { nodeLabel } from './labels';
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
  const unsupported = node.audit !== null && !node.audit.supported;

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

      {(unsupported || abstractOnly) && (
        <div className={styles.docFlags}>
          {unsupported && (
            <span className={styles.flagUnsupported} title={node.audit?.rationale}>
              ✕ Not supported by its evidence
            </span>
          )}
          {abstractOnly && <span className={styles.flagAbstract}>From abstract</span>}
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

const nodeTypes = { nucleus: NucleusNodeView, paper: PaperNodeView };
const edgeTypes = { lineage: LineageEdgeView };

// ── The canvas ───────────────────────────────────────────────────────────────

interface CanvasProps {
  detail: NucleusDetail;
  events: ExpansionEvent[];
  running: boolean;
  selected: string[];
  onSelectionChange: (ids: string[]) => void;
}

function Canvas({ detail, events, running, selected, onSelectionChange }: CanvasProps) {
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

  const edges: LineageFlowEdge[] = detail.lineage.map((l) => {
    const source = l.from_kind === 'nucleus' ? root : l.from_node_id!;
    const how = l.prompt_id
      ? `by “${promptText.get(l.prompt_id) ?? 'a prompt'}” · via ${SOURCE_NAMES[l.retrieval_source ?? ''] ?? 'the archivist'}`
      : 'added by you';
    return {
      id: l.id, source, target: l.to_node_id, type: 'lineage', selectable: false,
      data: { title: `Lineage — found from ${labelOf.get(source) ?? 'a paper'} ${how}` },
    };
  });

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
