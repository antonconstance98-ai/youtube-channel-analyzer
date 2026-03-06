"""
Unified ingestion module — handles YouTube channels, curated video lists,
local transcript files, and PDFs.

All sources are normalized to the same output format:
  staging/{mentor-slug}/raw/{source_id}.json

Each JSON file contains:
  {
    "source_id": "unique identifier (video ID, filename hash, etc.)",
    "title": "human-readable title",
    "source_type": "youtube|local|pdf",
    "source_url": "URL if applicable",
    "raw_transcript": "the raw transcript text",
    "metadata": { ... extra fields ... }
  }
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from mentor_config import MentorConfig, MentorSource, PipelineState, SourceType

# Lazy imports for optional dependencies
_scrapetube = None
_channel_analyzer = None
_fitz = None


def _get_scrapetube():
    global _scrapetube
    if _scrapetube is None:
        import scrapetube
        _scrapetube = scrapetube
    return _scrapetube


def _get_channel_analyzer():
    global _channel_analyzer
    if _channel_analyzer is None:
        import channel_analyzer
        _channel_analyzer = channel_analyzer
    return _channel_analyzer


def _get_fitz():
    global _fitz
    if _fitz is None:
        try:
            import fitz  # pymupdf
            _fitz = fitz
        except ImportError:
            raise ImportError("pymupdf not installed. Run: pip install pymupdf")
    return _fitz


def _file_hash(path: str) -> str:
    """Generate a short hash of a file's content for use as source_id."""
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:12]


def _save_raw(staging_dir: Path, mentor_slug: str, source_id: str, data: dict):
    """Save a raw ingested document to staging."""
    raw_dir = staging_dir / mentor_slug / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    safe_id = re.sub(r'[^\w\-]', '_', source_id)
    out_path = raw_dir / f"{safe_id}.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ingest_youtube_channel(
    source: MentorSource,
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> int:
    """Ingest transcripts from a full YouTube channel.

    Returns number of new transcripts ingested.
    """
    ca = _get_channel_analyzer()

    if progress:
        progress(f"Fetching video list from {source.path}...")

    videos = ca.get_channel_videos(source.path, mentor.max_videos)
    if not videos:
        if progress:
            progress("No videos found on channel.")
        return 0

    if progress:
        progress(f"Found {len(videos)} videos. Fetching transcripts...")

    count = 0
    for i, video in enumerate(videos, 1):
        video_id = video['video_id']

        # Skip already-ingested
        if video_id in state.ingested_ids:
            if progress:
                progress(f"[{i}/{len(videos)}] Already ingested: {video['title'][:50]}")
            continue

        transcript = ca.get_transcript(video_id)
        if not transcript:
            if progress:
                progress(f"[{i}/{len(videos)}] No transcript: {video['title'][:50]}")
            continue

        data = {
            "source_id": video_id,
            "title": video['title'],
            "source_type": "youtube",
            "source_url": f"https://youtube.com/watch?v={video_id}",
            "raw_transcript": transcript,
            "metadata": {
                "published_date": video.get('published_text', ''),
                "duration_seconds": video.get('duration_seconds', 0),
                "duration_text": video.get('duration_text', ''),
                "view_count": video.get('view_count', 0),
                "view_count_text": video.get('view_count_text', ''),
                "description": video.get('description', ''),
            }
        }

        _save_raw(staging_dir, mentor.slug, video_id, data)
        state.mark_ingested(video_id)
        count += 1

        if progress:
            progress(f"[{i}/{len(videos)}] Ingested: {video['title'][:50]}")

    return count


