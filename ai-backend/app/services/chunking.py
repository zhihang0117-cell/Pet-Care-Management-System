"""Section-aware chunking with recursive fallback for oversized subsections."""

import json
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DATA_PROCESSED,
    MIN_CHUNK_SIZE,
    SECTION_SUB_SPLIT,
)
from app.services.metadata_tagging import tag_chunk_metadata

SECTION_START = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+){0,2})\s+([^\n]+)",
    re.MULTILINE,
)

NOISE_MARKERS = (
    "table of contents",
    "all rights reserved",
)


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
    if _is_boilerplate_only(text):
        return False
    return True


def _slugify(name: str) -> str:
    stem = Path(name).stem.lower()
    return re.sub(r"[^a-z0-9]+", "_", stem).strip("_")


def _section_to_slug(section_id: str) -> str:
    return section_id.replace(".", "_")


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
    return {
        s["section_id"]: s["section_title"]
        for s in sections
        if s.get("section_id")
    }


def _structure_metadata(section_id: str, section_title: str, title_map: dict[str, str]) -> dict[str, str]:
    """Derive main_header, sub_header, section_path from numbered section hierarchy."""
    ancestors = _section_ancestors(section_id)
    if not ancestors:
        return {
            "main_header": "",
            "sub_header": "",
            "section_path": "",
        }

    main_id = ancestors[0]
    main_header = title_map.get(main_id, "")
    if not main_header:
        # Top-level heading may be missing; use first titled ancestor/descendant.
        for sid in ancestors:
            if title_map.get(sid):
                main_header = title_map[sid]
                break
        if not main_header:
            for sid, title in title_map.items():
                if sid == main_id or sid.startswith(f"{main_id}."):
                    if title:
                        main_header = title
                        break
        if not main_header:
            main_header = f"Section {main_id}"

    if len(ancestors) >= 2:
        sub_id = ancestors[1]
        sub_header = title_map.get(sub_id, section_title or sub_id)
    else:
        sub_header = section_title or main_header

    path_parts: list[str] = []
    for sid in ancestors:
        title = title_map.get(sid, "")
        path_parts.append(f"{sid} {title}".strip() if title else sid)

    return {
        "main_header": main_header,
        "sub_header": sub_header,
        "section_path": " > ".join(path_parts),
    }


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


def _build_chunks_for_pdf(pages: list, source_file: str, tenant_id: str) -> list[dict]:
    file_slug = _slugify(source_file)

    page_texts = []
    for page_doc in pages:
        cleaned = _clean_text(page_doc.page_content)
        if cleaned:
            page_texts.append(cleaned)

    full_text = _clean_text("\n\n".join(page_texts))
    sections = _split_into_sections(full_text)
    title_map = _build_title_map(sections)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
    )

    chunks: list[dict] = []
    part_counter: dict[str, int] = {}
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

            if section_id:
                part_counter[section_id] = part_counter.get(section_id, 0) + 1
                part_num = part_counter[section_id]
                if part_num == 1:
                    chunk_id = f"{file_slug}_{_section_to_slug(section_id)}"
                else:
                    chunk_id = f"{file_slug}_{_section_to_slug(section_id)}_{part_num}"
            else:
                part_counter["_misc"] = part_counter.get("_misc", 0) + 1
                chunk_id = f"{file_slug}_misc_{part_counter['_misc']:03d}"

            chunk_id = _unique_chunk_id(chunk_id)

            embed_text = part_text
            if section_title:
                embed_text = f"{section_title}:\n\n{part_text}"

            tagged = tag_chunk_metadata(
                {
                    "chunk_id": chunk_id,
                    "tenant_id": tenant_id,
                    "file_name": source_file,
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
                    "tenant_id": tenant_id,
                    "dataset_type": tagged["dataset_type"],
                    "pet_type": tagged["pet_type"],
                    "service_type": tagged["service_type"],
                    "service_info": tagged["service_info"],
                    "main_header": tagged["main_header"],
                    "sub_header": tagged["sub_header"],
                    "section_path": tagged["section_path"],
                    "document_file_name": source_file,
                    "source_file": source_file,
                    "language": "unknown",
                    "section_id": section_id,
                    "section_title": section_title,
                    "chapter": major_chapter,
                    "category": category,
                    "is_rule": chunk_is_content,
                    "page": 0,
                    "text": embed_text,
                    "text_preview": embed_text[:120].replace("\n", " "),
                }
            )

    return chunks


