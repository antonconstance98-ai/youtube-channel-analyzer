"""
Step 5b (6): RAG Chunk Optimization

Reads knowledge files from staging/{mentor}/knowledge/ and produces
embedding-optimized chunks with structured metadata for vector DB ingestion.

Addresses six RAG optimization concerns:
  1. Structured metadata (YAML frontmatter) for filtered/hybrid search
  2. Controlled chunk sizing (200-500 words, split at paragraph boundaries)
  3. One concept per chunk (split at ## section boundaries)
  4. Embedding-optimized summary per topic (LLM-generated parent chunk)
  5. JSONL output for direct vector DB upsert
  6. Parent-child hierarchy (summary parent, detail children)

Output: staging/{mentor}/rag/
  chunks/
    {topic-slug}--summary.md     Parent summary chunk (LLM-generated)
    {topic-slug}--001.md         Detail chunks (one per concept section)
    {topic-slug}--002.md
    ...
  chunks.jsonl                   All chunks as {id, text, metadata} JSONL
  rag_manifest.json              Chunk stats and index
"""

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from llm_processor import LLMProcessor
from mentor_config import MentorConfig, PipelineState


# Chunk sizing — tuned for embedding model sweet spot (256-512 tokens)
TARGET_WORDS = 350
MAX_WORDS = 500
MIN_WORDS = 100


# ---------------------------------------------------------------------------
# Parsing & chunking helpers
# ---------------------------------------------------------------------------

def _extract_topic_title(content: str) -> str:
    """Extract the # heading from a knowledge file."""
    for line in content.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _split_into_sections(content: str) -> Tuple[str, List[dict]]:
    """Split markdown into overview text and ## sections.

    Returns (overview_text, [{'title': str, 'text': str}, ...])
    """
    lines = content.split("\n")
    overview_lines: List[str] = []
    sections: List[dict] = []
    current_title = ""
    current_lines: List[str] = []
    in_sections = False

    for line in lines:
        if line.startswith("## "):
            if in_sections and (current_lines or current_title):
                sections.append({
                    "title": current_title,
                    "text": "\n".join(current_lines).strip(),
                })
            current_title = line[3:].strip()
            current_lines = []
            in_sections = True
        elif line.startswith("# "):
            # Skip top-level title (captured separately)
            continue
        elif not in_sections:
            overview_lines.append(line)
        else:
            current_lines.append(line)

    # Flush last section
    if in_sections and (current_lines or current_title):
        sections.append({
            "title": current_title,
            "text": "\n".join(current_lines).strip(),
        })

    return "\n".join(overview_lines).strip(), sections


