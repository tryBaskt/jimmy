"""Yahoo Finance price and estimated market-cap tools."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import logging
from math import isfinite
from typing import Sequence

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """Raised when a market-data request or response is invalid."""


def get_adjusted_close_prices(
    symbols: Sequence[str], start_datetime: datetime, end_datetime: datetime
) -> pd.DataFrame:
    """Download daily split/dividend-adjusted closes, retaining missing symbols."""
    requested = list(dict.fromkeys(symbol.upper() for symbol in symbols))
    if not requested:
        raise MarketDataError("At least one symbol is required")
    chunks = []
    batch_count = (len(requested) + 19) // 20
    for offset in range(0, len(requested), 20):
        batch = requested[offset:offset + 20]
        batch_number = offset // 20 + 1
        logger.info("Adjusted-price batch %s/%s started symbols=%s",
                    batch_number, batch_count, len(batch))
        try:
            raw = yf.download(
                batch, start=start_datetime, end=end_datetime, interval="1d",
                auto_adjust=True, ignore_tz=False, progress=False, threads=True,
                group_by="column", multi_level_index=True,
            )
        except Exception as error:
            raise MarketDataError(f"Could not download prices for {batch}") from error
        if raw is None or raw.empty:
            logger.warning("Adjusted-price batch %s/%s returned no rows",
                           batch_number, batch_count)
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            if "Close" in raw.columns.get_level_values(0):
                close = raw.xs("Close", axis=1, level=0)
            elif "Close" in raw.columns.get_level_values(1):
                close = raw.xs("Close", axis=1, level=1)
            else:
                raise MarketDataError("Yahoo returned no adjusted close prices")
        elif len(batch) == 1 and "Close" in raw:
            close = raw[["Close"]].rename(columns={"Close": batch[0]})
        else:
            raise MarketDataError("Yahoo returned unexpected price columns")
        close = close.rename(columns=str.upper).reindex(columns=batch)
        close.index = pd.to_datetime(close.index, utc=True)
        chunks.append(close)
        logger.info("Adjusted-price batch %s/%s complete rows=%s",
                    batch_number, batch_count, len(close))
    if not chunks:
        raise MarketDataError("Yahoo returned no daily prices")
    result = pd.concat(chunks, axis=1).sort_index().reindex(columns=requested)
    result = result.apply(pd.to_numeric, errors="coerce")
    result = result.loc[(result.index >= start_datetime) & (result.index < end_datetime)]
    result.index.name = "timestamp"
    logger.info("Adjusted-price download complete symbols=%s trading_days=%s",
                len(requested), len(result))
    return result


def get_daily_prices_and_market_caps(
    symbols: Sequence[str], start_datetime: datetime, end_datetime: datetime
) -> pd.DataFrame:
    """Fetch daily prices and estimated historical market caps in ``[start, end)``.

    Return a DataFrame indexed by UTC ``timestamp`` and ``symbol`` with
    ``close`` (Yahoo's split-adjusted close), ``adjusted_close`` (also dividend
    adjusted), reconstructed ``raw_close``, ``shares_outstanding``, and
    ``market_cap``. Market cap uses raw_close times the latest historical
    share count, carried forward through any subsequent stock splits. Yahoo
    may lack share observations; those market caps remain NaN. Yahoo does not
    provide the publication times of these share counts here, so the result
    is an estimate and is not certified point-in-time data for backtesting.
    Symbols without prices are omitted from the result.
    """
    if isinstance(symbols, str):
        raise MarketDataError("Symbols must be a sequence, not a string")
    requested = [symbol.strip().upper() for symbol in symbols]
    if not requested or any(not symbol for symbol in requested):
        raise MarketDataError("At least one non-empty symbol is required")
    if len(set(requested)) != len(requested):
        raise MarketDataError("Symbols must be unique")
    if start_datetime.tzinfo is None or end_datetime.tzinfo is None:
        raise MarketDataError("Start and end datetimes must include timezones")
    start = start_datetime.astimezone(timezone.utc)
    end = end_datetime.astimezone(timezone.utc)
    if start >= end:
        raise MarketDataError("Start datetime must precede end datetime")

    logger.info("Market-cap price download started symbols=%s", len(requested))
    try:
        prices = yf.download(
            requested,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=False,
            ignore_tz=False,
            progress=False,
            threads=True,
            group_by="column",
            multi_level_index=True,
        )
    except Exception as error:
        raise MarketDataError("Could not download daily prices") from error
    if prices is None or prices.empty:
        raise MarketDataError("Yahoo returned no daily prices")
    logger.info("Market-cap price download complete rows=%s; loading share histories",
                len(prices))

    results = []
    for number, symbol in enumerate(requested, 1):
        if isinstance(prices.columns, pd.MultiIndex):
            if symbol in prices.columns.get_level_values(1):
                stock = prices.xs(symbol, axis=1, level=1)
            elif symbol in prices.columns.get_level_values(0):
                stock = prices.xs(symbol, axis=1, level=0)
            else:
                continue
        elif len(requested) == 1:
            stock = prices
        else:
            raise MarketDataError("Yahoo returned unexpected price columns")
        if "Close" not in stock or "Adj Close" not in stock:
            raise MarketDataError(f"Yahoo returned incomplete prices for {symbol}")

        stock = stock[["Close", "Adj Close"]].rename(
            columns={"Close": "close", "Adj Close": "adjusted_close"}
        ).apply(pd.to_numeric, errors="coerce")
        stock = stock.dropna(subset=["close"]).copy()
        if stock.empty:
            continue
        local_dates = pd.to_datetime(stock.index)
        utc_dates = pd.to_datetime(stock.index, utc=True)
        in_range = (utc_dates >= start) & (utc_dates < end)
        stock = stock.loc[in_range].copy()
        local_dates = local_dates[in_range]
        utc_dates = utc_dates[in_range]
        if stock.empty:
            continue

        try:
            # Fetch earlier observations so the first requested day can use
            # the last share count known before the window began.
            ticker = yf.Ticker(symbol)
            shares = ticker.get_shares_full(
                start=start - timedelta(days=730), end=end
            )
            # Yahoo's Close is generally adjusted for *all* later splits,
            # including splits after the requested end date.
            splits = ticker.get_splits(period="max")
        except Exception as error:
            raise MarketDataError(f"Could not download share history for {symbol}") from error

        if splits is None:
            raise MarketDataError(f"Yahoo returned no split history for {symbol}")
        split_dates = []
        split_factors = [1.0]
        if not splits.empty:
            splits = pd.to_numeric(splits, errors="coerce").sort_index()
            split_index = pd.to_datetime(splits.index)
            if local_dates.tz is not None:
                if split_index.tz is None:
                    split_index = split_index.tz_localize(local_dates.tz)
                else:
                    split_index = split_index.tz_convert(local_dates.tz)
            for day, ratio in zip(split_index.date, splits.to_numpy()):
                if not isfinite(ratio) or ratio <= 0:
                    raise MarketDataError(f"Yahoo returned an invalid split for {symbol}")
                split_dates.append(day)
                split_factors.append(split_factors[-1] * ratio)

        price_dates = local_dates.date
        price_split_positions = [bisect_right(split_dates, day) for day in price_dates]
        # Undo Yahoo's later split adjustments to recover the price as traded.
        stock["raw_close"] = stock["close"].to_numpy() * [
            split_factors[-1] / split_factors[position]
            for position in price_split_positions
        ]

        stock["shares_outstanding"] = float("nan")
        if shares is not None and not shares.empty:
            shares = pd.to_numeric(shares, errors="coerce")
            shares = shares[shares > 0].sort_index()
            if not shares.empty:
                share_dates = pd.to_datetime(shares.index)
                if local_dates.tz is not None:
                    if share_dates.tz is None:
                        share_dates = share_dates.tz_localize(local_dates.tz)
                    else:
                        share_dates = share_dates.tz_convert(local_dates.tz)
                dated_shares = pd.Series(shares.to_numpy(), index=share_dates.date)
                dated_shares = dated_shares[~dated_shares.index.duplicated(keep="last")]
                # Forward fill only: never use a future share count for a past day.
                observed = pd.DataFrame(
                    {"count": dated_shares, "date": dated_shares.index},
                    index=dated_shares.index,
                ).reindex(price_dates, method="ffill")
                stock["shares_outstanding"] = [
                    count * split_factors[price_position]
                    / split_factors[bisect_right(split_dates, observed_date)]
                    if pd.notna(count) else float("nan")
                    for count, observed_date, price_position in zip(
                        observed["count"], observed["date"], price_split_positions
                    )
                ]

        stock["market_cap"] = stock["raw_close"] * stock["shares_outstanding"]
        stock.index = utc_dates
        stock.index.name = "timestamp"
        stock["symbol"] = symbol
        results.append(stock.reset_index().set_index(["timestamp", "symbol"]))
        logger.info("Market-cap share history %s/%s complete has_shares=%s",
                    number, len(requested), bool(stock["shares_outstanding"].notna().any()))

    if not results:
        raise MarketDataError("Yahoo returned no prices for the requested symbols")
    result = pd.concat(results).sort_index()
    logger.info("Market-cap download complete symbols=%s rows=%s", len(results), len(result))
    return result