ORIGINAL_TEXT_PATH = DATA_PROCESSED / "original_text.json"


def _save_original_text(
    by_file: dict[str, str],
    path: Path | None = None,
    *,
    merge: bool = False,
) -> None:
    path = path or ORIGINAL_TEXT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = by_file
    if merge and path.exists():
        existing = _load_original_text(path)
        existing.update(by_file)
        payload = existing
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_original_text(path: Path | None = None) -> dict[str, str]:
    path = path or ORIGINAL_TEXT_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _reconstruct_text_from_chunks(chunks: list[dict]) -> str:
    ordered = sorted(chunks, key=lambda c: c.get("chunk_id", ""))
    return "\n\n".join(c.get("text", "") for c in ordered)


def get_chunking_breakdown() -> list[dict]:
    """Describe structure-aware chunking: original text, detected sections, final chunks."""
    chunks = load_chunks()
    if not chunks:
        return []

    originals = _load_original_text()
    by_file: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk["source_file"], []).append(chunk)

    breakdowns: list[dict] = []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
    )

    for source_file, file_chunks in by_file.items():
        full_text = originals.get(source_file) or _reconstruct_text_from_chunks(file_chunks)
        sections = _split_into_sections(full_text)

        section_groups: list[dict] = []
        chunks_by_section: dict[str, list[dict]] = {}
        unsectioned: list[dict] = []

        for chunk in sorted(file_chunks, key=lambda c: c["chunk_id"]):
            sid = chunk.get("section_id") or ""
            if sid:
                chunks_by_section.setdefault(sid, []).append(chunk)
            else:
                unsectioned.append(chunk)

        if unsectioned:
            section_groups.append(
                {
                    "section_id": "",
                    "section_title": "(No numbered headings — full document treated as one block)",
                    "char_count": len(full_text),
                    "split_method": "recursive",
                    "reason": f"Document has no line-start numbered sections; recursive split applied ({CHUNK_SIZE} chars, {CHUNK_OVERLAP} overlap) because text exceeds {SECTION_SUB_SPLIT} chars",
                    "parts_count": len(unsectioned),
                    "chunks": [
                        {
                            "chunk_id": c["chunk_id"],
                            "tenant_id": c.get("tenant_id", ""),
                            "main_header": c.get("main_header", ""),
                            "sub_header": c.get("sub_header", ""),
                            "section_path": c.get("section_path", ""),
                            "document_file_name": c.get("document_file_name", c.get("source_file", "")),
                            "dataset_type": c.get("dataset_type", c.get("source_file", "")),
                            "pet_type": c.get("pet_type", "all"),
                            "service_type": c.get("service_type", "general"),
                            "service_info": c.get("service_info", ""),
                            "char_count": len(c.get("text", "")),
                            "text": c.get("text", ""),
                            "is_rule": c.get("is_rule", True),
                        }
                        for c in unsectioned
                    ],
                }
            )

        for section in sections:
            if not section["section_id"]:
                continue
            sid = section["section_id"]
            parts = _subsplit_section(section, splitter)
            split_method = "structure-only" if len(parts) == 1 else "recursive"
            reason = (
                f"Section kept intact ({len(section['text'])} chars ≤ {SECTION_SUB_SPLIT})"
                if split_method == "structure-only"
                else f"Section exceeded {SECTION_SUB_SPLIT} chars → split into {len(parts)} parts"
            )
            section_groups.append(
                {
                    "section_id": sid,
                    "section_title": section["section_title"],
                    "char_count": len(section["text"]),
                    "split_method": split_method,
                    "reason": reason,
                    "parts_count": len(parts),
                    "chunks": [
                        {
                            "chunk_id": c["chunk_id"],
                            "tenant_id": c.get("tenant_id", ""),
                            "main_header": c.get("main_header", ""),
                            "sub_header": c.get("sub_header", ""),
                            "section_path": c.get("section_path", ""),
                            "document_file_name": c.get("document_file_name", c.get("source_file", "")),
                            "dataset_type": c.get("dataset_type", c.get("source_file", "")),
                            "pet_type": c.get("pet_type", "all"),
                            "service_type": c.get("service_type", "general"),
                            "service_info": c.get("service_info", ""),
                            "char_count": len(c.get("text", "")),
                            "text": c.get("text", ""),
                            "is_rule": c.get("is_rule", True),
                        }
                        for c in chunks_by_section.get(sid, [])
                    ],
                }
            )

        breakdowns.append(
            {
                "source_file": source_file,
                "original_text": full_text,
                "original_char_count": len(full_text),
                "total_chunks": len(file_chunks),
                "sections_detected": len([s for s in sections if s["section_id"]]),
                "pipeline": [
                    "Step 1: Merge Word document text into one cleaned text block",
                    "Step 2: Detect numbered sections (regex: 1, 1.1, 1.1.1 at line start)",
                    f"Step 3: If a section exceeds {SECTION_SUB_SPLIT} chars → recursive split ({CHUNK_SIZE} chars, {CHUNK_OVERLAP} overlap)",
                ],
                "sections": section_groups,
            }
        )

    return breakdowns


