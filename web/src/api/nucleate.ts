import { api } from './client';

/*
 * Nucleate's API. Types mirror src/athenaeum/domain/models.py one to one, and
 * the vocabulary below mirrors src/athenaeum/domain/nucleate.py — change them
 * together.
 */

export type ReviewStatus = 'frontier' | 'accepted' | 'rejected' | 'pinned';
export type Provenance = 'ai_proposed' | 'ai_proposed_accepted' | 'ai_proposed_corrected' | 'user_set';
export type FulltextState = 'available' | 'abstract_only' | 'user_must_supply';
export type ExpansionStatus = 'running' | 'done' | 'failed' | 'cancelled';

/** The stages of one expansion, in order (handoff Part V). */
export const STAGES = [
  'interpreting',
  'retrieving',
  'ranking',
  'ingesting',
  'extracting',
  'interpreting_papers',
  'verifying',
  'placing',
  'awaiting_review',
] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  interpreting: 'Interpreting',
  retrieving: 'Retrieving',
  ranking: 'Ranking',
  ingesting: 'Ingesting',
  extracting: 'Extracting',
  interpreting_papers: 'Interpreting papers',
  verifying: 'Verifying',
  placing: 'Placing',
  awaiting_review: 'Awaiting review',
};

/** A node's own status during a run, shown on the node (handoff Part V). */
export type NodeRunStatus = 'fetching' | 'extracting' | 'verifying' | 'ready' | 'needs_full_text';
export const NODE_RUN_STATUSES: readonly string[] = [
  'fetching', 'extracting', 'verifying', 'ready', 'needs_full_text',
];

export interface Nucleus {
  id: string;
  project_id: string;
  question: string;
  created_at: string;
}

export interface Expansion {
  id: string;
  nucleus_id: string;
  prompt_id: string;
  status: ExpansionStatus;
  budget: number;
  stage: Stage | null;
  reply_text: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  started_at: string;
  finished_at: string | null;
}

export interface ExpansionEvent {
  ord: number;
  stage: Stage;
  /** A stage state (current/completed/failed) or, with node_id, a node status. */
  status: string;
  node_id: string | null;
  counts: Record<string, number> | null;
  message: string | null;
  input_tokens: number;
  output_tokens: number;
  at: string;
}

export interface NucleusPrompt {
  id: string;
  text: string;
  anchor_node_ids: string[];
  created_at: string;
  expansion: Expansion | null;
}

export interface CanvasPaper {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  /** The Unpaywall check: a URL, or checked with none, or never checked. */
  oa_pdf_url: string | null;
  oa_checked_at: string | null;
}

export interface CanvasReview {
  id: string;
  status: ReviewStatus;
  rejection_reason: string | null;
  relevance_text: string | null;
  /** Only 'ai_proposed' renders as AI text. */
  relevance_provenance: Provenance | null;
  selection_reasoning: string | null;
}

export interface TextQuoteSelector {
  type: 'TextQuoteSelector';
  exact: string;
  prefix?: string;
  suffix?: string;
}

export interface CanvasEvidence {
  id: string;
  ord: number;
  source: 'full_text' | 'abstract';
  verbatim_text: string;
  text_quote_selector: TextQuoteSelector;
}

export interface CanvasAudit {
  supported: boolean;
  rationale: string;
  checked_at: string;
}

export interface CanvasNode {
  node_id: string;
  paper: CanvasPaper;
  fulltext: FulltextState;
  review: CanvasReview;
  evidence: CanvasEvidence[];
  audit: CanvasAudit | null;
  /** Other nuclei this node also belongs to. */
  other_nucleus_ids: string[];
}

export interface CanvasLineage {
  id: string;
  from_kind: 'nucleus' | 'node';
  from_node_id: string | null;
  to_node_id: string;
  prompt_id: string | null;
  /** Null when a person added the node rather than the archivist. */
  retrieval_source: 'library' | 'openalex' | 'web' | null;
}

