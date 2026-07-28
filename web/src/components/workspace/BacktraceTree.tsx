import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import {
  backtraceApi,
  type BacktraceCandidate,
  type BacktraceChainSummary,
  type BacktraceHop,
} from '../../api/backtrace';
import { papersApi } from '../../api/papers';
import { formatCitation, trunc } from '../../lib/format';
import { AiNote, type FindImportState } from './FindResultCard';
import styles from './BacktraceTree.module.css';

// A phylogenetic-tree-style view of one backtrace chain: the origin claim at
// the top, cited works fanning out below it, branch position on the X axis
// purely for layout (siblings spread apart, no meaning beyond "not
// overlapping"), and Y position mapped to publication year — so tracing a
// claim backward through the literature visibly moves down through time.
//
// Deliberately a *second*, independent Cytoscape instance rather than
// reusing the knowledge-graph's — different data, different interaction
// model (this is a strict tree with a year-pinned layout; the graph is a
// general force/dagre-laid-out statement network). Both use Cytoscape's
// default 2D-canvas renderer (not WebGL), so running two side by side has
// no context-limit concern. This component's own container is meant to
// stay mounted at all times (see WorkspaceView's graphViewMode toggle) —
// Cytoscape's creation effect only runs once, tied to the container
// existing at mount, so conditionally unmounting it would prevent
// re-initialization on toggle. Visibility is a CSS concern of the parent;
// this component just needs an explicit `visible` prop so it can call
// `resize()`/`fit()` itself once, the moment it actually becomes visible —
// resize doesn't happen automatically for a canvas that started at 0×0.

const ORIGIN_NODE_ID = '__origin__';
const X_SPACING = 170;
const Y_PER_YEAR = 60;
const MIN_Y_SPAN = 240;
const ORIGIN_Y_GAP = 110;
const MIN_PARENT_CHILD_GAP = 70;

interface TreePosition { x: number; y: number }

interface TreeLayout {
  positions: Map<string, TreePosition>;
  originPosition: TreePosition;
  minYear: number;
  maxYear: number;
  ySpan: number;
}

/** X: leaves get sequential slots, internal nodes get the mean of their
 * children's X (a minimal Reingold–Tilford-style tree layout). Y: a linear
 * mapping from `to_year`, newest at the top — nodes with an unknown year
 * fall back to the tree's most recent known year rather than collapsing to
 * a single point. The origin sits above the newest hop by a fixed gap. */
function computeTreeLayout(hops: BacktraceHop[]): TreeLayout {
  const childrenOf = new Map<string, BacktraceHop[]>();
  const roots: BacktraceHop[] = [];
  for (const h of hops) {
    if (h.parent_hop_id) {
      const list = childrenOf.get(h.parent_hop_id) ?? [];
      list.push(h);
      childrenOf.set(h.parent_hop_id, list);
    } else {
      roots.push(h);
    }
  }

  const xById = new Map<string, number>();
  let nextLeafSlot = 0;
  function assignX(hop: BacktraceHop): number {
    const kids = childrenOf.get(hop.id) ?? [];
    let x: number;
    if (kids.length === 0) {
      x = nextLeafSlot;
      nextLeafSlot += 1;
    } else {
      const childXs = kids.map(assignX);
      x = childXs.reduce((a, b) => a + b, 0) / childXs.length;
    }
    xById.set(hop.id, x);
    return x;
  }
  roots.forEach(assignX);

  const knownYears = hops.map((h) => h.to_year).filter((y): y is number => y != null);
  const thisYear = new Date().getFullYear();
  const maxYear = knownYears.length ? Math.max(...knownYears) : thisYear;
  const minYear = knownYears.length ? Math.min(...knownYears) : thisYear - 1;
  const yearSpan = Math.max(1, maxYear - minYear);
  const ySpan = Math.max(MIN_Y_SPAN, yearSpan * Y_PER_YEAR);
  const originY = -ORIGIN_Y_GAP;

  // Year alone can't be trusted to keep nodes apart — two hops at different
  // tree depths (e.g. a dataset paper and its companion task paper,
  // published the same year) would otherwise land on the exact same Y and
  // silently hide one behind the other. Walking parent-before-child and
  // enforcing a minimum gap from each node's *final* (already-clamped)
  // parent Y guarantees separation regardless of how years repeat, while
  // still preferring the year-derived Y whenever it's already far enough
  // down to satisfy that gap on its own.
  const positions = new Map<string, TreePosition>();
  function assignY(hop: BacktraceHop, parentY: number): void {
    const x = (xById.get(hop.id) ?? 0) * X_SPACING;
    const year = hop.to_year ?? maxYear;
    const yearY = ((maxYear - year) / yearSpan) * ySpan;
    const y = Math.max(yearY, parentY + MIN_PARENT_CHILD_GAP);
    positions.set(hop.id, { x, y });
    for (const child of childrenOf.get(hop.id) ?? []) assignY(child, y);
  }
  roots.forEach((h) => assignY(h, originY));

  const rootXs = roots.map((h) => xById.get(h.id) ?? 0);
  const originX = (rootXs.length ? rootXs.reduce((a, b) => a + b, 0) / rootXs.length : 0) * X_SPACING;

  return { positions, originPosition: { x: originX, y: originY }, minYear, maxYear, ySpan };
}

