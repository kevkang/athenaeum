# Zotero Connector Extension — Implementation Plan

Status: **not started**. Supersedes/operationalizes the investigation in
`extension-plan-context.md` (source of the architecture and security research below) with a
concrete build plan checked against the actual current state of the codebase (2026-08-21).

## What's changed since the context doc was written

The context doc's "related work happening in parallel" section is no longer parallel — it's
landed:

- **Highlight import is built** (commit `65ae02d`): `src/athenaeum/api/zotero_highlights.py`,
  `web/src/api/zoteroHighlights.ts`, `web/src/components/ZoteroHighlightImportModal.tsx`. Step 4
  below plugs into this directly instead of coordinating with in-flight work.
- **A fourth Zotero surface exists that the context doc doesn't mention**: "Zotero-library Find"
  (`src/athenaeum/api/zotero_find.py`, `web/src/api/zotero.ts`'s `zoteroFindApi`) — an AI-panel
  feature for finding items in the user's Zotero library. Out of scope for this extension (it's a
  search/reasoning feature, not an ingestion path), but worth knowing about so it isn't confused
  with the four ingestion paths.
- **The frontend already has the exact seam this plan needs.** `web/src/api/zotero.ts` defines
  `ZoteroBrowseClient`, an interface deliberately factored out so `ZoteroBrowserModal.tsx` "can be
  pointed at either the Web API client (zoteroApi) or the local one (zoteroLocalApi) without
  duplicating the modal" (`zotero.ts:63-70`). `zoteroLocalApi` (`web/src/api/zoteroLocal.ts`)
  already conforms to it. This means the v1 UI work (build-order step 2) is not "build parity with
  the existing browse UI" — it's "write one more object satisfying `ZoteroBrowseClient`," and the
  existing modal, its collection tree, search, and table rendering are reused verbatim, zero
  duplication. This is a stronger reuse story than the context doc assumed.
- **The backend has a settled four-times-repeated route shape to copy exactly.** Both
  `api/zotero.py` and `api/zotero_local.py` independently converged on the same shape:
  `GET /credentials|status`, `GET /search`, `GET /collections`, `GET /items`, `POST /import`, each
  gated by `_require_member(session, project_id, user.id)`. This directly answers the context
  doc's open question ("does the backend need a new endpoint, or can the extension reuse
  `papersApi.ingest`/`.upload` directly?") — **yes, a new endpoint module, mirroring this exact
  shape.** Inventing a different pattern for a fourth source would be the outlier, not the
  reasonable choice.

Everything else in the context doc — the CORS/DNS-rebinding research, the three-piece extension
architecture, the security scoping, the non-goals — still holds and isn't re-derived here. Read
that doc first if you haven't.

## Design decisions this plan is making (resolving the context doc's open questions)

| Question | Decision | Reasoning |
|---|---|---|
| New backend endpoint, or reuse `papersApi.ingest`/`.upload`? | **New endpoint module**, `src/athenaeum/api/zotero_connector.py`, mirroring `zotero.py`/`zotero_local.py`'s route shape. PDF bytes still go through the *existing*, unmodified `papersApi.upload`. | Consistent with the other three paths; keeps DOI-dedup (`_existing_dois`) and project-membership checks in one place instead of re-implementing them client-side. |
| Extension source location | `extension/` subfolder at repo root, own `package.json`, not a workspace member of `web/` (the repo has no root `package.json`/workspace tooling — `web/` is a standalone Vite app). | Discoverable alongside the backend endpoint it depends on, versioned together, but doesn't force `web/`'s toolchain to absorb a WebExtension build target it doesn't need. |
| Raw MV3 vs. framework (WXT/Plasmo) | **Raw MV3 + `webextension-polyfill`** for v1. | v1 is Chrome/Edge-only (identical MV3 engine) — a cross-browser framework buys nothing until the Firefox/Safari phase, and adds a build-tool dependency + learning curve now for zero v1 benefit. Revisit when Firefox milestone starts; `zotero-connectors` itself is proof this can be done in raw MV3 if kept disciplined about it. |
| Metadata port target | Port `zotero_item_to_paper_fields` / `_hit_from_item`'s mapping logic (`ingest/zotero.py:176,300`) to TypeScript in the extension, since the local API's response shape is the same Zotero item JSON. | The context doc notes this reuse is possible; it means "reusable" as *logic ported to the extension's language*, not *imported cross-language* — Python and TS can't share source. Keep the TS port's field list in sync with the Python original by comment cross-reference (`# mirrors ingest/zotero.py:_hit_from_item`), not by abstraction. |

