"""Normalized Yahoo Finance adjusted closing prices."""

import numpy as np
import pandas as pd
import yfinance as yf


class YFinanceClient:
    def get_stock_prices_over_time(self, symbols, start_datetime, end_datetime, timeframe="1d"):
        aliases = {"1D": "1d", "1H": "1h", "1Min": "1m", "5Min": "5m"}
        interval = aliases.get(timeframe, timeframe.lower())
        if interval not in {"1d", "1h", "1m", "5m"}:
            raise ValueError("Unsupported Yahoo interval")
        if start_datetime >= end_datetime or not symbols:
            raise ValueError("Supply symbols and a nonempty date range")
        raw = yf.download(
            symbols, start=start_datetime, end=end_datetime, interval=interval,
            auto_adjust=True, progress=False, threads=False, group_by="column", timeout=30,
        )
        if raw is None or raw.empty:
            raise ValueError("Yahoo returned no prices for this request")
        if isinstance(raw.columns, pd.MultiIndex):
            level = next((i for i in range(raw.columns.nlevels)
                          if "Close" in raw.columns.get_level_values(i)), None)
            if level is None:
                raise ValueError("Yahoo returned no Close field")
            close = raw.xs("Close", axis=1, level=level)
        else:
            close = raw[["Close"]].rename(columns={"Close": symbols[0]})
        close.columns = close.columns.astype(str)
        close = close.reindex(columns=symbols).apply(pd.to_numeric, errors="coerce")
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        close = close.loc[~close.index.duplicated()].sort_index()
        close = close.where(np.isfinite(close) & (close > 0))
        close.index.name = "date"
        return close
