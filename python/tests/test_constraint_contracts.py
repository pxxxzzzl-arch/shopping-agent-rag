"""Source-level contracts for explicit shopping constraints on synthetic catalogs."""

from __future__ import annotations

import asyncio
import json

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _service(tmp_path, rows):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = tmp_path / "source-facts.jsonl"
    catalog.write_text("\n".join(json.dumps({
        "kind": "product", "product_id": row[0], "name": row[1],
        "category": row[2], "price": row[3], "stock": row[4],
        "description": row[5], "tags": row[6] if len(row) > 6 else [],
        "reviews": row[7] if len(row) > 7 else [],
        "data_origin": "synthetic_demo",
    }, ensure_ascii=False) for row in rows), encoding="utf-8")
    service.store.import_documents_jsonl(catalog)
    return service


def _ask(service, query, count=5):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="constraint-contract", query=query, num_items=count,
    )))


def _judge(service, query, product_id):
    from shopping_agent.constraints import judge_conditions, parse_conditions

    return judge_conditions(
        parse_conditions(query), service.store.get_product(product_id), service.store,
    )


def _one(judgments, field):
    return next(j for j in judgments if j.field == field)


def test_battery_lower_boundary_is_supported_and_strict_bound_refutes(tmp_path):
    service = _service(tmp_path, [
        ("BAT-5000", "合成手机甲", "手机", 900, 2, "5000mAh 电池。"),
        ("BAT-5100", "合成手机乙", "手机", 950, 2, "5100mAh 电池。"),
    ])
    inclusive = _ask(service, "推荐电池至少 5000mAh 的手机")
    strict = _ask(service, "推荐电池大于 5000mAh 的手机")
    assert {item.product_id for item in inclusive.recommendations} == {"BAT-5000", "BAT-5100"}
    assert [item.product_id for item in strict.recommendations] == ["BAT-5100"]
    judgment = _one(_judge(service, "电池大于 5000mAh 的手机", "BAT-5000"), "battery_mah")
    assert judgment.operator == ">" and judgment.status == "refuted"
    assert judgment.evidence[0].source_id == "BAT-5000:description"


def test_weight_kg_converts_to_grams_and_keeps_inclusive_boundary(tmp_path):
    service = _service(tmp_path, [
        ("GRAM-650", "合成平板甲", "平板", 600, 2, "支持手写笔；重量 650g。"),
        ("GRAM-700", "合成平板乙", "平板", 600, 2, "支持手写笔；重量 700g。"),
    ])
    response = _ask(service, "推荐重量不超过 0.65kg 的平板")
    assert [item.product_id for item in response.recommendations] == ["GRAM-650"]
    judgment = _one(response.recommendations[0].condition_judgments, "weight_g")
    assert judgment.status == "supported" and judgment.unit == "g"
    assert judgment.evidence[0].source_id == "GRAM-650:description"


def test_storage_exact_value_does_not_confuse_ram_with_storage(tmp_path):
    service = _service(tmp_path, [
        ("STORE-512", "合成手机甲", "手机", 900, 2, "8GB 内存；512GB 存储。"),
        ("RAM-512", "合成手机乙", "手机", 900, 2, "512GB 内存；256GB 存储。"),
    ])
    response = _ask(service, "推荐精确 512GB 存储的手机")
    assert [item.product_id for item in response.recommendations] == ["STORE-512"]
    assert _one(_judge(service, "精确 512GB 存储的手机", "RAM-512"), "storage_gb").status == "refuted"


def test_exact_resolution_does_not_accept_another_pixel_pair(tmp_path):
    service = _service(tmp_path, [
        ("PIXEL-YES", "合成显示器甲", "显示器", 900, 2, "27英寸 IPS；2560×1440 分辨率。"),
        ("PIXEL-NO", "合成显示器乙", "显示器", 900, 2, "27英寸 IPS；1920×1080 分辨率。"),
    ])
    response = _ask(service, "推荐精确 2560×1440 分辨率的显示器")
    assert [item.product_id for item in response.recommendations] == ["PIXEL-YES"]
    assert _one(_judge(service, "2560×1440 分辨率的显示器", "PIXEL-NO"), "resolution").status == "refuted"


