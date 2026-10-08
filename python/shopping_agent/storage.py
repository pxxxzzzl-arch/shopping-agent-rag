"""SQLite backed catalog and interaction history for the shopping agent.

Every public operation owns its SQLAlchemy session.  The returned ``Product``
objects are immutable snapshots, so callers never retain a live ORM object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, create_engine, delete, event, func, select
from sqlalchemy.orm import registry, relationship, sessionmaker
from sqlalchemy.pool import QueuePool


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    name: str
    category: str
    price: float
    stock: int
    description: str
    tags: tuple[str, ...]
    reviews: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Faq:
    faq_id: str
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """A current, versioned citation with the exact imported line for audit."""

    source_id: str
    product_id: str | None
    source_type: str
    text: str
    source_url: str | None
    license: str
    updated_at: str
    version: int
    original_text: str
    faq_id: str | None = None


def verbatim_source_text(document: SourceDocument) -> str:
    """Return one cited field exactly as it appeared in the imported JSONL.

    Search indexes intentionally join fields for retrieval. A joined search
    string is not itself a quotation of the original product or FAQ record.
    """
    try:
        row = json.loads(document.original_text)
        if document.source_type == "description":
            value = row["description"]
        elif document.source_type == "answer":
            value = row["answer"]
        elif document.source_type == "review":
            review_index = int(document.source_id.split(":review:", 1)[1].split(":", 1)[0]) - 1
            value = row["reviews"][review_index]
        else:
            return ""
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return ""
    return value if isinstance(value, str) and value in document.original_text else ""


Base = registry().generate_base()


class ProductRow(Base):
    __tablename__ = "products"

    product_id = Column(String(80), primary_key=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False, index=True)
    price_cents = Column(Integer, nullable=False)
    stock = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)
    tags_json = Column(Text, nullable=False)
    reviews_json = Column(Text, nullable=False)
    events = relationship("InteractionRow", back_populates="product")


class InteractionRow(Base):
    __tablename__ = "interaction_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    product_id = Column(
        ForeignKey("products.product_id"), nullable=False, index=True
    )
    event_type = Column(String(40), nullable=False)
    product = relationship("ProductRow", back_populates="events")


class FaqRow(Base):
    __tablename__ = "faqs"

    faq_id = Column(String(80), primary_key=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)


class DocumentRevisionRow(Base):
    __tablename__ = "document_revisions"

    entity_key = Column(String(180), primary_key=True)
    version = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    deleted = Column(Boolean, nullable=False, default=False)


class SourceRow(Base):
    __tablename__ = "source_documents"

    source_id = Column(String(240), primary_key=True)
    entity_key = Column(String(180), nullable=False, index=True)
    product_id = Column(String(80), nullable=True, index=True)
    faq_id = Column(String(80), nullable=True, index=True)
    source_type = Column(String(40), nullable=False)
    text = Column(Text, nullable=False)
    source_url = Column(Text, nullable=True)
    license = Column(String(200), nullable=False)
    updated_at = Column(String(40), nullable=False)
    version = Column(Integer, nullable=False)
    original_text = Column(Text, nullable=False)


def _snapshot(row: ProductRow) -> Product:
    return Product(
        product_id=row.product_id,
        name=row.name,
        category=row.category,
        price=row.price_cents / 100,
        stock=row.stock,
        description=row.description,
        tags=tuple(json.loads(row.tags_json)),
        reviews=tuple(json.loads(row.reviews_json)),
    )


def _parse_product(data: dict[str, Any], line_number: int) -> Product:
    try:
        required_text = (
            data["product_id"], data["name"], data["category"], data["description"]
        )
        if not all(isinstance(value, str) for value in required_text):
            raise ValueError("product_id, name, category and description must be strings")
        product_id, name, category, description = (
            value.strip() for value in required_text
        )
        raw_price = data["price"]
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
            raise ValueError("price must be a decimal amount")
        price = Decimal(str(raw_price))
        if not price.is_finite() or price * 100 != (price * 100).to_integral_value():
            raise ValueError("price must be finite and have at most two decimal places")
        price_cents = int(price * 100)
        stock = data["stock"]
        if type(stock) is not int:
            raise ValueError("stock must be an integer")
        tags = data["tags"]
        reviews = data["reviews"]
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation) as exc:
        raise ValueError(f"Invalid product on JSONL line {line_number}: {exc}") from exc

    if not all((product_id, name, category, description)):
        raise ValueError(f"Empty required field on JSONL line {line_number}")
    if price_cents < 0 or stock < 0:
        raise ValueError(f"Negative price or stock on JSONL line {line_number}")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError(f"Invalid tags on JSONL line {line_number}")
    if not isinstance(reviews, list) or not all(isinstance(r, str) for r in reviews):
        raise ValueError(f"Invalid reviews on JSONL line {line_number}")

    return Product(
        product_id=product_id,
        name=name,
        category=category,
        price=price_cents / 100,
        stock=stock,
        description=description,
        tags=tuple(tags),
        reviews=tuple(reviews),
    )


def _parse_faq(data: dict[str, Any], line_number: int) -> Faq:
    try:
        values = (data["faq_id"], data["question"], data["answer"])
    except KeyError as exc:
        raise ValueError(f"Invalid FAQ on JSONL line {line_number}: missing {exc}") from exc
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"Invalid FAQ on JSONL line {line_number}: fields must be nonempty strings")
    faq_id, question, answer = (value.strip() for value in values)
    if len(faq_id) > 80:
        raise ValueError(f"Invalid FAQ on JSONL line {line_number}: faq_id is too long")
    return Faq(faq_id=faq_id, question=question, answer=answer)


def _parse_metadata(
    data: dict[str, Any], line_number: int, *, require_provenance: bool
) -> tuple[str | None, str, str | None]:
    source_url = data.get("source_url")
    if require_provenance and data.get("data_origin") != "synthetic_demo":
        if source_url is None or data.get("license") is None:
            raise ValueError(f"source_url and license are required on JSONL line {line_number}")
    if source_url is not None:
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"Invalid source_url on JSONL line {line_number}")
        source_url = source_url.strip()
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https", "file"} or (
            parsed.scheme in {"http", "https"} and not parsed.netloc
        ):
            raise ValueError(f"Invalid source_url on JSONL line {line_number}")

    license_name = data.get("license")
    if license_name is None:
        license_name = "synthetic_demo" if data.get("data_origin") == "synthetic_demo" else "unspecified"
    if not isinstance(license_name, str) or not license_name.strip() or len(license_name) > 200:
        raise ValueError(f"Invalid license on JSONL line {line_number}")

    raw_updated_at = data.get("updated_at")
    if require_provenance and data.get("data_origin") != "synthetic_demo" and raw_updated_at is None:
        raise ValueError(
            f"updated_at is required for non-synthetic sources on JSONL line {line_number}"
        )
    updated_at: str | None = None
    if raw_updated_at is not None:
        if not isinstance(raw_updated_at, str):
            raise ValueError(f"Invalid updated_at on JSONL line {line_number}")
        try:
            timestamp = datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timezone is required")
            updated_at = timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError as exc:
            raise ValueError(f"Invalid updated_at on JSONL line {line_number}: {exc}") from exc
    return source_url, license_name.strip(), updated_at


def _source_snapshot(row: SourceRow) -> SourceDocument:
    return SourceDocument(
        source_id=row.source_id,
        product_id=row.product_id,
        faq_id=row.faq_id,
        source_type=row.source_type,
        text=row.text,
        source_url=row.source_url,
        license=row.license,
        updated_at=row.updated_at,
        version=row.version,
        original_text=row.original_text,
    )


def _fingerprint(kind: str, item: Product | Faq, metadata: tuple[str | None, str, str | None]) -> str:
    if isinstance(item, Product):
        payload: dict[str, Any] = {
            "product_id": item.product_id,
            "name": item.name,
            "category": item.category,
            "price_cents": round(item.price * 100),
            "stock": item.stock,
            "description": item.description,
            "tags": item.tags,
            "reviews": item.reviews,
        }
    else:
        payload = {"faq_id": item.faq_id, "question": item.question, "answer": item.answer}
    payload.update(kind=kind, source_url=metadata[0], license=metadata[1], updated_at=metadata[2])
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_document_jsonl(
    path: str | Path, allowed_kinds: set[str], *, require_provenance: bool
) -> list[tuple[str, Product | Faq, tuple[str | None, str, str | None], str, str]]:
    parsed: list[tuple[str, Product | Faq, tuple[str | None, str, str | None], str, str]] = []
    seen_keys: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object")
                kind = data.get("kind", "faq" if "faq_id" in data and "product_id" not in data else "product")
                if not isinstance(kind, str):
                    raise ValueError("kind must be a string")
                if kind not in allowed_kinds:
                    raise ValueError(f"kind must be one of {sorted(allowed_kinds)}")
                item = _parse_product(data, line_number) if kind == "product" else _parse_faq(data, line_number)
                metadata = _parse_metadata(data, line_number, require_provenance=require_provenance)
                item_id = item.product_id if isinstance(item, Product) else item.faq_id
                entity_key = f"{kind}:{item_id}"
                if entity_key in seen_keys:
                    raise ValueError(f"Duplicate {kind} ID on JSONL line {line_number}")
                seen_keys.add(entity_key)
                parsed.append((kind, item, metadata, line.rstrip("\r\n"), _fingerprint(kind, item, metadata)))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid JSONL line {line_number}: {exc}") from exc
    return parsed


class CatalogStore:
    """Persistent catalog with small, synchronous operations.

    ``get_stock`` returns zero for an unknown product, allowing the caller to
    treat a missing catalog entry as unavailable.  Seed operations are atomic
    and idempotent: a product ID is updated on a repeated seed.
    """

    def __init__(self, database_url: str):
        engine_options: dict[str, Any] = {}
        if database_url.startswith("sqlite:"):
            engine_options["connect_args"] = {"check_same_thread": False}
            if database_url in {"sqlite://", "sqlite:///:memory:"}:
                # One in-memory database needs one connection. Queue its
                # checkouts so concurrent sessions cannot interleave reads,
                # commits or rollbacks on the same DBAPI transaction.
                engine_options.update(poolclass=QueuePool, pool_size=1, max_overflow=0)

        self.engine = create_engine(database_url, **engine_options)
        if database_url.startswith("sqlite:"):
            @event.listens_for(self.engine, "connect")
            def _enable_foreign_keys(dbapi_connection: Any, _: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def seed_from_jsonl(self, path: str | Path, *, overwrite: bool = True) -> int:
        """Load legacy product JSONL atomically, preserving initial citation IDs."""

        return self._import_jsonl(path, {"product"}, overwrite=overwrite, require_provenance=False)

    def seed_faq_from_jsonl(self, path: str | Path, *, overwrite: bool = True) -> int:
        """Load FAQ JSONL atomically. Demo seeding uses ``overwrite=False``."""

        return self._import_jsonl(path, {"faq"}, overwrite=overwrite, require_provenance=True)

    def import_documents_jsonl(self, path: str | Path) -> int:
        """Atomically import a mixed product/FAQ JSONL document batch.

        The optional ``kind`` field selects ``product`` or ``faq``. Product
        records retain the legacy format; FAQ records need ``faq_id``,
        ``question``, and ``answer``. Each record may include ``source_url``,
        ``license``, and timezone-aware ISO-8601 ``updated_at``. All three
        are required for non-synthetic sources. A changed
        record receives a new local version and new source IDs. Old source IDs
        are removed in the same transaction.
        """

        return self._import_jsonl(path, {"product", "faq"}, require_provenance=True)

    def _import_jsonl(
        self, path: str | Path, allowed_kinds: set[str], *,
        overwrite: bool = True, require_provenance: bool,
    ) -> int:
        records = _read_document_jsonl(path, allowed_kinds, require_provenance=require_provenance)
        with self._sessions.begin() as session:
            for kind, item, metadata, original_text, fingerprint in records:
                entity_id = item.product_id if isinstance(item, Product) else item.faq_id
                entity_key = f"{kind}:{entity_id}"
                revision = session.get(DocumentRevisionRow, entity_key)
                existing = session.get(ProductRow if kind == "product" else FaqRow, entity_id)
                if not overwrite and (existing is not None or revision is not None):
                    continue
                if revision is not None and not revision.deleted and revision.fingerprint == fingerprint and existing is not None:
                    continue
                version = revision.version + 1 if revision is not None else 1
                if revision is None:
                    revision = DocumentRevisionRow(entity_key=entity_key, version=version, fingerprint=fingerprint, deleted=False)
                    session.add(revision)
                else:
                    revision.version = version
                    revision.fingerprint = fingerprint
                    revision.deleted = False

                session.execute(delete(SourceRow).where(SourceRow.entity_key == entity_key))
                if kind == "product":
                    assert isinstance(item, Product)
                    row = existing or ProductRow(product_id=item.product_id)
                    if existing is None:
                        session.add(row)
                    row.name = item.name
                    row.category = item.category
                    row.price_cents = round(item.price * 100)
                    row.stock = item.stock
                    row.description = item.description
                    row.tags_json = json.dumps(item.tags, ensure_ascii=False)
                    row.reviews_json = json.dumps(item.reviews, ensure_ascii=False)
                    source_specs = [
                        (f"{item.product_id}:description", "description", f"{item.name} {item.category} {' '.join(item.tags)} {item.description}"),
                        *[
                            (f"{item.product_id}:review:{index}", "review", review)
                            for index, review in enumerate(item.reviews, start=1)
                        ],
                    ]
                else:
                    assert isinstance(item, Faq)
                    row = existing or FaqRow(faq_id=item.faq_id)
                    if existing is None:
                        session.add(row)
                    row.question = item.question
                    row.answer = item.answer
                    source_specs = [(f"{item.faq_id}:answer", "answer", f"{item.question} {item.answer}")]

                source_url, license_name, updated_at = metadata
                effective_updated_at = updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
                for base_source_id, source_type, text in source_specs:
                    source_id = base_source_id if version == 1 else f"{base_source_id}:v{version}"
                    session.add(
                        SourceRow(
                            source_id=source_id,
                            entity_key=entity_key,
                            product_id=entity_id if kind == "product" else None,
                            faq_id=entity_id if kind == "faq" else None,
                            source_type=source_type,
                            text=text,
                            source_url=source_url,
                            license=license_name,
                            updated_at=effective_updated_at,
                            version=version,
                            original_text=original_text,
                        )
                    )
        return len(records)

    def list_documents(self) -> list[SourceDocument]:
        """Return only current citations, suitable for rebuilding any index."""

        with self._sessions() as session:
            rows = session.scalars(select(SourceRow).order_by(SourceRow.source_id)).all()
            return [
                _source_snapshot(row)
                for row in rows
                if self._is_current_source(session, row)
            ]

    def get_document(self, source_id: str) -> SourceDocument | None:
        """Resolve a citation to its original import line if still current."""

        with self._sessions() as session:
            row = session.get(SourceRow, source_id)
            return _source_snapshot(row) if row is not None and self._is_current_source(session, row) else None

    def get_product_description_document(self, product_id: str) -> SourceDocument | None:
        """Return the current authoritative catalog description for one product."""

        with self._sessions() as session:
            row = session.scalars(
                select(SourceRow).where(
                    SourceRow.product_id == product_id,
                    SourceRow.source_type == "description",
                )
            ).first()
            return _source_snapshot(row) if row is not None and self._is_current_source(session, row) else None

    def get_product_documents(self, product_id: str) -> list[SourceDocument]:
        """Return current description and reviews for one product, never old versions."""

        with self._sessions() as session:
            rows = session.scalars(
                select(SourceRow).where(SourceRow.product_id == product_id)
                .order_by(SourceRow.source_id)
            )
            return [
                _source_snapshot(row) for row in rows
                if self._is_current_source(session, row)
            ]

    def catalog_snapshot_source(self, product_id: str) -> tuple[str, str] | None:
        """Identify the current authoritative price, stock and category row.

        The content digest changes when those fields change, including changes
        made without a document import. This is a catalog fact source rather
        than a merchant description or a claim that imported prose is current.
        """

        with self._sessions() as session:
            row = session.get(ProductRow, product_id)
            if row is None:
                return None
            payload = json.dumps(
                [row.product_id, row.category, row.price_cents, row.stock],
                ensure_ascii=False, separators=(",", ":"),
            )
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
            source_id = f"catalog:{product_id}:sha256:{digest}"
            excerpt = (
                f"当前商品库记录：类别={row.category}；"
                f"价格={row.price_cents / 100:g}元；库存={row.stock}件。"
            )
            return source_id, excerpt

    def is_current_catalog_snapshot(self, source_id: str) -> bool:
        """Reject a catalog fact ID after price, stock, category or deletion changes."""

        if not source_id.startswith("catalog:") or ":sha256:" not in source_id:
            return False
        product_id = source_id[len("catalog:"):].rsplit(":sha256:", 1)[0]
        current = self.catalog_snapshot_source(product_id)
        return current is not None and current[0] == source_id

    def document_revision_signature(self) -> tuple[tuple[str, int, bool], ...]:
        """Cheap cross-process change token for rebuilding in-memory indexes."""
        with self._sessions() as session:
            rows = session.scalars(
                select(DocumentRevisionRow).order_by(DocumentRevisionRow.entity_key)
            )
            return tuple(
                (row.entity_key, row.version, bool(row.deleted)) for row in rows
            )

    get_source = get_document
    list_sources = list_documents

    @staticmethod
    def _is_current_source(session: Any, row: SourceRow) -> bool:
        revision = session.get(DocumentRevisionRow, row.entity_key)
        if revision is None or revision.deleted or revision.version != row.version:
            return False
        if row.product_id is not None:
            return session.get(ProductRow, row.product_id) is not None
        return row.faq_id is not None and session.get(FaqRow, row.faq_id) is not None

    def list_faqs(self) -> list[Faq]:
        with self._sessions() as session:
            rows = session.scalars(select(FaqRow).order_by(FaqRow.faq_id))
            return [Faq(row.faq_id, row.question, row.answer) for row in rows]

    def get_faq(self, faq_id: str) -> Faq | None:
        with self._sessions() as session:
            row = session.get(FaqRow, faq_id)
            return Faq(row.faq_id, row.question, row.answer) if row is not None else None

    def delete_product(self, product_id: str) -> bool:
        """Delete a product and its current citations; keep the version tombstone."""

        with self._sessions.begin() as session:
            row = session.get(ProductRow, product_id)
            if row is None:
                return False
            session.execute(delete(InteractionRow).where(InteractionRow.product_id == product_id))
            session.delete(row)
            entity_key = f"product:{product_id}"
            session.execute(delete(SourceRow).where(SourceRow.entity_key == entity_key))
            revision = session.get(DocumentRevisionRow, entity_key)
            if revision is None:
                revision = DocumentRevisionRow(entity_key=entity_key, version=1, fingerprint="", deleted=True)
                session.add(revision)
            else:
                revision.deleted = True
        return True

    def delete_faq(self, faq_id: str) -> bool:
        """Delete an FAQ and invalidate every current source ID for it."""

        with self._sessions.begin() as session:
            row = session.get(FaqRow, faq_id)
            if row is None:
                return False
            session.delete(row)
            entity_key = f"faq:{faq_id}"
            session.execute(delete(SourceRow).where(SourceRow.entity_key == entity_key))
            revision = session.get(DocumentRevisionRow, entity_key)
            if revision is None:
                revision = DocumentRevisionRow(entity_key=entity_key, version=1, fingerprint="", deleted=True)
                session.add(revision)
            else:
                revision.deleted = True
        return True

    def list_products(
        self,
        category: str | None = None,
        max_price: float | None = None,
        in_stock_only: bool = True,
    ) -> list[Product]:
        query = select(ProductRow)
        if category is not None:
            query = query.where(ProductRow.category == category)
        if max_price is not None:
            if max_price < 0:
                return []
            query = query.where(ProductRow.price_cents <= round(max_price * 100))
        if in_stock_only:
            query = query.where(ProductRow.stock > 0)
        query = query.order_by(ProductRow.product_id)
        with self._sessions() as session:
            return [_snapshot(row) for row in session.scalars(query)]

    def get_product(self, product_id: str) -> Product | None:
        with self._sessions() as session:
            row = session.get(ProductRow, product_id)
            return _snapshot(row) if row is not None else None

    def get_stock(self, product_id: str) -> int:
        with self._sessions() as session:
            stock = session.scalar(
                select(ProductRow.stock).where(ProductRow.product_id == product_id)
            )
            return stock if stock is not None else 0

    def record_event(self, user_id: str, product_id: str, event_type: str) -> None:
        if not user_id.strip() or not event_type.strip():
            raise ValueError("user_id and event_type must be nonempty")
        with self._sessions.begin() as session:
            if session.get(ProductRow, product_id) is None:
                raise ValueError(f"Unknown product_id: {product_id}")
            session.add(
                InteractionRow(
                    user_id=user_id,
                    product_id=product_id,
                    event_type=event_type,
                )
            )

    def get_user_category_counts(self, user_id: str) -> dict[str, int]:
        query = (
            select(ProductRow.category, func.count(InteractionRow.id))
            .join(InteractionRow.product)
            .where(InteractionRow.user_id == user_id)
            .group_by(ProductRow.category)
        )
        with self._sessions() as session:
            return {category: count for category, count in session.execute(query)}
