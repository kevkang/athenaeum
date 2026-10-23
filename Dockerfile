FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# Install CPU-only PyTorch so sentence-transformers doesn't pull the CUDA
# wheel (~2.5 GB) from PyPI.
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -e .

# Headless Chromium for .athmd -> PDF rendering (archive/athmd_render.py).
# --with-deps pulls in the apt packages Chromium needs on Debian slim.
RUN playwright install --with-deps chromium

EXPOSE 8000

CMD ["uvicorn", "athenaeum.main:app", "--host", "0.0.0.0", "--port", "8000"]
