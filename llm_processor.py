import os
from typing import Dict, Optional, List
from pathlib import Path
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda: None

try:
    import anthropic
except ImportError:
    anthropic = None

import prompts

# Load environment variables
load_dotenv()


class ContextGenerator:
    def __init__(self, api_key: Optional[str] = None):
        if not anthropic:
            raise ImportError("Anthropic library not installed. Please install it via pip.")

        self.api_key = api_key or os.getenv("LLM_API_KEY")
        if not self.api_key:
            print("WARNING: No API Key provided for ContextGenerator. Context generation will fail.")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=self.api_key)

        self.model = "claude-3-haiku-20240307"

    def _call_llm(self, system_prompt: str, user_content: str, max_tokens: int = 4096) -> str:
        """Call the LLM API."""
        if not self.client:
            return "Error: API Key missing."

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content}
                ]
            )
            return response.content[0].text
        except Exception as e:
            return f"Error calling LLM: {str(e)}"

    def generate_knowledge_card(self, transcript_text: str, video_metadata: Dict) -> str:
        """
        Generate a single consolidated knowledge card for one video.
        Returns the generated markdown text (caller decides where to save it).
        """
        if not self.client:
            return ""

        title = video_metadata.get('title', 'Untitled')
        date = video_metadata.get('published_date', 'N/A')
        duration = video_metadata.get('duration_text', 'N/A')
        url = video_metadata.get('url', '')

        # Prepend metadata header so the LLM has context
        user_content = f"VIDEO TITLE: {title}\nDATE: {date}\nDURATION: {duration}\nURL: {url}\n\n---TRANSCRIPT---\n\n{transcript_text}"

        return self._call_llm(prompts.PROMPT_KNOWLEDGE_CARD, user_content)

    def generate_channel_synthesis(self, channel_name: str, knowledge_cards: List[Dict[str, str]]) -> Dict[str, str]:
        """
        Run the cross-video synthesis pass.

        Args:
            channel_name: Name of the channel.
            knowledge_cards: List of dicts with 'title' and 'content' keys
                             (the knowledge card markdown for each video).

        Returns:
            Dict with keys 'knowledge_map', 'speaker_profile', 'glossary'
            containing the generated markdown text for each.
        """
        if not self.client:
            return {}

        # Build the concatenated input from all knowledge cards
        combined = f"CHANNEL: {channel_name}\nTOTAL VIDEOS: {len(knowledge_cards)}\n\n"
        for card in knowledge_cards:
            combined += f"---\nVIDEO: {card['title']}\n{card['content']}\n\n"

        # If combined input is very large, truncate each card to its summary + key claims
        # to stay within context limits. ~200k chars is a safe estimate for input.
        if len(combined) > 200000:
            combined = f"CHANNEL: {channel_name}\nTOTAL VIDEOS: {len(knowledge_cards)}\n\n"
            for card in knowledge_cards:
                # Take first 2000 chars of each card (summary + key claims section)
                truncated = card['content'][:2000]
                combined += f"---\nVIDEO: {card['title']}\n{truncated}\n\n"

        results = {}

        print("  - Generating Knowledge Map...")
        results['knowledge_map'] = self._call_llm(
            prompts.PROMPT_KNOWLEDGE_MAP,
            combined,
            max_tokens=4096
        )

        print("  - Generating Speaker Profile...")
        results['speaker_profile'] = self._call_llm(
            prompts.PROMPT_SPEAKER_PROFILE,
            combined,
            max_tokens=4096
        )

        print("  - Generating Glossary...")
        results['glossary'] = self._call_llm(
            prompts.PROMPT_GLOSSARY,
            combined,
            max_tokens=4096
        )

        return results

    @staticmethod
    def _write_file(path: Path, content: str):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
