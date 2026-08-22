// Ports the item-mapping logic in src/athenaeum/ingest/zotero.py to TypeScript.
// Zotero's local API returns the same item JSON shape as the cloud Web API,
// so this mirrors `_hit_from_item` / `_creator_names` / `_year_from_date` /
// `_normalize_doi` field-for-field. Keep this in sync with the Python
// original by hand — see ZOTERO_EXTENSION_PLAN.md's decisions table for why
// this is a ported copy, not a shared import, across the Python/TS boundary.

import type { AthenaeumZoteroItem } from '../shared/protocol';

interface ZoteroCreator {
  name?: string;
  firstName?: string;
  lastName?: string;
}

export interface ZoteroItemData {
  key: string;
  itemType: string;
  title?: string;
  creators?: ZoteroCreator[];
  date?: string;
  publicationTitle?: string;
  websiteTitle?: string;
  DOI?: string;
  abstractNote?: string;
  dateAdded?: string;
}

// mirrors ingest/zotero.py:_normalize_doi
function normalizeDoi(doi: string): string {
  for (const prefix of ['https://doi.org/', 'http://doi.org/', 'doi:']) {
    if (doi.startsWith(prefix)) return doi.slice(prefix.length);
  }
  return doi;
}

// mirrors ingest/zotero.py:_creator_names
function creatorNames(data: ZoteroItemData): string[] {
  const names: string[] = [];
  for (const creator of data.creators ?? []) {
    const name = creator.name || [creator.firstName, creator.lastName].filter(Boolean).join(' ');
    if (name) names.push(name);
  }
  return names;
}

// mirrors ingest/zotero.py:_year_from_date
function yearFromDate(dateStr: string | undefined): number | null {
  if (!dateStr) return null;
  const match = dateStr.match(/(19|20)\d{2}/);
  return match ? parseInt(match[0], 10) : null;
}

/**
 * mirrors ingest/zotero.py:_hit_from_item — returns null for notes/attachments,
 * which aren't browsable/importable bibliographic items.
 *
 * `hasAttachment` is deliberately not resolved here: knowing whether an item
 * has a PDF attachment requires a separate children request per item, which
 * isn't worth an N+1 fetch for every row in a browse list. It's always false
 * from this mapper; the real check happens lazily at import time (build
 * order step 6), same as `pick_best_attachment` in the Python original is
 * only called once an item is actually being imported, not while browsing.
 */
export function itemFromZoteroData(data: ZoteroItemData): AthenaeumZoteroItem | null {
  if (data.itemType === 'note' || data.itemType === 'attachment') return null;

  const rawDoi = data.DOI || null;

  return {
    itemKey: data.key,
    itemType: data.itemType,
    title: data.title || '(untitled)',
    creators: creatorNames(data),
    year: yearFromDate(data.date),
    venue: data.publicationTitle || data.websiteTitle || null,
    doi: rawDoi ? normalizeDoi(rawDoi) : null,
    dateAdded: data.dateAdded ?? null,
    hasAttachment: false,
  };
}

// mirrors ingest/zotero.py:_DOWNLOADABLE_LINK_MODES
const DOWNLOADABLE_LINK_MODES = new Set(['imported_file', 'imported_url']);

export interface ZoteroAttachmentData {
  itemKey: string;
  contentType: string;
  linkMode: string;
}

/**
 * mirrors ingest/zotero.py:pick_best_attachment — prefers a downloadable
 * PDF, then a downloadable HTML snapshot; skips linked_file/linked_url
 * attachments (never reachable as bytes, local or cloud).
 *
 * Unlike the Python original, there's no `synced` check: that flag exists
 * because the cloud Web API can only serve a file if the user paid for
 * Zotero's file-storage sync, but the local API serves straight off local
 * disk regardless of any cloud-sync state (the whole reason this extension
 * exists) — an unsynced-to-cloud attachment is still fully downloadable
 * here. A missing/moved local file is instead handled as a 404 by
 * getItemFile at fetch time, not filtered out in advance.
 */
export function pickBestAttachment(attachments: ZoteroAttachmentData[]): ZoteroAttachmentData | null {
  const downloadable = attachments.filter((a) => DOWNLOADABLE_LINK_MODES.has(a.linkMode));
  return (
    downloadable.find((a) => a.contentType === 'application/pdf') ??
    downloadable.find((a) => a.contentType === 'text/html') ??
    null
  );
}
