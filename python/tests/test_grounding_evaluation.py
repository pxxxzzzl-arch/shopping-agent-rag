"""Adversarial tests of the independent grounding checker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shopping_agent.grounding_evaluation import main, score_case, validate_atom_evidence, validate_reference
from shopping_agent.schemas import ConditionJudgment, EvidenceRef, Recommendation, ShopResponse
from shopping_agent.storage import CatalogStore


CATALOG = Path(__file__).parents[1] / "shopping_agent/data/grounding_v1_catalog.jsonl"


@pytest.fixture
def store():
    result = CatalogStore("sqlite://")
    result.initialize()
    assert result.import_documents_jsonl(CATALOG) == 30
    return result


def ref(source_id, excerpt, source_type="description"):
    return EvidenceRef(source_id=source_id, source_type=source_type, excerpt=excerpt)


def test_valid_current_product_excerpt(store):
    assert validate_reference(store, ref("G4-H01:description", "单次续航 8 小时"), "G4-H01")["valid"]


def test_other_product_source_id_is_not_support(store):
    assert not validate_reference(store, ref("G4-H02:description", "开放式耳机"), "G4-H01")["valid"]


def test_fabricated_excerpt_is_not_support(store):
    assert not validate_reference(store, ref("G4-H01:description", "单次续航 80 小时"), "G4-H01")["valid"]


def test_claim_excerpt_windows_must_be_in_original_order(store):
    assert not validate_reference(store, ref("G4-H01:description", "单次续航 8 小时 … 合成演示商品"), "G4-H01")["valid"]


def test_valid_split_excerpt_windows(store):
    assert validate_reference(store, ref("G4-H01:description", "合成演示商品 … 单次续航 8 小时"), "G4-H01")["valid"]


def test_wrong_source_type_is_not_support(store):
    assert not validate_reference(store, ref("G4-H01:description", "单次续航 8 小时", "review"), "G4-H01")["valid"]


def test_wrong_subject_snapshot_is_not_support(store):
    sid, excerpt = store.catalog_snapshot_source("G4-H01")
    assert not validate_reference(store, ref(sid, excerpt, "catalog_snapshot"), "G4-H02")["valid"]


def test_stale_snapshot_is_not_support_after_update(store):
    sid, excerpt = store.catalog_snapshot_source("G4-H01")
    row = next(json.loads(line) for line in CATALOG.read_text().splitlines() if '"product_id":"G4-H01"' in line)
    row["price"] += 1
    # A new imported version changes the current price and digest.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "update.jsonl"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
        store.import_documents_jsonl(path)
    assert not validate_reference(store, ref(sid, excerpt, "catalog_snapshot"), "G4-H01")["valid"]


def test_polarity_mismatch_is_not_support(store):
    atom = {"subject_id": "G4-H03", "polarity": "negative", "status": "supported", "evidence": [{"source_id": "G4-H03:review:1", "source_type": "review", "excerpt": "不支持主动降噪", "polarity": "negative"}]}
    assert not validate_atom_evidence(store, ref("G4-H03:description", "支持主动降噪"), atom)["valid"]


def test_valid_negative_review_support(store):
    atom = {"subject_id": "G4-H03", "polarity": "negative", "status": "supported", "evidence": [{"source_id": "G4-H03:review:1", "source_type": "review", "excerpt": "不支持主动降噪", "polarity": "negative"}]}
    assert validate_atom_evidence(store, ref("G4-H03:review:1", "不支持主动降噪", "review"), atom)["valid"]


def test_exact_product_set_does_not_hide_false_atomic_support(store):
    case = json.loads((Path(__file__).parents[1] / "shopping_agent/data/grounding_v1_labels.jsonl").read_text().splitlines()[0])
    item = Recommendation(product_id="G4-H01", name="测试", category="耳机", price=100,
                          stock=1, reason="测试", evidence=[ref("G4-H01:description", "单次续航 80 小时")],
                          condition_judgments=[ConditionJudgment(field="anc", operator="present", expected="anc",
                                                                   status="supported", evidence=[ref("G4-H02:description", "开放式耳机")])])
    response = ShopResponse(request_id="r", user_id="u", answer="", recommendations=[item])
    scored = score_case(case, response, store)
    assert scored["exact_set"]
    assert scored["erroneous_support_claims"] == 1
    assert scored["valid_citations"] == 0
    assert scored["covered_gold_atoms"] == 0


def test_faq_gold_needs_matching_answer_excerpt(store):
    case = json.loads((Path(__file__).parents[1] / "shopping_agent/data/grounding_v1_labels.jsonl").read_text().splitlines()[25])
    assert case["case_id"] == "G4-Q26"
    response = ShopResponse(request_id="r", user_id="u", answer="", route="faq",
                            knowledge_evidence=[ref("G4-FAQ-02:answer", "未满 99 元运费 8 元", "answer")])
    scored = score_case(case, response, store)
    assert scored["covered_gold_atoms"] == 0
    assert scored["valid_citations"] == 1


def test_cli_refuses_overwrite_before_service_call(tmp_path):
    output = tmp_path / "frozen.json"
    output.write_bytes(b"first run")
    with pytest.raises(FileExistsError):
        main(["--output", str(output)])
    assert output.read_bytes() == b"first run"


def _product_response(reference, *, operator="present"):
    item = Recommendation(
        product_id="G4-H01", name="测试", category="耳机", price=399, stock=7,
        reason="测试", evidence=[reference],
        condition_judgments=[ConditionJudgment(
            field="anc", operator=operator, expected="anc", status="supported",
            evidence=[reference],
        )],
    )
    return ShopResponse(request_id="r", user_id="u", answer="", recommendations=[item])


def _label(case_id):
    labels = Path(__file__).parents[1] / "shopping_agent/data/grounding_v1_labels.jsonl"
    return next(case for case in map(json.loads, labels.read_text().splitlines())
                if case["case_id"] == case_id)


def test_case_report_links_predicted_judgment_to_gold_and_citation(store):
    reference = ref("G4-H01:description", "支持主动降噪")
    scored = score_case(_label("G4-Q01"), _product_response(reference), store)
    predicted = scored["condition_predictions"][0]
    assert predicted["prediction_id"] == "G4-H01@0#0"
    assert {key: predicted[key] for key in ("field", "operator", "expected", "unit", "status", "conflict")} == {
        "field": "anc", "operator": "present", "expected": "anc", "unit": None,
        "status": "supported", "conflict": False,
    }
    assert predicted["references"][0] == {
        "source_id": "G4-H01:description", "source_type": "description",
        "excerpt": "支持主动降噪", "current": True, "valid": True, "reason": "",
    }
    assert predicted["matched_gold_atoms"][0]["field"] == "anc"
    assert predicted["matched_gold_atoms"][0]["evidence_supported"] is True
    atom = next(atom for atom in scored["atomic_results"] if atom["field"] == "anc")
    assert atom["matching_prediction_ids"] == ["G4-H01@0#0"]
    assert atom["uncovered_reason"] is None


@pytest.mark.parametrize("bad_reference,operator", [
    (ref("G4-H02:description", "开放式耳机"), "present"),
    (ref("G4-H01:description", "支持主动降噪 80 小时"), "present"),
    (ref("G4-H01:description", "支持主动降噪"), "absent"),
])
def test_bad_response_citation_or_polarity_changes_case_metrics(store, bad_reference, operator):
    case = _label("G4-Q01")
    good = score_case(case, _product_response(ref("G4-H01:description", "支持主动降噪")), store)
    bad = score_case(case, _product_response(bad_reference, operator=operator), store)
    assert bad["covered_gold_atoms"] < good["covered_gold_atoms"]
    assert bad["erroneous_support_claims"] > good["erroneous_support_claims"]
    predicted = bad["condition_predictions"][0]
    assert predicted["unsupported_reason"]
    atom = next(atom for atom in bad["atomic_results"] if atom["field"] == "anc")
    assert atom["uncovered_reason"]


def test_stale_document_response_changes_metrics(store, tmp_path):
    case = _label("G4-Q01")
    old_ref = ref("G4-H01:description", "支持主动降噪")
    good = score_case(case, _product_response(old_ref), store)
    row = next(json.loads(line) for line in CATALOG.read_text().splitlines()
               if '"product_id":"G4-H01"' in line)
    row["price"] += 1
    update = tmp_path / "update.jsonl"
    update.write_text(json.dumps(row, ensure_ascii=False) + "\n")
    store.import_documents_jsonl(update)
    bad = score_case(case, _product_response(old_ref), store)
    assert bad["covered_gold_atoms"] < good["covered_gold_atoms"]
    assert bad["erroneous_support_claims"] > good["erroneous_support_claims"]
    assert bad["condition_predictions"][0]["references"][0]["current"] is False
    assert bad["condition_predictions"][0]["unsupported_reason"] == "missing_or_stale_source"


def test_wrong_faq_response_changes_coverage_and_reports_reason(store):
    case = _label("G4-Q26")
    def response(reference):
        return ShopResponse(request_id="r", user_id="u", answer="", route="faq",
                            knowledge_evidence=[reference])
    good = score_case(case, response(ref("G4-FAQ-01:answer", "签收后 7 天内可申请退货", "answer")), store)
    bad = score_case(case, response(ref("G4-FAQ-02:answer", "未满 99 元运费 8 元", "answer")), store)
    assert bad["covered_gold_atoms"] < good["covered_gold_atoms"]
    atom = bad["atomic_results"][0]
    assert atom["uncovered_reason"] == "wrong_subject"
    assert atom["faq_references"][0]["source_id"] == "G4-FAQ-02:answer"
