# Nucleate — decisions and build order

Companion to the Nucleate handoff (AI as Archivist). The handoff specifies the feature; this file
records the decisions the handoff asked to be raised before starting, what those decisions retire,
and what is still open. Written 2026-09-26.

---

## Part 0 — Decisions taken 2026-09-26

Five decisions, in the order they were asked. Three came from the handoff's own "ask before
choosing" list; two came from gaps found while surveying the current build.

### D-N1 — The markdown editor stays a visible tab

**Decided:** Compose remains a top-level tab, unchanged. The tab bar is therefore **six** tabs, not
five: Read / Nucleate / Backtrace / Audit / Export / Compose.

**Why it was asked:** the handoff specified five tabs with no slot for Compose, and warned that it
is a live feature that must not be orphaned.

**Consequence:** nothing about Compose changes in this pass. See D-N5 for what keeps it working.

### D-N2 — `.athmd` stays, no converter

**Decided:** `.athmd` is the writing format; `.ath.json` is the network format. They describe
different things, so no conversion exists or is needed.

**Why:** a `.athmd` file's content is prose plus citations. A corpus file's content is nodes, edges
and evidence. Converting one to the other would yield a corpus with no nuclei, no relevance
statements and no selection reasoning — a lossy graph with no use. Nothing in a `.athmd` file is
corpus data that the new format would otherwise lose.

**Consequence:** `archive/athmd.py`, `archive/athmd_render.py`, the `/compose/export` and
`/compose/import` endpoints, and the `PRINCIPLES.athmd` home-page render path are all untouched.

### D-N3 — The corpus format is `.ath` v2 (JSON-LD), not a new `.ath.json`

**Decided:** evolve the existing `.ath` archive to format version 2: JSON instead of YAML, with a
single `@context`, plus the new fields the handoff specifies (nuclei, prompts, selectors, relevance,
provenance, rejection reasons). YAML v1 stays readable as legacy input.

**Why the handoff's framing was adjusted:** the handoff says `.ath.json` "replaces `.athmd`", but the
format it specifies overlaps the existing `.ath` archive instead. `archive/export.py` already
serializes the whole project graph — papers, passages with `verbatim_text`, statements, edges,
evidence, syntheses, annotations, highlights, events — already carries no paper full text, and
`archive/import_.py` already resolves papers by DOI. The handoff's skeleton is close to a subset of
it. Shipping a second corpus serializer would let the two diverge exactly as the two relation
vocabularies did, which is the failure the handoff's own edge section warns against.

**Consequence:** Part VI of the handoff is implemented as a version bump of an existing format.
The published JSON Schema covers the whole corpus, not half of it.

### D-N4 — The statement graph is retired

**Decided:** the cytoscape statement-graph canvas is removed. Read becomes full-screen: papers in a
left panel, the reader in a right panel. The Find panel is retired with it — Nucleate subsumes it.

**Why it was asked:** the handoff's five-tab list had no slot for either, the same orphaning risk it
flagged for the editor.

**What this removes:**

| Removed | Where |
|---|---|
| Cytoscape canvas, layout, node styling, evidence-count badge overlay | `web/src/pages/WorkspaceView.tsx` |
| W/E/R hold-and-drag typed edge creation, its key legend | same |
| Drag-passage-to-canvas statement creation, nest-into-node hit testing | same, `components/ReaderPane.tsx` |
| Double-click quick-create, right-click context menu | same |
| `GET /projects/{project_id}/graph` — its only consumer was the canvas | `src/athenaeum/api/graph.py` |
| Find panel and its result cards | `components/workspace/FindPanel.tsx`, `FindResultCard.tsx` |

**A side benefit:** `StatementEdge` (`relates_to`/`supports`/`tension`) existed only to serve that
canvas's edge drawing, and `KnowledgeLink`'s vocabulary
(`supports`/`contradicts`/`extends`/`cites`/`related`) has no UI consumer at all — `web/src/api/links.ts`
is imported nowhere. Retiring the canvas therefore removes the live half of the two diverging
relation vocabularies, which makes the still-open CiTO decision materially easier: no user-facing
edge drawing needs migrating. Existing `StatementEdge` rows stay in the database; nothing new writes
them.

**Also already dead, found while measuring:** `GET /projects/{project_id}/ghost_papers` and
`GET /papers/{paper_id}/co_citation_neighbors` have no frontend consumer either.

### D-N5 — `Statement` is retired as a destination, frozen in place for now

**Decided:** the `Statement` concept is retired. It cannot be dropped yet, so it is frozen:
`Statement` / `StatementEvidence` / `StatementEdge` become legacy-and-Compose-only. No new UI
creates them. When writing returns, Compose is rebuilt on corpus nodes and evidence, and a migration
drops the tables then — with a real replacement to point at.

