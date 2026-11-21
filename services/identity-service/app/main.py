from contextlib import asynccontextmanager

import app.core.platform

from fastapi import FastAPI

from app.api.auth import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Identity Service starting...")
    yield
    print("Identity Service shutting down...")


app = FastAPI(
    title="AI Farm Advisor - Identity Service",
    description="User registration and authentication API.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register authentication endpoints.
app.include_router(auth_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "identity-service",
    }