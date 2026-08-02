"""
Section-aware DOCX chunking for the company-document RAG index.

Ported and consolidated from the retired ai-backend/app/services/chunking.py +
ai-backend/app/rag/chunking.py (pre-restructure commit 2d5ecb4^). The original
split this into a generic "experiment" chunker plus a thin production wrapper
that only existed to support a parallel multi-model benchmarking harness that
no longer exists in this codebase — collapsed here into one function since
production (BGE-Large, single model) is the only caller now. The chunking
algorithm itself (numbered-section detection, recursive fallback for
oversized sections, header hierarchy) is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.documents.metadata_tagging import tag_chunk_metadata

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
MIN_CHUNK_SIZE = 80
SECTION_SUB_SPLIT = 2000

SECTION_START = re.compile(r"(?:^|\n)\s*(\d+(?:\.\d+){0,2})\s+([^\n]+)", re.MULTILINE)


def _is_boilerplate_only(text: str) -> bool:
    """Reject only TOC/copyright pages — not policy docs that mention copyright."""
    lowered = text.lower().strip()
    if len(lowered) < 200 and "table of contents" in lowered:
        return True
    if len(lowered) < 250 and lowered.startswith("copyright") and "all rights reserved" in lowered:
        return True
    return False


def _is_content_section(section_id: str, text: str) -> bool:
    if len(text.strip()) < MIN_CHUNK_SIZE:
        return False
    return not _is_boilerplate_only(text)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_") or "section"


def _clean_text(text: str) -> str:
    text = re.sub(r"(?<!\n)(\d+\.\d+\.\d+)\s+", r"\n\1 ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _category_for_section(section_id: str) -> str:
    if not section_id:
        return "general"
    return f"chapter_{section_id.split('.')[0]}"


def _section_ancestors(section_id: str) -> list[str]:
    """'2.1.4' -> ['2', '2.1', '2.1.4']."""
    if not section_id:
        return []
    parts = section_id.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _build_title_map(sections: list[dict]) -> dict[str, str]:
    return {s["section_id"]: s["section_title"] for s in sections if s.get("section_id")}


def _structure_metadata(section_id: str, section_title: str, title_map: dict[str, str]) -> dict[str, str]:
    """Derive main_header, sub_header, section_path from numbered section hierarchy."""
    ancestors = _section_ancestors(section_id)
    if not ancestors:
        return {"main_header": "", "sub_header": "", "section_path": ""}

    main_id = ancestors[0]
    main_header = title_map.get(main_id, "")
    if not main_header:
        for sid in ancestors:
            if title_map.get(sid):
                main_header = title_map[sid]
                break
        if not main_header:
            for sid, title in title_map.items():
                if (sid == main_id or sid.startswith(f"{main_id}.")) and title:
                    main_header = title
                    break
        if not main_header:
            main_header = f"Section {main_id}"

    if len(ancestors) >= 2:
        sub_id = ancestors[1]
        sub_header = title_map.get(sub_id, section_title or sub_id)
    else:
        sub_header = section_title or main_header

    path_parts = []
    for sid in ancestors:
        title = title_map.get(sid, "")
        path_parts.append(f"{sid} {title}".strip() if title else sid)

    return {"main_header": main_header, "sub_header": sub_header, "section_path": " > ".join(path_parts)}


def _split_into_sections(full_text: str) -> list[dict]:
    matches = list(SECTION_START.finditer(full_text))
    if not matches:
        return [{"section_id": "", "section_title": "", "text": full_text}]

    sections: list[dict] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        section_id = match.group(1).strip()
        section_title = match.group(2).strip()
        body_start = match.end()
        text = full_text[start:end].strip()
        body = full_text[body_start:end].strip()
        if not body:
            body = text
        sections.append(
            {
                "section_id": section_id,
                "section_title": section_title,
                "text": body if len(body) >= MIN_CHUNK_SIZE else text,
            }
        )
    return sections


def _subsplit_section(section: dict, splitter: RecursiveCharacterTextSplitter) -> list[str]:
    text = section["text"]
    if len(text) <= SECTION_SUB_SPLIT:
        return [text]
    parts = splitter.split_text(text)
    return [p.strip() for p in parts if p.strip()]


def chunk_docx_pages(
    pages: list[Document],
    *,
    company_id: int,
    document_id: str,
    document_type: str,
    service_type: str = "general",
) -> list[dict]:
    """DOCX pages -> section-aware chunks tagged and scoped for one company document.

    Chunk ids are deterministic (document_id + section + part number) so a
    re-upload of the same document_id produces the same ids — required by
    replace_document_chunks_bge_large's atomic (company_id, document_id) swap.
    """
    document_id = str(document_id).strip()
    service_type = str(service_type or "general").strip().lower()
    if service_type not in {"grooming", "boarding", "daycare", "general"}:
        raise ValueError(f"Unsupported service_type '{service_type}'")

    page_texts = [_clean_text(p.page_content) for p in pages if _clean_text(p.page_content)]
    full_text = _clean_text("\n\n".join(page_texts))
    sections = _split_into_sections(full_text)
    title_map = _build_title_map(sections)
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, length_function=len)

    doc_slug = _slug(document_id)
    part_counters: dict[str, int] = {}
    used_chunk_ids: set[str] = set()

    def _unique_chunk_id(base: str) -> str:
        if base not in used_chunk_ids:
            used_chunk_ids.add(base)
            return base
        suffix = 2
        while f"{base}_{suffix}" in used_chunk_ids:
            suffix += 1
        unique = f"{base}_{suffix}"
        used_chunk_ids.add(unique)
        return unique

    chunks: list[dict] = []
    for section in sections:
        section_id = section["section_id"]
        section_title = section["section_title"]
        major_chapter = section_id.split(".")[0] if section_id else ""
        category = _category_for_section(section_id)
        structure = _structure_metadata(section_id, section_title, title_map)

        for part_text in _subsplit_section(section, splitter):
            if not part_text.strip():
                continue

            chunk_is_content = _is_content_section(section_id, part_text)

            counter_key = section_id or "_misc"
            part_counters[counter_key] = part_counters.get(counter_key, 0) + 1
            part_num = part_counters[counter_key]
            section_key = _slug(section_id) if section_id else "misc"
            chunk_id = f"{doc_slug}__{section_key}" if part_num == 1 else f"{doc_slug}__{section_key}_{part_num}"
            chunk_id = _unique_chunk_id(chunk_id)

            embed_text = f"{section_title}:\n\n{part_text}" if section_title else part_text

            tagged = tag_chunk_metadata(
                {
                    "chunk_id": chunk_id,
                    "file_name": document_id,
                    "main_header": structure["main_header"],
                    "sub_header": structure["sub_header"],
                    "section_path": structure["section_path"],
                    "section_title": section_title,
                    "text": embed_text,
                }
            )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "company_id": company_id,
                    "document_id": document_id,
                    # Staff-declared category/service on upload wins over the
                    # per-chunk auto-detected values for these two fields —
                    # pet_type/service_info/headers stay auto-tagged.
                    "dataset_type": document_type,
                    "document_type": document_type,
                    "service_type": service_type,
                    "pet_type": tagged["pet_type"],
                    "service_info": tagged["service_info"],
                    "main_header": tagged["main_header"],
                    "sub_header": tagged["sub_header"],
                    "section_path": tagged["section_path"],
                    "document_file_name": document_id,
                    "language": "unknown",
                    "section_id": section_id,
                    "section_title": section_title,
                    "chapter": major_chapter,
                    "category": category,
                    "is_rule": chunk_is_content,
                    "page": 0,
                    "text": embed_text,
                }
            )

    if not chunks:
        raise ValueError("Chunking produced zero chunks from the document.")
    return chunks