**Why the phasing, rather than dropping now:** Compose *is* statements.
`web/src/extensions/StatementLink.ts` is a TipTap mark binding prose spans to real `Statement` UUIDs
inside the document's own JSON tree; `domain/citations.py` derives every reference number and
`[^N.k]` evidence marker by walking statements and their evidence, and is shared by the editor's
hover UI, the `.athmd` exporter and Audit; `synthesize/audit.py` audits statement-by-statement.
Dropping `Statement` today would leave Compose a plain markdown editor with no citations, and would
take `.athmd`'s citation grammar with it, because that grammar *is* statement numbering.

**What does not block retirement:** the new Audit tab. It is specced to audit a pasted or uploaded
draft, which has no `Statement` rows by definition — its citations are parsed out of the submitted
text. The Audit tab is statement-free by design, so only Compose holds the tables open.

### D-N8 — Backtrace splits: stepper stays in Read, tree gets the tab

**Decided:** the Backtrace tab is `BacktraceTree` alone. `BacktracePanel`, the single-path stepper,
stays as a mode of Read's reader pane.

**Why the split rather than moving both:** the two are complementary by design, and the code says so.
`BacktracePanel`'s own comment: "This panel deliberately stays a single-path stepper … Exploring
several branches at once (the tree view) lives in BacktraceTree.tsx instead; this is the 'quick trace
while reading' tool." Its whole reason to exist is tracing without leaving the paper being read, which
only works if it lives in the reading tab. The tree needed no chrome to move: it already owns its
chain-history picker, its candidate cards, and its own empty state.

The two chain selections are deliberately independent — starting a trace in Read does not retarget a
tree left open in the Backtrace tab. Read invalidates the shared `backtrace-chains` query so the new
chain appears there, and that is the only coupling.

### D-N9 — The Audit tab takes pasted text and resolves its citations

**Decided:** parse claim-and-citation pairs out of pasted prose, resolve each citation via OpenAlex,
fetch what text is available, then audit with the existing engine.

**Why not the smaller version:** auditing an uploaded `.athmd` would reuse today's engine almost
unchanged, but it only serves people who already export from Athenaeum — which is not the
"zero-commitment entry point" the handoff asks for. It would also mean parsing the `## References`
section, which `archive/athmd.py` explicitly says "is never parsed back … never trusted as source of
truth", and a foreign project's statement ids degrade to plain text anyway.

**What this obliges.** Resolution, fetching and extraction are each new failure modes, and each has to
be reported as plainly as the audit's own response-integrity counts already are. A citation that
could not be resolved is not a citation that failed its audit, and the UI must never let those two
read the same way. Not built yet.

### D-N10 — Audit results are grouped by outcome, not listed in draft order

**Decided:** four sections in severity order — Not supported, Needs your input, Couldn't check,
Supported — each with a count and a one-line explanation.

**Why grouping rather than per-row badges:** the feature's whole obligation is that a citation which
was *never checked* must not read like one that *failed*. A per-row badge leaves that distinction to
whoever is skimming. A section makes it structural, and lets the difference be stated once, in
words, where it cannot be missed: "These citations were never examined — no match was found, or the
matched work had no readable text. That is not the same as a citation that failed."

The cost, accepted: the draft's own reading order is lost. A side-by-side draft-and-findings view
was considered and deferred — it would need the claim located back in the submitted text, which
extraction does not currently record.

### D-N11 — Review state lives per (paper, nucleus), and cannot exist without lineage

**Decided:** status, rejection reason, relevance statement and selection reasoning live in
`node_reviews`, one row per node per nucleus. Nodes themselves (`corpus_nodes`) are shared across
nuclei, one per paper per project.

**Why:** the handoff's export skeleton puts `status` and `relevance` on the node, but every one of
its behaviour rules is per question — down-weight rejected papers *for that nucleus*, never
re-propose a paper rejected *under the same nucleus*, pinned nodes considered *for that nucleus* —
and relevance is always relevance to a question. The approved worked example: Koch et al. 2018 is
accepted under "How quickly do microbes grow in soil?" and rejected under "What controls microbial
carbon use efficiency?". Both hold at once, on one shared node, which a project-wide status could
not represent. `tests/test_nucleate_schema.py` runs that exact example.

**The membership concern, and how it's closed:** per-(node, nucleus) rows sit close to the separate
membership list the handoff forbids. Membership is therefore *defined* as having a lineage edge in
the nucleus, and the two directions of drift are closed separately:

- *A review without lineage* is impossible at the database level. Each review names the lineage
  edge that introduced it through a composite foreign key onto `(id, nucleus_id, to_node_id)`, so
  the edge must exist, be in the same nucleus, and point at the same node. Verified by
  counterfactual: with the constraint the illegal write is refused; with it dropped (in a
  rolled-back transaction) the same write is accepted.
- *Lineage without a review* is prevented in code. `nucleate/corpus.place_node` is the only
  writer of either, and a source-scanning test fails if anything else constructs them.

`place_node` also enforces the handoff's rule that the archivist never re-proposes a paper rejected
under the same nucleus. A *person* re-adding one is allowed, since that's a deliberate choice, and
never overwrites their earlier judgement.

Nodes are keyed on the paper row, not the DOI, because uploaded PDFs and some preprints have no DOI.
The DOI becomes the export identifier where one exists.

### D-N12 — One provenance vocabulary for statements and edges

**Decided:** `ai_proposed` / `ai_proposed_accepted` / `ai_proposed_corrected` / `user_set`, for
relevance statements as well as semantic edges. Only `ai_proposed` renders as AI text (blue
italics). This is the no-third-vocabulary principle the handoff applies to relations, applied here
to provenance.

### D-N13 — The automatic audit is a third AuditRun kind

**Decided:** `AuditRun.kind` gains `nucleate_node`, tied to the `nucleus_expansions` row that
triggered it, and `AuditFinding` gains a third anchor, the node review. One engine, one findings
table, one integrity count, and the trigger stays distinct in the run records, as the handoff
requires.

**Refinement, made in the build:** the option described the review keeping a pointer to its latest
finding. It doesn't; the latest finding is derived by query. A stored pointer would have created a
circular foreign key (review → finding → review) and could drift.

### D-N14 — A cancelled expansion is its own status

**Decided:** `running` / `done` / `failed` / `cancelled`. This is the one value the schema needed
that neither the handoff nor the earlier answers defined. The handoff requires "cancel available
throughout". A cancelled run is neither finished nor broken, and recording it as `failed` would
pollute any failure rate computed from the run records.

### D-N15 — Full text arrives in the background, after a node is placed

**Decided:** nodes appear quickly with abstract-derived evidence, labelled as such. Open-access
PDFs are then fetched and parsed in the background, and a node upgrades to full-text evidence when
its PDF lands. This keeps the promise that you can read the first paper while the rest arrive,
without an expansion taking minutes. The cost: it still downloads papers you may reject, though only
open-access ones. Built as its own slice (7c); the expansion itself only settles what text is
already in hand.

### D-N16 — Web search is a source from the start, matched to papers deterministically

**Decided:** all three sources — library, OpenAlex, web — ship together. Web results are pages, not
papers, so each is matched to a scholarly work before it can become a node, and **only
deterministically**: a DOI in the page's URL or title, or a single exact title match (with the same
short-title and double-match safeguards the Audit tab uses). Pages that match nothing are dropped
and *counted* (`web_unmatched`), never guessed at. "Matched by title" was read strictly because a
looser match could attach a page's claims to a paper that never made them.

### D-N17 — The left panel is a fixed stack, as the handoff draws it

**Decided:** switcher, stage list, chat, needs-full-text queue, prompt, top to bottom. The stage
list shows in full during a run and then collapses to one line ("Awaiting review · 6 proposed"),
so the explicit awaiting-review state stays visible without crowding the chat. The queue appears
only when non-empty. Chosen over stages-inline-in-chat and over tabs, which would hide the stage
list while you read.

### D-N18 — Accepting or pinning a paper puts it in the library

**Decided:** keeping a node adds its paper to the project's library, so it appears in the Read tab
for reading and highlighting. Frontier and rejected papers stay in Nucleate only. Un-accepting
never removes a paper again, since it may already carry highlights. It also makes later retrieval
treat kept papers as library hits, which is true.

**Consequence, found while building:** paper visibility used to mean "linked to your project's
library", so a needs-full-text node's paper — in the corpus but not the library — would have
refused the very PDF the researcher supplies for it. Visibility now also covers corpus
membership, still only for members of that project.

### D-N19 — Semantic relations are proposed on demand, from one paper at a time

**Decided 2026-09-27:** never during a run. The node drawer's "Propose relations" asks the
archivist for relations *from* that paper (A, the citing or using paper) to the other papers in the
nucleus. Evidence comes first, as everywhere else: A's full text is searched deterministically for
sentences naming each other paper's first author and year, and the archivist classifies only those
sentences, quoting one verbatim. The quote is anchored in A's stored sentences like node evidence.
No full text, no mention found, or no relation among the six: no proposal, and the response says
which. Relations *into* a paper come from proposing on the other paper.

**Known limit:** numbered citation styles ("[12]") name no author, so those mentions aren't found.

