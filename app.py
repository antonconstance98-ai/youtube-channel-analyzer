"""
YouTube Channel Analyzer - Flask Web Application
"""

import os
import io
import json
import hmac
import queue
import uuid
import threading
import zipfile
import time
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, send_file, session, redirect, url_for
from channel_analyzer import (
    get_channel_videos,
    get_channel_name,
    get_transcript,
    clean_filename,
    parse_duration,
    parse_view_count,
    _create_http_session,
)
from transcript_processor import run_processing
from datetime import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

if not app.secret_key:
    raise RuntimeError("SECRET_KEY not set in .env file. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")

SITE_PASSWORD = os.environ.get('SITE_PASSWORD')

if not SITE_PASSWORD:
    raise RuntimeError("SITE_PASSWORD not set in .env file.")

# Global state for sessions
progress_queues = {}
skip_signals = {}  # session_id -> threading.Event (set = skip current video)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if hmac.compare_digest(password, SITE_PASSWORD):
            session['authenticated'] = True
            return redirect(url_for('index'))
        else:
            error = 'Incorrect password'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Serve the main page."""
    return render_template('index.html')


@app.route('/api/videos', methods=['POST'])
@login_required
def fetch_videos():
    """Fetch video list from a channel without starting processing."""
    data = request.json
    channel_url = data.get('channel_url', '').strip()
    max_videos = int(data.get('max_videos', 200))

    if not channel_url:
        return jsonify({'error': 'No channel URL provided'}), 400

    if 'youtube.com' not in channel_url and 'youtu.be' not in channel_url:
        return jsonify({'error': 'Invalid YouTube URL'}), 400

    try:
        videos = get_channel_videos(channel_url, max_videos)
        if not videos:
            return jsonify({'error': 'No videos found on this channel'}), 404

        channel_name = get_channel_name(channel_url)

        video_list = []
        for i, v in enumerate(videos, 1):
            video_list.append({
                'index': i,
                'video_id': v['video_id'],
                'title': v['title'],
                'published_text': v['published_text'],
                'duration_text': v['duration_text'],
                'view_count_text': v['view_count_text'],
            })

        return jsonify({
            'channel_name': channel_name,
            'total_videos': len(videos),
            'videos': video_list,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/start', methods=['POST'])
@login_required
def start_export():
    """Start the export process."""
    data = request.json
    channel_url = data.get('channel_url', '').strip()
    max_videos = int(data.get('max_videos', 200))
    process_transcripts = data.get('process_transcripts', False)
    selected_video_ids = data.get('selected_video_ids', None)  # None = all videos

    if not channel_url:
        return jsonify({'error': 'No channel URL provided'}), 400

    if 'youtube.com' not in channel_url and 'youtu.be' not in channel_url:
        return jsonify({'error': 'Invalid YouTube URL'}), 400

    # Create a unique session ID
    session_id = str(uuid.uuid4())
    progress_queues[session_id] = queue.Queue()
    skip_signals[session_id] = threading.Event()

    # Start processing in background thread
    thread = threading.Thread(
        target=process_channel,
        args=(session_id, channel_url, max_videos, process_transcripts, selected_video_ids)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'session_id': session_id})


@app.route('/api/skip/<session_id>', methods=['POST'])
@login_required
def skip_video(session_id):
    """Signal the processor to skip the current video."""
    if session_id in skip_signals:
        skip_signals[session_id].set()
        return jsonify({'ok': True, 'message': 'Skip signal sent'})
    return jsonify({'error': 'Invalid session'}), 404


@app.route('/api/download/<session_id>')
@login_required
def download_zip(session_id):
    """Create and serve a ZIP file of the processed data."""
    output_base = 'output'
    session_dir = os.path.join(output_base, session_id)

    if not os.path.exists(session_dir):
        return "Session data not found", 404

    try:
        subdirs = [d for d in os.listdir(session_dir) if os.path.isdir(os.path.join(session_dir, d))]
        if not subdirs:
            return "No channel data found in session", 404
        channel_name = subdirs[0]
    except Exception as e:
        return f"Error finding channel data: {str(e)}", 500

    channel_dir = os.path.join(session_dir, channel_name)
    processed_dir = os.path.join(channel_dir, 'processed')

    # Determine what to zip: processed folder if exists, else entire channel folder
    target_dir = processed_dir if os.path.exists(processed_dir) else channel_dir

    if not os.path.exists(target_dir):
        return "Channel data not found", 404

    # Create zip in memory
    memory_file = io.BytesIO()

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(target_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(target_dir))
                zf.write(file_path, arcname)

    memory_file.seek(0)
    zip_size = memory_file.getbuffer().nbytes

    filename = f"{channel_name}_knowledge_files.zip"

    response = send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=filename
    )
    response.headers['X-Zip-Size'] = str(zip_size)
    return response


@app.route('/api/progress/<session_id>')
@login_required
def get_progress(session_id):
    """Stream progress updates via Server-Sent Events."""
    def generate():
        q = progress_queues.get(session_id)
        if not q:
            yield f"data: {json.dumps({'error': 'Invalid session'})}\n\n"
            return

        while True:
            try:
                message = q.get(timeout=60)
                yield f"data: {json.dumps(message)}\n\n"

                if message.get('complete') or message.get('error'):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }
    )


def process_channel(session_id, channel_url, max_videos, process_transcripts=False, selected_video_ids=None):
    """Process channel in background thread."""
    q = progress_queues[session_id]
    skip_event = skip_signals.get(session_id)

    try:
        # Send initial status
        q.put({'status': 'Fetching video list...', 'phase': 'init'})

        # Get videos
        videos = get_channel_videos(channel_url, max_videos)

        if not videos:
            q.put({'error': 'No videos found on this channel'})
            return

        channel_name = get_channel_name(channel_url)

        # Filter by selected videos if provided
        if selected_video_ids:
            selected_set = set(selected_video_ids)
            videos = [v for v in videos if v['video_id'] in selected_set]
            if not videos:
                q.put({'error': 'None of the selected videos were found'})
                return

        q.put({
            'status': 'Channel found',
            'phase': 'ready',
            'channel_name': channel_name,
            'total_videos': len(videos)
        })

        # Test access first
        q.put({'status': 'Testing YouTube access...', 'phase': 'testing'})

        test_result = get_transcript(videos[0]['video_id'])
        if test_result is None:
            q.put({
                'warning': 'YouTube may be blocking requests. Some videos may be skipped.',
                'phase': 'warning'
            })

        # Create output directories
        output_dir = os.path.join('output', session_id)
        channel_dir = os.path.join(output_dir, channel_name)
        transcripts_dir = os.path.join(channel_dir, 'transcripts')
        metadata_dir = os.path.join(channel_dir, 'metadata')

        os.makedirs(transcripts_dir, exist_ok=True)
        os.makedirs(metadata_dir, exist_ok=True)

        # Process videos with parallel workers
        transcripts_saved = 0
        skipped_videos = []
        completed_count = 0
        results_lock = threading.Lock()
        total_videos = len(videos)

        def fetch_video_transcript(args):
            nonlocal transcripts_saved, skipped_videos, completed_count
            i, video = args
            video_id = video['video_id']
            title = video['title']

            # Check skip signal before starting
            if skip_event and skip_event.is_set():
                skip_event.clear()  # Reset for next video

            # Each worker gets its own session for thread safety
            local_session = _create_http_session()

            # Get transcript
            transcript = get_transcript(video_id, http_session=local_session)

            # Create file prefix
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

            # Save metadata
            metadata_file = os.path.join(metadata_dir, f"{prefix}_{safe_title}.json")
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            if transcript:
                # Save transcript
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

                with results_lock:
                    transcripts_saved += 1
            else:
                with results_lock:
                    skipped_videos.append({
                        'title': title,
                        'url': f"https://youtube.com/watch?v={video_id}"
                    })

            with results_lock:
                completed_count += 1

            # Send progress update
            q.put({
                'status': f'Processed: {title[:50]}...' if len(title) > 50 else f'Processed: {title}',
                'phase': 'processing',
                'current': completed_count,
                'total': total_videos,
                'transcripts_saved': transcripts_saved,
                'skipped': len(skipped_videos)
            })

        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(fetch_video_transcript, enumerate(videos, 1))

        # Write channel info
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

        # Write skipped videos
        skipped_file = os.path.join(channel_dir, "_SKIPPED_VIDEOS.txt")
        if skipped_videos:
            skipped_content = "The following videos were skipped (no captions available):\n\n"
            for i, vid in enumerate(skipped_videos, 1):
                skipped_content += f"{i}. {vid['title']} - {vid['url']}\n"
        else:
            skipped_content = "All videos had available captions!"

        with open(skipped_file, 'w', encoding='utf-8') as f:
            f.write(skipped_content)

        # Post-processing for Claude Files
        processing_result = None
        if process_transcripts and transcripts_saved > 0:
            q.put({
                'status': 'Starting Claude knowledge file generation...',
                'phase': 'post_processing'
            })
            processed_dir = os.path.join(channel_dir, 'processed')

            # Progress callback that forwards LLM progress to SSE queue
            def llm_progress_callback(message, **kwargs):
                update = {'status': message}
                update.update(kwargs)
                q.put(update)

            # Skip check that reads from the skip event
            def skip_check():
                if skip_event and skip_event.is_set():
                    skip_event.clear()
                    return True
                return False

            processing_result = run_processing(
                transcripts_dir, processed_dir, channel_name,
                progress_callback=llm_progress_callback,
                skip_check=skip_check
            )

        # Send completion
        q.put({
            'complete': True,
            'channel_name': channel_name,
            'total_videos': len(videos),
            'transcripts_saved': transcripts_saved,
            'videos_skipped': len(skipped_videos),
            'processed_files': processing_result if processing_result else None,
            'session_id': session_id,
        })

    except Exception as e:
        q.put({'error': str(e)})
    finally:
        # Clean up after a delay
        time.sleep(30)
        if session_id in progress_queues:
            del progress_queues[session_id]
        if session_id in skip_signals:
            del skip_signals[session_id]


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("  YouTube Channel Analyzer")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50 + "\n")
    app.run(debug=True, port=5000, threaded=True)