## Repo layout

```
extension/
  manifest.json                    # MV3, host_permissions: 127.0.0.1:23119 only
  package.json                     # own build, esbuild or vite, no workspace linkage to web/
  tsconfig.json
  src/
    background/
      index.ts                     # service worker entrypoint, message router
      zoteroLocalClient.ts         # fetch() wrapper for http://127.0.0.1:23119/api/*
      itemMapper.ts                # ported zotero_item_to_paper_fields / _hit_from_item
    content/
      relay.ts                     # window.postMessage <-> chrome.runtime.sendMessage bridge
    shared/
      protocol.ts                  # message type/schema shared between background & content
  icons/
  dist/                            # build output, gitignored
```

## Build order

Each numbered step is independently shippable/testable — don't start N+1 before N's manual
verification passes.

### 1. Prove the CORS-bypass claim empirically

Bare `manifest.json` (`manifest_version: 3`, `host_permissions: ["http://127.0.0.1:23119/*"]`,
`background.service_worker`), no content script yet. Background worker does:

```ts
fetch('http://127.0.0.1:23119/api/users/0/items?limit=1', {
  headers: { 'X-Zotero-Connector-API-Version': '1' },
}).then(r => r.json()).then(console.log);
```

Load unpacked in Chrome, open the service worker's console, confirm it logs real item data with
Zotero Desktop running and fails cleanly (network error, not a CORS error) with Zotero Desktop
closed. This is the whole point of the context doc's research — verify it firsthand before
building anything on top of it. Budget: under a day; if this doesn't work as documented, the rest
of the plan needs to be revisited before proceeding.

**Exit criteria:** logged JSON response with Zotero running; distinguishable failure mode with it
stopped.

**Verified 2026-08-21.** Confirms the context doc's core claim: the extension's `fetch()` is not
CORS-blocked. One undocumented prerequisite found that the context doc didn't mention: the local
API is gated behind a Zotero preference, `httpServer.localAPI.enabled`, **off by default** — a
plain webpage fetch and the unconfigured local API both fail, so this needs its own UX handling
(step 5's extension-detection ping should distinguish "extension not installed," "Zotero not
running," and "Zotero running but Local API disabled" — the latter needs its own message pointing
the user at Settings → Advanced, not a generic "not detected" fallback). Confirmed via
`server_localAPI.js`: disabled requests get `403 "Local API is not enabled"` as a plain-text body,
not JSON — the step 2 client needs to check `response.ok`/content-type before parsing JSON, not
assume every response is valid JSON on success.

### 2. Background service worker — full local API client

`extension/src/background/zoteroLocalClient.ts`: typed wrappers over the local API surface v1
needs —

