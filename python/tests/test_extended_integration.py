"""A/B strategy wiring and request-level API attribution."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from shopping_agent.app import create_app
from shopping_agent.config import Settings
from shopping_agent.retrieval import EvidenceIndex
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _users_by_variant(service):
    users = {}
    for number in range(100):
        user = f"experiment-user-{number}"
        assigned = service.experiments.assign(user)
        users.setdefault(assigned.variant, user)
        if len(users) == 2:
            break
    assert set(users) == {"control", "treatment"}
    return users


def test_two_variants_drive_actual_retrieval_and_exposure(tmp_path):
    service = create_service(Settings(
        database_url=f"sqlite:///{tmp_path / 'ab.sqlite3'}",
        experiment_enabled=True,
    ))
    users = _users_by_variant(service)
    responses = {
        variant: asyncio.run(service.recommend(ShopRequest(
            user_id=user, query="推荐主动降噪耳机", num_items=2,
        )))
        for variant, user in users.items()
    }
    assert responses["control"].requested_retrieval_mode == "bm25"
    assert responses["control"].retrieval_mode == "bm25"
    assert responses["treatment"].requested_retrieval_mode == "hybrid"
    assert responses["treatment"].retrieval_mode == "hybrid"
    assert "product_retrieval:bm25" in responses["control"].tool_trace
    assert "product_retrieval:hybrid" in responses["treatment"].tool_trace
    assert responses["control"].request_id != responses["treatment"].request_id
    requests = {entry.request_id: entry for entry in service.experiments.list_requests()}
    assert requests[responses["control"].request_id].actual_mode == "bm25"
    assert requests[responses["treatment"].request_id].actual_mode == "hybrid"
    assert not any(entry.degraded for entry in requests.values())


def test_vector_outage_is_logged_as_treatment_degradation(tmp_path):
    service = create_service(Settings(
        database_url=f"sqlite:///{tmp_path / 'fallback.sqlite3'}",
        experiment_enabled=True,
    ))
    treatment_user = _users_by_variant(service)["treatment"]

    class UnavailableEmbedding:
        name = "unavailable-test-embedding"

        def embed(self, texts):
            raise ConnectionError("local model unavailable")

    service.retriever = EvidenceIndex(
        service.retriever.documents, UnavailableEmbedding()
    )
    response = asyncio.run(service.recommend(ShopRequest(
        user_id=treatment_user, query="推荐主动降噪耳机", num_items=2,
    )))
    assert response.variant == "treatment"
    assert response.requested_retrieval_mode == "hybrid"
    assert response.retrieval_mode == "bm25"
    assert "product_retrieval:bm25" in response.tool_trace
    logged = next(
        entry for entry in service.experiments.list_requests()
        if entry.request_id == response.request_id
    )
    assert logged.actual_mode == "bm25"
    assert logged.degraded
    assert service.experiments.replay_stats()["variants"]["treatment"][
        "degraded_requests"
    ] == 1


def test_attributed_event_succeeds_after_exposed_product_is_deleted(tmp_path):
    service = create_service(Settings(
        database_url=f"sqlite:///{tmp_path / 'events.sqlite3'}",
        experiment_enabled=True,
    ))
    app = create_app(service=service)
    with TestClient(app) as client:
        recommendation = client.post(
            "/api/v1/shop/recommend",
            json={"user_id": "buyer", "query": "推荐主动降噪耳机", "num_items": 1},
        )
        assert recommendation.status_code == 200
        body = recommendation.json()
        product_id = body["recommendations"][0]["product_id"]
        service.store.delete_product(product_id)
        click = client.post(
            "/api/v1/events",
            json={
                "user_id": "buyer", "request_id": body["request_id"],
                "product_id": product_id, "event_type": "click",
            },
        )
        wrong_user = client.post(
            "/api/v1/events",
            json={
                "user_id": "stranger", "request_id": body["request_id"],
                "product_id": product_id, "event_type": "purchase",
            },
        )
    assert click.status_code == 201
    assert wrong_user.status_code == 404
    assert len([
        event for event in service.experiments.list_events()
        if event.request_id == body["request_id"] and event.event_type == "click"
    ]) == 1
