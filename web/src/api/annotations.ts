import { api } from './client';

/** Personal notes on a paper within a project (src/athenaeum/api/annotations.py).
 * Nucleate's drawer uses target_kind 'paper': a node is one paper in one
 * project, so its notes show in every nucleus that paper appears in. */
export interface Annotation {
  id: string;
  project_id: string;
  author_id: string;
  target_kind: string;
  target_id: string;
  body: string;
  created_at: string;
}

export const annotationsApi = {
  forPaper: (projectId: string, paperId: string) =>
    api
      .get<Annotation[]>('/annotations', {
        params: { project_id: projectId, target_kind: 'paper', target_id: paperId },
      })
      .then((r) => r.data),

  addToPaper: (projectId: string, paperId: string, body: string) =>
    api
      .post<Annotation>('/annotations', {
        project_id: projectId, target_kind: 'paper', target_id: paperId, body,
      })
      .then((r) => r.data),

  remove: (annotationId: string) => api.delete(`/annotations/${annotationId}`),
};
