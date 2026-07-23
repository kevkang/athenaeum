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
  origin_text: string;
  hop_count: number;
  created_at: string;
}

export interface BacktraceCandidate {
  openalex_id: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  venue: string | null;
}

export interface BacktraceChainDetail {
  chain: BacktraceChain;
  hops: BacktraceHop[];
}

export interface BacktraceStepResult {
  chain: BacktraceChain;
  hop: BacktraceHop | null;
  candidates: BacktraceCandidate[] | null;
  message: string | null;
  model: string | null;
}

export const backtraceApi = {
  start: (
    projectId: string,
    paperId: string,
    sentenceId: string | null,
    claimText: string,
    streamId?: string,
  ) =>
    api
      .post<BacktraceStepResult>(
        '/backtrace/chains',
        { project_id: projectId, paper_id: paperId, sentence_id: sentenceId, claim_text: claimText },
        { params: { stream_id: streamId } },
      )
      .then((r) => r.data),

  continueChain: (chainId: string, streamId?: string) =>
    api
      .post<BacktraceStepResult>(`/backtrace/chains/${chainId}/continue`, null, {
        params: { stream_id: streamId },
      })
      .then((r) => r.data),

  pick: (chainId: string, openalexId: string, streamId?: string) =>
    api
      .post<BacktraceStepResult>(
        `/backtrace/chains/${chainId}/pick`,
        { openalex_id: openalexId },
        { params: { stream_id: streamId } },
      )
      .then((r) => r.data),

  stop: (chainId: string) =>
    api.post<BacktraceChainDetail>(`/backtrace/chains/${chainId}/stop`).then((r) => r.data),

  recheckHop: (chainId: string, hopId: string, streamId?: string) =>
    api
      .post<BacktraceStepResult>(`/backtrace/chains/${chainId}/hops/${hopId}/recheck`, null, {
        params: { stream_id: streamId },
      })
      .then((r) => r.data),

  get: (chainId: string) =>
    api.get<BacktraceChainDetail>(`/backtrace/chains/${chainId}`).then((r) => r.data),

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