function nodeLabel(hop: BacktraceHop): string {
  const citation = formatCitation(hop.to_authors_json?.[0] ?? null, hop.to_year);
  return trunc(hop.to_title, 34) + (citation !== 'Unknown source' ? `\n${citation}` : '');
}

function descendantCount(hopId: string, hops: BacktraceHop[]): number {
  const childrenOf = new Map<string, BacktraceHop[]>();
  for (const h of hops) {
    if (h.parent_hop_id) {
      const list = childrenOf.get(h.parent_hop_id) ?? [];
      list.push(h);
      childrenOf.set(h.parent_hop_id, list);
    }
  }
  let count = 0;
  const stack = [...(childrenOf.get(hopId) ?? [])];
  while (stack.length) {
    const h = stack.pop()!;
    count += 1;
    stack.push(...(childrenOf.get(h.id) ?? []));
  }
  return count;
}

const TREE_CY_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      label: 'data(label)', color: '#e6e1d6', 'font-family': 'Inter, sans-serif',
      'font-size': '9px', 'text-wrap': 'wrap', 'text-valign': 'center', 'text-halign': 'center',
      shape: 'roundrectangle', width: '152px', height: '46px', padding: '6px',
      'text-max-width': '138px', 'border-width': 1.5, 'border-color': '#8a7a5c',
      'background-color': '#1f1d18',
    },
  },
  {
    selector: 'node[kind = "origin"]',
    style: {
      'background-color': '#1a1a26', 'border-color': '#89a5c4', 'border-width': 2,
      'font-style': 'italic', height: '54px',
    },
  },
  {
    selector: 'node[status = "abstract_match"], node[status = "full_text_match"]',
    style: { 'background-color': '#1a231a', 'border-color': '#8aa57c' },
  },
  {
    selector: 'node[status = "no_match"], node[status = "awaiting_manual_import"]',
    style: { 'background-color': '#231a1a', 'border-color': '#c67a5c', 'border-style': 'dashed' },
  },
  {
    selector: 'node[status = "pending"], node[status = "ingesting"]',
    style: { 'border-style': 'dotted' },
  },
  {
    selector: 'node[?collapsed]',
    style: { 'border-style': 'double', 'border-width': 3 },
  },
  {
    selector: 'node:selected',
    style: { 'border-color': '#c9a876', 'border-width': 2.5 },
  },
  {
    selector: 'edge',
    style: {
      width: 1.5, 'curve-style': 'bezier', 'line-color': '#8a7a5c',
      'target-arrow-color': '#8a7a5c', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.75,
    },
  },
];

type DetailTarget = { kind: 'origin' } | { kind: 'hop'; hopId: string };

