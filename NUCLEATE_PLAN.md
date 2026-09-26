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

---

## Part 1 — Tab structure after the reorganization

Today: `Workspace` / `Compose` / `Export`, where Workspace is
`PapersSidebar │ reader (Read/Find/Backtrace sub-modes) │ statement graph`.

After:

| Tab | Content | Status |
|---|---|---|
| **Read** | `PapersSidebar │ ReaderPane`, full screen, resizable | Reshaped from Workspace: graph and Find removed, Backtrace promoted out |
| **Nucleate** | Left panel (nucleus switcher, stages, chat, needs-full-text queue, prompt) │ canvas │ node drawer | New |
| **Backtrace** | `BacktracePanel` / `BacktraceTree`, unchanged | Promoted from a reader sub-mode to a tab |
| **Audit** | Pasted or uploaded draft → audit run | New backend path; the existing panel is nested under the compose document |
| **Export** | `.ath` v2 corpus export | Reshaped per D-N3 |
| **Compose** | Unchanged | Kept per D-N1, frozen per D-N5 |

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

Prerequisites 3.1 (Zotero position capture) and 3.2 (TextQuoteSelector) are **not started** — no
`selector` or position-capture code exists anywhere in `src/`. 3.1 is small, independent, and on an
expiring window.

---

## Part 3 — Build order

No phase is now blocked on a decision.

1. **Land the uncommitted audit-evaluation work.** Done 2026-09-26. Note it does not include the
   production set-level verdict; see "Also noted" above.
2. **Phase 3.1 — Zotero position capture.** Independent, small, expiring window.
3. **Tab reorganization.** Retire the canvas and Find (D-N4), reshape Read to
   `papers │ reader`, promote Backtrace to a tab, add the Audit tab with its pasted-draft backend
   path, keep Compose as-is.
4. **Phase 3.2 — TextQuoteSelector** on passages, before the archivist starts generating quotes at
   scale.
5. **Node schema** — nuclei, corpus nodes keyed by DOI, lineage edges, evidence, relevance,
   selection reasoning, run records with per-stage cost. Unblocked by D-N6.
6. **Retrieval pipeline and stage event stream.**
7. **Nucleate UI** — left panel, canvas, node drawer, progressive rendering.
8. **Semantic edges** on the D-N7 vocabulary — `domain/relations.py` is written; what remains is
   the edge table, the CHECK constraint, the AI proposal path with one-click confirm, and the
   `KnowledgeLink` migration.
9. **`.ath` v2 (D-N3)** and its published JSON Schema.
