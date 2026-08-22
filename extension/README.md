# Athenaeum Zotero Connector

MV3 browser extension that bridges Athenaeum's web app to a locally running Zotero Desktop's
local API (`http://127.0.0.1:23119`). See `../ZOTERO_EXTENSION_PLAN.md` for the full plan and
`../extension-plan-context.md` for the underlying research this is based on.

## Prerequisites

- Zotero Desktop running, with **Settings → Advanced → "Allow other applications on this
  computer to communicate with Zotero"** and the local API enabled
  (`extensions.zotero.httpServer.localAPI.enabled` in the config editor, if not exposed directly
  in your Zotero version). Off by default — undocumented by Zotero itself, discovered while
  building step 1 of this plan.

## Build

```
npm install
npm run build      # one-shot build to dist/
npm run watch       # rebuild on change
npm run typecheck
```

## Load unpacked in Chrome/Edge

`chrome://extensions` → enable Developer mode → "Load unpacked" → select the `dist/` folder
(**not** the `extension/` folder itself — the manifest and compiled JS both live in `dist/`
after a build).

## Current status

Steps 1–4 of `ZOTERO_EXTENSION_PLAN.md`'s build order: background worker with a typed local-API
client (`browseItems`, `getCollections`, `getAnnotations`, `getItemFile`, `getBestAttachmentFile`),
a content-script relay, and the frontend's `zoteroExtensionApi` in `web/src/lib/zoteroExtension.ts`.
`content_scripts.matches` covers local dev (`localhost`/`127.0.0.1`) and Athenaeum's real domain
(`athenaindex.app` and its subdomains, which covers PR preview deploys at `pr-N.athenaindex.app`).
