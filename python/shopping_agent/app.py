"""HTTP entry point for the runnable shopping assistant."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .config import Settings
from .schemas import EventRequest, ShopRequest, ShopResponse
from .workflow import ShoppingService, create_service


def create_app(settings: Settings | None = None, service: ShoppingService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.service is None:
            app.state.service = create_service(settings)
        yield

    app = FastAPI(
        title="Shopping Agent API",
        description="可检索商品证据、核查库存的电商导购演示框架",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, int | str]:
        count = len(app.state.service.store.list_products(in_stock_only=False))
        return {"status": "healthy", "catalog_products": count}

    @app.get("/api/v1/catalog")
    def catalog(
        category: str | None = None,
        max_price: float | None = Query(default=None, gt=0),
        limit: int = Query(default=30, ge=1, le=100),
    ) -> list[dict]:
        products = app.state.service.store.list_products(category, max_price, True)
        return [
            {
                "product_id": product.product_id,
                "name": product.name,
                "category": product.category,
                "price": product.price,
                "stock": product.stock,
            }
            for product in products[:limit]
        ]

    @app.post("/api/v1/events", status_code=201)
    def record_event(event: EventRequest) -> dict[str, str]:
        try:
            if event.request_id is not None:
                if app.state.service.experiments is None:
                    raise HTTPException(
                        status_code=409, detail="A/B experiment is not enabled"
                    )
                if event.event_type == "view":
                    raise HTTPException(
                        status_code=422, detail="Exposure is recorded by recommendation requests"
                    )
                app.state.service.experiments.record_event(
                    event.user_id, event.request_id, event.product_id,
                    event.event_type,
                )
                # The attributed event remains valid if an exposed item was
                # deleted before a click; profile storage is best effort.
                if app.state.service.store.get_product(event.product_id) is not None:
                    try:
                        app.state.service.store.record_event(
                            event.user_id, event.product_id, event.event_type
                        )
                    except ValueError:
                        pass
            else:
                app.state.service.store.record_event(
                    event.user_id, event.product_id, event.event_type
                )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "recorded"}

    @app.post("/api/v1/shop/recommend", response_model=ShopResponse)
    async def recommend(request: ShopRequest) -> ShopResponse:
        return await app.state.service.recommend(request)

    return app


app = create_app()
