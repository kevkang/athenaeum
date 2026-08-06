# Athenaeum

[![CI](https://github.com/kevkang/athenaeum/actions/workflows/ci.yml/badge.svg)](https://github.com/kevkang/athenaeum/actions/workflows/ci.yml)
[![Deploy](https://github.com/kevkang/athenaeum/actions/workflows/deploy.yml/badge.svg)](https://github.com/kevkang/athenaeum/actions/workflows/deploy.yml)

A research traceability tool 

by Tris Caro, Kevin Kang, Liz Wu

Tris is a Stanback Postdoctoral Fellow at Caltech
Liz is an MDE at Harvard
Kevin is a software engineer


## An opinionated framework

This section of the `README` was written by TC and not reviewed by an LLM.

Our goal is to create a research traceability tool for working scientists: every claim in a project traces back to a verbatim passage in a source paper, every citation can be chased backward through the literature, and every cited piece of evidence can be independently checked against the statement it supports.

Problems of the AI era:
1. The speed of research is increasing, but our capacity to parse it is not.
2. When the volume of research increases, risks to traceability increase exponentially. Even basic cross-references are polluted with faulty or hallucinated citations.

Boons of the AI era:
1. Parsing text (intelligently) is now a rapid computational task.

**These are our opinions (hence an opinionated framework):**

1. Humans are ultimately responsible for scientific output because it is humans, not computers, that ultimately live in the world. Only humans can take responsibility for and ownership of scientific output. 

2. Every citation should include the *verbatim* text that supports the relevant claim/statement.

3. Traceability should be an easily human-and-computer auditable process.

---
The following sections of the `README` were written with and reviewed by Claude Sonnet 4.6.

## Stack

- **Backend**: FastAPI (Python 3.12), async SQLAlchemy 2.0 / asyncpg, Postgres with `pgvector`
  (sentence embeddings), Alembic migrations.
- **Background jobs**: `arq` over Redis for ingestion, synthesis, audit, and graph-sync work; live
  progress streamed to the browser over WebSocket (Redis pub/sub relayed per-connection), auth'd
  via a query-param JWT since browsers can't set custom headers on a WS handshake.
- **Graph**: a Neo4j mirror of the Statement/Evidence/Citation graph, kept in sync from Postgres via
  idempotent `MERGE`-based upserts (`graph/sync.py`) — queried for graph-shaped views (citation
  chains, co-citation) the relational schema doesn't answer well.
- **Retrieval**: hybrid search — pgvector cosine similarity + Postgres full-text search, fused with
  reciprocal rank fusion (`retrieve/hybrid.py`).
- **LLM**: Anthropic Claude via a small provider abstraction (`provider/`); a per-user API key
  (`LLMCredentials`) always takes precedence over the server's own default key.
- **Object storage**: S3 (MinIO locally) for uploaded PDFs and figures, read back via presigned
  URLs.
- **Frontend**: React 19 + TypeScript + Vite; TanStack Router + Query; Zustand for local UI state;
  Cytoscape (+ `cytoscape-dagre`) for the knowledge graph and Backtrace tree views; TipTap v3 for
  the Compose editor.
- **Deployment**: Docker Compose (`nginx`, `api`, `worker`, `postgres`, `redis`, `neo4j`, `grobid`,
  `minio`); GitHub Actions for CI (`.github/workflows/ci.yml`) and deploy
  (`deploy.yml` → SSH to an EC2 host → `git pull && bash deploy.sh`).

## Features

- **Workspace** — ingest papers (PDF via GROBID, Zotero sync/local, or OpenAlex/web search via the
  "Find" pipelines), read them with highlighting, and build a project-scoped knowledge graph of
  `Statement`s linked to their `StatementEvidence`.
- **Synthesis** — a five-step LLM pipeline (`synthesize/orchestrator.py`) that answers a research
  question by retrieving candidate passages, extracting quotes, and independently re-verifying
  every quote (`verifier.py`) before it's trusted, producing a cited, evidence-backed answer.
- **Backtrace** — LLM-assisted citation chasing: given a claim and the paper that states it,
  identify which of that paper's own OpenAlex references most plausibly supports it, and walk
  backward hop by hop. A sentence citing several works branches into several parallel hops rather
  than a single chain.
- **Compose** — a TipTap-based manuscript editor where prose spans bind directly to real
  `Statement`/`StatementEvidence` graph rows via an inline mark, with a floating table of contents,
  LaTeX, figures, and tables. Exports to/from `.athmd`, a git-diffable markdown dialect with nested,
  human-readable citation numbering (`archive/athmd.py`).
- **Audit** — an independent LLM pass over a Compose document that checks whether each cited piece
  of evidence actually supports the statement it's attached to, with no visibility into how that
  evidence was originally selected (same "independent checker" pattern as Synthesis's verifier).
- **Archive** — whole-project export/import as `.ath` (YAML, `archive/export.py`), and a single
  citation chain as `.athtrace` (`archive/athtrace.py`).

## Domain model

```
Project
 └─ Paper (ingested; split into PaperSection → PaperSentence)
     └─ Passage (a specific extracted span, used as evidence)

Statement (a claim, author-drafted or synthesis-produced)
 └─ StatementEvidence (link to a Passage / Paper / Dataset / another Statement)

Synthesis → SynthesisStep → SynthesisEvidence   (one research-question run through the 5-step pipeline)
BacktraceChain → BacktraceHop (parent_hop_id)    (a tree of citation hops traced backward from a claim)
ComposeDocument                                  (one TipTap document per project; round-trips to .athmd)
AuditRun → AuditFinding                          (one independent evidence-support check per Compose doc)
Event                                            (append-only, project-scoped log everything above funnels through)
```

All mutations are meant to flow through `events/applier.py::apply_event()` in the same transaction
as the row change — that discipline is what keeps the per-project history (`api/history.py`)
faithful to what actually happened.

## Repo layout

```
src/athenaeum/
  api/          FastAPI routers — one module per resource (papers, statements, compose, backtrace, audit, …)
  domain/       Pydantic request/response models + shared pure logic (e.g. citation numbering)
  storage/      SQLAlchemy ORM models, DB session/engine, S3 helpers
  ingest/       PDF (GROBID) / Zotero / web extraction, embeddings, the full ingestion pipeline
  synthesize/   LLM pipelines: orchestrator, verifier, backtrace, audit, find-ranking, prompts
  retrieve/     Hybrid (vector + keyword) search
  graph/        Neo4j driver, Cypher queries, Postgres → Neo4j sync
  archive/      .ath / .athtrace / .athmd export-import
  provider/     LLM provider abstraction + per-user credential resolution
  events/       Append-only project event log
  worker/       arq job definitions + settings
migrations/     Alembic migrations
tests/          pytest suite (backend)
web/src/
  pages/        Top-level routed views (Workspace, Compose, Export, …)
  components/   Feature panels (SourcesPanel, AuditPanel, BacktraceTree, …)
  extensions/   Custom TipTap extensions (StatementLink, Figure)
  api/, store/  Typed API clients, Zustand stores
nginx/          Production reverse proxy + Dockerfile that builds the frontend
```

## Local development

See [`testing_readme.md`](testing_readme.md) for the full guide (which Docker services you need for
what, running the automated suite vs. testing in a browser, minting a dev session without ORCID
OAuth, and shutting everything down cleanly). Short version:

```bash
docker compose up -d postgres redis neo4j grobid minio minio-init
.venv/bin/python -m alembic upgrade head
uvicorn athenaeum.main:app --reload &
arq athenaeum.worker.settings.WorkerSettings &
cd web && npm run dev
```

Backend checks: `ruff check .`, `mypy src`, `pytest`. Frontend checks: `tsc -b`, `eslint .`,
`vite build`.
