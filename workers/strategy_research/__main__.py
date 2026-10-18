"""Run a Bedrock-planned, read-only strategy backtest from the repository root."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from packages.agentic_workflows.strategy_and_research.plan import BedrockPlanner
from packages.agentic_workflows.strategy_and_research.research import run_research
from packages.clients.dynamodb_client import DynamoDBClient
from packages.clients.vectorbt_client import VectorBTClient, VectorBTClientError
from packages.clients.yfinance_client import YFinanceClient, YFinanceClientError


def _date(value: str) -> datetime:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD") from error
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Research a monthly momentum strategy")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--lookback-months", type=int, required=True, choices=range(1, 13))
    parser.add_argument("--profile", help="AWS CLI profile; otherwise use the default credential chain")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument("--model-id", default="us.amazon.nova-pro-v1:0")
    parser.add_argument("--start", type=_date, default=datetime.now(timezone.utc) - timedelta(days=365 * 5))
    parser.add_argument("--end", type=_date, default=datetime.now(timezone.utc))
    parser.add_argument("--output", type=Path, default=Path("strategy_research_result.json"))
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        print("Planning the strategy with Amazon Bedrock...", flush=True)
        plan = BedrockPlanner(session=session, model_id=args.model_id).plan(
            args.prompt, args.lookback_months
        )
        print(
            f"Plan: {plan.universe}, top {plan.top_n}, "
            f"{plan.ranking_rule()}.",
            flush=True,
        )
        membership = DynamoDBClient(
            metadata_table_name=f"jimmy-{args.env}-stock-metadata",
            membership_table_name=f"jimmy-{args.env}-stock-universe-membership",
            dynamodb_resource=session.resource("dynamodb"),
        )
        print("Reading universe membership and downloading adjusted prices...", flush=True)
        report = run_research(
            plan=plan,
            membership_client=membership,
            price_client=YFinanceClient(),
            simulator=VectorBTClient(),
            start=args.start,
            end=args.end,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(
            f"{plan.lookback_months}-month monthly strategy return: "
            f"{report['strategy']['total_return']:.2%}; "
            f"SPY: {report['spy']['total_return']:.2%}; "
            f"max drawdown: {report['strategy']['max_drawdown']:.2%}.",
            flush=True,
        )
        print(
            f"Eligible symbols: {report['eligible_symbol_count']}/"
            f"{report['universe_symbol_count']}. Full report: {args.output}",
            flush=True,
        )
        print("Exploratory result: current membership introduces survivorship bias.", flush=True)
        return 0
    except (
        BotoCoreError, ClientError, ValueError, RuntimeError, OSError,
        VectorBTClientError, YFinanceClientError,
    ) as error:
        print(f"Research failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