def _split_oversized_text(text: str) -> List[str]:
    """Split text that exceeds MAX_WORDS at paragraph boundaries."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())
        if current_words + para_words > MAX_WORDS and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_words = para_words
        else:
            current.append(para)
            current_words += para_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _merge_small_sections(sections: List[dict]) -> List[dict]:
    """Merge sections under MIN_WORDS with their neighbour."""
    if not sections:
        return []

    merged: List[dict] = []
    buffer: Optional[dict] = None

    for section in sections:
        word_count = len(section["text"].split()) if section["text"] else 0

        if word_count < MIN_WORDS:
            if buffer is None:
                buffer = dict(section)
            else:
                # Append into buffer
                separator = f"\n\n## {section['title']}\n\n" if section["title"] else "\n\n"
                buffer["text"] += separator + section["text"]
                if section["title"]:
                    buffer["title"] += " & " + section["title"]
        else:
            if buffer is not None:
                # Prepend buffer into this section
                section = dict(section)
                section["text"] = buffer["text"] + "\n\n" + section["text"]
                if buffer["title"]:
                    section["title"] = buffer["title"] + " & " + section["title"]
                buffer = None
            merged.append(section)

    # Flush remaining buffer
    if buffer is not None:
        if merged:
            merged[-1] = dict(merged[-1])
            merged[-1]["text"] += "\n\n" + buffer["text"]
        else:
            merged.append(buffer)

    return merged


def _build_detail_chunks(overview: str, sections: List[dict]) -> List[dict]:
    """Produce right-sized detail chunks from parsed sections.

    1. Merge small sections (< MIN_WORDS)
    2. Split oversized sections (> MAX_WORDS) at paragraph boundaries
    3. Fold overview text into the first section or make it standalone
    """
    # Handle overview text
    if overview:
        overview_words = len(overview.split())
        if sections and overview_words < MIN_WORDS:
            sections = [dict(s) for s in sections]
            sections[0]["text"] = overview + "\n\n" + sections[0]["text"]
        else:
            sections = [{"title": "Overview", "text": overview}] + list(sections)

    # Merge undersized sections
    sections = _merge_small_sections(sections)

    # Split oversized sections
    final: List[dict] = []
    for section in sections:
        word_count = len(section["text"].split())
        if word_count > MAX_WORDS:
            parts = _split_oversized_text(section["text"])
            for j, part in enumerate(parts):
                suffix = f" (Part {j + 1})" if len(parts) > 1 else ""
                final.append({"title": section["title"] + suffix, "text": part})
        else:
            final.append(section)

    return final


# ---------------------------------------------------------------------------
# YAML frontmatter builder
# ---------------------------------------------------------------------------

def _build_frontmatter(meta: dict) -> str:
    """Serialize metadata dict as YAML frontmatter block."""
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_scalar(value) -> str:
    """Quote a YAML scalar if it contains special characters."""
    s = str(value)
    if any(c in s for c in [":", "#", "{", "}", "[", "]", ",", '"', "'", "&", "*", "!", "|", ">"]):
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


# ---------------------------------------------------------------------------
# Topic tag enrichment
# ---------------------------------------------------------------------------

def _build_topic_tags_map(staging_dir: Path, mentor_slug: str) -> Dict[str, List[str]]:
    """Build topic_name → [tags] mapping from deduplicated frameworks.

    Step 3 groups frameworks by their first tag — that tag becomes the topic
    name. This function collects ALL tags from every framework in each group,
    giving richer metadata for vector search filtering.
    """
    dedup_file = staging_dir / mentor_slug / "extracted" / "deduplicated.json"
    if not dedup_file.exists():
        return {}

    with open(dedup_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    tag_map: Dict[str, Set[str]] = {}
    for fw in data.get("deduplicated_frameworks", []):
        tags = fw.get("topic_tags", [])
        if not tags:
            continue
        primary = tags[0].lower().strip()
        if primary not in tag_map:
            tag_map[primary] = set()
        tag_map[primary].update(t.lower().strip() for t in tags)

    return {k: sorted(v) for k, v in tag_map.items()}


# ---------------------------------------------------------------------------
# Main step entry point
# ---------------------------------------------------------------------------

def run(
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    llm: LLMProcessor,
    progress: Optional[Callable] = None,
) -> dict:
    """Generate RAG-optimized chunks from knowledge files.

    Returns dict with chunk statistics.
    """
    knowledge_dir = staging_dir / mentor.slug / "knowledge"
    rag_dir = staging_dir / mentor.slug / "rag"
    chunks_dir = rag_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if not knowledge_dir.exists():
        if progress:
            progress("No knowledge files found. Run Step 3 first.")
        return {}

    md_files = sorted(knowledge_dir.glob("*.md"))
    if not md_files:
        if progress:
            progress("No knowledge files to chunk.")
        return {}

    if progress:
        progress(f"Step 5b: Generating RAG chunks for {len(md_files)} knowledge files...")

    # Load enriched topic tags from deduplicated frameworks
    topic_tags_map = _build_topic_tags_map(staging_dir, mentor.slug)

    all_records: List[dict] = []
    total_summaries = 0
    total_detail = 0

    for file_idx, md_file in enumerate(md_files, 1):
        topic_slug = md_file.stem
        content = md_file.read_text(encoding="utf-8")
        topic_title = _extract_topic_title(content) or topic_slug

        # Load per-topic metadata from Step 3
        meta_file = knowledge_dir / f"{topic_slug}.meta.json"
        file_meta: dict = {}
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                file_meta = json.load(f)

        domain = file_meta.get("domain", mentor.default_domain.value)
        topic_name = file_meta.get("topic", topic_slug.replace("-", " "))
        source_count = file_meta.get("source_count", 0)
        framework_count = file_meta.get("framework_count", 0)

        # Resolve topic tags: prefer enriched map, fall back to topic name
        topics = topic_tags_map.get(topic_name.lower().strip(), [topic_name.lower()])

        # Parse content into sections and build right-sized chunks
        overview, sections = _split_into_sections(content)
        detail_chunks = _build_detail_chunks(overview, sections)

        if not detail_chunks:
            if progress:
                progress(f"[{file_idx}/{len(md_files)}] No content to chunk: {topic_slug}")
            continue

        total_in_topic = len(detail_chunks) + 1  # +1 for summary

        # --- Parent: LLM-generated summary chunk ---
        if progress:
            progress(f"[{file_idx}/{len(md_files)}] Summarising: {topic_slug}")

        try:
            summary_text = llm.generate_chunk_summary(content)
        except Exception as e:
            if progress:
                progress(f"  Summary failed ({e}), using fallback")
            summary_text = overview[:300] if overview else detail_chunks[0]["text"][:300]

        summary_id = f"{mentor.slug}/{topic_slug}/summary"
        summary_meta = {
            "id": summary_id,
            "title": topic_title,
            "mentor": mentor.name,
            "mentor_slug": mentor.slug,
            "domain": domain,
            "topics": topics,
            "parent_topic": topic_slug,
            "chunk_type": "summary",
            "chunk_index": 0,
            "total_chunks": total_in_topic,
            "source_count": source_count,
            "framework_count": framework_count,
            "word_count": len(summary_text.split()),
        }

        summary_md = _build_frontmatter(summary_meta) + "\n\n" + summary_text + "\n"
        (chunks_dir / f"{topic_slug}--summary.md").write_text(summary_md, encoding="utf-8")

        all_records.append({
            "id": summary_id,
            "text": summary_text,
            "metadata": {k: v for k, v in summary_meta.items() if k != "id"},
        })
        total_summaries += 1

        # --- Children: detail chunks ---
        for chunk_idx, chunk in enumerate(detail_chunks, 1):
            chunk_id = f"{mentor.slug}/{topic_slug}/{chunk_idx:03d}"
            chunk_text = chunk["text"]

            chunk_meta = {
                "id": chunk_id,
                "title": chunk["title"] or f"{topic_slug} part {chunk_idx}",
                "mentor": mentor.name,
                "mentor_slug": mentor.slug,
                "domain": domain,
                "topics": topics,
                "parent_topic": topic_slug,
                "parent_id": summary_id,
                "chunk_type": "detail",
                "chunk_index": chunk_idx,
                "total_chunks": total_in_topic,
                "source_count": source_count,
                "framework_count": framework_count,
                "word_count": len(chunk_text.split()),
            }

            chunk_md = _build_frontmatter(chunk_meta) + "\n\n" + chunk_text + "\n"
            (chunks_dir / f"{topic_slug}--{chunk_idx:03d}.md").write_text(
                chunk_md, encoding="utf-8"
            )

            all_records.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": {k: v for k, v in chunk_meta.items() if k != "id"},
            })
            total_detail += 1

        if progress:
            progress(
                f"[{file_idx}/{len(md_files)}] {topic_slug} → "
                f"1 summary + {len(detail_chunks)} detail chunks"
            )

    # --- Include SOUL profile as a special chunk if it exists ---
    soul_file = staging_dir / mentor.slug / f"soul-profile-{mentor.slug}.md"
    if soul_file.exists():
        soul_content = soul_file.read_text(encoding="utf-8")
        soul_id = f"{mentor.slug}/soul-profile/summary"

        soul_meta = {
            "id": soul_id,
            "title": f"SOUL Profile: {mentor.name}",
            "mentor": mentor.name,
            "mentor_slug": mentor.slug,
            "domain": "soul-profiles",
            "topics": ["personality", "communication style", "decision making"],
            "parent_topic": "soul-profile",
            "chunk_type": "soul_profile",
            "chunk_index": 0,
            "total_chunks": 1,
            "source_count": 0,
            "framework_count": 0,
            "word_count": len(soul_content.split()),
        }

        soul_md = _build_frontmatter(soul_meta) + "\n\n" + soul_content + "\n"
        (chunks_dir / "soul-profile--summary.md").write_text(soul_md, encoding="utf-8")

        all_records.append({
            "id": soul_id,
            "text": soul_content,
            "metadata": {k: v for k, v in soul_meta.items() if k != "id"},
        })

        if progress:
            progress(f"Included SOUL profile as RAG chunk")

    # --- Write JSONL (one {id, text, metadata} object per line) ---
    jsonl_path = rag_dir / "chunks.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for record in all_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- Write manifest ---
    manifest = {
        "mentor": mentor.name,
        "mentor_slug": mentor.slug,
        "knowledge_files_processed": len(md_files),
        "summary_chunks": total_summaries,
        "detail_chunks": total_detail,
        "total_chunks": len(all_records),
        "jsonl_path": str(jsonl_path),
        "chunk_target_words": TARGET_WORDS,
        "chunk_max_words": MAX_WORDS,
        "chunk_min_words": MIN_WORDS,
    }

    with open(rag_dir / "rag_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if progress:
        progress(
            f"Step 5b complete: {total_summaries} summaries + {total_detail} detail "
            f"chunks + JSONL written to {jsonl_path}"
        )

    return manifest
