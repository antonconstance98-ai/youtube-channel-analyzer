# YouTube Channel Analyzer

A Python pipeline that scrapes YouTube channel transcripts and processes them through a multi-step AI pipeline to produce structured knowledge files optimized for LLM context and RAG (Retrieval-Augmented Generation) ingestion.

No YouTube API key required — uses `scrapetube` and `youtube-transcript-api` for all YouTube access.

## What It Does

Takes a YouTube channel URL (or local transcripts, or PDFs) and produces:

- **Knowledge files** — Topic-organized markdown files (500-2000 words each), synthesized from across all videos, with specific frameworks, data points, and source attribution
- **SOUL profiles** — Behavioral directive files that capture a speaker's thinking patterns, communication style, and decision-making heuristics
- **RAG chunks** — Embedding-optimized chunks with structured metadata and JSONL output ready for vector database upsert (Pinecone, Chroma, Qdrant, etc.)

## Pipeline Steps

| Step | Name | What It Does |
|------|------|-------------|
| 0 | **Ingest** | Scrapes transcripts from YouTube channels, curated video lists, local files, or PDFs |
| 1 | **Clean** | LLM pass to fix transcription errors, remove filler words, add paragraph breaks |
| 2 | **Extract** | Identifies every framework, methodology, and concept; tags with topic categories; deduplicates across videos |
| 3 | **Knowledge** | Groups by topic and synthesizes into dense reference files organized by concept, not by video |
| 4 | **SOUL** | Extracts behavioral personality profile as actionable directives from the full transcript corpus |
| 5 | **Route** | Places files into domain-specific directories for deployment |
| 6 | **RAG** | Splits knowledge files into embedding-optimized chunks with metadata and JSONL output |

The pipeline is **idempotent** — re-running skips already-processed items, so you can resume interrupted runs or add new videos incrementally.

## Setup

```bash
# Clone the repo
git clone https://github.com/antonconstance98-ai/youtube-channel-analyzer.git
cd youtube-channel-analyzer

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and add your Anthropic API key
```

### Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/) (for Steps 1-4 and 6)
- No YouTube API key needed

### Dependencies

| Package | Purpose |
|---------|---------|
| `youtube-transcript-api` | Fetches video transcripts without authentication |
| `scrapetube` | Lists channel videos without API key |
| `anthropic` | Claude API for transcript processing |
| `python-dotenv` | Loads environment variables from `.env` |
| `pymupdf` | PDF text extraction (optional, for PDF sources) |
| `flask` | Web UI (optional) |

## Usage

### Interactive Mode

```bash
python main.py
```

Presents a menu to process predefined mentors, custom channels, run cross-mentor synthesis, or deploy.

### CLI Mode

```bash
# Process a predefined mentor (all steps)
python main.py --mentor mark

# Run only specific steps
python main.py --mentor mark --steps 0        # Ingest only
python main.py --mentor mark --steps 1,2,3    # Clean, extract, synthesize
python main.py --mentor mark --steps 6        # RAG chunking only

# Process a custom channel interactively
python main.py                                # Choose option 2

# Cross-mentor synthesis (needs 2+ processed mentors)
python main.py --cross-mentor

# Check pipeline status
python main.py --status

# List available predefined mentors
python main.py --list-mentors

# Deploy staged files to live directory
python main.py --deploy
```

### Web UI

```bash
python app.py
```

Opens a browser-based interface for channel processing with real-time progress. Protected by a site password (set in `.env`).

## Output Structure

### Knowledge Files (Steps 0-5)

```
staging/
├── {mentor-slug}/
│   ├── raw/                    # Step 0: Raw ingested transcripts (JSON)
│   ├── cleaned/                # Step 1: Cleaned transcripts (JSON)
│   ├── extracted/
│   │   ├── per_video/          # Step 2: Per-video framework extractions
│   │   └── deduplicated.json   # Step 2: Master deduplicated framework list
│   ├── knowledge/              # Step 3: Topic-organized knowledge files (.md)
│   ├── soul-profile-*.md       # Step 4: Behavioral personality profile
│   ├── rag/                    # Step 6: RAG-optimized output (see below)
│   └── state.json              # Pipeline state for idempotent re-runs
├── deploy/                     # Step 5: Domain-routed files ready for review
│   ├── {domain}/
│   │   └── {topic}.md
│   ├── soul-profiles/
│   └── manifest.json
└── cross-mentor/               # Cross-mentor synthesis output
    ├── merged/
    └── overlap_analysis.json
```

