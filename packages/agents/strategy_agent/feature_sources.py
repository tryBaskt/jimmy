"""Extensible, point-in-time feature evaluation for strategy plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

import pandas as pd

from .signal_expression import evaluate_signal, validate_signal


class UnavailableFeatureSource(ValueError):
    """A plan requests data that this deployment cannot currently provide."""


@dataclass(frozen=True)
class FeatureContext:
    as_of: pd.Timestamp
    close: pd.DataFrame
    spy: pd.Series
    evidence: Mapping[str, Any] = field(default_factory=dict)
    prepared: Any = None


@dataclass(frozen=True)
class FeaturePreparationContext:
    symbols: tuple[str, ...]
    start: datetime
    end: datetime
    evidence: Mapping[str, Any] = field(default_factory=dict)


FeatureEvaluator = Callable[[dict, FeatureContext], pd.Series]
FeatureValidator = Callable[[dict], None]
FeaturePreparer = Callable[[list[dict], FeaturePreparationContext], Any]


class FeatureSourceRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[str, FeatureEvaluator] = {}
        self._descriptions: dict[str, str] = {}
        self._validators: dict[str, FeatureValidator] = {}
        self._preparers: dict[str, FeaturePreparer] = {}
        self._prepared: dict[str, Any] = {}
        self.register(
            "price_history",
            lambda feature, context: evaluate_signal(
                feature["instruction"], context.close, context.spy,
            ),
            description=("Historical adjusted closes for each stock and SPY. "
                         "Instruction is a numeric expression over close and spy histories "
                         "using +, -, *, /, **, first, last, mean, min, max, std, var, "
                         "pct_change, cov, abs, or sqrt; return one value per stock."),
            validator=lambda feature: validate_signal(feature["instruction"]),
        )

    def register(self, source: str, evaluator: FeatureEvaluator, *, description: str = "",
                 validator: FeatureValidator | None = None,
                 prepare: FeaturePreparer | None = None) -> None:
        if not source or source in self._evaluators:
            raise ValueError(f"Duplicate or empty feature source: {source}")
        self._evaluators[source] = evaluator
        self._descriptions[source] = description or source
        if validator is not None:
            self._validators[source] = validator
        if prepare is not None:
            self._preparers[source] = prepare

    def capabilities(self) -> dict[str, str]:
        return self._descriptions.copy()

    def validate(self, feature: dict) -> None:
        if feature["source"] in self._validators:
            self._validators[feature["source"]](feature)

    def require(self, features: tuple[dict, ...]) -> None:
        unavailable = sorted({feature["source"] for feature in features
                              if feature["source"] not in self._evaluators})
        if unavailable:
            raise UnavailableFeatureSource(
                f"Data source {', '.join(unavailable)} is not connected to this strategy worker"
            )

    def prepare(self, features: tuple[dict, ...], context: FeaturePreparationContext) -> None:
        self.require(features)
        self._prepared.clear()
        for source, preparer in self._preparers.items():
            requested = [feature for feature in features if feature["source"] == source]
            if requested:
                self._prepared[source] = preparer(requested, context)

    def evaluate(self, feature: dict, context: FeatureContext) -> pd.Series:
        self.require((feature,))
        source_context = FeatureContext(
            as_of=context.as_of, close=context.close, spy=context.spy,
            evidence=context.evidence, prepared=self._prepared.get(feature["source"]),
        )
        values = self._evaluators[feature["source"]](feature, source_context)
        if (not isinstance(values, pd.Series) or not values.index.is_unique
                or not values.index.isin(context.close.columns).all()):
            raise ValueError("Feature source returned invalid stock symbols")
        try:
            return pd.to_numeric(values.reindex(context.close.columns), errors="raise")
        except (TypeError, ValueError) as error:
            raise ValueError("Feature source must return numeric scores for each stock") from error


def price_history_registry() -> FeatureSourceRegistry:
    return FeatureSourceRegistry()
