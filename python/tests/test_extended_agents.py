"""Conditional roles, persistent turns, source validation and failure behavior."""

from __future__ import annotations

import asyncio
import json
import time

from shopping_agent.agent_roles import KnowledgeAgent, KnowledgeInput
from shopping_agent.config import Settings
from shopping_agent.storage import CatalogStore
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _ask(service, query, **kwargs):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="route-test-user", query=query, num_items=2, **kwargs
    )))


def test_three_routes_execute_different_tools_and_show_sources():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    product = _ask(service, "推荐可通勤的主动降噪耳机")
    faq = _ask(service, "演示商品的库存是实时的吗？")
    mixed = _ask(service, "推荐有货耳机，并说明库存是否实时")

    assert (product.route, faq.route, mixed.route) == ("product", "faq", "mixed")
    assert product.recommendations and product.knowledge_evidence == []
    assert faq.recommendations == [] and faq.knowledge_evidence
    assert mixed.recommendations and mixed.knowledge_evidence
    assert product.tool_trace != faq.tool_trace != mixed.tool_trace
    assert any(tool.startswith("product_retrieval:") for tool in product.tool_trace)
    assert any(tool.startswith("faq_retrieval:") for tool in faq.tool_trace)
    assert any(tool.startswith("faq_retrieval:") for tool in mixed.tool_trace)
    assert product.routing_reason and faq.routing_reason and mixed.routing_reason
    assert all(
        service.store.get_document(ref.source_id) is not None
        for response in (product, faq, mixed)
        for item in response.recommendations
        for ref in item.evidence
    )


def test_multi_turn_state_survives_service_recreation_and_rechecks_stock(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'turns.sqlite3'}"
    first_service = create_service(Settings(database_url=database_url))
    first = _ask(
        first_service, "推荐一款地铁用的降噪耳机",
        conversation_id="session-one",
    )
    assert first.recommendations
    previous_ids = {item.product_id for item in first.recommendations}

    second_service = create_service(Settings(database_url=database_url))
    followup = _ask(
        second_service, "这款现在还有货吗？",
        conversation_id="session-one",
    )
    assert followup.route == "product"
    assert followup.recommendations
    assert {item.product_id for item in followup.recommendations} <= previous_ids
    assert second_service.conversations.recent("route-test-user", "session-one")[-1].query == (
        "这款现在还有货吗？"
    )

    for product_id in previous_ids:
        second_service.store.delete_product(product_id)
    sold_out_followup = _ask(
        second_service, "这款现在还有货吗？",
        conversation_id="session-one",
    )
    assert sold_out_followup.recommendations == []
    assert "没有满足条件" in sold_out_followup.answer


def test_unknown_or_conflicting_faq_abstains_without_citation(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "faq", "faq_id": faq_id,
            "question": "商品退货期限是多少天？",
            "answer": answer, "data_origin": "synthetic_demo",
        }
        for faq_id, answer in (
            ("CONFLICT-1", "可以在七天内退货。"),
            ("CONFLICT-2", "不能在七天内退货。"),
        )
    ]
    path = tmp_path / "conflicting_faq.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
    service.store.import_documents_jsonl(path)
    service.refresh_index()

    conflict = _ask(service, "商品退货期限是多少天？")
    unknown = _ask(service, "运费险的赔付金额是多少？")
    assert conflict.route == unknown.route == "faq"
    assert conflict.knowledge_evidence == []
    assert "冲突" in conflict.answer or "无法核实" in conflict.answer
    assert unknown.knowledge_evidence == []
    assert "未找到" in unknown.answer


def test_paraphrased_same_policy_with_opposite_answers_abstains(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "faq", "faq_id": "SHIPPING-1",
            "question": "退货需要运费吗？", "answer": "退货免运费。",
            "data_origin": "synthetic_demo",
        },
        {
            "kind": "faq", "faq_id": "SHIPPING-2",
            "question": "退款时运费谁承担？", "answer": "退货运费由顾客支付。",
            "data_origin": "synthetic_demo",
        },
    ]
    path = tmp_path / "shipping.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
    service.store.import_documents_jsonl(path)
    service.refresh_index()
    response = _ask(service, "退货运费谁付？")
    assert response.route == "faq"
    assert response.knowledge_evidence == []
    assert "冲突" in response.answer


def test_deleted_faq_stale_index_cannot_be_cited():
    service = create_service(Settings(database_url="sqlite://"))
    before = _ask(service, "演示商品的库存是实时的吗？")
    assert before.knowledge_evidence
    source_id = before.knowledge_evidence[0].source_id
    assert service.store.delete_faq("DEMO-FAQ-03")
    after = _ask(service, "演示商品的库存是实时的吗？")
    assert service.store.get_document(source_id) is None
    assert all(ref.source_id != source_id for ref in after.knowledge_evidence)
    assert "未找到" in after.answer


