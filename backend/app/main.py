from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import chat, health, knowledge

logger = logging.getLogger("spidey")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # dev convenience bootstrap; canonical schema path is `alembic upgrade head`
    # Log which embedding provider is active (print: uvicorn's default logging
    # config drops INFO from app loggers, so print guarantees visibility).
    from app.rag.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    print(
        f"[spidey] Embedding provider ACTIVE: {provider.name} "
        f"(dim={provider.dim})",
        flush=True,
    )
    yield


def create_app():
    app = FastAPI(title="SPIDEY", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(health.router)
    app.include_router(knowledge.router)

    @app.get("/")
    async def root():
        return {"service": "spidey", "phase": 3}

    return app


app = create_app()
