"""Offline A/B pipeline tests; no live users or business lift claims."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from shopping_agent.experiments import (
    ExperimentConfig,
    ExperimentStore,
    VariantConfig,
    estimate_sample_size,
)


def test_assignment_stays_stable_across_requests_and_process_instances(tmp_path):
    database = tmp_path / "experiments.sqlite3"
    first = ExperimentStore(database)
    assignment = first.assign("repeat-buyer")
    assert first.assign("repeat-buyer") == assignment
    first.record_exposure("repeat-buyer", "request-one", ["sku-1"])
    first.record_exposure("repeat-buyer", "request-two", ["sku-2"])
    assert {event.variant for event in first.list_events()} == {assignment.variant}
    first.close()

    reopened = ExperimentStore(database)
    assert reopened.assign("repeat-buyer") == assignment
    assert {event.request_id for event in reopened.list_events()} == {
        "request-one", "request-two"
    }
    reopened.close()


def test_variants_select_distinct_real_retrieval_modes(tmp_path):
    store = ExperimentStore(tmp_path / "variants.sqlite3")
    assignments = {store.assign(f"buyer-{index}") for index in range(100)}
    by_variant = {assignment.variant: assignment.retrieval_mode for assignment in assignments}
    assert by_variant == {"control": "bm25", "treatment": "hybrid"}
    assert all(store.assign(f"buyer-{index}").retrieval_mode in {"bm25", "hybrid"}
               for index in range(100))

    with pytest.raises(ValueError, match="actually change retrieval"):
        ExperimentConfig(
            variants=(VariantConfig("a", "bm25"), VariantConfig("b", "bm25"))
        )
    store.close()


def test_degraded_treatment_is_counted_by_effective_mode_not_assigned_mode(tmp_path):
    store = ExperimentStore(tmp_path / "degraded.sqlite3")
    treatment_user = next(
        f"buyer-{index}" for index in range(100)
        if store.assign(f"buyer-{index}").variant == "treatment"
    )
    assignment = store.record_exposure(
        treatment_user,
        "degraded-request",
        ["sku-1"],
        actual_retrieval_mode="bm25",
        fallback_reason="vector retrieval failed: ConnectionError",
    )
    assert assignment.retrieval_mode == "hybrid"
    request = store.list_requests()[0]
    assert request.requested_mode == "hybrid"
    assert request.actual_mode == "bm25"
    assert request.fallback_reason == "vector retrieval failed: ConnectionError"
    assert request.degraded is True

    treatment = store.replay_stats()["variants"]["treatment"]
    assert treatment["degraded_requests"] == 1
    assert treatment["actual_mode_counts"] == {
        "bm25": 1, "vector": 0, "hybrid": 0
    }
    assert treatment["exposed_requests"] == 1

    with pytest.raises(ValueError, match="different retrieval execution"):
        store.record_exposure(
            treatment_user,
            "degraded-request",
            ["sku-1"],
            actual_retrieval_mode="hybrid",
        )

    store.record_exposure(
        treatment_user, "no-retrieval", [], actual_retrieval_mode=None
    )
    skipped = next(
        request for request in store.list_requests()
        if request.request_id == "no-retrieval"
    )
    assert skipped.actual_mode is None and skipped.degraded is False
    treatment = store.replay_stats()["variants"]["treatment"]
    assert treatment["no_retrieval_requests"] == 1
    assert treatment["actual_mode_counts"]["hybrid"] == 0

    store.record_exposure(
        treatment_user, "healthy-hybrid", ["sku-2"],
        actual_retrieval_mode="hybrid",
    )
    treatment = store.replay_stats()["variants"]["treatment"]
    assert treatment["degraded_requests"] == 1
    assert treatment["actual_mode_counts"] == {
        "bm25": 1, "vector": 0, "hybrid": 1
    }
    store.close()


def test_existing_request_table_migrates_without_inventing_historical_mode(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE experiment_assignments (
                experiment_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                assigned_at TEXT NOT NULL,
                PRIMARY KEY (experiment_id, user_id)
            );
            CREATE TABLE experiment_requests (
                experiment_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                variant TEXT NOT NULL,
                exposed_at TEXT NOT NULL,
                PRIMARY KEY (experiment_id, request_id)
            );
            INSERT INTO experiment_assignments VALUES
                ('retrieval-v1', 'legacy-user', 'treatment', '2026-09-23T00:00:00+00:00');
            INSERT INTO experiment_requests VALUES
                ('retrieval-v1', 'legacy-request', 'legacy-user', 'treatment',
                 '2026-09-23T00:00:01+00:00');
        """)

    store = ExperimentStore(database)
    legacy = store.list_requests()[0]
    assert legacy.requested_mode is None
    assert legacy.actual_mode is None
    assert legacy.degraded is False
    assert store.replay_stats()["variants"]["treatment"]["unknown_actual_mode_requests"] == 1

    store.record_exposure("legacy-user", "new-request", ["sku-1"])
    new = next(request for request in store.list_requests() if request.request_id == "new-request")
    assert new.requested_mode == "hybrid" and new.actual_mode == "hybrid"
    store.close()


