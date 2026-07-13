"""Async-friendly S3 helpers using boto3 via run_in_executor.

Also used against MinIO for local dev (see docker-compose.yml's `minio` service)
by passing `endpoint_url` — real production S3 always leaves it unset.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


def _client(region: str, endpoint_url: str | None):
    import boto3
    from botocore.config import Config

    # MinIO (and most S3-compatible stores) don't do virtual-hosted-style
    # bucket addressing (`bucket.host/key`) out of the box the way AWS does —
    # `path` style (`host/bucket/key`) is what actually resolves against a
    # plain `localhost:9000`-type endpoint. Real AWS keeps its own default.
    config = Config(s3={"addressing_style": "path"}) if endpoint_url else None
    return boto3.client("s3", region_name=region, endpoint_url=endpoint_url, config=config)


async def upload_fileobj(
    data: bytes, bucket: str, key: str, region: str = "us-east-1", endpoint_url: str | None = None
) -> None:
    loop = asyncio.get_running_loop()
    client = _client(region, endpoint_url)
    await loop.run_in_executor(None, lambda: client.put_object(Bucket=bucket, Key=key, Body=data))


async def download_to_path(
    bucket: str, key: str, dest: Path, region: str = "us-east-1", endpoint_url: str | None = None
) -> None:
    loop = asyncio.get_running_loop()
    client = _client(region, endpoint_url)
    await loop.run_in_executor(None, lambda: client.download_file(bucket, key, str(dest)))


async def delete_object(
    bucket: str, key: str, region: str = "us-east-1", endpoint_url: str | None = None
) -> None:
    loop = asyncio.get_running_loop()
    client = _client(region, endpoint_url)
    await loop.run_in_executor(None, lambda: client.delete_object(Bucket=bucket, Key=key))
