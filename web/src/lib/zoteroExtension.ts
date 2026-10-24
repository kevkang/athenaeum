// Bridges to the Athenaeum Zotero Connector browser extension (../../extension/),
// via window.postMessage, relayed by the extension's content script to its
// background worker, which holds the only host_permissions for Zotero
// Desktop's local API. See ZOTERO_EXTENSION_PLAN.md step 4.
//
// The message envelope/payload shapes here are a hand-kept mirror of
// extension/src/shared/protocol.ts — these are two separate packages
// (extension/ has no build dependency on web/, and vice versa), so this is
// duplicated by design, not shared via import. Keep the two in sync by hand.

import { papersApi, type Paper } from '../api/papers';
import type { ZoteroBrowseClient, ZoteroBrowseParams, ZoteroBrowseResponse, ZoteroCollection } from '../api/zotero';

const SOURCE = 'athenaeum-zotero-connector';

interface AthenaeumZoteroItem {
  itemKey: string;
  itemType: string;
  title: string | null;
  creators: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  dateAdded: string | null;
  hasAttachment: boolean;
}

interface AthenaeumZoteroCollection {
  key: string;
  name: string;
  parentKey: string | null;
}

type ZoteroRequest =
  | { type: 'ping' }
  | {
      type: 'browseItems';
      collectionKey?: string;
      query?: string;
      start?: number;
      limit?: number;
      sort?: 'title' | 'creator' | 'date' | 'dateAdded';
      direction?: 'asc' | 'desc';
    }
  | { type: 'getCollections' }
  | { type: 'getItem'; itemKey: string }
  | { type: 'getBestAttachmentFile'; itemKey: string }
  | { type: 'getAnnotations'; itemKey: string };

interface AthenaeumZoteroAnnotation {
  key: string;
  text: string | null;
  comment: string | null;
  color: string | null;
}

type ZoteroResponse =
  | { type: 'pong' }
  | { type: 'items'; items: AthenaeumZoteroItem[]; total: number }
  | { type: 'collections'; collections: AthenaeumZoteroCollection[] }
  | { type: 'item'; item: AthenaeumZoteroItem | null }
  | { type: 'itemFile'; found: false }
  | { type: 'itemFile'; found: true; filename: string; contentType: string; bytesBase64: string }
  | { type: 'annotations'; annotations: AthenaeumZoteroAnnotation[] }
  | { type: 'error'; message: string };

interface RelayEnvelope {
  source: typeof SOURCE;
  direction: 'request' | 'response';
  requestId: string;
  payload: ZoteroRequest | ZoteroResponse;
}

function sendZoteroRequest(payload: ZoteroRequest, timeoutMs = 5000): Promise<ZoteroResponse> {
  return new Promise((resolve, reject) => {
    const requestId = Math.random().toString(36).slice(2);
    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      reject(new Error('Zotero Connector extension did not respond in time'));
    }, timeoutMs);

    function handler(event: MessageEvent) {
      if (event.source !== window) return;
      const envelope = event.data as RelayEnvelope | undefined;
      if (
        !envelope ||
        envelope.source !== SOURCE ||
        envelope.direction !== 'response' ||
        envelope.requestId !== requestId
      ) {
        return;
      }
      clearTimeout(timer);
      window.removeEventListener('message', handler);
      resolve(envelope.payload as ZoteroResponse);
    }

    window.addEventListener('message', handler);
    const request: RelayEnvelope = { source: SOURCE, direction: 'request', requestId, payload };
    window.postMessage(request, window.location.origin);
  });
}

/** Pings the extension with a short timeout — resolves false (never throws)
 * if the extension isn't installed, Zotero Desktop isn't running, or its
 * Local API preference is disabled. Callers that need to distinguish those
 * cases should call sendZoteroRequest directly and inspect the error. */
export async function pingExtension(timeoutMs = 500): Promise<boolean> {
  try {
    const response = await sendZoteroRequest({ type: 'ping' }, timeoutMs);
    return response.type === 'pong';
  } catch {
    return false;
  }
}

