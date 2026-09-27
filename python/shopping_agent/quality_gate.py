"""Fail CI when the frozen synthetic offline acceptance reports regress.

This module uses only the Python standard library. Its thresholds are the
documented development-set baselines, not claims about unseen or online data.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


MODES = frozenset({"bm25", "vector", "hybrid"})
ORIGINAL_RATES = (
    "category_hit_rate", "in_stock_rate", "source_attachment_rate",
    "expected_presence_accuracy", "hard_constraint_pass_rate",
)


def _mapping(value: object, name: str, failures: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        failures.append(f"{name}: required JSON object is missing or invalid")
        return {}
    return value


def _integer(
    report: Mapping[str, Any], key: str, expected: int, name: str,
    failures: list[str],
) -> None:
    value = report.get(key)
    if type(value) is not int or value != expected:
        failures.append(f"{name}.{key}: expected {expected}, got {value!r}")


def _no_errors(report: Mapping[str, Any], name: str, failures: list[str]) -> None:
    _integer(report, "error_count", 0, name, failures)
    if report.get("errors") != []:
        failures.append(f"{name}.errors: expected an empty list")


def _original(report: Mapping[str, Any], failures: list[str]) -> None:
    _integer(report, "case_count", 38, "original", failures)
    _no_errors(report, "original", failures)
    for key in ORIGINAL_RATES:
        value = report.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or value != 1:
            failures.append(f"original.{key}: expected 1.0, got {value!r}")


def _catalog(
    report: Mapping[str, Any], name: str, count: int, no_answer: int,
    failures: list[str],
) -> None:
    _integer(report, "label_count", count, name, failures)
    _integer(report, "unanswerable_cases", no_answer, name, failures)
    modes = _mapping(report.get("modes"), f"{name}.modes", failures)
    if set(modes) != MODES:
        failures.append(
            f"{name}.modes: expected {sorted(MODES)}, got {sorted(modes)}"
        )
    for mode in sorted(MODES & set(modes)):
        prefix = f"{name}.{mode}"
        item = _mapping(modes[mode], prefix, failures)
        if item.get("requested_retrieval_mode") != mode:
            failures.append(
                f"{prefix}.requested_retrieval_mode: expected {mode!r}, "
                f"got {item.get('requested_retrieval_mode')!r}"
            )
        _integer(
            item, "actual_product_retrieval_mode_missing_count",
            no_answer, prefix, failures,
        )
        actual_modes = item.get("actual_product_retrieval_modes")
        expected_actual_modes = {mode: count - no_answer}
        if actual_modes != expected_actual_modes:
            failures.append(
                f"{prefix}.actual_product_retrieval_modes: expected "
                f"{expected_actual_modes!r}, got {actual_modes!r}"
            )
        _integer(item, "exact_set_cases", count, prefix, failures)
        _integer(item, "safe_unanswerable_cases", no_answer, prefix, failures)
        _integer(item, "actual_product_retrieval_mode_mismatch_count", 0, prefix, failures)
        _no_errors(item, prefix, failures)
        returned = item.get("returned_items")
        correct = item.get("correct_returned_items")
        if (
            type(returned) is not int or returned <= 0
            or type(correct) is not int or correct != returned
        ):
            failures.append(
                f"{prefix}.correct_returned_items: expected all returned items "
                f"to be eligible, got {correct!r}/{returned!r}"
            )


def validate(
    original: Mapping[str, Any], transfer: Mapping[str, Any],
    constraint: Mapping[str, Any],
) -> list[str]:
    """Return all failed checks; an absent field never silently passes."""

    failures: list[str] = []
    _original(_mapping(original, "original", failures), failures)
    _catalog(_mapping(transfer, "transfer", failures), "transfer", 24, 6, failures)
    _catalog(_mapping(constraint, "constraint", failures), "constraint", 18, 4, failures)
    return failures


def _read(path: Path, name: str) -> Mapping[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name}: cannot read a valid JSON report: {type(exc).__name__}") from exc
    if not isinstance(report, dict):
        raise ValueError(f"{name}: report root must be a JSON object")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--transfer", required=True, type=Path)
    parser.add_argument("--constraint", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        failures = validate(
            _read(args.original, "original"),
            _read(args.transfer, "transfer"),
            _read(args.constraint, "constraint"),
        )
    except ValueError as exc:
        failures = [str(exc)]
    if failures:
        for failure in failures:
            print(f"quality gate failed: {failure}", file=sys.stderr)
        return 1
    print("quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
