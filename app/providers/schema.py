"""The stages' JSON schemas, as a hosted provider wants them, and checked here.

The stages write their schemas for Ollama (app/tagging/llm.py and the rest),
and those stay as they are. For a provider:

    strict      OpenAI's strict structured output wants every property
                required and `additionalProperties: false` on every object,
                and rejects some keywords. The size limits (maxItems) are
                dropped from what is sent; the stages' own parsing cuts lists
                to length, and `validate` still checks the rest.
    validate    every reply is checked here against the stage's own schema,
                whatever the provider promised. A reply that fails is an API
                error on that reel, never a result.

Only the keywords the stages use are understood: type (object, array, string,
integer, number, boolean), properties, required, items, enum. A list longer
than its maxItems is not an error: the stage's parsing cuts it, as it does
for Ollama.

Stdlib only.
"""

from __future__ import annotations

import copy
from typing import Any

# Not sent in strict mode; enforced, where it matters, by the stages' parsing.
_DROPPED = ("maxItems", "minItems")


def strict(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of `schema` that OpenAI's strict mode accepts."""
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for k in _DROPPED:
            node.pop(k, None)
        if node.get("type") == "object":
            props = node.get("properties", {})
            node["required"] = list(props)
            node["additionalProperties"] = False
            for v in props.values():
                walk(v)
        if node.get("type") == "array":
            walk(node.get("items"))

    walk(out)
    return out


_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def validate(data: Any, schema: dict[str, Any], path: str = "reply") -> list[str]:
    """What is wrong with `data` against `schema`; empty when nothing is."""
    errors: list[str] = []
    kind = schema.get("type")
    if kind:
        types = _TYPES.get(kind, ())
        # bool is an int in Python, and not in JSON.
        if not isinstance(data, types) or (
            kind in ("integer", "number") and isinstance(data, bool)
        ):
            return [f"{path} is not {kind}"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path} is not one of the allowed values")
    if kind == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in data:
                errors.append(f"{path}.{name} is missing")
        for name, sub in props.items():
            if name in data:
                errors += validate(data[name], sub, f"{path}.{name}")
    if kind == "array":
        # maxItems is not checked: a list too long is cut by the stage's parsing.
        items = schema.get("items")
        if items:
            for i, v in enumerate(data):
                errors += validate(v, items, f"{path}[{i}]")
    return errors
