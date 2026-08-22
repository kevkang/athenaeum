// Message shapes passed between the Athenaeum page (via window.postMessage),
// the content script relay, and the background service worker
// (via chrome.runtime.sendMessage). See ZOTERO_EXTENSION_PLAN.md step 3.

export const ATHENAEUM_ZOTERO_SOURCE = 'athenaeum-zotero-connector';

export interface AthenaeumZoteroItem {
  itemKey: string;
  itemType: string;
  title: string | null;
  creators: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  hasAttachment: boolean;
}

export interface AthenaeumZoteroCollection {
  key: string;
  name: string;
  parentKey: string | null;
}

export interface AthenaeumZoteroAnnotation {
  key: string;
  text: string | null;
  comment: string | null;
  color: string | null;
}

export type ZoteroRequest =
  | { type: 'ping' }
  | { type: 'browseItems'; collectionKey?: string; query?: string; start?: number; limit?: number }
  | { type: 'getCollections' }
  | { type: 'getItemFile'; itemKey: string }
  | { type: 'getAnnotations'; itemKey: string };

export type ZoteroResponse =
  | { type: 'pong' }
  | { type: 'items'; items: AthenaeumZoteroItem[]; total: number }
  | { type: 'collections'; collections: AthenaeumZoteroCollection[] }
  | { type: 'itemFile'; found: false }
  | { type: 'itemFile'; found: true; filename: string; contentType: string; bytesBase64: string }
  | { type: 'annotations'; annotations: AthenaeumZoteroAnnotation[] }
  | { type: 'error'; message: string };

export interface RelayEnvelope {
  source: typeof ATHENAEUM_ZOTERO_SOURCE;
  direction: 'request' | 'response';
  requestId: string;
  payload: ZoteroRequest | ZoteroResponse;
}
