"""Yahoo adjusted-price retrieval. Historical membership is never inferred."""

from datetime import timedelta
import re
import pandas as pd
from paperlab.connectors import Candidate
from paperlab.storage import digest


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

        frames = []
        request = candidate.request
        start = pd.Timestamp(request["start"]) - pd.DateOffset(months=84)
        end = pd.Timestamp(request["end"]) + timedelta(days=1)
        for symbol in request["symbols"]:
            prices = yf.Ticker(symbol).history(
                start=start.date(),
                end=end.date(),
                interval="1d",
                auto_adjust=False,
                actions=True,
                repair=False,
                raise_errors=True,
            )
            if prices.empty or "Adj Close" not in prices:
                continue
            index = prices.index.tz_localize(None) if prices.index.tz is not None else prices.index
            series = pd.Series(prices["Adj Close"].to_numpy(), index=index).resample("ME").last()
            # No forward filling across missing months.
            returns = series.pct_change(fill_method=None)
            frames.append(
                pd.DataFrame(
                    {
                        "date": returns.index.strftime("%Y-%m-%d"),
                        "security_id": symbol,
                        "return": returns.values,
                    }
                )
            )
        if not frames:
            raise ValueError("No adjusted price observations available")
        data = pd.concat(frames).dropna().sort_values(["date", "security_id"])
        body = data.to_csv(index=False, float_format="%.12g").encode()
        return body, {
            "source": "https://finance.yahoo.com/",
            "provider": self.name,
            "frequency": "monthly",
            "units": "decimal_total_return_proxy",
            "rows": len(data),
            "start": data.date.min(),
            "end": data.date.max(),
            "symbols": request["symbols"],
            "adjustment": request["adjustment"],
            "availability": "historical_vintage_unknown",
        }
