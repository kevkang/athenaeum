FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# Make the CPU-only PyTorch index authoritative for the whole build (not
# just a separate first step) so that when sentence-transformers/transformers
# pulls in a torch version bump, pip resolves it from here too instead of
# silently falling back to the default PyPI index's CUDA wheel (~5+ GB of
# nvidia-*/cuda-toolkit/triton packages that blew past this host's tmpfs).
ENV PIP_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PIP_EXTRA_INDEX_URL=https://pypi.org/simple

RUN pip install --no-cache-dir -e .

# Headless Chromium for .athmd -> PDF rendering (archive/athmd_render.py).
# --with-deps pulls in the apt packages Chromium needs on Debian slim.
RUN playwright install --with-deps chromium

EXPOSE 8000

CMD ["uvicorn", "athenaeum.main:app", "--host", "0.0.0.0", "--port", "8000"]
