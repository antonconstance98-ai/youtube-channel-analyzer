"""
Step 1: Clean & Normalize Transcripts

Reads raw ingested documents from staging/{mentor}/raw/
and produces cleaned versions in staging/{mentor}/cleaned/

For YouTube transcripts: removes filler, fixes punctuation, adds paragraph breaks.
For PDFs: already clean text, just normalize formatting.
For local files: light cleanup pass.
"""

import json
from pathlib import Path
from typing import Callable, Optional

from llm_processor import LLMProcessor
from mentor_config import MentorConfig, PipelineState


def run(
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    llm: LLMProcessor,
    progress: Optional[Callable] = None,
) -> int:
    """Clean all raw transcripts for a mentor.

    Returns number of transcripts cleaned.
    """
    raw_dir = staging_dir / mentor.slug / "raw"
    clean_dir = staging_dir / mentor.slug / "cleaned"
    clean_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        if progress:
            progress("No raw transcripts found. Run ingestion first.")
        return 0

    raw_files = sorted(raw_dir.glob("*.json"))
    if progress:
        progress(f"Step 1: Cleaning {len(raw_files)} transcripts...")

    count = 0
    for i, raw_file in enumerate(raw_files, 1):
        with open(raw_file, 'r', encoding='utf-8') as f:
            doc = json.load(f)

        source_id = doc["source_id"]
        title = doc["title"]

        # Check if already cleaned
        clean_file = clean_dir / raw_file.name
        if clean_file.exists():
            if progress:
                progress(f"[{i}/{len(raw_files)}] Already clean: {title[:50]}")
            count += 1
            continue

        raw_text = doc["raw_transcript"]

        # Skip LLM cleaning for very short transcripts (not worth the API call)
        if len(raw_text) < 200:
            cleaned_text = raw_text
        else:
            if progress:
                progress(f"[{i}/{len(raw_files)}] Cleaning: {title[:50]}")

            try:
                cleaned_text = llm.clean_transcript(raw_text)
            except Exception as e:
                if progress:
                    progress(f"[{i}/{len(raw_files)}] Error cleaning {title[:50]}: {e}")
                cleaned_text = raw_text  # Fall back to raw on error

        # Save cleaned version (same structure, just cleaned transcript)
        cleaned_doc = {
            "source_id": source_id,
            "title": title,
            "source_type": doc["source_type"],
            "source_url": doc.get("source_url", ""),
            "transcript": cleaned_text,
            "metadata": doc.get("metadata", {}),
        }

        with open(clean_file, 'w', encoding='utf-8') as f:
            json.dump(cleaned_doc, f, indent=2, ensure_ascii=False)

        count += 1

    if progress:
        progress(f"Step 1 complete: {count} transcripts cleaned.")

    return count
