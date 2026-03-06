"""
YouTube Channel Analyzer - Core Module
Extracts transcripts from all videos on a YouTube channel.
"""

import os
import re
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
import threading

import scrapetube
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
)

# Thread-safe rate limiter
_rate_lock = threading.Lock()
_last_request_time = 0
_current_delay = 0.5  # Start with 0.5s delay
_max_delay = 4.0
_min_delay = 0.3


def _create_http_session() -> Session:
    """Create a reusable HTTP session with browser-like headers."""
    session = Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


def _adaptive_rate_limit(success: bool = True):
    """Apply adaptive rate limiting based on success/failure."""
    global _last_request_time, _current_delay

    with _rate_lock:
        if not success:
            # Increase delay on failure (exponential backoff)
            _current_delay = min(_current_delay * 2, _max_delay)
        else:
            # Gradually decrease delay on success
            _current_delay = max(_current_delay * 0.9, _min_delay)

        # Wait for the required delay since last request
        elapsed = time.time() - _last_request_time
        if elapsed < _current_delay:
            time.sleep(_current_delay - elapsed)

        _last_request_time = time.time()


def get_channel_videos(channel_url: str, max_videos: int = 200) -> List[Dict[str, Any]]:
    """
    Fetch list of videos from a YouTube channel.
    
    Args:
        channel_url: YouTube channel URL (supports /channel/, /@username, /c/ formats)
        max_videos: Maximum number of videos to fetch
        
    Returns:
        List of video dictionaries with id, title, and metadata
    """
    videos = []
    video_generator = None

    # scrapetube can handle different URL formats
    # Extract channel identifier from URL
    if "/@" in channel_url:
        # Handle /@username format
        match = re.search(r'/@([^/\?]+)', channel_url)
        if match:
            channel_handle = match.group(1)
            video_generator = scrapetube.get_channel(channel_username=channel_handle)
    elif "/channel/" in channel_url:
        # Handle /channel/UCXXXX format
        match = re.search(r'/channel/([^/\?]+)', channel_url)
        if match:
            channel_id = match.group(1)
            video_generator = scrapetube.get_channel(channel_id=channel_id)
    elif "/c/" in channel_url:
        # Handle /c/customname format
        match = re.search(r'/c/([^/\?]+)', channel_url)
        if match:
            channel_name = match.group(1)
            video_generator = scrapetube.get_channel(channel_url=channel_url)

    # Fallback for unrecognized formats or failed regex matches
    if video_generator is None:
        video_generator = scrapetube.get_channel(channel_url=channel_url)
    
    count = 0
    for video in video_generator:
        if count >= max_videos:
            break
            
        video_data = {
            'video_id': video.get('videoId', ''),
            'title': video.get('title', {}).get('runs', [{}])[0].get('text', 'Untitled'),
            'published_text': video.get('publishedTimeText', {}).get('simpleText', ''),
            'view_count_text': video.get('viewCountText', {}).get('simpleText', '0 views'),
            'duration_text': video.get('lengthText', {}).get('simpleText', '0:00'),
            'description': video.get('descriptionSnippet', {}).get('runs', [{}])[0].get('text', ''),
        }
        
        # Parse duration to seconds
        video_data['duration_seconds'] = parse_duration(video_data['duration_text'])
        
        # Parse view count
        video_data['view_count'] = parse_view_count(video_data['view_count_text'])
        
        videos.append(video_data)
        count += 1
    
    return videos


def parse_duration(duration_text: str) -> int:
    """Convert duration string (e.g., '10:35' or '1:05:30') to seconds."""
    try:
        parts = duration_text.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0


def parse_view_count(view_text: str) -> int:
    """Parse view count text (e.g., '1.2M views') to integer."""
    try:
        # Remove 'views' and other text
        text = view_text.lower().replace('views', '').replace('view', '').strip()
        text = text.replace(',', '')
        
        multiplier = 1
        if 'k' in text:
            multiplier = 1000
            text = text.replace('k', '')
        elif 'm' in text:
            multiplier = 1000000
            text = text.replace('m', '')
        elif 'b' in text:
            multiplier = 1000000000
            text = text.replace('b', '')
        
        return int(float(text) * multiplier)
    except (ValueError, AttributeError):
        return 0


