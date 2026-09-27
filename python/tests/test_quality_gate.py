"""The offline quality command must fail closed on incomplete or worse reports."""

from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest


def _reports():
    original = {
        "case_count": 38, "error_count": 0, "errors": [],
        "category_hit_rate": 1.0, "in_stock_rate": 1.0,
        "source_attachment_rate": 1.0, "expected_presence_accuracy": 1.0,
        "hard_constraint_pass_rate": 1.0,
    }

    def catalog(count, no_answer, returned):
        mode = {
            "exact_set_cases": count,
            "correct_returned_items": returned,
            "returned_items": returned,
            "safe_unanswerable_cases": no_answer,
            "actual_product_retrieval_mode_mismatch_count": 0,
            "error_count": 0, "errors": [],
        }
        modes = {}
        for name in ("bm25", "vector", "hybrid"):
            item = copy.deepcopy(mode)
            item["requested_retrieval_mode"] = name
            item["actual_product_retrieval_modes"] = {name: count - no_answer}
            item["actual_product_retrieval_mode_missing_count"] = no_answer
            modes[name] = item
        return {
            "label_count": count, "unanswerable_cases": no_answer,
            "modes": modes,
        }

    return original, catalog(24, 6, 39), catalog(18, 4, 23)


def _run_gate(tmp_path, reports):
    paths = []
    for name, report in zip(("original", "transfer", "constraint"), reports):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    return subprocess.run(
        [sys.executable, "-m", "shopping_agent.quality_gate",
         "--original", str(paths[0]), "--transfer", str(paths[1]),
         "--constraint", str(paths[2])],
        text=True, capture_output=True, check=False,
    )


def test_quality_gate_accepts_complete_baseline(tmp_path):
    result = _run_gate(tmp_path, _reports())
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("lower_one_mode", "transfer.vector.exact_set_cases"),
        ("remove_field", "constraint.hybrid.exact_set_cases"),
        ("add_error", "original.error_count"),
        ("drop_mode", "transfer.modes"),
    ],
)
def test_quality_gate_rejects_bad_report(tmp_path, mutation, expected_error):
    reports = list(_reports())
    if mutation == "lower_one_mode":
        reports[1]["modes"]["vector"]["exact_set_cases"] -= 1
    elif mutation == "remove_field":
        del reports[2]["modes"]["hybrid"]["exact_set_cases"]
    elif mutation == "add_error":
        reports[0]["error_count"] = 1
        reports[0]["errors"] = ["synthetic failure"]
    elif mutation == "drop_mode":
        del reports[1]["modes"]["hybrid"]
    result = _run_gate(tmp_path, reports)
    assert result.returncode == 1, result.stderr
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("empty_actual_modes", "transfer.vector.actual_product_retrieval_modes"),
        ("fallback_actual_mode", "transfer.vector.actual_product_retrieval_modes"),
        ("missing_count_too_high", "constraint.hybrid.actual_product_retrieval_mode_missing_count"),
        ("missing_count_absent", "constraint.hybrid.actual_product_retrieval_mode_missing_count"),
        ("actual_modes_absent", "transfer.vector.actual_product_retrieval_modes"),
        ("requested_mode_wrong", "transfer.vector.requested_retrieval_mode"),
    ],
)
def test_quality_gate_rejects_unverified_retrieval_mode(tmp_path, mutation, expected_error):
    reports = list(_reports())
    transfer_vector = reports[1]["modes"]["vector"]
    constraint_hybrid = reports[2]["modes"]["hybrid"]
    if mutation == "empty_actual_modes":
        transfer_vector["actual_product_retrieval_modes"] = {}
    elif mutation == "fallback_actual_mode":
        transfer_vector["actual_product_retrieval_modes"] = {"bm25": 18}
    elif mutation == "missing_count_too_high":
        constraint_hybrid["actual_product_retrieval_mode_missing_count"] = 18
    elif mutation == "missing_count_absent":
        del constraint_hybrid["actual_product_retrieval_mode_missing_count"]
    elif mutation == "actual_modes_absent":
        del transfer_vector["actual_product_retrieval_modes"]
    elif mutation == "requested_mode_wrong":
        transfer_vector["requested_retrieval_mode"] = "bm25"
    result = _run_gate(tmp_path, reports)
    assert result.returncode == 1, result.stderr
    assert expected_error in result.stderr
