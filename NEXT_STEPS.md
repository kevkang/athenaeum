# Next session — what's left

## Update 2026-07-24 — Compose Pane (new feature, on `compose` branch, not merged)

**Built and live-browser-verified end to end**, all 7 phases: a new "Compose" nav tab (between
Workspace and Export) with a TipTap/ProseMirror markdown editor (H1–H4, bold, italic). Selecting
prose the user wrote opens an evidence-linking popover: create a new `Statement` from that text or
link an existing one, then attach evidence (paper search, a new "Highlights" bookmark system,
whole papers, or another statement) — all via the *existing* `POST /statements` /
`POST /statements/{id}/evidence` endpoints, unmodified. Because the Workspace graph reads
`Statement`/`StatementEvidence` straight from Postgres (`api/graph.py`'s own comment: "served from
Postgres for immediate consistency"), statements authored in Compose appear on the Workspace graph
immediately — verified via screenshot, no new sync code needed.

Also new: a persisted **yellow highlight** system in the reader pane (`★ Highlight` button next to
the existing "Drag to workspace"/"Backtrace" tooltip) — a personal "maybe useful" bookmark,
independent of formal evidence-linking, backed by a new `highlights` table. Compose's Sources panel
(right-hand, toggleable) surfaces these project-wide, draggable straight into the document — dropping
one inserts its text and opens the evidence popover pre-seeded, so creating the statement is the only
manual step before the citation auto-attaches.

**New backend**: migration `0020_compose.py` (`compose_documents` — one JSONB doc per project,
`version`-guarded optimistic-concurrency autosave; `highlights` — mirrors `Passage`'s shape minus
verification fields), `ComposeDocument`/`Highlight` models, `api/compose.py`, `api/highlights.py`.
Neither new table is event-logged or triggers graph-sync, matching the precedent set by `Annotation`
(personal/free-text content sits outside the event system by design).

**New frontend**: `ReaderPane` was extracted out of `WorkspaceView.tsx` into its own
`components/ReaderPane.tsx` (Phase 0, no behavior change — needed since Compose mounts its own
reader instance in the Sources panel) — `WorkspaceView.tsx` now imports it. New files:
`pages/ComposeView.tsx` (+ `.module.css`), `extensions/StatementLink.ts` (custom TipTap mark binding
prose spans to real statement IDs — lives in the ProseMirror doc's own JSON tree, so it can't drift
as the document is edited), `components/EvidenceLinkPopover.tsx`, `components/StatementPicker.tsx`,
`components/SourcesPanel.tsx`, `components/ComposeEditor.css` (plain, non-module — TipTap renders
into DOM outside CSS Modules' reach), `api/compose.ts`, `api/highlights.ts`. New dependency:
`@tiptap/{core,pm,react,starter-kit}` v3.

**Verification performed**: full backend suite (115 tests, ruff, mypy) and frontend (`tsc`, `eslint`,
`vite build`) all clean — confirmed no new lint/type errors beyond what already existed on `main`
before this branch. Live-browser-tested via a Playwright driver script (`chromium-cli` wasn't
available in this environment) against a locally-seeded test user/project/paper (bypassing ORCID
OAuth and GROBID ingestion, neither needed for UI verification) — every phase covered: highlight
create/persist/delete, editor formatting/autosave/reload, statement creation + evidence attach +
appearance on the Workspace graph, drag-from-Sources-panel auto-attach, and statement-as-evidence
linking. No project skill existed yet for driving this app headlessly — worth generating one via
`/run-skill-generator` if this becomes a recurring need.

**Not done / explicitly deferred**: Export-pane integration (turning the compose document + its
locked statements into a manuscript export) was scoped out of this pass per the original request;
the data model was kept compatible (`content_json` is portable JSON, every statement it creates is
an ordinary `Statement` row) but no exporter was built. **Not yet committed** — all changes are
sitting in the working tree on the `compose` branch, unstaged.

## Update 2026-07-21 — deploy fixes + browser-based Zotero import

**Deploy is healthy again.** The 0011 migration constraint-ordering bug (UPDATE running before the
old CHECK constraint was dropped, so `brick_rests_on_target_kind_chk` rejected the new
`target_kind` values mid-migration) was root-caused and fixed on `main` via PR #5
(`674d167`) — both `upgrade()` and `downgrade()`. Confirmed via `gh run list`: the Deploy workflow
on `main` succeeded at 2026-07-21T03:23:31Z.

**Found and fixed, but NOT YET committed/pushed:** production PDF uploads were broken because
`docker-compose.prod.yml` never overrode the dev-only `S3_ENDPOINT_URL` / `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` that `docker-compose.yml` hardcodes for local MinIO — so prod containers
were pointed at a local `minio` service with fake credentials instead of real AWS S3 via the EC2
instance's IAM role. Fixed locally by unsetting those three vars (Compose's `KEY:` null syntax,
verified empirically to fully unset rather than pass `""`) for `api`/`worker` in
`docker-compose.prod.yml`. **Needs a commit + push to `main` + redeploy to actually take effect.**

**New feature, built but not yet live-tested in a browser:** "Zotero (Local)" previously bind-mounted
the *server's* home directory (`ZOTERO_LOCAL_HOST_MOUNT` → `${HOME}` on whatever machine runs
`docker compose`) — correct for a single-operator local install, meaningless on a hosted multi-user
deploy (EC2's home directory has no Zotero data in it, hence the reported "only shows `~`"). Added a
second, client-side import path that works on the hosted deployment:

- `src/athenaeum/ingest/zotero_local.py`: `parse_full_library()` — bulk-parses every item in a
  `zotero.sqlite` (collections + primary attachment path per item) in one pass, reusing the
  existing tested EAV-schema queries. Unit-tested against a synthetic sqlite fixture (not just
  typechecked) — see conversation history for the test schema if it needs re-running.
- `src/athenaeum/api/zotero_local.py`: `POST /zotero-local/browser-import/parse` — accepts an
  uploaded `zotero.sqlite` (multipart, no other files), parses it in a scratch temp dir, returns
  items + collections. Persists nothing; the actual PDFs never touch the server at this step.
- `web/src/lib/zoteroFolderAccess.ts`: picker abstraction. `showDirectoryPicker()` on Chrome/Edge
  (persisted handle, lazy attachment resolution); `<input webkitdirectory>` fallback everywhere
  else (flat FileList, `webkitRelativePath`-keyed lookup).
- `web/src/components/ZoteroBrowserImportModal.tsx` (+ `.module.css`): picker → parsing → review
  table (collection filter, search, per-row PDF-found check, dedupe-by-DOI) → import (reuses the
  existing `papersApi.upload`/`papersApi.ingest`, no new upload endpoint needed) → done/retry-failed.
- Wired into `WorkspaceView.tsx`'s existing "Zotero (Local)" tab as an "Import from Zotero folder…"
  button, additive alongside the old host-mount flow (not removed — still useful for actual
  single-operator local installs).

**Known, permanent platform limitation (not a bug to fix):** Firefox and Safari don't implement
`showDirectoryPicker()` — Mozilla has explicitly declined on privacy/security grounds, not a
"coming later" gap. Those browsers get the `webkitdirectory` fallback, which works fine for a
one-shot import but can't silently re-scan for new PDFs later; the user has to re-open the picker
each time. Fine for the "import" use case as built; would matter if a future "auto-detect new
Zotero items" feature gets requested.

**Not done yet — first thing to do on resume:** this was verified via `tsc --noEmit`, `eslint`,
`vite build`, and a standalone unit test of `parse_full_library()` against a hand-built sqlite
fixture — but never exercised in an actual running browser against a real Zotero library. Run the
app, pick a real Zotero data directory, and click through the full picker → review → import flow
before considering this done, especially: large-library performance (the PDF-existence check walks
the directory tree per attachment — untested at real-world library sizes), and the
`already_in_project` dedupe against a project that actually has papers in it.

---

Originally written 2026-07-10 as a pure planning handoff. Updated 2026-07-12 (twice): first after
building S3/MinIO + the Workspace split-view merge, then again after building everything in the
10-item follow-up batch below (local Zotero, cement→lock rename, paper/statement delete, full
text view, drag-to-workspace, evidence badges/popover, and all the graph canvas interactions in
the old §1). **As of this update, everything requested is implemented.** What's actually left is
finishing the verification pass (typecheck/lint/tests/live browser check) that was interrupted by
a transient tool-availability outage partway through, and then committing.

---

## Status: implementation complete, verification in progress

Backend: full test suite (57 tests) + ruff + mypy were all green as of the last successful run
mid-session, covering everything through the local-Zotero and passages endpoints. Frontend: a
full `tsc --noEmit` pass and an ESLint pass on `WorkspaceView.tsx` were both clean as of the last
successful run, which was *before* the final two additions (the Zotero-local sidebar tab and the
paper-delete button). Those two were reviewed by hand (types line up, JSX tags balance) but not
yet re-run through the compiler — that's the first thing to do on resume:

```
cd web && ./node_modules/.bin/tsc --noEmit -p .   # avoid `npx tsc` — it re-triggers a package
                                                    # resolution step; the local binary doesn't
npx eslint src/pages/WorkspaceView.tsx
cd .. && .venv/bin/python -m pytest
```

Then a live Playwright pass against the running docker-compose stack (per this session's
established verification pattern) before calling the branch done, especially for: the local
Zotero tab (needs `ZOTERO_LOCAL_DATA_DIR` pointed at a real Zotero data dir to test past the
"not configured" message), the W/E/R hold-and-drag edge creation, and the drag-to-workspace →
nest-into-existing-node flow.

## What was built (all in this session, on the `zotero` branch)

1. **Local Zotero library** — `ingest/zotero_local.py` reads a bind-mounted `zotero.sqlite`
   read-only (no Web API, no credentials); `api/zotero_local.py` mirrors the sync router's
   shape; a `zotero_source` column on `Paper` (`'sync'`/`'local'`) lets `/papers/{id}/retry`
   pick the right re-ingestion path. Frontend: a second "Zotero (Local)" tab next to
   "Zotero (Sync)" in the papers sidebar, and `ZoteroBrowserModal` now takes a `client` prop so
   the same browse UI works against either source.
2. **cement → lock rename**, end to end (migration `0012_lock_terminology.py`, `Statement.status`
   CHECK, API endpoints `/lock`+`/unlock`, `.ath` export/import incl. legacy-archive fallback).
   Locking is explicitly *not* immutable — editing a locked statement auto-unlocks it, and there's
   a standalone unlock button. A new `POST /statements/by-project/{id}/lock-all` backs a
   "lock all & export" prompt in `ExportView.tsx` that fires when unlocked statements exist.
3. **Delete**: `DELETE /papers/{id}` unlinks from the current project (only fully deletes the
   `Paper` row, cascading sections/sentences/passages, when no other project references it
   anymore); a hover "×" in the papers sidebar calls it. Statement delete already existed;
   now also reachable via the graph canvas (Delete/Backspace key, right-click context menu).
4. **Reader pane**: a full-text/section-view toggle (`fullTextView` state); the old per-sentence
   draggable-div + click-to-create button are gone, replaced by a text-selection-driven "Drag to
   workspace" button (drag, not click) that creates a new statement when dropped on empty canvas,
   or nests into an existing statement as evidence when dropped directly on its node
   (`findStatementNodeAtPoint` hit-tests cytoscape's rendered node boxes against the drop point).
5. **Evidence-count badges**: `evidence_count` added to `StatementNodeOut`/the graph query; an
   HTML overlay layer (not a compound node) repositioned via a `cy.on('render', ...)` tick,
   computed in a `useEffect` (not read from the ref during render — hit that same
   `react-hooks/refs` pitfall as earlier in this session and fixed it the same way).
6. **Evidence-preview popover**: a new small popover (not the existing detail panel, per explicit
   product decision) opens near a clicked node, listing each evidence item's actual quote +
   citation. Needed a new `GET /passages/{id}` endpoint — and a fix once built: the reader pane's
   drag-to-workspace flow links evidence to a raw `PaperSentence` id under `target_kind='passage'`
   (no real `Passage` row involved), so the endpoint resolves either kind, falling back from
   `Passage` to `PaperSentence` before giving up with a 404.
7. **Graph canvas interactions** (the old NEXT_STEPS §1, now all done): double-click empty canvas
   → inline quick-create popup; right-click → context menu (create/delete depending on what's
   under the cursor); hold W/E/R + drag between two statement nodes → typed edge
   (`cy.autoungrabify` toggled on keydown/keyup so the drag doesn't just move the source node;
   a small on-screen legend shows the mapping and highlights the currently-held key). Went with
   the hand-rolled approach over `cytoscape-edgehandles`/`cytoscape-context-menus` to avoid new
   dependencies mid-session — worth revisiting if the hand-rolled version feels rough in practice.