### D-N20 — Semantic relations belong to the project

**Decided 2026-09-27:** "A uses the method of B" is a fact about two papers, not about a question.
One row per (A, B, relation) across the project, shown in every nucleus containing both papers;
accepting it once accepts it everywhere. Legacy `KnowledgeLink` rows are project-wide too, so the
migration maps them directly.

### D-N21 — Semantic edges render as labelled arrows; review happens in the drawer

**Decided 2026-09-27:** a curved arrow A → B with a short label. Unreviewed (`ai_proposed`): dashed,
AI blue, italic label. Accepted, corrected or user-set: solid, neutral. Lineage edges stay thin,
straight and grey. Clicking an edge opens the drawer: A → relation → B, the verbatim quote, and
Accept / Change relation / Reject. The node drawer lists each paper's relations with the same
actions and "Add relation…" for setting one directly. A rejected relation is hidden but kept, so a
later proposal doesn't offer it again (as a rejected paper steers retrieval).

### D-N22 — No automatic audit; the drawer leads with why the archivist chose a paper

**Decided 2026-09-28, after live use, reversing the automatic-audit half of D-N13:** a "Supported
by its evidence" verdict is too opinionated for the archivist, which exists to find and describe,
not to judge. Nucleate no longer audits relevance statements: the verifying stage is gone from runs
(kept as a retired stage so older runs still read), the full-text upgrade no longer re-audits, and
the canvas no longer flags statements. Past audit rows are kept and still exported. The Audit tab is
untouched.

The drawer's first item is the archivist's selection reasoning, "Why the archivist chose this", in
AI blue italics, so the researcher judges relevance themselves. The relevance statement is no longer
shown or edited in the drawer; it remains on the canvas card.

### D-N23 — From the drawer: open a paper in Read, and supply its full text

**Decided 2026-09-28:** "Open in Read" puts the paper in the library and opens it there
(`/read?paper=`); the review is left alone, since reading is not accepting. A paper without full
text can be supplied from the drawer as the Read tab adds papers: drop or choose a PDF, or take it
from Zotero (Web API or local). A Zotero item supplied this way fills in the existing paper
(`paper_id` on the import) rather than adding a second one. Supplied papers join the library,
from the drawer and from the needs-full-text queue alike.

---

## Part 1 — Tab structure after the reorganization

Today: `Workspace` / `Compose` / `Export`, where Workspace is
`PapersSidebar │ reader (Read/Find/Backtrace sub-modes) │ statement graph`.

After:

| Tab | Content | Status |
|---|---|---|
| **Read** | `PapersSidebar │ ReaderPane`, full screen | **Built** 2026-09-26. `pages/ReadView.tsx`. Reader keeps a Backtrace sub-mode per D-N8; Find and the graph are gone |
| **Nucleate** | Left panel │ canvas │ node drawer | Not built |
| **Backtrace** | `BacktraceTree`, full screen | **Built** 2026-09-26. `pages/BacktraceView.tsx` |
| **Audit** | Pasted draft → resolve citations → audit run | **Built** 2026-09-27. `pages/AuditView.tsx`, results grouped by outcome (D-N10) |
| **Export** | `.ath` v2 corpus export | Built (step 10): JSON-LD, published schema |
| **Compose** | Unchanged | Kept per D-N1, frozen per D-N5 |

The visible tab bar is therefore **Read / Backtrace / Audit / Compose / Export** until Nucleate
exists. A tab that leads nowhere is worse than a tab that is not there yet.

`/projects/$id/workspace` redirects to `/projects/$id/read`, so old links still land somewhere.

**Retired in the same pass:** `pages/WorkspaceView.tsx` (1,656 lines) and
`components/workspace/FindPanel.tsx` are deleted, along with `GET /projects/{id}/graph`, the reader's
"⠿ Drag to workspace" affordance, and the `cytoscape` / `cytoscape-dagre` dependencies.
`components/workspace/FindResultCard.tsx` stays — both Backtrace views render it.

**Browser-verified 2026-09-26** against the running docker-compose stack and a real project
(5 papers, 20 backtrace chains), driven with Playwright:

| Check | Result |
|---|---|
| Nav bar | Read / Backtrace / Compose / Export, correct tab highlighted on each route |
| Read, no selection | Papers sidebar + full-width reader empty state |
| Read, paper open | Metadata, status, Passages, expandable Body section render full width |
| Reader selection tooltip | `★ Highlight` and `🔍 Backtrace` only — "Drag to workspace" gone |
| Backtrace | Tree renders a real chain: 4 nodes, 3 edges, year axis, History picker, coverage panel |
| Compose | Unchanged — toolbar, citation marker, References, Sources panel, Audit button |
| Export | Loads |
| `/workspace` | Redirects to `/read` |
| `GET /projects/{id}/graph` | 404 |
| Console | No errors on any route |

