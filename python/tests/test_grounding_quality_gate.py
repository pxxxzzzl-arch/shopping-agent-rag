"""The fourth report must fail the same CLI used by CI when evidence regresses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shopping_agent.quality_gate import (
    GROUNDING_CATALOG_SHA256, GROUNDING_LABELS_SHA256, main, validate,
)


def _old_reports():
    original = {
        "case_count": 38, "error_count": 0, "errors": [],
        "category_hit_rate": 1.0, "in_stock_rate": 1.0,
        "source_attachment_rate": 1.0, "expected_presence_accuracy": 1.0,
        "hard_constraint_pass_rate": 1.0,
    }

    def catalog(count, no_answer, returned):
        modes = {}
        for mode in ("bm25", "vector", "hybrid"):
            modes[mode] = {
                "requested_retrieval_mode": mode,
                "actual_product_retrieval_modes": {mode: count - no_answer},
                "actual_product_retrieval_mode_missing_count": no_answer,
                "actual_product_retrieval_mode_mismatch_count": 0,
                "exact_set_cases": count, "safe_unanswerable_cases": no_answer,
                "returned_items": returned, "correct_returned_items": returned,
                "error_count": 0, "errors": [],
            }
        return {"label_count": count, "unanswerable_cases": no_answer, "modes": modes}

    return original, catalog(24, 6, 39), catalog(18, 4, 23)


def _grounding():
    modes = {}
    for mode in ("bm25", "vector", "hybrid"):
        cases = []
        for index in range(36):
            cases.append({
                "case_id": f"synthetic-{index:02d}", "exact_set": True,
                "supported_gold_atoms": 100 if index == 0 else 0,
                "covered_gold_atoms": 99 if index == 0 else 0,
                "predicted_support_claims": 130 if index == 0 else 0,
                "erroneous_support_claims": 41 if index == 0 else 0,
                "no_answer_safe": True if index >= 27 else None,
                "citations": [{"valid": True} for _ in range(183)] if index == 0 else [],
                "valid_citations": 183 if index == 0 else 0,
            })
        modes[mode] = {
            "case_count": 36, "error_count": 0,
            "exact_set_cases": 36, "exact_set_denominator": 36,
            "route_matches": 36, "effective_mode_counts": {mode: 30},
            "supported_atomic_coverage": {"numerator": 99, "denominator": 100},
            "erroneous_support": {"numerator": 41, "denominator": 130},
            "current_valid_citations": {"numerator": 183, "denominator": 183},
            "no_answer_abstention": {"numerator": 9, "denominator": 9},
            "cases": cases,
        }
    return {"synthetic": True, "catalog_sha256": GROUNDING_CATALOG_SHA256,
            "labels_sha256": GROUNDING_LABELS_SHA256, "modes": modes}


def test_fourth_report_baseline_passes_with_old_thresholds_intact():
    assert validate(*_old_reports(), grounding=_grounding()) == []


@pytest.mark.parametrize(("mutation", "field"), [
    ("missing_citation_counts", "current_valid_citations"),
    ("missing_mode", "grounding.modes"),
    ("invalid_reference", "invalid original-text reference"),
    ("lower_citation_count", "current_valid_citations"),
    ("lower_atomic_coverage", "supported_atomic_coverage"),
    ("more_unsupported", "erroneous_support"),
    ("runtime_error", "error_count"),
])
def test_fourth_report_regressions_fail_closed(mutation, field):
    report = _grounding()
    bm25 = report["modes"]["bm25"]
    if mutation == "missing_citation_counts":
        del bm25["current_valid_citations"]
    elif mutation == "missing_mode":
        del report["modes"]["hybrid"]
    elif mutation == "invalid_reference":
        bm25["cases"][0]["citations"][0]["valid"] = False
    elif mutation == "lower_citation_count":
        bm25["current_valid_citations"]["numerator"] -= 1
    elif mutation == "lower_atomic_coverage":
        bm25["supported_atomic_coverage"]["numerator"] = 88
        bm25["cases"][0]["covered_gold_atoms"] = 88
    elif mutation == "more_unsupported":
        bm25["erroneous_support"]["numerator"] = 42
        bm25["cases"][0]["erroneous_support_claims"] = 42
    elif mutation == "runtime_error":
        bm25["error_count"] = 1
    assert any(field in failure for failure in validate(*_old_reports(), grounding=report))


def test_cli_rejects_damaged_fourth_report(tmp_path, capsys):
    paths = []
    for name, report in zip(("original", "transfer", "constraint"), _old_reports()):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report))
        paths.extend((f"--{name}", str(path)))
    grounding = tmp_path / "grounding.json"
    report = _grounding()
    grounding.write_text(json.dumps(report))
    assert main([*paths, "--grounding", str(grounding)]) == 0
    report["modes"]["hybrid"]["current_valid_citations"]["numerator"] -= 1
    grounding.write_text(json.dumps(report))
    assert main([*paths, "--grounding", str(grounding)]) == 1
    assert "grounding.hybrid.current_valid_citations" in capsys.readouterr().err


def test_ci_runs_fourth_report_and_passes_it_to_gate():
    workflow = (Path(__file__).parents[2] / ".github/workflows/shopping-agent.yml").read_text()
    assert "python -m shopping_agent.grounding_evaluation" in workflow
    assert '--grounding "$RUNNER_TEMP/shopping-grounding.json"' in workflow
    assert "--ignore=tests/test_ab_test.py" in workflow
