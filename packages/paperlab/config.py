"""Explicit configuration; local mode never activates in AWS implicitly."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    root: str = ".paperlab"
    region: str = "us-east-1"
    table: str = ""
    bucket: str = ""
    workflow: str = ""
    issuer: str = ""
    audience: str = ""
    origin: str = "http://localhost:5173"
    planner_model: str = "us.moonshotai.kimi-k3"
    worker_model: str = "us.moonshotai.kimi-k3"
    bigdata_secret: str = ""
    websocket_endpoint: str = ""
    max_upload: int = 25 * 1024 * 1024
    max_experiments: int = 16
    max_extrapolations: int = 4
    max_replans: int = 3

    @classmethod
    def load(cls):
        aws = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME") or os.getenv("ECS_CONTAINER_METADATA_URI_V4"))
        s = cls(
            mode=os.getenv("PAPERLAB_MODE", "aws" if aws else "local"),
            root=os.getenv("PAPERLAB_ROOT", ".paperlab"),
            region=os.getenv("AWS_REGION", "us-east-1"),
            table=os.getenv("TABLE_NAME", ""),
            bucket=os.getenv("ARTIFACT_BUCKET", ""),
            workflow=os.getenv("WORKFLOW_ARN", ""),
            issuer=os.getenv("JWT_ISSUER", ""),
            audience=os.getenv("JWT_AUDIENCE", ""),
            origin=os.getenv("WEB_ORIGIN", "http://localhost:5173"),
            planner_model=os.getenv("PLANNER_MODEL_ID", "us.moonshotai.kimi-k3"),
            worker_model=os.getenv("WORKER_MODEL_ID", "us.moonshotai.kimi-k3"),
            bigdata_secret=os.getenv("BIGDATA_SECRET_ARN", ""),
            websocket_endpoint=os.getenv("WEBSOCKET_MANAGEMENT_ENDPOINT", ""),
        )
        if s.mode not in {"local", "aws"} or (aws and s.mode != "aws"):
            raise ValueError("Invalid runtime mode")
        if s.mode == "aws" and not (s.table and s.bucket and s.issuer and s.audience):
            raise ValueError("AWS metadata, storage and identity must be configured")
        return s
