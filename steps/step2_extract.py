"""
Step 2: Topic Extraction & Deduplication

Reads cleaned transcripts from staging/{mentor}/cleaned/
Extracts frameworks/concepts from each, tags with topic categories,
then deduplicates across all videos for the mentor.

Output: staging/{mentor}/extracted/
  - per_video/{source_id}.json  — raw extraction per video
  - deduplicated.json           — master deduplicated framework list
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
    """Extract topics from all cleaned transcripts, then deduplicate.

    Returns total number of deduplicated frameworks.
    """
    clean_dir = staging_dir / mentor.slug / "cleaned"
    extract_dir = staging_dir / mentor.slug / "extracted"
    per_video_dir = extract_dir / "per_video"
    per_video_dir.mkdir(parents=True, exist_ok=True)

    if not clean_dir.exists():
        if progress:
            progress("No cleaned transcripts found. Run Step 1 first.")
        return 0

    clean_files = sorted(clean_dir.glob("*.json"))
    if progress:
        progress(f"Step 2: Extracting topics from {len(clean_files)} transcripts...")

    # Phase 1: Per-video extraction
    all_extractions = []
    for i, clean_file in enumerate(clean_files, 1):
        with open(clean_file, 'r', encoding='utf-8') as f:
            doc = json.load(f)

        source_id = doc["source_id"]
        title = doc["title"]

        # Check if already extracted (file on disk is the source of truth,
        # state may not have been saved if a previous run timed out)
        extract_file = per_video_dir / clean_file.name
        if extract_file.exists():
            try:
                with open(extract_file, 'r', encoding='utf-8') as f:
                    extraction = json.load(f)
                all_extractions.append(extraction)
                state.mark_extracted(source_id)
                if progress:
                    progress(f"[{i}/{len(clean_files)}] Already extracted: {title[:50]}")
                continue
            except (json.JSONDecodeError, KeyError):
                pass  # Corrupt file, re-extract

        if progress:
            progress(f"[{i}/{len(clean_files)}] Extracting: {title[:50]}")

        try:
            extraction = llm.extract_topics(
                transcript=doc["transcript"],
                title=title,
                source_id=source_id,
            )
        except Exception as e:
            if progress:
                progress(f"[{i}/{len(clean_files)}] Error extracting {title[:50]}: {e}")
            continue

        # Skip low-value transcripts
        if extraction.get("low_value", False):
            if progress:
                progress(f"[{i}/{len(clean_files)}] Low value (skipped): {title[:50]}")
            state.mark_extracted(source_id)
            continue

        # Save per-video extraction
        with open(extract_file, 'w', encoding='utf-8') as f:
            json.dump(extraction, f, indent=2, ensure_ascii=False)

        all_extractions.append(extraction)
        state.mark_extracted(source_id)

        fw_count = len(extraction.get("frameworks", []))
        if progress:
            progress(f"[{i}/{len(clean_files)}] Extracted {fw_count} frameworks: {title[:50]}")

    if not all_extractions:
        if progress:
            progress("No frameworks extracted from any transcript.")
        return 0

    # Phase 2: Deduplicate across all videos
    total_raw = sum(len(e.get("frameworks", [])) for e in all_extractions)
    if progress:
        progress(f"Deduplicating {total_raw} raw frameworks across {len(all_extractions)} transcripts...")

    try:
        deduped = llm.deduplicate_frameworks(all_extractions)
    except Exception as e:
        if progress:
            progress(f"Error during deduplication: {e}")
        # Save raw extractions even if dedup fails
        deduped = {"deduplicated_frameworks": []}
        for ext in all_extractions:
            for fw in ext.get("frameworks", []):
                deduped["deduplicated_frameworks"].append({
                    "canonical_name": fw["name"],
                    "canonical_slug": fw["slug"],
                    "topic_tags": fw["topic_tags"],
                    "sources": [{
                        "source_id": ext.get("source_id", ""),
                        "source_title": ext.get("source_title", ""),
                        "framework_name_in_source": fw["name"],
                    }],
                    "best_summary": fw["summary"],
                    "all_key_points": fw.get("key_points", []),
                    "all_data_points": fw.get("data_points", []),
                    "best_quotes": fw.get("source_quotes", []),
                })

    # Save deduplicated output
    dedup_file = extract_dir / "deduplicated.json"
    with open(dedup_file, 'w', encoding='utf-8') as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)

    dedup_count = len(deduped.get("deduplicated_frameworks", []))
    if progress:
        progress(f"Step 2 complete: {total_raw} raw → {dedup_count} deduplicated frameworks.")

    return dedup_count