def test_exposure_click_purchase_are_attributed_to_same_request_and_product(tmp_path):
    store = ExperimentStore(tmp_path / "events.sqlite3")
    start = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
    assignment = store.record_exposure(
        "buyer", "request-1", ["sku-a", "sku-b"], occurred_at=start
    )
    assert store.record_event(
        "buyer", "request-1", "sku-a", "click", occurred_at=start + timedelta(seconds=1)
    )
    assert store.record_event(
        "buyer", "request-1", "sku-a", "purchase", occurred_at=start + timedelta(seconds=2)
    )
    assert not store.record_event("buyer", "request-1", "sku-a", "click")
    assert store.record_exposure("buyer", "request-1", ["sku-b", "sku-a"]) == assignment

    events = store.list_events()
    assert len(events) == 4
    assert {event.event_type for event in events} == {"exposure", "click", "purchase"}
    assert all(
        (event.experiment_id, event.variant, event.user_id, event.request_id)
        == (assignment.experiment_id, assignment.variant, "buyer", "request-1")
        for event in events
    )
    assert all(datetime.fromisoformat(event.occurred_at).utcoffset() == timedelta(0)
               for event in events)
    assert events[0].occurred_at < events[-1].occurred_at

    with pytest.raises(ValueError, match="same request|exposed request"):
        store.record_event("buyer", "request-2", "sku-a", "purchase")
    with pytest.raises(ValueError, match="this user"):
        store.record_event("other-buyer", "request-1", "sku-a", "click")
    with pytest.raises(ValueError, match="not exposed"):
        store.record_event("buyer", "request-1", "sku-c", "click")
    with pytest.raises(ValueError, match="another user"):
        store.record_exposure("other-buyer", "request-1", ["sku-a"])
    with pytest.raises(ValueError, match="different exposure"):
        store.record_exposure("buyer", "request-1", ["sku-a"])
    store.close()


def test_replay_counts_empty_exposures_and_only_attributed_events(tmp_path):
    store = ExperimentStore(tmp_path / "replay.sqlite3")
    control_user = next(
        f"buyer-{index}" for index in range(100)
        if store.assign(f"buyer-{index}").variant == "control"
    )
    treatment_user = next(
        f"buyer-{index}" for index in range(100)
        if store.assign(f"buyer-{index}").variant == "treatment"
    )
    store.record_exposure(control_user, "control-1", ["sku-1", "sku-2"])
    store.record_event(control_user, "control-1", "sku-1", "click")
    store.record_event(control_user, "control-1", "sku-1", "purchase")
    store.record_exposure(control_user, "control-2", [])
    store.record_exposure(treatment_user, "treatment-1", ["sku-3"])

    replay = store.replay_stats()
    assert replay["experiment_id"] == "retrieval-v1"
    control = replay["variants"]["control"]
    treatment = replay["variants"]["treatment"]
    assert control["exposed_users"] == 1
    assert control["exposed_requests"] == 2
    assert control["product_impressions"] == 2
    assert control["clicks"] == 1 and control["purchases"] == 1
    assert control["item_ctr"] == 0.5
    assert control["request_click_rate"] == 0.5
    assert treatment["exposed_requests"] == 1
    assert treatment["product_impressions"] == 1
    assert treatment["clicks"] == 0 and treatment["purchases"] == 0
    assert treatment["item_ctr"] == 0.0
    assert "not an online lift" in replay["note"]
    store.close()


def test_sample_size_estimate_is_reproducible_and_validates_inputs():
    estimate = estimate_sample_size(0.1, 0.02)
    assert estimate.per_variant == estimate_sample_size(0.1, 0.02).per_variant
    assert 3000 < estimate.per_variant < 5000
    assert estimate.total == 2 * estimate.per_variant
    for args in ((0, 0.02), (0.1, 0), (0.99, 0.02), (float("nan"), 0.02)):
        with pytest.raises(ValueError):
            estimate_sample_size(*args)