def chunk_from_pages(pages: list[Document], tenant_id: str, *, merge_originals: bool = False) -> list[dict]:
    by_file: dict[str, list] = {}
    for page_doc in pages:
        source_file = page_doc.metadata.get("source_file", "unknown.docx")
        by_file.setdefault(source_file, []).append(page_doc)

    original_by_file: dict[str, str] = {}
    for source_file, file_pages in by_file.items():
        page_texts = [_clean_text(p.page_content) for p in file_pages if _clean_text(p.page_content)]
        original_by_file[source_file] = _clean_text("\n\n".join(page_texts))
    _save_original_text(original_by_file, merge=merge_originals)

    chunks: list[dict] = []
    for source_file in by_file:
        chunks.extend(_build_chunks_for_pdf(by_file[source_file], source_file, tenant_id))
    return chunks


def chunk_documents(
    pages: list[Document],
    tenant_id: str,
    save_path: Path | None = None,
    *,
    merge: bool = True,
) -> list[dict]:
    """Chunk uploaded pages. By default merge into the existing multi-document corpus."""
    save_path = save_path or DATA_PROCESSED / "chunks.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    new_chunks = chunk_from_pages(pages, tenant_id, merge_originals=merge)
    incoming_files = {c["source_file"] for c in new_chunks}

    if merge:
        existing = [c for c in load_chunks(save_path) if c.get("source_file") not in incoming_files]
        for chunk in existing:
            chunk["tenant_id"] = tenant_id
        chunks = existing + new_chunks
    else:
        chunks = new_chunks

    save_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks


def list_documents(path: Path | None = None) -> list[dict]:
    """List ingested documents with chunk counts."""
    chunks = load_chunks(path)
    by_file: dict[str, int] = {}
    for chunk in chunks:
        name = chunk.get("source_file") or "unknown"
        by_file[name] = by_file.get(name, 0) + 1
    return [
        {"source_file": name, "chunk_count": count}
        for name, count in sorted(by_file.items())
    ]


