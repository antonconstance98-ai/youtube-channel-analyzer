#!/usr/bin/env python3
"""
Mentor Content Pipeline — CLI Interface

Processes YouTube channels, local transcripts, and PDFs through a 5-step
AI pipeline to produce organized knowledge base files and SOUL personality profiles.

Usage:
    python main.py                          # Interactive menu
    python main.py --mentor mark            # Process a predefined mentor
    python main.py --mentor mark --steps 0  # Only run ingestion
    python main.py --mentor mark --steps 6  # Only run RAG chunking
    python main.py --cross-mentor           # Run cross-mentor synthesis
    python main.py --deploy                 # Deploy staged files to live
    python main.py --list-mentors           # Show available mentors
    python main.py --status                 # Show pipeline state
"""

import argparse
import sys
from pathlib import Path

from mentor_config import MENTORS, MentorConfig, MentorSource, SourceType, TargetDomain, PipelineState


STAGING_DIR = Path("staging")  # Default, overridden by --output flag


def list_mentors():
    print("\nAvailable mentors:")
    print("-" * 60)
    for key, m in MENTORS.items():
        sources = ", ".join(s.source_type.value for s in m.sources)
        print(f"  {key:12s} {m.name:25s} [{sources}] → {m.default_domain.value}")
    print()


def show_status():
    print("\nPipeline Status:")
    print("-" * 60)

    if not STAGING_DIR.exists():
        print("  No staging directory found. Pipeline has not been run.")
        return

    for mentor_dir in sorted(STAGING_DIR.iterdir()):
        if not mentor_dir.is_dir() or mentor_dir.name in ("deploy", "cross-mentor"):
            continue

        state = PipelineState.load(STAGING_DIR, mentor_dir.name)
        raw_count = len(list((mentor_dir / "raw").glob("*.json"))) if (mentor_dir / "raw").exists() else 0
        clean_count = len(list((mentor_dir / "cleaned").glob("*.json"))) if (mentor_dir / "cleaned").exists() else 0
        knowledge_count = len(list((mentor_dir / "knowledge").glob("*.md"))) if (mentor_dir / "knowledge").exists() else 0
        soul_exists = (mentor_dir / f"soul-profile-{mentor_dir.name}.md").exists()
        rag_manifest = mentor_dir / "rag" / "rag_manifest.json"
        rag_chunks = 0
        if rag_manifest.exists():
            import json as _json
            with open(rag_manifest) as _f:
                rag_chunks = _json.load(_f).get("total_chunks", 0)

        print(f"\n  {mentor_dir.name}:")
        print(f"    Ingested:     {len(state.ingested_ids)} items ({raw_count} raw files)")
        print(f"    Cleaned:      {clean_count} transcripts")
        print(f"    Extracted:    {len(state.extracted_ids)} transcripts processed")
        print(f"    Knowledge:    {knowledge_count} files ({len(state.generated_topics)} topics)")
        print(f"    SOUL profile: {'Yes' if soul_exists else 'No'}")
        print(f"    RAG chunks:   {rag_chunks}")
        print(f"    Routed:       {'Yes' if state.routed else 'No'}")
        print(f"    Last run:     {state.last_run or 'Never'}")

    # Check deploy staging
    deploy_dir = STAGING_DIR / "deploy"
    if deploy_dir.exists():
        file_count = sum(1 for _ in deploy_dir.rglob("*.md"))
        print(f"\n  Deploy staging: {file_count} files ready for review")
    print()


def run_interactive():
    print("=" * 60)
    print("  Mentor Content Pipeline")
    print("  Process YouTube channels → Knowledge files + SOUL profiles")
    print("=" * 60)
    print()

    print("What would you like to do?")
    print("  1. Process a predefined mentor")
    print("  2. Process a custom YouTube channel")
    print("  3. Run cross-mentor synthesis")
    print("  4. Show pipeline status")
    print("  5. Deploy staged files to live")
    print()

    choice = input("Enter choice (1-5): ").strip()

    if choice == "1":
        list_mentors()
        key = input("Enter mentor key: ").strip().lower()
        if key not in MENTORS:
            print(f"Unknown mentor: {key}")
            return
        mentor = MENTORS[key]
        steps_input = input("Steps to run (0-6, comma-separated, or 'all'): ").strip()
        if steps_input.lower() == 'all' or not steps_input:
            steps = None
        else:
            steps = [int(s.strip()) for s in steps_input.split(",")]
        _run_pipeline(mentor, steps)

    elif choice == "2":
        url = input("YouTube channel URL: ").strip()
        if not url:
            print("No URL provided.")
            return
        name = input("Mentor name: ").strip() or "Custom"
        slug = input("Slug (lowercase, hyphens): ").strip() or name.lower().replace(" ", "-")
        mentor = MentorConfig(
            name=name,
            slug=slug,
            default_domain=TargetDomain.DIRECT_RESPONSE_MARKETING,
            sources=[MentorSource(SourceType.YOUTUBE_CHANNEL, url)],
        )
        max_vid = input("Max videos (default 200): ").strip()
        if max_vid:
            mentor.max_videos = int(max_vid)
        _run_pipeline(mentor, None)

    elif choice == "3":
        _run_cross_mentor()

    elif choice == "4":
        show_status()

    elif choice == "5":
        _run_deploy()

    else:
        print("Invalid choice.")


