# YouTube Channel Analyzer - Project Specification

## Overview
A Python application that takes a YouTube channel URL, extracts all video transcripts, and exports them as organized text files optimized for use in Claude Projects.

## Core Requirements

### Input
- User pastes a YouTube channel URL (any format: /channel/, /@username, /c/)
- Optional: Set maximum video limit (default: 200)

### Processing
1. Extract the channel ID from the URL
2. Fetch list of all public videos on the channel (up to the limit)
3. For each video, attempt to get the transcript
4. Track which videos have transcripts vs which were skipped

### Output
Create a folder structure like this:
```
output/
├── {channel_name}/
│   ├── _CHANNEL_INFO.txt          # Channel stats and metadata
│   ├── _SKIPPED_VIDEOS.txt        # List of videos without transcripts
│   ├── transcripts/
│   │   ├── 001_video-title.txt
│   │   ├── 002_video-title.txt
│   │   └── ...
│   └── metadata/
│       ├── 001_video-title.json
│       ├── 002_video-title.json
│       └── ...
```

## File Formats

### _CHANNEL_INFO.txt
```
Channel Name: {name}
Channel URL: {url}
Total Videos Found: {count}
Videos With Transcripts: {count}
Videos Skipped (no captions): {count}
Export Date: {date}
```

### _SKIPPED_VIDEOS.txt
```
The following videos were skipped because they have no available captions:

1. {video_title} - {video_url}
2. {video_title} - {video_url}
...
```

### Individual Transcript File (001_video-title.txt)
```
TITLE: {video_title}
URL: {video_url}
PUBLISHED: {date}
DURATION: {duration}
VIEWS: {view_count}

---TRANSCRIPT---

{full transcript text, cleaned up with proper line breaks}
```

### Individual Metadata File (001_video-title.json)
```json
{
  "title": "Video Title",
  "video_id": "abc123",
  "url": "https://youtube.com/watch?v=abc123",
  "published_date": "2024-01-15",
  "duration_seconds": 845,
  "view_count": 12500,
  "description": "Video description...",
  "tags": ["tag1", "tag2"],
  "has_transcript": true
}
```

## Technical Implementation

### Required Python Packages
```
youtube-transcript-api    # For getting transcripts without browser automation
scrapetube               # For listing channel videos (no API key needed)
```

### Key Functions Needed

1. **get_channel_videos(channel_url, max_videos=200)**
   - Uses scrapetube to get video list
   - Returns list of video objects with id, title, etc.

2. **get_transcript(video_id)**
   - Uses youtube-transcript-api
   - Returns transcript text or None if unavailable
   - Handle exceptions gracefully

3. **clean_filename(title)**
   - Remove special characters
   - Truncate to reasonable length
   - Make filesystem-safe

4. **export_channel(channel_url, output_dir, max_videos)**
   - Main function that orchestrates everything
   - Creates folder structure
   - Loops through videos with progress indicator
   - Writes all output files

### Simple CLI Interface
```
python main.py

> Enter YouTube channel URL: https://youtube.com/@channelname
> Max videos to process (default 200): 
> Processing...
> [=====>          ] 45/150 videos processed
> 
> Done! Output saved to: output/ChannelName/
> - 142 transcripts saved
> - 8 videos skipped (no captions)
```

## Error Handling
- If channel URL is invalid: Show clear error message
- If video has no transcript: Add to skipped list, continue processing
- If rate limited: Add small delay between requests (0.5 seconds)
- If network error: Retry once, then skip and log

## Important Notes for Building This

1. **No YouTube API key required** - both scrapetube and youtube-transcript-api work without authentication

2. **Keep it simple** - This is an MVP. No GUI, no database, just files.

3. **Progress feedback** - Show the user what's happening since processing 200 videos takes a few minutes

4. **File naming** - Prefix with numbers (001_, 002_) to maintain chronological order

5. **Transcript cleaning** - The youtube-transcript-api returns transcript as segments. Combine them into readable paragraphs.

## Example Usage Flow

1. User runs `python main.py`
2. User pastes: `https://www.youtube.com/@AIExplained-official`
3. App shows: "Found channel: AI Explained (187 videos)"
4. App shows progress as it processes each video
5. App creates output folder with all files
6. User can now upload the entire folder to Claude Projects