**Known follow-up, not done:** 83 of the 188 class names in `pages/Workspace.module.css` are now
unreferenced (statement panels, canvas chrome, history, synthesis). They were left in place
deliberately: `FindResultCard` and `PapersSidebar` both index that stylesheet dynamically
(`styles[p.status]`, `styles[roleLabel]`), so a bulk prune risks a visual regression that neither
`tsc` nor `eslint` would catch and that was not browser-verifiable in this pass. `lib/format.ts`'s
`typeClass()` is likewise now unused. Also still dead, as noted under D-N4:
`GET /projects/{id}/ghost_papers`, `GET /papers/{id}/co_citation_neighbors`, `web/src/api/links.ts`,
and the `StatementEdge` write endpoints in `api/statements.py`.

---

## Part 2 — Vocabulary decisions

Both are now resolved, as D-N6 and D-N7. Kept here with their reasoning because both were
decisions about vocabulary, and the handoff's "no silent vocabulary invention" rule means the
reasoning has to stay inspectable.

### O-1 — RESOLVED 2026-09-26 as D-N6: derive `fulltext`, put `retrieval_source` on the lineage edge

The handoff specifies `fulltext ∈ {available, abstract_only, user_must_supply}` and
`retrieval_source ∈ {library, openalex, web}`. Both overlapped columns that already exist on
`Paper`. The reconciliation adds **no new enum values**; D-N4's retirements are what make that
possible.

**`fulltext` is derived, not stored.** All three states are a function of two existing columns:

| Handoff state | Derivation |
|---|---|
| `available` | `Paper.status == 'full_text'` |
| `abstract_only` | `status == 'ghost'` and `abstract IS NOT NULL` |
| `user_must_supply` | `status == 'ghost'` with no abstract, or `status == 'failed'` |

A computed field in the API and the export. A duplicate stored column could drift out of step with
ingestion; a derivation cannot.

**`retrieval_source` lives on the lineage edge**, with the handoff's exact three values.
`Paper.found_corpus` looks like the natural home but is the wrong shape twice: it is single-slot
(re-finding overwrites it) and global to the paper, when "how this was found" is a property of one
expansion. The lineage edge already records "found from that node, by that prompt", so the source
belongs beside it. The export still shows `retrieval_source` on the node, computed from that node's
earliest lineage edge — the handoff's output shape over a sounder store.

**`Paper.found_quote` / `found_relevance_note` / `found_corpus` get the `StatementEdge` treatment.**
Their only writer is the Find panel's add-to-project path (`api/papers.py`), and Find is retired
under D-N4, so they become legacy-with-no-writer and drop in the same later migration as the
statement tables.

**Known word collision, deliberately kept:** `Paper.status = 'ghost'` means "metadata only, no PDF
ingested"; the handoff uses "ghost" as the canvas word for `user_must_supply`. The handoff specifies
the canvas term, so both stay, with a comment wherever they meet.

### O-2 — RESOLVED 2026-09-26 as D-N7: a six-term CiTO subset, plus a separate question-side vocabulary

This also closes D3 from the 2026-09 remediation plan. Names, definitions and IRIs live in
`src/athenaeum/domain/relations.py`, mirroring how `domain/audit_verdicts.py` holds the audit
verdicts; `tests/test_relations.py` pins the parts that erode quietly.

**Evidence the decision rests on.** CiTO's namespace is `http://purl.org/spar/cito/` and its current
specification lists 44 sub-properties of `cito:cites`, each with a declared inverse — roughly 88
terms. The spec also states that no domain or range constraint is declared on any property, "so that
this ontology could be easily integrated with other models", which means a CiTO term on a
nucleus→paper edge would have broken no formal constraint.

| Evidence | Scheme size | Finding |
|---|---|---|
| *Evaluating citation functions in CiTO: cognitive issues* (ESWC 2014) | Full CiTO | Annotators' mental models "hardly ever converge to a single shared opinion" |
| *Journal of Cheminformatics* CiTO pilot, 2020–2022 | Full CiTO | 15 articles (~8% of output), ~75% of each bibliography annotated; discontinued over author burden, absent tool support, manual pipeline steps |
| SciCite (`method`/`background`/`result`, 10,969 instances) | 3 classes | Reported SOTA macro-F1 ≈ 89.6% |
| ACL-ARC (background, uses, compareOrContrast, motivation, extends, future work; 1,941 samples) | 6 classes | Reported SOTA macro-F1 ≈ 76.2% |

