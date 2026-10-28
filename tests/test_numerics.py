import numpy as np
import pandas as pd
import pytest
from paperlab.numerics import execute, validate_returns, compare
from paperlab.models import Expected


def prices():
    rows = []
    for i, date in enumerate(pd.date_range("2020-01-31", periods=8, freq="ME")):
        rows.extend(
            [
                {"date": str(date.date()), "security_id": "A", "return": 0.04 + i * 0.001},
                {"date": str(date.date()), "security_id": "B", "return": -0.01 + i * 0.0002},
            ]
        )
    return pd.DataFrame(rows)


def test_rank_before_return_and_equal_weight(experiment):
    frame = prices()
    result = execute(frame.to_csv(index=False).encode(), experiment)
    expected = [0.05 + i * 0.0008 for i in range(1, 6)]
    assert [x["return"] for x in result["series"]] == pytest.approx(expected)
    assert result["metrics"]["mean_monthly_return"] == pytest.approx(np.mean(expected))
    assert result["holdings"][0]["winners"] == ["A"]


def test_no_future_leakage(experiment):
    frame = prices()
    first = execute(frame.to_csv(index=False).encode(), experiment)
    frame.loc[frame.date > "2020-04-30", "return"] *= 2
    second = execute(frame.to_csv(index=False).encode(), experiment)
    assert first["series"][:3] == second["series"][:3]


def test_missing_held_stock_does_not_redistribute(experiment):
    frame = prices()
    frame = frame[~((frame.date == "2020-03-31") & (frame.security_id == "A"))]
    with pytest.raises(ValueError, match="Held-security"):
        execute(frame.to_csv(index=False).encode(), experiment)


def test_duplicate_month_and_invalid_returns_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        validate_returns(b"date,security_id,return\n2020-01-01,A,.1\n2020-01-31,A,.2\n")
    with pytest.raises(ValueError):
        validate_returns(b"date,security_id,return\n2020-01-31,A,-2\n")


def test_modified_experiment_not_declared_reproduced(experiment):
    experiment.expected = [
        Expected(
            metric="mean_monthly_return", value=0.1, tolerance=0.001, citation={"page": 1, "quote": "winners"}
        )
    ]
    result = {"metrics": {"mean_monthly_return": 0.1}}
    assert compare(result, experiment, False)[0]["outcome"] == "not_comparable"
    assert compare(result, experiment, True)[0]["outcome"] == "within_tolerance"


def test_overlapping_cohorts_and_hac(experiment):
    experiment.holding_months = 2
    result = execute(prices().to_csv(index=False).encode(), experiment)
    assert result["metrics"]["hac_lags"] == 1
    assert result["series"][0]["date"] == "2020-03-31"
    assert result["metrics"]["months"] == 4
