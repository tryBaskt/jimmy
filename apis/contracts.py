from typing import Literal
from pydantic import Field
from paperlab.models import Contract


class NewProject(Contract):
    title: str = Field(min_length=1, max_length=180)


class NewUpload(Contract):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["paper", "data"]
    size: int = Field(gt=0, le=25 * 1024 * 1024)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class NewRun(Contract):
    paper_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    idempotency_key: str = Field(min_length=8, max_length=100)