def ingest_youtube_curated(
    source: MentorSource,
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> int:
    """Ingest transcripts from a curated list of YouTube video IDs.

    The source.video_ids field should contain the list of video IDs.
    Returns number of new transcripts ingested.
    """
    ca = _get_channel_analyzer()

    video_ids = source.video_ids or []
    if not video_ids:
        if progress:
            progress("No video IDs provided for curated list.")
        return 0

    if progress:
        progress(f"Fetching {len(video_ids)} curated videos...")

    count = 0
    for i, video_id in enumerate(video_ids, 1):
        if video_id in state.ingested_ids:
            if progress:
                progress(f"[{i}/{len(video_ids)}] Already ingested: {video_id}")
            continue

        transcript = ca.get_transcript(video_id)
        if not transcript:
            if progress:
                progress(f"[{i}/{len(video_ids)}] No transcript: {video_id}")
            continue

        data = {
            "source_id": video_id,
            "title": f"Video {video_id}",  # Minimal title — could enhance with API
            "source_type": "youtube",
            "source_url": f"https://youtube.com/watch?v={video_id}",
            "raw_transcript": transcript,
            "metadata": {}
        }

        _save_raw(staging_dir, mentor.slug, video_id, data)
        state.mark_ingested(video_id)
        count += 1

        if progress:
            progress(f"[{i}/{len(video_ids)}] Ingested: {video_id}")

    return count


def _parse_yaml_frontmatter(content: str) -> tuple:
    """Parse YAML frontmatter from a markdown file.

    Returns (metadata_dict, body_text). If no frontmatter found,
    returns ({}, full_content).
    """
    if not content.startswith("---"):
        return {}, content

    # Find the closing --- (must be on its own line after the opening ---)
    lines = content.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, content

    frontmatter_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1:]).strip()

    # Simple YAML parsing — handles key: value and key: [list] patterns
    metadata = {}
    current_key = None
    current_list = None

    for line in frontmatter_lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check for list continuation (- item)
        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            metadata[current_key] = current_list
            continue

        # Check for key: value
        if ": " in stripped or stripped.endswith(":"):
            if ": " in stripped:
                key, value = stripped.split(": ", 1)
            else:
                key = stripped.rstrip(":")
                value = ""

            key = key.strip().lower()
            value = value.strip().strip("'\"")

            if not value:
                # Next lines might be a list
                current_key = key
                current_list = []
                metadata[key] = ""
            else:
                metadata[key] = value
                current_key = key
                current_list = None

    return metadata, body


