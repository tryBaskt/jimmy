"""VectorBT execution diagnostics for a series of target exposures."""

import numpy as np


class VectorBTClient:
    def calculate_portfolio_analytics(self, *, prices, target_exposure, fees=0.0, slippage=0.0,
                                      initial_cash=100_000.0, buy_and_hold=False):
        from vectorbt import Portfolio

        if not prices.index.equals(target_exposure.index) or not prices.columns.equals(target_exposure.columns):
            raise ValueError("Prices and exposures must have identical axes")
        if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
            raise ValueError("VectorBT requires complete positive prices")
        if initial_cash <= 0 or fees < 0 or slippage < 0:
            raise ValueError("Invalid cash, fees, or slippage")
        orders = target_exposure.copy()
        if buy_and_hold:
            orders.iloc[1:] = np.nan
        portfolio = Portfolio.from_orders(
            close=prices, size=orders, size_type="targetpercent", direction="both",
            init_cash=initial_cash, cash_sharing=True, group_by=True, call_seq="auto",
            fees=fees, slippage=slippage, freq="30D", raise_reject=True,
        )
        value = portfolio.value()
        return {
            "final_account_return": float(value.iloc[-1] / initial_cash - 1),
            "filled_orders": len(portfolio.orders.records),
            "note": "Monthly-close execution diagnostic including configured fees and slippage. "
                    "No borrow fees, financing constraints or market impact. Not the paper's gross spread.",
        }
