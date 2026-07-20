"""arq WorkerSettings — entry point for `arq athenaeum.worker.settings.WorkerSettings`."""
from arq.connections import RedisSettings

from athenaeum.config import get_settings
from athenaeum.worker.jobs import (
    ingest_local_zotero_item,
    ingest_paper,
    ingest_zotero_item,
    run_synthesis_job,
    shutdown,
    startup,
    sync_project_graph,
)

_settings = get_settings()


class WorkerSettings:
    functions = [
        ingest_paper,
        ingest_zotero_item,
        ingest_local_zotero_item,
        run_synthesis_job,
        sync_project_graph,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = 4
    job_timeout = 1800  # 30 minutes — GROBID + embedding on a large PDF