Two rows measure humans annotating CiTO; two measure models classifying smaller schemes. No
published mapping between the two sets of labels was found, so the mapping below is this project's
own. The ESWC paper's numeric agreement figures were not obtained — its PDF would not extract — so
only its stated qualitative finding is relied on.

Note what killed the Cheminformatics pilot: **author burden**, humans hand-filling a taxonomy. That
is the failure mode the governing principle already rules out, and it argues for the
AI-proposes/user-confirms pattern rather than against CiTO.

**Semantic relations, paper → paper.** Six terms. Every one reads A → B with A as the citing or
using paper, so direction never needs per-relation reasoning.

| Relation | CiTO IRI |
|---|---|
| `uses_method_of` | `cito:usesMethodIn` |
| `uses_data_from` | `cito:usesDataFrom` |
| `extends` | `cito:extends` |
| `supports` | `cito:supports` |
| `disagrees_with` | `cito:disagreesWith` |
| `obtains_background_from` | `cito:obtainsBackgroundFrom` |

Six rather than three because three collapses agreement and conflict into one "result" class, and
that distinction is exactly what the archivist is allowed to observe. `cito:supports` was chosen over
the tighter `cito:confirms` for continuity with `KnowledgeLink`'s existing `supports` value.
**Known wrinkle, documented in the module:** `supports` and `disagrees_with` are then not opposites —
`cito:supports` is A endorsing B, `cito:disagreesWith` is a conflict between their claims. The
archivist's prompt must define each on its own terms, never as the negation of the other.

**No catch-all.** `cito:citesAsRelated` and plain `cito:cites` are excluded. A "related" bucket
absorbs every uncertain call and makes the other six meaningless; when the archivist cannot name one
of the six it proposes no semantic edge, and the lineage edge already records that one paper was
found from another.

**Question relations, nucleus → paper.** All four in Athenaeum's own namespace, not CiTO's:
`ath:addresses`, `ath:measures`, `ath:providesMethodFor`, `ath:providesContextFor`. CiTO does have
`providesMethodFor` and `givesBackgroundTo` as real inverse properties, so two of the four could have
reused it — the separation is so that a CiTO-aware consumer never mistakes an edge leaving a question
for a citation between documents. The handoff's rule against defining a new vocabulary is about not
creating a third *paper-to-paper* vocabulary; it already concedes the question side is different
("'Supports' is meaningless on an edge from a question").

**Legacy.** `KnowledgeLink.rel` maps `supports`→`supports`, `contradicts`→`disagrees_with`,
`extends`→`extends`, and `cites`/`related`→nothing. `StatementEdge.relation` gets no forward mapping:
its edges were user-drawn between statements, not papers, and it retires with the statement graph.
The migration reports affected row counts rather than guessing, per the reversible/dry-run rule.

### JSON null is not SQL NULL — found and fixed 2026-09-27 (migration 0033)

A test written to prove unanchored evidence *can't* be stored showed that it could. SQLAlchemy's
JSON types store Python `None` as the JSON value `null` unless told otherwise, and
`'null'::jsonb IS NULL` is false. Two consequences in already-committed work:

- **Phase 3.1:** a Zotero highlight imported without a position was meant to be found later by a
  backfill using `zotero_position_json IS NULL`. It would have been silently skipped.
- **Phase 3.2:** a passage whose selector `check_passage` cleared was invisible to any "not yet
  anchored" query.

The selector `CHECK`s could also be bypassed: `null ->> 'type'` is `NULL`, and a `CHECK` treats
`NULL` as passing. Fixed by declaring every JSONB column added this session `none_as_null=True`, and
by migration 0033, which converts any stored JSON null and tightens the selector `CHECK`s to require
an object, the right type, and `exact`. The dry run found zero rows to convert: the bug was latent
and caught before any data hit it. `tests/test_jsonb_null.py` guards both halves. Older JSONB
columns (`authors_json`, `query_expansion_json` and so on) were left alone: out of scope, and
changing how they store values could affect existing reads.

### Also noted, not a decision

Uncommitted work sat in the tree and is committed on this branch as of 2026-09-26. Checked rather
than assumed, it is **not** the production half of set-level Audit — there is no `set_verdict`
anywhere in `synthesize/audit.py`, `domain/models.py` or `storage/models.py`. What it actually is:

- the audit verdict taxonomy (`domain/audit_verdicts.py`) and the evaluation harness
  (`evaluation/`: items, runner, metrics, predictors, graded, review, stability, tabulate, cli, sets)
- graded verdicts stored on `audit_findings` (migration 0026), backfilled only where certain
- the corrected source check re-run beside the original verdict (migration 0027)
- **response-integrity counting** (migration 0028, `audit_malformed_threshold` in config, a banner in
  `AuditPanel.tsx`): how many evidence checks came back unusable, because a skipped check records a
  citation as unsupported with nothing having judged it
