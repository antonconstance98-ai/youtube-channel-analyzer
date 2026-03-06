"""
Step 3: Knowledge File Generation

Reads deduplicated frameworks from staging/{mentor}/extracted/deduplicated.json
Groups them by topic, synthesizes into knowledge files.

Output: staging/{mentor}/knowledge/
  - {topic-slug}.md files, 500-2000 words each, organized by topic not person
"""

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from llm_processor import LLMProcessor
from mentor_config import MentorConfig, PipelineState, resolve_domain


def _group_by_topic(frameworks: List[dict]) -> Dict[str, List[dict]]:
    """Group frameworks by their primary topic tag.

    Frameworks with multiple topic tags are placed under the first tag.
    Similar tags are normalized to reduce fragmentation.
    """
    topic_groups: Dict[str, List[dict]] = {}

    for fw in frameworks:
        tags = fw.get("topic_tags", [])
        if not tags:
            tags = ["general"]

        # Use first tag as primary grouping
        primary_tag = tags[0].lower().strip()
        if primary_tag not in topic_groups:
            topic_groups[primary_tag] = []
        topic_groups[primary_tag].append(fw)

    return topic_groups


def _merge_small_groups(groups: Dict[str, List[dict]], min_size: int = 1) -> Dict[str, List[dict]]:
    """Merge very small topic groups into related larger groups.

    This prevents generating a separate file for a topic that has only
    one minor framework — better to fold it into a related topic.
    """
    # For now, keep all groups — the synthesis prompt handles sparse topics well
    return groups


def run(
    mentor: MentorConfig,
    staging_dir: Path,
    state: PipelineState,
    llm: LLMProcessor,
    progress: Optional[Callable] = None,
) -> int:
    """Generate knowledge files for all extracted topics.

    Returns number of knowledge files generated.
    """
    extract_dir = staging_dir / mentor.slug / "extracted"
    knowledge_dir = staging_dir / mentor.slug / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    dedup_file = extract_dir / "deduplicated.json"
    if not dedup_file.exists():
        if progress:
            progress("No deduplicated frameworks found. Run Step 2 first.")
        return 0

    with open(dedup_file, 'r', encoding='utf-8') as f:
        dedup_data = json.load(f)

    frameworks = dedup_data.get("deduplicated_frameworks", [])
    if not frameworks:
        if progress:
            progress("No frameworks to synthesize.")
        return 0

    # Group by topic
    topic_groups = _group_by_topic(frameworks)
    topic_groups = _merge_small_groups(topic_groups)

    if progress:
        progress(f"Step 3: Generating knowledge files for {len(topic_groups)} topics...")

    # Build attribution map: framework_name → mentor_name
    mentor_attributions = {}
    for fw in frameworks:
        name = fw.get("canonical_name", fw.get("name", ""))
        # Attribution comes from the mentor being processed
        mentor_attributions[name] = mentor.name

    count = 0
    for topic_name, topic_frameworks in topic_groups.items():
        # Generate slug for filename
        topic_slug = re.sub(r'[^\w\s-]', '', topic_name).strip().replace(' ', '-').lower()
        if not topic_slug:
            topic_slug = f"topic-{count}"

        # Check if already generated
        if topic_slug in state.generated_topics:
            if progress:
                progress(f"Already generated: {topic_slug}.md")
            count += 1
            continue

        if progress:
            fw_count = len(topic_frameworks)
            progress(f"Synthesizing: {topic_name} ({fw_count} frameworks)")

        try:
            content = llm.synthesize_knowledge_file(
                topic_name=topic_name,
                frameworks=topic_frameworks,
                mentor_attributions=mentor_attributions,
            )
        except Exception as e:
            if progress:
                progress(f"Error synthesizing {topic_name}: {e}")
            continue

        # Save knowledge file
        out_path = knowledge_dir / f"{topic_slug}.md"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Track the domain for this topic
        tags = []
        for fw in topic_frameworks:
            tags.extend(fw.get("topic_tags", []))
        domain = resolve_domain(tags, mentor.default_domain)

        # Save topic metadata for routing
        meta_path = knowledge_dir / f"{topic_slug}.meta.json"
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump({
                "topic": topic_name,
                "slug": topic_slug,
                "domain": domain.value,
                "framework_count": len(topic_frameworks),
                "mentor": mentor.name,
                "mentor_slug": mentor.slug,
                "source_count": sum(
                    len(fw.get("sources", []))
                    for fw in topic_frameworks
                ),
            }, f, indent=2)

        state.mark_topic_generated(topic_slug)
        count += 1

        if progress:
            progress(f"Generated: {topic_slug}.md ({domain.value})")

    if progress:
        progress(f"Step 3 complete: {count} knowledge files generated.")

    return count
