"""AgentPay Guard API entry point.

Run locally from the ``backend/`` directory:

    uvicorn app.main:app --reload --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_payment_error_handlers
from app.api.router import api_router
from app.api.routes.audit import get_audit_recorder
from app.api.routes.intent import intent_error_handler
from app.core.config import get_settings
from app.services.intent import IntentExtractionError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Flush pending audit events to Elastic (best effort) and close the client.
    try:
        await get_audit_recorder(get_settings()).aclose()
    except Exception:  # noqa: BLE001
        pass


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="AgentPay Guard API",
        description="Let AI transact. Let humans stay in control.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(IntentExtractionError, intent_error_handler)
    register_payment_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
