"""Memory SQLite sessions must not share an active DBAPI transaction."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from sqlalchemy import insert

from shopping_agent.seed import seed_demo_data
from shopping_agent.storage import CatalogStore, InteractionRow


@pytest.mark.parametrize("database_url", ["sqlite://", "sqlite:///:memory:"])
def test_concurrent_reader_cannot_rollback_or_see_uncommitted_event(database_url):
    store = CatalogStore(database_url)
    seed_demo_data(store)
    product = store.list_products()[0]
    inserted, read_started, commit_allowed = Event(), Event(), Event()

    def write():
        with store.engine.begin() as connection:
            connection.execute(insert(InteractionRow).values(
                user_id="concurrent-user", product_id=product.product_id,
                event_type="click",
            ))
            inserted.set()
            assert commit_allowed.wait(5), "reader did not release writer"

    def read():
        read_started.set()
        return store.get_user_category_counts("concurrent-user")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            assert inserted.wait(5)
            reader = pool.submit(read)
            read_while_uncommitted = False
            try:
                assert read_started.wait(5)
                try:
                    reader.result(timeout=.2)
                    read_while_uncommitted = True
                except TimeoutError:
                    pass
            finally:
                commit_allowed.set()
            writer.result(timeout=5)
            counts = reader.result(timeout=5)
        expected = {product.category: 1}
        assert store.get_user_category_counts("concurrent-user") == expected
        assert counts == expected
        assert not read_while_uncommitted, "reader used the writer's active connection"
    finally:
        commit_allowed.set()
        store.engine.dispose()
