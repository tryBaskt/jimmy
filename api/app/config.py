import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    local_strategy_enabled: bool = field(
        default_factory=lambda: os.getenv("JIMMY_LOCAL_STRATEGY", "0") == "1"
    )
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    bedrock_model_id: str = field(
        default_factory=lambda: os.getenv("JIMMY_BEDROCK_MODEL_ID", "us.moonshotai.kimi-k3")
    )
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if origin.strip()
        )
    )
