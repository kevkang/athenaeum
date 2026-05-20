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

export function firstAuthor(authors: unknown): string {
  if (!Array.isArray(authors) || authors.length === 0) return '';
  const a = authors[0];
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

  ingest: (doi: string, projectId: string) =>
    api.post<Paper>('/papers', { doi, project_id: projectId }).then((r) => r.data),

  sections: (paperId: string) =>
    api.get<Section[]>(`/papers/${paperId}/sections`).then((r) => r.data),

  sentences: (paperId: string, sectionId?: string) =>
    api
      .get<Sentence[]>(`/papers/${paperId}/sentences`, {
        params: sectionId ? { section_id: sectionId } : undefined,
      })
      .then((r) => r.data),

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
