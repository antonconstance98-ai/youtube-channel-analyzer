"""
Cross-Mentor Synthesis — detects overlapping topics across mentors
and merges into unified knowledge files.

Run this AFTER at least 2 mentors have been processed through Steps 1-5.
Works at the knowledge-file level (Step 3 output) rather than raw frameworks,
keeping input size manageable for the LLM.
"""

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

from llm_processor import LLMProcessor
from mentor_config import MENTORS, resolve_domain, TargetDomain


def find_processed_mentors(staging_dir: Path) -> List[str]:
    """Find all mentor slugs that have completed Step 3 (knowledge files)."""
    processed = []
    for mentor_dir in staging_dir.iterdir():
        if not mentor_dir.is_dir():
            continue
        knowledge_dir = mentor_dir / "knowledge"
        if knowledge_dir.exists() and any(knowledge_dir.glob("*.md")):
            processed.append(mentor_dir.name)
    return sorted(processed)


def load_mentor_topics(staging_dir: Path, mentor_slug: str) -> List[dict]:
    """Load knowledge file metadata for a mentor.

    Returns list of {slug, title, domain, first_500_chars} for each topic.
    """
    knowledge_dir = staging_dir / mentor_slug / "knowledge"
    if not knowledge_dir.exists():
        return []

    topics = []
    for md_file in sorted(knowledge_dir.glob("*.md")):
        slug = md_file.stem
        content = md_file.read_text(encoding="utf-8")

        # Extract title from first heading
        title = slug
        for line in content.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # Load metadata if available
        meta_file = knowledge_dir / f"{slug}.meta.json"
        domain = "direct-response-marketing"
        tags = []
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            domain = meta.get("domain", domain)
            tags = meta.get("topic_tags", [])

        topics.append({
            "slug": slug,
            "title": title,
            "domain": domain,
            "tags": tags,
            "preview": content[:500],
        })

    return topics


