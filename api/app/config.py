import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    cognito_domain: str = field(default_factory=lambda: os.getenv("JIMMY_COGNITO_DOMAIN", ""))
    cognito_client_id: str = field(default_factory=lambda: os.getenv("JIMMY_COGNITO_CLIENT_ID", ""))
    public_url: str = field(default_factory=lambda: os.getenv("JIMMY_PUBLIC_URL", ""))
    sessions_table: str = field(default_factory=lambda: os.getenv("JIMMY_SESSIONS_TABLE", ""))
    jobs_table: str = field(default_factory=lambda: os.getenv("JIMMY_JOBS_TABLE", ""))
    jobs_queue_url: str = field(default_factory=lambda: os.getenv("JIMMY_JOBS_QUEUE_URL", ""))
    results_bucket: str = field(default_factory=lambda: os.getenv("JIMMY_RESULTS_BUCKET", ""))
