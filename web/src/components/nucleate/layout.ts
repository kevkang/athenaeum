import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force';

/*
 * Incremental force-directed layout for the Nucleate canvas (handoff Part IV).
 *
 * The rule that shapes it: new nodes enter near their lineage parent rather
 * than triggering a full re-layout that scrambles what the researcher has
 * already oriented themselves to. So every node that already has a position
 * is held fixed, the nucleus is pinned at the centre, and only new nodes are
 * free to settle. The simulation is run to rest synchronously and never
 * animated, which also means nothing moves on its own under reduced motion.
 */

export interface Pos {
  x: number;
  y: number;
}

export interface LayoutLink {
  source: string;
  target: string;
}

interface SimNode extends SimulationNodeDatum {
  id: string;
}

const LINK_DISTANCE = 260;
const REPULSION = -1100;
const COLLIDE_RADIUS = 150;
const TICKS = 300;

/** A small, repeatable offset from an id, so the same data always lays out
 * the same way (no randomness between reloads). */
function jitter(id: string): Pos {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const angle = ((h >>> 0) % 3600) / 3600 * Math.PI * 2;
  return { x: Math.cos(angle) * 90, y: Math.sin(angle) * 90 };
}

/**
 * Positions for every node in `ids`. `known` holds positions already on
 * screen; those are returned unchanged. `rootId` is pinned at the origin.
 */
export function layout(
  ids: string[],
  links: LayoutLink[],
  known: ReadonlyMap<string, Pos>,
  rootId: string,
): Map<string, Pos> {
  const result = new Map<string, Pos>();
  const fresh = ids.filter((id) => id !== rootId && !known.has(id));

  for (const id of ids) {
    if (id === rootId) result.set(id, { x: 0, y: 0 });
    else if (known.has(id)) result.set(id, known.get(id)!);
  }
  if (fresh.length === 0) return result;

  // Seed each new node beside its parent. Parents may themselves be new, so
  // keep placing whatever now has a placed parent until nothing changes; any
  // left over start beside the nucleus.
  const parentOf = new Map<string, string>();
  for (const l of links) if (!parentOf.has(l.target)) parentOf.set(l.target, l.source);
  const seeded = new Map<string, Pos>();
  let pending = [...fresh];
  for (let pass = 0; pass < fresh.length && pending.length; pass++) {
    pending = pending.filter((id) => {
      const parent = parentOf.get(id);
      const anchor = parent && (result.get(parent) ?? seeded.get(parent));
      if (!anchor) return true;
      const j = jitter(id);
      seeded.set(id, { x: anchor.x + j.x, y: anchor.y + j.y });
      return false;
    });
  }
  for (const id of pending) seeded.set(id, jitter(id));

  const nodes: SimNode[] = ids.map((id) => {
    const fixed = result.get(id);
    if (fixed) return { id, x: fixed.x, y: fixed.y, fx: fixed.x, fy: fixed.y };
    const s = seeded.get(id)!;
    return { id, x: s.x, y: s.y };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const simLinks: SimulationLinkDatum<SimNode>[] = links
    .filter((l) => byId.has(l.source) && byId.has(l.target))
    .map((l) => ({ source: l.source, target: l.target }));

  forceSimulation(nodes)
    .force('link', forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
      .id((d) => d.id).distance(LINK_DISTANCE).strength(0.5))
    .force('charge', forceManyBody().strength(REPULSION))
    .force('collide', forceCollide(COLLIDE_RADIUS))
    .force('x', forceX(0).strength(0.02))
    .force('y', forceY(0).strength(0.02))
    .stop()
    .tick(TICKS);

  for (const id of fresh) {
    const n = byId.get(id)!;
    result.set(id, { x: n.x ?? 0, y: n.y ?? 0 });
  }
  return result;
}
