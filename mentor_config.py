"""
Mentor configuration and definitions.

Defines source types, target domains, domain routing rules,
and per-mentor settings for the processing pipeline.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional


class SourceType(Enum):
    YOUTUBE_CHANNEL = "youtube_channel"
    YOUTUBE_CURATED = "youtube_curated"
    LOCAL_TRANSCRIPTS = "local_transcripts"
    PDF = "pdf"


class TargetDomain(Enum):
    DIRECT_RESPONSE_MARKETING = "direct-response-marketing"
    SOFTWARE_ENGINEERING = "software-engineering"
    NEUROSCIENCE_PROTOCOLS = "neuroscience-protocols"
    CONTENT_CREATION = "workspace-content/knowledge"


# Domain routing: topic keyword → target domain
# Used by Step 2 (topic extraction) to tag frameworks, and Step 5 (routing) to place files
TOPIC_DOMAIN_MAP = {
    # Direct Response Marketing
    "copywriting": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "ads": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "ad creative": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "sales letters": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "vssl": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "headlines": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "hooks": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "consumer psychology": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "persuasion": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "buyer behavior": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "offer construction": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "pricing": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "bonuses": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "value equation": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "funnel architecture": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "quiz funnel": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "landing pages": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "advertorial": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "ecommerce": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "product testing": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "suppliers": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "fulfillment": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "scaling": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "delegation": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "hiring": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "operations": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "brand building": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "positioning": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "market awareness": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "cro": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "conversion optimization": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "direct response": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "dropshipping": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "facebook ads": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "meta ads": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "email marketing": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "sms marketing": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "upsells": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "aov optimization": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "customer acquisition": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "retention": TargetDomain.DIRECT_RESPONSE_MARKETING,
    "split testing": TargetDomain.DIRECT_RESPONSE_MARKETING,

    # Content Creation
    "content creation": TargetDomain.CONTENT_CREATION,
    "filming": TargetDomain.CONTENT_CREATION,
    "video editing": TargetDomain.CONTENT_CREATION,
    "posting strategy": TargetDomain.CONTENT_CREATION,
    "content repurposing": TargetDomain.CONTENT_CREATION,
    "youtube strategy": TargetDomain.CONTENT_CREATION,
    "thumbnails": TargetDomain.CONTENT_CREATION,

    # Software Engineering
    "ai architecture": TargetDomain.SOFTWARE_ENGINEERING,
    "rag": TargetDomain.SOFTWARE_ENGINEERING,
    "llm integration": TargetDomain.SOFTWARE_ENGINEERING,
    "saas": TargetDomain.SOFTWARE_ENGINEERING,
    "deployment": TargetDomain.SOFTWARE_ENGINEERING,
    "coding practices": TargetDomain.SOFTWARE_ENGINEERING,

    # Neuroscience Protocols
    "circadian biology": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "sleep": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "light exposure": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "dopamine": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "motivation": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "reward systems": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "addiction": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "exercise protocols": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "bdnf": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "cognitive enhancement": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "focus": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "attention": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "deep work": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "habit formation": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "neuroplasticity": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "behavior change": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "stress management": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "breathwork": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "hrv": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "supplements": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "nutrition": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "meditation": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "hypnosis": TargetDomain.NEUROSCIENCE_PROTOCOLS,
    "nsdr": TargetDomain.NEUROSCIENCE_PROTOCOLS,
}


def resolve_domain(topic_tags: List[str], default: TargetDomain) -> TargetDomain:
    """Resolve a list of topic tags to the most likely target domain.

    Counts how many tags map to each domain and returns the majority.
    Falls back to the mentor's default domain if no tags match.
    """
    if not topic_tags:
        return default

    domain_counts: dict[TargetDomain, int] = {}
    for tag in topic_tags:
        tag_lower = tag.lower().strip()
        for keyword, domain in TOPIC_DOMAIN_MAP.items():
            if keyword in tag_lower or tag_lower in keyword:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

    if not domain_counts:
        return default

    return max(domain_counts, key=domain_counts.get)


@dataclass
class MentorSource:
    """A single input source for a mentor."""
    source_type: SourceType
    path: str  # URL for YouTube, directory/file path for local/PDF
    video_ids: Optional[List[str]] = None  # For YOUTUBE_CURATED mode


@dataclass
class MentorConfig:
    """Configuration for a single mentor to process."""
    name: str
    slug: str
    default_domain: TargetDomain
    sources: List[MentorSource] = field(default_factory=list)
    max_videos: int = 200


# Predefined mentor configurations
MENTORS = {
    "mark": MentorConfig(
        name="Mark Builds Brands",
        slug="mark-builds-brands",
        default_domain=TargetDomain.DIRECT_RESPONSE_MARKETING,
        sources=[
            MentorSource(SourceType.LOCAL_TRANSCRIPTS, "/home/alucard/projects/The-Crucible/mark-builds-brands-transcripts"),
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@MarkBuildsBrands"),
        ],
    ),
    "hormozi": MentorConfig(
        name="Alex Hormozi",
        slug="alex-hormozi",
        default_domain=TargetDomain.DIRECT_RESPONSE_MARKETING,
        sources=[
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@AlexHormozi"),
        ],
    ),
    "carl": MentorConfig(
        name="Carl Weische",
        slug="carl-weische",
        default_domain=TargetDomain.DIRECT_RESPONSE_MARKETING,
        sources=[
            MentorSource(SourceType.LOCAL_TRANSCRIPTS, "/home/alucard/projects/The-Crucible/carl-weische-transcripts"),
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@carlweische"),
        ],
    ),
    "junyuh": MentorConfig(
        name="Jun Yuh",
        slug="jun-yuh",
        default_domain=TargetDomain.CONTENT_CREATION,
        sources=[
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@JunYuh"),
        ],
    ),
    "huberman": MentorConfig(
        name="Andrew Huberman",
        slug="andrew-huberman",
        default_domain=TargetDomain.NEUROSCIENCE_PROTOCOLS,
        sources=[
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@hubermanlab"),
        ],
    ),
}


@dataclass
class PipelineState:
    """Tracks processing state for idempotency.

    Stored as state.json in each mentor's staging directory.
    Re-running the pipeline skips already-processed items.
    """
    mentor_slug: str
    ingested_ids: List[str] = field(default_factory=list)
    extracted_ids: List[str] = field(default_factory=list)
    generated_topics: List[str] = field(default_factory=list)
    soul_generated: bool = False
    routed: bool = False
    last_run: str = ""

    def mark_ingested(self, source_id: str):
        if source_id not in self.ingested_ids:
            self.ingested_ids.append(source_id)

    def mark_extracted(self, source_id: str):
        if source_id not in self.extracted_ids:
            self.extracted_ids.append(source_id)

    def mark_topic_generated(self, topic_slug: str):
        if topic_slug not in self.generated_topics:
            self.generated_topics.append(topic_slug)

    def save(self, staging_dir: Path):
        self.last_run = datetime.now().isoformat()
        state_file = staging_dir / self.mentor_slug / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, 'w') as f:
            json.dump({
                "mentor_slug": self.mentor_slug,
                "ingested_ids": self.ingested_ids,
                "extracted_ids": self.extracted_ids,
                "generated_topics": self.generated_topics,
                "soul_generated": self.soul_generated,
                "routed": self.routed,
                "last_run": self.last_run,
            }, f, indent=2)

    @classmethod
    def load(cls, staging_dir: Path, mentor_slug: str) -> "PipelineState":
        state_file = staging_dir / mentor_slug / "state.json"
        if state_file.exists():
            with open(state_file) as f:
                data = json.load(f)
            return cls(**data)
        return cls(mentor_slug=mentor_slug)
