# Next session — what's left

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