def get_transcript(video_id: str, retry_count: int = 0, http_session: Session = None) -> Optional[str]:
    """
    Fetch transcript for a YouTube video.

    Args:
        video_id: YouTube video ID
        retry_count: Current retry attempt (for exponential backoff)
        http_session: Reusable HTTP session (created if not provided)

    Returns:
        Transcript text as a string, or None if unavailable
    """
    if http_session is None:
        http_session = _create_http_session()

    # Apply adaptive rate limiting
    _adaptive_rate_limit()

    try:
        # youtube-transcript-api v1.2.3+ uses instance method .fetch()
        ytt_api = YouTubeTranscriptApi(http_client=http_session)
        fetched_transcript = ytt_api.fetch(video_id)

        # Signal success to rate limiter
        _adaptive_rate_limit(success=True)

        # Combine transcript segments into readable paragraphs
        full_text = []
        current_paragraph = []

        for snippet in fetched_transcript:
            text = snippet.text.strip()
            if text:
                current_paragraph.append(text)

                # Create paragraph breaks at natural points
                if text.endswith(('.', '!', '?')) and len(current_paragraph) > 3:
                    full_text.append(' '.join(current_paragraph))
                    current_paragraph = []

        # Add any remaining text
        if current_paragraph:
            full_text.append(' '.join(current_paragraph))

        return '\n\n'.join(full_text)

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except RequestBlocked as e:
        _adaptive_rate_limit(success=False)
        # Retry with exponential backoff if blocked
        if retry_count < 2:
            wait_time = (retry_count + 1) * 3  # 3s, 6s
            time.sleep(wait_time)
            return get_transcript(video_id, retry_count + 1, http_session)
        return None
    except Exception as e:
        error_msg = str(e)
        if "RequestBlocked" in error_msg or "IpBlocked" in error_msg:
            _adaptive_rate_limit(success=False)
        else:
            print(f"\n    Warning: Error fetching transcript for {video_id}: {e}")
        return None


def clean_filename(title: str, max_length: int = 50) -> str:
    """
    Create a filesystem-safe filename from a video title.
    
    Args:
        title: Original video title
        max_length: Maximum filename length
        
    Returns:
        Cleaned filename string
    """
    # Remove or replace special characters
    cleaned = re.sub(r'[<>:"/\\|?*]', '', title)
    cleaned = re.sub(r'[^\w\s-]', '', cleaned)
    cleaned = re.sub(r'\s+', '-', cleaned.strip())
    cleaned = cleaned.lower()
    
    # Truncate to max length
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip('-')
    
    return cleaned or 'untitled'


def get_channel_name(channel_url: str) -> str:
    """Extract channel name/identifier from URL for folder naming."""
    if "/@" in channel_url:
        match = re.search(r'/@([^/\?]+)', channel_url)
        if match:
            return match.group(1)
    elif "/channel/" in channel_url:
        match = re.search(r'/channel/([^/\?]+)', channel_url)
        if match:
            return match.group(1)
    elif "/c/" in channel_url:
        match = re.search(r'/c/([^/\?]+)', channel_url)
        if match:
            return match.group(1)
    
    return "youtube_channel"