/** The six CiTO-mapped relations (D-N7), read "A <relation> B". */
export const SEMANTIC_RELATIONS = [
  'uses_method_of',
  'uses_data_from',
  'extends',
  'supports',
  'disagrees_with',
  'obtains_background_from',
] as const;
export type SemanticRelation = (typeof SEMANTIC_RELATIONS)[number];

/** A typed relation between two papers, owned by the project (D-N20). */
export interface SemanticEdge {
  id: string;
  from_node_id: string;
  to_node_id: string;
  relation: SemanticRelation;
  /** What the archivist proposed; null for a relation you set yourself. */
  proposed_relation: SemanticRelation | null;
  provenance: Provenance;
  rationale: string | null;
  rejected: boolean;
  rejection_reason: string | null;
  evidence: CanvasEvidence[];
}

/** What proposing found for one other paper, so an empty result says why. */
export type RelationOutcome =
  | 'proposed' | 'no_full_text' | 'no_mention' | 'no_relation' | 'already_recorded';

export interface RelationPair {
  to_node_id: string;
  outcome: RelationOutcome;
  proposed: number;
}

export interface RelationProposal {
  edges: SemanticEdge[];
  pairs: RelationPair[];
}

export type EdgeUpdate =
  | { action: 'accept' }
  | { action: 'correct'; relation: SemanticRelation }
  | { action: 'reject'; rejection_reason?: string }
  | { action: 'restore' };

export interface NucleusDetail {
  nucleus: Nucleus;
  nodes: CanvasNode[];
  lineage: CanvasLineage[];
  prompts: NucleusPrompt[];
  semantic_edges: SemanticEdge[];
}

export interface ReviewUpdate {
  status?: ReviewStatus;
  rejection_reason?: string;
  relevance_text?: string;
}

export const nucleateApi = {
  list: (projectId: string) =>
    api.get<Nucleus[]>(`/projects/${projectId}/nuclei`).then((r) => r.data),

  create: (projectId: string, question: string, budget?: number) =>
    api
      .post<NucleusDetail>(`/projects/${projectId}/nuclei`, { question, ...(budget ? { budget } : {}) })
      .then((r) => r.data),

  get: (nucleusId: string) =>
    api.get<NucleusDetail>(`/nuclei/${nucleusId}`).then((r) => r.data),

  prompt: (nucleusId: string, text: string, anchorNodeIds: string[]) =>
    api
      .post<NucleusPrompt>(`/nuclei/${nucleusId}/prompts`, { text, anchor_node_ids: anchorNodeIds })
      .then((r) => r.data),

  expansion: (expansionId: string, after = -1) =>
    api
      .get<{ expansion: Expansion; events: ExpansionEvent[] }>(`/expansions/${expansionId}`, {
        params: { after },
      })
      .then((r) => r.data),

  cancel: (expansionId: string) =>
    api.post<Expansion>(`/expansions/${expansionId}/cancel`).then((r) => r.data),

  review: (reviewId: string, update: ReviewUpdate) =>
    api.patch<CanvasReview>(`/node-reviews/${reviewId}`, update).then((r) => r.data),

  proposeRelations: (nucleusId: string, nodeId: string) =>
    api
      .post<RelationProposal>(`/nuclei/${nucleusId}/nodes/${nodeId}/relation-proposals`)
      .then((r) => r.data),

  addRelation: (nucleusId: string, fromNodeId: string, toNodeId: string, relation: SemanticRelation) =>
    api
      .post<SemanticEdge>(`/nuclei/${nucleusId}/semantic-edges`, {
        from_node_id: fromNodeId, to_node_id: toNodeId, relation,
      })
      .then((r) => r.data),

  updateRelation: (edgeId: string, update: EdgeUpdate) =>
    api.patch<SemanticEdge>(`/semantic-edges/${edgeId}`, update).then((r) => r.data),

  deleteRelation: (edgeId: string) => api.delete(`/semantic-edges/${edgeId}`),
};
