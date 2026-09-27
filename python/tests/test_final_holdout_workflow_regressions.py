"""Keep money, numeric comparisons, and cited catalog facts consistent."""

from __future__ import annotations

import asyncio

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _ask(query: str, count: int = 4):
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    return asyncio.run(service.recommend(ShopRequest(
        user_id="final-regression", query=query, num_items=count,
    )))


def test_not_above_money_caps_price_without_confusing_storage():
    response = _ask(
        "手机至少要256GB存储，单价不高于3000元，现在有货的型号有哪些？",
    )
    assert {item.product_id for item in response.recommendations} == {
        "DEMO-P02", "DEMO-P03", "DEMO-P04",
    }


def test_cannot_be_below_battery_capacity_is_a_lower_bound():
    response = _ask(
        "手机屏幕刷新率要超过100Hz，电池不能低于5000mAh，三千元以内有货的有哪些？",
    )
    assert {item.product_id for item in response.recommendations} == {
        "DEMO-P02", "DEMO-P04",
    }


def test_dont_block_ear_canal_excludes_in_ear_anc_models():
    response = _ask("耳机不要堵耳道，同时必须主动降噪，预算400元内")
    assert response.recommendations == []


def test_recommendations_include_current_description_even_when_only_numeric_rules_apply():
    response = _ask("平板屏幕至少10英寸，单台预算2000元且要有现货")
    assert {item.product_id for item in response.recommendations} == {
        "DEMO-T02", "DEMO-T05",
    }
    for item in response.recommendations:
        assert item.product_id + ":description" in {
            ref.source_id for ref in item.evidence
        }
