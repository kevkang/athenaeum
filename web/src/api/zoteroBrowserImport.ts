import { api } from './client';
import type { ZoteroCollection } from './zotero';

export interface ZoteroBrowserImportItem {
  item_key: string;
  title: string;
  creators: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  item_type: string;
  collection_keys: string[];
  attachment_relative_path: string | null;
  already_in_project: boolean;
}

export interface ZoteroBrowserImportParseResponse {
  items: ZoteroBrowserImportItem[];
  collections: ZoteroCollection[];
}

/** Client-side counterpart to zoteroLocalApi — parses a zotero.sqlite the
 * browser read from a user-picked folder (see lib/zoteroFolderAccess.ts)
 * instead of one bind-mounted on the server. Only the sqlite file is ever
 * uploaded here; matching PDFs go through the ordinary papersApi.upload(). */
export const zoteroBrowserImportApi = {
  parse: (sqliteFile: File, projectId: string) => {
    const form = new FormData();
    form.append('file', sqliteFile, 'zotero.sqlite');
    return api
      .post<ZoteroBrowserImportParseResponse>('/zotero-local/browser-import/parse', form, {
        params: { project_id: projectId },
      })
      .then((r) => r.data);
  },
};
