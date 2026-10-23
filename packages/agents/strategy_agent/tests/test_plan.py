import pytest
from unittest.mock import Mock

from packages.agents.strategy_agent.plan import BedrockStrategyAgent, StrategyPlan


def model_plan(**overrides):
    value = {
        "supported": True, "unsupported_reason": "",
        "hypothesis": "Buy the 10 strongest prior performers",
        "universe": "SP500", "top_n": 10, "rebalance": "monthly",
        "metric": "trailing_return", "rank_direction": "highest",
        "weighting": "equal", "allow_short": False,
        "fees_bps": 5, "slippage_bps": 5,
    }
    return {**value, **overrides}


def test_explicit_hypothesis_is_a_fixed_plan():
    plan = StrategyPlan.from_model(model_plan(), 3)
    assert plan.ranking_rule() == "highest price return over 3 months"
    assert plan.to_dict()["top_n"] == 10


def test_discovery_request_is_rejected():
    with pytest.raises(ValueError, match="specific"):
        StrategyPlan.from_model(model_plan(
            supported=False, unsupported_reason="Please state a specific, testable hypothesis"
        ), 3)


def test_market_cap_weighting_is_not_silently_substituted():
    plan = StrategyPlan.from_model(model_plan(weighting="market_cap"), 3)
    assert plan.weighting == "market_cap"


def test_unsupported_plan_needs_only_a_reason():
    with pytest.raises(ValueError, match="transcripts"):
        StrategyPlan.from_model({
            "supported": False, "unsupported_reason": "Historical transcripts are unavailable"
        }, 3)


def test_market_weight_prompt_accepts_missing_optional_model_fields():
    agent = BedrockStrategyAgent(session=Mock(), model_id="test")
    agent._ask = lambda *args: {
        "metric": "trailing_return",
        "rank_direction": "highest",
        "weighting": "market_cap",
    }
    plan = agent.plan(
        "Buy the 10 strongest S&P 500 stocks monthly at market weights", 3
    )
    assert plan.weighting == "market_cap"
    assert plan.top_n == 10
    assert plan.fees_bps == 5


def test_complete_random_hypothesis_uses_generic_sampling_step():
    agent = BedrockStrategyAgent(session=Mock(), model_id="test")
    agent._ask = lambda *args: {
        "top_n": 15, "rebalance": "monthly",
        "selection_steps": [{"operation": "sample"}],
    }
    plan = agent.plan("Choose 15 S&P 500 stocks randomly each month", 2)
    assert plan.selection_steps == ({"operation": "sample"},)
    assert plan.ranking_rule() == "random sample"


def test_selection_steps_reject_unavailable_operations():
    with pytest.raises(ValueError, match="Invalid selection step"):
        StrategyPlan.from_model(model_plan(selection_steps=[{"operation": "execute_python"}]), 2)
