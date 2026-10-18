#!/usr/bin/env python3
"""Ingest local stock universes and FMP company profiles into dev DynamoDB.

Requires Python 3.10+ and boto3 (``python -m pip install boto3``). The FMP key is
loaded at runtime from Secrets Manager secret jimmy-dev-fmp-api-key. Never put
the key in command arguments or source control. FMP_API_KEY is an optional
runtime environment override, for example when injected by a secret resolver.

Examples, from this directory:
    python universe_stock_ingest.py --validate-inputs
    python universe_stock_ingest.py --dry-run --limit 5
    python universe_stock_ingest.py --profile siby_baskt

The default input directory is this script's directory. The latest dated file
for each universe is selected, with an undated file as fallback. Russell ETF
proxies retain distinct RUSSELL1000_PROXY / RUSSELL2000_PROXY / RUSSELL3000_PROXY
labels. Each unique symbol is fetched once, even across overlapping universes.

--validate-inputs is entirely offline. --dry-run fetches the secret and calls FMP
but never accesses DynamoDB.
Normal runs check the AWS account and both table schemas, then fetch/validate all
profiles before writing anything. Writes upsert complete profile items and exact
universe/symbol pairs. They do not delete stale memberships or old profiles.
Writes across the two tables are not atomic; interrupted runs can be rerun safely.

The local AWS identity needs secretsmanager:GetSecretValue for the FMP secret,
sts:GetCallerIdentity, dynamodb:DescribeTable, and
dynamodb:BatchWriteItem on the two tables. Provisioning permissions alone do not
include item writes. This script never creates tables or modifies IAM policies.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

LOG = logging.getLogger("universe_stock_ingest")
ACCOUNT_ID = "499133675835"
METADATA_TABLE = "jimmy-dev-stock-metadata"
MEMBERSHIP_TABLE = "jimmy-dev-stock-universe-membership"
FMP_SECRET_ID = "jimmy-dev-fmp-api-key"
UNIVERSE_FILES = {
    "SP500": "sp500",
    "NASDAQ100": "nasdaq100",
    "RUSSELL1000_PROXY": "russell1000_proxy",
    "RUSSELL2000_PROXY": "russell2000_proxy",
    "RUSSELL3000_PROXY": "russell3000_proxy",
}
PROFILE_STRINGS = (
    "companyName",
    "exchange",
    "country",
    "state",
    "city",
    "sector",
    "industry",
    "currency",
)


class IngestionError(RuntimeError):
    """An actionable ingestion failure, safe to report without provider bodies."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # FMP authentication is in the URL. Do not forward it to another host.
        return None


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().replace(".", "-")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*", symbol):
        raise IngestionError("Invalid ticker; expected one stock symbol per line")
    return symbol


def load_universes(directory: Path, selected: list[str]) -> dict[str, set[str]]:
    """Return symbol -> memberships, selecting one snapshot per universe."""
    memberships: dict[str, set[str]] = {}
    for universe in selected:
        prefix = UNIVERSE_FILES[universe]
        candidates = []
        for path in directory.glob(f"{prefix}*.txt"):
            match = re.fullmatch(rf"{re.escape(prefix)}(?:_(\d{{8}}))?\.txt", path.name)
            if not match or not path.is_file():
                continue
            date = match.group(1)
            if date:
                datetime.strptime(date, "%Y%m%d")
            candidates.append((date or "00000000", path))
        if not candidates:
            raise IngestionError(f"No ticker file for {universe} in {directory}")
        path = max(candidates, key=lambda candidate: candidate[0])[1]
        symbols = set()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), 1
        ):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                symbols.add(normalize_symbol(line))
            except IngestionError as exc:
                raise IngestionError(f"{path.name}:{line_number}: {exc}") from None
        if not symbols:
            raise IngestionError(f"Empty ticker file: {path.name}")
        for symbol in symbols:
            memberships.setdefault(symbol, set()).add(universe)
        LOG.info("%s: %s symbols from %s", universe, len(symbols), path.name)
    return memberships