def _run_pipeline(mentor: MentorConfig, steps):
    from pipeline import MentorPipeline

    print(f"\nStarting pipeline for: {mentor.name}")
    print(f"Sources: {len(mentor.sources)}")
    print()

    pipeline = MentorPipeline(mentor, staging_dir=STAGING_DIR)
    pipeline.run(steps=steps)


def _run_cross_mentor():
    from cross_mentor import run_cross_mentor_synthesis, find_processed_mentors
    from llm_processor import LLMProcessor

    processed = find_processed_mentors(STAGING_DIR)
    if len(processed) < 2:
        print(f"Need at least 2 processed mentors. Currently have: {processed}")
        return

    print(f"Processed mentors: {', '.join(processed)}")
    confirm = input("Run cross-mentor synthesis? (y/n): ").strip().lower()
    if confirm != 'y':
        return

    llm = LLMProcessor()
    run_cross_mentor_synthesis(STAGING_DIR, llm, progress=lambda msg, **kw: print(f"  {msg}"))


def _run_deploy():
    from steps.step5_route import deploy_to_live

    live_base = Path.home() / ".openclaw" / "shared" / "knowledge"
    print(f"Deploy target: {live_base}")
    print()

    # Show what would be deployed
    deploy_dir = STAGING_DIR / "deploy"
    if not deploy_dir.exists():
        print("No files staged for deployment.")
        return

    print("Files to deploy:")
    for md_file in sorted(deploy_dir.rglob("*.md")):
        rel = md_file.relative_to(deploy_dir)
        print(f"  {rel}")
    print()

    confirm = input("Deploy these files? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Deployment cancelled.")
        return

    count = deploy_to_live(STAGING_DIR, live_base, progress=lambda msg, **kw: print(f"  {msg}"))
    print(f"\nDeployed {count} files.")


def main():
    parser = argparse.ArgumentParser(description="Mentor Content Pipeline")
    parser.add_argument("--mentor", "-m", help="Predefined mentor key (e.g., mark, hormozi, carl)")
    parser.add_argument("--steps", "-s", help="Comma-separated step numbers (0-6) or 'all' (6=RAG chunks)")
    parser.add_argument("--output", "-o", help="Output/staging directory (default: ./staging)")
    parser.add_argument("--cross-mentor", action="store_true", help="Run cross-mentor synthesis")
    parser.add_argument("--deploy", action="store_true", help="Deploy staged files to live")
    parser.add_argument("--list-mentors", action="store_true", help="List available mentors")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    parser.add_argument("--local-only", action="store_true", help="Only use local transcript sources, skip YouTube")

    args = parser.parse_args()

    global STAGING_DIR
    if args.output:
        STAGING_DIR = Path(args.output)

    if args.list_mentors:
        list_mentors()
        return

    if args.status:
        show_status()
        return

    if args.cross_mentor:
        _run_cross_mentor()
        return

    if args.deploy:
        _run_deploy()
        return

    if args.mentor:
        if args.mentor not in MENTORS:
            print(f"Unknown mentor: {args.mentor}")
            print("Use --list-mentors to see available options.")
            sys.exit(1)

        mentor = MENTORS[args.mentor]

        # If --local-only, filter out YouTube sources
        if args.local_only:
            mentor = MentorConfig(
                name=mentor.name,
                slug=mentor.slug,
                default_domain=mentor.default_domain,
                sources=[s for s in mentor.sources if s.source_type == SourceType.LOCAL_TRANSCRIPTS],
                max_videos=mentor.max_videos,
            )
            if not mentor.sources:
                print(f"No local transcript sources configured for {args.mentor}")
                sys.exit(1)

        steps = None
        if args.steps and args.steps.lower() != 'all':
            steps = [int(s.strip()) for s in args.steps.split(",")]

        _run_pipeline(mentor, steps)
        return

    # No arguments — interactive mode
    run_interactive()


if __name__ == "__main__":
    main()
