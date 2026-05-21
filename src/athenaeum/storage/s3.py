"""Async-friendly S3 helpers using boto3 via run_in_executor."""
from __future__ import annotations

import asyncio
from pathlib import Path


def _client(region: str):
    import boto3
    return boto3.client("s3", region_name=region)


async def upload_fileobj(data: bytes, bucket: str, key: str, region: str = "us-east-1") -> None:
    loop = asyncio.get_running_loop()
    client = _client(region)
    await loop.run_in_executor(None, lambda: client.put_object(Bucket=bucket, Key=key, Body=data))


async def download_to_path(bucket: str, key: str, dest: Path, region: str = "us-east-1") -> None:
    loop = asyncio.get_running_loop()
    client = _client(region)
    await loop.run_in_executor(None, lambda: client.download_file(bucket, key, str(dest)))


async def delete_object(bucket: str, key: str, region: str = "us-east-1") -> None:
    loop = asyncio.get_running_loop()
    client = _client(region)
    await loop.run_in_executor(None, lambda: client.delete_object(Bucket=bucket, Key=key))
