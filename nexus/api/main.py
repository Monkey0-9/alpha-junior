from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import Dict, AsyncGenerator, Callable, Awaitable, Any
import logging
import os
import hmac
from nexus.api.alpaca_router import router as alpaca_router
from nexus.api.monitor_router import router as monitor_router
from nexus.execution.alpaca import get_client
from nexus.utils.config import Config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Initializing Nexus API Backend...")
    valid, missing = Config.validate()
    if not valid:
        logger.warning(
            "Alpaca credentials missing. " "Execution routes disabled until configured."
        )

    client = get_client()
    if client.enabled:
        try:
            acc = await client.get_account()
            status = acc.get("status")
            if status == "ACTIVE":
                logger.info("Alpaca execution link established.")
            else:
                error_msg = acc.get("error", "Unknown Error")
                logger.warning(f"Alpaca status: {status} | Error: {error_msg}")
                if status == "UNAUTHORIZED":
                    logger.error(
                        "CRITICAL: Alpaca API Keys invalid. " "Check .env file."
                    )
        except Exception as exc:
            logger.warning(f"Unable to verify Alpaca account: {exc}")

    yield
    await get_client().close()
    logger.info("Nexus API Backend shutdown complete.")


app = FastAPI(
    title="Nexus Institutional API",
    description="Unified API for market data and execution.",
    version="2.0.0",
    lifespan=lifespan,
)


# --- Fail-closed API authentication middleware ---
@app.middleware("http")
async def api_key_auth(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    """Authenticate every non-health route; never treat an unset key as dev access."""
    logger.info("API Request: %s %s", request.method, request.url.path)

    # Liveness-only endpoints expose no account, portfolio, signal, or execution data.
    safe_paths = {"/api/health", "/api/alpaca/health", "/api/monitor/health"}
    if request.method == "OPTIONS" or request.url.path in safe_paths:
        return await call_next(request)

    expected_key = os.getenv("NEXUS_API_KEY", "").strip()
    placeholder_keys = {
        "changeme",
        "your_api_key",
        "your_secure_api_key",
        "generate_a_secure_random_key_for_internal_auth",
    }
    if not expected_key or expected_key.lower() in placeholder_keys:
        return JSONResponse(
            status_code=503,
            content={"detail": "API authentication is not configured; set NEXUS_API_KEY."},
        )

    provided_key = request.headers.get("X-API-Key", "")
    if not provided_key or not hmac.compare_digest(
        provided_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)


# --- CORS — Restricted to Streamlit dashboard ---
allowed_origins = [
    f"http://localhost:{Config.STREAMLIT_PORT}",
    f"http://127.0.0.1:{Config.STREAMLIT_PORT}",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "api": "healthy",
        "database": "unknown",
        "broker": "unknown",
    }
    overall_status = "healthy"

    # 1. Database check
    try:
        from pathlib import Path
        import sqlite3

        db_path = Path("data/nexus_audit.db")
        if not db_path.exists():
            checks["database"] = "uninitialized"
            overall_status = "degraded"
        else:
            conn = sqlite3.connect(str(db_path), timeout=2)
            conn.execute("SELECT 1 FROM audit_log LIMIT 1")
            conn.close()
            checks["database"] = "healthy"
    except Exception as exc:
        checks["database"] = f"error: {str(exc)}"
        overall_status = "degraded"

    # 2. Broker connection check
    try:
        client = get_client()
        if not client.enabled:
            checks["broker"] = "disabled"
            overall_status = "degraded"
        elif client.simulated:
            checks["broker"] = "simulated"
            overall_status = "degraded"
        else:
            acc = await client.get_account()
            if acc.get("status") in {"ACTIVE", "healthy"}:
                checks["broker"] = "connected"
            else:
                checks["broker"] = acc.get("status", "error")
                overall_status = "degraded"
    except Exception as exc:
        checks["broker"] = f"error: {str(exc)}"
        overall_status = "degraded"

    return {
        "status": overall_status,
        "service": "Nexus Institutional API",
        "version": "2.0.0",
        "checks": checks,
    }


app.include_router(alpaca_router)
app.include_router(monitor_router)

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    uvicorn.run(app, host=Config.API_HOST, port=Config.API_PORT)