- running token totals on `AnthropicProvider`, so a batch run can report what it spent
- Set A1 and two paid runs under `eval/`

**Consequence for Nucleate.** The handoff wants each relevance statement's audit verdict stored on
the node. The production engine currently returns per-citation supported/not-supported plus the
integrity counts — not the graded verdicts, which so far exist only in the harness. So a node's audit
field is binary until the production set-level verdict lands ("both": a set-level verdict alongside
the per-citation findings, per the 2026-09-16 decision). That is a real gap, not a blocker: build the
node field against the binary engine and widen it when the graded production path exists.

Prerequisite 3.1 (Zotero position capture) is **done** 2026-09-26: migration 0029 adds
`zotero_annotation_key` / `zotero_position_json` / `zotero_sort_index` / `zotero_page_label` to
`highlights`, and both Zotero readers now parse the `position` JSON string they previously discarded.
Position is taken from a server-side re-read of the annotation at import, never from the request
body, because its purpose is to record where the highlight demonstrably was. A failed or impossible
lookup still imports the highlight and still records the annotation key, which is what leaves the row
backfillable.

Prerequisite 3.2 (TextQuoteSelector) is **done** 2026-09-27. Every stored quote — `Passage` and
`Highlight` — carries a W3C `text_quote_selector` `{type, exact, prefix, suffix}`, built by
`domain/source_check.text_quote_selector` so that "the check found this quote" and "we can anchor it"
can never disagree: one normalization serves both.

- **Built at creation** from this server's sentences, preferring the quote's own sentence ids, so a
  phrase repeated in the paper ("n = 12 cores") is anchored where it was actually taken from.
- **Re-anchored** whenever `check_passage` / `repoint_highlight` run (import, re-ingestion), and
  **cleared** when the words are no longer found — a selector says where a quote *is*, so a stale
  one must not survive.
- **Text form:** `exact`/`prefix`/`suffix` come from the text after NFKC, so `CO2` not `CO₂`.
  Mapping back through NFKC to raw characters is fiddly and buys nothing, since anyone re-finding a
  quote normalizes anyway; the displayed quote stays in `verbatim_text`. 32 characters of context,
  as Hypothesis uses.
- **Backfill:** migration 0031, with frozen logic per the 0025/0027 convention, and a test checking
  the frozen copy still matches the live function. A passage is anchored only if its recorded
  `source_check` is `matches_source`, so the 0027 corrected-verdict question stays undecided.
- **Guard:** `tests/test_every_quote_is_anchored.py` reads the source and fails if any
  `Passage(...)`/`Highlight(...)` construction lacks a selector. The column is nullable on purpose,
  so a forgotten path would otherwise fail silently — and the first sweep for these sites did miss
  three.

Selectors are exported since `.ath` v2 (step 10). Not yet: draft-audit findings, whose quotes come
from OpenAlex abstracts, carry none. `text_quote_selector_in(quote, text)` exists for exactly that
single-text case and for Nucleate's abstract-derived evidence.

---

## Part 3 — Build order

No phase is now blocked on a decision.

1. **Land the uncommitted audit-evaluation work.** Done 2026-09-26. Note it does not include the
   production set-level verdict; see "Also noted" above.
2. **Phase 3.1 — Zotero position capture.** Done 2026-09-26.
3. **Tab reorganization.** Read and Backtrace done 2026-09-26. The Audit tab is split out as its
   own item below, since D-N9 makes it a feature rather than a panel move.
4. **Audit tab** (D-N9). Data model done 2026-09-26: `domain/draft_audit.py`, migration 0030,
   `AuditRun.kind`, `AuditDraftCitation`, findings anchored to a statement *or* a citation.
   Remaining: citation extraction from prose, OpenAlex resolution, evidence selection, the worker
   job, the API, and the tab itself.

   Resolution vocabulary, settled by the D-N9 answers and recorded here because it is new:
   `exact` / `needs_confirmation` / `confirmed` / `not_found`, of which only `exact` and `confirmed`
   proceed to an audit — `exact` is a deterministic string match, never a model's ranking. Evidence
   source is `project_full_text` / `abstract` / `no_text`.
5. **Phase 3.2 — TextQuoteSelector.** Done 2026-09-27; see "Also noted" above.
6. **Node schema.** Done 2026-09-27: migration 0032, `domain/nucleate.py`,
   `nucleate/corpus.py`. See D-N11..D-N14. Building it surfaced a latent bug in earlier committed
   work, fixed in 0033 — see "JSON null" below.