class FMPClient:
    def __init__(self, api_key: str, requests_per_minute: float, attempts: int = 5):
        self.api_key = api_key
        self.interval = 60 / requests_per_minute
        self.attempts = attempts
        self.next_request = 0.0
        self.opener = build_opener(NoRedirects())

    def profile(self, symbol: str) -> dict:
        url = "https://financialmodelingprep.com/stable/profile?" + urlencode(
            {"symbol": symbol, "apikey": self.api_key}
        )
        for attempt in range(self.attempts):
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.interval
            retry_after = 0.0
            try:
                request = Request(url, headers={"Accept": "application/json"})
                with self.opener.open(request, timeout=30) as response:
                    body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise IngestionError(f"FMP response too large for {symbol}")
                break
            except HTTPError as exc:
                status = exc.code
                header = exc.headers.get("Retry-After", "0")
                exc.close()
                if status not in {429, 500, 502, 503, 504}:
                    raise IngestionError(
                        f"FMP HTTP {status} for {symbol}; check API access and subscription"
                    ) from None
                try:
                    retry_after = float(header)
                except ValueError:
                    try:
                        retry_after = (
                            parsedate_to_datetime(header) - datetime.now(timezone.utc)
                        ).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        retry_after = 0.0
                if not math.isfinite(retry_after) or retry_after > 300:
                    raise IngestionError(
                        "FMP requested a long retry delay; retry the run later"
                    )
            except (URLError, TimeoutError, OSError):
                # Never log the exception: it may contain the authenticated URL.
                pass
            if attempt + 1 == self.attempts:
                raise IngestionError(
                    f"FMP request failed after {self.attempts} attempts: {symbol}"
                )
            LOG.warning("Retrying FMP request for %s (attempt %s)", symbol, attempt + 2)
            time.sleep(max(retry_after, min(2**attempt, 30) + random.random()))
        try:
            payload = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise IngestionError(f"FMP returned invalid JSON for {symbol}") from None
        if (
            not isinstance(payload, list)
            or len(payload) != 1
            or not isinstance(payload[0], dict)
        ):
            raise IngestionError(f"FMP did not return exactly one profile for {symbol}")
        return payload[0]


def profile_item(
    profile: dict, symbol: str, universes: set[str], updated_at: str
) -> dict:
    """Keep only the agreed metadata fields; preserve numbers as Decimal."""
    returned_symbol = profile.get("symbol")
    if (
        not isinstance(returned_symbol, str)
        or normalize_symbol(returned_symbol) != symbol
    ):
        raise IngestionError(
            f"FMP profile symbol does not match requested symbol {symbol}"
        )
    item = {"symbol": symbol, "universes": sorted(universes), "updatedAt": updated_at}
    for field in PROFILE_STRINGS:
        value = profile.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise IngestionError(f"Invalid FMP {field} type for {symbol}")
        if value.strip():
            item[field] = value.strip()
    if "companyName" not in item:
        raise IngestionError(f"FMP profile has no companyName for {symbol}")
    value = profile.get("marketCap")
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise IngestionError(f"Invalid FMP marketCap type for {symbol}")
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise IngestionError(f"Invalid FMP marketCap value for {symbol}")
        item["marketCap"] = number
    value = profile.get("isActivelyTrading")
    if value is not None:
        if not isinstance(value, bool):
            raise IngestionError(f"Invalid FMP isActivelyTrading type for {symbol}")
        item["isActivelyTrading"] = value
    return item


def connect_dynamodb(profile: str | None, region: str):
    import boto3
    from botocore.config import Config

    config = Config(
        retries={"mode": "standard", "total_max_attempts": 5},
        connect_timeout=10,
        read_timeout=30,
    )
    session = boto3.Session(profile_name=profile, region_name=region)
    identity = session.client("sts", config=config).get_caller_identity()
    if identity["Account"] != ACCOUNT_ID:
        raise IngestionError(
            f"Expected AWS account {ACCOUNT_ID}; refusing writes to another account"
        )
    dynamodb = session.resource("dynamodb", config=config)
    for name, keys in (
        (METADATA_TABLE, {"symbol": "HASH"}),
        (MEMBERSHIP_TABLE, {"universe": "HASH", "symbol": "RANGE"}),
    ):
        description = dynamodb.meta.client.describe_table(TableName=name)["Table"]
        schema = {
            key["AttributeName"]: key["KeyType"] for key in description["KeySchema"]
        }
        types = {
            field["AttributeName"]: field["AttributeType"]
            for field in description["AttributeDefinitions"]
        }
        if schema != keys or any(types.get(key) != "S" for key in keys):
            raise IngestionError(f"Unexpected key schema on {name}")
        if description["TableStatus"] != "ACTIVE":
            raise IngestionError(f"Table {name} must be ACTIVE before ingestion")
    return dynamodb


def load_fmp_api_key(profile: str | None, region: str, secret_id: str) -> str:
    """Resolve the API key only inside the running ingestion process."""
    import boto3
    from botocore.config import Config

    session = boto3.Session(profile_name=profile, region_name=region)
    config = Config(
        retries={"mode": "standard", "total_max_attempts": 5},
        connect_timeout=10,
        read_timeout=30,
    )
    if (
        session.client("sts", config=config).get_caller_identity()["Account"]
        != ACCOUNT_ID
    ):
        raise IngestionError(
            f"Expected AWS account {ACCOUNT_ID}; refusing secret lookup"
        )
    client = session.client("secretsmanager", config=config)
    response = client.get_secret_value(SecretId=secret_id)
    key = response.get("SecretString")
    if not isinstance(key, str) or not key.strip():
        raise IngestionError(
            "FMP secret must contain a nonempty plain-text SecretString"
        )
    return key.strip()


