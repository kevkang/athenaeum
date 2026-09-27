import { api } from './client';

/** One candidate work offered for a citation that could not be matched
 * deterministically. */
export interface DraftCitationCandidate {
  openalex_id: string | null;
  doi: string | null;
  title: string;
  first_author: string | null;
  venue: string | null;
  year: number | null;
  cited_by_count: number;
}

/** How a citation in the draft was matched. Only `exact` and `confirmed` are
 * ever audited — see src/athenaeum/domain/draft_audit.py. */
export type DraftResolution = 'exact' | 'needs_confirmation' | 'confirmed' | 'not_found';

/** What text the claim was checked against. `no_text` means nothing was
 * checked, which is not the same as a claim that failed its check. */
export type DraftEvidenceSource = 'project_full_text' | 'abstract' | 'no_text';

export interface DraftCitation {
  id: string;
  ord: number;
  claim_text: string;
  citation_text: string;
  resolution: DraftResolution;
  openalex_id: string | null;
  doi: string | null;
  matched_title: string | null;
  matched_year: number | null;
  matched_authors_json: string[] | null;
  candidates_json: DraftCitationCandidate[] | null;
  paper_id: string | null;
  evidence_source: DraftEvidenceSource | null;
  created_at: string;
}

export interface DraftFinding {
  id: string;
  ord: number;
  draft_citation_id: string | null;
  sub_id: string;
  quote: string | null;
  supported: boolean;
  rationale: string;
  created_at: string;
}

export interface DraftAuditRun {
  id: string;
  project_id: string;
  kind: string;
  draft_text: string | null;
  status: 'running' | 'done' | 'failed';
  model: string | null;
  quotes_checked: number | null;
  malformed_entries: number | null;
  created_at: string;
}

export interface DraftAuditDetail {
  run: DraftAuditRun;
  findings: DraftFinding[];
  draft_citations: DraftCitation[];
}

export const auditDraftsApi = {
  create: (projectId: string, text: string) =>
    api
      .post<DraftAuditRun>(`/projects/${projectId}/audit-drafts`, { text })
      .then((r) => r.data),

  get: (auditRunId: string) =>
    api.get<DraftAuditDetail>(`/audit-runs/${auditRunId}`).then((r) => r.data),

  confirm: (citationId: string, openalexId: string) =>
    api
      .post<DraftCitation>(`/audit-drafts/citations/${citationId}/confirm`, {
        openalex_id: openalexId,
      })
      .then((r) => r.data),
};
