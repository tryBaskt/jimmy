"""Yahoo adjusted-price retrieval. Historical membership is never inferred."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import re
import pandas as pd
from paperlab.connectors import Candidate
from paperlab.storage import digest
from paperlab.connectors.prices import monthly_returns, encode_frames


class YahooConnector:
    name = "yfinance"

    def __init__(self, sharing_scope="provider:yfinance"):
        self.scope = sharing_scope

    def discover(self, requirement, scope):
        if requirement.kind not in {"prices", "returns"} or not requirement.symbols:
            return []
        symbols = sorted(set(requirement.symbols))
        if any(not re.fullmatch(r"[A-Za-z0-9.^=_-]{1,24}", s) for s in symbols):
            raise ValueError("Invalid price identifier")
        request = {
            "symbols": symbols,
            "start": str(requirement.start),
            "end": str(requirement.end),
            "frequency": "monthly",
            "adjustment": "yahoo_adj_close_v1",
            "version": 1,
        }
        return [
            Candidate(
                id=digest(request),
                connector=self.name,
                title="Yahoo adjusted-price history",
                request=request,
                scope=self.scope,
                kind="stock_returns",
                caveats=[
                    "Requested symbols do not establish historical universe membership.",
                    "Delisted securities and point-in-time adjustment vintages are not guaranteed.",
                    "Monthly entry only; an exact one-week entry delay is unsupported.",
                ],
            )
        ]

    def fetch(self, candidate):
        import yfinance as yf

        request = candidate.request
        start = pd.Timestamp(request["start"]) - pd.DateOffset(months=84)
        end = pd.Timestamp(request["end"]) + timedelta(days=1)

        def one(symbol):
            try:
                prices = yf.Ticker(symbol).history(
                    start=start.date(), end=end.date(), interval="1d", auto_adjust=False,
                    actions=True, repair=False, raise_errors=True, timeout=15,
                )
                if prices.empty or "Adj Close" not in prices:
                    return None, {"symbol": symbol, "code": "no_adjusted_prices"}
                index = prices.index.tz_localize(None) if prices.index.tz is not None else prices.index
                series = pd.Series(prices["Adj Close"].to_numpy(), index=index)
                return monthly_returns(series, symbol), None
            except Exception as error:
                # Provider error bodies are not returned to the planner or activity stream.
                return None, {"symbol": symbol, "code": type(error).__name__}

        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(one, request["symbols"]))
        body, data = encode_frames([frame for frame, error in outcomes if frame is not None])
        return body, {
            "source": "https://finance.yahoo.com/", "provider": self.name,
            "frequency": "monthly", "units": "decimal_total_return_proxy",
            "return_basis": "total_return", "rows": len(data),
            "start": data.date.min(), "end": data.date.max(),
            "requested_symbols": request["symbols"],
            "returned_symbols": sorted(data.security_id.unique()),
            "failures": [error for _, error in outcomes if error],
            "adjustment": request["adjustment"], "availability": "historical_vintage_unknown",
        }