def write_items(dynamodb, table: str, items: list[dict], attempts: int = 8) -> None:
    """Use native Python types and bounded retries for unprocessed batch writes."""
    for offset in range(0, len(items), 25):
        pending = [
            {"PutRequest": {"Item": item}} for item in items[offset : offset + 25]
        ]
        for attempt in range(attempts):
            response = dynamodb.meta.client.batch_write_item(
                RequestItems={table: pending}
            )
            pending = response.get("UnprocessedItems", {}).get(table, [])
            if not pending:
                break
            if attempt + 1 == attempts:
                raise IngestionError(
                    f"Unprocessed writes remain for {table}; rerun to complete"
                )
            time.sleep(min(2**attempt, 30) + random.random())


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input-dir", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument(
        "--universe",
        action="append",
        choices=list(UNIVERSE_FILES),
        help="Repeat to select universes; defaults to all five",
    )
    parser.add_argument(
        "--profile", help="AWS profile; otherwise use the default credential chain"
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--fmp-secret-id",
        default=FMP_SECRET_ID,
        help="Secrets Manager secret name or ARN (default: jimmy-dev-fmp-api-key)",
    )
    parser.add_argument(
        "--requests-per-minute",
        type=positive_float,
        default=60.0,
        help="FMP request rate; set within your subscription's limits (default: 60)",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="Ingest only the first N unique sorted symbols",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-inputs",
        action="store_true",
        help="Check local files only; no API calls",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch the secret and FMP profiles without accessing DynamoDB",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    writes_started = False
    try:
        memberships = load_universes(
            args.input_dir, list(dict.fromkeys(args.universe or UNIVERSE_FILES))
        )
        if args.limit:
            memberships = {
                symbol: memberships[symbol]
                for symbol in sorted(memberships)[: args.limit]
            }
        pairs = [
            {"universe": universe, "symbol": symbol}
            for symbol in sorted(memberships)
            for universe in sorted(memberships[symbol])
        ]
        LOG.info(
            "Selected %s unique symbols and %s membership pairs",
            len(memberships),
            len(pairs),
        )
        if args.validate_inputs:
            print(
                json.dumps(
                    {
                        "symbols": len(memberships),
                        "membership_pairs": len(pairs),
                        "mode": "inputs-only",
                    }
                )
            )
            return 0
        if not api_key:
            api_key = load_fmp_api_key(args.profile, args.region, args.fmp_secret_id)
        dynamodb = None if args.dry_run else connect_dynamodb(args.profile, args.region)
        client = FMPClient(api_key, args.requests_per_minute)
        profiles = []
        for symbol in sorted(memberships):
            profile = client.profile(symbol)
            updated_at = (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            profiles.append(
                profile_item(profile, symbol, memberships[symbol], updated_at)
            )
            if len(profiles) % 100 == 0:
                LOG.info("Fetched %s/%s profiles", len(profiles), len(memberships))
        if not args.dry_run:
            from boto3.dynamodb.types import TypeSerializer

            serializer = TypeSerializer()
            # Validate every item before either table is modified.
            for item in profiles + pairs:
                serializer.serialize(item)
            writes_started = True
            write_items(dynamodb, METADATA_TABLE, profiles)
            for universe in dict.fromkeys(args.universe or UNIVERSE_FILES):
                universe_pairs = [
                    pair for pair in pairs if pair["universe"] == universe
                ]
                if not universe_pairs:
                    continue
                write_items(dynamodb, MEMBERSHIP_TABLE, universe_pairs)
                LOG.info(
                    "%s done: %s profiles and %s membership pairs written",
                    universe,
                    len(universe_pairs),
                    len(universe_pairs),
                )
        else:
            for universe in dict.fromkeys(args.universe or UNIVERSE_FILES):
                count = sum(pair["universe"] == universe for pair in pairs)
                if count:
                    LOG.info(
                        "%s dry run done: %s profiles validated; no writes",
                        universe,
                        count,
                    )
        print(
            json.dumps(
                {
                    "profiles": len(profiles),
                    "membership_pairs": len(pairs),
                    "mode": "dry-run" if args.dry_run else "written",
                }
            )
        )
        return 0
    except Exception as exc:
        # Keep credential-bearing URLs and provider bodies out of error logs.
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]").replace(
                quote_plus(api_key), "[redacted]"
            )
        LOG.error("%s", message)
        if writes_started:
            LOG.error(
                "Partial writes may have occurred. Rerun after resolving the error; no rows were deleted."
            )
        return 1
    except KeyboardInterrupt:
        LOG.error(
            "Interrupted%s",
            "; partial writes may have occurred"
            if writes_started
            else " before writes",
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
