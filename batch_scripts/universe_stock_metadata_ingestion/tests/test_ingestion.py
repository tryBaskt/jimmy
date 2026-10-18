"""Offline coverage for quotas, provider batching, and interrupted database writes."""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "universe_stock_ingest.py"
spec = importlib.util.spec_from_file_location("universe_stock_ingest", SCRIPT)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


def profile(symbol):
    return {"symbol": symbol, "companyName": f"Company {symbol}", "marketCap": 123.45}


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return json.dumps(self.payload).encode()[:size]


class Provider:
    def __init__(self):
        self.calls = []
        self.batch_status = None
        self.partial_batch = False
        self.empty = set()
        self.next_status = None

    def open(self, request, timeout):
        symbols = parse_qs(urlsplit(request.full_url).query)["symbol"][0].split(",")
        self.calls.append(symbols)
        status = self.next_status
        self.next_status = None
        if len(symbols) > 1 and self.batch_status:
            status = self.batch_status
        if status:
            raise HTTPError(request.full_url, status, "provider failure", {}, None)
        if self.partial_batch:
            symbols = symbols[:1]
        return Response([profile(s) for s in symbols if s not in self.empty])


@pytest.fixture
def setup(monkeypatch, tmp_path):
    provider = Provider()
    monkeypatch.setattr(ingest, "build_opener", lambda *args: provider)
    monkeypatch.setattr(ingest.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("FMP_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(ingest, "connect_dynamodb", lambda *args: object())
    writes = []
    monkeypatch.setattr(
        ingest, "write_items", lambda db, table, items: writes.append((table, items))
    )
    clock = [1_800_000_000.0]
    monkeypatch.setattr(ingest.time, "time", lambda: clock[0])
    args = [
        "--input-dir",
        str(tmp_path),
        "--universe",
        "SP500",
        "--checkpoint",
        str(tmp_path / "state" / "checkpoint.json"),
    ]
    return provider, writes, clock, args


def input_symbols(tmp_path, count):
    symbols = [f"S{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(count)]
    (tmp_path / "sp500.txt").write_text("\n".join(symbols))
    return symbols


def read_state(tmp_path):
    return json.loads((tmp_path / "state" / "checkpoint.json").read_text())


def test_batches_of_ten_and_cached_resume(setup, tmp_path):
    provider, writes, _, args = setup
    input_symbols(tmp_path, 23)
    assert ingest.main(args) == 0
    assert [len(call) for call in provider.calls] == [10, 10, 3]
    assert (
        sum(len(items) for table, items in writes if table == ingest.METADATA_TABLE)
        == 23
    )
    assert all(
        record["published"] == ["SP500"]
        for record in read_state(tmp_path)["profiles"].values()
    )
    # No provider calls, no secret lookup, and no redundant database writes on resume.
    writes.clear()
    assert ingest.main(args) == 0
    assert len(provider.calls) == 3
    assert writes == []


def test_budget_preserves_twenty_uploads_and_resumes_next_day(setup, tmp_path):
    provider, writes, clock, args = setup
    input_symbols(tmp_path, 25)
    args += ["--max-requests-per-day", "2"]
    assert ingest.main(args) == 2
    assert len(provider.calls) == 2
    assert len(read_state(tmp_path)["profiles"]) == 20
    assert len(writes) == 4
    assert ingest.main(args) == 2
    assert len(provider.calls) == 2
    clock[0] += 86401
    assert ingest.main(args) == 0
    assert len(provider.calls) == 3
    assert len(read_state(tmp_path)["profiles"]) == 25


@pytest.mark.parametrize("status", [400, 402, 403, 404, 422])
def test_unsupported_batch_falls_back_and_remembers(setup, tmp_path, status):
    provider, _, _, args = setup
    input_symbols(tmp_path, 12)
    provider.batch_status = status
    assert ingest.main(args) == 0
    assert len(provider.calls[0]) == 10
    assert all(len(call) == 1 for call in provider.calls[1:])
    assert len(provider.calls) == 13  # Failed probe also consumes one request.
    assert read_state(tmp_path)["batch_disabled"] is True


def test_partial_batch_is_not_treated_as_unresolved(setup, tmp_path):
    provider, _, _, args = setup
    input_symbols(tmp_path, 10)
    provider.partial_batch = True
    assert ingest.main(args) == 0
    assert len(provider.calls) == 10  # First result reused, nine individual requests.
    assert len(read_state(tmp_path)["profiles"]) == 10
    assert read_state(tmp_path)["unresolved"] == {}


def test_429_stops_without_retrying_and_keeps_prior_uploads(
    setup, tmp_path, monkeypatch
):
    provider, _, _, args = setup
    input_symbols(tmp_path, 20)
    original = provider.open

    def throttle_second(request, timeout):
        if len(provider.calls) == 1:
            provider.next_status = 429
        return original(request, timeout)

    monkeypatch.setattr(provider, "open", throttle_second)
    assert ingest.main(args) == 2
    assert len(provider.calls) == 2
    assert len(read_state(tmp_path)["profiles"]) == 10
    assert len(read_state(tmp_path)["requests"]) == 2


def test_partial_write_replays_cached_profiles_without_refetch(
    setup, tmp_path, monkeypatch
):
    provider, writes, _, args = setup
    input_symbols(tmp_path, 10)

    def fail_memberships(db, table, items):
        if table == ingest.MEMBERSHIP_TABLE:
            raise RuntimeError("simulated interrupted membership write")
        writes.append((table, items))

    monkeypatch.setattr(ingest, "write_items", fail_memberships)
    assert ingest.main(args) == 1
    assert len(read_state(tmp_path)["profiles"]) == 10
    assert all(not r["published"] for r in read_state(tmp_path)["profiles"].values())
    monkeypatch.setattr(
        ingest, "write_items", lambda db, table, items: writes.append((table, items))
    )
    assert ingest.main(args) == 0
    assert len(provider.calls) == 1
    assert all(
        r["published"] == ["SP500"] for r in read_state(tmp_path)["profiles"].values()
    )
    assert isinstance(writes[-2][1][0]["marketCap"], Decimal)


def test_dry_run_cache_can_be_published_without_more_fmp_calls(
    setup, tmp_path, monkeypatch
):
    provider, writes, _, args = setup
    input_symbols(tmp_path, 10)
    monkeypatch.setattr(
        ingest, "connect_dynamodb", lambda *args: pytest.fail("dry run touched AWS")
    )
    assert ingest.main(args + ["--dry-run"]) == 0
    assert writes == []
    assert all(not r["published"] for r in read_state(tmp_path)["profiles"].values())
    monkeypatch.setattr(ingest, "connect_dynamodb", lambda *args: object())
    assert ingest.main(args) == 0
    assert len(provider.calls) == 1
    assert len(writes) == 2


def test_unresolved_missing_from_both_writes_and_reported(setup, tmp_path):
    provider, writes, _, args = setup
    symbols = input_symbols(tmp_path, 10)
    provider.empty.add(symbols[2])
    assert ingest.main(args) == 0
    for _, items in writes:
        assert symbols[2] not in [item["symbol"] for item in items]
    report = json.loads((tmp_path / "unresolved_symbols.json").read_text())
    assert [row["symbol"] for row in report["skipped"]] == [symbols[2]]


def test_retry_attempts_use_persistent_budget(setup, tmp_path):
    provider, _, _, args = setup
    input_symbols(tmp_path, 10)
    provider.next_status = 503
    assert ingest.main(args + ["--max-requests-per-day", "1"]) == 2
    assert len(provider.calls) == 1
    assert len(read_state(tmp_path)["requests"]) == 1


def test_same_symbol_new_universe_uses_cache_and_preserves_old_membership(
    setup, tmp_path
):
    provider, writes, _, args = setup
    symbols = input_symbols(tmp_path, 1)
    assert ingest.main(args) == 0
    (tmp_path / "nasdaq100.txt").write_text(symbols[0])
    args[args.index("SP500")] = "NASDAQ100"
    assert ingest.main(args) == 0
    assert len(provider.calls) == 1
    assert writes[-2][1][0]["universes"] == ["NASDAQ100", "SP500"]
    assert len(writes[-1][1]) == 2


def test_checkpoint_lock_prevents_concurrent_quota_and_write_races(tmp_path):
    path = tmp_path / "checkpoint.json"
    with ingest.Checkpoint(path, "us-east-1"):
        with pytest.raises(ingest.IngestionError, match="Another ingestion"):
            with ingest.Checkpoint(path, "us-east-1"):
                pass


def test_wrong_account_checkpoint_refused(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({"version": 1, "account": "other", "region": "us-east-1"})
    )
    with pytest.raises(ingest.IngestionError, match="mismatch"):
        with ingest.Checkpoint(path, "us-east-1"):
            pass


def test_retry_unresolved_only_when_requested(setup, tmp_path):
    provider, _, _, args = setup
    symbols = input_symbols(tmp_path, 1)
    provider.empty.add(symbols[0])
    assert ingest.main(args) == 0
    provider.empty.clear()
    assert ingest.main(args) == 0
    assert len(provider.calls) == 1
    assert ingest.main(args + ["--retry-unresolved"]) == 0
    assert len(provider.calls) == 2
    assert read_state(tmp_path)["unresolved"] == {}


def test_unexpected_batch_symbol_stops_without_upload(setup, tmp_path, monkeypatch):
    provider, writes, _, args = setup
    input_symbols(tmp_path, 10)
    monkeypatch.setattr(
        provider, "open", lambda request, timeout: Response([profile("WRONG")])
    )
    assert ingest.main(args) == 1
    assert writes == []


def test_limit_does_not_claim_full_universe_done(setup, tmp_path, capsys):
    _, _, _, args = setup
    input_symbols(tmp_path, 20)
    assert ingest.main(args + ["--limit", "10"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["coverage"]["SP500"] == {
        "total": 20,
        "uploaded": 10,
        "cached": 10,
        "skipped": 0,
        "pending": 10,
    }


def test_budget_stop_mid_fallback_publishes_every_valid_profile(setup, tmp_path):
    provider, writes, _, args = setup
    input_symbols(tmp_path, 10)
    provider.batch_status = 403
    assert ingest.main(args + ["--max-requests-per-day", "3"]) == 2
    # One rejected probe and two single calls; the remaining eight stay pending.
    assert len(provider.calls) == 3
    state = read_state(tmp_path)
    assert len(state["profiles"]) == 2
    assert all(r["published"] == ["SP500"] for r in state["profiles"].values())
    assert (
        sum(len(items) for table, items in writes if table == ingest.METADATA_TABLE)
        == 2
    )
