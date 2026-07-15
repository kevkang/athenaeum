import { api } from './client';

/** A "Find" result over OpenAlex's global database — mirrors ZoteroFindResult. */
export interface OpenAlexFindResult {
  openalex_id: string | null;
  doi: string | null;
  title: string;
  creators: string[];
  year: number | null;
  venue: string | null;
  cited_by_count: number;
  already_in_project: boolean;
  quote: string;
  relevance_note: string;
}

export const openAlexFindApi = {
  find: (query: string, projectId: string) =>
    api
      .post<OpenAlexFindResult[]>('/openalex/find', { query, project_id: projectId })
      .then((r) => r.data),
};
