// Relays messages between the Athenaeum page (window.postMessage) and the
// background service worker (chrome.runtime.sendMessage). This is the only
// thing the content script does — no page scripting, no other permissions.
// Scoped via manifest.json's content_scripts.matches to Athenaeum's own
// origin(s) + local dev, never <all_urls>.

import { ATHENAEUM_ZOTERO_SOURCE, type RelayEnvelope } from '../shared/protocol';

window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) return;

  const envelope = event.data as RelayEnvelope | undefined;
  if (!envelope || envelope.source !== ATHENAEUM_ZOTERO_SOURCE || envelope.direction !== 'request') {
    return;
  }

  chrome.runtime.sendMessage(envelope.payload).then((responsePayload) => {
    const responseEnvelope: RelayEnvelope = {
      source: ATHENAEUM_ZOTERO_SOURCE,
      direction: 'response',
      requestId: envelope.requestId,
      payload: responsePayload,
    };
    window.postMessage(responseEnvelope, window.location.origin);
  });
});
