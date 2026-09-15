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
  Its accuracy has not yet been measured — see [What is and isn't measured](#what-is-and-isnt-measured).
- **Archive** — whole-project export/import as `.ath` (YAML, `archive/export.py`), and a single
  citation chain as `.athtrace` (`archive/athtrace.py`).

## What is and isn't measured

Athenaeum runs several automated checks, and they answer different questions. Only some of them
have a number behind them, so they are named separately here and must not be read as one claim.

| Check | Question it answers | Status |
|---|---|---|
| **Verbatim accuracy** (`domain/source_check.py`) | Is this quote word for word what the paper says? | Not a rate; an outcome recorded on each quote. Quotes built in the app are copied from the paper's stored sentences, so they match by construction. Quotes arriving in a `.ath` import are compared with this server's own copy of the paper and recorded as `matches_source`, `differs_from_source`, or `source_unavailable` (the paper isn't on this server; the quote is re-checked if the paper is ingested later). The comparison ignores only spacing, line-break hyphens and Unicode character forms, so a `differs_from_source` result can still come from a harmless PDF-extraction difference: it marks a quote for review, not proof of fabrication. The outcome is currently stored but not shown; it will appear in Audit once Audit reports graded verdicts. |
| **Synthesis relevance** (`synthesize/verifier.py`) | Does each passage Synthesis found actually relate to the claim? | A model's judgment. Published at `GET /public/synthesis-relevance-rate`. |
| **Backtrace resolution** (`domain/backtrace_stats.py`) | Of the citation hops users chased, how many reached a verbatim quote at all? | Measured. Published at `GET /public/backtrace-resolution`, broken out by hop depth. |
| **Evidence support** (Audit, `synthesize/audit.py`) | Does this quote actually support the claim it was cited for? | **Not yet evaluated.** No accuracy figure exists. |

Synthesis relevance and evidence support are both semantic judgments made by a language model.
Relevance is the easier question (is this passage on topic?); support is the harder one (does it
establish the specific claim?). Neither judgment's error rate against known-good and known-bad
examples has been measured. A passing Audit result is a prompt for the author to look closer, not a
guarantee to a reader, and no trustworthiness claim should rest on it until an evaluation against a
human-labeled set is published with a confidence interval.

Published Synthesis rates count only syntheses run on the publishing server. Syntheses imported from a
`.ath` file keep the numbers they arrived with for display, but those were measured elsewhere, so
they are left out of public aggregates.

Two caveats on the Backtrace number, both also stated in the endpoint's response docs:

- The rate counts only hops that **finished** looking. Hops still running are left out of the
  denominator; hops handed back to the user for manual import count as unresolved.
- The denominator is references a person **chose to chase**, not every reference the paper cites.
  It measures "when we looked, did we find it," not "how much of the literature is reachable."

Resolution typically falls with depth, so read the per-depth figures rather than the overall rate
alone: a tree's deepest hops are the ones being presented as origins.

`GET /public/verification-rate` still answers but is deprecated. It reports the retired combined
check (a model's guess that text looked verbatim, combined with relevance), which new syntheses no
longer record.

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