def test_esim_absence_has_support_refutation_and_unknown_states(tmp_path):
    service = _service(tmp_path, [
        ("SIM-NO", "合成手机甲", "手机", 900, 2, "不支持 eSIM。"),
        ("SIM-YES", "合成手机乙", "手机", 900, 2, "支持 eSIM。"),
        ("SIM-UNKNOWN", "合成手机丙", "手机", 900, 2, "支持双实体卡。"),
    ])
    query = "推荐明确不支持 eSIM 的手机"
    assert [item.product_id for item in _ask(service, query).recommendations] == ["SIM-NO"]
    assert _one(_judge(service, query, "SIM-NO"), "esim").status == "supported"
    assert _one(_judge(service, query, "SIM-YES"), "esim").status == "refuted"
    assert _one(_judge(service, query, "SIM-UNKNOWN"), "esim").status == "unknown"


def test_positive_nfc_requires_current_positive_source(tmp_path):
    service = _service(tmp_path, [
        ("NFC-YES", "合成手机甲", "手机", 900, 2, "支持 NFC。"),
        ("NFC-NO", "合成手机乙", "手机", 900, 2, "不支持 NFC。"),
        ("NFC-UNKNOWN", "合成手机丙", "手机", 900, 2, "支持双卡。"),
    ])
    query = "推荐必须支持 NFC 的手机"
    response = _ask(service, query)
    assert [item.product_id for item in response.recommendations] == ["NFC-YES"]
    assert _one(_judge(service, query, "NFC-NO"), "nfc").status == "refuted"
    assert _one(_judge(service, query, "NFC-UNKNOWN"), "nfc").status == "unknown"


def test_description_review_conflict_never_passes_positive_keyword(tmp_path):
    service = _service(tmp_path, [
        ("ANC-CONFLICT", "合成耳机", "耳机", 400, 2,
         "支持主动降噪；单次续航14小时。", ["主动降噪"], ["这款不支持主动降噪。"]),
    ])
    query = "推荐支持主动降噪的耳机"
    response = _ask(service, query)
    assert response.recommendations == []
    judgment = _one(_judge(service, query, "ANC-CONFLICT"), "anc")
    assert judgment.status == "refuted" and judgment.conflict
    assert {ref.source_id for ref in judgment.evidence} == {
        "ANC-CONFLICT:description", "ANC-CONFLICT:review:1",
    }
    assert "冲突" in response.answer or any("冲突" in warning for warning in response.warnings)


def test_source_update_invalidates_old_id_and_rejudges_new_version(tmp_path):
    service = _service(tmp_path, [
        ("VERSIONED", "合成手机", "手机", 900, 2, "支持 NFC。"),
    ])
    query = "推荐支持 NFC 的手机"
    first = _ask(service, query)
    assert [item.product_id for item in first.recommendations] == ["VERSIONED"]
    old_id = _one(first.recommendations[0].condition_judgments, "nfc").evidence[0].source_id
    update = tmp_path / "updated.jsonl"
    update.write_text(json.dumps({
        "kind": "product", "product_id": "VERSIONED", "name": "合成手机",
        "category": "手机", "price": 900, "stock": 2,
        "description": "不支持 NFC。", "tags": [], "reviews": [],
        "data_origin": "synthetic_demo",
    }, ensure_ascii=False), encoding="utf-8")
    service.store.import_documents_jsonl(update)
    assert service.store.get_document(old_id) is None
    assert _ask(service, query).recommendations == []
    updated = _one(_judge(service, query, "VERSIONED"), "nfc")
    assert updated.status == "refuted" and updated.evidence[0].source_id != old_id
    assert service.store.get_document(updated.evidence[0].source_id) is not None


def test_deleted_source_cannot_remain_in_a_recommendation(tmp_path):
    service = _service(tmp_path, [
        ("DELETED", "合成手机", "手机", 900, 2, "支持 NFC。"),
    ])
    old = _one(_ask(service, "推荐支持 NFC 的手机").recommendations[0].condition_judgments, "nfc")
    assert service.store.delete_product("DELETED")
    assert service.store.get_document(old.evidence[0].source_id) is None
    assert _ask(service, "推荐支持 NFC 的手机").recommendations == []