export default function BacktraceTree({
  chainId, projectId, visible, onChainLoaded,
}: {
  chainId: string | null;
  projectId: string;
  visible: boolean;
  /** Called when the user picks a chain from history or successfully
   * imports a .athbacktrace file — chainId itself is owned by the parent
   * (WorkspaceView), since it's independent of whatever the left panel's
   * linear stepper is doing with its own activeBacktrace chain. */
  onChainLoaded?: (chainId: string) => void;
}) {
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [renderTick, setRenderTick] = useState(0);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [detailTarget, setDetailTarget] = useState<DetailTarget | null>(null);
  const [hoveredHopId, setHoveredHopId] = useState<string | null>(null);
  const [importedHopIds, setImportedHopIds] = useState<Set<string>>(new Set());
  const [historyOpen, setHistoryOpen] = useState(false);
  const importFileInputRef = useRef<HTMLInputElement>(null);

  const { data: chainSummaries, isLoading: historyLoading } = useQuery({
    queryKey: ['backtrace-chains', projectId],
    queryFn: () => backtraceApi.list(projectId),
    enabled: historyOpen,
  });

  const exportMutation = useMutation({
    mutationFn: () => backtraceApi.exportChain(chainId!),
    onSuccess: (yamlText) => {
      const blob = new Blob([yamlText], { type: 'application/x-yaml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `backtrace-${chainId}.athbacktrace`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
  });

  const importFileMutation = useMutation({
    mutationFn: (file: File) => backtraceApi.importChain(projectId, file),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['backtrace-chains', projectId] });
      onChainLoaded?.(result.chain_id);
    },
  });

  const { data: detail } = useQuery({
    queryKey: ['backtrace-chain', chainId],
    queryFn: () => backtraceApi.get(chainId!),
    enabled: !!chainId,
    refetchInterval: (q) =>
      q.state.data?.hops.some((h) => h.resolution_status === 'ingesting') ? 3000 : false,
  });

  const { data: originPaper } = useQuery({
    queryKey: ['paper', detail?.chain.origin_paper_id],
    queryFn: () => papersApi.get(detail!.chain.origin_paper_id),
    enabled: !!detail,
  });

  const hops = useMemo(() => detail?.hops ?? [], [detail]);

  // Hops whose closest collapsed ancestor makes them hidden — collapsing a
  // node folds everything beneath it, not the node itself.
  const hiddenHopIds = useMemo(() => {
    const byId = new Map(hops.map((h) => [h.id, h]));
    const hidden = new Set<string>();
    for (const h of hops) {
      let p = h.parent_hop_id;
      while (p) {
        if (collapsed.has(p)) { hidden.add(h.id); break; }
        p = byId.get(p)?.parent_hop_id ?? null;
      }
    }
    return hidden;
  }, [hops, collapsed]);

  const layout = useMemo(() => computeTreeLayout(hops), [hops]);

  // ── Cytoscape lifecycle — created once, never destroyed on chain switch ──
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: TREE_CY_STYLE,
      layout: { name: 'preset' },
      wheelSensitivity: 0.3,
      minZoom: 0.15,
      maxZoom: 3,
    });
    cy.on('tap', 'node', (e) => {
      const id = e.target.id();
      setDetailTarget(id === ORIGIN_NODE_ID ? { kind: 'origin' } : { kind: 'hop', hopId: id });
    });
    cy.on('tap', (e) => {
      if (e.target === cy) setDetailTarget(null);
    });
    cy.on('mouseover', 'node', (e) => {
      const id = e.target.id();
      if (id !== ORIGIN_NODE_ID) setHoveredHopId(id);
    });
    cy.on('mouseout', 'node', () => setHoveredHopId(null));
    cy.on('render', () => setRenderTick((t) => t + 1));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, []);

  // Explicit resize/fit on becoming visible — a canvas created (or last
  // laid out) while `display:none` doesn't pick either up automatically.
  const wasVisibleRef = useRef(false);
  useEffect(() => {
    if (visible && !wasVisibleRef.current) {
      cyRef.current?.resize();
      cyRef.current?.fit(undefined, 60);
    }
    wasVisibleRef.current = visible;
  }, [visible]);

  // ── Sync elements whenever the chain/tree data or collapse state changes ──
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !detail) return;

    const elements: cytoscape.ElementDefinition[] = [
      {
        group: 'nodes',
        data: { id: ORIGIN_NODE_ID, kind: 'origin', label: trunc(detail.chain.origin_text, 60) },
        position: layout.originPosition,
      },
    ];
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      const pos = layout.positions.get(h.id) ?? { x: 0, y: 0 };
      elements.push({
        group: 'nodes',
        data: { id: h.id, kind: 'hop', status: h.resolution_status, label: nodeLabel(h), collapsed: collapsed.has(h.id) },
        position: pos,
      });
      const source = h.parent_hop_id && !hiddenHopIds.has(h.parent_hop_id) ? h.parent_hop_id : ORIGIN_NODE_ID;
      elements.push({ group: 'edges', data: { id: `e-${h.id}`, source, target: h.id } });
    }

    cy.elements().remove();
    cy.add(elements);
    if (visible) cy.fit(undefined, 60);
  }, [detail, hops, layout, hiddenHopIds, collapsed, visible]);

  // ── Positions derived from the live Cytoscape instance — computed in an
  // effect and stored as real state, not read from the ref during render
  // (a ref's `.current` can change without React knowing to re-render, so
  // reading it inline during render risks becoming stale or inconsistent;
  // recomputing here, keyed on renderTick, keeps it correctly reactive to
  // every pan/zoom/layout change instead).
  const [yearTicks, setYearTicks] = useState<{ year: number; screenY: number }[]>([]);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) { setYearTicks([]); return; }
    const pan = cy.pan();
    const zoom = cy.zoom();
    const span = layout.maxYear - layout.minYear;
    const step = Math.max(1, Math.ceil(span / 6));
    const ticks: { year: number; screenY: number }[] = [];
    for (let year = layout.maxYear; year >= layout.minYear; year -= step) {
      const modelY = ((layout.maxYear - year) / Math.max(1, span)) * layout.ySpan;
      ticks.push({ year, screenY: modelY * zoom + pan.y });
    }
    setYearTicks(ticks);
  }, [layout, renderTick]);

  // ── Detail popover ──────────────────────────────────────────────────────
  const detailHop = detailTarget?.kind === 'hop' ? hops.find((h) => h.id === detailTarget.hopId) ?? null : null;

  // renderedPosition() is relative to the Cytoscape *container*, but the
  // popover/tooltip CSS is `position: fixed` (viewport-relative) so it can
  // escape the tree's own overflow:hidden bounds — so the container's own
  // page offset has to be added in here, or every popover lands shifted up
  // and left by however far the canvas sits from the viewport's origin.
  const toViewportPos = (renderedPos: { x: number; y: number }): { x: number; y: number } => {
    const rect = containerRef.current?.getBoundingClientRect();
    const x = renderedPos.x + (rect?.left ?? 0);
    const y = renderedPos.y + (rect?.top ?? 0);
    // Clamped so a node near the tree's own edges (common — the origin is
    // always at the top, leaf hops are often near the bottom) can't produce
    // a popover that renders partly or entirely outside the viewport,
    // which `position: fixed` + no scroll makes genuinely unreachable
    // rather than just visually clipped.
    const clampedX = Math.min(Math.max(x, 170), window.innerWidth - 170);
    const clampedY = Math.min(Math.max(y, 10), window.innerHeight - 220);
    return { x: clampedX, y: clampedY };
  };

  const [detailPos, setDetailPos] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !detailTarget) { setDetailPos(null); return; }
    const id = detailTarget.kind === 'origin' ? ORIGIN_NODE_ID : detailTarget.hopId;
    const el = cy.getElementById(id);
    setDetailPos(el.nonempty() ? toViewportPos(el.renderedPosition()) : null);
  }, [detailTarget, renderTick]);

  const [hoveredPos, setHoveredPos] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !hoveredHopId) { setHoveredPos(null); return; }
    const el = cy.getElementById(hoveredHopId);
    setHoveredPos(el.nonempty() ? toViewportPos(el.renderedPosition()) : null);
  }, [hoveredHopId, renderTick]);

  const [rankState, setRankState] = useState<{
    parentHopId: string | null;
    candidates: BacktraceCandidate[];
    message: string | null;
    model: string | null;
  } | null>(null);
  const [checkedCandidates, setCheckedCandidates] = useState<Set<string>>(new Set());

  const rankMutation = useMutation({
    mutationFn: (parentHopId: string | null) => backtraceApi.rank(chainId!, parentHopId),
    onSuccess: (result, parentHopId) => {
      setRankState({ parentHopId, candidates: result.candidates, message: result.message, model: result.model });
      setCheckedCandidates(new Set(result.candidates.slice(0, 1).map((c) => c.openalex_id)));
    },
  });

  const openBranchesMutation = useMutation({
    mutationFn: async ({ parentHopId, candidates }: { parentHopId: string | null; candidates: BacktraceCandidate[] }) => {
      for (const c of candidates) {
        await backtraceApi.commitHop(chainId!, parentHopId, c.openalex_id, c.reasoning);
      }
    },
    onSuccess: () => {
      setRankState(null);
      queryClient.invalidateQueries({ queryKey: ['backtrace-chain', chainId] });
    },
  });

  const importMutation = useMutation({
    mutationFn: (hop: BacktraceHop) =>
      papersApi.ingest(hop.to_doi!, projectId, {
        foundQuote: hop.quote ?? undefined,
        foundRelevanceNote: hop.relevance_note ?? undefined,
      }),
    onSuccess: (_paper, hop) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setImportedHopIds((prev) => new Set(prev).add(hop.id));
    },
  });

  const hopImportState = (hop: BacktraceHop): FindImportState => {
    const imported = !!hop.to_paper_id || importedHopIds.has(hop.id);
    return imported ? 'already_in_project' : hop.to_doi ? 'importable' : 'no_doi';
  };

  // Reset transient popover state whenever the selected node changes —
  // adjusted during render (React's recommended pattern for this, see
  // ComposeView.tsx/ProjectSettingsModal.tsx for the same idiom elsewhere
  // in this codebase) rather than in an effect, which would commit once
  // with stale state and then re-render a second time.
  const [syncedDetailTarget, setSyncedDetailTarget] = useState(detailTarget);
  if (detailTarget !== syncedDetailTarget) {
    setSyncedDetailTarget(detailTarget);
    setRankState(null);
    setCheckedCandidates(new Set());
  }

  const canExploreFurther = detailTarget?.kind === 'origin'
    ? true
    : !!detailHop && (detailHop.resolution_status === 'abstract_match' || detailHop.resolution_status === 'full_text_match');
  const explorableParentId = detailTarget?.kind === 'origin' ? null : (detailHop?.id ?? null);
  const isCollapsed = detailTarget?.kind === 'hop' && collapsed.has(detailTarget.hopId);
  const childCount = detailTarget?.kind === 'hop' ? hops.filter((h) => h.parent_hop_id === detailTarget.hopId).length : 0;

  const hoveredHop = hoveredHopId ? hops.find((h) => h.id === hoveredHopId) ?? null : null;

  return (
    <div className={styles.wrap}>
      <div ref={containerRef} className={styles.canvas} />

      <div className={styles.header}>
        <button className={styles.headerBtn} onClick={() => setHistoryOpen((v) => !v)}>
          History {historyOpen ? '▲' : '▼'}
        </button>
        <div className={styles.headerSpacer} />
        {chainId && (
          <button
            className={styles.headerBtn}
            onClick={() => exportMutation.mutate()}
            disabled={exportMutation.isPending}
          >
            {exportMutation.isPending ? 'Saving…' : '↓ Save .athbacktrace'}
          </button>
        )}
        <button
          className={styles.headerBtn}
          onClick={() => importFileInputRef.current?.click()}
          disabled={importFileMutation.isPending}
        >
          {importFileMutation.isPending ? 'Loading…' : '↑ Load .athbacktrace'}
        </button>
        <input
          ref={importFileInputRef}
          type="file"
          accept=".athbacktrace"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importFileMutation.mutate(f);
            e.target.value = '';
          }}
        />
      </div>

      {historyOpen && (
        <div className={styles.historyPanel}>
          {historyLoading && <div className={styles.emptyDesc}>Loading…</div>}
          {chainSummaries?.length === 0 && (
            <div className={styles.emptyDesc}>No backtrace chains in this project yet.</div>
          )}
          {chainSummaries?.map((c: BacktraceChainSummary) => (
            <button
              key={c.id}
              className={styles.historyRow}
              onClick={() => { onChainLoaded?.(c.id); setHistoryOpen(false); }}
            >
              <span className={styles.historyClaim}>{trunc(c.origin_text, 70)}</span>
              <span className={styles.historyMeta}>
                {c.hop_count} hop{c.hop_count === 1 ? '' : 's'} · depth {c.max_depth}
                {c.has_pending ? ' · resolving…' : ''}
              </span>
            </button>
          ))}
        </div>
      )}

      {importFileMutation.isError && (
        <div className={styles.headerError}>
          {(importFileMutation.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            ?? 'Could not import this file'}
        </div>
      )}

      {!chainId && (
        <div className={styles.emptyState}>
          <div className={styles.emptyTitle}>No backtrace selected</div>
          <div className={styles.emptyDesc}>
            Start one from a highlighted sentence in the reader, or load a past trace from history.
          </div>
        </div>
      )}

      {/* ── Year axis ── */}
      {chainId && detail && (
        <div className={styles.yearAxis}>
          {yearTicks.map((t) => (
            <div key={t.year} className={styles.yearTick} style={{ top: t.screenY }}>
              <span className={styles.yearTickLabel}>{t.year}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Hover tooltip (match_reasoning) ── */}
      {hoveredHop && hoveredPos && (hoveredHop.match_reasoning || hoveredHop.relevance_note) && (
        <div className={styles.hoverTooltip} style={{ left: hoveredPos.x, top: hoveredPos.y + 30 }}>
          {hoveredHop.match_reasoning && (
            <div className={styles.hoverTooltipText}>{trunc(hoveredHop.match_reasoning, 160)}</div>
          )}
          {!hoveredHop.match_reasoning && hoveredHop.relevance_note && (
            <div className={styles.hoverTooltipText}>{trunc(hoveredHop.relevance_note, 160)}</div>
          )}
        </div>
      )}

      {/* ── Detail popover ── */}
      {detailTarget && detailPos && (
        <div className={styles.detailPopover} style={{ left: detailPos.x, top: detailPos.y + 34 }}>
          {detailTarget.kind === 'origin' ? (
            <>
              <div className={styles.detailKind}>origin claim</div>
              <div className={styles.detailQuote}>&ldquo;{detail?.chain.origin_text}&rdquo;</div>
              {originPaper && (
                <div className={styles.detailMeta}>from &ldquo;{trunc(originPaper.title, 60)}&rdquo;</div>
              )}
            </>
          ) : detailHop ? (
            <>
              <div className={styles.detailKind}>{detailHop.resolution_status.replace('_', ' ')}</div>
              <div className={styles.detailTitle}>{detailHop.to_title}</div>
              <div className={styles.detailMeta}>
                {(detailHop.to_authors_json ?? []).join(', ') || 'Unknown authors'}
                {detailHop.to_venue ? ` · ${detailHop.to_venue}` : ''}
                {detailHop.to_year ? ` · ${detailHop.to_year}` : ''}
              </div>
              {detailHop.quote && <div className={styles.detailQuote}>&ldquo;{detailHop.quote}&rdquo;</div>}
              {detailHop.relevance_note && (
                detailHop.model
                  ? <AiNote text={detailHop.relevance_note} model={detailHop.model} label="Relevance" />
                  : <div className={styles.detailHint}><strong>Relevance:</strong> {detailHop.relevance_note}</div>
              )}
              {detailHop.match_reasoning && (
                detailHop.model
                  ? <AiNote text={detailHop.match_reasoning} model={detailHop.model} label="Match reasoning" />
                  : <div className={styles.detailHint}><strong>Match reasoning:</strong> {detailHop.match_reasoning}</div>
              )}
              <div className={styles.detailActionsRow}>
                {detailHop.to_doi && (
                  <a className={styles.detailLinkBtn} href={`https://doi.org/${detailHop.to_doi}`} target="_blank" rel="noreferrer">
                    visit
                  </a>
                )}
                {hopImportState(detailHop) === 'importable' && (
                  <button
                    className={styles.detailBtn}
                    onClick={() => importMutation.mutate(detailHop)}
                    disabled={importMutation.isPending}
                  >
                    {importMutation.isPending ? 'Importing…' : '+ import'}
                  </button>
                )}
                {hopImportState(detailHop) === 'already_in_project' && (
                  <span className={styles.detailHint}>✓ in project</span>
                )}
                {childCount > 0 && (
                  <button
                    className={styles.detailBtn}
                    onClick={() =>
                      setCollapsed((prev) => {
                        const next = new Set(prev);
                        if (next.has(detailTarget.hopId)) next.delete(detailTarget.hopId);
                        else next.add(detailTarget.hopId);
                        return next;
                      })
                    }
                  >
                    {isCollapsed ? `Expand (${descendantCount(detailTarget.hopId, hops)})` : 'Collapse subtree'}
                  </button>
                )}
              </div>
            </>
          ) : null}

          {canExploreFurther && (
            <div className={styles.exploreSection}>
              {!rankState ? (
                <button
                  className={styles.detailBtn}
                  onClick={() => rankMutation.mutate(explorableParentId)}
                  disabled={rankMutation.isPending}
                >
                  {rankMutation.isPending ? 'Ranking…' : 'Explore further from here'}
                </button>
              ) : rankState.candidates.length === 0 ? (
                <div className={styles.detailHint}>{rankState.message ?? 'No further references found.'}</div>
              ) : (
                <>
                  <div className={styles.detailKind}>pick candidates to branch into</div>
                  {rankState.candidates.map((c) => {
                    const checked = checkedCandidates.has(c.openalex_id);
                    const citation = formatCitation(c.authors[0] ?? null, c.year);
                    return (
                      <label key={c.openalex_id} className={styles.candidateRow}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() =>
                            setCheckedCandidates((prev) => {
                              const next = new Set(prev);
                              if (next.has(c.openalex_id)) next.delete(c.openalex_id);
                              else next.add(c.openalex_id);
                              return next;
                            })
                          }
                        />
                        <span className={styles.candidateLabel}>
                          {c.title}{citation !== 'Unknown source' ? `, ${citation}` : ''}
                          {!c.confident && <span className={styles.candidateUnsure}> (uncertain)</span>}
                        </span>
                      </label>
                    );
                  })}
                  <div className={styles.detailActionsRow}>
                    <button
                      className={styles.detailBtn}
                      disabled={checkedCandidates.size === 0 || openBranchesMutation.isPending}
                      onClick={() =>
                        openBranchesMutation.mutate({
                          parentHopId: rankState.parentHopId,
                          candidates: rankState.candidates.filter((c) => checkedCandidates.has(c.openalex_id)),
                        })
                      }
                    >
                      {openBranchesMutation.isPending
                        ? 'Opening…'
                        : `Open ${checkedCandidates.size || ''} branch${checkedCandidates.size === 1 ? '' : 'es'}`}
                    </button>
                    <button className={styles.detailLinkBtn} onClick={() => setRankState(null)}>cancel</button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
