import copy
import json
from decimal import Decimal

import pytest

from batch_scripts.historical_stock_ingestion.publish import (
    decode, encode, validate_plan, verify,
)


def plan():
    return {
        "stock_metadata": [{"security_id": "security-1"}],
        "stock_universe_membership": [{"universe": "SP500", "security_id": "security-1",
                                       "membership_key": "security-1#2000-01-01"}],
        "stock_gics_history": [],
    }


def test_unknown_identity_rejected_before_writing():
    data = plan()
    data["stock_universe_membership"][0]["security_id"] = "missing"
    with pytest.raises(ValueError, match="Unknown security"):
        validate_plan(data)


def test_duplicate_membership_rejected():
    data = plan()
    data["stock_universe_membership"] *= 2
    with pytest.raises(ValueError, match="duplicate key"):
        validate_plan(data)


def test_provider_taxonomy_cannot_be_published_as_gics():
    data = plan()
    data["stock_gics_history"] = [{"security_id": "security-1", "classification_key": "today",
                                    "taxonomy": "provider", "sector": "Technology"}]
    with pytest.raises(ValueError, match="explicit GICS"):
        validate_plan(data)


def test_undated_gics_rejected():
    data = plan()
    data["stock_gics_history"] = [{"security_id": "security-1", "classification_key": "today",
                                    "taxonomy": "GICS", "sector": "Information Technology"}]
    with pytest.raises(ValueError, match="effective or observed date"):
        validate_plan(data)


def test_decimal_backup_roundtrip():
    original = {"marketCap": Decimal("123456789.123")}
    assert json.loads(json.dumps(original, default=encode), object_hook=decode) == original


def test_verify_checks_contents_and_unexpected_rows(monkeypatch):
    import batch_scripts.historical_stock_ingestion.publish as module
    data = plan()
    monkeypatch.setattr(module, "check_schemas", lambda db: None)
    records = {name: copy.deepcopy(data[logical]) for logical, (name, _) in module.TABLES.items()}
    monkeypatch.setattr(module, "read_items", lambda db, name: records[name])
    verify(None, data)
    records["jimmy-dev-stock-metadata"].append({"security_id": "unexpected"})
    with pytest.raises(ValueError, match="Read-back mismatch"):
        verify(None, data)
