import { api } from './client';

export interface Statement {
  id: string;
  project_id: string;
  author_id: string | null;
  type: string;
  status: 'draft' | 'locked' | 'open' | 'resolved' | 'wontfix';
  assertion: string;
  locked_at: string | null;
  drafted_at: string;
  built_from_synthesis_id: string | null;
}

/** The lighter-weight shape returned by GET /projects/:id/graph — a subset of
 * Statement's fields, plus a pre-aggregated evidence_count for the graph's
 * node badges. */
export interface StatementNode {
  id: string;
  project_id: string;
  type: string;
  status: 'draft' | 'locked' | 'open' | 'resolved' | 'wontfix';
  assertion: string;
  evidence_count: number;
}

export type StatementRelation = 'relates_to' | 'supports' | 'tension';

export interface StatementEdge {
  from_statement_id: string;
  to_statement_id: string;
  relation: StatementRelation;
  created_by: string | null;
  created_at: string;
}

export interface Evidence {
  statement_id: string;
  target_kind: 'paper' | 'passage' | 'dataset' | 'statement';
  target_id: string;
  role: string;
}

export const statementsApi = {
  list: (projectId: string) =>
    api.get<Statement[]>(`/statements/by-project/${projectId}`).then((r) => r.data),

  create: (projectId: string, type: string, assertion: string, builtFromSynthesisId?: string) =>
    api
      .post<Statement>('/statements', {
        project_id: projectId,
        type,
        assertion,
        built_from_synthesis_id: builtFromSynthesisId ?? null,
      })
      .then((r) => r.data),

  update: (id: string, fields: { assertion?: string; type?: string; status?: string }) =>
    api.patch<Statement>(`/statements/${id}`, fields).then((r) => r.data),

  delete: (id: string) => api.delete(`/statements/${id}`),

  lock: (id: string) => api.post<Statement>(`/statements/${id}/lock`).then((r) => r.data),

  unlock: (id: string) => api.post<Statement>(`/statements/${id}/unlock`).then((r) => r.data),

  lockAll: (projectId: string) =>
    api.post<Statement[]>(`/statements/by-project/${projectId}/lock-all`).then((r) => r.data),

  edges: (id: string) =>
    api.get<StatementEdge[]>(`/statements/${id}/edges`).then((r) => r.data),

  addEdge: (id: string, toStatementId: string, relation: StatementRelation) =>
    api
      .post<StatementEdge>(`/statements/${id}/edges`, {
        to_statement_id: toStatementId,
        relation,
      })
      .then((r) => r.data),

  removeEdge: (id: string, otherId: string) =>
    api.delete(`/statements/${id}/edges/${otherId}`),

  evidence: (id: string) =>
    api.get<Evidence[]>(`/statements/${id}/evidence`).then((r) => r.data),

  addEvidence: (id: string, targetKind: string, targetId: string, role: string) =>
    api
      .post<Evidence>(`/statements/${id}/evidence`, {
        target_kind: targetKind,
        target_id: targetId,
        role,
      })
      .then((r) => r.data),

  removeEvidence: (id: string, targetKind: string, targetId: string) =>
    api.delete(`/statements/${id}/evidence/${targetKind}/${targetId}`),
};
