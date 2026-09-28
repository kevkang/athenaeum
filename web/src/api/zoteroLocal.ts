import { api } from './client';
import type { Paper } from './papers';
import type {
  ZoteroBrowseParams,
  ZoteroBrowseResponse,
  ZoteroCollection,
  ZoteroSearchResult,
} from './zotero';

export interface ZoteroLocalStatus {
  available: boolean;
  relative_path: string | null;
}

export interface ZoteroLocalDirEntry {
  name: string;
  has_zotero_sqlite: boolean;
}

export interface ZoteroLocalBrowseDirsResponse {
  path: string;
  parent: string | null;
  entries: ZoteroLocalDirEntry[];
}

/** Same response shapes as zoteroApi (the Web API / "Zotero Sync" client) —
 * this reads a bind-mounted local zotero.sqlite instead. The operator's whole
 * home directory is bind-mounted read-only as infrastructure (docker-compose);
 * which subfolder under that is the actual Zotero data directory is chosen
 * from the UI via selectLibrary() and persisted server-side. Structurally
 * compatible with ZoteroBrowseClient so ZoteroBrowserModal can be pointed at
 * either source. */
export const zoteroLocalApi = {
  getStatus: () => api.get<ZoteroLocalStatus>('/zotero-local/status').then((r) => r.data),

  /** relativePath is relative to the operator's home directory, e.g. "Zotero". */
  selectLibrary: (relativePath: string) =>
    api
      .post<ZoteroLocalStatus>('/zotero-local/select', { relative_path: relativePath })
      .then((r) => r.data),

  clearLibrary: () => api.delete('/zotero-local/select').then(() => undefined),

  /** path is relative to the home directory ("" for the root itself). */
  browseDirs: (path: string) =>
    api
      .get<ZoteroLocalBrowseDirsResponse>('/zotero-local/browse-dirs', { params: { path } })
      .then((r) => r.data),

  search: (q: string, projectId: string) =>
    api
      .get<ZoteroSearchResult[]>('/zotero-local/search', { params: { q, project_id: projectId } })
      .then((r) => r.data),

  /** Use a Zotero item as the full text of a paper Athenaeum already has
   * (a Nucleate node that needs it), instead of adding a new paper. */
  supplyPaper: (projectId: string, zoteroItemKey: string, paperId: string) =>
    api
      .post<Paper>('/zotero-local/import', {
        project_id: projectId, zotero_item_key: zoteroItemKey, paper_id: paperId,
      })
      .then((r) => r.data),

  importItem: (
    projectId: string,
    zoteroItemKey: string,
    found?: { foundQuote?: string; foundRelevanceNote?: string },
  ) =>
    api
      .post<Paper>('/zotero-local/import', {
        project_id: projectId,
        zotero_item_key: zoteroItemKey,
        found_quote: found?.foundQuote,
        found_relevance_note: found?.foundRelevanceNote,
      })
      .then((r) => r.data),

  getCollections: () =>
    api.get<ZoteroCollection[]>('/zotero-local/collections').then((r) => r.data),

  browseItems: (params: ZoteroBrowseParams) =>
    api
      .get<ZoteroBrowseResponse>('/zotero-local/items', {
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
