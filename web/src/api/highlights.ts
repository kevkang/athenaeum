import { api } from './client';

export interface Highlight {
  id: string;
  project_id: string;
  paper_id: string;
  sentence_ids: string[];
  verbatim_text: string;
  author_id: string | null;
  created_at: string;
  paper_title: string;
  paper_year: number | null;
  paper_first_author: string | null;
}

export const highlightsApi = {
  list: (projectId: string, paperId?: string) =>
    api
      .get<Highlight[]>(`/projects/${projectId}/highlights`, {
        params: paperId ? { paper_id: paperId } : undefined,
      })
      .then((r) => r.data),
  create: (payload: { project_id: string; paper_id: string; sentence_ids: string[]; verbatim_text: string }) =>
    api.post<Highlight>('/highlights', payload).then((r) => r.data),
  delete: (id: string) => api.delete(`/highlights/${id}`).then(() => undefined),
};
