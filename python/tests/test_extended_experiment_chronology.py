"""Event attribution must preserve request and item exposure chronology."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from shopping_agent.experiments import ExperimentStore


@pytest.mark.parametrize("event_type", ["click", "purchase"])
def test_event_cannot_precede_exposure_across_timezones(tmp_path, event_type):
    store = ExperimentStore(tmp_path / "chronology.sqlite3")
    exposure = datetime(2026, 9, 23, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    store.record_exposure("buyer", "request-1", ["sku-1"], occurred_at=exposure)

    with pytest.raises(ValueError, match="before.*exposure"):
        store.record_event(
            "buyer", "request-1", "sku-1", event_type,
            occurred_at=datetime(2026, 9, 22, 23, 59, 59, tzinfo=timezone.utc),
        )
    assert [event.event_type for event in store.list_events()] == ["exposure"]

    # The same instant in UTC is valid even though its displayed hour is earlier.
    assert store.record_event(
        "buyer", "request-1", "sku-1", event_type,
        occurred_at=datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc),
    )
    assert not store.record_event(
        "buyer", "request-1", "sku-1", event_type,
        occurred_at=datetime(2026, 9, 22, 23, 0, tzinfo=timezone.utc),
    )
    assert len(store.list_events()) == 2
    store.close()


@pytest.mark.parametrize("event_type", ["click", "purchase"])
def test_event_must_follow_item_exposure_as_well_as_request(tmp_path, event_type):
    database = tmp_path / "legacy-exposure.sqlite3"
    store = ExperimentStore(database)
    start = datetime(2026, 9, 23, tzinfo=timezone.utc)
    store.record_exposure("buyer", "request-1", ["sku-1"], occurred_at=start)

    # Older data may have a later per-item exposure than the request timestamp.
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE experiment_events SET occurred_at=?
               WHERE request_id=? AND event_type='exposure' AND product_id=?""",
            ("2026-09-23T02:00:00+02:00", "request-1", "sku-1"),
        )
    assert store.record_event(
        "buyer", "request-1", "sku-1", event_type,
        occurred_at=start + timedelta(seconds=1),
    )
    store.close()


@pytest.mark.parametrize("event_type", ["click", "purchase"])
def test_event_rejects_later_item_exposure_even_if_request_is_older(tmp_path, event_type):
    database = tmp_path / "later-item.sqlite3"
    store = ExperimentStore(database)
    start = datetime(2026, 9, 23, tzinfo=timezone.utc)
    store.record_exposure("buyer", "request-1", ["sku-1"], occurred_at=start)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE experiment_events SET occurred_at=?
               WHERE request_id=? AND event_type='exposure' AND product_id=?""",
            ((start + timedelta(minutes=1)).isoformat(), "request-1", "sku-1"),
        )

    with pytest.raises(ValueError, match="before.*exposure"):
        store.record_event(
            "buyer", "request-1", "sku-1", event_type,
            occurred_at=start + timedelta(seconds=30),
        )
    assert store.record_event(
        "buyer", "request-1", "sku-1", event_type,
        occurred_at=start + timedelta(minutes=1),
    )
    store.close()


@pytest.mark.parametrize("event_type", ["click", "purchase"])
def test_event_rejects_later_request_exposure_even_if_item_is_older(tmp_path, event_type):
    database = tmp_path / "later-request.sqlite3"
    store = ExperimentStore(database)
    start = datetime(2026, 9, 23, tzinfo=timezone.utc)
    store.record_exposure("buyer", "request-1", ["sku-1"], occurred_at=start)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """UPDATE experiment_requests SET exposed_at=? WHERE request_id=?""",
            ((start + timedelta(minutes=1)).isoformat(), "request-1"),
        )

    with pytest.raises(ValueError, match="before.*exposure"):
        store.record_event(
            "buyer", "request-1", "sku-1", event_type,
            occurred_at=start + timedelta(seconds=30),
        )
    assert store.record_event(
        "buyer", "request-1", "sku-1", event_type,
        occurred_at=start + timedelta(minutes=1),
    )
    store.close()
