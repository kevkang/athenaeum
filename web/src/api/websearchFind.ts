import { api } from './client';

/** A "Find" result via Claude's own web search — arbitrary web pages, no DOI,
 * nothing to import. quote/relevance_note come from Claude's own citations. */
export interface WebSearchFindResult {
  url: string;
  title: string;
  source: string | null;
  quote: string;
  relevance_note: string;
  model: string;
}

export const webSearchFindApi = {
  find: (query: string, projectId: string) =>
    api
      .post<WebSearchFindResult[]>('/websearch/find', { query, project_id: projectId })
      .then((r) => r.data),
};
