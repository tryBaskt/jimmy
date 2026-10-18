#!/usr/bin/env python3
"""Ingest local stock universes and FMP company profiles into dev DynamoDB.

Requires macOS/Linux, Python 3.10+, and boto3 (``python -m pip install boto3``). The FMP key is
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

--validate-inputs is entirely offline. --dry-run caches any newly fetched profiles
but never accesses DynamoDB. Cached-only runs need no FMP calls or secret lookup.
Normal runs check the AWS account and both table schemas. Profiles are cached
as they arrive, then written in groups of up to 10 symbols with their membership
pairs. Reruns use the same checkpoint and replay pending writes without fetching
completed profiles again. The default rolling 24-hour budget is 200 HTTP attempts.
Comma-separated profile requests are attempted once; unsupported or incomplete
batches fall back to single-symbol calls. The checkpoint remembers this fallback.
Unresolved symbols are skipped in both tables and reported. Writes upsert
complete profile items and universe/symbol pairs without deleting stale data.
Writes across the two tables are not atomic; interrupted runs can be rerun safely.

The local AWS identity needs secretsmanager:GetSecretValue for the FMP secret,
sts:GetCallerIdentity, dynamodb:DescribeTable, and
dynamodb:BatchWriteItem on the two tables. Provisioning permissions alone do not
include item writes. This script never creates tables or modifies IAM policies.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
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


class UnresolvedSymbol(IngestionError):
    """FMP could not identify one usable profile for a requested ticker."""


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


class IngestionPaused(IngestionError):
    """Progress is saved; the request allowance is exhausted or throttled."""


class FMPAccessError(IngestionError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"FMP HTTP {status}; check endpoint access and subscription")


def atomic_json(path: Path, payload: dict) -> None:
    """Replace a private checkpoint atomically; never store credentials."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Checkpoint:
    """Cache validated metadata and acknowledge only fully completed writes."""

    def __init__(self, path: Path, region: str):
        self.path = path
        self.region = region
        self.lock = None
        self.data = {
            "version": 1,
            "account": ACCOUNT_ID,
            "region": region,
            "requests": [],
            "profiles": {},
            "unresolved": {},
            "batch_disabled": False,
        }

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = self.path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (data.get("version"), data.get("account"), data.get("region")) != (
                    1,
                    ACCOUNT_ID,
                    self.region,
                ):
                    raise IngestionError(
                        "Checkpoint version, account, or region mismatch"
                    )
                if (
                    not isinstance(data.get("requests"), list)
                    or any(
                        isinstance(t, bool)
                        or not isinstance(t, (int, float))
                        or not math.isfinite(t)
                        for t in data["requests"]
                    )
                    or not isinstance(data.get("profiles"), dict)
                    or not isinstance(data.get("unresolved"), dict)
                ):
                    raise IngestionError(
                        "Invalid ingestion checkpoint; restore a valid copy"
                    )
                for symbol, record in data["profiles"].items():
                    item = record["item"]
                    if "marketCap" in item:
                        item["marketCap"] = Decimal(item["marketCap"])
                    # Revalidate disk data before allowing any writes.
                    universes = item["universes"]
                    if (
                        not isinstance(universes, list)
                        or not universes
                        or any(u not in UNIVERSE_FILES for u in universes)
                        or not isinstance(item["updatedAt"], str)
                    ):
                        raise IngestionError("Invalid cached profile memberships")
                    record["item"] = profile_item(
                        item, symbol, set(universes), item["updatedAt"]
                    )
                    if not isinstance(record.get("published"), list) or any(
                        u not in universes for u in record["published"]
                    ):
                        raise IngestionError(
                            "Invalid published memberships in checkpoint"
                        )
                self.data = data
            return self
        except BlockingIOError:
            self.lock.close()
            raise IngestionError("Another ingestion is using this checkpoint") from None
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.lock.close()

    def save(self):
        atomic_json(self.path, self.data)

    def reserve_request(self, maximum: int):
        now = time.time()
        self.data["requests"] = [t for t in self.data["requests"] if t > now - 86400]
        if len(self.data["requests"]) >= maximum:
            raise IngestionPaused(
                f"Local rolling 24-hour request budget ({maximum}) reached; rerun later"
            )
        # Reserve before sending, including retries and failed requests.
        self.data["requests"].append(now)
        self.save()

    def remember(self, symbol: str, item: dict):
        self.data["profiles"][symbol] = {"item": item, "published": []}
        self.data["unresolved"].pop(symbol, None)
        self.save()


