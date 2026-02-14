"""S3 download/upload helpers for Lambda handlers."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import boto3

logger = logging.getLogger(__name__)

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def get_bucket_name() -> str:
    return os.environ["DATA_BUCKET"]


def download_file(s3_key: str, local_path: str) -> str:
    """Download an S3 object to a local file path. Returns local_path."""
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    get_s3_client().download_file(get_bucket_name(), s3_key, local_path)
    logger.info(f"Downloaded s3://{get_bucket_name()}/{s3_key} -> {local_path}")
    return local_path


def upload_file(local_path: str, s3_key: str) -> None:
    """Upload a local file to S3."""
    get_s3_client().upload_file(local_path, get_bucket_name(), s3_key)
    logger.info(f"Uploaded {local_path} -> s3://{get_bucket_name()}/{s3_key}")


def upload_json(data: Any, s3_key: str) -> None:
    """Upload a JSON-serializable object to S3."""
    body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    get_s3_client().put_object(
        Bucket=get_bucket_name(),
        Key=s3_key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(f"Uploaded JSON -> s3://{get_bucket_name()}/{s3_key}")


def download_json(s3_key: str) -> Any:
    """Download and parse a JSON object from S3."""
    resp = get_s3_client().get_object(Bucket=get_bucket_name(), Key=s3_key)
    body = resp["Body"].read().decode("utf-8")
    return json.loads(body)


def list_keys(prefix: str, suffix: str = "") -> List[str]:
    """List S3 keys under a prefix, optionally filtered by suffix."""
    client = get_s3_client()
    bucket = get_bucket_name()
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not suffix or key.endswith(suffix):
                keys.append(key)
    return keys


def generate_presigned_put_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for uploading to S3."""
    return get_s3_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_bucket_name(), "Key": s3_key},
        ExpiresIn=expires_in,
    )


def generate_presigned_get_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a presigned URL for downloading from S3."""
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_bucket_name(), "Key": s3_key},
        ExpiresIn=expires_in,
    )
