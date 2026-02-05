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

    # Text Extraction
    TEXT_MIN_CHARS: int = int(os.getenv("TEXT_MIN_CHARS", "40"))
    TEXTRACT_MODE: str = os.getenv("TEXTRACT_MODE", "auto")  # auto, always, never
    TEXTRACT_MAX_LINES: int = int(os.getenv("TEXTRACT_MAX_LINES", "300"))

    # Table Detection
    TABLE_DETECTION_ENABLED: bool = os.getenv("TABLE_DETECTION_ENABLED", "false").lower() == "true"

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

        if cls.AWS_REGION != "us-west-2":
            raise ValueError(f"AWS_REGION must be us-west-2, got: {cls.AWS_REGION}")

    @classmethod
    def get_job_dir(cls, job_id: str) -> Path:
        """Get the job directory path."""
        return Path(cls.OUTPUT_DIR) / job_id

    @classmethod
    def get_artifacts_dir(cls, job_id: str) -> Path:
        """Get the artifacts directory path."""
        return cls.get_job_dir(job_id) / "artifacts"


# Validate configuration on import
Config.validate()