### RAG Output (Step 6)

```
staging/{mentor-slug}/rag/
├── chunks/
│   ├── {topic}--summary.md     # Parent chunk (LLM-generated embedding summary)
│   ├── {topic}--001.md         # Detail chunk (one concept/section)
│   ├── {topic}--002.md
│   └── ...
├── chunks.jsonl                # All chunks for vector DB upsert
└── rag_manifest.json           # Chunk statistics
```

#### Chunk Markdown Format

Every chunk includes YAML frontmatter with structured metadata:

```yaml
---
id: mark-builds-brands/copywriting/001
title: Hook Framework (Source: Mark Builds Brands)
mentor: Mark Builds Brands
mentor_slug: mark-builds-brands
domain: direct-response-marketing
topics:
  - copywriting
  - hooks
parent_topic: copywriting
parent_id: mark-builds-brands/copywriting/summary
chunk_type: detail
chunk_index: 1
total_chunks: 5
source_count: 3
framework_count: 4
word_count: 342
---

[chunk content here]
```

#### JSONL Format

Each line in `chunks.jsonl` is a self-contained record ready for vector DB upsert:

```json
{
  "id": "mark-builds-brands/copywriting/001",
  "text": "The Hook Framework emphasizes...",
  "metadata": {
    "title": "Hook Framework",
    "mentor": "Mark Builds Brands",
    "domain": "direct-response-marketing",
    "topics": ["copywriting", "hooks"],
    "chunk_type": "detail",
    "parent_id": "mark-builds-brands/copywriting/summary",
    "word_count": 342
  }
}
```

### RAG Optimization Details

The RAG step addresses six key retrieval concerns:

| Concern | Solution |
|---------|----------|
| **Filtered search** | Structured metadata (mentor, domain, topics) enables hybrid vector + metadata queries |
| **Chunk sizing** | Targets 200-500 words per chunk (embedding model sweet spot), splits at paragraph boundaries |
| **Retrieval precision** | One concept per chunk — splits at `##` section boundaries so embeddings aren't diluted |
| **Embedding recall** | LLM-generated parent summary per topic captures all key terminology for broad matching |
| **Vector DB ingestion** | JSONL output with `{id, text, metadata}` — ready for Pinecone, Chroma, Qdrant, Weaviate |
| **Context expansion** | Parent-child hierarchy — retrieve precise child chunk, expand to parent summary for context |

## Adding Mentors

Edit `mentor_config.py` to add predefined mentors:

```python
MENTORS = {
    "your_mentor": MentorConfig(
        name="Mentor Display Name",
        slug="mentor-slug",
        default_domain=TargetDomain.DIRECT_RESPONSE_MARKETING,
        sources=[
            MentorSource(SourceType.YOUTUBE_CHANNEL, "https://www.youtube.com/@ChannelHandle"),
            MentorSource(SourceType.LOCAL_TRANSCRIPTS, "/path/to/transcript/files"),
            MentorSource(SourceType.PDF, "/path/to/pdf/directory"),
        ],
        max_videos=200,
    ),
}
```

### Source Types

| Type | Description |
|------|-------------|
| `YOUTUBE_CHANNEL` | Scrapes all videos from a channel URL |
| `YOUTUBE_CURATED` | Processes a specific list of video IDs |
| `LOCAL_TRANSCRIPTS` | Reads `.txt` or `.md` files from a directory |
| `PDF` | Extracts text from PDF files |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_API_KEY` or `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `SECRET_KEY` | Web UI only | Flask session secret |
| `SITE_PASSWORD` | Web UI only | Password for web interface access |

## License

Private repository.