def test_external_faq_update_rebuilds_index_and_never_cites_old_version(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    path = tmp_path / "live-faq.jsonl"
    row = {
        "kind": "faq", "faq_id": "LIVE-FAQ",
        "question": "退货运费由谁承担？", "answer": "请以授权政策为准。",
        "data_origin": "synthetic_demo",
    }
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    first = _ask(service, "退货运费由谁承担？")
    assert [ref.source_id for ref in first.knowledge_evidence] == ["LIVE-FAQ:answer"]

    row["answer"] = "新的演示说明：该情形的运费由买家承担。"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    second = _ask(service, "退货运费由谁承担？")
    assert [ref.source_id for ref in second.knowledge_evidence] == [
        "LIVE-FAQ:answer:v2"
    ]
    assert service.store.get_document("LIVE-FAQ:answer") is None


def test_negation_no_answer_and_stock_rules_reject_unfounded_items():
    service = create_service(Settings(database_url="sqlite://"))
    no_noise = _ask(service, "只要不带主动降噪的耳机，运动时还要听见环境声")
    no_fold = _ask(service, "请给我一部有现货的折叠屏手机")
    no_ports = _ask(service, "三口充电器的接口必须全是 USB-C")
    no_projector = _ask(service, "需要内置投影仪和 8K 的显示器")
    no_teleport = _ask(service, "有没有量子传送功能的手机？")
    no_moon = _ask(service, "想买能瞬间传送到月球的手机")
    assert no_noise.recommendations
    assert all(
        not any("降噪" in tag for tag in service.store.get_product(item.product_id).tags)
        for item in no_noise.recommendations
    )
    assert no_fold.recommendations == []
    assert no_ports.recommendations == []
    assert no_projector.recommendations == []
    assert no_teleport.recommendations == []
    assert no_moon.recommendations == []
    assert "缺少证据" in no_teleport.answer
    assert all(item.stock > 0 for item in no_noise.recommendations)


def test_unverified_negative_and_capability_requests_abstain():
    service = create_service(Settings(database_url="sqlite://"))
    for query in (
        "推荐没有摄像头的手机",
        "推荐不要摄像头的手机",
        "推荐一款会飞的手机",
        "推荐续航 72 小时的耳机",
    ):
        response = _ask(service, query)
        assert response.recommendations == [], query
        assert not any(item.evidence for item in response.recommendations)
    meeting = _ask(service, "帮我选会议用的耳机")
    assert meeting.recommendations
    single_charge = _ask(service, "推荐单次续航 20 小时的耳机")
    assert single_charge.recommendations == []
    no_anc = _ask(service, "推荐不支持主动降噪的耳机")
    assert no_anc.recommendations
    assert all(
        "主动降噪" not in service.store.get_product(item.product_id).tags
        for item in no_anc.recommendations
    )


def test_explicitly_documented_absence_can_satisfy_a_negative_request(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "kind": "product", "product_id": "CAMERALESS-1",
        "name": "无摄像头演示手机", "category": "手机", "price": 299,
        "stock": 3, "description": "合成演示商品。没有摄像头的基础手机。",
        "tags": ["无摄像头"], "reviews": [], "data_origin": "synthetic_demo",
    }
    path = tmp_path / "camera-free.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    response = _ask(service, "想要没有摄像头的手机")
    assert [item.product_id for item in response.recommendations] == ["CAMERALESS-1"]
    assert response.recommendations[0].evidence


def test_conflicting_product_description_and_review_are_not_recommended(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "kind": "product", "product_id": "CONFLICT-1",
        "name": "演示降噪耳机", "category": "耳机", "price": 99,
        "stock": 10, "description": "合成演示商品。支持主动降噪。",
        "tags": ["主动降噪"],
        "reviews": ["演示评价：这款没有主动降噪，商家介绍有误。"],
        "data_origin": "synthetic_demo",
    }
    path = tmp_path / "conflicting-product.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    response = _ask(service, "推荐支持主动降噪的耳机")
    assert response.recommendations == []
    assert "冲突" in response.answer
    assert any("冲突" in warning for warning in response.warnings)


def test_distinct_capabilities_and_accessories_are_not_false_conflicts(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product", "product_id": "WIRELESS-1",
            "name": "演示无线耳机", "category": "耳机", "price": 89,
            "stock": 2, "description": "合成演示商品。蓝牙无线连接。没有无线充电。",
            "tags": ["无线", "蓝牙"], "reviews": [],
            "data_origin": "synthetic_demo",
        },
        {
            "kind": "product", "product_id": "PEN-1",
            "name": "演示手写平板", "category": "平板", "price": 399,
            "stock": 2, "description": "合成演示商品。支持手写笔输入。包装内没有手写笔，须另购。",
            "tags": ["手写笔"], "reviews": [],
            "data_origin": "synthetic_demo",
        },
    ]
    path = tmp_path / "distinct-capabilities.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows))
    service.store.import_documents_jsonl(path)
    wireless = _ask(service, "推荐无线耳机")
    pen = _ask(service, "推荐支持手写笔的平板")
    assert any(item.product_id == "WIRELESS-1" for item in wireless.recommendations)
    assert any(item.product_id == "PEN-1" for item in pen.recommendations)
    assert not any("冲突" in warning for warning in wireless.warnings + pen.warnings)