def load_mentor_frameworks(staging_dir: Path, mentor_slug: str) -> List[dict]:
    """Load deduplicated frameworks for a mentor (used for merge content)."""
    dedup_file = staging_dir / mentor_slug / "extracted" / "deduplicated.json"
    if not dedup_file.exists():
        return []
    with open(dedup_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("deduplicated_frameworks", [])


def run_cross_mentor_synthesis(
    staging_dir: Path,
    llm: LLMProcessor,
    mentor_slugs: Optional[List[str]] = None,
    progress: Optional[Callable] = None,
) -> dict:
    """Analyze overlap across mentors and generate merged knowledge files.

    Works at the knowledge-file level (topics) rather than raw frameworks.
    """
    if progress:
        progress("Cross-Mentor Synthesis")
        progress("=" * 50)

    if mentor_slugs is None:
        mentor_slugs = find_processed_mentors(staging_dir)

    if len(mentor_slugs) < 2:
        if progress:
            progress("Need at least 2 processed mentors for cross-mentor synthesis.")
        return {"error": "insufficient_mentors"}

    if progress:
        progress(f"Analyzing {len(mentor_slugs)} mentors: {', '.join(mentor_slugs)}")

    # Load topic-level data (much smaller than raw frameworks)
    mentor_topics: Dict[str, List[dict]] = {}
    mentor_display_names: Dict[str, str] = {}

    for slug in mentor_slugs:
        name = slug
        for key, config in MENTORS.items():
            if config.slug == slug:
                name = config.name
                break

        mentor_display_names[slug] = name
        topics = load_mentor_topics(staging_dir, slug)
        if topics:
            mentor_topics[name] = topics
            if progress:
                progress(f"  {name}: {len(topics)} knowledge topics")

    if len(mentor_topics) < 2:
        if progress:
            progress("Not enough mentors with knowledge files.")
        return {"error": "insufficient_topics"}

    # Step 1: Identify overlapping topics via LLM
    if progress:
        progress("\nIdentifying overlapping topics...")

    try:
        overlap_analysis = llm.analyze_cross_mentor_overlap(mentor_topics)
    except Exception as e:
        if progress:
            progress(f"Error analyzing overlaps: {e}")
        return {"error": str(e)}

    # Save analysis
    cross_dir = staging_dir / "cross-mentor"
    cross_dir.mkdir(parents=True, exist_ok=True)
    with open(cross_dir / "overlap_analysis.json", "w", encoding="utf-8") as f:
        json.dump(overlap_analysis, f, indent=2, ensure_ascii=False)

    overlapping = overlap_analysis.get("overlapping_topics", [])
    unique = overlap_analysis.get("unique_topics", [])

    if progress:
        progress(f"Found {len(overlapping)} overlapping topics, {len(unique)} unique topics")

    # Step 2: Merge overlapping topics
    merged_dir = cross_dir / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    deploy_dir = staging_dir / "deploy"

    # Load raw frameworks for content merging
    mentor_frameworks: Dict[str, List[dict]] = {}
    for slug in mentor_slugs:
        name = mentor_display_names[slug]
        mentor_frameworks[name] = load_mentor_frameworks(staging_dir, slug)

    merged_count = 0
    for topic in overlapping:
        if not topic.get("should_merge", False):
            continue

        topic_name = topic["topic"]
        topic_slug = topic.get("topic_slug", "")
        mentor_file_map = topic.get("mentor_files", {})

        if progress:
            mentors_str = ", ".join(topic.get("mentors", []))
            progress(f"Merging: {topic_name} ({mentors_str})")

        # Read the actual knowledge file content for each mentor
        source_contents = []
        for mentor_name, file_slugs in mentor_file_map.items():
            mentor_slug = None
            for s, n in mentor_display_names.items():
                if n == mentor_name:
                    mentor_slug = s
                    break
            if not mentor_slug:
                continue

            for fs in (file_slugs if isinstance(file_slugs, list) else [file_slugs]):
                knowledge_file = staging_dir / mentor_slug / "knowledge" / f"{fs}.md"
                if knowledge_file.exists():
                    source_contents.append({
                        "mentor": mentor_name,
                        "slug": fs,
                        "content": knowledge_file.read_text(encoding="utf-8"),
                    })

        if not source_contents:
            # Fall back to framework-level merge
            all_frameworks = []
            attributions = {}
            for mentor_name, fw_names in topic.get("mentor_frameworks", {}).items():
                for fw in mentor_frameworks.get(mentor_name, []):
                    canonical = fw.get("canonical_name", fw.get("name", ""))
                    if canonical in (fw_names if isinstance(fw_names, list) else []):
                        all_frameworks.append(fw)
                        attributions[canonical] = mentor_name

            if not all_frameworks:
                continue

            try:
                content = llm.synthesize_knowledge_file(
                    topic_name=topic_name,
                    frameworks=all_frameworks,
                    mentor_attributions=attributions,
                )
            except Exception as e:
                if progress:
                    progress(f"  Error merging {topic_name}: {e}")
                continue
        else:
            # Merge existing knowledge files
            try:
                content = llm.merge_knowledge_files(
                    topic_name=topic_name,
                    source_files=source_contents,
                )
            except Exception as e:
                if progress:
                    progress(f"  Error merging {topic_name}: {e}")
                continue

        # Save merged file
        out_path = merged_dir / f"{topic_slug}.md"
        out_path.write_text(content, encoding="utf-8")

        # Determine domain from existing files or tags
        domain_str = "direct-response-marketing"
        for sc in source_contents:
            meta_file = None
            for slug in mentor_slugs:
                candidate = staging_dir / slug / "knowledge" / f"{sc['slug']}.meta.json"
                if candidate.exists():
                    meta_file = candidate
                    break
            if meta_file:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                domain_str = meta.get("domain", domain_str)
                break

        domain_dir = deploy_dir / domain_str
        domain_dir.mkdir(parents=True, exist_ok=True)

        # Remove per-mentor versions of this topic
        for existing in domain_dir.glob(f"{topic_slug}*.md"):
            existing.unlink()

        deploy_path = domain_dir / f"{topic_slug}.md"
        deploy_path.write_text(content, encoding="utf-8")

        merged_count += 1
        if progress:
            progress(f"  Merged → {topic_slug}.md ({domain_str})")

    summary = {
        "mentors_analyzed": list(mentor_topics.keys()),
        "overlapping_topics": len(overlapping),
        "unique_topics": len(unique),
        "merged_files": merged_count,
    }

    with open(cross_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if progress:
        progress(f"\nCross-mentor synthesis complete: {merged_count} merged files generated.")

    return summary
