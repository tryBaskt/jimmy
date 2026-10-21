"""Validate a user hypothesis and translate it into a fixed strategy plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from packages.agent_harness.bedrock import BedrockStructuredClient

"""Instructions shared by the strategy planner and dated decision maker."""

PLAN_INSTRUCTIONS = """You are a strategy testing agent, not a strategy discovery agent.
Translate the user's explicit hypothesis into a bounded test plan. Do not search
for the best-performing rule or invent an investment hypothesis. Reject vague
requests to discover whichever signal worked best, and requests requiring
earnings calls, news, fundamentals, options, or other unavailable data.
Reject sector-neutral, beta-neutral, or other portfolio constraints that
cannot be enforced by this version of the simulator.
The available signals are trailing price return, realized volatility,
drawdown from a period high, distance from the period average price, and beta
to SPY. The available universe is current SP500 membership. Rebalancing may
be monthly or quarterly. The user's separate lookback-months control takes
precedence over any lookback in the text. Default to 10 holdings, monthly,
long-only, equal weight, 5 bps fees, and 5 bps slippage when unspecified.
Never silently change an explicitly requested rule. "Market weights" means
market_cap weighting, calculated from Yahoo historical market-cap estimates
at each prior rebalance close. If the prompt cannot be tested with these
inputs, set supported=false and explain why.
Return exactly one submit_strategy_plan tool call.
"""

DECISION_INSTRUCTIONS = """Apply the fixed strategy plan to the supplied
as-of-date price signals. Select only supplied symbols. Do not invent prices,
change the hypothesis, use later data, or optimize against backtest outcomes.
Return exactly one submit_portfolio_decision tool call with up to the plan's
top_n positions. Each position needs a symbol, long/short direction, positive
weight, and brief reason grounded in its supplied signal. For equal weighting,
return equal weights that sum to one. For model weighting, positive weights
must sum to at most one in absolute exposure. For market_cap weighting, return
positive placeholder weights; the harness replaces them with normalized
historical market-cap estimates before the backtest. Do not short unless the
plan explicitly permits it.
"""


METRICS = {
    "trailing_return": "price return",
    "realized_volatility": "return volatility",
    "drawdown_from_peak": "drawdown from period high",
    "moving_average_gap": "distance from average price",
    "market_beta": "beta to SPY",
}


@dataclass(frozen=True)
class StrategyPlan:
    hypothesis: str
    universe: str
    top_n: int
    lookback_months: int
    rebalance: str
    metric: str
    rank_direction: str
    weighting: str
    allow_short: bool
    fees_bps: int
    slippage_bps: int

    @classmethod
    def from_model(cls, value: Any, lookback_months: int) -> "StrategyPlan":
        if type(lookback_months) is not int or not 1 <= lookback_months <= 12:
            raise ValueError("Lookback must be an integer from 1 to 12 months")
        fields = {
            "supported", "unsupported_reason", "hypothesis", "universe", "top_n",
            "rebalance", "metric", "rank_direction", "weighting", "allow_short",
            "fees_bps", "slippage_bps",
        }
        if not isinstance(value, dict):
            raise ValueError("The model returned an invalid strategy plan")
        if type(value.get("supported")) is not bool or not isinstance(value.get("unsupported_reason", ""), str):
            raise ValueError("The model returned an invalid support assessment")
        if not value["supported"]:
            raise ValueError(value.get("unsupported_reason", "")[:300] or "Please state a specific, testable hypothesis")
        missing = fields - set(value) - {"unsupported_reason"}
        if missing:
            raise ValueError(
                "The model returned an incomplete strategy plan"
                + (f" (missing: {', '.join(sorted(missing))})" if missing else "")
            )
        hypothesis = value["hypothesis"]
        if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 1000:
            raise ValueError("The strategy needs a specific hypothesis")
        if value["universe"] != "SP500":
            raise ValueError("Only the SP500 universe is currently supported")
        if value["rebalance"] not in ("monthly", "quarterly"):
            raise ValueError("Rebalancing must be monthly or quarterly")
        if not isinstance(value["metric"], str) or value["metric"] not in METRICS or value["rank_direction"] not in ("highest", "lowest"):
            raise ValueError("The requested signal is not supported by the available price data")
        if value["weighting"] not in ("equal", "model", "market_cap"):
            raise ValueError("Weighting must be equal, model suggested, or market cap")
        if type(value["allow_short"]) is not bool:
            raise ValueError("The shorting rule must be explicit")
        if type(value["top_n"]) is not int or not 1 <= value["top_n"] <= 20:
            raise ValueError("top_n must be an integer from 1 to 20")
        for key in ("fees_bps", "slippage_bps"):
            if type(value[key]) is not int or not 0 <= value[key] <= 100:
                raise ValueError(f"{key} must be 0-100 basis points")
        return cls(
            hypothesis.strip(), "SP500", value["top_n"], lookback_months,
            value["rebalance"], value["metric"], value["rank_direction"],
            value["weighting"], value["allow_short"], value["fees_bps"],
            value["slippage_bps"],
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def ranking_rule(self) -> str:
        return f"{self.rank_direction} {METRICS[self.metric]} over {self.lookback_months} months"


def _tool(name: str, description: str, schema: dict) -> dict:
    return {"toolSpec": {"name": name, "description": description, "inputSchema": {"json": schema}}}


PLAN_TOOL = _tool("submit_strategy_plan", "Submit a bounded strategy test plan", {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "unsupported_reason": {"type": "string"},
        "hypothesis": {"type": "string"},
        "universe": {"type": "string", "enum": ["SP500"]},
        "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
        "rebalance": {"type": "string", "enum": ["monthly", "quarterly"]},
        "metric": {"type": "string", "enum": list(METRICS)},
        "rank_direction": {"type": "string", "enum": ["highest", "lowest"]},
        "weighting": {"type": "string", "enum": ["equal", "model", "market_cap"]},
        "allow_short": {"type": "boolean"},
        "fees_bps": {"type": "integer", "minimum": 0, "maximum": 100},
        "slippage_bps": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["supported", "unsupported_reason", "hypothesis", "universe", "top_n", "rebalance", "metric", "rank_direction", "weighting", "allow_short", "fees_bps", "slippage_bps"],
    "additionalProperties": False,
})

DECISION_TOOL = _tool("submit_portfolio_decision", "Submit target positions for one rebalance", {
    "type": "object",
    "properties": {"positions": {
        "type": "array", "maxItems": 20,
        "items": {"type": "object", "properties": {
            "symbol": {"type": "string"},
            "direction": {"type": "string", "enum": ["long", "short"]},
            "weight": {"type": "number"},
            "reason": {"type": "string"},
        }, "required": ["symbol", "direction", "weight", "reason"], "additionalProperties": False},
    }},
    "required": ["positions"], "additionalProperties": False,
})


class BedrockStrategyAgent:
    def __init__(self, *, session: Any, model_id: str) -> None:
        self.model_id = model_id
        self._model = BedrockStructuredClient(session=session, model_id=model_id)

    def _ask(self, instructions: str, prompt: str, tool: dict, max_tokens: int) -> dict:
        return self._model.request(
            instructions=instructions, prompt=prompt, tool=tool, max_tokens=max_tokens,
        )

    def plan(self, prompt: str, lookback_months: int) -> StrategyPlan:
        if not prompt.strip() or len(prompt) > 4000:
            raise ValueError("Prompt must contain 1-4000 characters")
        if type(lookback_months) is not int or not 1 <= lookback_months <= 12:
            raise ValueError("Lookback must be an integer from 1 to 12 months")
        result = self._ask(
            PLAN_INSTRUCTIONS,
            f"User hypothesis: {prompt}\nLookback control: {lookback_months} months.",
            PLAN_TOOL, 800,
        )
        if isinstance(result, dict) and result.get("supported", True):
            market_weight_requested = bool(re.search(
                r"\bmarket(?:[- ]cap(?:italization)?)?[- ]weight(?:s|ed|ing)?\b",
                prompt, re.IGNORECASE,
            ))
            result = {
                "supported": True,
                "unsupported_reason": "",
                "hypothesis": prompt.strip(),
                "universe": "SP500",
                "top_n": 10,
                "rebalance": "monthly",
                "weighting": "market_cap" if market_weight_requested else "equal",
                "allow_short": False,
                "fees_bps": 5,
                "slippage_bps": 5,
                **result,
            }
            if market_weight_requested and result["weighting"] != "market_cap":
                raise ValueError("The model did not preserve the requested market-cap weighting")
        return StrategyPlan.from_model(result, lookback_months)

    def decide(self, plan: StrategyPlan, signal_date: str, signals: list[dict]) -> dict:
        import json

        result = self._ask(
            DECISION_INSTRUCTIONS,
            json.dumps({"plan": plan.to_dict(), "signal_date": signal_date, "eligible_signals": signals}, allow_nan=False),
            DECISION_TOOL, 1800,
        )
        return result
