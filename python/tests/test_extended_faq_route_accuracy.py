"""Regression checks for location-scoped FAQ evidence and mixed shopping intent."""

from __future__ import annotations

import asyncio
import json

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _ask(service, query: str):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="faq-route-user", query=query, num_items=2,
    )))


def _import_faqs(service, tmp_path, *rows: dict[str, str]) -> None:
    path = tmp_path / "route-faqs.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)


def _faq(faq_id: str, question: str, answer: str) -> dict[str, str]:
    return {
        "kind": "faq", "faq_id": faq_id,
        "question": question, "answer": answer,
        "data_origin": "synthetic_demo", "license": "synthetic_demo",
    }


def test_city_specific_delivery_does_not_cite_another_city(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    _import_faqs(
        service, tmp_path,
        _faq("BEIJING", "北京配送多久？", "北京配送预计两天。"),
        _faq("SHANGHAI", "上海配送多久？", "上海配送预计三天。"),
        _faq("RETURNS", "商品退货政策是什么？", "演示商品可在七天内申请退货。"),
    )

    unsupported = _ask(service, "广州配送多久？")
    supported = _ask(service, "请问北京配送多久？")
    generic = _ask(service, "商品退货政策是什么？")

    assert unsupported.route == "faq"
    assert unsupported.knowledge_evidence == []
    assert "未找到" in unsupported.answer
    assert "北京配送预计两天" not in unsupported.answer
    assert "上海配送预计三天" not in unsupported.answer
    assert [ref.source_id for ref in supported.knowledge_evidence] == ["BEIJING:answer"]
    assert [ref.source_id for ref in generic.knowledge_evidence] == ["RETURNS:answer"]


def test_generic_product_recommendation_and_returns_policy_runs_both_agents(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    _import_faqs(
        service, tmp_path,
        _faq("RETURNS", "商品退货政策是什么？", "演示商品可在七天内申请退货。"),
    )

    response = _ask(service, "给我推荐商品，并说明退货政策")

    assert response.route == "mixed"
    assert response.recommendations
    assert [ref.source_id for ref in response.knowledge_evidence] == ["RETURNS:answer"]
    assert "七天内申请退货" in response.answer
    assert any(tool.startswith("product_retrieval:") for tool in response.tool_trace)
    assert any(tool.startswith("faq_retrieval:") for tool in response.tool_trace)


def test_get_product_returns_policy_without_purchase_verb_is_faq(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    _import_faqs(
        service, tmp_path,
        _faq("RETURNS", "商品退货政策是什么？", "演示商品可在七天内申请退货。"),
    )

    response = _ask(service, "给我查一下商品退货政策")

    assert response.route == "faq"
    assert response.recommendations == []
    assert [ref.source_id for ref in response.knowledge_evidence] == ["RETURNS:answer"]