class FMPClient:
    def __init__(
        self,
        api_key: str,
        requests_per_minute: float,
        checkpoint: Checkpoint,
        max_requests: int = 200,
        batch: bool = True,
        attempts: int = 5,
    ):
        self.api_key = api_key
        self.interval = 60 / requests_per_minute
        self.attempts = attempts
        self.next_request = 0.0
        self.opener = build_opener(NoRedirects())
        self.checkpoint = checkpoint
        self.max_requests = max_requests
        self.batch = batch and not checkpoint.data.get("batch_disabled", False)
        self.requests_this_run = 0

    def request(self, symbols: list[str]) -> list[dict]:
        url = "https://financialmodelingprep.com/stable/profile?" + urlencode(
            {"symbol": ",".join(symbols), "apikey": self.api_key}
        )
        failure = "request failed"
        for attempt in range(self.attempts):
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.checkpoint.reserve_request(self.max_requests)
            self.requests_this_run += 1
            self.next_request = time.monotonic() + self.interval
            try:
                request = Request(url, headers={"Accept": "application/json"})
                with self.opener.open(request, timeout=30) as response:
                    body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise IngestionError("FMP response exceeds the profile size limit")
                break
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status == 429:
                    raise IngestionPaused(
                        "FMP HTTP 429: quota or rate limit reached; progress saved, rerun later"
                    ) from None
                if status not in {500, 502, 503, 504}:
                    raise FMPAccessError(status) from None
                failure = f"HTTP {status}"
            except (URLError, TimeoutError, OSError) as exc:
                # Exceptions can include the authenticated URL. Log only their type.
                failure = type(exc).__name__
            if attempt + 1 == self.attempts:
                raise IngestionError(f"FMP {failure} after {self.attempts} attempts")
            LOG.warning("Retrying FMP after %s (attempt %s)", failure, attempt + 2)
            time.sleep(min(2**attempt, 30) + random.random())
        try:
            payload = json.loads(body, parse_float=Decimal)
        except (ValueError, UnicodeError):
            raise IngestionError("FMP returned invalid JSON") from None
        if not isinstance(payload, list) or any(
            not isinstance(p, dict) for p in payload
        ):
            # Do not print provider error bodies; they may echo the request URL.
            raise IngestionError(
                "FMP returned an invalid profile response or error object"
            )
        return payload

    @staticmethod
    def map_profiles(payload: list[dict], symbols: list[str]) -> dict[str, dict]:
        profiles = {}
        for item in payload:
            raw = item.get("symbol")
            if not isinstance(raw, str):
                raise IngestionError("FMP returned a profile without a symbol")
            symbol = normalize_symbol(raw)
            if symbol not in symbols or symbol in profiles:
                raise IngestionError(
                    "FMP returned unexpected or duplicate profile symbols"
                )
            profiles[symbol] = item
        return profiles

    def disable_batch(self):
        LOG.warning(
            "FMP did not support a complete multi-symbol profile request; "
            "using individual profile calls. Each HTTP attempt counts toward the budget."
        )
        self.batch = False
        self.checkpoint.data["batch_disabled"] = True
        self.checkpoint.save()

    def profiles(self, symbols: list[str]):
        """Yield verified batch results, then resolve missing symbols individually."""
        found = {}
        if self.batch and len(symbols) > 1:
            try:
                found = self.map_profiles(self.request(symbols), symbols)
            except FMPAccessError as exc:
                if exc.status not in {400, 402, 403, 404, 422}:
                    raise
                self.disable_batch()
            else:
                if len(found) != len(symbols):
                    self.disable_batch()
                else:
                    LOG.info(
                        "FMP returned all %s profiles in one request", len(symbols)
                    )
            for symbol, profile in found.items():
                yield symbol, profile
        for symbol in symbols:
            if symbol in found:
                continue
            try:
                single = self.map_profiles(self.request([symbol]), [symbol])
            except FMPAccessError as exc:
                if exc.status != 404:
                    raise
                single = {}
            yield symbol, single.get(symbol)


