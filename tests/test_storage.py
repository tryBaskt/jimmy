import concurrent.futures
import pytest
from paperlab.storage import Conflict, emit, digest
from paperlab.catalog import Catalog, AcquisitionPending
from paperlab.connectors import Candidate


def candidate(scope="public:test"):
    return Candidate(
        "test", "test", "Test", {"start": "2020-01-01", "end": "2021-01-01"}, scope, "stock_returns"
    )


def test_transaction_rollback(store):
    store.put("p", "a", {"x": 1}, 0)
    with pytest.raises(Conflict):
        store.batch([("p", "b", {}, 0), ("p", "a", {}, 0)])
    assert store.get("p", "b") == (None, 0)


def test_event_cursor_atomic_and_replay(store):
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: emit(store, "p", "test", str(i)), range(20)))
    rows = store.query("project#p", "event#", limit=100)
    assert [x[1]["sequence"] for x in rows] == list(range(1, 21))
    assert len(store.query("project#p", "event#", after=rows[9][0])) == 10


@pytest.mark.parametrize("conflicts", [3, 12])
def test_event_cursor_contention_backs_off_and_remains_bounded(store, monkeypatch, conflicts):
    from paperlab import storage

    attempts = []
    delays = []
    original_batch = store.batch

    def batch(writes):
        attempts.append(writes)
        if len(attempts) <= conflicts:
            raise Conflict()
        original_batch(writes)

    monkeypatch.setattr(store, "batch", batch)
    monkeypatch.setattr(storage.time, "sleep", delays.append)
    if conflicts < 12:
        assert emit(store, "p", "test", "retry")["sequence"] == 1
        assert len(attempts) == conflicts + 1
        assert len(store.query("project#p", "event#")) == 1
    else:
        with pytest.raises(Conflict, match="Activity stream contention"):
            emit(store, "p", "test", "retry")
        assert len(attempts) == 12
        assert store.query("project#p", "event#") == []
    assert len(delays) == min(conflicts, 11)
    assert all(0.001 <= delay <= 0.05 for delay in delays)


def test_catalog_hit_deduplicates_across_projects(store):
    calls = []

    def fetch(c):
        calls.append(c.id)
        return b"identical", {"frequency": "monthly"}

    first = Catalog(store).acquire(candidate(), fetch)
    second = Catalog(store).acquire(candidate(), fetch)
    assert first["key"] == second["key"]
    assert len(calls) == 1
    assert len(list((store.root / "objects" / "shared-data").rglob("*" + digest(b"identical")))) == 1


def test_scopes_do_not_share_objects(store):
    def f(c):
        return (b"identical", {})

    a = Catalog(store).acquire(candidate("project:a"), f)
    b = Catalog(store).acquire(candidate("project:b"), f)
    assert a["key"] != b["key"]


def test_simultaneous_acquisition_is_single_fetch(store):
    catalog = Catalog(store)

    def fetch(c):
        with pytest.raises(AcquisitionPending):
            catalog.acquire(c, lambda _: (b"bad", {}))
        return b"correct", {}

    result = catalog.acquire(candidate(), fetch)
    assert store.blob_get(result["key"]) == b"correct"


def test_failed_acquisition_releases_lease(store):
    def fail(_):
        raise ValueError("unavailable")

    with pytest.raises(ValueError):
        Catalog(store).acquire(candidate(), fail)
    assert Catalog(store).acquire(candidate(), lambda _: (b"recovered", {}))["checksum"] == digest(
        b"recovered"
    )


def test_identical_refresh_reuses_object_but_keeps_retrieval_history(store):
    def f(c):
        return (b"same", {})

    a = Catalog(store).acquire(candidate(), f, max_age=0)
    b = Catalog(store).acquire(candidate(), f, max_age=0)
    assert a["key"] == b["key"]
    assert len(store.query("catalog#public:test", "retrieval#")) == 2


def test_catalog_superset_reuses_source_with_slice(store):
    broad = candidate()
    broad.request.update(symbols=["A", "B"], adjustment="total")
    original = Catalog(store).acquire(broad, lambda c: (b"broad", {}))
    narrow = candidate()
    narrow.request.update(symbols=["A"], start="2020-06-01", end="2020-12-01", adjustment="total")
    result = Catalog(store).acquire(
        narrow, lambda c: (_ for _ in ()).throw(AssertionError("Redundant fetch"))
    )
    assert result["key"] == original["key"]
    assert result["slice"]["symbols"] == ["A"]
