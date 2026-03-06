"""
Step 4: Personality / SOUL Profile Extraction

Reads cleaned transcripts from staging/{mentor}/cleaned/
Analyzes communication patterns, decision-making style, core beliefs
across the entire corpus for one mentor.

Output: staging/{mentor}/soul-profile-{mentor-slug}.md
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
) -> bool:
    """Extract SOUL profile for a mentor.

    Returns True if profile was generated successfully.
    """
    clean_dir = staging_dir / mentor.slug / "cleaned"
    out_path = staging_dir / mentor.slug / f"soul-profile-{mentor.slug}.md"

    if state.soul_generated and out_path.exists():
        if progress:
            progress(f"SOUL profile already generated for {mentor.name}.")
        return True

    if not clean_dir.exists():
        if progress:
            progress("No cleaned transcripts found. Run Step 1 first.")
        return False

    clean_files = sorted(clean_dir.glob("*.json"))
    if not clean_files:
        if progress:
            progress("No cleaned transcripts available.")
        return False

    if progress:
        progress(f"Step 4: Extracting SOUL profile for {mentor.name} from {len(clean_files)} transcripts...")

    # Load all transcripts for analysis
    transcripts = []
    for fpath in clean_files:
        with open(fpath, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        transcripts.append({
            "title": doc["title"],
            "text": doc["transcript"],
        })

    try:
        profile = llm.extract_soul_profile(mentor.name, transcripts)
    except Exception as e:
        if progress:
            progress(f"Error extracting SOUL profile: {e}")
        return False

    if not profile or profile.startswith("Error"):
        if progress:
            progress(f"SOUL extraction failed for {mentor.name}.")
        return False

    # Save the profile
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(profile)

    state.soul_generated = True

    if progress:
        progress(f"Step 4 complete: soul-profile-{mentor.slug}.md generated.")

    return True