def test_num_items_only_caps_eligible_items_never_fills_unknowns(tmp_path):
    service = _service(tmp_path, [
        ("ONE-YES", "合成手机甲", "手机", 900, 2, "支持 NFC。"),
        ("TWO-NO", "合成手机乙", "手机", 900, 2, "不支持 NFC。"),
        ("THREE-UNKNOWN", "合成手机丙", "手机", 900, 2, "支持双卡。"),
    ])
    response = _ask(service, "推荐支持 NFC 的手机", count=5)
    assert [item.product_id for item in response.recommendations] == ["ONE-YES"]
    assert response.recommendations[0].condition_judgments
    assert all(j.status == "supported" for j in response.recommendations[0].condition_judgments)


def test_price_limit_uses_auditable_current_catalog_snapshot(tmp_path):
    service = _service(tmp_path, [
        ("PRICE-BOUND", "合成耳机", "耳机", 100, 2, "入耳式耳机。"),
    ])
    query = "推荐售价不超过 100 元的耳机"
    response = _ask(service, query)
    assert [item.product_id for item in response.recommendations] == ["PRICE-BOUND"]
    price = _one(response.recommendations[0].condition_judgments, "price")
    assert price.status == "supported" and price.operator == "<="
    assert price.evidence[0].source_type == "catalog_snapshot"
    assert service.store.is_current_catalog_snapshot(price.evidence[0].source_id)
    assert "100" in price.evidence[0].excerpt


def test_stock_threshold_uses_authoritative_count(tmp_path):
    service = _service(tmp_path, [
        ("STOCK-2", "合成手机甲", "手机", 900, 2, "普通手机。"),
        ("STOCK-1", "合成手机乙", "手机", 900, 1, "普通手机。"),
    ])
    response = _ask(service, "推荐库存至少 2 件的手机")
    assert [item.product_id for item in response.recommendations] == ["STOCK-2"]
    assert _one(_judge(service, "库存至少 2 件的手机", "STOCK-1"), "stock").status == "refuted"


def test_open_unsupported_capability_is_explicitly_unknown(tmp_path):
    service = _service(tmp_path, [
        ("ORDINARY", "合成手机", "手机", 900, 2, "支持 NFC。"),
    ])
    query = "推荐必须支持量子传送功能的手机"
    response = _ask(service, query)
    assert response.recommendations == []
    judgment = _one(_judge(service, query, "ORDINARY"), "unparsed_capability")
    assert judgment.status == "unknown"
    assert "无法" in response.answer or any("无法" in warning for warning in response.warnings)


def test_absent_camera_claim_is_unknown_not_negative_support(tmp_path):
    service = _service(tmp_path, [
        ("SILENT-CAMERA", "合成手机", "手机", 900, 2, "支持 NFC。"),
    ])
    query = "推荐明确没有摄像头的手机"
    assert _ask(service, query).recommendations == []
    judgment = _one(_judge(service, query, "SILENT-CAMERA"), "camera")
    assert judgment.status == "unknown"


def test_every_explicit_requirement_has_current_supported_judgment(tmp_path):
    service = _service(tmp_path, [
        ("ALL-YES", "合成手机甲", "手机", 2900, 2,
         "支持 eSIM；512GB 存储；5500mAh 电池。"),
        ("ALL-NO", "合成手机乙", "手机", 2900, 2,
         "不支持 eSIM；512GB 存储；5500mAh 电池。"),
    ])
    response = _ask(service, "推荐支持 eSIM、512GB 存储、电池至少 5000mAh、预算 3000 元以内的手机")
    assert [item.product_id for item in response.recommendations] == ["ALL-YES"]
    judgments = response.recommendations[0].condition_judgments
    assert {j.field for j in judgments} >= {"esim", "storage_gb", "battery_mah", "price"}
    assert all(j.status == "supported" and j.evidence for j in judgments)
    for judgment in judgments:
        for ref in judgment.evidence:
            assert (service.store.get_document(ref.source_id) is not None
                    if ref.source_type != "catalog_snapshot"
                    else service.store.is_current_catalog_snapshot(ref.source_id))


