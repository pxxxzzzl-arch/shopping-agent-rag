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
GROUNDING_CATALOG_SHA256 = "de6fbd9ce7aa64c2bb1602e067d1572039e50f9776eb6413323d7df67af9cbdd"
GROUNDING_LABELS_SHA256 = "a9db2cbbf967eb394fcabd8e0f50edc870e3557dbdb629692c3d6de88f959091"
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


def _grounding(report: Mapping[str, Any], failures: list[str]) -> None:
    name = "grounding"
    if report.get("catalog_sha256") != GROUNDING_CATALOG_SHA256:
        failures.append(f"{name}.catalog_sha256: frozen catalog hash mismatch")
    if report.get("labels_sha256") != GROUNDING_LABELS_SHA256:
        failures.append(f"{name}.labels_sha256: frozen labels hash mismatch")
    if report.get("synthetic") is not True:
        failures.append(f"{name}.synthetic: expected true")
    modes = _mapping(report.get("modes"), f"{name}.modes", failures)
    if set(modes) != MODES:
        failures.append(f"{name}.modes: expected {sorted(MODES)}, got {sorted(modes)}")
    for mode in sorted(MODES & set(modes)):
        prefix = f"{name}.{mode}"
        item = _mapping(modes[mode], prefix, failures)
        _integer(item, "case_count", 36, prefix, failures)
        _integer(item, "error_count", 0, prefix, failures)
        _integer(item, "exact_set_cases", 36, prefix, failures)
        _integer(item, "exact_set_denominator", 36, prefix, failures)
        _integer(item, "route_matches", 36, prefix, failures)
        if item.get("effective_mode_counts") != {mode: 30}:
            failures.append(f"{prefix}.effective_mode_counts: expected {{{mode!r}: 30}}")
        coverage = _mapping(item.get("supported_atomic_coverage"), f"{prefix}.supported_atomic_coverage", failures)
        _integer(coverage, "denominator", 100, f"{prefix}.supported_atomic_coverage", failures)
        covered = coverage.get("numerator")
        if type(covered) is not int or not 89 <= covered <= 100:
            failures.append(f"{prefix}.supported_atomic_coverage.numerator: expected 89..100, got {covered!r}")
        unsupported = _mapping(item.get("erroneous_support"), f"{prefix}.erroneous_support", failures)
        bad = unsupported.get("numerator")
        predicted = unsupported.get("denominator")
        if type(bad) is not int or not 0 <= bad <= 41:
            failures.append(f"{prefix}.erroneous_support.numerator: expected 0..41, got {bad!r}")
        if type(predicted) is not int or predicted < 130:
            failures.append(f"{prefix}.erroneous_support.denominator: expected at least 130, got {predicted!r}")
        citations = _mapping(item.get("current_valid_citations"), f"{prefix}.current_valid_citations", failures)
        valid = citations.get("numerator")
        total = citations.get("denominator")
        if type(total) is not int or total < 183 or type(valid) is not int or valid != total:
            failures.append(f"{prefix}.current_valid_citations: expected all of at least 183 citations valid, got {valid!r}/{total!r}")
        abstention = _mapping(item.get("no_answer_abstention"), f"{prefix}.no_answer_abstention", failures)
        _integer(abstention, "numerator", 9, f"{prefix}.no_answer_abstention", failures)
        _integer(abstention, "denominator", 9, f"{prefix}.no_answer_abstention", failures)
        cases = item.get("cases")
        if not isinstance(cases, list) or len(cases) != 36 or not all(isinstance(row, dict) for row in cases):
            failures.append(f"{prefix}.cases: expected 36 per-question objects")
            continue
        if len({row.get("case_id") for row in cases}) != 36:
            failures.append(f"{prefix}.cases: duplicate or missing case IDs")
        checks = (
            ("exact_set_cases", sum(row.get("exact_set") is True for row in cases)),
            ("supported_atomic_coverage.denominator", sum(row.get("supported_gold_atoms", 0) for row in cases)),
            ("supported_atomic_coverage.numerator", sum(row.get("covered_gold_atoms", 0) for row in cases)),
            ("erroneous_support.denominator", sum(row.get("predicted_support_claims", 0) for row in cases)),
            ("erroneous_support.numerator", sum(row.get("erroneous_support_claims", 0) for row in cases)),
            ("no_answer_abstention.denominator", sum(row.get("no_answer_safe") is not None for row in cases)),
            ("no_answer_abstention.numerator", sum(row.get("no_answer_safe") is True for row in cases)),
            ("current_valid_citations.denominator", sum(len(row.get("citations", [])) for row in cases)),
            ("current_valid_citations.numerator", sum(sum(ref.get("valid") is True for ref in row.get("citations", [])) for row in cases)),
        )
        expected_values = {
            "exact_set_cases": item.get("exact_set_cases"),
            "supported_atomic_coverage.denominator": coverage.get("denominator"),
            "supported_atomic_coverage.numerator": coverage.get("numerator"),
            "erroneous_support.denominator": unsupported.get("denominator"),
            "erroneous_support.numerator": unsupported.get("numerator"),
            "no_answer_abstention.denominator": abstention.get("denominator"),
            "no_answer_abstention.numerator": abstention.get("numerator"),
            "current_valid_citations.denominator": total,
            "current_valid_citations.numerator": valid,
        }
        for field, actual in checks:
            if type(actual) is not int or actual != expected_values[field]:
                failures.append(f"{prefix}.{field}: per-question total {actual!r} disagrees with summary {expected_values[field]!r}")
        for row in cases:
            refs = row.get("citations")
            if not isinstance(refs, list) or not all(isinstance(ref, dict) for ref in refs):
                failures.append(f"{prefix}.{row.get('case_id')}.citations: invalid list")
                continue
            if any(ref.get("valid") is not True for ref in refs):
                failures.append(f"{prefix}.{row.get('case_id')}.citations: invalid original-text reference")
            if row.get("valid_citations") != len(refs):
                failures.append(f"{prefix}.{row.get('case_id')}.valid_citations: expected {len(refs)}")


def validate(
    original: Mapping[str, Any], transfer: Mapping[str, Any],
    constraint: Mapping[str, Any], grounding: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return all failed checks; an absent field never silently passes."""

    failures: list[str] = []
    _original(_mapping(original, "original", failures), failures)
    _catalog(_mapping(transfer, "transfer", failures), "transfer", 24, 6, failures)
    _catalog(_mapping(constraint, "constraint", failures), "constraint", 18, 4, failures)
    if grounding is not None:
        _grounding(_mapping(grounding, "grounding", failures), failures)
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
    parser.add_argument("--grounding", type=Path, help="Fourth synthetic grounding report")
    args = parser.parse_args(argv)
    try:
        failures = validate(
            _read(args.original, "original"),
            _read(args.transfer, "transfer"),
            _read(args.constraint, "constraint"),
            _read(args.grounding, "grounding") if args.grounding is not None else None,
        )
    except ValueError as exc:
        failures = [str(exc)]
    if failures:
        for failure in failures:
            print(f"quality gate failed: {failure}", file=sys.stderr)
        return 1
    suffix = "; grounding 36/36 with current citations" if args.grounding is not None else ""
    print(f"quality gate PASS: original 38/0; transfer 24/24; constraint 18/18; all modes{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
