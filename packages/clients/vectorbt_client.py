"""Client wrapper for normalized VectorBT portfolio simulations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from vectorbt.portfolio.base import Portfolio


@dataclass(frozen=True)
class VectorBTPortfolioAnalytics:
    """Normalized VectorBT result shared with the research workflow."""

    timestamps: list[datetime]
    cumulative_returns: list[float]
    prices: list[float] | None
    final_cumulative_return: float
    cagr: float | None
    annualized_volatility: float | None
    leverage_adjusted_direction: float | None
    alpha: float | None
    beta: float | None
    sharpe_ratio: float | None
    maximum_drawdown: float | None
    maximum_drawdown_duration: float | None


class VectorBTClientError(Exception):
    """Raised when VectorBT input validation or simulation fails."""

    def __init__(
        self,
        message: str,
        code: str = "VECTORBT_CLIENT_ERROR",
    ) -> None:
        """Initialize a VectorBT client exception.

        Args:
            message: Human-readable failure details.
            code: Stable application error code identifying the failure.

        Returns:
            None.
        """
        super().__init__(message)
        self.code = code


class VectorBTClient:
    """Run VectorBT portfolio simulations with caller-prepared inputs."""

    def calculate_portfolio_analytics(
        self,
        *,
        prices: pd.DataFrame,
        target_exposure: pd.DataFrame,
        frequency: str,
        initial_cash: float = 10_000.0,
        fees: float = 0.0,
        slippage: float = 0.0,
        excluded_direction_symbols: Optional[Sequence[str]] = None,
        benchmark_returns: Optional[pd.Series] = None,
    ) -> VectorBTPortfolioAnalytics:
        """Simulate a portfolio from prices and target percentage exposures.

        ``target_exposure`` uses VectorBT target-percent semantics. Non-null
        rows trigger portfolio rebalancing, while null rows leave the current
        holdings unchanged. Positive values represent long exposure and
        negative values represent short exposure.

        Args:
            prices: Complete numeric price data indexed by unique, sorted
                timestamps with one column per symbol. Values must be finite,
                non-null, and greater than zero.
            target_exposure: DataFrame with the same index and columns as
                prices. Rebalance rows contain decimal target exposures and
                non-rebalance rows contain null values.
            frequency: VectorBT-compatible pandas frequency such as ``1d``,
                ``1h``, or ``5min``.
            initial_cash: Starting cash value for the simulation.
            fees: Proportional transaction fee applied by VectorBT.
            slippage: Proportional execution slippage applied by VectorBT.
            excluded_direction_symbols: Synthetic symbols to exclude when
                calculating leverage-adjusted direction, such as a financing
                position.
            benchmark_returns: Optional benchmark returns aligned to the price
                index. Required for alpha and beta.

        Returns:
            VectorBTPortfolioAnalytics: Normalized return series and metrics. All
            return values are decimal fractions rather than percentages.

        Raises:
            VectorBTClientError: If VectorBT cannot simulate the supplied
                inputs or returns an empty result.
        """
        if not isfinite(initial_cash) or initial_cash <= 0:
            raise VectorBTClientError(
                message="VectorBT initial_cash must be greater than zero",
                code="VECTORBT_INVALID_INITIAL_CASH",
            )
        if not isfinite(fees) or fees < 0:
            raise VectorBTClientError(
                message="VectorBT fees cannot be negative",
                code="VECTORBT_INVALID_FEES",
            )
        if not isfinite(slippage) or slippage < 0:
            raise VectorBTClientError(
                message="VectorBT slippage cannot be negative",
                code="VECTORBT_INVALID_SLIPPAGE",
            )

        if prices.empty or not isinstance(prices.index, pd.DatetimeIndex):
            raise VectorBTClientError("Prices need a non-empty datetime index")
        if not prices.index.is_monotonic_increasing or not prices.index.is_unique:
            raise VectorBTClientError("Price timestamps must be sorted and unique")
        if not prices.columns.is_unique or not prices.columns.equals(target_exposure.columns):
            raise VectorBTClientError("Price and exposure symbols must match exactly")
        if not prices.index.equals(target_exposure.index):
            raise VectorBTClientError("Price and exposure timestamps must match exactly")
        try:
            numeric_prices = prices.to_numpy(dtype="float64")
            numeric_exposure = target_exposure.to_numpy(dtype="float64")
        except (TypeError, ValueError) as error:
            raise VectorBTClientError("Prices and exposures must be numeric") from error
        if not np.isfinite(numeric_prices).all() or (numeric_prices <= 0).any():
            raise VectorBTClientError("Prices must be finite and greater than zero")
        if np.isinf(numeric_exposure).any():
            raise VectorBTClientError("Exposures must be finite or null")
        if benchmark_returns is not None:
            if not benchmark_returns.index.equals(prices.index):
                raise VectorBTClientError("Benchmark timestamps must match prices")
            if not np.isfinite(benchmark_returns.to_numpy(dtype="float64")).all():
                raise VectorBTClientError("Benchmark returns must be finite")

        try:
            portfolio = Portfolio.from_orders(
                close=prices,
                size=target_exposure,
                size_type="targetpercent",
                direction="both",
                init_cash=float(initial_cash),
                cash_sharing=True,
                group_by=True,
                call_seq="auto",
                fees=float(fees),
                slippage=float(slippage),
                freq="1D" if frequency.lower() == "1d" else frequency.lower(),
            )

            cumulative_returns = portfolio.cumulative_returns()
            if not isinstance(cumulative_returns, pd.Series):
                cumulative_returns = pd.Series(
                    cumulative_returns,
                    index=prices.index,
                )
            if cumulative_returns.empty:
                raise VectorBTClientError(
                    message="VectorBT returned an empty cumulative return series",
                    code="VECTORBT_EMPTY_RESULT",
                )

            cagr = portfolio.annualized_return()
            annualized_volatility = portfolio.annualized_volatility()
            sharpe_ratio = portfolio.sharpe_ratio()
            maximum_drawdown = portfolio.max_drawdown()
            maximum_drawdown_duration = portfolio.drawdowns.max_duration()
            alpha = None
            beta = None
            if benchmark_returns is not None:
                alpha = portfolio.returns_acc.alpha(
                    benchmark_rets=benchmark_returns,
                )
                beta = portfolio.returns_acc.beta(
                    benchmark_rets=benchmark_returns,
                )

            asset_value = portfolio.asset_value(group_by=False)
            if isinstance(asset_value, pd.Series):
                asset_value = asset_value.to_frame()
            excluded_symbols = set(excluded_direction_symbols or ())
            included_columns = [
                column
                for column in asset_value.columns
                if str(column) not in excluded_symbols
            ]
            final_portfolio_value = portfolio.value().iloc[-1]
            leverage_adjusted_direction = (
                None
                if pd.isna(final_portfolio_value) or final_portfolio_value == 0
                else float(asset_value[included_columns].iloc[-1].sum())
                / float(final_portfolio_value)
            )
            return VectorBTPortfolioAnalytics(
                timestamps=[
                    timestamp.to_pydatetime()
                    if isinstance(timestamp, pd.Timestamp)
                    else timestamp
                    for timestamp in cumulative_returns.index
                ],
                cumulative_returns=[
                    float(value) for value in cumulative_returns.to_numpy()
                ],
                prices=(
                    [float(value) for value in prices.iloc[:, 0].to_numpy()]
                    if len(prices.columns) == 1
                    else None
                ),
                final_cumulative_return=float(cumulative_returns.iloc[-1]),
                cagr=None if pd.isna(cagr) else float(cagr),
                annualized_volatility=(
                    None
                    if pd.isna(annualized_volatility)
                    else float(annualized_volatility)
                ),
                leverage_adjusted_direction=leverage_adjusted_direction,
                alpha=None if alpha is None or pd.isna(alpha) else float(alpha),
                beta=None if beta is None or pd.isna(beta) else float(beta),
                sharpe_ratio=(
                    None if pd.isna(sharpe_ratio) else float(sharpe_ratio)
                ),
                maximum_drawdown=(
                    None if pd.isna(maximum_drawdown) else float(maximum_drawdown)
                ),
                maximum_drawdown_duration=(
                    None
                    if pd.isna(maximum_drawdown_duration)
                    else float(pd.Timedelta(maximum_drawdown_duration) / pd.Timedelta(days=1))
                ),
            )
        except VectorBTClientError:
            raise
        except Exception as error:
            raise VectorBTClientError(
                message=f"Failed to simulate portfolio with VectorBT: {error}",
                code="VECTORBT_SIMULATION_FAILED",
            ) from error
