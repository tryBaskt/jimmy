"""Local-only HTTP adapter for the finite strategy research workflow."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings

router = APIRouter(prefix="/research", tags=["research"])
logger = logging.getLogger(__name__)
RESEARCH_SCHEMA_VERSION = 2


class ResearchRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    lookback_months: int = Field(ge=1, le=12, strict=True)


def execute_research(prompt: str, lookback_months: int, settings: Settings) -> dict:
    """Load the heavy research dependencies only in local research mode."""
    import boto3

    from packages.agentic_workflows.strategy_and_research.plan import BedrockPlanner
    from packages.agentic_workflows.strategy_and_research.research import run_research
    from packages.clients.dynamodb_client import DynamoDBClient
    from packages.clients.vectorbt_client import VectorBTClient
    from packages.clients.yfinance_client import YFinanceClient

    session = boto3.Session(region_name=settings.aws_region)
    plan = BedrockPlanner(session=session, model_id=settings.bedrock_model_id).plan(
        prompt, lookback_months
    )
    membership = DynamoDBClient(
        metadata_table_name="jimmy-dev-stock-metadata",
        membership_table_name="jimmy-dev-stock-universe-membership",
        dynamodb_resource=session.resource("dynamodb"),
    )
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * 5)
    return run_research(
        plan=plan,
        membership_client=membership,
        price_client=YFinanceClient(),
        simulator=VectorBTClient(),
        start=start,
        end=end,
    )


@router.get("/capabilities")
def capabilities(request: Request) -> dict:
    return {"enabled": request.app.state.settings.local_research_enabled,
            "schema_version": RESEARCH_SCHEMA_VERSION}


@router.post("/run")
def run(request: Request, body: ResearchRequest) -> dict:
    settings: Settings = request.app.state.settings
    if not settings.local_research_enabled:
        raise HTTPException(status_code=503, detail="Research is available in local development only")
    try:
        result = execute_research(body.prompt.strip(), body.lookback_months, settings)
        return {"schema_version": RESEARCH_SCHEMA_VERSION, **result}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Local research failed")
        raise HTTPException(
            status_code=502,
            detail="A model or market-data service failed. Check the API logs for details.",
        ) from error
