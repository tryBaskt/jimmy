from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from packages.agents.strategy_agent.tools.yfinance import (
    MarketDataError,
    get_daily_prices_and_market_caps,
)


START = datetime(2024, 1, 2, tzinfo=timezone.utc)
END = datetime(2024, 1, 5, tzinfo=timezone.utc)


def test_daily_prices_and_caps_use_only_previous_share_observations():
    dates = pd.date_range("2024-01-02", periods=3, tz="America/New_York")
    prices = pd.DataFrame(
        {("Close", "AAPL"): [100.0, 110.0, 120.0],
         ("Adj Close", "AAPL"): [90.0, 99.0, 108.0]},
        index=dates,
    )
    shares = pd.Series(
        [10.0, 12.0],
        index=pd.DatetimeIndex([dates[1], dates[2]]),
    )
    ticker = Mock()
    ticker.get_shares_full.return_value = shares
    ticker.get_splits.return_value = pd.Series(dtype="float64")
    with patch("packages.agents.strategy_agent.tools.yfinance.yf.download", return_value=prices), \
         patch("packages.agents.strategy_agent.tools.yfinance.yf.Ticker", return_value=ticker):
        result = get_daily_prices_and_market_caps(["aapl"], START, END)

    assert result.index.names == ["timestamp", "symbol"]
    assert result.index.get_level_values("symbol").tolist() == ["AAPL"] * 3
    assert result["close"].tolist() == [100.0, 110.0, 120.0]
    assert result["raw_close"].tolist() == [100.0, 110.0, 120.0]
    assert result["adjusted_close"].tolist() == [90.0, 99.0, 108.0]
    assert pd.isna(result["market_cap"].iloc[0])
    assert result["market_cap"].iloc[1:].tolist() == [1100.0, 1440.0]


def test_no_shares_leaves_market_cap_missing():
    prices = pd.DataFrame(
        {("Close", "AAPL"): [100.0], ("Adj Close", "AAPL"): [95.0]},
        index=pd.DatetimeIndex(["2024-01-02"], tz="America/New_York"),
    )
    ticker = Mock()
    ticker.get_shares_full.return_value = None
    ticker.get_splits.return_value = pd.Series(dtype="float64")
    with patch("packages.agents.strategy_agent.tools.yfinance.yf.download", return_value=prices), \
         patch("packages.agents.strategy_agent.tools.yfinance.yf.Ticker", return_value=ticker):
        result = get_daily_prices_and_market_caps(["AAPL"], START, END)
    assert pd.isna(result["market_cap"].iloc[0])


def test_rejects_invalid_window_before_network_call():
    with pytest.raises(MarketDataError, match="precede"):
        get_daily_prices_and_market_caps(["AAPL"], END, START)


def test_split_factors_keep_price_and_shares_on_same_basis():
    dates = pd.date_range("2024-01-02", periods=3, tz="America/New_York")
    prices = pd.DataFrame(
        {("Close", "AAPL"): [50.0, 55.0, 60.0],
         ("Adj Close", "AAPL"): [49.0, 54.0, 60.0]},
        index=dates,
    )
    ticker = Mock()
    ticker.get_shares_full.return_value = pd.Series([10.0], index=dates[:1])
    ticker.get_splits.return_value = pd.Series([2.0], index=dates[2:3])
    with patch("packages.agents.strategy_agent.tools.yfinance.yf.download", return_value=prices), \
         patch("packages.agents.strategy_agent.tools.yfinance.yf.Ticker", return_value=ticker):
        result = get_daily_prices_and_market_caps(["AAPL"], START, END)

    assert result["raw_close"].tolist() == [100.0, 110.0, 60.0]
    assert result["shares_outstanding"].tolist() == [10.0, 10.0, 20.0]
    assert result["market_cap"].tolist() == [1000.0, 1100.0, 1200.0]


def test_split_after_requested_window_still_affects_yahoo_close():
    dates = pd.date_range("2024-01-02", periods=2, tz="America/New_York")
    prices = pd.DataFrame(
        {("Close", "AAPL"): [25.0, 30.0],
         ("Adj Close", "AAPL"): [25.0, 30.0]},
        index=dates,
    )
    ticker = Mock()
    ticker.get_shares_full.return_value = pd.Series([10.0], index=dates[:1])
    ticker.get_splits.return_value = pd.Series(
        [4.0], index=pd.DatetimeIndex(["2024-02-01"], tz="America/New_York")
    )
    with patch("packages.agents.strategy_agent.tools.yfinance.yf.download", return_value=prices), \
         patch("packages.agents.strategy_agent.tools.yfinance.yf.Ticker", return_value=ticker):
        result = get_daily_prices_and_market_caps(["AAPL"], START, END)

    assert result["raw_close"].tolist() == [100.0, 120.0]
    assert result["market_cap"].tolist() == [1000.0, 1200.0]