function toBrowseItem(item: AthenaeumZoteroItem, alreadyInProjectDois: ReadonlySet<string>) {
  return {
    item_key: item.itemKey,
    title: item.title ?? '(untitled)',
    creators: item.creators,
    year: item.year,
    venue: item.venue,
    doi: item.doi,
    item_type: item.itemType,
    already_in_project: item.doi !== null && alreadyInProjectDois.has(item.doi),
    date_added: item.dateAdded,
  };
}

function base64ToFile(base64: string, filename: string, contentType: string): File {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], filename, { type: contentType });
}

/** Implements ZoteroBrowseClient against the live extension bridge instead of
 * a backend endpoint — path 4 is architecturally identical to path 3
 * (ZoteroBrowserImportModal): the browser holds both the metadata and the
 * PDF bytes, so importItem reuses papersApi.upload/.ingest directly, exactly
 * as ZoteroBrowserImportModal.tsx does, rather than adding a new endpoint
 * (see ZOTERO_EXTENSION_PLAN.md's revised decision on this).
 *
 * `alreadyInProjectDois` is supplied by the caller (from the project's
 * already-loaded papers) since there's no server round-trip here to compute
 * it from — see PapersSidebar.tsx's existing `alreadyInProject` Set. */
export function createZoteroExtensionApi(alreadyInProjectDois: ReadonlySet<string>): ZoteroBrowseClient {
  return {
    getCollections: async (): Promise<ZoteroCollection[]> => {
      const response = await sendZoteroRequest({ type: 'getCollections' });
      if (response.type !== 'collections') throw new Error('Unexpected response from Zotero extension');
      return response.collections.map((c) => ({ key: c.key, name: c.name, parent_key: c.parentKey }));
    },

    browseItems: async (params: ZoteroBrowseParams): Promise<ZoteroBrowseResponse> => {
      const sortMap = { title: 'title', creator: 'creator', year: 'date', date_added: 'dateAdded' } as const;
      const response = await sendZoteroRequest({
        type: 'browseItems',
        collectionKey: params.collectionKey,
        query: params.q || undefined,
        start: params.start ?? 0,
        limit: params.limit ?? 50,
        sort: sortMap[params.sort ?? 'date_added'],
        direction: params.direction ?? 'desc',
      });
      if (response.type !== 'items') throw new Error('Unexpected response from Zotero extension');
      return {
        items: response.items.map((item) => toBrowseItem(item, alreadyInProjectDois)),
        total: response.total,
      };
    },

    importItem: async (projectId: string, zoteroItemKey: string): Promise<Paper> => {
      const itemResponse = await sendZoteroRequest({ type: 'getItem', itemKey: zoteroItemKey });
      if (itemResponse.type !== 'item' || !itemResponse.item) {
        throw new Error('Zotero item not found');
      }
      const item = itemResponse.item;

      const fileResponse = await sendZoteroRequest({ type: 'getBestAttachmentFile', itemKey: zoteroItemKey });
      if (fileResponse.type === 'itemFile' && fileResponse.found) {
        const file = base64ToFile(fileResponse.bytesBase64, fileResponse.filename, fileResponse.contentType);
        return papersApi.upload(file, projectId, {
          title: item.title ?? undefined,
          doi: item.doi ?? undefined,
          zoteroItemKey,
        });
      }

      if (item.doi) {
        return papersApi.ingest(item.doi, projectId, { zoteroItemKey });
      }

      throw new Error('No PDF or DOI available to import this item');
    },
  };
}

/** Fetches highlight/underline annotations for a Zotero item through the
 * extension bridge, for ZoteroHighlightImportModal's extension-backed data
 * source (papers with zotero_source === 'extension'; see
 * ZOTERO_EXTENSION_PLAN.md step 7). Throws if the extension isn't reachable
 * — callers should surface that as an error state, same as a failed
 * server-side preview fetch. */
export async function getAnnotationsViaExtension(zoteroItemKey: string) {
  const response = await sendZoteroRequest({ type: 'getAnnotations', itemKey: zoteroItemKey });
  if (response.type !== 'annotations') throw new Error('Unexpected response from Zotero extension');
  return response.annotations
    .filter((a): a is AthenaeumZoteroAnnotation & { text: string } => a.text !== null)
    .map((a) => ({ item_key: a.key, text: a.text, comment: a.comment, color: a.color }));
}
