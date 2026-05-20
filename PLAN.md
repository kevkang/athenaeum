# Athenaeum Backend — Implementation Plan

A research traceability tool for working scientists. This document describes the backend
architecture: a knowledge-graph–oriented system in which every AI-generated claim is
anchored to specific sentences in specific papers, verified by an independent checking pass,
and exported as a portable, version-controllable `.ath` framework file.

**Stack decisions:**
- Language: **Python** (single-language stack)
- Source-of-truth DB: **Postgres 16 + pgvector**
- Knowledge graph: **Neo4j 5** as a derived read model
- Deployment: **self-hostable, multi-user, open source**

---

## 1. Architecture overview

Two long-running Python processes, one queue, two databases:

```
┌─ athenaeum-api (FastAPI + Uvicorn)   ── HTTP + WebSocket, auth, CRUD, orchestration
├─ athenaeum-worker (arq, async)       ── ingestion, embedding, synthesis, verification, projection
├─ Postgres 16 + pgvector              ── source of truth (relational + vectors)
├─ Neo4j 5                             ── derived graph projection (Graph view, ghosts, co-citation)
└─ Redis                               ── arq queue + WebSocket pub/sub
```

ML libraries run in-process where they're used: the worker hosts embedding and extraction
models; the API hosts only cheap retrieval at request time. LLM calls (synthesis,
extraction, verification, signal detection) go out to Anthropic / OpenAI / local Ollama via a
`Provider` protocol — model identity is recorded on every synthesis row for provenance.

GROBID runs as its own Docker container called over HTTP via `grobid-client-python`. It is
infrastructure, not code we maintain.

---

## 2. Data model (Postgres = source of truth)

The schema follows what the mockups already commit to. Nine first-class tables, plus their
join/edge tables.

| Table | Key fields | Notes |
|---|---|---|
| `users` | id, orcid, email, display_name | ORCID is the natural identity |
| `projects` | id, title, description, license, created_at, owner_id | the sustained research unit |
| `project_members` | project_id, user_id, role | multi-user collaboration |
| `papers` | id, doi, openalex_id, title, authors_json, venue, year, abstract, status (`full_text` / `ghost`) | ghosts = cited but no full text |
| `paper_sections` | id, paper_id, label (`§Introduction`), ord, char_start, char_end | flat list, addressable |
| `paper_sentences` | id, paper_id, section_id, ord, text, char_start, char_end, embedding `vector(768)` | atomic addressable unit; pgvector HNSW index |
| `project_papers` | project_id, paper_id, added_at, added_by | what's in scope for this project |
| `passages` | id, paper_id, sentence_ids[], verbatim_text, verified, verified_at, checker_model | a passage = one or more contiguous sentences |
| `annotations` | id, project_id, author_id, target_kind, target_id, body, created_at | polymorphic target |
| `questions` | id, project_id, author_id, body, status, attached_to[] | first-class |
| `syntheses` | id, project_id, claim_text, query_expansion_json, retrieval_corpus, model, checker_model, run_at | one run of the pipeline |
| `synthesis_evidence` | synthesis_id, passage_id, role (`theory`/`opposing`/`framework`/`stakes`/`method`/`data`), ord | extracted passages per run |
| `synthesis_steps` | synthesis_id, ord, kind (`query_interp`/`retrieval`/`extract`/`signal`/`verify`), payload_json | reasoning trace exposed in UI |
| `evaluations` | id, synthesis_id, passage_id, user_id, stance (`agree`/`partial`/`disagree`), body | per-passage user evaluation |
| `bricks` | id, project_id, type (`methods`/`foundation`/`synthesis`/…), status (`draft`/`cemented`), assertion, cemented_at, drafted_at, built_from_synthesis_id | the unit of work |
| `brick_rests_on` | brick_id, target_kind (`paper`/`passage`/`dataset`/`brick`), target_id, role | the supporting chips |
| `brick_builds_on` | brick_id, parent_brick_id, ord | DAG over bricks |
| `datasets` | id, project_id, kind (`figure`/`table`/`synthesis`/`raw`), title, storage_uri | "athenaeum-vault://…" |
| `tension_edges` | id, from_kind/id, to_kind/id, label (`opposing_direction`/`scale_mismatch`/…), evidence_passage_id | first-class so the graph view can render them |
| `citation_edges` | from_paper_id, to_paper_id, source (`openalex`/`extracted`) | drives ghost surfacing & co-citation |
| `provenance_log` | id, project_id, kind, payload_json, created_at | append-only audit trail |

