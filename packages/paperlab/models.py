"""Validated contracts shared by planner, API and trusted numerical templates."""

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Citation(Contract):
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=3000)


class UniverseDefinition(Contract):
    description: str
    exchanges: list[str] = Field(default_factory=list, max_length=10)
    country: str = ""
    security_type: Literal["common_stock", "unspecified"] = "common_stock"
    historical_membership_required: bool = True


class Requirement(Contract):
    kind: Literal["prices", "returns", "documents", "fundamentals"]
    description: str
    symbols: list[str] = Field(default_factory=list, max_length=100)
    start: date
    end: date
    frequency: Literal["daily", "monthly"] = "monthly"
    query: str = ""
    vintage: str = "unspecified"
    universe: UniverseDefinition | None = None
    return_basis: Literal["total_return", "price_return"] = "total_return"

    @model_validator(mode="after")
    def dates(self):
        if self.end <= self.start:
            raise ValueError("End must be later than start")
        return self


class Expected(Contract):
    metric: Literal["mean_monthly_return", "t_stat", "annualized_volatility", "max_drawdown"]
    value: float
    tolerance: float = Field(ge=0)
    citation: Citation


class Experiment(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str
    claim: str
    central: bool = True
    method: Literal["momentum_sort", "return_series", "unsupported"]
    requirement: Requirement
    formation_months: int = Field(default=6, ge=1, le=36)
    holding_months: int = Field(default=6, ge=1, le=36)
    skip_months: int = Field(default=0, ge=0, le=12)
    quantiles: int = Field(default=10, ge=2, le=20)
    expected: list[Expected] = Field(default_factory=list, max_length=30)
    citations: list[Citation] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class PaperPlan(Contract):
    title: str
    main_claim: str
    supporting_claims: list[str]
    citations: list[Citation] = Field(min_length=1)
    experiments: list[Experiment] = Field(min_length=1, max_length=16)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph(self):
        ids = [x.id for x in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate experiment IDs")
        visited, active = set(), set()
        nodes = {x.id: x for x in self.experiments}

        def visit(key):
            if key not in nodes or key in active:
                raise ValueError("Invalid dependency graph")
            if key in visited:
                return
            active.add(key)
            for dep in nodes[key].dependencies:
                visit(dep)
            active.remove(key)
            visited.add(key)

        for key in ids:
            visit(key)
        return self


class Selection(Contract):
    candidate_id: str | None
    rationale: str
    modified_experiment: Experiment | None
    caveats: list[str]
    comparison_eligible: bool


class DataReplan(Contract):
    alternatives: list[Experiment] = Field(default_factory=list, max_length=3)
    rationale: str


class Extension(Contract):
    parent_id: str
    rationale: str
    experiment: Experiment
    designation: Literal["exploratory", "reserved_evaluation"] = "exploratory"


class Extensions(Contract):
    experiments: list[Extension] = Field(max_length=4)
    exclusions: list[str]
