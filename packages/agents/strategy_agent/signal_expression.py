"""Evaluate model-authored price signals without executing arbitrary Python."""

from __future__ import annotations

import ast
from math import isfinite, sqrt

import pandas as pd


class SignalExpressionError(ValueError):
    """The proposed price calculation cannot be evaluated safely."""


def _cov(left: pd.DataFrame, right: pd.Series) -> pd.Series:
    if not isinstance(left, pd.DataFrame) or not isinstance(right, pd.Series):
        raise SignalExpressionError("cov expects stock histories and one benchmark history")
    return left.apply(lambda column: column.cov(right))


def _reduce(value, operation: str):
    if not isinstance(value, (pd.DataFrame, pd.Series)):
        raise SignalExpressionError(f"{operation} expects a price history")
    if operation == "first":
        return value.iloc[0]
    if operation == "last":
        return value.iloc[-1]
    if operation == "std":
        return value.std(ddof=1)
    if operation == "var":
        return value.var(ddof=1)
    return getattr(value, operation)()


def evaluate_signal(expression: str, close: pd.DataFrame, spy: pd.Series) -> pd.Series:
    """Return one as-of-date signal per stock from a restricted expression."""
    if not isinstance(expression, str) or not 1 <= len(expression) <= 240:
        raise SignalExpressionError("Signal calculation must be 1-240 characters")
    if close.empty or spy.empty or not close.index.equals(spy.index):
        raise SignalExpressionError("Stock and benchmark histories must share dates")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise SignalExpressionError("Signal calculation has invalid syntax") from error
    if sum(1 for _ in ast.walk(tree)) > 60:
        raise SignalExpressionError("Signal calculation is too complex")

    binary = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
              ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
              ast.Pow: lambda a, b: a ** b}
    unary = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}
    reducers = {"first", "last", "mean", "min", "max", "std", "var"}

    def calculate(node: ast.AST, depth: int = 0):
        if depth > 12:
            raise SignalExpressionError("Signal calculation is too deeply nested")
        if isinstance(node, ast.Expression):
            return calculate(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1_000_000 or not isfinite(node.value):
                raise SignalExpressionError("Signal calculation has an invalid number")
            return node.value
        if isinstance(node, ast.Name) and node.id in ("close", "spy"):
            return close if node.id == "close" else spy
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = calculate(node.left, depth + 1), calculate(node.right, depth + 1)
            if ((isinstance(left, pd.DataFrame) and isinstance(right, pd.Series))
                    or (isinstance(left, pd.Series) and isinstance(right, pd.DataFrame))
                    or (isinstance(left, pd.Series) and isinstance(right, pd.Series)
                        and not left.index.equals(right.index))):
                raise SignalExpressionError("Signal calculation mixes incompatible histories")
            if isinstance(node.op, ast.Pow) and (type(right) not in (int, float) or abs(right) > 4):
                raise SignalExpressionError("Signal exponent must be a number between -4 and 4")
            return binary[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](calculate(node.operand, depth + 1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name in reducers and len(node.args) == 1:
                return _reduce(calculate(node.args[0], depth + 1), name)
            if name == "pct_change" and len(node.args) == 1:
                value = calculate(node.args[0], depth + 1)
                if not isinstance(value, (pd.DataFrame, pd.Series)):
                    raise SignalExpressionError("pct_change expects a price history")
                return value.pct_change(fill_method=None).iloc[1:]
            if name == "cov" and len(node.args) == 2:
                return _cov(calculate(node.args[0], depth + 1), calculate(node.args[1], depth + 1))
            if name == "abs" and len(node.args) == 1:
                return abs(calculate(node.args[0], depth + 1))
            if name == "sqrt" and len(node.args) == 1:
                value = calculate(node.args[0], depth + 1)
                if type(value) not in (int, float) or value < 0:
                    raise SignalExpressionError("sqrt expects a nonnegative number")
                return sqrt(value)
        raise SignalExpressionError("Signal calculation uses an unavailable operation")

    try:
        result = calculate(tree)
    except SignalExpressionError:
        raise
    except (ArithmeticError, TypeError, ValueError) as error:
        raise SignalExpressionError("Signal calculation could not be evaluated") from error
    if not isinstance(result, pd.Series) or not result.index.equals(close.columns):
        raise SignalExpressionError("Signal calculation must return one value per stock")
    return pd.to_numeric(result, errors="coerce")


def validate_signal(expression: str) -> None:
    """Reject invalid formulas before a job downloads the universe."""
    dates = pd.date_range("2025-01-01", periods=8, tz="UTC")
    close = pd.DataFrame({"A": [10, 11, 12, 13, 14, 15, 16, 17],
                          "B": [20, 21, 21, 22, 23, 22, 24, 25]}, index=dates)
    spy = pd.Series([100, 101, 102, 101, 103, 104, 105, 106], index=dates)
    evaluate_signal(expression, close, spy)
