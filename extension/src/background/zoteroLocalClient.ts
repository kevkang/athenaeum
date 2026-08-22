// The only module allowed to hold host_permissions for 127.0.0.1:23119 —
// no other extension code should fetch this origin directly (least
// privilege, see ZOTERO_EXTENSION_PLAN.md security checklist).

import { itemFromZoteroData, type ZoteroItemData } from './itemMapper';
import type {
  AthenaeumZoteroAnnotation,
  AthenaeumZoteroCollection,
  AthenaeumZoteroItem,
} from '../shared/protocol';

const BASE_URL = 'http://127.0.0.1:23119/api';
// Local API accepts userID 0 or the actual logged-in user's numeric ID
// interchangeably for "my library" (verified against zotero source,
// server_localAPI.js, 2026-08-21) — 0 is simpler and avoids a lookup.
const USER_PATH = `${BASE_URL}/users/0`;

export class ZoteroLocalApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ZoteroLocalApiError';
  }
}

async function localFetch(path: string, params?: Record<string, string | number | undefined>): Promise<unknown> {
  const url = new URL(`${USER_PATH}${path}`);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const response = await fetch(url, {
    headers: { 'X-Zotero-Connector-API-Version': '1' },
  });

  const text = await response.text();

  if (!response.ok) {
    // Local API returns plain-text error bodies (e.g. 403 "Local API is not
    // enabled"), not JSON — never try to JSON.parse on a non-ok response.
    throw new ZoteroLocalApiError(text || `HTTP ${response.status}`, response.status);
  }

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return text;

  return JSON.parse(text);
}

export async function ping(): Promise<boolean> {
  try {
    await localFetch('/items', { limit: 1 });
    return true;
  } catch {
    return false;
  }
}

export async function browseItems(opts: {
  collectionKey?: string;
  query?: string;
  start?: number;
  limit?: number;
}): Promise<{ items: AthenaeumZoteroItem[]; total: number }> {
  const path = opts.collectionKey ? `/collections/${opts.collectionKey}/items/top` : '/items/top';
  const data = (await localFetch(path, {
    q: opts.query,
    start: opts.start ?? 0,
    limit: opts.limit ?? 50,
  })) as ZoteroItemDataWrapper[];

  const items = data
    .map((entry) => itemFromZoteroData(entry.data))
    .filter((item): item is AthenaeumZoteroItem => item !== null);

  return { items, total: items.length };
}

export async function getCollections(): Promise<AthenaeumZoteroCollection[]> {
  const data = (await localFetch('/collections')) as ZoteroCollectionDataWrapper[];
  return data.map((entry) => ({
    key: entry.data.key,
    name: entry.data.name,
    parentKey: entry.data.parentCollection || null,
  }));
}

export async function getAnnotations(itemKey: string): Promise<AthenaeumZoteroAnnotation[]> {
  const data = (await localFetch(`/items/${itemKey}/children`)) as ZoteroItemDataWrapper[];
  return data
    .filter((entry) => entry.data.itemType === 'annotation')
    .map((entry) => ({
      key: entry.data.key,
      text: entry.data.annotationText ?? null,
      comment: entry.data.annotationComment ?? null,
      color: entry.data.annotationColor ?? null,
    }));
}

export async function getItemFile(
  itemKey: string,
): Promise<{ found: false } | { found: true; filename: string; contentType: string; bytes: ArrayBuffer }> {
  const url = `${USER_PATH}/items/${itemKey}/file`;
  const response = await fetch(url, {
    headers: { 'X-Zotero-Connector-API-Version': '1' },
  });

  if (response.status === 404) return { found: false };
  if (!response.ok) {
    throw new ZoteroLocalApiError(await response.text(), response.status);
  }

  const contentDisposition = response.headers.get('content-disposition') ?? '';
  const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);

  return {
    found: true,
    filename: filenameMatch?.[1] ?? `${itemKey}.pdf`,
    contentType: response.headers.get('content-type') ?? 'application/pdf',
    bytes: await response.arrayBuffer(),
  };
}

interface ZoteroItemDataWrapper {
  data: ZoteroItemData & {
    itemType: string;
    key: string;
    annotationText?: string;
    annotationComment?: string;
    annotationColor?: string;
  };
}

interface ZoteroCollectionDataWrapper {
  data: { key: string; name: string; parentCollection: string | false };
}
