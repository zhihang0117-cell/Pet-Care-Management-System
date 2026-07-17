"""Production DOCX ingestion wrapper (format validation only)."""

from __future__ import annotations

from pathlib import Path

from app.services.ingestion import is_supported_document, load_document

SUPPORTED_FILE_EXTENSIONS = {".docx"}


def validate_docx_file(path: Path) -> None:
    """Ensure the local file is a supported Word document."""
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    if not is_supported_document(path.name):
        supported = ", ".join(sorted(SUPPORTED_FILE_EXTENSIONS))
        raise ValueError(
            f"Unsupported file format: {path.name}. Supported extensions: {supported}"
        )


def load_docx_pages(path: Path):
    """Load a DOCX file into LangChain pages without touching experiment corpus files."""
    validate_docx_file(path)
    pages = load_document(path)
    if not pages:
        raise ValueError(f"No extractable text found in document: {path.name}")
    return pages
