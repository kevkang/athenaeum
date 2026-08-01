import { api } from './client';
import { getToken } from './client';

export interface AuditRun {
  id: string;
  project_id: string;
  compose_document_id: string;
  created_by: string | null;
  model: string | null;
  status: 'running' | 'done' | 'failed';
  created_at: string;
}

export interface AuditFinding {
  id: string;
  ord: number;
  statement_id: string;
  sub_id: string;
  target_kind: string;
  target_id: string;
  quote: string | null;
  supported: boolean;
  rationale: string;
  created_at: string;
}

export interface AuditRunDetail {
  run: AuditRun;
  findings: AuditFinding[];
  /** Statements with zero attached evidence — computed fresh at read time,
   * never persisted as findings (see domain/citations.py). */
  statements_with_no_evidence: string[];
}

export const auditApi = {
  create: (projectId: string) =>
    api.post<AuditRun>(`/projects/${projectId}/compose-document/audit`).then((r) => r.data),

  get: (auditRunId: string) =>
    api.get<AuditRunDetail>(`/audit-runs/${auditRunId}`).then((r) => r.data),

  streamUrl(auditRunId: string): string {
    const base = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/^http/, 'ws');
    const token = getToken() ?? '';
    return `${base}/audit-runs/${auditRunId}/stream?token=${encodeURIComponent(token)}`;
  },
};