Two schema invariants to enforce up front:

1. **Every quote is sentence-addressable.** `passages.sentence_ids` is the canonical anchor.
   The verifier looks up `paper_sentences.text` and requires `verbatim_text` to be
   reconstructible from concatenated sentences (modulo whitespace). No free-text quotes.
   This is what makes a <2% hallucination rate measurable rather than claimed.
2. **Polymorphic targets via `(target_kind, target_id)`** — annotations, questions, and brick
   edges all use this pattern. Enforce with CHECK constraints, not FKs; integrity is verified
   in application code.

Row-level security keys off `project_id` so multi-tenancy is enforced in the database, not
only at the application layer.

---

## 3. Knowledge graph layer (Postgres → Neo4j projection)

Postgres holds truth. **Neo4j is a derived read model**, rebuilt from events emitted on
Postgres writes. The application never writes directly to Neo4j from request handlers.

**Nodes** mirrored to Neo4j: `Paper`, `Sentence`, `Passage`, `Brick`, `Annotation`,
`Question`, `Dataset`, `Project`.

**Edges** mirrored: `CITES` (paper→paper), `CO_CITED_WITH` (paper↔paper, weighted),
`RESTS_ON` (brick→paper/passage/dataset, with `role` property), `BUILDS_ON` (brick→brick),
`ANNOTATES` (annotation→target), `ASKS_ABOUT` (question→target), `OPPOSES` (tension),
`EXTRACTED_FROM` (passage→sentence).

**Why both:** the bricks list, paper list, synthesis runs, and `.ath` export are relational —
Postgres is the right home. The graph-view canvas, "is there a path from this brick to that
opposing paper through co-citation?", ghost-paper surfacing, and the public community
corpus are graph-shaped — Cypher in Neo4j is dramatically simpler than recursive CTEs.

**Sync mechanism:** start with **Postgres `LISTEN`/`NOTIFY` via asyncpg**. The API fires a
notification on writes; the worker subscribes and upserts into Neo4j. Move to **logical
replication via `wal2json`** when traffic justifies it. Either way, the projection is
**rebuildable from scratch** by replaying Postgres — Neo4j is a cache.

Three Cypher queries to write early as integration checkpoints:

- `brick_subgraph(brick_id, depth=2)` — drives the graph view
- `ghost_papers(project_id)` — papers cited by in-project papers but not in `project_papers`
- `co_citation_neighbors(paper_id, k=20)` — for "papers you may want to add"

---

## 4. Core subsystems

### 4a. Ingestion

- `POST /papers` accepts a DOI or OpenAlex ID.
- Worker pulls metadata from OpenAlex via `pyalex`; tries Unpaywall → arxiv → biorxiv → S2
  for the PDF.
- PDF → GROBID (via `grobid-client-python`) → TEI XML → sectioned plain text → sentences
  via `pysbd` → rows in `paper_sections` and `paper_sentences` with char offsets.
- Sentence embeddings via `sentence-transformers` (`bge-small-en-v1.5` or similar, 768-d) →
  `paper_sentences.embedding`. ONNX Runtime is an opt-in fast path.
- Failure modes (no OA PDF, GROBID parse failure) leave `papers.status = 'ghost'` with
  metadata only.

### 4b. Retrieval

Hybrid, in-Postgres:

- BM25 via the `pg_search` extension on `paper_sentences.text`
- Cosine similarity via pgvector HNSW on `paper_sentences.embedding`
- Reciprocal rank fusion (cheap in SQL)
- Restrict to `project_papers` by default; expand to OpenAlex adjacency on demand and
  surface those as "candidates to add"

### 4c. Synthesis pipeline (the heart of the system)

An arq job. Surfaces incremental steps to the API over WebSocket (Redis pub/sub) so the
reasoning trace renders live:

1. **Query interpretation** — LLM parses the claim into entities + relation; emits expanded
   terms. Persisted to `syntheses.query_expansion_json`. User can edit and rerun.
2. **Retrieval** — hybrid search, top-K.
3. **Sentence extraction** — LLM picks supporting sentences from each retrieved paper;
   **must return `sentence_ids`**, not free text. The orchestrator looks up the actual
   sentence text from Postgres before showing it.
4. **Signal detection** — LLM compares directions across extracted passages → labels
   `theory` / `opposing` / `framework` / `stakes` / `method`.
5. **Citation verification (independent pass)** — *different model context*, given only
   `(claim, extracted_passages_with_paper_ids)`. For each passage, fetches the sentence
   text from Postgres again and checks verbatim match. Result is the
   `citation_verification_rate` logged on the synthesis and aggregated into the public
   monthly figure.

Each step writes to `synthesis_steps` so the trace panel is just
`GET /syntheses/:id/steps`.

### 4d. Bricks

- `POST /bricks` from a synthesis (status `draft`) or by hand.
- Draft → cement is a state transition that snapshots the supporting passages (records
  `passages.verified_at`, freezes `brick_rests_on`).
- `builds_on` is a DAG; reject cycles in the API layer.
- Editing a cemented brick auto-bumps status back to `draft` and logs the diff to
  `provenance_log`.

### 4e. `.ath` export / import

- Export: a single assembler in Python → `ruamel.yaml` (preserves anchor/alias style so
  `&malik2016` / `*malik2016` round-trip cleanly).
- Import: parse YAML → resolve DOIs against OpenAlex → create stubs for papers not yet
  ingested → create bricks with `status = imported`. User can then ingest full text per
  paper.
- Round-trip integrity is part of CI: `assert import(export(project)) == project` on a
  fixture.

---

## 5. API surface (illustrative, REST + WS)

```
POST   /projects
GET    /projects/:id
POST   /projects/:id/papers              (by DOI/OpenAlex)
GET    /projects/:id/papers
GET    /papers/:id                       (metadata)
GET    /papers/:id/sections
GET    /papers/:id/sentences?section=…

POST   /projects/:id/syntheses           → returns synthesis_id; WS for live trace
GET    /syntheses/:id
GET    /syntheses/:id/steps              (reasoning trace)
POST   /syntheses/:id/evaluations        (per-passage stance)

POST   /projects/:id/bricks
POST   /bricks/:id/cement
POST   /bricks/:id/builds_on
GET    /projects/:id/bricks

GET    /projects/:id/graph?brick=:bid    (Neo4j-backed; powers Graph view)
GET    /projects/:id/ghosts              (Neo4j-backed)

POST   /annotations
POST   /questions

GET    /projects/:id/export.ath          (YAML, streamed)
POST   /projects/import                  (multipart YAML upload)

GET    /public/verification-rate         (monthly rolling, published openly)
```

Auth: ORCID OAuth as the primary path; institutional SSO via OIDC. Session tokens are JWT;
rate limiting at the gateway.

---

## 6. Package layout

```
athenaeum/
├─ src/athenaeum/
│  ├─ api/             FastAPI routers, dependencies, auth (ORCID OAuth)
│  ├─ domain/          pydantic models — Brick, Passage, Synthesis, invariants
│  ├─ storage/         SQLAlchemy 2.x async models + repos; pgvector helpers
│  ├─ graph/           neo4j driver client, projection sync, Cypher queries
│  ├─ ingest/          OpenAlex (pyalex), Unpaywall, GROBID client, pysbd pipeline
│  ├─ retrieve/        hybrid BM25 (pg_search) + pgvector fusion
│  ├─ synthesize/      orchestrator, Provider protocol, independent verifier
│  ├─ provider/        anthropic, openai, ollama implementations
│  ├─ export/          .ath YAML codec (ruamel.yaml — round-trip preserving)
│  ├─ worker/          arq job definitions, NOTIFY listener (asyncpg)
│  └─ telemetry/       structlog + OpenTelemetry, verification-rate aggregator
├─ services/
│  ├─ api/             entrypoint: uvicorn athenaeum.api:app
│  └─ worker/          entrypoint: arq athenaeum.worker.WorkerSettings
├─ migrations/         alembic
├─ tests/              pytest + pytest-asyncio + testcontainers
└─ docker/             compose: postgres, neo4j, redis, api, worker, grobid
```

