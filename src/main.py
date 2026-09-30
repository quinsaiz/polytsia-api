import asyncio
import logging
import logging.config
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.auth.router import router as auth_router
from src.config import settings
from src.database import engine
from src.games.router import router as games_router
from src.movies.router import router as movies_router
from src.recommendations.router import router as recommendations_router
from src.tierlists.router import router as tierlists_router

logger = logging.getLogger(__name__)

_LOG_CONFIG = Path("logging.ini")
READINESS_TIMEOUT_SECONDS = 1.0


def _setup_logging() -> None:
    if _LOG_CONFIG.exists():
        logging.config.fileConfig(_LOG_CONFIG, disable_existing_loggers=False)
    else:
        logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    _setup_logging()
    logger.info("Starting up %s...", settings.app_name)
    logger.info("Debug mode: %s", settings.debug)

    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=10,
        ),
    )

    yield

    await app.state.http_client.aclose()
    logger.info("Shutting down %s...", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Personal media tracker (movies, games, anime, series) with Tier Lists",
    debug=settings.debug,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(movies_router, prefix="/api/v1")
app.include_router(games_router, prefix="/api/v1")
app.include_router(recommendations_router, prefix="/api/v1")
app.include_router(tierlists_router, prefix="/api/v1")


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/ready", tags=["System"], responses={503: {"description": "Database unavailable"}}
)
async def readiness_check() -> dict[str, str]:
    try:
        # Bound connection acquisition and the query, including pool exhaustion.
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as error:
        # Driver errors may contain connection details; expose only a fixed message.
        logger.warning("Readiness database check failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from None
    return {"status": "ready"}