def test_final_verifier_rechecks_changed_price_and_snapshot(tmp_path):
    from shopping_agent.agent_roles import VerificationInput
    from shopping_agent.constraints import judgments_are_current

    service = _service(tmp_path, [
        ("PRICE-CHANGE", "合成耳机", "耳机", 100, 2, "支持主动降噪。"),
    ])
    request = ShopRequest(user_id="constraint-contract", query="推荐售价不超过 100 元的耳机")
    first = asyncio.run(service.recommend(request))
    assert [item.product_id for item in first.recommendations] == ["PRICE-CHANGE"]
    old_price_ref = _one(first.recommendations[0].condition_judgments, "price").evidence[0]

    update = tmp_path / "changed-price.jsonl"
    update.write_text(json.dumps({
        "kind": "product", "product_id": "PRICE-CHANGE", "name": "合成耳机",
        "category": "耳机", "price": 150, "stock": 2,
        "description": "支持主动降噪。", "tags": [], "reviews": [],
        "data_origin": "synthetic_demo",
    }, ensure_ascii=False), encoding="utf-8")
    service.store.import_documents_jsonl(update)
    assert not service.store.is_current_catalog_snapshot(old_price_ref.source_id)
    assert not judgments_are_current(first.recommendations[0].condition_judgments, service.store)
    verified = service.verifier.run(VerificationInput(
        request, tuple(first.recommendations), "耳机", 100,
    ))
    assert verified.recommendations == () and verified.removed_count == 1
    assert _ask(service, request.query).recommendations == []


def test_explicitly_excluded_refresh_rate_still_checks_other_monitor_specs(tmp_path):
    service = _service(tmp_path, [
        ("FAST-27", "合成高速显示器", "显示器", 900, 2,
         "27英寸 2560×1440；165 Hz 刷新率。", ["2K"]),
        ("SLOW-27", "合成普通显示器", "显示器", 800, 2,
         "27英寸 2560×1440；75 Hz 刷新率。", ["2K"]),
        ("FAST-24", "合成小显示器", "显示器", 700, 2,
         "24英寸 2560×1440；165 Hz 刷新率。", ["2K"]),
    ])
    query = "不要 75 Hz 的 27 英寸 2K 显示器，推荐符合的型号"
    response = _ask(service, query)
    assert [item.product_id for item in response.recommendations] == ["FAST-27"]
    judgments = response.recommendations[0].condition_judgments
    assert {(j.field, j.operator, j.status) for j in judgments} >= {
        ("refresh_hz", "!=", "supported"),
        ("screen_inches", "=", "supported"),
        ("resolution_class", "=", "supported"),
    }


def test_keyboard_name_and_explicit_absence_are_current_source_facts(tmp_path):
    service = _service(tmp_path, [
        ("COMPACT", "合成 68 键机械键盘", "键盘", 200, 2,
         "支持热插拔；没有独立数字区。", ["机械键盘"]),
        ("FULL", "合成 98 键机械键盘", "键盘", 250, 2,
         "支持热插拔；有独立数字区。", ["机械键盘"]),
    ])
    response = _ask(service, "推荐不要独立数字区、带热插拔和机械轴的键盘")
    assert [item.product_id for item in response.recommendations] == ["COMPACT"]
    assert {(j.field, j.operator, j.status) for j in
            response.recommendations[0].condition_judgments} >= {
        ("numpad", "absent", "supported"),
        ("hotswap", "present", "supported"),
        ("mechanical", "present", "supported"),
    }


def test_strict_ip_rating_excludes_the_equal_boundary(tmp_path):
    service = _service(tmp_path, [
        ("IP-EQUAL", "合成耳机甲", "耳机", 100, 2, "防护等级 IPX5。"),
        ("IP-HIGH", "合成耳机乙", "耳机", 120, 2, "防护等级 IPX6。"),
    ])
    query = "推荐防护等级高于 IPX5 的耳机"
    assert [item.product_id for item in _ask(service, query).recommendations] == ["IP-HIGH"]
    assert _one(_judge(service, query, "IP-EQUAL"), "ip_rating").status == "refuted"
    assert _one(_judge(service, query, "IP-HIGH"), "ip_rating").status == "supported"


def test_unordered_resolution_comparison_is_unknown(tmp_path):
    service = _service(tmp_path, [
        ("PIXEL", "合成显示器", "显示器", 700, 2, "2560×1440 分辨率。"),
    ])
    judgment = _one(_judge(service, "要求分辨率高于 1920×1080 的显示器", "PIXEL"),
                    "resolution")
    assert judgment.operator == ">" and judgment.status == "unknown"
