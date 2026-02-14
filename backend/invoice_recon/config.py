"""Configuration management for invoice reconciliation."""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Fix AWS_PROFILE if it's an empty string - unset it from environment
if os.getenv("AWS_PROFILE") == "":
    del os.environ["AWS_PROFILE"]


class Config:
    """Application configuration."""

    # AWS Configuration
    AWS_REGION: str = os.getenv("AWS_REGION", "us-west-2")
    AWS_PROFILE: Optional[str] = os.getenv("AWS_PROFILE") or None  # Convert empty string to None

    # Bedrock Model (REQUIRED)
    BEDROCK_MODEL_ID: str = os.getenv(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    )

    # Bedrock Vision Model (for table detection - faster/cheaper than main model)
    BEDROCK_VISION_MODEL_ID: str = os.getenv(
        "BEDROCK_VISION_MODEL_ID", "us.amazon.nova-lite-v1:0"
    )

    # Text Extraction
    TEXT_MIN_CHARS: int = int(os.getenv("TEXT_MIN_CHARS", "40"))
    TEXTRACT_MODE: str = os.getenv("TEXTRACT_MODE", "auto")  # auto, always, never
    TEXTRACT_MAX_LINES: int = int(os.getenv("TEXTRACT_MAX_LINES", "300"))

    # Table Detection
    TABLE_DETECTION_ENABLED: bool = os.getenv("TABLE_DETECTION_ENABLED", "false").lower() == "true"

    # PyMuPDF Table Extraction (quality thresholds)
    MIN_TABLE_ROWS: int = int(os.getenv("MIN_TABLE_ROWS", "2"))
    MIN_TABLE_CELLS: int = int(os.getenv("MIN_TABLE_CELLS", "4"))
    MIN_TABLE_CELL_COVERAGE: float = float(os.getenv("MIN_TABLE_CELL_COVERAGE", "0.5"))
    PYMUPDF_TABLE_STRATEGY: str = os.getenv("PYMUPDF_TABLE_STRATEGY", "lines")  # lines, text, lines_strict

    # Processing
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "3"))

    # Matching
    MIN_CANDIDATE_SCORE: float = float(os.getenv("MIN_CANDIDATE_SCORE", "0.1"))

    # Output
    OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "jobs")

    @classmethod
    def validate(cls) -> None:
        """Validate required configuration."""
        if not cls.BEDROCK_MODEL_ID:
            raise ValueError("BEDROCK_MODEL_ID is required")

        if cls.TEXTRACT_MODE not in ("auto", "always", "never"):
            raise ValueError(f"Invalid TEXTRACT_MODE: {cls.TEXTRACT_MODE}")

    @classmethod
    def get_job_dir(cls, job_id: str) -> Path:
        """Get the job directory path."""
        return Path(cls.OUTPUT_DIR) / job_id

    @classmethod
    def get_artifacts_dir(cls, job_id: str) -> Path:
        """Get the artifacts directory path."""
        return cls.get_job_dir(job_id) / "artifacts"


# Validate configuration on import (skip in Lambda — env vars set by CDK)
if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    Config.validate()
