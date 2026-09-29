from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import chat, health


def create_app():
    app = FastAPI(title="SPIDEY", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat.router)
    app.include_router(health.router)

    @app.get("/")
    async def root():
        return {"service": "spidey", "phase": 1}

    return app


app = create_app()
