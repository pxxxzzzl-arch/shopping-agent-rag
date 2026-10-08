"""Local multi-turn state, scoped by user and explicit conversation ID."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select
from sqlalchemy.engine import Engine


_metadata = MetaData()
_turns = Table(
    "shopping_conversation_turns",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("conversation_id", String(128), nullable=False, index=True),
    Column("request_id", String(40), nullable=False, unique=True),
    Column("created_at", String(40), nullable=False),
    Column("query", Text, nullable=False),
    Column("route", String(16), nullable=False),
    Column("product_ids_json", Text, nullable=False),
    Column("source_ids_json", Text, nullable=False),
)


@dataclass(frozen=True)
class ConversationTurn:
    request_id: str
    query: str
    route: str
    product_ids: tuple[str, ...]
    source_ids: tuple[str, ...]


class ConversationStore:
    def __init__(self, engine: Engine):
        self.engine = engine
        _metadata.create_all(engine)

    def recent(self, user_id: str, conversation_id: str, limit: int = 5) -> list[ConversationTurn]:
        statement = (
            select(_turns)
            .where(_turns.c.user_id == user_id, _turns.c.conversation_id == conversation_id)
            .order_by(_turns.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as connection:
            rows = list(connection.execute(statement))
        return [
            ConversationTurn(
                request_id=row.request_id,
                query=row.query,
                route=row.route,
                product_ids=tuple(json.loads(row.product_ids_json)),
                source_ids=tuple(json.loads(row.source_ids_json)),
            )
            for row in reversed(rows)
        ]

    def append(
        self, user_id: str, conversation_id: str, request_id: str,
        query: str, route: str, product_ids: list[str], source_ids: list[str],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(_turns.insert().values(
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                query=query,
                route=route,
                product_ids_json=json.dumps(product_ids),
                source_ids_json=json.dumps(source_ids),
            ))
