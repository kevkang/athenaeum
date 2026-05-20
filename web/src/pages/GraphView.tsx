import { useEffect, useRef, useState, useCallback } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import { api } from '../api/client';
import { bricksApi } from '../api/bricks';
import styles from './GraphView.module.css';

cytoscape.use(dagre);

// ── Types ─────────────────────────────────────────────────────────────────────

interface BrickNode {
  id: string;
  project_id: string;
  type: string;
  status: 'draft' | 'cemented';
  assertion: string;
}

interface BuildsOnEdge {
  brick_id: string;
  parent_brick_id: string;
  ord: number;
}

interface RestsOnEdge {
  brick_id: string;
  target_kind: string;
  target_id: string;
  role: string;
}

interface BrickGraph {
  nodes: BrickNode[];
  builds_on: BuildsOnEdge[];
  rests_on: RestsOnEdge[];
}

type Filter = 'all' | 'draft' | 'cemented';

// ── Helpers ───────────────────────────────────────────────────────────────────

function typeClass(type: string): string {
  const known = ['observation', 'methods', 'foundation', 'synthesis', 'claim'];
  return known.includes(type) ? type : 'other';
}

function buildElements(graph: BrickGraph, filter: Filter) {
  const visibleIds = new Set(
    graph.nodes
      .filter((n) => filter === 'all' || n.status === filter)
      .map((n) => n.id)
  );

  const nodes = graph.nodes
    .filter((n) => visibleIds.has(n.id))
    .map((n) => ({
      data: {
        id: n.id,
        label: n.assertion.length > 60 ? n.assertion.slice(0, 57) + '…' : n.assertion,
        type: n.type,
        status: n.status,
        assertion: n.assertion,
      },
    }));

  const edges = graph.builds_on
    .filter((e) => visibleIds.has(e.brick_id) && visibleIds.has(e.parent_brick_id))
    .map((e) => ({
      data: {
        id: `bo-${e.brick_id}-${e.parent_brick_id}`,
        source: e.brick_id,
        target: e.parent_brick_id,
        kind: 'builds_on',
      },
    }));

  return [...nodes, ...edges];
}

// ── Node detail panel ─────────────────────────────────────────────────────────

