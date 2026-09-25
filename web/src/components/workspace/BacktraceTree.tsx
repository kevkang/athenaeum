import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  useReactFlow,
  useViewport,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  backtraceApi,
  type BacktraceCandidate,
  type BacktraceChainSummary,
  type BacktraceHop,
  type BacktraceResolution,
  type BacktraceResolutionStatus,
} from '../../api/backtrace';
import { papersApi } from '../../api/papers';
import { trunc } from '../../lib/format';
import FindResultCard, { AiNote, type FindImportState } from './FindResultCard';
import BacktraceCandidateCard from './BacktraceCandidateCard';
import styles from './BacktraceTree.module.css';

// A phylogenetic-tree-style view of one backtrace chain: the origin claim at
// the top, cited works fanning out below it, branch position on the X axis
// purely for layout (siblings spread apart, no meaning beyond "not
// overlapping"), and Y position mapped to publication year — so tracing a
// claim backward through the literature visibly moves down through time.
//
// Every node is a full, always-visible card (title/authors/year/venue/quote/
// relevance/match-reasoning) rather than a small labeled dot with a
// click-triggered popover — ranking a frontier (the origin, or any resolved
// hop) happens automatically and its results render as their own "pending"
// leaf cards in the tree immediately, before the user spends the cost of
// actually resolving a verbatim quote for any of them (per-card "Continue
// backtrace →", or "Backtrace all" for the whole batch at once).

const ORIGIN_NODE_ID = '__origin__';
const EDGE_COLOR = '#8a7a5c';
const X_SPACING = 340;
// Real citation chains can span many decades (a recent paper citing 1960s
// work) — cards are now full-size content, not small dots, so a naive
// linear year mapping with the old dot-sized constants would force fitView
// to zoom out so far that cards (and their buttons) render at a handful of
// screen pixels. A low per-year rate plus a hard cap keeps "further back in
// time sits lower" as a visual trend without letting one old outlier
// citation blow up the whole canvas.
const Y_PER_YEAR = 16;
const MAX_Y_SPAN = 900;
const MIN_Y_SPAN = 320;
const ORIGIN_Y_GAP = 260;
const MIN_PARENT_CHILD_GAP = 260;

interface TreePosition { x: number; y: number }
interface LayoutInput { id: string; parentId: string | null; year: number | null }

interface TreeLayout {
  positions: Map<string, TreePosition>;
  originPosition: TreePosition;
  minYear: number;
  maxYear: number;
  ySpan: number;
}

/** X: leaves get sequential slots, internal nodes get the mean of their
 * children's X (a minimal Reingold–Tilford-style tree layout). Y: a linear
 * mapping from `year`, newest at the top — items with an unknown year fall
 * back to the tree's most recent known year rather than collapsing to a
 * single point. The origin sits above the newest item by a fixed gap.
 * Generalized over plain `{id, parentId, year}` rather than `BacktraceHop`
 * directly so ephemeral (not-yet-committed) pending candidates can be laid
 * out in the same pass as real hops. */
