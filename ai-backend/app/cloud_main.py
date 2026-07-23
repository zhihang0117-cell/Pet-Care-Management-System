from fastapi import FastAPI

from app.api.production.documents import router as documents_router
from app.api.production.rag import router as rag_router

app = FastAPI(
    title="Pawfect AI",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "Pawfect AI",
    }


app.include_router(documents_router, prefix="/api")
app.include_router(rag_router, prefix="/api")