function DetailPanel({
  node,
  graph,
  onClose,
  onSelectNode,
}: {
  node: BrickNode;
  graph: BrickGraph;
  onClose: () => void;
  onSelectNode: (id: string) => void;
}) {
  const queryClient = useQueryClient();

  const cementMutation = useMutation({
    mutationFn: () => bricksApi.cement(node.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['graph', node.project_id] }),
  });

  const parents = graph.builds_on
    .filter((e) => e.brick_id === node.id)
    .map((e) => graph.nodes.find((n) => n.id === e.parent_brick_id))
    .filter(Boolean) as BrickNode[];

  const children = graph.builds_on
    .filter((e) => e.parent_brick_id === node.id)
    .map((e) => graph.nodes.find((n) => n.id === e.brick_id))
    .filter(Boolean) as BrickNode[];

  const restsOnEdges = graph.rests_on.filter((e) => e.brick_id === node.id);

  return (
    <div className={styles.detailPanel}>
      <div className={styles.detailPanelHeader}>
        <span className={`${styles.typeBadge} ${styles[typeClass(node.type)]}`}>
          {node.type}
        </span>
        <button className={styles.closeBtn} onClick={onClose}>✕</button>
      </div>

      <div className={styles.detailPanelScroll}>
        <div className={styles.detailAssertion}>{node.assertion}</div>

        <div className={styles.detailStatusRow}>
          <span className={`${styles.statusDot} ${styles[node.status]}`} />
          <span className={styles.statusLabel}>{node.status}</span>
          {node.status === 'draft' && (
            <button
              className={styles.cementPanelBtn}
              onClick={() => cementMutation.mutate()}
              disabled={cementMutation.isPending}
            >
              Cement
            </button>
          )}
        </div>

        {parents.length > 0 && (
          <div className={styles.detailSection}>
            <div className={styles.detailSectionLabel}>Builds on</div>
            {parents.map((p) => (
              <div
                key={p.id}
                className={styles.connectionChip}
                onClick={() => onSelectNode(p.id)}
              >
                <span className={`${styles.typeBadge} ${styles[typeClass(p.type)]}`}>{p.type}</span>
                <span className={styles.connectionChipText}>{p.assertion}</span>
              </div>
            ))}
          </div>
        )}

        {children.length > 0 && (
          <div className={styles.detailSection}>
            <div className={styles.detailSectionLabel}>Built upon by</div>
            {children.map((c) => (
              <div
                key={c.id}
                className={styles.connectionChip}
                onClick={() => onSelectNode(c.id)}
              >
                <span className={`${styles.typeBadge} ${styles[typeClass(c.type)]}`}>{c.type}</span>
                <span className={styles.connectionChipText}>{c.assertion}</span>
              </div>
            ))}
          </div>
        )}

        {restsOnEdges.length > 0 && (
          <div className={styles.detailSection}>
            <div className={styles.detailSectionLabel}>Rests on · {restsOnEdges.length} source{restsOnEdges.length !== 1 ? 's' : ''}</div>
            {restsOnEdges.map((e) => (
              <div key={`${e.target_kind}:${e.target_id}`} className={styles.connectionChip}>
                <span className={styles.connectionChipText}>{e.target_kind} · {e.role}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function GraphView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/graph' });
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: graph, isLoading } = useQuery({
    queryKey: ['graph', projectId],
    queryFn: () => api.get<BrickGraph>(`/projects/${projectId}/graph`).then((r) => r.data),
  });

  const selectedNode = graph?.nodes.find((n) => n.id === selectedId) ?? null;

  const handleSelectNode = useCallback((id: string) => {
    setSelectedId(id);
    cyRef.current?.$(`#${id}`).select();
  }, []);

  // Build / rebuild the graph whenever data or filter changes
  useEffect(() => {
    if (!containerRef.current || !graph) return;

    const elements = buildElements(graph, filter);

    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            shape: 'roundrectangle',
            width: '180px',
            height: '52px',
            'background-color': '#1f1d18',
            'border-width': 1,
            'border-color': '#8a7a5c',
            label: 'data(label)',
            color: '#e6e1d6',
            'font-family': 'Lora, serif',
            'font-size': '11px',
            'font-style': 'italic',
            'text-wrap': 'wrap',
            'text-max-width': '162px',
            'text-valign': 'center',
            'text-halign': 'center',
            padding: '8px',
          },
        },
        {
          selector: 'node[status = "cemented"]',
          style: {
            'background-color': '#1a221a',
            'border-color': '#8aa57c',
            'border-width': 1.5,
          },
        },
        {
          selector: 'node:selected',
          style: {
            'background-color': '#2a2a1e',
            'border-color': '#c9a876',
            'border-width': 2,
          },
        },
        {
          selector: 'edge[kind = "builds_on"]',
          style: {
            width: 1.5,
            'line-color': '#3a3730',
            'target-arrow-color': '#5f5c55',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'arrow-scale': 0.9,
          },
        },
        {
          selector: 'edge:selected',
          style: {
            'line-color': '#8a7a5c',
            'target-arrow-color': '#8a7a5c',
          },
        },
      ],
      layout: {
        name: 'dagre',
        rankDir: 'BT',
        nodeSep: 40,
        rankSep: 60,
        padding: 40,
      } as cytoscape.LayoutOptions,
      wheelSensitivity: 0.3,
      minZoom: 0.1,
      maxZoom: 3,
    });

    cy.on('tap', 'node', (e: cytoscape.EventObject) => {
      setSelectedId(e.target.id());
    });

    cy.on('tap', (e: cytoscape.EventObject) => {
      if (e.target === cy) setSelectedId(null);
    });

    cyRef.current = cy;

    // Re-select previously selected node after rebuild
    if (selectedId && cy.$(`#${selectedId}`).length) {
      cy.$(`#${selectedId}`).select();
    }

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, filter]);

  // Sync selection highlight when selectedId changes without a rebuild
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedId) cy.$(`#${selectedId}`).select();
  }, [selectedId]);

  const counts = graph
    ? { all: graph.nodes.length, draft: graph.nodes.filter((n) => n.status === 'draft').length, cemented: graph.nodes.filter((n) => n.status === 'cemented').length }
    : null;

  return (
    <div className={styles.wrapper}>
      {/* ── Toolbar ── */}
      <div className={styles.toolbar}>
        <span className={styles.toolbarLabel}>Show</span>
        {(['all', 'draft', 'cemented'] as Filter[]).map((f) => (
          <button
            key={f}
            className={`${styles.filterChip} ${filter === f ? styles.active : ''}`}
            onClick={() => setFilter(f)}
          >
            {f} {counts ? `(${counts[f]})` : ''}
          </button>
        ))}

        <div className={styles.toolbarSep} />

        <button
          className={styles.toolbarBtn}
          onClick={() => cyRef.current?.fit(undefined, 40)}
        >
          Fit view
        </button>
        <button
          className={styles.toolbarBtn}
          onClick={() => cyRef.current?.reset()}
        >
          Reset zoom
        </button>

        {graph && (
          <span className={styles.countBadge}>
            {graph.builds_on.length} edge{graph.builds_on.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* ── Canvas + detail ── */}
      <div className={styles.canvasArea}>
        <div ref={containerRef} className={styles.cytoscapeContainer} />

        {isLoading && (
          <div className={styles.loadingOverlay}>Loading graph…</div>
        )}

        {!isLoading && graph && graph.nodes.length === 0 && (
          <div className={styles.emptyState}>
            <div className={styles.emptyTitle}>No bricks yet</div>
            <div className={styles.emptyDesc}>
              Create bricks in the Knowledge view to see them here.
            </div>
          </div>
        )}

        {selectedNode && graph && (
          <DetailPanel
            node={selectedNode}
            graph={graph}
            onClose={() => setSelectedId(null)}
            onSelectNode={handleSelectNode}
          />
        )}
      </div>
    </div>
  );
}
