"""
Pipeline Orchestrator — runs all 5 steps for a given mentor.

Usage:
    from pipeline import MentorPipeline
    pipeline = MentorPipeline(mentor_config, staging_dir="/path/to/staging")
    pipeline.run()                    # Run all steps
    pipeline.run(steps=[1, 2, 3])     # Run specific steps
    pipeline.run(steps=[4])           # Just extract SOUL profile
"""

from pathlib import Path
from typing import Callable, List, Optional

from llm_processor import LLMProcessor
from mentor_config import MentorConfig, PipelineState
from ingest import ingest_all
from steps import step1_clean, step2_extract, step3_knowledge, step4_personality, step5_route, step5b_rag


DEFAULT_STAGING = Path("staging")


class MentorPipeline:
    """Orchestrates the 5-step mentor content pipeline."""

    def __init__(
        self,
        mentor: MentorConfig,
        staging_dir: Optional[Path] = None,
        api_key: Optional[str] = None,
        progress: Optional[Callable] = None,
    ):
        self.mentor = mentor
        self.staging_dir = staging_dir or DEFAULT_STAGING
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        self.progress = progress or self._default_progress
        self.state = PipelineState.load(self.staging_dir, mentor.slug)

        # LLM is initialized lazily — not needed for Step 0 (ingest) or Step 5 (routing)
        self._api_key = api_key
        self._llm = None

    @property
    def llm(self) -> LLMProcessor:
        if self._llm is None:
            self._llm = LLMProcessor(api_key=self._api_key)
        return self._llm

    @staticmethod
    def _default_progress(message: str, **kwargs):
        print(f"  {message}")

    def _save_state(self):
        self.state.save(self.staging_dir)

    def run(self, steps: Optional[List[int]] = None):
        """Run the pipeline.

        Args:
            steps: List of step numbers to run (0-6). None = run all.
                   0 = ingest, 1 = clean, 2 = extract, 3 = knowledge,
                   4 = personality, 5 = route, 6 = rag chunks
        """
        if steps is None:
            steps = [0, 1, 2, 3, 4, 5, 6]

        self.progress(f"Pipeline starting for: {self.mentor.name}")
        self.progress(f"Staging directory: {self.staging_dir / self.mentor.slug}")
        self.progress(f"Steps to run: {steps}")
        self.progress("")

        results = {}

        if 0 in steps:
            self.progress("=" * 50)
            self.progress("STEP 0: INGEST")
            self.progress("=" * 50)
            count = ingest_all(
                self.mentor, self.staging_dir, self.state, self.progress
            )
            self._save_state()
            results["ingest"] = count
            self.progress(f"Ingested {count} new items.\n")

        if 1 in steps:
            self.progress("=" * 50)
            self.progress("STEP 1: CLEAN TRANSCRIPTS")
            self.progress("=" * 50)
            count = step1_clean.run(
                self.mentor, self.staging_dir, self.state, self.llm, self.progress
            )
            self._save_state()
            results["clean"] = count
            self.progress(f"Cleaned {count} transcripts.\n")

        if 2 in steps:
            self.progress("=" * 50)
            self.progress("STEP 2: TOPIC EXTRACTION")
            self.progress("=" * 50)
            count = step2_extract.run(
                self.mentor, self.staging_dir, self.state, self.llm, self.progress
            )
            self._save_state()
            results["extract"] = count
            self.progress(f"Extracted {count} deduplicated frameworks.\n")

        if 3 in steps:
            self.progress("=" * 50)
            self.progress("STEP 3: KNOWLEDGE FILE GENERATION")
            self.progress("=" * 50)
            count = step3_knowledge.run(
                self.mentor, self.staging_dir, self.state, self.llm, self.progress
            )
            self._save_state()
            results["knowledge"] = count
            self.progress(f"Generated {count} knowledge files.\n")

        if 4 in steps:
            self.progress("=" * 50)
            self.progress("STEP 4: SOUL PROFILE EXTRACTION")
            self.progress("=" * 50)
            success = step4_personality.run(
                self.mentor, self.staging_dir, self.state, self.llm, self.progress
            )
            self._save_state()
            results["soul"] = success
            self.progress(f"SOUL profile: {'generated' if success else 'failed'}.\n")

        if 5 in steps:
            self.progress("=" * 50)
            self.progress("STEP 5: DOMAIN ROUTING")
            self.progress("=" * 50)
            manifest = step5_route.run(
                self.mentor, self.staging_dir, self.state, self.progress
            )
            self._save_state()
            results["route"] = manifest
            file_count = len(manifest.get("files", []))
            self.progress(f"Routed {file_count} files to deployment staging.\n")

        if 6 in steps:
            self.progress("=" * 50)
            self.progress("STEP 5b: RAG CHUNK OPTIMIZATION")
            self.progress("=" * 50)
            rag_result = step5b_rag.run(
                self.mentor, self.staging_dir, self.state, self.llm, self.progress
            )
            self._save_state()
            results["rag"] = rag_result
            total = rag_result.get("total_chunks", 0) if isinstance(rag_result, dict) else 0
            self.progress(f"Generated {total} RAG chunks.\n")

        # Summary
        self.progress("=" * 50)
        self.progress("PIPELINE COMPLETE")
        self.progress("=" * 50)
        self.progress(f"Mentor: {self.mentor.name}")
        self.progress(f"Staging: {self.staging_dir / self.mentor.slug}")
        self.progress(f"Deploy staging: {self.staging_dir / 'deploy'}")
        self.progress(f"RAG chunks: {self.staging_dir / self.mentor.slug / 'rag'}")
        self.progress("")
        self.progress("Review the files in staging/deploy/ then run 'deploy' to copy to live.")
        self.progress("RAG chunks are in staging/{mentor}/rag/ — upsert chunks.jsonl to your vector DB.")

        return results

    def deploy(self, live_base: Optional[Path] = None):
        """Deploy staged files to live OpenClaw knowledge directories.

        Args:
            live_base: Path to live knowledge dir.
                       Defaults to ~/.openclaw/shared/knowledge/
        """
        if live_base is None:
            live_base = Path.home() / ".openclaw" / "shared" / "knowledge"

        self.progress(f"Deploying to: {live_base}")

        count = step5_route.deploy_to_live(
            self.staging_dir, live_base, self.progress
        )

        self.progress(f"Deployed {count} files.")
        return count
