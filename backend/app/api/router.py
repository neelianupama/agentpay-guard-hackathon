"""Top-level API router.

Each feature area gets its own module in ``app/api/routes/`` and is
registered here. Operational endpoints (``/health``) are unversioned;
feature endpoints are mounted under ``/api/v1``, e.g. ``risk`` and ``intent`` now and
future ``payments``, ``agents``, ``approvals`` and ``audit`` routers.
"""

from fastapi import APIRouter

from app.api.routes import audit, health, intent, payments, risk

API_V1_PREFIX = "/api/v1"

v1_router = APIRouter(prefix=API_V1_PREFIX)
v1_router.include_router(risk.router)
v1_router.include_router(intent.router)
v1_router.include_router(payments.router)
v1_router.include_router(audit.router)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(v1_router)
