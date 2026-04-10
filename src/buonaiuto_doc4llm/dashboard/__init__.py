"""Buonaiuto Doc4LLM web dashboard — FastAPI + Jinja2 + HTMX."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from buonaiuto_doc4llm.service import DocsHubService

_HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"


def filesizeformat(value: int | None) -> str:
    """Jinja2 filter: format bytes as human-readable size."""
    if value is None:
        return "\u2014"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024  # type: ignore[assignment]
    return f"{value:.1f} TB"


def create_app(base_dir: Path | str) -> FastAPI:
    """Create the FastAPI dashboard application."""
    import logging
    logger = logging.getLogger(__name__)

    base = Path(base_dir)

    # Try to set up vector search (Qdrant + embeddings)
    retriever = None
    indexer = None
    vector_info: dict = {}
    try:
        from buonaiuto_doc4llm.vector_setup import create_qdrant_retriever_and_indexer
        vector_info = create_qdrant_retriever_and_indexer(base)
        retriever = vector_info.get("retriever")
        indexer = vector_info.get("indexer")
        if vector_info.get("provider_name"):
            logger.info(
                "Vector search enabled: provider=%s, qdrant=%s",
                vector_info["provider_name"], vector_info.get("qdrant_path"),
            )
        else:
            logger.info("Vector search not available, using lexical search")
    except Exception as exc:
        logger.warning("Vector setup failed: %s — using lexical search", exc)

    service = DocsHubService(base, retriever=retriever, indexer=indexer)

    app = FastAPI(title="Buonaiuto Doc4LLM", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters["filesizeformat"] = filesizeformat

    # Attach to app state for routes
    app.state.service = service
    app.state.base_dir = base
    app.state.templates = templates
    app.state.vector_info = vector_info

    from buonaiuto_doc4llm.dashboard.routes import register_routes
    register_routes(app)

    return app
