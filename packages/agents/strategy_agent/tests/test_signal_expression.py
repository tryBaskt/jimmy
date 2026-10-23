import pandas as pd
import pytest

from packages.agents.strategy_agent.signal_expression import (
    SignalExpressionError,
    evaluate_signal,
    validate_signal,
)


@pytest.fixture
def histories():
    dates = pd.bdate_range("2025-01-01", periods=8, tz="UTC")
    close = pd.DataFrame({
        "AAA": [10, 11, 12, 11, 12, 13, 14, 15],
        "BBB": [20, 19, 18, 19, 20, 19, 18, 17],
    }, index=dates)
    spy = pd.Series([100, 101, 102, 101, 103, 104, 105, 106], index=dates)
    return close, spy


def test_model_authored_price_formulas_are_evaluated_per_stock(histories):
    close, spy = histories
    drawdown = evaluate_signal("last(close) / max(close) - 1", close, spy)
    assert drawdown["AAA"] == 0
    assert drawdown["BBB"] == pytest.approx(17 / 20 - 1)
    beta = evaluate_signal(
        "cov(pct_change(close), pct_change(spy)) / var(pct_change(spy))",
        close, spy,
    )
    assert list(beta.index) == ["AAA", "BBB"]
    assert beta.notna().all()


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo unsafe')",
    "close.iloc[-1]",
    "close[-1]",
    "sum(close)",
    "last(spy)",
    "close ** 100",
])
def test_unsafe_or_non_stock_formulas_are_rejected(expression):
    with pytest.raises(SignalExpressionError):
        validate_signal(expression)
