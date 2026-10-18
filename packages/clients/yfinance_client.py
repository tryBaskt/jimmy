"""Download adjusted close prices from Yahoo Finance."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import pandas as pd
import yfinance as yf


class YFinanceClientError(Exception):
    """Raised when a price request or its response is invalid."""


class YFinanceClient:
    """Return UTC-indexed, adjusted close prices in one column per symbol."""

    _intervals = {
        "1min": "1m",
        "1m": "1m",
        "5min": "5m",
        "5m": "5m",
        "1h": "1h",
        "60m": "1h",
        "1d": "1d",
    }

    def get_stock_prices_over_time(
        self,
        symbols: Sequence[str],
        start_datetime: datetime,
        end_datetime: datetime,
        timeframe: str,
    ) -> pd.DataFrame:
        """Fetch adjusted closes in ``[start_datetime, end_datetime)``.

        Missing symbols remain as NaN columns. Callers must decide whether a
        stock with missing prices is eligible for a backtest.
        """
        requested = [symbol.strip().upper() for symbol in symbols]
        if not requested or any(not symbol for symbol in requested):
            raise YFinanceClientError("At least one non-empty symbol is required")
        if len(set(requested)) != len(requested):
            raise YFinanceClientError("Symbols must be unique")
        if start_datetime.tzinfo is None or end_datetime.tzinfo is None:
            raise YFinanceClientError("Start and end datetimes must include timezones")
        start = start_datetime.astimezone(timezone.utc)
        end = end_datetime.astimezone(timezone.utc)
        if start >= end:
            raise YFinanceClientError("Start datetime must precede end datetime")
        interval = self._intervals.get(timeframe.strip().lower())
        if interval is None:
            raise YFinanceClientError(f"Unsupported timeframe: {timeframe}")

        try:
            raw = yf.download(
                requested,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=True,
                group_by="column",
                multi_level_index=True,
            )
            if raw is None or raw.empty:
                result = pd.DataFrame(columns=requested, dtype="float64")
            elif isinstance(raw.columns, pd.MultiIndex):
                if "Close" in raw.columns.get_level_values(0):
                    result = raw.xs("Close", axis=1, level=0)
                elif "Close" in raw.columns.get_level_values(1):
                    result = raw.xs("Close", axis=1, level=1)
                else:
                    raise YFinanceClientError("Yahoo response has no Close prices")
            elif len(requested) == 1 and "Close" in raw.columns:
                result = raw[["Close"]].rename(columns={"Close": requested[0]})
            else:
                raise YFinanceClientError("Yahoo response has unexpected columns")

            if isinstance(result, pd.Series):
                result = result.to_frame(name=requested[0])
            result = result.rename(columns=str.upper).reindex(columns=requested)
            result = result.apply(pd.to_numeric, errors="coerce").astype("float64")
            result.index = pd.to_datetime(result.index, utc=True)
            result = result.loc[(result.index >= start) & (result.index < end)]
            result = result.sort_index()
            result.index.name = "timestamp"
            return result
        except YFinanceClientError:
            raise
        except Exception as error:
            raise YFinanceClientError(
                f"Could not download adjusted close prices for {requested}"
            ) from error
