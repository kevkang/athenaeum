"""Sentence embedding via sentence-transformers (CPU/GPU, async wrapper)."""
from __future__ import annotations

import asyncio
from functools import cache


@cache
def _get_model(model_name: str):  # type: ignore[return]
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def _encode_sync(texts: list[str], model_name: str) -> list[list[float]]:
    model = _get_model(model_name)
    vectors = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return vectors.tolist()


async def embed_sentences(texts: list[str], model_name: str) -> list[list[float]]:
    """Embed a list of sentences; runs the CPU-bound encode in a thread executor."""
    if not texts:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _encode_sync, texts, model_name)