- `GET /api/users/0/items` (browse/search — local API uses `0` as the userID placeholder for "my
  library"; confirm exact semantics against `zotero-connectors` source, not assumed)
- `GET /api/users/0/collections`
- `GET /api/users/0/items/:itemKey/children` (attachments + annotations)
- `GET /api/users/0/items/:itemKey/file` (PDF bytes)

Every request sends `X-Zotero-Connector-API-Version`. No other extension code is allowed to import
this module directly — it's the only thing with `host_permissions` for `127.0.0.1`, per the
context doc's least-privilege scoping.

`extension/src/background/itemMapper.ts`: ports the shape of `_hit_from_item` /
`zotero_item_to_paper_fields` (`src/athenaeum/ingest/zotero.py:176,300`) — title, creators, year,
venue, DOI, item type extraction from raw Zotero item JSON — so the background worker emits
already-normalized objects, not raw Zotero JSON, to the content script.

**Exit criteria:** unit-testable pure functions for mapping (Vitest), manually verified against a
real local library covering: item with DOI, item without DOI, item with a PDF attachment, item
with no attachment, an annotation on a PDF.

**Built 2026-08-21.** `zoteroLocalClient.ts` (typed client for `/items/top`, `/collections`,
`/items/:key/children`, `/items/:key/file`) and `itemMapper.ts` (ported mapping, 6 Vitest cases,
all passing). Response-parsing fix baked in from the start: the client checks `response.ok` and
`content-type` before ever calling `JSON.parse` — a non-ok response can be plain text (see step
1's finding above).

**Real-data verification, 2026-08-22.** `browseItems`/`getCollections` confirmed correct
immediately against 3 real papers (titles, creators, DOIs, venues, years all matched Zotero).
`getAnnotations` needed a real fix, not just a test: `GET /items/:attachmentKey/children` **does
not reliably return annotation items**, even for a highlight visible in Zotero's own UI and
returned by `GET /items?itemType=annotation` — confirmed after ruling out a stale-cache theory
(full Zotero Desktop restart didn't change the result). Zotero's own source doesn't explicitly
exclude `itemType=annotation` from `/children`'s search conditions, so this is presumably an
internal quirk of which relation that endpoint's query actually walks, not a documented
restriction — treat it as an opaque fact about the local API, not something worth chasing further
upstream. **Fix:** `getAnnotations` now finds an item's attachment keys via `/children` (which
*does* work for `itemType: attachment`/`note`), then separately paginates
`GET /items?itemType=annotation` library-wide and filters by each annotation's `parentItem` field
matching one of those attachment keys (capped at 20 pages / 2000 annotations as a safety bound —
revisit if that's ever a real constraint). Verified against all 3 real annotations: correct text,
color, and comment on the two papers they were actually added to, empty array on the one with
none.

### 3. Content script + message protocol

`extension/src/shared/protocol.ts` defines the request/response message shapes (ping, browse,
search, get-collections, get-item-file, get-annotations), discriminated by a `type` field.

`extension/src/content/relay.ts`: listens for `window.postMessage` from the Athenaeum page
(origin-checked against its own `matches` list, not `*`), forwards to
`chrome.runtime.sendMessage`, relays the response back via `postMessage`. Declared `matches` in
the manifest: Athenaeum's production origin(s) + `http://localhost:*/*` for local dev against
`web/`'s Vite dev server. No other scripting permission.

**Exit criteria:** a throwaway test page (or `web/`'s dev server with a temporary debug button)
can `postMessage` a ping and get a pong back through the full relay chain.

**Verified 2026-08-21**, against `web/`'s real dev server (no throwaway page needed) — a
`postMessage`'d `ping` round-tripped through content script → background worker → Zotero local
API → back and resolved `{ type: 'pong' }`.

### 4. Frontend: extension detection + `ZoteroBrowseClient` implementation

New `web/src/lib/zoteroExtension.ts`:
- `pingExtension(timeoutMs = 500): Promise<boolean>` — posts a ping, races against a timeout.
- `zoteroExtensionApi: ZoteroBrowseClient` — implements `getCollections`, `browseItems`,
  `importItem` by relaying through the content script and (for `importItem`) calling the new
  backend endpoint from step 5 with the extension-supplied item JSON, then separately fetching the
  PDF via the extension and uploading it through the *existing* `papersApi.upload` — matching
  exactly how `ZoteroBrowserImportModal.tsx:216-219` already does the "resolve attachment, then
  `papersApi.upload`" two-step for the folder-picker path, just swapping the attachment source
  from a `FileSystemDirectoryHandle` to an extension fetch.

Wherever the app currently decides between `zoteroApi` and `zoteroLocalApi` for
`ZoteroBrowserModal`'s `client` prop, add a third branch: if `pingExtension()` resolves true,
prefer `zoteroExtensionApi`. No visible UI changes beyond that selection — same modal, same table,
same collection tree, per the `ZoteroBrowseClient` abstraction already in place. If the ping fails
or times out, fall back to today's behavior unchanged (path 3 or path 1, whatever the app already
chooses).

**Exit criteria:** with the extension installed and Zotero running, opening the existing Zotero
import modal in the app shows live data with no folder picker prompt; with the extension absent,
behavior is pixel-identical to today.

### 5. Backend: thin new endpoint module

`src/athenaeum/api/zotero_connector.py`, mirroring `zotero_local.py`'s shape but with no
filesystem/sqlite dependency at all — it never talks to Zotero itself; it receives
already-fetched item JSON from the browser (via the extension) and does exactly what
`browser-import/parse` does for a whole sqlite, but for the single item(s) the extension already
resolved:

- `POST /zotero-connector/import` — body shaped like `ZoteroImportRequest` plus the raw mapped
  item fields (title/creators/year/venue/doi/item_type), since there's no server-side item lookup
  to do here (unlike `zotero_local.py`'s `/import`, which re-fetches from the mounted sqlite). Runs
  `_require_member`, checks/reuses `_existing_dois`, creates the `Paper` row exactly like the other
  three paths do, returns `PaperOut`. PDF upload stays entirely separate through
  `papersApi.upload`, same as path 3 — this endpoint is metadata-only, matching
  `browser-import/parse`'s documented boundary (`zotero_local.py:290-294`).

No `GET /search|/collections|/items` needed server-side — those are served live by the extension
itself in step 4, unlike paths 1/2 which need the backend to hold credentials or mount the
filesystem. This endpoint exists solely as the metadata write path, which is a smaller surface
than the context doc's "thin new endpoint" framing implied once the actual division of labor is
worked out.

**Exit criteria:** existing backend test patterns for `zotero_local.py`'s import endpoint
(membership check, dedup check, `Paper` row creation) ported to this module; `pytest` green.

### 6. Attachment file fetch, wired end to end

Connects steps 2/4/5: importing an item with a PDF attachment fetches file bytes via
`GET /api/users/0/items/:itemKey/file` in the background worker, hands the `Blob` to the content
script, which calls `papersApi.upload` from the page context (so it carries the user's normal
Athenaeum auth — the extension never touches Athenaeum credentials, per the context doc's
no-new-trust-boundary principle).

**Exit criteria:** import a real Zotero item with a locally-stored (not cloud-synced) PDF through
the new UI path; confirm the `Paper` row and its file both land correctly, and — critically —
confirm this works for a PDF that is *not* synced to Zotero's cloud storage, since that's the
capability path 1 structurally cannot offer and the whole reason this extension is worth building.

### 7. Annotation fetch → highlight import

Wire `GET .../children` (annotation items) into the already-built highlight import flow
(`api/zotero_highlights.py`, `ZoteroHighlightImportModal.tsx`, commit `65ae02d`). Concretely: give
that modal a live data source option the same way step 4 did for the browse modal — check whatever
seam `zoteroHighlights.ts` currently uses to fetch annotations (Web-API-backed today) and add an
extension-backed variant. Read the existing highlight-matching code before touching it; this step
supplies raw `annotationText`/`annotationComment`/`annotationColor` data over the new transport,
it does not change the fuzzy-matching logic itself.

**Exit criteria:** import highlights for a paper via the extension path, confirm they match
sentences the same way the existing Web-API-backed flow does, on a real annotated PDF.

### 8. Package and submit to Chrome Web Store

Separate real time budget from build time (review queue latency is out of engineering's control).
Before submitting: run the security-scoping checklist below as an actual audit, not a rubber stamp
— the store review process will reject or flag over-broad permissions, so getting this right
first avoids a slow review-reject-fix-resubmit loop.

## Security scoping checklist (audit before step 8, verify continuously)

Directly from the context doc's security section — restated here as a literal checklist to run
against the actual built manifest, not just intent:

- [ ] `host_permissions` in `manifest.json` is exactly `["http://127.0.0.1:23119/*"]` — not the
      broader `127.0.0.1/*` or `*://*/*` the real Connector uses.
- [ ] Content script `matches` is exactly Athenaeum's real origin(s) + local dev — never
      `<all_urls>`.
- [ ] `permissions` array contains nothing beyond what v1 actually calls — audit against
      Connector's full list (`tabs`, `contextMenus`, `cookies`, `scripting`, `offscreen`,
      `webRequest`, `declarativeNetRequest`, `webNavigation`, `storage`) and justify or cut each
      one; a read-only metadata/file/annotation bridge should need close to none of these.
- [ ] No code path lets the content script call `zoteroLocalClient.ts` directly — only the
      background worker holds the host permission and does the fetching.
- [ ] No new unauthenticated backend surface: `zotero_connector.py`'s endpoint(s) require the same
      session auth + `_require_member` check as every other project-scoped route.
- [ ] Extension never receives or stores Athenaeum credentials — `papersApi.upload` calls happen
      from the content-script-relayed page context, using the page's own session, not the
      extension's.

## Non-goals (unchanged from context doc)

Firefox/Safari support, document-integration (citation insertion), auto-sync/background polling,
and any write access to the user's Zotero library are all out of scope for this plan — see the
context doc for the reasoning on each.

## Open items still worth deciding before/during step 1

- Confirm the local API's `userID` placeholder semantics (`/api/users/0/...` vs. needing the
  actual numeric local userID) against current `zotero-connectors` source — the context doc's
  citations are from `main` branch, checked 2026-08; re-verify at implementation time in case
  upstream has moved.
- Whether Chrome Web Store review flags `host_permissions` for a `127.0.0.1` origin at all (some
  store policies scrutinize localhost access) — worth a quick policy check before step 8, not
  after a build is ready to submit.
