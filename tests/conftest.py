import pytest
from paperlab.config import Settings
from paperlab.storage import LocalStore
from paperlab.models import Experiment


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path)


@pytest.fixture
def settings(tmp_path):
    return Settings(root=str(tmp_path))


@pytest.fixture
def experiment():
    return Experiment.model_validate(
        {
            "id": "momentum",
            "title": "Momentum",
            "claim": "Winners outperform losers",
            "method": "momentum_sort",
            "formation_months": 1,
            "holding_months": 1,
            "quantiles": 2,
            "requirement": {
                "kind": "returns",
                "description": "Monthly stock returns",
                "symbols": ["A", "B"],
                "start": "2020-02-01",
                "end": "2020-06-30",
            },
            "citations": [{"page": 1, "quote": "winners"}],
        }
    )
