import { api } from './client';
import type { Paper } from './papers';

export interface ZoteroCredentialsStatus {
  connected: boolean;
  zotero_user_id: string | null;
}

export interface ZoteroSearchResult {
  item_key: string;
  title: string;
  creators: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  item_type: string;
  already_in_project: boolean;
  date_added: string | null;
}

export type ZoteroFindCorpus = 'zotero_sync' | 'zotero_local';

/** A "Find" result over the whole Zotero library (sync or local), not just
 * items already imported into the project — see zoteroFindApi.find(). */
export interface ZoteroFindResult {
  item_key: string;
  title: string;
  creators: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  item_type: string;
  already_in_project: boolean;
  quote: string;
  relevance_note: string;
  model: string;
}

export interface ZoteroCollection {
  key: string;
  name: string;
  parent_key: string | null;
}

export interface ZoteroBrowseResponse {
  items: ZoteroSearchResult[];
  total: number;
}

export type ZoteroSortField = 'title' | 'creator' | 'year' | 'date_added';
export type ZoteroSortDirection = 'asc' | 'desc';

export interface ZoteroBrowseParams {
  projectId: string;
  collectionKey?: string;
  q?: string;
  start?: number;
  limit?: number;
  sort?: ZoteroSortField;
  direction?: ZoteroSortDirection;
}

/** The subset of zoteroApi's shape ZoteroBrowserModal actually needs — lets
 * it be pointed at either the Web API client (zoteroApi) or the local one
 * (zoteroLocalApi) without duplicating the modal. */
export interface ZoteroBrowseClient {
  getCollections: () => Promise<ZoteroCollection[]>;
  browseItems: (params: ZoteroBrowseParams) => Promise<ZoteroBrowseResponse>;
  importItem: (projectId: string, zoteroItemKey: string) => Promise<Paper>;
}

export const zoteroApi = {
  getStatus: () => api.get<ZoteroCredentialsStatus>('/zotero/credentials').then((r) => r.data),

  saveCredentials: (apiKey: string) =>
    api
      .put<ZoteroCredentialsStatus>('/zotero/credentials', { api_key: apiKey })
      .then((r) => r.data),

  disconnect: () => api.delete('/zotero/credentials').then(() => undefined),

  search: (q: string, projectId: string) =>
    api
      .get<ZoteroSearchResult[]>('/zotero/search', { params: { q, project_id: projectId } })
      .then((r) => r.data),

  /** Use a Zotero item as the full text of a paper Athenaeum already has
   * (a Nucleate node that needs it), instead of adding a new paper. */
  supplyPaper: (projectId: string, zoteroItemKey: string, paperId: string) =>
    api
      .post<Paper>('/zotero/import', {
        project_id: projectId, zotero_item_key: zoteroItemKey, paper_id: paperId,
      })
      .then((r) => r.data),

  importItem: (
    projectId: string,
    zoteroItemKey: string,
    found?: { foundQuote?: string; foundRelevanceNote?: string },
  ) =>
    api
      .post<Paper>('/zotero/import', {
        project_id: projectId,
        zotero_item_key: zoteroItemKey,
        found_quote: found?.foundQuote,
        found_relevance_note: found?.foundRelevanceNote,
      })
      .then((r) => r.data),

  getCollections: () =>
    api.get<ZoteroCollection[]>('/zotero/collections').then((r) => r.data),

  browseItems: (params: ZoteroBrowseParams) =>
    api
      .get<ZoteroBrowseResponse>('/zotero/items', {
        params: {
          project_id: params.projectId,
          collection_key: params.collectionKey,
          q: params.q || undefined,
          start: params.start ?? 0,
          limit: params.limit ?? 50,
          sort: params.sort ?? 'date_added',
          direction: params.direction ?? 'desc',
        },
      })
      .then((r) => r.data),
};

export const zoteroFindApi = {
  find: (query: string, projectId: string, corpus: ZoteroFindCorpus) =>
    api
      .post<ZoteroFindResult[]>('/zotero/find', { query, project_id: projectId, corpus })
      .then((r) => r.data),
};
