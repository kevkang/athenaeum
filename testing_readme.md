# Testing Athenaeum locally with Docker

This covers two things: running the **automated test suite** against a real
database, and **manually testing the app** in a browser. Both use the same
`docker-compose.yml` services — the difference is how much of the stack you
bring up.

## What the services are

`docker-compose.yml` defines eight services. You rarely need all of them:

| Service | Port(s) | What it's for | Needed for automated tests? | Needed for manual UI testing? |
|---|---|---|---|---|
| `postgres` | 5432 | Source of truth — `pgvector/pgvector:pg16`, not stock Postgres, because sentence embeddings need the `vector` extension | Yes | Yes |
| `neo4j` | 7474 (browser), 7687 (bolt) | Derived graph projection (citation/co-citation, ghost papers) | Only if you're testing `graph/sync.py` | Yes |
| `redis` | 6379 | `arq` job queue + pub/sub for live synthesis/backtrace step streaming | No | Yes |
| `grobid` | 8070 | PDF → structured text extraction for paper ingestion | No | Only if ingesting real PDFs |
| `minio` + `minio-init` | 9000 (S3 API), 9001 (console) | Local S3-compatible storage for uploaded PDFs | No | Only if uploading papers |
| `api` | 8000 | FastAPI backend, `--reload` on | No (tests import the code directly) | Yes |
| `worker` | — | `arq` background jobs (ingestion, synthesis, embedding) | No | Yes, for anything that isn't instant (paper ingestion, synthesis runs) |

## Running the automated test suite

This is what CI (`.github/workflows/ci.yml`) does. Most of the suite is pure
unit tests with no dependencies, but the DB-backed tests (`tests/conftest.py`'s
`db_session` fixture — archive round-trip, hybrid search, orchestrator
extraction) need a real, migrated Postgres to run against.

```bash
# 1. Start just the database — no need for the full stack to run tests.
docker compose up -d postgres

# 2. Wait for it to actually accept connections before doing anything else.
until docker exec athenaeum-postgres-1 pg_isready -U athenaeum; do sleep 1; done

# 3. Apply migrations. Safe to run repeatedly — Alembic tracks what's
#    already applied and no-ops if the schema is current.
.venv/bin/python -m alembic upgrade head

# 4. Run the checks CI runs, in the same order.
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m pytest
```

**Why `postgres` alone, not the full stack:** the test suite never touches
Redis, Grobid, or MinIO, and `graph/sync.py` currently has no automated
coverage (it opens its own DB connection internally, which doesn't play well
with the rollback-isolated test fixture — see the comment in
`tests/conftest.py`). Bringing up the whole stack just to run `pytest` is
slower for no benefit. Add `neo4j` to the `up -d` line only once tests that
actually exercise it exist.

**Test isolation:** each test that uses the `db_session` fixture runs inside
its own transaction that's rolled back at teardown — including tests that
call `session.commit()` internally (e.g. exercising `import_project`). You
never need to manually clean up test data between runs; if you ever *do* see
leftover rows, something about the fixture broke, not your test.

## Manually testing in a browser

This needs the full stack plus the frontend dev server.

```bash
# Build and start everything. --build picks up backend dependency/Dockerfile
# changes; for ordinary src/ edits it's not needed — see "Do I need --build?" below.
docker compose up -d --build

# In a separate terminal: the frontend.
cd web && npm run dev
```

Open `http://localhost:5173`.

### Logging in

ORCID OAuth needs a registered sandbox client ID/secret
(`ORCID_CLIENT_ID`/`ORCID_CLIENT_SECRET`), which isn't set by default in
`docker-compose.yml` — the login button won't work out of the box. For local
testing, mint a session directly instead of going through OAuth:

```bash
.venv/bin/python <(cat <<'EOF'
import asyncio, uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from athenaeum.security import issue_session_token
from athenaeum.storage.models import Project, ProjectMember, User

async def main():
    engine = create_async_engine("postgresql+asyncpg://athenaeum:athenaeum@localhost:5432/athenaeum")
    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(id=uuid.uuid4(), orcid="0000-0001-2345-6789", display_name="Test User")
        s.add(u); await s.flush()
        p = Project(id=uuid.uuid4(), title="Test Project", owner_id=u.id)
        s.add(p); await s.flush()
        s.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner"))
        await s.commit()
        print("token:", issue_session_token(u.id))
        print("project:", p.id)
asyncio.run(main())
EOF
)
```

Then, in the browser console at `http://localhost:5173`:

```js
localStorage.setItem('ath_token', '<token from above>');
localStorage.setItem('ath_user', JSON.stringify({id: '<user id>', orcid: '0000-0001-2345-6789', name: 'Test User'}));
```

Reload and navigate to `/projects/<project id>/workspace`.

This only works because `ENVIRONMENT` defaults to `development` in
`config.py`, which is what `docker-compose.yml`'s `api` service runs under —
the same trick will not work (and should not) against a production
deployment, where `ENVIRONMENT=production` makes the server refuse to boot
with the default `JWT_SECRET` in the first place.

### Known local-only gaps

- **No `ANTHROPIC_API_KEY` set by default.** Find, Backtrace, and Synthesis
  will fail fast with a 503 rather than actually calling an LLM. Set the env
  var on the `api`/`worker` services in `docker-compose.yml`, or add a
  personal key via Settings → Configure LLM in the app once logged in.
- **Zotero (Local)** needs `ZOTERO_LOCAL_HOST_MOUNT` pointed at a real Zotero
  data directory to do anything beyond showing "not configured."

### Do I need `--build`?

`docker-compose.yml` bind-mounts `./src` into the `api`/`worker` containers
and runs `uvicorn --reload`, so ordinary Python source edits show up live —
no rebuild needed, just save the file. You only need
`docker compose up -d --build` when `pyproject.toml`'s dependencies change or
the `Dockerfile` itself changes. Frontend changes never need it — `npm run
dev` runs outside Docker entirely.

## Shutting down

Three options, in increasing order of destructiveness — use the least
destructive one that does what you need:

| Command | What it does | When to use it |
|---|---|---|
| `docker compose stop` | Stops containers, keeps them and their volumes intact | Between testing sessions. Resume instantly with `docker compose start` — no rebuild, no re-running migrations. |
| `docker compose down` | Stops **and removes** containers; named volumes (`postgres-data`, `neo4j-data`, etc.) survive | After changing `docker-compose.yml` itself (new env vars, port mappings) — forces a clean container recreate on next `up`, without losing data. |
| `docker compose down -v` | Removes containers **and volumes** | Only when you deliberately want a blank database — this deletes every seeded user, paper, and project. Re-run migrations after. |

**Stopping the frontend dev server:** `npm run dev` doesn't forward signals
to the underlying Vite process it spawns, so `kill`ing the `npm run dev` job
directly (or `Ctrl-C` in its terminal, which does work) can leave the actual
listener on port 5173 running. Find and kill the real process by port
instead:

```bash
lsof -ti:5173 -sTCP:LISTEN | xargs -r kill
```

Avoid broad patterns like `pkill -f node` or `pkill -f vite` — on a dev
machine with other Node processes running, that can kill more than you
intended.

### Verifying a clean shutdown

```bash
docker compose ps                                    # should list nothing (or only "exited")
lsof -ti:5173,8000,5432,7687,6379,8070,9000           # should print nothing
```
