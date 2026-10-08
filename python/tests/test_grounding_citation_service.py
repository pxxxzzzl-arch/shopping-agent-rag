"""Real-service regression checks against frozen, independent source text."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from shopping_agent.answer import AnswerComposer
from shopping_agent.config import Settings
from shopping_agent.grounding_evaluation import validate_atom_evidence, validate_reference
from shopping_agent.schemas import ShopRequest
from shopping_agent.storage import CatalogStore
from shopping_agent.workflow import ShoppingService


DATA = Path(__file__).parents[1] / "shopping_agent/data"


def _service():
    settings = Settings(database_url="sqlite://", seed_demo=False)
    store = CatalogStore(settings.database_url)
    store.initialize()
    assert store.import_documents_jsonl(DATA / "grounding_v1_catalog.jsonl") == 30
    return ShoppingService(store, AnswerComposer(settings), settings)


def _ask(service, case):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="citation-" + case["case_id"], query=case["query"],
        num_items=case["num_items"], retrieval_mode="bm25",
    )))


def _cases():
    return [json.loads(line) for line in (DATA / "grounding_v1_labels.jsonl").read_text().splitlines()]


def test_product_reason_quotes_current_import_line():
    service = _service()
    response = _ask(service, _cases()[0])
    assert [item.product_id for item in response.recommendations] == ["G4-H01"]
    assert response.recommendations[0].evidence
    for item in response.recommendations:
        for ref in item.evidence:
            assert validate_reference(service.store, ref, item.product_id)["valid"]


def test_negative_anc_claim_uses_negative_current_evidence():
    service = _service()
    case = _cases()[1]
    response = _ask(service, case)
    assert {item.product_id for item in response.recommendations} == set(case["eligible_product_ids"])
    for item in response.recommendations:
        atom = next(atom for atom in case["atomic_conditions"] if atom["subject_id"] == item.product_id and atom["field"] == "anc")
        judgment = next(judgment for judgment in item.condition_judgments if judgment.field == "anc")
        assert judgment.operator == "absent" and judgment.status == "supported"
        assert any(validate_atom_evidence(service.store, ref, atom)["valid"] for ref in judgment.evidence)
        assert all(validate_reference(service.store, ref, item.product_id)["valid"] for ref in item.evidence)


def test_negative_faq_policy_quotes_current_answer_not_joined_fields():
    service = _service()
    case = next(case for case in _cases() if case["case_id"] == "G4-Q30")
    response = _ask(service, case)
    assert response.route == "faq" and len(response.knowledge_evidence) == 1
    atom = case["atomic_conditions"][0]
    ref = response.knowledge_evidence[0]
    assert validate_atom_evidence(service.store, ref, atom)["valid"]
    assert ref.excerpt in service.store.get_document(ref.source_id).original_text
    assert "不提供可用于真实交易的优惠券" in response.answer


def test_all_fourth_catalog_response_citations_are_verbatim_and_not_dropped():
    service = _service()
    count = 0
    for case in _cases():
        response = _ask(service, case)
        for item in response.recommendations:
            for ref in item.evidence:
                assert validate_reference(service.store, ref, item.product_id)["valid"], case["case_id"]
                count += 1
            for judgment in item.condition_judgments:
                for ref in judgment.evidence:
                    assert validate_reference(service.store, ref, item.product_id)["valid"], case["case_id"]
                    count += 1
        for ref in response.knowledge_evidence:
            source = service.store.get_document(ref.source_id)
            assert source is not None and source.faq_id is not None
            assert validate_reference(service.store, ref, source.faq_id)["valid"], case["case_id"]
            count += 1
    assert count >= 183
