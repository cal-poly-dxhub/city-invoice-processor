"""Step 1: Parse CSV Lambda handler."""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3

# Add backend to path so invoice_recon modules are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared.s3_utils import download_file, upload_json

from invoice_recon.csv_parser import parse_csv
from pathlib import Path

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# DynamoDB for job status updates
_dynamodb = boto3.resource("dynamodb")
_cache_table = _dynamodb.Table(os.environ["CACHE_TABLE"])


def _update_job_status(job_id: str, status: str) -> None:
    """Update job status in DynamoDB."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        _cache_table.update_item(
            Key={"PK": "JOBS", "SK": job_id},
            UpdateExpression="SET #s = :status, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": status, ":now": now},
        )
    except Exception as e:
        logger.warning(f"Failed to update job status: {e}")


def lambda_handler(event, context):
    """
    Parse invoice CSV from S3.

    Input:  {csv_key, bucket}  (job_id derived from csv_key)
    Output: {job_id, csv_key, pdf_prefix, line_items_key}

    Line items are written to S3 to avoid Step Functions 256KB payload limit.
    """
    csv_key = event["csv_key"]
    # Derive job_id from csv_key: "uploads/{job_id}/invoice.csv" -> "{job_id}"
    parts = csv_key.split("/")
    job_id = parts[1] if len(parts) >= 3 else event.get("job_id", "unknown")
    pdf_prefix = f"uploads/{job_id}/pdf/"

    logger.info(f"ParseCSV: job_id={job_id}, csv_key={csv_key}")

    # Mark job as processing
    _update_job_status(job_id, "PROCESSING")

    # Download CSV to /tmp
    local_csv = f"/tmp/{job_id}/invoice.csv"
    download_file(csv_key, local_csv)

    # Parse using existing csv_parser module
    line_items = parse_csv(Path(local_csv))

    # Serialize line items (Pydantic models -> dicts)
    line_items_data = [item.model_dump(mode="json") for item in line_items]

    logger.info(f"ParseCSV: parsed {len(line_items_data)} line items")

    # Write line items to S3 (avoids 256KB Step Functions payload limit)
    line_items_key = f"jobs/{job_id}/line_items.json"
    upload_json(line_items_data, line_items_key)
    logger.info(f"ParseCSV: wrote line items to s3://{line_items_key}")

    return {
        "job_id": job_id,
        "csv_key": csv_key,
        "pdf_prefix": pdf_prefix,
        "line_items_key": line_items_key,
    }
