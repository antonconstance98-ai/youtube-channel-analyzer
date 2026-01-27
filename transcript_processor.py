import os
import re
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable

from llm_processor import ContextGenerator

# Configuration
INPUT_DIR = r"output/carlweische/transcripts"
OUTPUT_BASE = r"processed"


class TranscriptProcessor:
    def __init__(self, input_dir: str, output_base: str, channel_name: str = "",
                 progress_callback: Optional[Callable] = None,
                 skip_check: Optional[Callable] = None):
        self.input_dir = Path(input_dir)
        self.output_base = Path(output_base)
        self.channel_name = channel_name
        self.videos_data = []
        self.knowledge_cards = []  # Stores {'title': ..., 'content': ...} for synthesis
        self.progress_callback = progress_callback
        self.skip_check = skip_check  # Callable that returns True if current video should be skipped

        # Initialize LLM Generator
        try:
            self.context_generator = ContextGenerator()
        except Exception as e:
            print(f"Warning: Could not initialize LLM Generator: {e}")
            self.context_generator = None

    def _report_progress(self, message: str, **kwargs):
        """Send a progress update if callback is available."""
        if self.progress_callback:
            self.progress_callback(message, **kwargs)

    def _should_skip(self) -> bool:
        """Check if the current video should be skipped."""
        if self.skip_check:
            return self.skip_check()
        return False

    def setup_directories(self):
        """Create necessary directories."""
        self.output_base.mkdir(parents=True, exist_ok=True)

    def parse_file(self, file_path: Path) -> Optional[Dict]:
        """Parse a single transcript file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Split header and transcript
            parts = content.split("---TRANSCRIPT---")
            if len(parts) < 2:
                print(f"Skipping {file_path.name}: Invalid format")
                return None

            header_text = parts[0].strip()
            transcript_text = parts[1].strip()

            video_data = {
                "filename": file_path.name,
                "transcript": transcript_text
            }

            # Parse header fields
            for line in header_text.split('\n'):
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    video_data[key.strip().upper()] = value.strip()

            return video_data
        except Exception as e:
            print(f"Error processing {file_path.name}: {e}")
            return None

    def generate_video_context(self, video: Dict) -> Optional[str]:
        """
        Generate a single knowledge card for a video using the LLM.
        Returns the generated markdown content, or None on failure.
        """
        if not self.context_generator or not self.context_generator.client:
            print(f"Skipping LLM generation for {video['filename']} (No Generator/API Key)")
            return None

        title = video.get('TITLE', 'Untitled')
        url = video.get('URL', '')

        card_content = self.context_generator.generate_knowledge_card(
            transcript_text=video['transcript'],
            video_metadata={
                'title': title,
                'published_date': video.get('PUBLISHED', 'Unknown'),
                'duration_text': video.get('DURATION', 'Unknown'),
                'url': url,
            }
        )

        if not card_content or card_content.startswith("Error"):
            print(f"  Warning: Failed to generate knowledge card for {title}")
            return None

        return card_content

    def save_knowledge_card(self, video: Dict, card_content: str):
        """Save a knowledge card as a single .md file and append the full transcript below."""
        title = video.get('TITLE', 'Untitled')
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-').lower()
        if not safe_title:
            safe_title = Path(video['filename']).stem

        # Truncate to keep filenames reasonable
        if len(safe_title) > 60:
            safe_title = safe_title[:60].rstrip('-')

        # Extract the sequence number from the original filename (e.g., "015_...")
        prefix_match = re.match(r'^(\d+)', video['filename'])
        prefix = prefix_match.group(1) if prefix_match else "000"

        filename = f"{prefix}_{safe_title}.md"
        filepath = self.output_base / filename

        # Build the full file: knowledge card + separator + full transcript
        url = video.get('URL', '')
        date = video.get('PUBLISHED', '')
        duration = video.get('DURATION', '')
        views = video.get('VIEWS', '')

        metadata_block = f"**Source**: {url}  \n**Published**: {date}  \n**Duration**: {duration}  \n**Views**: {views}\n"

        full_content = f"{card_content}\n\n---\n\n## Metadata\n{metadata_block}\n\n## Full Transcript\n\n{video['transcript']}\n"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)

    def run_synthesis(self):
        """Run the cross-video synthesis pass and save channel-level files."""
        if not self.context_generator or not self.context_generator.client:
            print("Skipping synthesis: No LLM generator available.")
            return

        if not self.knowledge_cards:
            print("Skipping synthesis: No knowledge cards generated.")
            return

        channel = self.channel_name or "Unknown Channel"
        print(f"\nRunning channel synthesis for {channel} ({len(self.knowledge_cards)} videos)...")
        self._report_progress(
            f"Running channel synthesis ({len(self.knowledge_cards)} videos)...",
            phase='synthesis',
            synthesis_step='starting'
        )

        synthesis = self.context_generator.generate_channel_synthesis(
            channel_name=channel,
            knowledge_cards=self.knowledge_cards
        )

        if not synthesis:
            print("  Warning: Synthesis returned empty results.")
            return

        # Save synthesis files with _ prefix so they sort to the top
        synthesis_files = {
            '_KNOWLEDGE_MAP.md': synthesis.get('knowledge_map', ''),
            '_SPEAKER_PROFILE.md': synthesis.get('speaker_profile', ''),
            '_GLOSSARY.md': synthesis.get('glossary', ''),
        }

        for filename, content in synthesis_files.items():
            if content and not content.startswith("Error"):
                filepath = self.output_base / filename
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  Saved {filename}")
                self._report_progress(
                    f"Generated {filename}",
                    phase='synthesis',
                    synthesis_step=filename
                )

    def generate_index(self):
        """Generate _INDEX.md — lightweight table listing all videos."""
        index_path = self.output_base / "_INDEX.md"

        content = f"# {self.channel_name or 'Channel'} — Video Index\n\n"
        content += "| # | Title | Date | Duration |\n"
        content += "|---|---|---|---|\n"

        for video in self.videos_data:
            title = video.get('TITLE', 'Untitled')
            date = video.get('PUBLISHED', '-')
            duration = video.get('DURATION', '-')

            prefix_match = re.match(r'^(\d+)', video['filename'])
            num = prefix_match.group(1) if prefix_match else "-"

            content += f"| {num} | {title} | {date} | {duration} |\n"

        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def process(self):
        print(f"Scanning {self.input_dir}...")
        self.setup_directories()

        if not self.input_dir.exists():
            print(f"Error: Input directory {self.input_dir} does not exist.")
            return

        files = sorted(self.input_dir.glob("*.txt"))
        total_files = len(files)
        print(f"Found {total_files} transcript files.")

        self._report_progress(
            f"Starting knowledge card generation for {total_files} videos...",
            phase='llm_processing',
            llm_current=0,
            llm_total=total_files
        )

        # Phase 1: Generate per-video knowledge cards
        for i, file_path in enumerate(files, 1):
            # Check if we should skip this video
            if self._should_skip():
                print(f"[{i}/{total_files}] SKIPPED (user request): {file_path.name}")
                self._report_progress(
                    f"Skipped (user request): {file_path.stem[:50]}",
                    phase='llm_processing',
                    llm_current=i,
                    llm_total=total_files
                )
                continue

            video_data = self.parse_file(file_path)
            if not video_data:
                self._report_progress(
                    f"Skipped (invalid format): {file_path.stem[:50]}",
                    phase='llm_processing',
                    llm_current=i,
                    llm_total=total_files
                )
                continue

            self.videos_data.append(video_data)

            title = video_data.get('TITLE', 'Untitled')
            print(f"[{i}/{total_files}] Processing: {title}")

            display_title = title[:50] + "..." if len(title) > 50 else title
            self._report_progress(
                f"Generating knowledge card: {display_title}",
                phase='llm_processing',
                llm_current=i,
                llm_total=total_files,
                current_video_title=title
            )

            card_content = self.generate_video_context(video_data)

            # Check skip again after LLM call (in case user clicked skip during generation)
            if self._should_skip():
                print(f"  Skipped after generation (user request)")
                self._report_progress(
                    f"Skipped: {title[:50]}",
                    phase='llm_processing',
                    llm_current=i,
                    llm_total=total_files
                )
                continue

            if card_content:
                self.save_knowledge_card(video_data, card_content)
                self.knowledge_cards.append({
                    'title': title,
                    'content': card_content,
                })
                completed_title = title[:50] + "..." if len(title) > 50 else title
                self._report_progress(
                    f"Completed: {completed_title}",
                    phase='llm_processing',
                    llm_current=i,
                    llm_total=total_files,
                    cards_generated=len(self.knowledge_cards)
                )

        # Phase 2: Generate channel-level synthesis
        self.run_synthesis()

        # Phase 3: Generate lightweight index
        self.generate_index()

        self._report_progress(
            'Knowledge file generation complete!',
            phase='llm_complete',
            cards_generated=len(self.knowledge_cards)
        )

        print("Processing complete!")
        return {
            "processed_dir": str(self.output_base),
            "knowledge_cards": len(self.knowledge_cards),
            "synthesis_generated": bool(self.knowledge_cards),
        }


def run_processing(input_dir: str, output_base: str, channel_name: str = "",
                   progress_callback: Optional[Callable] = None,
                   skip_check: Optional[Callable] = None):
    processor = TranscriptProcessor(
        input_dir, output_base, channel_name,
        progress_callback=progress_callback,
        skip_check=skip_check
    )
    return processor.process()


if __name__ == "__main__":
    processor = TranscriptProcessor(INPUT_DIR, OUTPUT_BASE, channel_name="carlweische")
    processor.process()
