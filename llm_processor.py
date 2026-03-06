"""
LLM Processor — handles all AI API calls for the pipeline.

Supports configurable models per pipeline step. Defaults to high-quality
models since the spec prioritizes quality over speed/cost.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda: None

try:
    import anthropic
except ImportError:
    anthropic = None

import prompts

load_dotenv()

# Model defaults — quality over speed
MODEL_CLEAN = "claude-haiku-4-5-20251001"     # Cleaning is simple, Haiku is fine
MODEL_EXTRACT = "claude-sonnet-4-6"  # Extraction needs quality
MODEL_SYNTHESIZE = "claude-sonnet-4-6"  # Synthesis needs quality
MODEL_SOUL = "claude-sonnet-4-6"     # Behavioral analysis needs quality
MODEL_LEGACY = "claude-haiku-4-5-20251001"    # Legacy web UI knowledge cards


class LLMProcessor:
    """Unified LLM processor for all pipeline steps."""

    def __init__(self, api_key: Optional[str] = None):
        if not anthropic:
            raise ImportError("anthropic library not installed. Run: pip install anthropic")

        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("No API key. Set LLM_API_KEY or ANTHROPIC_API_KEY env var.")

        self.client = anthropic.Anthropic(api_key=self.api_key, timeout=300.0)

    def _call(self, system_prompt: str, user_content: str,
              model: str = MODEL_EXTRACT, max_tokens: int = 8192,
              retries: int = 3) -> str:
        """Make a single LLM API call with retry on transient errors."""
        for attempt in range(retries):
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}]
                )
                return response.content[0].text
            except Exception as e:
                error_str = str(e).lower()
                is_transient = any(k in error_str for k in [
                    "rate_limit", "overloaded", "timeout", "529", "529",
                    "500", "502", "503", "504", "connection",
                ])
                if is_transient and attempt < retries - 1:
                    wait = (attempt + 1) * 5
                    print(f"    LLM API error (attempt {attempt + 1}/{retries}): {e}")
                    print(f"    Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

    def _call_json(self, system_prompt: str, user_content: str,
                   model: str = MODEL_EXTRACT, max_tokens: int = 8192) -> dict:
        """Make an LLM call and parse JSON response. Retries once on parse failure."""
        raw = self._call(system_prompt, user_content, model, max_tokens)

        # Strip markdown code fences if the model wrapped the JSON
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.index("\n")
            last_fence = cleaned.rfind("```")
            if last_fence > first_newline:
                cleaned = cleaned[first_newline + 1:last_fence].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Retry with explicit instruction
            retry_prompt = (
                f"{system_prompt}\n\n"
                "IMPORTANT: Your previous response was not valid JSON. "
                "Output ONLY valid JSON with no markdown fencing or commentary."
            )
            raw = self._call(retry_prompt, user_content, model, max_tokens)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                first_newline = cleaned.index("\n")
                last_fence = cleaned.rfind("```")
                if last_fence > first_newline:
                    cleaned = cleaned[first_newline + 1:last_fence].strip()
            return json.loads(cleaned)

    # =========================================================================
    # Step 1: Clean transcript
    # =========================================================================

    def clean_transcript(self, raw_text: str) -> str:
        """Clean a raw transcript — fix errors, remove filler, add paragraph breaks."""
        # For very long transcripts, process in chunks to stay within context limits
        max_chunk = 80000  # chars, well within context window
        if len(raw_text) <= max_chunk:
            return self._call(prompts.PROMPT_CLEAN_TRANSCRIPT, raw_text,
                              model=MODEL_CLEAN, max_tokens=16384)

        chunks = []
        for i in range(0, len(raw_text), max_chunk):
            chunk = raw_text[i:i + max_chunk]
            cleaned = self._call(prompts.PROMPT_CLEAN_TRANSCRIPT, chunk,
                                 model=MODEL_CLEAN, max_tokens=16384)
            chunks.append(cleaned)
        return "\n\n".join(chunks)

    # =========================================================================
    # Step 2: Topic extraction
    # =========================================================================

    def extract_topics(self, transcript: str, title: str, source_id: str) -> dict:
        """Extract frameworks and concepts from a single transcript.

        Returns dict with 'frameworks' list, each tagged with topic categories.
        For very long transcripts, processes in chunks and merges results.
        """
        max_chars = 120000  # Stay well within context window
        header = f"TITLE: {title}\nSOURCE_ID: {source_id}\n\n---TRANSCRIPT---\n\n"

        if len(transcript) <= max_chars:
            user_content = header + transcript
            return self._call_json(prompts.PROMPT_TOPIC_EXTRACT, user_content,
                                   model=MODEL_EXTRACT, max_tokens=8192)

        # Process in chunks for very long transcripts (e.g. 3-hour podcasts)
        all_frameworks = []
        for i in range(0, len(transcript), max_chars):
            chunk = transcript[i:i + max_chars]
            chunk_num = (i // max_chars) + 1
            user_content = (
                f"TITLE: {title} (Part {chunk_num})\n"
                f"SOURCE_ID: {source_id}\n\n---TRANSCRIPT---\n\n{chunk}"
            )
            result = self._call_json(prompts.PROMPT_TOPIC_EXTRACT, user_content,
                                     model=MODEL_EXTRACT, max_tokens=8192)
            all_frameworks.extend(result.get("frameworks", []))

        return {
            "source_title": title,
            "source_id": source_id,
            "frameworks": all_frameworks,
            "low_value": len(all_frameworks) == 0,
        }

    def deduplicate_frameworks(self, all_frameworks: List[dict]) -> dict:
        """Deduplicate frameworks across multiple videos from the same mentor.

        Args:
            all_frameworks: List of framework extraction results (one per video)

        Returns:
            Deduplicated master framework list
        """
        # Build combined input
        combined = json.dumps(all_frameworks, indent=1)

        # If too large, summarize each framework
        if len(combined) > 150000:
            summarized = []
            for extraction in all_frameworks:
                for fw in extraction.get("frameworks", []):
                    summarized.append({
                        "name": fw["name"],
                        "slug": fw["slug"],
                        "topic_tags": fw["topic_tags"],
                        "summary": fw["summary"],
                        "source_id": extraction.get("source_id", ""),
                        "source_title": extraction.get("source_title", ""),
                    })
            combined = json.dumps(summarized, indent=1)

        return self._call_json(prompts.PROMPT_DEDUP, combined,
                               model=MODEL_EXTRACT, max_tokens=16384)

    # =========================================================================
    # Step 3: Knowledge file generation
    # =========================================================================

    def synthesize_knowledge_file(self, topic_name: str,
                                  frameworks: List[dict],
                                  mentor_attributions: Dict[str, str]) -> str:
        """Generate a knowledge file for a specific topic.

        Args:
            topic_name: The topic being synthesized
            frameworks: List of framework dicts relevant to this topic
            mentor_attributions: {framework_name: mentor_name} for attribution

        Returns:
            Markdown content for the knowledge file
        """
        user_content = f"TOPIC: {topic_name}\n\n"
        user_content += "FRAMEWORKS TO SYNTHESIZE:\n\n"

        for fw in frameworks:
            mentor = mentor_attributions.get(fw.get("canonical_name", fw.get("name", "")), "Unknown")
            user_content += f"---\nFRAMEWORK: {fw.get('canonical_name', fw.get('name', ''))}\n"
            user_content += f"SOURCE: {mentor}\n"
            user_content += f"SUMMARY: {fw.get('best_summary', fw.get('summary', ''))}\n"
            key_points = fw.get("all_key_points", fw.get("key_points", []))
            if key_points:
                user_content += f"KEY POINTS: {'; '.join(key_points)}\n"
            data_points = fw.get("all_data_points", fw.get("data_points", []))
            if data_points:
                user_content += f"DATA POINTS: {'; '.join(data_points)}\n"
            quotes = fw.get("best_quotes", fw.get("source_quotes", []))
            if quotes:
                user_content += f"QUOTES: {'; '.join(quotes)}\n"
            user_content += "\n"

        return self._call(prompts.PROMPT_KNOWLEDGE_SYNTHESIS, user_content,
                          model=MODEL_SYNTHESIZE, max_tokens=8192)

    # =========================================================================
    # Step 4: SOUL/personality extraction
    # =========================================================================

    def extract_soul_profile(self, mentor_name: str, transcripts: List[dict]) -> str:
        """Extract behavioral directives from a mentor's transcript corpus.

        Args:
            mentor_name: The mentor's name
            transcripts: List of dicts with 'title' and 'text' keys

        Returns:
            SOUL profile markdown
        """
        user_content = f"MENTOR: {mentor_name}\n"
        user_content += f"TOTAL TRANSCRIPTS ANALYZED: {len(transcripts)}\n\n"

        # Include representative samples — prioritize variety over volume
        char_budget = 180000
        chars_used = 0
        for t in transcripts:
            entry = f"---\nTITLE: {t['title']}\n{t['text'][:5000]}\n\n"
            if chars_used + len(entry) > char_budget:
                break
            user_content += entry
            chars_used += len(entry)

        return self._call(prompts.PROMPT_SOUL_EXTRACT, user_content,
                          model=MODEL_SOUL, max_tokens=4096)

    # =========================================================================
    # Step 5b: RAG chunk summary
    # =========================================================================

    def generate_chunk_summary(self, knowledge_file_content: str) -> str:
        """Generate a dense embedding-optimized summary for RAG retrieval.

        Takes the full knowledge file content and returns a 2-3 sentence
        summary designed to maximize semantic search recall.
        """
        # Truncate if very long — the summary only needs the gist
        content = knowledge_file_content[:15000]
        return self._call(prompts.PROMPT_RAG_SUMMARY, content,
                          model=MODEL_CLEAN, max_tokens=256)

    # =========================================================================
    # Cross-mentor analysis
    # =========================================================================

    def analyze_cross_mentor_overlap(self, mentor_topics: Dict[str, list]) -> dict:
        """Identify overlapping topics across mentors.

        Args:
            mentor_topics: {mentor_name: [list of topic dicts with slug, title, tags, preview]}

        Returns:
            Cross-mentor topic map with overlap/unique identification
        """
        user_content = "MENTORS AND THEIR KNOWLEDGE TOPICS:\n\n"
        for mentor, topics in mentor_topics.items():
            user_content += f"## {mentor}\n"
            for t in topics:
                title = t.get("title", t.get("slug", ""))
                slug = t.get("slug", "")
                tags = ", ".join(t.get("tags", []))
                preview = t.get("preview", "")[:200]
                user_content += f"- **{title}** (slug: {slug}) [{tags}]: {preview}\n"
            user_content += "\n"

        return self._call_json(prompts.PROMPT_CROSS_MENTOR_MAP, user_content,
                               model=MODEL_SYNTHESIZE, max_tokens=8192)

    def merge_knowledge_files(self, topic_name: str,
                              source_files: list) -> str:
        """Merge multiple per-mentor knowledge files on the same topic into one.

        Args:
            topic_name: The unified topic name
            source_files: List of dicts with 'mentor', 'slug', 'content' keys

        Returns:
            Merged markdown content
        """
        user_content = f"TOPIC: {topic_name}\n\n"
        user_content += "SOURCE KNOWLEDGE FILES TO MERGE:\n\n"
        for sf in source_files:
            user_content += f"--- SOURCE: {sf['mentor']} (file: {sf['slug']}) ---\n"
            user_content += sf["content"] + "\n\n"

        system_prompt = (
            "You are a knowledge synthesis engine. You are given multiple knowledge files "
            "on the SAME topic written from different mentor perspectives. Merge them into "
            "a single unified knowledge file.\n\n"
            "Rules:\n"
            "- Preserve ALL specific data points, metrics, benchmarks, and frameworks from every source\n"
            "- Use (Source: Mentor Name) attribution after each framework, claim, or data point\n"
            "- Where mentors agree, present the consensus view with both attributions\n"
            "- Where mentors disagree or have different approaches, present both perspectives clearly\n"
            "- Maintain the same format: # Title, sections with ##, bullet points, bold terms\n"
            "- Include Key Metrics & Benchmarks and Common Pitfalls sections at the end\n"
            "- Do NOT add information not present in the source files\n"
            "- Do NOT remove any specific numbers, case studies, or brand examples\n"
            "- Target 500-2000 words for the merged file"
        )

        return self._call(system_prompt, user_content,
                          model=MODEL_SYNTHESIZE, max_tokens=8192)

    # =========================================================================
    # Legacy methods — backward compatibility with web UI
    # =========================================================================

    def generate_knowledge_card(self, transcript_text: str, video_metadata: Dict) -> str:
        """Legacy: Generate a single knowledge card for one video (web UI)."""
        title = video_metadata.get('title', 'Untitled')
        date = video_metadata.get('published_date', 'N/A')
        duration = video_metadata.get('duration_text', 'N/A')
        url = video_metadata.get('url', '')

        user_content = (
            f"VIDEO TITLE: {title}\nDATE: {date}\nDURATION: {duration}\n"
            f"URL: {url}\n\n---TRANSCRIPT---\n\n{transcript_text}"
        )
        return self._call(prompts.PROMPT_KNOWLEDGE_CARD, user_content,
                          model=MODEL_LEGACY, max_tokens=4096)

    def generate_channel_synthesis(self, channel_name: str,
                                   knowledge_cards: List[Dict[str, str]]) -> Dict[str, str]:
        """Legacy: Run the cross-video synthesis pass (web UI)."""
        combined = f"CHANNEL: {channel_name}\nTOTAL VIDEOS: {len(knowledge_cards)}\n\n"
        for card in knowledge_cards:
            combined += f"---\nVIDEO: {card['title']}\n{card['content']}\n\n"

        if len(combined) > 200000:
            combined = f"CHANNEL: {channel_name}\nTOTAL VIDEOS: {len(knowledge_cards)}\n\n"
            for card in knowledge_cards:
                truncated = card['content'][:2000]
                combined += f"---\nVIDEO: {card['title']}\n{truncated}\n\n"

        results = {}
        results['knowledge_map'] = self._call(
            prompts.PROMPT_KNOWLEDGE_MAP, combined, model=MODEL_LEGACY, max_tokens=4096)
        results['speaker_profile'] = self._call(
            prompts.PROMPT_SPEAKER_PROFILE, combined, model=MODEL_LEGACY, max_tokens=4096)
        results['glossary'] = self._call(
            prompts.PROMPT_GLOSSARY, combined, model=MODEL_LEGACY, max_tokens=4096)
        return results


# Legacy alias for backward compatibility with transcript_processor.py
class ContextGenerator(LLMProcessor):
    pass
