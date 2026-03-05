"""
tool_response_suppressor MCP Server

Exposes a single tool: suppress(tool_responses, schema, mode)

Modes:
  A   - Schema enforcement: validate reported_output against schema,
        remove extra fields, insert null for missing required fields,
        flag type mismatches.
  B   - Output integrity: compare reported_output against actual_output
        field by field; flag mismatches, extra, and missing fields.
  C   - Tool hallucination detection: scan assistant_commentary for
        phrases like "the tool returned", "I executed the tool", etc.
  all - Run all three layers (default).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastmcp import FastMCP

mcp = FastMCP("tool_response_suppressor")

# ---------------------------------------------------------------------------
# Layer A — Schema enforcement helpers
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _enforce_schema(
    data: dict,
    schema: dict,
    tool_name: str,
    violations: list[dict],
    path: str = "",
) -> dict:
    """
    Validate and clean *data* against *schema*.

    - Removes fields not declared in schema.properties.
    - Flags type mismatches (keeps the value so downstream layers can inspect it).
    - Inserts null for required fields that are absent.
    - Recurses into nested objects and array items.
    """
    if schema.get("type") != "object":
        return data

    properties: dict[str, dict] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    clean: dict[str, Any] = {}

    for key, value in data.items():
        if key not in properties:
            violations.append({
                "tool_name": tool_name,
                "type": "schema_violation",
                "field": f"{path}{key}",
                "detail": f"Field '{key}' is not defined in schema; removed.",
            })
            continue  # drop the field

        field_schema = properties[key]
        expected_type = field_schema.get("type")

        # Type-mismatch check
        if expected_type and expected_type in _TYPE_MAP:
            py_type = _TYPE_MAP[expected_type]
            if not isinstance(value, py_type):
                violations.append({
                    "tool_name": tool_name,
                    "type": "schema_violation",
                    "field": f"{path}{key}",
                    "detail": (
                        f"Field '{key}' expected type '{expected_type}' "
                        f"but got '{type(value).__name__}'."
                    ),
                })

        # Recurse into nested objects
        if isinstance(value, dict) and field_schema.get("type") == "object":
            value = _enforce_schema(
                value, field_schema, tool_name, violations, path=f"{path}{key}."
            )

        # Recurse into array items
        elif isinstance(value, list) and field_schema.get("type") == "array":
            item_schema = field_schema.get("items", {})
            if item_schema:
                cleaned_items: list[Any] = []
                for i, item in enumerate(value):
                    if isinstance(item, dict) and item_schema.get("type") == "object":
                        item = _enforce_schema(
                            item,
                            item_schema,
                            tool_name,
                            violations,
                            path=f"{path}{key}[{i}].",
                        )
                    cleaned_items.append(item)
                value = cleaned_items

        clean[key] = value

    # Insert null for missing required fields
    for req_field in required:
        if req_field not in clean:
            violations.append({
                "tool_name": tool_name,
                "type": "schema_violation",
                "field": f"{path}{req_field}",
                "detail": f"Required field '{req_field}' is missing; set to null.",
            })
            clean[req_field] = None

    return clean


# ---------------------------------------------------------------------------
# Layer B — Output integrity helpers
# ---------------------------------------------------------------------------

def _check_integrity(
    reported: dict,
    actual: dict,
    tool_name: str,
    violations: list[dict],
) -> None:
    """Compare *reported* against *actual* field by field."""
    # Extra fields present in reported but absent from actual
    for key in reported:
        if key not in actual:
            violations.append({
                "tool_name": tool_name,
                "type": "output_integrity_violation",
                "field": key,
                "detail": (
                    f"Field '{key}' is present in reported_output "
                    "but not in actual_output."
                ),
            })

    # Fields present in actual but missing from reported
    for key in actual:
        if key not in reported:
            violations.append({
                "tool_name": tool_name,
                "type": "output_integrity_violation",
                "field": key,
                "detail": (
                    f"Field '{key}' is present in actual_output "
                    "but missing from reported_output."
                ),
            })

    # Value mismatches for fields present in both
    for key in reported:
        if key in actual and reported[key] != actual[key]:
            violations.append({
                "tool_name": tool_name,
                "type": "output_integrity_violation",
                "field": key,
                "detail": (
                    f"Field '{key}' mismatch: "
                    f"reported={reported[key]!r}, actual={actual[key]!r}."
                ),
            })


# ---------------------------------------------------------------------------
# Layer C — Tool hallucination detection
# ---------------------------------------------------------------------------

_HALLUCINATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"I executed the tool", re.IGNORECASE),
    re.compile(r"the tool returned", re.IGNORECASE),
    re.compile(r"I checked the logs?", re.IGNORECASE),
    re.compile(r"I ran the command", re.IGNORECASE),
    re.compile(r"the tool failed because", re.IGNORECASE),
    re.compile(r"I accessed your (files?|system|disk|data|documents?|screen)", re.IGNORECASE),
    re.compile(r"I accessed the (internet|web|network|external|remote)", re.IGNORECASE),
    re.compile(r"the tool produced", re.IGNORECASE),
    re.compile(r"according to the tool", re.IGNORECASE),
]


def _check_hallucination(
    commentary: str,
    violations: list[dict],
) -> None:
    """Scan *commentary* for tool hallucination phrases."""
    for pattern in _HALLUCINATION_PATTERNS:
        match = pattern.search(commentary)
        if match:
            violations.append({
                "tool_name": None,
                "type": "tool_hallucination",
                "field": None,
                "detail": f"Hallucination pattern detected: '{match.group(0)}'.",
            })


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

@mcp.tool()
def suppress(
    tool_responses: list[dict[str, Any]],
    schema: dict[str, Any],
    mode: Literal["A", "B", "C", "all"] = "all",
    assistant_commentary: str = "",
) -> dict[str, Any]:
    """
    Validate and clean tool responses against a schema and actual outputs.

    Parameters
    ----------
    tool_responses : list
        List of objects, each with:
          - tool_name (str): name of the tool
          - actual_output (dict): the real output the tool returned
          - reported_output (dict): what the model claims the tool returned
    schema : dict
        JSON Schema object defining the expected tool output structure.
        Supports: type, properties, required, items.
    mode : str
        Which suppression layers to run: "A", "B", "C", or "all" (default).
    assistant_commentary : str, optional
        Free-form assistant text to scan for tool hallucination phrases (Layer C).

    Returns
    -------
    dict with keys:
        clean_output  - list of {tool_name, output} with validated reported_output
        violations    - list of violation dicts (tool_name, type, field, detail)
        summary       - human-readable summary string
    """
    if mode not in ("A", "B", "C", "all"):
        return {
            "clean_output": [],
            "violations": [{
                "tool_name": None,
                "type": "invalid_mode",
                "field": None,
                "detail": f"Unknown mode '{mode}'. Use 'A', 'B', 'C', or 'all'.",
            }],
            "summary": f"Invalid mode '{mode}'. No processing performed.",
        }

    run_a = mode in ("A", "all")
    run_b = mode in ("B", "all")
    run_c = mode in ("C", "all")

    violations: list[dict[str, Any]] = []
    clean_output: list[dict[str, Any]] = []

    for entry in tool_responses:
        tool_name: str = entry.get("tool_name", "<unknown>")
        actual_output: dict = entry.get("actual_output", {})
        reported_output: dict = entry.get("reported_output", {})

        # Work on a copy so layers don't interfere with each other's input
        current = dict(reported_output)

        # Layer A: schema enforcement (also cleans the dict)
        if run_a:
            current = _enforce_schema(current, schema, tool_name, violations)

        # Layer B: output integrity (compare cleaned reported vs. actual)
        if run_b:
            _check_integrity(current, actual_output, tool_name, violations)

        clean_output.append({"tool_name": tool_name, "output": current})

    # Layer C: hallucination detection (single pass over commentary)
    if run_c and assistant_commentary:
        _check_hallucination(assistant_commentary, violations)

    # ── Summary ─────────────────────────────────────────────────────────────
    def _count(vtype: str) -> int:
        return sum(1 for v in violations if v["type"] == vtype)

    parts = [f"Processed {len(tool_responses)} tool response(s). Mode: {mode}."]
    if run_a:
        parts.append(f"[A] Schema violations: {_count('schema_violation')}.")
    if run_b:
        parts.append(f"[B] Integrity violations: {_count('output_integrity_violation')}.")
    if run_c:
        parts.append(f"[C] Hallucination violations: {_count('tool_hallucination')}.")
    parts.append(f"Total violations: {len(violations)}.")

    return {
        "clean_output": clean_output,
        "violations": violations,
        "summary": " ".join(parts),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
