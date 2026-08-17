import { api } from './client';

export interface ComposeDocument {
  id: string;
  project_id: string;
  content_json: Record<string, unknown>;
  version: number;
  updated_by: string | null;
  updated_at: string;
}

export interface CitationEvidence {
  sub_id: string;
  target_kind: string;
  target_id: string;
  quote: string | null;
  role: string;
}

export interface CitationReference {
  number: number;
  kind: string;
  id: string;
  paper_title: string | null;
  paper_year: number | null;
  paper_first_author: string | null;
  paper_doi: string | null;
  dataset_title: string | null;
  dataset_kind: string | null;
  statement_assertion: string | null;
  evidence: CitationEvidence[];
}

export interface CitationNumbering {
  references: CitationReference[];
  // statement_id -> the sub-ids that statement's own evidence resolves to
  statement_markers: Record<string, string[]>;
}

export interface ComposeImportWarning {
  span_text: string;
  missing_statement_id: string;
}

export interface ComposeImportResult {
  document: ComposeDocument;
  warnings: ComposeImportWarning[];
}

export const composeApi = {
  get: (projectId: string) =>
    api.get<ComposeDocument>(`/projects/${projectId}/compose-document`).then((r) => r.data),
  save: (projectId: string, contentJson: Record<string, unknown>, version: number) =>
    api
      .put<ComposeDocument>(`/projects/${projectId}/compose-document`, {
        content_json: contentJson,
        version,
      })
      .then((r) => r.data),
  /** The shared numbering pass — see domain/citations.py on the backend.
   * Computed fresh from the currently persisted document on every call. */
  citations: (projectId: string) =>
    api
      .get<CitationNumbering>(`/projects/${projectId}/compose-document/citations`)
      .then((r) => r.data),
  /** Raw .athmd markdown text — download it client-side (see ExportView's
   * `downloadNow` for the established blob-download pattern) rather than
   * following a browser navigation, since the request needs an auth header. */
  export: (projectId: string) =>
    api
      .get<string>(`/projects/${projectId}/compose-document/export`, { responseType: 'text' })
      .then((r) => r.data),
  /** Self-contained HTML file — see archive/athmd_render.py. Same
   * blob-download rationale as `export`. */
  renderHtml: (projectId: string) =>
    api
      .get<string>(`/projects/${projectId}/compose-document/render.html`, { responseType: 'text' })
      .then((r) => r.data),
  /** PDF, via a shared headless-Chromium instance on the backend. */
  renderPdf: (projectId: string) =>
    api
      .get<ArrayBuffer>(`/projects/${projectId}/compose-document/render.pdf`, {
        responseType: 'arraybuffer',
      })
      .then((r) => r.data),
  /** Replaces the document's prose from an uploaded .athmd file — see
   * archive/athmd.py's module docstring for what does/doesn't survive the
   * round trip. Always overwrites (no version check, unlike `save`), since
   * there's no meaningful "client's last-known version" for a file picked
   * from disk. */
  importFile: (projectId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api
      .post<ComposeImportResult>(`/projects/${projectId}/compose-document/import`, form)
      .then((r) => r.data);
  },
};
