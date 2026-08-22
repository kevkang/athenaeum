import * as zoteroLocal from './zoteroLocalClient';
import type { ZoteroRequest, ZoteroResponse } from '../shared/protocol';

async function handleRequest(request: ZoteroRequest): Promise<ZoteroResponse> {
  switch (request.type) {
    case 'ping': {
      const reachable = await zoteroLocal.ping();
      if (!reachable) return { type: 'error', message: 'Zotero local API not reachable' };
      return { type: 'pong' };
    }

    case 'browseItems': {
      const { items, total } = await zoteroLocal.browseItems(request);
      return { type: 'items', items, total };
    }

    case 'getCollections': {
      const collections = await zoteroLocal.getCollections();
      return { type: 'collections', collections };
    }

    case 'getAnnotations': {
      const annotations = await zoteroLocal.getAnnotations(request.itemKey);
      return { type: 'annotations', annotations };
    }

    case 'getItem': {
      const item = await zoteroLocal.getItem(request.itemKey);
      return { type: 'item', item };
    }

    case 'getItemFile': {
      const result = await zoteroLocal.getItemFile(request.itemKey);
      if (!result.found) return { type: 'itemFile', found: false };
      return {
        type: 'itemFile',
        found: true,
        filename: result.filename,
        contentType: result.contentType,
        bytesBase64: arrayBufferToBase64(result.bytes),
      };
    }

    case 'getBestAttachmentFile': {
      const result = await zoteroLocal.getBestAttachmentFile(request.itemKey);
      if (!result.found) return { type: 'itemFile', found: false };
      return {
        type: 'itemFile',
        found: true,
        filename: result.filename,
        contentType: result.contentType,
        bytesBase64: arrayBufferToBase64(result.bytes),
      };
    }
  }
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

chrome.runtime.onMessage.addListener((message: ZoteroRequest, _sender, sendResponse) => {
  handleRequest(message)
    .then(sendResponse)
    .catch((err: unknown) =>
      sendResponse({ type: 'error', message: err instanceof Error ? err.message : String(err) }),
    );
  return true; // keep the message channel open for the async sendResponse
});