function computeTreeLayout(items: LayoutInput[]): TreeLayout {
  const childrenOf = new Map<string, LayoutInput[]>();
  const roots: LayoutInput[] = [];
  for (const item of items) {
    if (item.parentId) {
      const list = childrenOf.get(item.parentId) ?? [];
      list.push(item);
      childrenOf.set(item.parentId, list);
    } else {
      roots.push(item);
    }
  }

  const xById = new Map<string, number>();
  let nextLeafSlot = 0;
  function assignX(item: LayoutInput): number {
    const kids = childrenOf.get(item.id) ?? [];
    let x: number;
    if (kids.length === 0) {
      x = nextLeafSlot;
      nextLeafSlot += 1;
    } else {
      const childXs = kids.map(assignX);
      x = childXs.reduce((a, b) => a + b, 0) / childXs.length;
    }
    xById.set(item.id, x);
    return x;
  }
  roots.forEach(assignX);

  const knownYears = items.map((item) => item.year).filter((y): y is number => y != null);
  const thisYear = new Date().getFullYear();
  const maxYear = knownYears.length ? Math.max(...knownYears) : thisYear;
  const minYear = knownYears.length ? Math.min(...knownYears) : thisYear - 1;
  const yearSpan = Math.max(1, maxYear - minYear);
  const ySpan = Math.min(MAX_Y_SPAN, Math.max(MIN_Y_SPAN, yearSpan * Y_PER_YEAR));
  const originY = -ORIGIN_Y_GAP;

  const positions = new Map<string, TreePosition>();
  function assignY(item: LayoutInput, parentY: number): void {
    const x = (xById.get(item.id) ?? 0) * X_SPACING;
    const year = item.year ?? maxYear;
    const yearY = ((maxYear - year) / yearSpan) * ySpan;
    const y = Math.max(yearY, parentY + MIN_PARENT_CHILD_GAP);
    positions.set(item.id, { x, y });
    for (const child of childrenOf.get(item.id) ?? []) assignY(child, y);
  }
  roots.forEach((item) => assignY(item, originY));

  const rootXs = roots.map((item) => xById.get(item.id) ?? 0);
  const originX = (rootXs.length ? rootXs.reduce((a, b) => a + b, 0) / rootXs.length : 0) * X_SPACING;

  return { positions, originPosition: { x: originX, y: originY }, minYear, maxYear, ySpan };
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

// ── Node content ─────────────────────────────────────────────────────────

/** Per-frontier (origin or a resolved hop) state: is it currently being
 * ranked, what pending candidates has ranking surfaced so far, and the
 * actions for acting on them. Shared shape between the origin and hop node
 * components so `FrontierBar` doesn't need to know which one it's on. */
interface FrontierState {
  ranking: boolean;
  pending: BacktraceCandidate[];
  rankModel: string | null;
  onContinueOne: (candidate: BacktraceCandidate) => void;
  onContinueAll: () => void;
  committingIds: Set<string>;
  backtracingAll: boolean;
}

function FrontierBar({ frontier }: { frontier: FrontierState }) {
  if (frontier.pending.length === 0) {
    return frontier.ranking ? <div className={styles.frontierStatus}>finding citations…</div> : null;
  }
  return (
    <div className={styles.frontierRow}>
      <button
        className={styles.detailBtn}
        onClick={frontier.onContinueAll}
        disabled={frontier.backtracingAll}
      >
        {frontier.backtracingAll ? 'Backtracing all…' : `Backtrace all (${frontier.pending.length})`}
      </button>
    </div>
  );
}

interface OriginNodeData extends Record<string, unknown> {
  originText: string;
  originPaperTitle: string | null;
  frontier: FrontierState;
}

function OriginNodeView({ data }: NodeProps<Node<OriginNodeData, 'origin'>>) {
  return (
    <div className={`${styles.nodeCardWrap} nodrag nopan`}>
      <div className={styles.originCard}>
        <div className={styles.detailKind}>origin claim</div>
        <div className={styles.detailQuote}>&ldquo;{data.originText}&rdquo;</div>
        {data.originPaperTitle && (
          <div className={styles.detailMeta}>from &ldquo;{trunc(data.originPaperTitle, 60)}&rdquo;</div>
        )}
      </div>
      <FrontierBar frontier={data.frontier} />
      <Handle type="source" position={Position.Bottom} className={styles.rfHandle} />
    </div>
  );
}

interface HopNodeData extends Record<string, unknown> {
  hop: BacktraceHop;
  importState: FindImportState;
  onImport: () => void;
  importing: boolean;
  hasChildren: boolean;
  descendantCount: number;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  frontier: FrontierState;
}

// Statuses where the automatic search finished without finding supporting
// text; mirrors UNRESOLVED_STATES in domain/backtrace_stats.py. A branch that
// ends in one of these is a dead end and must never look like an origin.
const DEAD_END_STATUSES: ReadonlySet<BacktraceResolutionStatus> = new Set([
  'no_match',
  'awaiting_manual_import',
]);
const DEAD_END_EDGE_COLOR = '#5a5040';

function HopNodeView({ data }: NodeProps<Node<HopNodeData, 'hop'>>) {
  const { hop } = data;
  const deadEnd = DEAD_END_STATUSES.has(hop.resolution_status);
  const placeholderQuote =
    hop.resolution_status === 'ingesting' ? 'Fetching full text…'
    : hop.resolution_status === 'awaiting_manual_import' ? 'No open-access version found automatically.'
    : hop.resolution_status === 'no_match' ? 'No supporting text found in this paper.'
    : 'Resolving…';

  return (
    <div className={`${styles.nodeCardWrap} ${deadEnd ? styles.deadEnd : ''} nodrag nopan`}>
      <Handle type="target" position={Position.Top} className={styles.rfHandle} />
      {deadEnd && (
        <div className={styles.deadEndBadge}>
          ⌀ dead end · {hop.resolution_status === 'no_match' ? 'no supporting text' : 'no open-access text'} · not a source
        </div>
      )}
      <FindResultCard
        title={hop.to_title}
        authors={hop.to_authors_json ?? []}
        year={hop.to_year}
        venue={hop.to_venue}
        visitUrl={hop.to_doi ? `https://doi.org/${hop.to_doi}` : null}
        quote={hop.quote ?? placeholderQuote}
        relevanceNote={hop.relevance_note}
        model={hop.model}
        importState={data.importState}
        onImport={data.onImport}
        importing={data.importing}
      />
      {hop.match_reasoning && (
        hop.model
          ? <AiNote text={hop.match_reasoning} model={hop.model} label="Match reasoning" />
          : <div className={styles.detailHint}><strong>Match reasoning:</strong> {hop.match_reasoning}</div>
      )}
      {data.hasChildren && (
        <div className={styles.detailActionsRow}>
          <button className={styles.detailBtn} onClick={data.onToggleCollapse}>
            {data.isCollapsed ? `Expand (${data.descendantCount})` : 'Collapse subtree'}
          </button>
        </div>
      )}
      <FrontierBar frontier={data.frontier} />
      <Handle type="source" position={Position.Bottom} className={styles.rfHandle} />
    </div>
  );
}

interface PendingNodeData extends Record<string, unknown> {
  candidate: BacktraceCandidate;
  rankModel: string | null;
  committing: boolean;
  onContinue: () => void;
}

function PendingNodeView({ data }: NodeProps<Node<PendingNodeData, 'pending'>>) {
  return (
    <div className={`${styles.nodeCardWrap} nodrag nopan`}>
      <Handle type="target" position={Position.Top} className={styles.rfHandle} />
      <BacktraceCandidateCard
        candidate={data.candidate}
        rankModel={data.rankModel}
        committing={data.committing}
        onContinue={data.onContinue}
      />
    </div>
  );
}

function formatRate(rate: number | null): string {
  return rate == null ? '—' : `${Math.round(rate * 100)}%`;
}

/** Resolution coverage by depth, pinned under the header. Each bar is resolved
 * over attempted for one level, so the fall-off with depth is visible at a
 * glance. The summary names the deepest measured level because that rate, not
 * the overall one, decides whether the tree's tips can be called origins. */
function DepthStrip({ resolution }: { resolution: BacktraceResolution }) {
  const { deepest } = resolution;
  return (
    <div className={styles.depthStrip}>
      <div className={styles.depthSummary}>
        {resolution.resolved} of {resolution.attempted} hops chased resolved ({formatRate(resolution.resolution_rate)})
        {deepest && (
          <> · deepest measured, depth {deepest.depth}: {deepest.resolved}/{deepest.attempted} ({formatRate(deepest.resolution_rate)})</>
        )}
      </div>
      {resolution.by_depth.map((d) => (
        <div key={d.depth} className={styles.depthRow}>
          <span className={styles.depthLabel}>d{d.depth}</span>
          <span className={styles.depthBar}>
            <span
              className={styles.depthBarFill}
              style={{ width: d.attempted ? `${(d.resolved / d.attempted) * 100}%` : '0%' }}
            />
          </span>
          <span className={styles.depthCount}>{d.attempted ? `${d.resolved}/${d.attempted}` : '—'}</span>
          <span className={styles.depthRate}>{formatRate(d.resolution_rate)}</span>
          {d.in_flight > 0 && <span className={styles.depthInFlight}>{d.in_flight} resolving</span>}
        </div>
      ))}
      <div className={styles.depthFootnote}>
        Counts hops that finished looking, out of the references chosen to chase. Tips are the
        earliest reachable given corpus coverage, not proven originals.
      </div>
    </div>
  );
}

const nodeTypes = { origin: OriginNodeView, hop: HopNodeView, pending: PendingNodeView };

// ── Main component ──────────────────────────────────────────────────────

export default function BacktraceTree(props: {
  chainId: string | null;
  projectId: string;
  visible: boolean;
  /** Called when the user picks a chain from history or successfully
   * imports a .athtrace file — chainId itself is owned by the parent
   * (WorkspaceView), since it's independent of whatever the left panel's
   * linear stepper is doing with its own activeBacktrace chain. */
  onChainLoaded?: (chainId: string) => void;
}) {
  return (
    <ReactFlowProvider>
      <BacktraceTreeInner {...props} />
    </ReactFlowProvider>
  );
}

function pendingNodeId(parentKey: string, openalexId: string): string {
  return `pending:${parentKey}:${openalexId}`;
}

function BacktraceTreeInner({
  chainId, projectId, visible, onChainLoaded,
}: {
  chainId: string | null;
  projectId: string;
  visible: boolean;
  onChainLoaded?: (chainId: string) => void;
}) {
  const queryClient = useQueryClient();
  const { fitView } = useReactFlow();
  const viewport = useViewport();

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [importedHopIds, setImportedHopIds] = useState<Set<string>>(new Set());
  const [historyOpen, setHistoryOpen] = useState(false);
  const importFileInputRef = useRef<HTMLInputElement>(null);

  // Frontier ranking/commit state — keyed by ORIGIN_NODE_ID or a hop id.
  // "Already started ranking" is a plain ref, not state: it's a pure
  // dedup guard for the auto-rank effect below, never rendered directly —
  // using state for it would mean setting it synchronously as part of the
  // effect's own call stack, which react-hooks/set-state-in-effect flags.
  const rankStartedRef = useRef<Set<string>>(new Set());
  const [rankingInFlight, setRankingInFlight] = useState<Set<string>>(new Set());
  const [pendingByParent, setPendingByParent] = useState<
    Map<string, { candidates: BacktraceCandidate[]; model: string | null }>
  >(new Map());
  const [committingIds, setCommittingIds] = useState<Set<string>>(new Set());
  const [backtracingAllKeys, setBacktracingAllKeys] = useState<Set<string>>(new Set());

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
      a.download = `backtrace-${chainId}.athtrace`;
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

  // Coverage comes from the server instead of being recounted from `hops`, so
  // the tree shows exactly the definition the public endpoint publishes.
  // Putting every hop's status in the query key refetches it whenever the
  // chain changes (a new hop, or one finishing) without extra invalidation.
  const statusSignature = detail?.hops.map((h) => `${h.id}:${h.resolution_status}`).join('|') ?? '';
  const { data: resolution } = useQuery({
    queryKey: ['backtrace-resolution', chainId, statusSignature],
    queryFn: () => backtraceApi.resolution(chainId!),
    enabled: !!chainId && !!detail,
    // Keep the previous strip on screen while a refetch runs, but never
    // carry one chain's numbers over into a different chain.
    placeholderData: (prev) => (prev?.chain_id === chainId ? prev : undefined),
  });

  const { data: originPaper } = useQuery({
    queryKey: ['paper', detail?.chain.origin_paper_id],
    queryFn: () => papersApi.get(detail!.chain.origin_paper_id),
    enabled: !!detail,
  });

  const hops = useMemo(() => detail?.hops ?? [], [detail]);

  // Switching chains (via History) starts every per-chain UI concern fresh —
  // adjusted during render (React's recommended pattern for resetting state
  // on a prop change) rather than in an effect, which would commit once
  // with stale state and then re-render a second time.
  const [syncedChainId, setSyncedChainId] = useState(chainId);
  if (chainId !== syncedChainId) {
    setSyncedChainId(chainId);
    setRankingInFlight(new Set());
    setPendingByParent(new Map());
    setCommittingIds(new Set());
    setBacktracingAllKeys(new Set());
    setCollapsed(new Set());
  }
  // Refs can't be reset during render (only state can) — a plain effect,
  // not the render-time block above, is the correct place for this one.
  useEffect(() => {
    rankStartedRef.current = new Set();
  }, [chainId]);

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

  const childrenByParentKey = useMemo(() => {
    const map = new Map<string, BacktraceHop[]>();
    for (const h of hops) {
      const key = h.parent_hop_id ?? ORIGIN_NODE_ID;
      const list = map.get(key) ?? [];
      list.push(h);
      map.set(key, list);
    }
    return map;
  }, [hops]);

  // A candidate stops being "pending" the moment a real hop for the same
  // target exists under the same parent — no separate bookkeeping needed to
  // remove it from `pendingByParent` on commit, this just recomputes it away.
  const visiblePendingFor = useCallback((key: string): BacktraceCandidate[] => {
    const entry = pendingByParent.get(key);
    if (!entry) return [];
    const already = new Set((childrenByParentKey.get(key) ?? []).map((h) => h.to_openalex_id));
    return entry.candidates.filter((c) => !already.has(c.openalex_id));
  }, [pendingByParent, childrenByParentKey]);

  // ── Rank one frontier — advisory only, never creates a hop. The
  // `await Promise.resolve()` defers everything past the calling effect's
  // own synchronous execution — react-hooks/set-state-in-effect flags
  // setState reachable synchronously from an effect body, even through a
  // called function, so nothing here can run before that yield. The dedup
  // guard itself (rankStartedRef) is a plain ref mutated synchronously in
  // the effect, which is fine — only *state* updates are the concern. ──
  const runRank = useCallback(async (key: string, parentHopId: string | null) => {
    await Promise.resolve();
    setRankingInFlight((prev) => new Set(prev).add(key));
    try {
      const result = await backtraceApi.rank(chainId!, parentHopId);
      setPendingByParent((prev) => new Map(prev).set(key, { candidates: result.candidates, model: result.model }));
    } catch {
      // Advisory only — a failed rank just means no pending cards appear
      // for this frontier; everything already in the tree is unaffected.
    } finally {
      setRankingInFlight((prev) => { const next = new Set(prev); next.delete(key); return next; });
    }
  }, [chainId]);

  // Auto-rank every frontier the moment it becomes eligible: the origin as
  // soon as the chain loads with no hops yet, and any hop the moment it
  // resolves — no manual "Explore further" trigger, matching
  // BacktracePanel.tsx's own auto-rank-on-frontier-change precedent.
  useEffect(() => {
    if (!chainId || !detail) return;
    if (hops.length === 0 && !rankStartedRef.current.has(ORIGIN_NODE_ID)) {
      rankStartedRef.current.add(ORIGIN_NODE_ID);
      void runRank(ORIGIN_NODE_ID, null);
    }
    for (const h of hops) {
      const resolved = h.resolution_status === 'abstract_match' || h.resolution_status === 'full_text_match';
      if (resolved && !rankStartedRef.current.has(h.id)) {
        rankStartedRef.current.add(h.id);
        void runRank(h.id, h.id);
      }
    }
  }, [chainId, detail, hops, runRank]);

  const commitCandidate = useCallback(async (parentHopId: string | null, candidate: BacktraceCandidate) => {
    setCommittingIds((prev) => new Set(prev).add(candidate.openalex_id));
    try {
      await backtraceApi.commitHop(chainId!, parentHopId, candidate.openalex_id, candidate.reasoning);
      queryClient.invalidateQueries({ queryKey: ['backtrace-chain', chainId] });
    } finally {
      setCommittingIds((prev) => { const next = new Set(prev); next.delete(candidate.openalex_id); return next; });
    }
  }, [chainId, queryClient]);

  const commitAll = useCallback(async (key: string, parentHopId: string | null) => {
    setBacktracingAllKeys((prev) => new Set(prev).add(key));
    try {
      // Genuinely parallel — safe because each commit's hop_index is
      // derived from the already-resolved parent, not a shared counter, and
      // the DB's own backtrace_hops_chain_target_uq constraint (plus each
      // request's own fresh exclusion check) rules out duplicate targets
      // even if two of these land concurrently.
      await Promise.all(visiblePendingFor(key).map((c) => commitCandidate(parentHopId, c)));
    } finally {
      setBacktracingAllKeys((prev) => { const next = new Set(prev); next.delete(key); return next; });
    }
  }, [visiblePendingFor, commitCandidate]);

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

  // ── Layout: real (visible) hops plus every currently-pending candidate ──
  const layout = useMemo(() => {
    const items: LayoutInput[] = [];
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      items.push({ id: h.id, parentId: h.parent_hop_id, year: h.to_year });
    }
    for (const c of visiblePendingFor(ORIGIN_NODE_ID)) {
      items.push({ id: pendingNodeId(ORIGIN_NODE_ID, c.openalex_id), parentId: null, year: c.year });
    }
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      for (const c of visiblePendingFor(h.id)) {
        items.push({ id: pendingNodeId(h.id, c.openalex_id), parentId: h.id, year: c.year });
      }
    }
    return computeTreeLayout(items);
  }, [hops, hiddenHopIds, visiblePendingFor]);

  const makeFrontier = useCallback((key: string, parentHopId: string | null): FrontierState => ({
    ranking: rankingInFlight.has(key),
    pending: visiblePendingFor(key),
    rankModel: pendingByParent.get(key)?.model ?? null,
    onContinueOne: (c) => void commitCandidate(parentHopId, c),
    onContinueAll: () => void commitAll(key, parentHopId),
    committingIds,
    backtracingAll: backtracingAllKeys.has(key),
  }), [rankingInFlight, visiblePendingFor, pendingByParent, committingIds, backtracingAllKeys, commitCandidate, commitAll]);

  const nodes: Node[] = useMemo(() => {
    if (!detail) return [];
    const list: Node[] = [
      {
        id: ORIGIN_NODE_ID,
        type: 'origin',
        position: layout.originPosition,
        draggable: false,
        data: {
          originText: detail.chain.origin_text,
          originPaperTitle: originPaper?.title ?? null,
          frontier: makeFrontier(ORIGIN_NODE_ID, null),
        } satisfies OriginNodeData,
      },
    ];

    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      list.push({
        id: h.id,
        type: 'hop',
        position: layout.positions.get(h.id) ?? { x: 0, y: 0 },
        draggable: false,
        data: {
          hop: h,
          importState: hopImportState(h),
          onImport: () => importMutation.mutate(h),
          importing: importMutation.isPending,
          hasChildren: (childrenByParentKey.get(h.id) ?? []).length > 0,
          descendantCount: descendantCount(h.id, hops),
          isCollapsed: collapsed.has(h.id),
          onToggleCollapse: () => setCollapsed((prev) => {
            const next = new Set(prev);
            if (next.has(h.id)) next.delete(h.id); else next.add(h.id);
            return next;
          }),
          frontier: makeFrontier(h.id, h.id),
        } satisfies HopNodeData,
      });
    }

    for (const c of visiblePendingFor(ORIGIN_NODE_ID)) {
      list.push({
        id: pendingNodeId(ORIGIN_NODE_ID, c.openalex_id),
        type: 'pending',
        position: layout.positions.get(pendingNodeId(ORIGIN_NODE_ID, c.openalex_id)) ?? { x: 0, y: 0 },
        draggable: false,
        data: {
          candidate: c,
          rankModel: pendingByParent.get(ORIGIN_NODE_ID)?.model ?? null,
          committing: committingIds.has(c.openalex_id),
          onContinue: () => void commitCandidate(null, c),
        } satisfies PendingNodeData,
      });
    }
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      for (const c of visiblePendingFor(h.id)) {
        list.push({
          id: pendingNodeId(h.id, c.openalex_id),
          type: 'pending',
          position: layout.positions.get(pendingNodeId(h.id, c.openalex_id)) ?? { x: 0, y: 0 },
          draggable: false,
          data: {
            candidate: c,
            rankModel: pendingByParent.get(h.id)?.model ?? null,
            committing: committingIds.has(c.openalex_id),
            onContinue: () => void commitCandidate(h.id, c),
          } satisfies PendingNodeData,
        });
      }
    }

    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail, hops, hiddenHopIds, layout, originPaper, collapsed, childrenByParentKey, visiblePendingFor, pendingByParent, committingIds, makeFrontier, importMutation]);

  const edges: Edge[] = useMemo(() => {
    const list: Edge[] = [];
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      const source = h.parent_hop_id && !hiddenHopIds.has(h.parent_hop_id) ? h.parent_hop_id : ORIGIN_NODE_ID;
      const deadEnd = DEAD_END_STATUSES.has(h.resolution_status);
      const stroke = deadEnd ? DEAD_END_EDGE_COLOR : EDGE_COLOR;
      list.push({
        id: `e-${h.id}`, source, target: h.id, type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        // Dotted rather than the dashed pattern pending candidates use, so a
        // dead end reads as "looked and found nothing", not "not looked yet".
        style: deadEnd ? { stroke, strokeDasharray: '1 4' } : { stroke },
      });
    }
    for (const c of visiblePendingFor(ORIGIN_NODE_ID)) {
      const id = pendingNodeId(ORIGIN_NODE_ID, c.openalex_id);
      list.push({ id: `e-${id}`, source: ORIGIN_NODE_ID, target: id, type: 'smoothstep', style: { stroke: EDGE_COLOR, strokeDasharray: '4 3' } });
    }
    for (const h of hops) {
      if (hiddenHopIds.has(h.id)) continue;
      for (const c of visiblePendingFor(h.id)) {
        const id = pendingNodeId(h.id, c.openalex_id);
        list.push({ id: `e-${id}`, source: h.id, target: id, type: 'smoothstep', style: { stroke: EDGE_COLOR, strokeDasharray: '4 3' } });
      }
    }
    return list;
  }, [hops, hiddenHopIds, visiblePendingFor]);

  // Explicit fit on becoming visible (a flow laid out while `display:none`
  // doesn't compute correctly) and whenever the node count changes — new
  // hops/pending cards appearing should stay in frame automatically.
  const wasVisibleRef = useRef(false);
  useEffect(() => {
    if (!visible) { wasVisibleRef.current = false; return; }
    const alreadyVisible = wasVisibleRef.current;
    wasVisibleRef.current = true;
    const id = requestAnimationFrame(() => fitView({ padding: 0.15, minZoom: 0.3, duration: alreadyVisible ? 200 : 0 }));
    return () => cancelAnimationFrame(id);
  }, [visible, nodes.length, fitView]);

  const yearTicks = useMemo(() => {
    const span = layout.maxYear - layout.minYear;
    const step = Math.max(1, Math.ceil(span / 6));
    const ticks: { year: number; screenY: number }[] = [];
    for (let year = layout.maxYear; year >= layout.minYear; year -= step) {
      const modelY = ((layout.maxYear - year) / Math.max(1, span)) * layout.ySpan;
      ticks.push({ year, screenY: modelY * viewport.zoom + viewport.y });
    }
    return ticks;
  }, [layout, viewport]);

  return (
    <div className={styles.wrap}>
      <div className={styles.canvas}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          minZoom={0.3}
          maxZoom={2}
        >
          <Background color="#2a2a2a" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

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
            {exportMutation.isPending ? 'Saving…' : '↓ Save .athtrace'}
          </button>
        )}
        <button
          className={styles.headerBtn}
          onClick={() => importFileInputRef.current?.click()}
          disabled={importFileMutation.isPending}
        >
          {importFileMutation.isPending ? 'Loading…' : '↑ Load .athtrace'}
        </button>
        <input
          ref={importFileInputRef}
          type="file"
          accept=".athtrace"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importFileMutation.mutate(f);
            e.target.value = '';
          }}
        />
      </div>

      {chainId && resolution && resolution.by_depth.length > 0 && (
        <DepthStrip resolution={resolution} />
      )}

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

      {chainId && detail && (
        <div className={styles.yearAxis}>
          {yearTicks.map((t) => (
            <div key={t.year} className={styles.yearTick} style={{ top: t.screenY }}>
              <span className={styles.yearTickLabel}>{t.year}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
