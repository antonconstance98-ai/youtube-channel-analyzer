#!/usr/bin/env python3
"""
YouTube Channel Analyzer
Extracts all video transcripts from a YouTube channel for use in Claude Projects.

Usage:
    python main.py
"""

from channel_analyzer import export_channel


def main():
    print("=" * 60)
    print("  YouTube Channel Analyzer")
    print("  Extract transcripts for Claude Projects")
    print("=" * 60)
    print()
    
    # Get channel URL from user
    channel_url = input("Enter YouTube channel URL: ").strip()
    
    if not channel_url:
        print("Error: No URL provided.")
        return
    
    # Validate URL format (basic check)
    if "youtube.com" not in channel_url and "youtu.be" not in channel_url:
        print("Error: Please enter a valid YouTube channel URL.")
        print("Examples:")
        print("  https://www.youtube.com/@channelname")
        print("  https://www.youtube.com/channel/UCXXXXXXX")
        print("  https://www.youtube.com/c/channelname")
        return
    
    # Get max videos (optional)
    max_input = input("Max videos to process (default 200): ").strip()
    
    if max_input:
        try:
            max_videos = int(max_input)
            if max_videos <= 0:
                raise ValueError()
        except ValueError:
            print("Invalid number. Using default of 200.")
            max_videos = 200
    else:
        max_videos = 200
    
    print()
    print("Processing...")
    print()
    
    # Run the export
    result = export_channel(channel_url, max_videos=max_videos)
    
    if "error" in result:
        print(f"\nExport failed: {result['error']}")
        return
    
    # Show summary
    print()
    print("=" * 60)
    print("  EXPORT COMPLETE!")
    print("=" * 60)
    print(f"  Output saved to: {result['output_path']}/")
    print(f"  - {result['transcripts_saved']} transcripts saved")
    print(f"  - {result['videos_skipped']} videos skipped (no captions)")
    print()
    print("You can now upload the entire folder to Claude Projects!")
    print()


if __name__ == "__main__":
    main()
