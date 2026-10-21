"""Local HTTP adapter for a bounded strategy test."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings

router = APIRouter(prefix="/strategy", tags=["strategy"])
logger = logging.getLogger(__name__)
STRATEGY_SCHEMA_VERSION = 3


class StrategyRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    lookback_months: int = Field(ge=1, le=12, strict=True)


def execute_strategy(prompt: str, lookback_months: int, settings: Settings) -> dict:
    """Load heavy strategy dependencies only for local strategy runs."""
    import boto3

    from packages.agents.strategy_agent.plan import BedrockStrategyAgent
    from packages.agents.strategy_agent.tools.dynamodb import list_universe_symbols
    from packages.agents.strategy_agent.tools.yfinance import (
        get_adjusted_close_prices, get_daily_prices_and_market_caps,
    )
    from packages.agents.strategy_agent.workflow import run_strategy

    session = boto3.Session(region_name=settings.aws_region)
    dynamodb = session.client("dynamodb")
    end = datetime.now(timezone.utc)
    return run_strategy(
        prompt=prompt,
        lookback_months=lookback_months,
        agent=BedrockStrategyAgent(session=session, model_id=settings.bedrock_model_id),
        universe_loader=lambda universe: list_universe_symbols(
            dynamodb, "jimmy-dev-stock-universe-membership", universe
        ),
        price_loader=get_adjusted_close_prices,
        market_cap_loader=get_daily_prices_and_market_caps,
        start=end - timedelta(days=365),
        end=end,
    )


@router.get("/capabilities")
def capabilities(request: Request) -> dict:
    return {
        "enabled": request.app.state.settings.local_strategy_enabled,
        "schema_version": STRATEGY_SCHEMA_VERSION,
    }


@router.post("/run")
def run(request: Request, body: StrategyRequest) -> dict:
    settings: Settings = request.app.state.settings
    if not settings.local_strategy_enabled:
        raise HTTPException(status_code=503, detail="Strategy testing is available locally only")
    try:
        result = execute_strategy(body.prompt.strip(), body.lookback_months, settings)
        return {"schema_version": STRATEGY_SCHEMA_VERSION, **result}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.exception("Local strategy test failed")
        raise HTTPException(
            status_code=502,
            detail="A model or market-data service failed. Check the API logs for details.",
        ) from error
