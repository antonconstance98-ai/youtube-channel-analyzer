"""
Step 5: Domain Routing & Manifest Generation

Reads knowledge files from staging/{mentor}/knowledge/
Routes each to the correct domain directory in a deployment staging area.
Generates a manifest of all output files.

Output structure:
  staging/deploy/
  ├── {domain}/
  │   └── {topic-slug}.md
  ├── soul-profiles/
  │   └── soul-profile-{mentor}.md
  └── manifest.json

Files are NOT copied to live OpenClaw directories automatically.
The user reviews staging/deploy/ and approves deployment.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from mentor_config import MentorConfig, PipelineState


def run(
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    progress: Optional[Callable] = None,
) -> dict:
    """Route knowledge files to domain directories and generate manifest.

    Returns the manifest dict.
    """
    knowledge_dir = staging_dir / mentor.slug / "knowledge"
    deploy_dir = staging_dir / "deploy"
    soul_dir = deploy_dir / "soul-profiles"

    if not knowledge_dir.exists():
        if progress:
            progress("No knowledge files found. Run Step 3 first.")
        return {}

    if progress:
        progress(f"Step 5: Routing files for {mentor.name}...")

    manifest_entries = []

    # Route knowledge files by domain
    meta_files = sorted(knowledge_dir.glob("*.meta.json"))
    for meta_file in meta_files:
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        topic_slug = meta["slug"]
        domain = meta["domain"]
        md_file = knowledge_dir / f"{topic_slug}.md"

        if not md_file.exists():
            continue

        # Create domain directory and copy file
        domain_dir = deploy_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)

        dest = domain_dir / f"{topic_slug}.md"

        # If file already exists (from another mentor), we need to merge later
        # For now, append mentor slug to avoid overwriting
        if dest.exists():
            dest = domain_dir / f"{topic_slug}--{mentor.slug}.md"

        shutil.copy2(str(md_file), str(dest))

        manifest_entries.append({
            "file": str(dest.relative_to(staging_dir)),
            "type": "knowledge",
            "topic": meta["topic"],
            "domain": domain,
            "mentor": mentor.name,
            "mentor_slug": mentor.slug,
            "framework_count": meta.get("framework_count", 0),
            "source_count": meta.get("source_count", 0),
        })

        if progress:
            progress(f"Routed: {topic_slug}.md → {domain}/")

    # Route SOUL profile
    soul_file = staging_dir / mentor.slug / f"soul-profile-{mentor.slug}.md"
    if soul_file.exists():
        soul_dir.mkdir(parents=True, exist_ok=True)
        dest = soul_dir / f"soul-profile-{mentor.slug}.md"
        shutil.copy2(str(soul_file), str(dest))

        manifest_entries.append({
            "file": str(dest.relative_to(staging_dir)),
            "type": "soul-profile",
            "mentor": mentor.name,
            "mentor_slug": mentor.slug,
        })

        if progress:
            progress(f"Routed: soul-profile-{mentor.slug}.md → soul-profiles/")

    # Update manifest
    manifest_path = deploy_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)

    # Remove old entries for this mentor (idempotent update)
    manifest["files"] = [
        e for e in manifest["files"]
        if e.get("mentor_slug") != mentor.slug
    ]
    manifest["files"].extend(manifest_entries)
    manifest["last_updated"] = datetime.now().isoformat()
    manifest["mentors_processed"] = list(set(
        manifest.get("mentors_processed", []) + [mentor.slug]
    ))

    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    state.routed = True

    if progress:
        progress(f"Step 5 complete: {len(manifest_entries)} files routed. Manifest updated.")

    return manifest


def _load_manifest(path: Path) -> dict:
    """Load existing manifest or create a new one."""
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "pipeline": "mentor-content-pipeline",
        "version": "2.0",
        "files": [],
        "mentors_processed": [],
        "last_updated": "",
    }


def deploy_to_live(
    staging_dir: Path,
    live_base: Path,
    progress: Optional[Callable] = None,
) -> int:
    """Copy files from staging/deploy/ to live OpenClaw knowledge directories.

    This should only be called after user review and approval.

    Args:
        staging_dir: The pipeline staging directory
        live_base: Base path for live knowledge (e.g., ~/.openclaw/shared/knowledge/)

    Returns number of files deployed.
    """
    deploy_dir = staging_dir / "deploy"
    if not deploy_dir.exists():
        if progress:
            progress("No deployment staging found. Run the pipeline first.")
        return 0

    manifest_path = deploy_dir / "manifest.json"
    if not manifest_path.exists():
        if progress:
            progress("No manifest found.")
        return 0

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    count = 0
    for entry in manifest["files"]:
        src = staging_dir / entry["file"]
        if not src.exists():
            continue

        if entry["type"] == "knowledge":
            dest_dir = live_base / entry["domain"]
        elif entry["type"] == "soul-profile":
            dest_dir = live_base / "soul-profiles"
        else:
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(str(src), str(dest))
        count += 1

        if progress:
            progress(f"Deployed: {src.name} → {dest_dir}/")

    if progress:
        progress(f"Deployment complete: {count} files deployed to {live_base}")

    return count