def test_description_capability_overrides_missing_tag_for_explicit_constraints(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "kind": "product", "product_id": "UNTAGGED-ANC",
        "name": "演示耳机", "category": "耳机", "price": 199,
        "stock": 5, "description": "合成演示商品。支持主动降噪。",
        "tags": ["蓝牙"], "reviews": [], "data_origin": "synthetic_demo",
    }
    path = tmp_path / "untagged.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    denied = _ask(service, "推荐不要降噪的耳机")
    wanted = _ask(service, "推荐支持主动降噪的耳机")
    assert denied.recommendations == []
    assert [item.product_id for item in wanted.recommendations] == ["UNTAGGED-ANC"]


def test_followup_uses_last_product_turn_even_after_faq(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'after-faq.sqlite3'}"
    service = create_service(Settings(database_url=database_url))
    first = _ask(service, "推荐一款显示器", conversation_id="triplet")
    assert first.recommendations
    faq = _ask(service, "演示商品是真实的吗？", conversation_id="triplet")
    assert faq.route == "faq" and faq.recommendations == []
    followup = _ask(service, "那款还能买吗？", conversation_id="triplet")
    assert followup.route == "product"
    assert followup.recommendations
    assert {item.product_id for item in followup.recommendations} <= {
        item.product_id for item in first.recommendations
    }


def test_legacy_unverified_import_is_not_served_as_authorized_product(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "product_id": "UNVERIFIED-1", "name": "测试耳机", "category": "耳机",
        "price": 99, "stock": 10, "description": "未经来源核验的资料",
        "tags": ["蓝牙"], "reviews": [],
    }
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.seed_from_jsonl(path)
    response = _ask(service, "推荐耳机")
    assert service.store.get_product("UNVERIFIED-1") is not None
    assert response.recommendations == []


def test_tool_timeout_returns_safe_empty_response():
    service = create_service(Settings(
        database_url="sqlite://", tool_timeout_seconds=0.000001,
    ))
    response = _ask(service, "推荐降噪耳机")
    assert response.recommendations == []
    assert response.llm_used is False
    assert response.warnings
    assert "profile" not in response.tool_trace


def test_slow_faq_source_verification_times_out_without_a_fact():
    class SlowSourceStore(CatalogStore):
        def get_document(self, source_id):
            time.sleep(0.03)
            return super().get_document(source_id)

    seed = create_service(Settings(database_url="sqlite://"))
    # The slow store shares the same in-memory engine so this exercises the
    # real source lookup boundary without replacing KnowledgeAgent logic.
    slow = SlowSourceStore("sqlite://")
    slow.engine = seed.store.engine
    slow._sessions = seed.store._sessions
    agent = KnowledgeAgent(slow, seed.faq_retriever, timeout=0.001)
    result = asyncio.run(agent.run(KnowledgeInput(
        "演示商品的库存是实时的吗？", "bm25"
    )))
    assert result.evidence == ()
    assert "无法核实" in result.answer
    assert any("TimeoutError" in warning for warning in result.warnings)


def test_unverified_source_price_and_discount_never_override_catalog(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "kind": "product", "product_id": "PRICE-1", "name": "演示耳机",
        "category": "耳机", "price": 100, "stock": 4,
        "description": "合成演示商品。蓝牙耳机，来源声称售价1元并打九折。",
        "tags": ["蓝牙"], "reviews": [], "data_origin": "synthetic_demo",
    }
    path = tmp_path / "price.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False))
    service.store.import_documents_jsonl(path)
    response = _ask(service, "推荐蓝牙耳机")
    assert response.recommendations
    assert response.recommendations[0].price == 100
    assert response.recommendations[0].stock == 4
    assert response.recommendations[0].evidence == []
    assert "¥100" in response.answer
    assert "售价1元" not in response.answer
    assert "打九折" not in response.answer
