"""Local, auditable A/B assignment and event attribution.

This module records an experiment *pipeline*. The bundled catalog and test
traffic are synthetic, so its replay statistics are not business results.
Use a new experiment ID whenever variant policies or allocation weights change.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Literal, Sequence


RetrievalMode = Literal["bm25", "vector", "hybrid"]
EventType = Literal["exposure", "click", "purchase"]
_ASSIGNED_MODE = object()


@dataclass(frozen=True, slots=True)
class VariantConfig:
    name: str
    retrieval_mode: RetrievalMode
    weight: int = 1

    def __post_init__(self) -> None:
        if not self.name.strip() or self.retrieval_mode not in {"bm25", "vector", "hybrid"}:
            raise ValueError("Variant needs a name and supported retrieval mode")
        if type(self.weight) is not int or self.weight <= 0:
            raise ValueError("Variant weight must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    experiment_id: str = "retrieval-v1"
    variants: tuple[VariantConfig, ...] = (
        VariantConfig("control", "bm25"),
        VariantConfig("treatment", "hybrid"),
    )
    salt: str = "shopping-agent-retrieval-v1"

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.salt:
            raise ValueError("Experiment ID and salt must be nonempty")
        if len(self.variants) < 2 or len({v.name for v in self.variants}) != len(self.variants):
            raise ValueError("An experiment needs at least two distinctly named variants")
        if len({v.retrieval_mode for v in self.variants}) < 2:
            raise ValueError("Variants must actually change retrieval strategy")


@dataclass(frozen=True, slots=True)
class Assignment:
    experiment_id: str
    user_id: str
    variant: str
    retrieval_mode: RetrievalMode
    assigned_at: str


@dataclass(frozen=True, slots=True)
class ExperimentEvent:
    event_id: str
    experiment_id: str
    variant: str
    user_id: str
    request_id: str
    event_type: EventType
    product_id: str | None
    occurred_at: str


@dataclass(frozen=True, slots=True)
class ExperimentRequest:
    experiment_id: str
    request_id: str
    user_id: str
    variant: str
    exposed_at: str
    requested_mode: RetrievalMode | None
    actual_mode: RetrievalMode | None
    fallback_reason: str | None

    @property
    def degraded(self) -> bool:
        """Only classify requests with a known effective strategy."""
        return self.actual_mode is not None and (
            self.actual_mode != self.requested_mode or self.fallback_reason is not None
        )


@dataclass(frozen=True, slots=True)
class SampleSizeEstimate:
    per_variant: int
    total: int
    baseline_rate: float
    minimum_detectable_absolute_change: float
    alpha: float
    power: float


def estimate_sample_size(
    baseline_rate: float,
    minimum_detectable_absolute_change: float,
    *,
    alpha: float = 0.05,
    power: float = 0.8,
) -> SampleSizeEstimate:
    """Approximate two-sided equal-group sample size for a binary outcome.

    Rates are proportions; MDE is an *absolute* change, not a relative lift.
    The result is a planning estimate, not evidence of an experiment outcome.
    """
    p1 = baseline_rate
    delta = minimum_detectable_absolute_change
    p2 = p1 + delta
    if not all(math.isfinite(x) for x in (p1, delta, alpha, power)):
        raise ValueError("Parameters must be finite")
    if not (0 < p1 < 1 and 0 < delta and p2 < 1 and 0 < alpha < 1 and 0 < power < 1):
        raise ValueError("Rates, MDE, alpha and power must lie in their valid ranges")
    standard_normal = NormalDist()
    z_alpha = standard_normal.inv_cdf(1 - alpha / 2)
    z_power = standard_normal.inv_cdf(power)
    mean_rate = (p1 + p2) / 2
    numerator = (
        z_alpha * math.sqrt(2 * mean_rate * (1 - mean_rate))
        + z_power * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    per_variant = math.ceil(numerator / delta**2)
    return SampleSizeEstimate(per_variant, per_variant * 2, p1, delta, alpha, power)


def _utc_timestamp(occurred_at: datetime | None) -> str:
    timestamp = occurred_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Event time must include a timezone")
    return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _stored_utc_timestamp(value: str) -> datetime:
    """Parse an existing exposure by instant, including older offset formats."""
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("Stored exposure time is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Stored exposure time must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _database_path(database_url: str | Path) -> str:
    value = str(database_url)
    if value in {"sqlite://", "sqlite:///:memory:", ":memory:"}:
        return ":memory:"
    if value.startswith("sqlite:///"):
        path = value[len("sqlite:///"):]
        if not path:
            raise ValueError("SQLite database path is empty")
        return path
    if "://" in value:
        raise ValueError("ExperimentStore currently supports SQLite only")
    return value


class ExperimentStore:
    """Assign users consistently and persist attributed request events.

    Exposure is recorded once per request and recommended product. An empty
    recommendation still gets a request-level exposure with no product ID.
    Click and purchase must reference an exposed product in that same request;
    this prevents unrelated events from being counted as experiment outcomes.
    """

    def __init__(
        self,
        database_url: str | Path,
        config: ExperimentConfig | None = None,
    ) -> None:
        self.config = config or ExperimentConfig()
        self._variants = {variant.name: variant for variant in self.config.variants}
        path = _database_path(database_url)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiment_assignments (
                    experiment_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (experiment_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_requests (
                    experiment_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    exposed_at TEXT NOT NULL,
                    requested_mode TEXT,
                    actual_mode TEXT,
                    fallback_reason TEXT,
                    PRIMARY KEY (experiment_id, request_id),
                    FOREIGN KEY (experiment_id, user_id)
                        REFERENCES experiment_assignments (experiment_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_events (
                    event_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('exposure', 'click', 'purchase')),
                    product_id TEXT,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (experiment_id, request_id)
                        REFERENCES experiment_requests (experiment_id, request_id),
                    UNIQUE (experiment_id, request_id, event_type, product_id)
                );
                CREATE INDEX IF NOT EXISTS ix_experiment_events_replay
                    ON experiment_events (experiment_id, variant, event_type);
                """
            )
            # Older local databases have the same table without strategy fields.
            # Their historical effective mode is unknown and must stay unknown.
            columns = {
                row["name"] for row in self._connection.execute(
                    "PRAGMA table_info(experiment_requests)"
                )
            }
            for column in ("requested_mode", "actual_mode", "fallback_reason"):
                if column not in columns:
                    self._connection.execute(
                        f"ALTER TABLE experiment_requests ADD COLUMN {column} TEXT"
                    )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be nonempty text")
        return value.strip()

    def _chosen_variant(self, user_id: str) -> VariantConfig:
        digest = hashlib.sha256(
            f"{self.config.salt}\0{self.config.experiment_id}\0{user_id}".encode("utf-8")
        ).digest()
        bucket = int.from_bytes(digest[:8], "big") % sum(v.weight for v in self.config.variants)
        for variant in self.config.variants:
            if bucket < variant.weight:
                return variant
            bucket -= variant.weight
        raise AssertionError("Unreachable variant bucket")

    def _assign_locked(self, user_id: str, occurred_at: datetime | None = None) -> Assignment:
        experiment_id = self.config.experiment_id
        variant = self._chosen_variant(user_id)
        assigned_at = _utc_timestamp(occurred_at)
        self._connection.execute(
            """INSERT OR IGNORE INTO experiment_assignments
               (experiment_id, user_id, variant, assigned_at) VALUES (?, ?, ?, ?)""",
            (experiment_id, user_id, variant.name, assigned_at),
        )
        row = self._connection.execute(
            "SELECT variant, assigned_at FROM experiment_assignments WHERE experiment_id=? AND user_id=?",
            (experiment_id, user_id),
        ).fetchone()
        assert row is not None
        current_variant = self._variants.get(row["variant"])
        if current_variant is None:
            raise ValueError("Stored assignment names a variant absent from this experiment config")
        return Assignment(
            experiment_id, user_id, current_variant.name, current_variant.retrieval_mode,
            row["assigned_at"],
        )

    def assign(self, user_id: str) -> Assignment:
        """Persist a user-level variant; request IDs cannot alter the bucket."""
        user_id = self._required_text(user_id, "user_id")
        with self._lock, self._connection:
            return self._assign_locked(user_id)

    def record_exposure(
        self,
        user_id: str,
        request_id: str,
        product_ids: Sequence[str],
        *,
        occurred_at: datetime | None = None,
        actual_retrieval_mode: RetrievalMode | None | object = _ASSIGNED_MODE,
        fallback_reason: str | None = None,
    ) -> Assignment:
        """Write exposures with the strategy that actually ran.

        Omitted strategy fields keep existing callers compatible: the assigned
        strategy is assumed effective. Pass ``None`` explicitly when retrieval
        did not run; pass the effective mode and reason for a fallback.
        """
        user_id = self._required_text(user_id, "user_id")
        request_id = self._required_text(request_id, "request_id")
        if isinstance(product_ids, (str, bytes)):
            raise ValueError("product_ids must be a sequence of IDs")
        products = tuple(self._required_text(p, "product_id") for p in product_ids)
        if len(products) != len(set(products)):
            raise ValueError("Exposure product IDs must be distinct")
        if actual_retrieval_mode is not _ASSIGNED_MODE and actual_retrieval_mode is not None and actual_retrieval_mode not in {
            "bm25", "vector", "hybrid"
        }:
            raise ValueError("actual_retrieval_mode must be bm25, vector or hybrid")
        if fallback_reason is not None:
            fallback_reason = self._required_text(fallback_reason, "fallback_reason")
        timestamp = _utc_timestamp(occurred_at)
        with self._lock, self._connection:
            assignment = self._assign_locked(user_id, occurred_at)
            effective_mode = (
                assignment.retrieval_mode
                if actual_retrieval_mode is _ASSIGNED_MODE else actual_retrieval_mode
            )
            existing_request = self._connection.execute(
                """SELECT user_id, variant, requested_mode, actual_mode, fallback_reason
                   FROM experiment_requests WHERE experiment_id=? AND request_id=?""",
                (assignment.experiment_id, request_id),
            ).fetchone()
            if existing_request is not None:
                if existing_request["user_id"] != user_id or existing_request["variant"] != assignment.variant:
                    raise ValueError("Request ID already belongs to another user or variant")
                if existing_request["requested_mode"] is not None and (
                    existing_request["requested_mode"] != assignment.retrieval_mode
                    or existing_request["actual_mode"] != effective_mode
                    or existing_request["fallback_reason"] != fallback_reason
                ):
                    raise ValueError("Request ID already has a different retrieval execution")
                rows = self._connection.execute(
                    """SELECT product_id FROM experiment_events
                       WHERE experiment_id=? AND request_id=? AND event_type='exposure'""",
                    (assignment.experiment_id, request_id),
                ).fetchall()
                recorded = {row["product_id"] for row in rows if row["product_id"] is not None}
                if recorded != set(products):
                    raise ValueError("Request ID already has a different exposure set")
                return assignment

            self._connection.execute(
                """INSERT INTO experiment_requests
                   (experiment_id, request_id, user_id, variant, exposed_at,
                    requested_mode, actual_mode, fallback_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (assignment.experiment_id, request_id, user_id, assignment.variant,
                 timestamp, assignment.retrieval_mode, effective_mode, fallback_reason),
            )
            for product_id in products or (None,):
                self._connection.execute(
                    """INSERT INTO experiment_events
                       (event_id, experiment_id, variant, user_id, request_id,
                        event_type, product_id, occurred_at)
                       VALUES (?, ?, ?, ?, ?, 'exposure', ?, ?)""",
                    (str(uuid.uuid4()), assignment.experiment_id, assignment.variant,
                     user_id, request_id, product_id, timestamp),
                )
            return assignment

    def record_event(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        event_type: Literal["click", "purchase"],
        *,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Record a unique click or purchase at or after the item exposure.

        Returns False for an already recorded event of the same type, request
        and product. No metric can be attributed to a different request/user.
        """
        user_id = self._required_text(user_id, "user_id")
        request_id = self._required_text(request_id, "request_id")
        product_id = self._required_text(product_id, "product_id")
        if event_type not in {"click", "purchase"}:
            raise ValueError("event_type must be click or purchase")
        timestamp = _utc_timestamp(occurred_at)
        with self._lock, self._connection:
            request = self._connection.execute(
                """SELECT user_id, variant, exposed_at FROM experiment_requests
                   WHERE experiment_id=? AND request_id=?""",
                (self.config.experiment_id, request_id),
            ).fetchone()
            if request is None or request["user_id"] != user_id:
                raise ValueError("Event must belong to an exposed request for this user")
            exposed = self._connection.execute(
                """SELECT occurred_at FROM experiment_events WHERE experiment_id=? AND request_id=?
                   AND event_type='exposure' AND product_id=?""",
                (self.config.experiment_id, request_id, product_id),
            ).fetchone()
            if exposed is None:
                raise ValueError("Event product was not exposed in this request")
            already_recorded = self._connection.execute(
                """SELECT 1 FROM experiment_events WHERE experiment_id=? AND request_id=?
                   AND event_type=? AND product_id=?""",
                (self.config.experiment_id, request_id, event_type, product_id),
            ).fetchone()
            if already_recorded is not None:
                return False
            event_time = datetime.fromisoformat(timestamp)
            if (
                event_time < _stored_utc_timestamp(request["exposed_at"])
                or event_time < _stored_utc_timestamp(exposed["occurred_at"])
            ):
                raise ValueError("Event time is before request or product exposure")
            result = self._connection.execute(
                """INSERT OR IGNORE INTO experiment_events
                   (event_id, experiment_id, variant, user_id, request_id,
                    event_type, product_id, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), self.config.experiment_id, request["variant"],
                 user_id, request_id, event_type, product_id, timestamp),
            )
            return result.rowcount == 1

    def list_events(self) -> list[ExperimentEvent]:
        """Return the immutable raw log for auditing and offline replay."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT event_id, experiment_id, variant, user_id, request_id,
                          event_type, product_id, occurred_at
                   FROM experiment_events WHERE experiment_id=?
                   ORDER BY occurred_at, rowid""",
                (self.config.experiment_id,),
            ).fetchall()
        return [ExperimentEvent(**dict(row)) for row in rows]

    def list_requests(self) -> list[ExperimentRequest]:
        """Return request-level assignments and observed retrieval strategies."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT experiment_id, request_id, user_id, variant, exposed_at,
                          requested_mode, actual_mode, fallback_reason
                   FROM experiment_requests WHERE experiment_id=?
                   ORDER BY exposed_at, rowid""",
                (self.config.experiment_id,),
            ).fetchall()
        return [ExperimentRequest(**dict(row)) for row in rows]

    def replay_stats(self) -> dict[str, object]:
        """Summarize recorded events, including empty and unclicked exposures.

        The rates are descriptive only. In particular, synthetic replay is not
        an online CTR or purchase lift measurement.
        """
        events = self.list_events()
        requests = self.list_requests()
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id, variant FROM experiment_assignments WHERE experiment_id=?",
                (self.config.experiment_id,),
            ).fetchall()
        report: dict[str, dict[str, int | float]] = {}
        for variant in self.config.variants:
            group = [event for event in events if event.variant == variant.name]
            request_group = [request for request in requests if request.variant == variant.name]
            exposures = [event for event in group if event.event_type == "exposure"]
            clicks = [event for event in group if event.event_type == "click"]
            purchases = [event for event in group if event.event_type == "purchase"]
            exposed_requests = {event.request_id for event in exposures}
            item_impressions = sum(event.product_id is not None for event in exposures)
            clicked_requests = {event.request_id for event in clicks}
            report[variant.name] = {
                "assigned_users": sum(row["variant"] == variant.name for row in rows),
                "exposed_users": len({event.user_id for event in exposures}),
                "exposed_requests": len(exposed_requests),
                "product_impressions": item_impressions,
                "clicks": len(clicks),
                "purchases": len(purchases),
                "item_ctr": len(clicks) / item_impressions if item_impressions else 0.0,
                "request_click_rate": len(clicked_requests) / len(exposed_requests)
                if exposed_requests else 0.0,
                "degraded_requests": sum(request.degraded for request in request_group),
                "unknown_actual_mode_requests": sum(
                    request.requested_mode is None for request in request_group
                ),
                "no_retrieval_requests": sum(
                    request.requested_mode is not None and request.actual_mode is None
                    for request in request_group
                ),
                "actual_mode_counts": {
                    mode: sum(request.actual_mode == mode for request in request_group)
                    for mode in ("bm25", "vector", "hybrid")
                },
            }
        return {
            "experiment_id": self.config.experiment_id,
            "variants": report,
            "note": "Offline event replay; synthetic data is not an online lift result.",
        }