def remove_document(source_file: str, path: Path | None = None) -> list[dict]:
    """Remove one document's chunks (and original text) from the corpus."""
    path = path or DATA_PROCESSED / "chunks.json"
    chunks = [c for c in load_chunks(path) if c.get("source_file") != source_file]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

    originals = _load_original_text()
    if source_file in originals:
        originals.pop(source_file)
        ORIGINAL_TEXT_PATH.write_text(
            json.dumps(originals, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return chunks


def relabel_chunks(path: Path | None = None) -> list[dict]:
    """Fix is_rule flags and required structure metadata on existing chunks.json."""
    path = path or DATA_PROCESSED / "chunks.json"
    chunks = load_chunks(path)

    title_map: dict[str, str] = {}
    for chunk in chunks:
        sid = chunk.get("section_id") or ""
        title = chunk.get("section_title") or ""
        if sid and title and sid not in title_map:
            title_map[sid] = title

    for chunk in chunks:
        section_id = chunk.get("section_id", "")
        section_title = chunk.get("section_title", "")
        source_file = chunk.get("source_file", "")
        chunk["is_rule"] = _is_content_section(section_id, chunk.get("text", ""))
        structure = _structure_metadata(section_id, section_title, title_map)
        chunk["main_header"] = structure["main_header"]
        chunk["sub_header"] = structure["sub_header"]
        chunk["section_path"] = structure["section_path"]
        chunk["document_file_name"] = source_file
        chunk.pop("document_type", None)
        chunk.pop("chunk_type", None)
        chunk.pop("content_type", None)
        chunk.pop("scenario", None)
        chunk.pop("tags", None)
        chunk.setdefault("tenant_id", chunk.get("tenant_id") or "pawfect-demo")

        tagged = tag_chunk_metadata(
            {
                "chunk_id": chunk.get("chunk_id", ""),
                "tenant_id": chunk.get("tenant_id", ""),
                "file_name": source_file,
                "section_title": chunk.get("section_title", ""),
                "main_header": chunk.get("main_header", ""),
                "sub_header": chunk.get("sub_header", ""),
                "section_path": chunk.get("section_path", ""),
                "text": chunk.get("text", ""),
            }
        )
        chunk.update(
            {
                "dataset_type": tagged["dataset_type"],
                "pet_type": tagged["pet_type"],
                "service_type": tagged["service_type"],
                "service_info": tagged["service_info"],
            }
        )
        for legacy in (
            "scenario_type",
            "scenario_tags",
            "service_types",
            "section_tags",
            "file_name",
        ):
            chunk.pop(legacy, None)

    path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks


def auto_tag_chunks(path: Path | None = None, *, overwrite: bool = False) -> tuple[list[dict], dict[str, int]]:
    """Recompute standardized metadata tags for existing chunks."""
    path = path or DATA_PROCESSED / "chunks.json"
    chunks = load_chunks(path)
    updated = 0

    for chunk in chunks:
        tagged = tag_chunk_metadata(
            {
                "chunk_id": chunk.get("chunk_id", ""),
                "tenant_id": chunk.get("tenant_id", ""),
                "file_name": chunk.get("source_file", chunk.get("document_file_name", "")),
                "section_title": chunk.get("section_title", ""),
                "main_header": chunk.get("main_header", ""),
                "sub_header": chunk.get("sub_header", ""),
                "section_path": chunk.get("section_path", ""),
                "text": chunk.get("text", ""),
            }
        )

        if overwrite or not chunk.get("dataset_type"):
            chunk.update(
                {
                    "dataset_type": tagged["dataset_type"],
                    "pet_type": tagged["pet_type"],
                    "service_type": tagged["service_type"],
                    "service_info": tagged["service_info"],
                }
            )
            for legacy in (
                "scenario_type",
                "scenario_tags",
                "service_types",
                "section_tags",
                "file_name",
            ):
                chunk.pop(legacy, None)
            chunk.pop("scenario", None)
            chunk.pop("tags", None)
            updated += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    return chunks, {"total": len(chunks), "updated": updated}


def load_chunks(path: Path | None = None) -> list[dict]:
    path = path or DATA_PROCESSED / "chunks.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
