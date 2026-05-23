import { api } from './client';

export interface KnowledgeLink {
  id: string;
  project_id: string;
  from_type: string;
  from_id: string;
  from_title: string;
  to_type: string;
  to_id: string;
  to_title: string;
  rel: string;
  note: string | null;
  created_by: string;
  created_at: string;
}

export interface KnowledgeRef {
  id: string;
  ref_type: string;
  title: string;
  subtitle: string | null;
}

export type RelType = 'supports' | 'contradicts' | 'extends' | 'cites' | 'related';
export type RefType = 'paper' | 'synthesis';

export const REL_LABELS: Record<RelType, string> = {
  supports: 'supports',
  contradicts: 'contradicts',
  extends: 'extends',
  cites: 'cites',
  related: 'related',
};

export const linksApi = {
  create: (payload: {
    project_id: string;
    from_type: RefType;
    from_id: string;
    to_type: RefType;
    to_id: string;
    rel: RelType;
    note?: string;
  }) => api.post<KnowledgeLink>('/links', payload).then((r) => r.data),

  delete: (linkId: string) => api.delete(`/links/${linkId}`),

  listForRef: (projectId: string, refType: RefType, refId: string) =>
    api
      .get<KnowledgeLink[]>(`/projects/${projectId}/links`, {
        params: { ref_type: refType, ref_id: refId },
      })
      .then((r) => r.data),

  searchRefs: (projectId: string, q: string) =>
    api
      .get<KnowledgeRef[]>(`/projects/${projectId}/refs`, { params: { q, limit: 20 } })
      .then((r) => r.data),
};
