import { api } from './client';
import { getToken } from './client';

export interface Synthesis {
  id: string;
  project_id: string;
  claim_text: string;
  synthesis_text: string | null;
  query_expansion_json: unknown;
  retrieval_corpus: string;
  model: string;
  checker_model: string | null;
  verification_rate: number | null;
  status: 'running' | 'done' | 'failed';
  run_at: string;
}

export interface EvidenceFull {
  synthesis_id: string;
  passage_id: string;
  role: string;
  ord: number;
  verbatim_text: string;
  verified: boolean;
  paper_id: string;
  paper_title: string;
  paper_doi: string | null;
  paper_year: number | null;
  paper_venue: string | null;
}

export interface Evaluation {
  id: string;
  synthesis_id: string;
  passage_id: string;
  user_id: string;
  stance: 'agree' | 'partial' | 'disagree';
  body: string | null;
  created_at: string;
}

export interface SynthesisStep {
  id: string;
  ord: number;
  kind: string;
  payload_json: unknown;
  created_at: string;
}

export const synthesisApi = {
  listForProject: (projectId: string) =>
    api.get<Synthesis[]>(`/projects/${projectId}/syntheses`).then((r) => r.data),

  get: (id: string) =>
    api.get<Synthesis>(`/syntheses/${id}`).then((r) => r.data),

  create: (claimText: string, projectId: string, corpus: string) =>
    api
      .post<Synthesis>('/syntheses', {
        claim_text: claimText,
        project_id: projectId,
        retrieval_corpus: corpus,
      })
      .then((r) => r.data),

  updateText: (id: string, synthesisText: string) =>
    api
      .patch<Synthesis>(`/syntheses/${id}`, { synthesis_text: synthesisText })
      .then((r) => r.data),

  evidenceFull: (id: string) =>
    api.get<EvidenceFull[]>(`/syntheses/${id}/evidence-full`).then((r) => r.data),

  evaluations: (id: string) =>
    api.get<Evaluation[]>(`/syntheses/${id}/evaluations`).then((r) => r.data),

  upsertEvaluation: (synthesisId: string, passageId: string, stance: string, body?: string) =>
    api
      .post<Evaluation>(`/syntheses/${synthesisId}/evaluations`, {
        passage_id: passageId,
        stance,
        body: body ?? null,
      })
      .then((r) => r.data),

  streamUrl(synthesisId: string): string {
    const base = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/^http/, 'ws');
    const token = getToken() ?? '';
    return `${base}/syntheses/${synthesisId}/stream?token=${encodeURIComponent(token)}`;
  },
};
