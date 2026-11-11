"""Small, validated contracts shared by the local agents."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DATA_ATTEMPTS = 4
MAX_DATA_REQUESTS_PER_ROUND = 4


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    page: int = Field(ge=1, description="One-based PDF page, not printed page number")
    quote: str = Field(min_length=8, description="Exact short quote from this PDF page")


class PublishedResult(Contract):
    metric: Literal["mean_monthly_return", "hac_t_stat", "annualized_sharpe"]
    value: float = Field(allow_inf_nan=False)
    tolerance: float = Field(ge=0, allow_inf_nan=False)
    citation: Citation


class DatasetSpec(Contract):
    name: str
    source: Literal["yfinance", "public_csv"]
    symbols: list[str] = Field(max_length=100)
    start: date
    end: date = Field(description="Exclusive; use first of current month to avoid partial months")
    csv_url: str | None
    rationale: str
    known_differences: list[str]

    @model_validator(mode="after")
    def valid_request(self):
        if self.start >= self.end:
            raise ValueError("Dataset start must precede end")
        if self.source == "yfinance" and not self.symbols:
            raise ValueError("Yahoo requires explicit tickers")
        if self.source == "public_csv" and not self.csv_url:
            raise ValueError("Public CSV requires a direct download URL")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("Duplicate symbols")
        return self


class ExperimentSpec(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    name: str
    kind: Literal["paper", "extrapolation"]
    method: Literal["cross_sectional_momentum", "time_series_momentum", "buy_and_hold", "unsupported"]
    hypothesis: str
    formation_months: int = Field(ge=1, le=60)
    holding_months: int = Field(ge=1, le=60)
    skip_months: int = Field(ge=0, le=12)
    quantiles: int = Field(ge=2, le=20)
    fees_bps: float = Field(ge=0, le=1000, allow_inf_nan=False)
    slippage_bps: float = Field(ge=0, le=1000, allow_inf_nan=False)
    start: date | None
    end: date | None
    methodology_matches_paper: bool
    deviations: list[str]
    published: list[PublishedResult] = Field(max_length=8)

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.start and self.end and self.start >= self.end:
            raise ValueError("Experiment start must precede exclusive end")
        return self


class ResearchPlan(Contract):
    title: str
    summary: str
    main_claim: str
    claim_citation: Citation
    methodology: str
    original_data_requirements: str
    dataset: DatasetSpec
    experiments: list[ExperimentSpec] = Field(min_length=1, max_length=8)
    limitations: list[str]


class InvestigationPlan(ResearchPlan):
    extrapolations: list[ExperimentSpec] = Field(max_length=5)
    alternative_datasets: list[DatasetSpec] = Field(max_length=MAX_DATA_REQUESTS_PER_ROUND - 1)


class DataDecision(Contract):
    action: Literal["accept", "retry", "stop"]
    reason: str
    selected_candidate: int | None = Field(ge=1, le=MAX_DATA_ATTEMPTS * MAX_DATA_REQUESTS_PER_ROUND)
    requests: list[DatasetSpec] = Field(max_length=MAX_DATA_REQUESTS_PER_ROUND)
    deviations: list[str]
    evaluation_start: date | None = Field(description="Chosen evaluation start, excluding formation warmup")
    evaluation_end: date | None = Field(description="Exclusive chosen evaluation end")

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.evaluation_start and self.evaluation_end and self.evaluation_start >= self.evaluation_end:
            raise ValueError("Evaluation start must precede exclusive end")
        return self


class ExperimentAdjustment(Contract):
    id: str
    start: date | None
    end: date | None
    formation_months: int = Field(ge=1, le=60)
    holding_months: int = Field(ge=1, le=60)
    skip_months: int = Field(ge=0, le=12)
    quantiles: int = Field(ge=2, le=20)
    reason: str = Field(min_length=1)


class ExperimentRevision(Contract):
    selected_candidate: int | None = Field(ge=1, le=MAX_DATA_ATTEMPTS * MAX_DATA_REQUESTS_PER_ROUND)
    experiments: list[ExperimentAdjustment] = Field(max_length=13)
    rationale: str