def export_channel(channel_url: str, output_dir: str = "output", max_videos: int = 200) -> Dict[str, Any]:
    """
    Main function to export a YouTube channel's transcripts.
    
    Args:
        channel_url: YouTube channel URL
        output_dir: Base output directory
        max_videos: Maximum number of videos to process
        
    Returns:
        Summary dictionary with stats about the export
    """
    print(f"\nFetching video list from channel...")
    
    # Get channel videos
    try:
        videos = get_channel_videos(channel_url, max_videos)
    except Exception as e:
        print(f"Error: Could not fetch videos from channel. {e}")
        return {"error": str(e)}
    
    if not videos:
        print("Error: No videos found on this channel.")
        return {"error": "No videos found"}
    
    # Get channel name for folder
    channel_name = get_channel_name(channel_url)
    
    # Use first video's channel info if available
    print(f"Found channel: {channel_name} ({len(videos)} videos)")
    
    # Test if we can fetch transcripts before processing all videos
    print("\nTesting YouTube access...")
    test_transcript = get_transcript(videos[0]['video_id'])
    if test_transcript is None:
        print("\n" + "=" * 60)
        print("  WARNING: YouTube may be blocking transcript requests!")
        print("=" * 60)
        print("  This could be because:")
        print("  1. Your IP has been temporarily blocked by YouTube")
        print("  2. You've made too many requests recently")
        print("")
        print("  Possible solutions:")
        print("  - Wait 15-30 minutes and try again")
        print("  - Try from a different network (e.g., mobile hotspot)")
        print("  - Reduce max_videos to a smaller number")
        print("=" * 60)
        response = input("\nContinue anyway? (y/n): ").strip().lower()
        if response != 'y':
            return {"error": "YouTube blocking detected - user cancelled"}
        print("\nContinuing (blocked videos will be skipped)...")
    else:
        print("YouTube access OK - fetching transcripts...")
    
    # Create output directories
    channel_dir = os.path.join(output_dir, channel_name)
    transcripts_dir = os.path.join(channel_dir, "transcripts")
    metadata_dir = os.path.join(channel_dir, "metadata")
    
    os.makedirs(transcripts_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)
    
    # Track results
    transcripts_saved = 0
    skipped_videos = []

    # Process videos sequentially to respect rate limiting
    total = len(videos)
    http_session = _create_http_session()

    for i, video in enumerate(videos, 1):
        video_id = video['video_id']
        title = video['title']

        # Get transcript
        transcript = get_transcript(video_id, http_session=http_session)

        # Create file prefix with zero-padded number
        prefix = f"{i:03d}"
        safe_title = clean_filename(title)

        # Prepare metadata
        metadata = {
            "title": title,
            "video_id": video_id,
            "url": f"https://youtube.com/watch?v={video_id}",
            "published_date": video['published_text'],
            "duration_seconds": video['duration_seconds'],
            "view_count": video['view_count'],
            "description": video['description'],
            "has_transcript": transcript is not None
        }

        # Save metadata JSON
        metadata_file = os.path.join(metadata_dir, f"{prefix}_{safe_title}.json")
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        if transcript:
            # Save transcript file
            transcript_file = os.path.join(transcripts_dir, f"{prefix}_{safe_title}.txt")

            transcript_content = f"""TITLE: {title}
URL: https://youtube.com/watch?v={video_id}
PUBLISHED: {video['published_text']}
DURATION: {video['duration_text']}
VIEWS: {video['view_count_text']}

---TRANSCRIPT---

{transcript}
"""
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(transcript_content)

            transcripts_saved += 1
        else:
            skipped_videos.append({
                'title': title,
                'url': f"https://youtube.com/watch?v={video_id}"
            })

        progress = int((i / total) * 50)
        bar = '=' * progress + '>' + ' ' * (50 - progress - 1)
        print(f"\r[{bar}] {i}/{total} videos processed", end='', flush=True)

    print()  # New line after progress bar
    
    # Write channel info file
    channel_info_file = os.path.join(channel_dir, "_CHANNEL_INFO.txt")
    channel_info_content = f"""Channel Name: {channel_name}
Channel URL: {channel_url}
Total Videos Found: {len(videos)}
Videos With Transcripts: {transcripts_saved}
Videos Skipped (no captions): {len(skipped_videos)}
Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    with open(channel_info_file, 'w', encoding='utf-8') as f:
        f.write(channel_info_content)
    
    # Write skipped videos file
    skipped_file = os.path.join(channel_dir, "_SKIPPED_VIDEOS.txt")
    if skipped_videos:
        skipped_content = "The following videos were skipped because they have no available captions:\n\n"
        for i, vid in enumerate(skipped_videos, 1):
            skipped_content += f"{i}. {vid['title']} - {vid['url']}\n"
    else:
        skipped_content = "All videos had available captions! No videos were skipped."
    
    with open(skipped_file, 'w', encoding='utf-8') as f:
        f.write(skipped_content)
    
    # Return summary
    return {
        "channel_name": channel_name,
        "output_path": channel_dir,
        "total_videos": len(videos),
        "transcripts_saved": transcripts_saved,
        "videos_skipped": len(skipped_videos)
    }
