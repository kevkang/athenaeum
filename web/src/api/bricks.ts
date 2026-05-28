import { api } from './client';

export interface Brick {
  id: string;
  project_id: string;
  author_id: string | null;
  type: string;
  status: 'draft' | 'cemented' | 'open' | 'resolved' | 'wontfix';
  assertion: string;
  cemented_at: string | null;
  drafted_at: string;
  built_from_synthesis_id: string | null;
}

export interface BuildsOn {
  brick_id: string;
  parent_brick_id: string;
  ord: number;
}

export interface RestsOn {
  brick_id: string;
  target_kind: 'paper' | 'passage' | 'dataset' | 'brick';
  target_id: string;
  role: string;
}

export const bricksApi = {
  list: (projectId: string) =>
    api.get<Brick[]>(`/bricks/by-project/${projectId}`).then((r) => r.data),

  create: (projectId: string, type: string, assertion: string, builtFromSynthesisId?: string) =>
    api
      .post<Brick>('/bricks', {
        project_id: projectId,
        type,
        assertion,
        built_from_synthesis_id: builtFromSynthesisId ?? null,
      })
      .then((r) => r.data),

  update: (id: string, fields: { assertion?: string; type?: string; status?: string }) =>
    api.patch<Brick>(`/bricks/${id}`, fields).then((r) => r.data),

  delete: (id: string) => api.delete(`/bricks/${id}`),

  cement: (id: string) => api.post<Brick>(`/bricks/${id}/cement`).then((r) => r.data),

  buildsOn: (id: string) =>
    api.get<BuildsOn[]>(`/bricks/${id}/builds_on`).then((r) => r.data),

  addBuildsOn: (id: string, parentBrickId: string) =>
    api
      .post<BuildsOn>(`/bricks/${id}/builds_on`, { parent_brick_id: parentBrickId, ord: 0 })
      .then((r) => r.data),

  removeBuildsOn: (id: string, parentId: string) =>
    api.delete(`/bricks/${id}/builds_on/${parentId}`),

  restsOn: (id: string) =>
    api.get<RestsOn[]>(`/bricks/${id}/rests_on`).then((r) => r.data),

  addRestsOn: (id: string, targetKind: string, targetId: string, role: string) =>
    api
      .post<RestsOn>(`/bricks/${id}/rests_on`, { target_kind: targetKind, target_id: targetId, role })
      .then((r) => r.data),

  removeRestsOn: (id: string, targetKind: string, targetId: string) =>
    api.delete(`/bricks/${id}/rests_on/${targetKind}/${targetId}`),
};