7. **The archivist.** 7a done 2026-09-27: `nucleate/archivist.py` and `nucleate/retrieval.py`
   run one expansion end to end, tested with a scripted model. 7b done 2026-09-27:
   `run_expansion_job`, `api/nucleate.py` (nuclei, canvas detail, prompts, expansions, cancel,
   review actions, and a websocket event stream mirroring the Audit tab's). 7c done 2026-09-27:
   the Unpaywall check (migration 0034, `papers.oa_pdf_url`/`oa_checked_at`), background fetch via
   `ingest_paper`, and `nucleate/fulltext.py`'s post-ingest upgrade.

   **Open for step 8:** should corpus papers appear in the Read tab's paper list? Today they
   don't — full-text evidence for a corpus-only paper is found through a one-paper search scope
   (`hybrid.search(link_to_project=False)`) precisely so that frontier and rejected papers don't
   appear there unasked.
8. **Nucleate UI.** Done 2026-09-27: 8a the tab, nav and left panel; 8b the canvas; 8c the node
   drawer. Browser-verified against seeded nuclei, and the drawer's actions against the real API.

   **Live runs done 2026-09-27**, end to end against the real model, OpenAlex, Unpaywall and
   GROBID. They found and fixed: the opening prompt (the question itself) declined as synthesis;
   web search finding nothing (dynamic-filtering results carry no citations; the archivist now
   reads result blocks with the basic variant); OpenAlex 429s (anonymous search is throttled under
   load; optional `OPENALEX_API_KEY`, two retries, fail fast and count). Local testing: `./test.sh`.

   **Open:** web search's yield after that fix is unmeasured. The next live run's
   `web_search_pages.done` log line and the retrieving stage's counts say what it contributes;
   D-N16 (keep web search) should be revisited against its cost once that's known.

   **Gap worth closing later:** the frontend has no test runner. Canvas layout guarantees were
   verified by running the real `layout.ts` under Node's type-stripping; adopting vitest would let
   them live as tests. Not added unasked — it's a project-level choice.
9. **Semantic edges.** Done 2026-09-27, per D-N19 to D-N21: 9a the schema and the `KnowledgeLink`
   carry-over (migration 0035; `POST /links` now 410), 9b proposing from citing sentences, the
   review rules and the API, 9c the canvas arrows, the relation drawer and the node drawer's
   Relations section. Browser-verified against a seeded nucleus: every provenance renders as
   designed, and accept / add / delete work end to end.

   **Open:** proposing has run only against a scripted model. One real "Propose relations" on a
   full-text paper will show whether the archivist's picks hold up. Relations are not yet audited
   by the checker model the way relevance statements are; nothing was decided about that, so it
   was left out rather than invented. Numbered citation styles are not found (D-N19).
10. **`.ath` v2 (D-N3).** Done 2026-09-27. The exporter writes JSON with an inline `@context`
    (namespaces, relation terms mapped to CiTO, the TextQuoteSelector words), the v1 sections
    unchanged plus selectors and corpus papers, and the Nucleate sections: nuclei with prompts
    and runs, nodes with per-nucleus reviews and evidence, lineage and semantic edges. Import
    validates against `archive/schema/ath-v2.schema.json` before writing anything, replays
    lineage through `place_node`, and still reads v1 YAML. Served at `/formats/ath/2.0/`.
    Verified by round trip in tests and through the real endpoints.

    **Departures from the handoff's skeleton, each for a recorded reason:** review state is a
    per-nucleus `reviews` list on each node (D-N11); the `@context` is inline, since the
    handoff's URL is a placeholder that resolves nowhere; relations are Athenaeum's terms, which
    the context maps to CiTO IRIs.

    **Not carried over on import:** automatic audit verdicts (measured on the exporting server;
    reported as a warning, statements import unaudited). The schema's `$id` and the `ath:`
    namespace are placeholders until a real domain is chosen.
11. **Drawer changes from live use (D-N22, D-N23).** Done 2026-09-28.

    **Assessed, not built: a PDF viewer in Read.** Feasible; the work is storage, not the viewer.
    Every ingestion path currently discards the PDF once its text is extracted (uploads are deleted
    from S3; open-access and Zotero PDFs only touch a temp directory). A viewer needs: keep each PDF
    privately in S3 on every path (the real decision: storage cost, and retained copies of
    paywalled papers, which stay private and are never exported); serve it by presigned URL
    (`storage/s3.py` has `generate_presigned_url`); render it with pdf.js behind a Text / PDF
    toggle. Moderate. Jumping from a passage to its place is moderate (pdf.js text search);
    exact highlight boxes need GROBID coordinates and are larger. Existing papers have no stored
    PDF: open-access ones can be re-fetched, Zotero-local re-read, uploads re-uploaded.