def profile_item(
    profile: dict, symbol: str, universes: set[str], updated_at: str
) -> dict:
    """Keep only the agreed metadata fields; preserve numbers as Decimal."""
    returned_symbol = profile.get("symbol")
    if (
        not isinstance(returned_symbol, str)
        or normalize_symbol(returned_symbol) != symbol
    ):
        raise UnresolvedSymbol(
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
        raise UnresolvedSymbol(f"FMP profile has no companyName for {symbol}")
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


def publish_cached(
    checkpoint: Checkpoint,
    dynamodb,
    memberships: dict[str, set[str]],
    symbols: list[str],
) -> None:
    """Replay pending writes without another provider call; acknowledge both tables."""
    from boto3.dynamodb.types import TypeSerializer

    profiles = []
    records = []
    for symbol in symbols:
        record = checkpoint.data["profiles"].get(symbol)
        if record is None:
            continue
        item = record["item"]
        desired = set(item["universes"]) | memberships[symbol]
        item["universes"] = sorted(desired)
        if set(record["published"]) != desired:
            profiles.append(item)
            records.append(record)
    if not profiles:
        return
    # Save new membership intent before attempting either table.
    checkpoint.save()
    pairs = [
        {"universe": u, "symbol": item["symbol"]}
        for item in profiles
        for u in item["universes"]
    ]
    serializer = TypeSerializer()
    for item in profiles + pairs:
        serializer.serialize(item)
    write_items(dynamodb, METADATA_TABLE, profiles)
    write_items(dynamodb, MEMBERSHIP_TABLE, pairs)
    for record in records:
        record["published"] = list(record["item"]["universes"])
    checkpoint.save()
    LOG.info("Saved %s profiles and %s membership pairs", len(profiles), len(pairs))


def coverage(
    checkpoint: Checkpoint, memberships: dict[str, set[str]], selected: list[str]
) -> dict:
    result = {}
    for universe in selected:
        symbols = [s for s, universes in memberships.items() if universe in universes]
        uploaded = sum(
            universe in checkpoint.data["profiles"].get(s, {}).get("published", [])
            for s in symbols
        )
        cached = sum(s in checkpoint.data["profiles"] for s in symbols)
        skipped = sum(s in checkpoint.data["unresolved"] for s in symbols)
        pending = len(symbols) - uploaded - skipped
        result[universe] = {
            "total": len(symbols),
            "uploaded": uploaded,
            "cached": cached,
            "skipped": skipped,
            "pending": pending,
        }
        if pending == 0:
            LOG.info("%s done: %s uploaded, %s skipped", universe, uploaded, skipped)
        else:
            LOG.info(
                "%s progress: %s/%s uploaded, %s skipped, %s pending",
                universe,
                uploaded,
                len(symbols),
                skipped,
                pending,
            )
    return result


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
    parser.add_argument("--fmp-secret-id", default=FMP_SECRET_ID)
    parser.add_argument("--requests-per-minute", type=positive_float, default=60.0)
    parser.add_argument(
        "--batch-size",
        type=positive_int,
        default=10,
        help="Symbols per fetch/write group (1 to 10; default: 10)",
    )
    parser.add_argument(
        "--single-profile-requests",
        action="store_true",
        help="Skip the comma-separated profile capability probe",
    )
    parser.add_argument(
        "--max-requests-per-day",
        type=positive_int,
        default=200,
        help="Persistent rolling 24-hour HTTP-attempt budget (default: 200)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(__file__).resolve().parent
        / ".ingestion_state"
        / "checkpoint.json",
    )
    parser.add_argument(
        "--limit", type=positive_int, help="Process first N unique sorted symbols"
    )
    parser.add_argument("--unresolved-report", type=Path)
    parser.add_argument(
        "--retry-unresolved",
        action="store_true",
        help="Retry previously unresolved symbols in this selection",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-inputs", action="store_true")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch/cache profiles without accessing DynamoDB",
    )
    args = parser.parse_args(argv)
    if args.batch_size > 10:
        parser.error("--batch-size must be between 1 and 10")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    try:
        selected = list(dict.fromkeys(args.universe or UNIVERSE_FILES))
        memberships = load_universes(args.input_dir, selected)
        symbols = sorted(memberships)
        if args.limit:
            symbols = symbols[: args.limit]
        LOG.info(
            "Selected %s unique symbols; groups of %s", len(symbols), args.batch_size
        )
        if args.validate_inputs:
            print(
                json.dumps(
                    {
                        "symbols": len(symbols),
                        "membership_pairs": sum(len(memberships[s]) for s in symbols),
                        "mode": "inputs-only",
                    }
                )
            )
            return 0
        report = args.unresolved_report or args.input_dir / "unresolved_symbols.json"
        if report.resolve() == args.checkpoint.resolve():
            raise IngestionError("Report and checkpoint must be different files")
        with Checkpoint(args.checkpoint, args.region) as checkpoint:
            if args.retry_unresolved:
                for symbol in symbols:
                    checkpoint.data["unresolved"].pop(symbol, None)
                checkpoint.save()
            # Resolve the secret lazily: cached-only resume does not call FMP or Secrets Manager.
            client = None
            dynamodb = (
                None if args.dry_run else connect_dynamodb(args.profile, args.region)
            )
            status = "complete"
            exit_code = 0
            try:
                for offset in range(0, len(symbols), args.batch_size):
                    group = symbols[offset : offset + args.batch_size]
                    # Repair a prior partial write before spending more provider calls.
                    if not args.dry_run:
                        publish_cached(checkpoint, dynamodb, memberships, group)
                    pending = [
                        s
                        for s in group
                        if s not in checkpoint.data["profiles"]
                        and s not in checkpoint.data["unresolved"]
                    ]
                    if not pending:
                        continue
                    if client is None:
                        if not api_key:
                            api_key = load_fmp_api_key(
                                args.profile, args.region, args.fmp_secret_id
                            )
                        client = FMPClient(
                            api_key,
                            args.requests_per_minute,
                            checkpoint,
                            args.max_requests_per_day,
                            batch=not args.single_profile_requests,
                        )
                    try:
                        for symbol, profile in client.profiles(pending):
                            try:
                                if profile is None:
                                    raise UnresolvedSymbol(
                                        f"FMP has no profile for {symbol}"
                                    )
                                item = profile_item(
                                    profile,
                                    symbol,
                                    memberships[symbol],
                                    datetime.now(timezone.utc)
                                    .isoformat(timespec="seconds")
                                    .replace("+00:00", "Z"),
                                )
                            except UnresolvedSymbol as exc:
                                checkpoint.data["unresolved"][symbol] = str(exc)
                                checkpoint.save()
                                LOG.warning(
                                    "Skipping unresolved symbol %s: %s", symbol, exc
                                )
                            else:
                                checkpoint.remember(symbol, item)
                    finally:
                        # A 429, interrupt, or a later bad symbol cannot discard earlier successes.
                        if not args.dry_run:
                            publish_cached(checkpoint, dynamodb, memberships, group)
                    LOG.info(
                        "Processed %s/%s selected symbols",
                        min(offset + len(group), len(symbols)),
                        len(symbols),
                    )
            except IngestionPaused as exc:
                LOG.warning("%s", exc)
                status, exit_code = "paused", 2
            except BaseException:
                status = "failed"
                raise
            finally:
                skipped = [
                    {
                        "symbol": symbol,
                        "universes": sorted(memberships[symbol]),
                        "reason": reason,
                    }
                    for symbol, reason in sorted(checkpoint.data["unresolved"].items())
                    if symbol in memberships
                ]
                atomic_json(
                    report,
                    {
                        "updatedAt": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "universes": selected,
                        "skipped": skipped,
                    },
                )
                print(
                    json.dumps(
                        {
                            "status": status,
                            "selected_symbols": len(symbols),
                            "mode": "dry-run" if args.dry_run else "written",
                            "requests_this_run": client.requests_this_run
                            if client
                            else 0,
                            "coverage": coverage(checkpoint, memberships, selected),
                            "checkpoint": str(args.checkpoint),
                            "unresolved_report": str(report),
                        }
                    )
                )
            return exit_code
    except KeyboardInterrupt:
        LOG.error("Interrupted; validated profiles are checkpointed. Rerun to resume.")
        return 130
    except Exception as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]").replace(
                quote_plus(api_key), "[redacted]"
            )
        LOG.error("%s", message)
        LOG.error(
            "Progress is retained; partial writes can be replayed by rerunning with the same checkpoint."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