def ingest_local_transcripts(
    source: MentorSource,
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> int:
    """Ingest pre-existing transcript files from a local directory.

    Supports .txt and .md files. Handles three formats:
    1. YAML frontmatter markdown (The-Crucible format)
    2. TITLE/URL/---TRANSCRIPT--- format (channel_analyzer format)
    3. Plain text (treated as raw transcript)

    Returns number of new transcripts ingested.
    """
    src_dir = Path(source.path)
    if not src_dir.exists():
        if progress:
            progress(f"Directory not found: {source.path}")
        return 0

    files = sorted(list(src_dir.glob("*.txt")) + list(src_dir.glob("*.md")))
    # Skip non-transcript files like index files or JSON
    files = [f for f in files if not f.name.endswith(".json")]
    if progress:
        progress(f"Found {len(files)} transcript files in {source.path}")

    count = 0
    for i, fpath in enumerate(files, 1):
        source_id = f"local_{_file_hash(str(fpath))}"

        if source_id in state.ingested_ids:
            if progress and i % 20 == 0:
                progress(f"[{i}/{len(files)}] Skipping already-ingested files...")
            continue

        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()

        title = fpath.stem
        transcript_text = content
        metadata = {}
        source_url = ""

        # Format 1: YAML frontmatter (starts with ---)
        if content.startswith("---"):
            fm, body = _parse_yaml_frontmatter(content)
            if fm:
                metadata = fm
                transcript_text = body
                title = fm.get("title", title)
                source_url = fm.get("url", "")

                # Strip the markdown heading if it duplicates the title
                if transcript_text.startswith("# "):
                    first_nl = transcript_text.find("\n")
                    if first_nl > 0:
                        transcript_text = transcript_text[first_nl:].strip()

        # Format 2: channel_analyzer format
        elif "---TRANSCRIPT---" in content:
            parts = content.split("---TRANSCRIPT---", 1)
            header = parts[0].strip()
            transcript_text = parts[1].strip()

            for line in header.split('\n'):
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    key_upper = key.strip().upper()
                    metadata[key.strip().lower()] = value.strip()
                    if key_upper == "TITLE":
                        title = value.strip()
                    elif key_upper == "URL":
                        source_url = value.strip()

        # Format 3: plain text — use as-is

        if not transcript_text.strip():
            if progress:
                progress(f"[{i}/{len(files)}] Empty file: {fpath.name}")
            continue

        data = {
            "source_id": source_id,
            "title": title,
            "source_type": "local",
            "source_url": source_url or str(fpath),
            "raw_transcript": transcript_text,
            "metadata": metadata,
        }

        _save_raw(staging_dir, mentor.slug, source_id, data)
        state.mark_ingested(source_id)
        count += 1

        if progress:
            progress(f"[{i}/{len(files)}] Ingested: {title[:60]}")

    return count


def ingest_pdfs(
    source: MentorSource,
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> int:
    """Ingest PDF files — extract text and convert to transcript format.

    source.path can be a directory (all PDFs inside) or a single PDF file.
    Returns number of new documents ingested.
    """
    fitz = _get_fitz()

    src_path = Path(source.path)
    if src_path.is_dir():
        pdf_files = sorted(src_path.glob("*.pdf"))
    elif src_path.is_file() and src_path.suffix.lower() == '.pdf':
        pdf_files = [src_path]
    else:
        if progress:
            progress(f"Not a valid PDF path: {source.path}")
        return 0

    if progress:
        progress(f"Found {len(pdf_files)} PDF files")

    count = 0
    for i, pdf_path in enumerate(pdf_files, 1):
        source_id = f"pdf_{_file_hash(str(pdf_path))}"

        if source_id in state.ingested_ids:
            continue

        try:
            doc = fitz.open(str(pdf_path))
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()

            full_text = "\n\n".join(text_parts)

            if not full_text.strip():
                if progress:
                    progress(f"[{i}/{len(pdf_files)}] Empty PDF: {pdf_path.name}")
                continue

            data = {
                "source_id": source_id,
                "title": pdf_path.stem.replace('-', ' ').replace('_', ' ').title(),
                "source_type": "pdf",
                "source_url": str(pdf_path),
                "raw_transcript": full_text,
                "metadata": {
                    "filename": pdf_path.name,
                    "pages": len(text_parts),
                }
            }

            _save_raw(staging_dir, mentor.slug, source_id, data)
            state.mark_ingested(source_id)
            count += 1

            if progress:
                progress(f"[{i}/{len(pdf_files)}] Ingested: {pdf_path.name}")

        except Exception as e:
            if progress:
                progress(f"[{i}/{len(pdf_files)}] Error reading {pdf_path.name}: {e}")

    return count


# Source type → ingestion function
_INGEST_DISPATCH = {
    SourceType.YOUTUBE_CHANNEL: ingest_youtube_channel,
    SourceType.YOUTUBE_CURATED: ingest_youtube_curated,
    SourceType.LOCAL_TRANSCRIPTS: ingest_local_transcripts,
    SourceType.PDF: ingest_pdfs,
}


def ingest_all(
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> int:
    """Run ingestion for all sources defined in a mentor config.

    Returns total number of new items ingested.
    """
    total = 0
    for source in mentor.sources:
        handler = _INGEST_DISPATCH.get(source.source_type)
        if handler is None:
            if progress:
                progress(f"Unknown source type: {source.source_type}")
            continue

        if progress:
            progress(f"Ingesting from {source.source_type.value}: {source.path}")

        count = handler(source, mentor, staging_dir, state, progress)
        total += count

        if progress:
            progress(f"Ingested {count} items from {source.source_type.value}")

    return total
