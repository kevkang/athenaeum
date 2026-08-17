import { api } from './client';

export interface Paper {
  id: string;
  doi: string | null;
  openalex_id: string | null;
  title: string;
  authors_json: unknown;
  venue: string | null;
  year: number | null;
  abstract: string | null;
  status: 'ghost' | 'full_text' | 'failed';
  created_at: string;
  zotero_item_key: string | null;
  zotero_source: 'sync' | 'local' | null;
  found_quote: string | null;
  found_relevance_note: string | null;
  found_corpus: string | null;
}

export interface SentenceEvidence {
  sentence_id: string;
  statement_id: string;
  assertion: string;
  role: string;
}

export interface Section {
  id: string;
  label: string;
  ord: number;
  char_start: number;
  char_end: number;
}

export interface Sentence {
  id: string;
  section_id: string;
  ord: number;
  text: string;
  char_start: number;
  char_end: number;
}

export interface SearchHit {
  sentence_id: string;
  text: string;
  char_start: number;
  char_end: number;
  section_label: string;
  score: number;
  paper: {
    id: string;
    title: string;
    doi: string | null;
    year: number | null;
    venue: string | null;
  };
}

export interface SearchResponse {
  query: string;
  mode: string;
  hits: SearchHit[];
}

export interface OpenAlexResult {
  openalex_id: string | null;
  doi: string | null;
  title: string;
  first_author: string | null;
  venue: string | null;
  year: number | null;
  abstract: string | null;
  cited_by_count: number;
}

export function firstAuthor(authors: unknown): string {
  // OpenAlex-ingested papers store a bare array of author objects; Zotero
  // ones store `{ names: [...] }` (see zotero_item_to_paper_fields on the
  // backend) — handle both shapes rather than only the OpenAlex one.
  const list = Array.isArray(authors)
    ? authors
    : Array.isArray((authors as { names?: unknown })?.names)
      ? (authors as { names: unknown[] }).names
      : [];
  if (list.length === 0) return '';
  const a = list[0];
  if (typeof a === 'string') return a;
  if (typeof a === 'object' && a !== null) {
    const obj = a as Record<string, string>;
    return obj.display_name ?? obj.name ?? obj.author_name ?? '';
  }
  return '';
}

export const papersApi = {
  listForProject: (projectId: string) =>
    api.get<Paper[]>(`/projects/${projectId}/papers`).then((r) => r.data),

  get: (paperId: string) =>
    api.get<Paper>(`/papers/${paperId}`).then((r) => r.data),

  ingest: (
    doi: string,
    projectId: string,
    found?: { foundQuote?: string; foundRelevanceNote?: string },
  ) =>
    api
      .post<Paper>('/papers', {
        doi,
        project_id: projectId,
        found_quote: found?.foundQuote,
        found_relevance_note: found?.foundRelevanceNote,
      })
      .then((r) => r.data),

  sections: (paperId: string) =>
    api.get<Section[]>(`/papers/${paperId}/sections`).then((r) => r.data),

  evidenceSentences: (paperId: string) =>
    api.get<SentenceEvidence[]>(`/papers/${paperId}/evidence-sentences`).then((r) => r.data),

  sentences: (paperId: string, sectionId?: string) =>
    api
      .get<Sentence[]>(`/papers/${paperId}/sentences`, {
        params: sectionId ? { section_id: sectionId } : undefined,
      })
      .then((r) => r.data),

  upload: (file: File, projectId: string, opts: { title?: string; doi?: string } = {}) => {
    const form = new FormData();
    form.append('file', file);
    form.append('project_id', projectId);
    if (opts.title) form.append('title', opts.title);
    if (opts.doi) form.append('doi', opts.doi);
    return api.post<Paper>('/papers/upload', form).then((r) => r.data);
  },

  update: (paperId: string, data: { title?: string; abstract?: string }) =>
    api.patch<Paper>(`/papers/${paperId}`, data).then((r) => r.data),

  reupload: (paperId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.post<Paper>(`/papers/${paperId}/reupload`, form).then((r) => r.data);
  },

  summarize: (paperId: string) =>
    api.post<Paper>(`/papers/${paperId}/summarize`).then((r) => r.data),

  retry: (paperId: string) =>
    api.post<Paper>(`/papers/${paperId}/retry`).then((r) => r.data),

  /** Unlinks the paper from this project; the Paper row itself is only
   * fully deleted server-side if no other project still references it. */
  delete: (paperId: string, projectId: string) =>
    api.delete(`/papers/${paperId}`, { params: { project_id: projectId } }),

  searchOpenalex: (q: string, perPage = 15) =>
    api.get<OpenAlexResult[]>('/papers/search', { params: { q, per_page: perPage } }).then((r) => r.data),

  search: (query: string, projectId: string, mode = 'hybrid', topK = 20) =>
    api
      .post<SearchResponse>('/search', {
        query,
        project_id: projectId,
        mode,
        top_k: topK,
      })
      .then((r) => r.data),
};
