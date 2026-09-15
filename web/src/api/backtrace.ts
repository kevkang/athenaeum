import { api, getToken } from './client';

export type BacktraceResolutionStatus =
  | 'pending'
  | 'ingesting'
  | 'abstract_match'
  | 'full_text_match'
  | 'no_match'
  | 'awaiting_manual_import';

export type BacktraceUserDecision = 'pending' | 'continued' | 'stopped_here';

export interface BacktraceHop {
  id: string;
  chain_id: string;
  // NULL means this hop branches directly off the chain's origin paper.
  parent_hop_id: string | null;
  hop_index: number;
  to_openalex_id: string;
  to_doi: string | null;
  to_title: string;
  to_authors_json: string[] | null;
  to_year: number | null;
  to_venue: string | null;
  to_paper_id: string | null;
  quote: string | null;
  quote_source: 'abstract' | 'full_text' | null;
  relevance_note: string | null;
  match_reasoning: string | null;
  model: string | null;
  resolution_status: BacktraceResolutionStatus;
  user_decision: BacktraceUserDecision;
  created_at: string;
}

export interface BacktraceChain {
  id: string;
  project_id: string;
  origin_paper_id: string;
  origin_sentence_id: string | null;
  origin_text: string;
  created_at: string;
}

export interface BacktraceChainSummary {
  id: string;
  origin_paper_id: string;
  origin_sentence_id: string | null;
  origin_text: string;
  hop_count: number;
  max_depth: number;
  leaf_count: number;
  has_pending: boolean;
  created_at: string;
}

export interface BacktraceCandidate {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  venue: string | null;
  // The ranking model's own stated confidence/reasoning for this specific
  // candidate — a human still picks, this only narrows the options.
  reasoning: string;
  confident: boolean;
}

export interface BacktraceChainDetail {
  chain: BacktraceChain;
  hops: BacktraceHop[];
}

/** Response for /rank — advisory only, never creates a hop. */
export interface BacktraceRankResult {
  candidates: BacktraceCandidate[];
  message: string | null;
  model: string | null;
}

/** Response for /recheck — a hop plus an optional status note. */
export interface BacktraceHopResult {
  hop: BacktraceHop;
  message: string | null;
}

/** Resolution counts for one depth level. `depth` is 1-based: depth 1 is one
 * hop back from the origin paper. */
export interface BacktraceDepthCoverage {
  depth: number;
  resolved: number;
  unresolved: number;
  in_flight: number;
  attempted: number;
  resolution_rate: number | null;
}

/** Response for /chains/:id/resolution. `attempted` counts only hops that
 * finished looking, and it counts hops chased, not every reference the paper
 * cites. `resolution_rate` is null, not 0, when nothing has finished, so
 * "not measured yet" never renders as "0% resolved". */
export interface BacktraceResolution {
  chain_id: string | null;
  project_id: string | null;
  chain_count: number;
  resolved: number;
  unresolved: number;
  in_flight: number;
  attempted: number;
  resolution_rate: number | null;
  deepest_depth: number;
  deepest: BacktraceDepthCoverage | null;
  by_depth: BacktraceDepthCoverage[];
}

export const backtraceApi = {
  start: (projectId: string, paperId: string, sentenceId: string | null, claimText: string) =>
    api
      .post<BacktraceChain>('/backtrace/chains', {
        project_id: projectId,
        paper_id: paperId,
        sentence_id: sentenceId,
        claim_text: claimText,
      })
      .then((r) => r.data),

  /** parentHopId null ranks against the chain's origin paper. Creates no hop. */
  rank: (chainId: string, parentHopId: string | null, streamId?: string) =>
    api
      .post<BacktraceRankResult>(
        `/backtrace/chains/${chainId}/rank`,
        { parent_hop_id: parentHopId },
        { params: { stream_id: streamId } },
      )
      .then((r) => r.data),

  /** Commits one specific candidate (from a prior `rank` call) as a real hop. */
  commitHop: (
    chainId: string,
    parentHopId: string | null,
    openalexId: string,
    reasoning: string | null,
    streamId?: string,
  ) =>
    api
      .post<BacktraceHop>(
        `/backtrace/chains/${chainId}/hops`,
        { parent_hop_id: parentHopId, openalex_id: openalexId, reasoning },
        { params: { stream_id: streamId } },
      )
      .then((r) => r.data),

  stopHop: (chainId: string, hopId: string) =>
    api.post<BacktraceHop>(`/backtrace/chains/${chainId}/hops/${hopId}/stop`).then((r) => r.data),

  recheckHop: (chainId: string, hopId: string, streamId?: string) =>
    api
      .post<BacktraceHopResult>(`/backtrace/chains/${chainId}/hops/${hopId}/recheck`, null, {
        params: { stream_id: streamId },
      })
      .then((r) => r.data),

  get: (chainId: string) =>
    api.get<BacktraceChainDetail>(`/backtrace/chains/${chainId}`).then((r) => r.data),
  /** Resolution coverage for one tree, overall and broken out by depth. */
  resolution: (chainId: string) =>
    api.get<BacktraceResolution>(`/backtrace/chains/${chainId}/resolution`).then((r) => r.data),

  /** Raw .athtrace YAML text — download it client-side (see ExportView's
   * `downloadNow` for the established blob-download pattern) rather than
   * following a browser navigation, since the request needs an auth header. */
  exportChain: (chainId: string) =>
    api
      .get<string>(`/backtrace/chains/${chainId}/export`, { responseType: 'text' })
      .then((r) => r.data),

  importChain: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('project_id', projectId);
    form.append('file', file);
    return api.post<{ chain_id: string }>('/backtrace/import', form).then((r) => r.data);
  },

  list: (projectId: string, paperId?: string) =>
    api
      .get<BacktraceChainSummary[]>('/backtrace/chains', {
        params: { project_id: projectId, paper_id: paperId },
      })
      .then((r) => r.data),

  /** WS URL for live step-status pings during one mutating call — see
   * api/backtrace.py's module docstring for the stream_id protocol. */
  stepStreamUrl(streamId: string): string {
    const base = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/^http/, 'ws');
    const token = getToken() ?? '';
    return `${base}/backtrace/stream/${streamId}?token=${encodeURIComponent(token)}`;
  },
};