### Library choices

| Subsystem | Library |
|---|---|
| Web framework | **FastAPI** |
| DB driver / ORM | **SQLAlchemy 2.x async + asyncpg** |
| Migrations | **Alembic** |
| DTOs | **Pydantic v2** |
| Neo4j client | **neo4j** (official Python driver) |
| Embeddings | **sentence-transformers** (PyTorch); ONNX Runtime opt-in |
| PDF → TEI | **grobid-client-python** (talks to a GROBID Docker container) |
| Sentence tokenization | **pysbd** |
| YAML | **ruamel.yaml** (preserves anchors/aliases) |
| HTTP client | **httpx** (async) |
| Job queue / worker | **arq** (Redis-backed, async-native) |
| LISTEN/NOTIFY | **asyncpg** (native support) |
| OpenAlex | **pyalex** |
| Tracing | **structlog + opentelemetry-sdk** |
| Tests | **pytest + pytest-asyncio + testcontainers** |

---

## 7. Phased roadmap

Build in vertical slices — each phase ships a usable system.

- **Phase 0 — Foundations (1–2 weeks).** Repo scaffold, Docker compose, Alembic migrations
  for `users` / `projects` / `papers` / `paper_sections` / `paper_sentences`, ORCID auth,
  smoke-test `GET /projects/:id`.
- **Phase 1 — Ingestion (1–2 weeks).** OpenAlex client, PDF acquisition, GROBID, sentence
  extraction with offsets, embedding pipeline. End state: POST a DOI, see sentences with
  offsets in the DB.
- **Phase 2 — Retrieval (1 week).** Hybrid search; `POST /search` returns ranked sentence
  hits scoped to a project.
- **Phase 3 — Synthesis + verification (2–3 weeks).** Full orchestrator with reasoning trace
  persisted, independent checker pass, verification-rate counter. This is where the
  project's core claim (<2% hallucinations, measured) becomes a fact rather than a slogan.
- **Phase 4 — Bricks + annotations (2 weeks).** CRUD, `rests_on`, `builds_on`, draft ↔
  cemented transitions, evaluations, questions. End state: the **Bricks** and **Synthesis**
  mockups are functional.
- **Phase 5 — Neo4j projection + graph view (2 weeks).** Event subscriber, idempotent
  upsert, the three checkpoint Cypher queries. End state: the **Graph** mockup is
  functional, including ghost papers.
- **Phase 6 — `.ath` export / import (1 week).** YAML codec + round-trip CI test. End
  state: the **Export** mockup is functional.
- **Phase 7 — Community layer + public verification rate (ongoing).** Public read-only
  graphs (CC-BY), monthly verification rate published at `/public/verification-rate`,
  federation between Athenaeum instances over `.ath`.

Realistic total to Phase 6: ~10 weeks.

---

## 8. Cross-cutting concerns to get right early

- **Provenance is a first-class output, not a log.** Every synthesis row records model,
  checker model, retrieval corpus, and verification rate. These flow straight into `.ath`
  export. Don't bolt this on later — the project's credibility depends on it.
- **The checker model must run with no access to the synthesis trace.** Pass it only the
  claim, raw extracted text, and paper IDs. Different system prompt, ideally a different
  model. Otherwise it just rubber-stamps.
- **Verbatim is structural, not stylistic.** Schema enforces it: passages reference
  `sentence_ids`; the API never accepts free-text quotes that aren't reconstructible from
  sentences.
- **The graph projection is rebuildable.** Treat Neo4j as a cache; have a
  `rebuild_neo4j_from_postgres` command on day one. Protects against schema drift,
  replication lag, lost events.
- **Multi-tenancy via row-level security** in Postgres (`project_id` is the tenant key),
  not only at the application layer.

---

## 9. Python-specific note

Python's GIL plus heavy ML deps mean the worker should run as **multiple arq workers behind
one Redis** rather than a single threaded worker. Embedding throughput is the likely
bottleneck — keep the option open to pin the embedding model in a single process and call
it via an internal queue topic so the model isn't loaded N times.
