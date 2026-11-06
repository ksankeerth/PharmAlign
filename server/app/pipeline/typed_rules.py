"""Code decides typed and code checks: the agent extracts values, and a pack's rule compares them.

A rule is parsed and walked, never executed. It answers true, false or None when a value is missing. Pure.
"""
import ast
from typing import Any, Mapping, Optional

from .citations import normalise

ALL_MODULES = "$modules"  # an input that a rule can reach only through document_present


class RuleError(ValueError):
    """The rule itself can't be read. That's a fault in the pack, not in the company's file."""


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return x == y
    return normalise(str(a)) == normalise(str(b))


def _contains(items: list, value: Any) -> bool:
    return any(_same(i, value) for i in items)


def _compare(op: ast.cmpop, a: Any, b: Any) -> Optional[bool]:
    if a is None or b is None:
        return None
    if isinstance(op, (ast.In, ast.NotIn)):
        if not isinstance(b, list):
            raise RuleError("`in` needs a list")
        return _contains(b, a) == isinstance(op, ast.In)
    if isinstance(a, list) and isinstance(b, list):
        has_all_b = all(_contains(a, i) for i in b)
        has_all_a = all(_contains(b, i) for i in a)
        if isinstance(op, ast.GtE):
            return has_all_b
        if isinstance(op, ast.LtE):
            return has_all_a
        if isinstance(op, (ast.Eq, ast.NotEq)):
            return (has_all_a and has_all_b) == isinstance(op, ast.Eq)
        raise RuleError("this comparison doesn't apply to lists")
    if isinstance(op, ast.Eq):
        return _same(a, b)
    if isinstance(op, ast.NotEq):
        return not _same(a, b)
    x, y = _number(a), _number(b)
    if x is None or y is None:
        return None
    if isinstance(op, ast.Lt):
        return x < y
    if isinstance(op, ast.LtE):
        return x <= y
    if isinstance(op, ast.Gt):
        return x > y
    if isinstance(op, ast.GtE):
        return x >= y
    raise RuleError("unknown comparison")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    raise RuleError("only plain names can be read")


def _lookup(path: str, inputs: Mapping[str, Any]) -> Any:
    if path in inputs:
        return inputs[path]
    value: Any = inputs
    for part in path.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def _walk(node: ast.AST, inputs: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_walk(v, inputs) for v in node.values]
        if isinstance(node.op, ast.And):
            return False if any(v is False for v in values) else None if any(v is None for v in values) else True
        return True if any(v is True for v in values) else None if any(v is None for v in values) else False
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _walk(node.operand, inputs)
        return None if value is None else not value
    if isinstance(node, ast.Compare):
        results, left = [], _walk(node.left, inputs)
        for op, comparator in zip(node.ops, node.comparators):
            right = _walk(comparator, inputs)
            results.append(_compare(op, left, right))
            left = right
        return False if any(r is False for r in results) else None if any(r is None for r in results) else True
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_walk(e, inputs) for e in node.elts]
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "document_present" and len(node.args) == 1 and not node.keywords):
            raise RuleError("the only call a rule may make is document_present")
        wanted = _walk(node.args[0], inputs)
        return any(m == wanted or m.startswith(f"{wanted}.") for m in inputs.get(ALL_MODULES, []))
    if isinstance(node, ast.Name) and node.id in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[node.id]
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _lookup(_dotted(node), inputs)
    raise RuleError(f"a rule can't use {type(node).__name__}")


def evaluate(rule: str, inputs: Mapping[str, Any]) -> Optional[bool]:
    """True or false as the rule says, or None if a value it needs is missing. Raises RuleError for a bad rule."""
    try:
        tree = ast.parse(rule, mode="eval")
    except SyntaxError as e:
        raise RuleError("this rule can't be parsed") from e
    value = _walk(tree.body, inputs)
    return value if value is None else bool(value)


def inputs_used(rule: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The values the rule read, for the trace. Holds nothing else."""
    used: dict[str, Any] = {}
    for node in ast.walk(ast.parse(rule, mode="eval")):
        if isinstance(node, ast.Call):
            used[ast.unparse(node)] = _walk(node, inputs)
        elif isinstance(node, ast.Attribute) or (isinstance(node, ast.Name) and node.id not in ("true", "false", "null", "document_present")):
            path = _dotted(node)
            root = path.split(".")[0]
            if path in inputs:
                used[path] = inputs[path]
            elif root in inputs:
                used[root] = inputs[root]
    return used
