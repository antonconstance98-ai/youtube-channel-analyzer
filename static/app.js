/**
 * YouTube Channel Analyzer - Frontend JavaScript
 */

// Auth check: redirect to login on 401 responses
function checkAuth(response) {
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('Authentication required');
    }
    return response;
}

// State
let currentSessionId = null;
let eventSource = null;
let fetchedVideos = [];     // Full video list from /api/videos
let channelUrlForExport = '';
let channelNameForExport = '';

// DOM Elements
const channelUrlInput = document.getElementById('channelUrl');
const maxVideosInput = document.getElementById('maxVideos');
const startBtn = document.getElementById('startBtn');
const progressSection = document.getElementById('progressSection');
const completionBanner = document.getElementById('completionBanner');
const warningBanner = document.getElementById('warningBanner');
const videoSelectionSection = document.getElementById('videoSelectionSection');

// Progress elements
const channelInitial = document.getElementById('channelInitial');
const channelName = document.getElementById('channelName');
const videoCount = document.getElementById('videoCount');
const savedCount = document.getElementById('savedCount');
const skippedCount = document.getElementById('skippedCount');
const progressText = document.getElementById('progressText');
const progressPercent = document.getElementById('progressPercent');
const progressBar = document.getElementById('progressBar');
const statusText = document.getElementById('statusText');
const logContent = document.getElementById('logContent');

// LLM Progress elements
const llmProgressCard = document.getElementById('llmProgressCard');
const llmProgressText = document.getElementById('llmProgressText');
const llmProgressPercent = document.getElementById('llmProgressPercent');
const llmProgressBar = document.getElementById('llmProgressBar');
const llmStatusText = document.getElementById('llmStatusText');
const skipBtn = document.getElementById('skipBtn');

// Completion elements
const completedChannel = document.getElementById('completedChannel');
const finalTotal = document.getElementById('finalTotal');
const finalSaved = document.getElementById('finalSaved');
const finalSkipped = document.getElementById('finalSkipped');
const finalCards = document.getElementById('finalCards');
const finalCardsContainer = document.getElementById('finalCardsContainer');
const downloadBtn = document.getElementById('downloadBtn');

// Warning elements
const warningText = document.getElementById('warningText');

// Video Selection elements
const selectionChannelName = document.getElementById('selectionChannelName');
const selectionVideoCount = document.getElementById('selectionVideoCount');
const videoList = document.getElementById('videoList');
const selectedCounter = document.getElementById('selectedCounter');
const startProcessingBtn = document.getElementById('startProcessingBtn');

/**
 * Handle the initial "Fetch Videos" button click.
 * Fetches the video list and shows the selection UI.
 */
async function handleStart() {
    const channelUrl = channelUrlInput.value.trim();
    const maxVideos = parseInt(maxVideosInput.value) || 200;

    if (!channelUrl) {
        alert('Please enter a YouTube channel URL');
        channelUrlInput.focus();
        return;
    }

    // Disable button and show loading
    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="spinner"></span><span>Fetching videos...</span>';

    try {
        const response = await fetch('/api/videos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_url: channelUrl,
                max_videos: maxVideos
            })
        }).then(checkAuth);

        const data = await response.json();

        if (data.error) {
            alert(`Error: ${data.error}`);
            resetFetchButton();
            return;
        }

        // Store state
        fetchedVideos = data.videos;
        channelUrlForExport = channelUrl;
        channelNameForExport = data.channel_name;

        // Populate video selection UI
        showVideoSelection(data.channel_name, data.videos);

    } catch (error) {
        alert(`Connection error: ${error.message}`);
        resetFetchButton();
    }
}

/**
 * Show the video selection section.
 */
function showVideoSelection(name, videos) {
    selectionChannelName.textContent = name;
    selectionVideoCount.textContent = `${videos.length} videos found`;

    // Build video list HTML
    videoList.innerHTML = '';
    videos.forEach((v) => {
        const item = document.createElement('label');
        item.className = 'video-item';
        item.innerHTML = `
            <input type="checkbox" class="video-checkbox" value="${v.video_id}" checked>
            <span class="video-item-checkmark"></span>
            <span class="video-item-info">
                <span class="video-item-title">${escapeHtml(v.title)}</span>
                <span class="video-item-meta">${v.duration_text} &middot; ${v.published_text} &middot; ${v.view_count_text}</span>
            </span>
        `;
        videoList.appendChild(item);
    });

    updateSelectedCounter();
    videoSelectionSection.classList.remove('hidden');
    resetFetchButton();

    // Add change listeners
    videoList.querySelectorAll('.video-checkbox').forEach(cb => {
        cb.addEventListener('change', updateSelectedCounter);
    });

    // Scroll to selection
    videoSelectionSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function updateSelectedCounter() {
    const checked = videoList.querySelectorAll('.video-checkbox:checked');
    selectedCounter.textContent = `${checked.length} selected`;
    startProcessingBtn.disabled = checked.length === 0;
}

function selectAllVideos() {
    videoList.querySelectorAll('.video-checkbox').forEach(cb => cb.checked = true);
    updateSelectedCounter();
}

function deselectAllVideos() {
    videoList.querySelectorAll('.video-checkbox').forEach(cb => cb.checked = false);
    updateSelectedCounter();
}

/**
 * Start the actual export with selected videos.
 */
async function startProcessing() {
    const processTranscripts = document.getElementById('processTranscripts').checked;
    const selectedIds = Array.from(videoList.querySelectorAll('.video-checkbox:checked'))
        .map(cb => cb.value);

    if (selectedIds.length === 0) {
        alert('Please select at least one video.');
        return;
    }

    // Disable button
    startProcessingBtn.disabled = true;
    startProcessingBtn.innerHTML = '<span class="spinner"></span><span>Starting...</span>';

    try {
        const response = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_url: channelUrlForExport,
                max_videos: selectedIds.length,
                process_transcripts: processTranscripts,
                selected_video_ids: selectedIds
            })
        }).then(checkAuth);

        const data = await response.json();

        if (data.error) {
            addLog(`Error: ${data.error}`, 'error');
            resetProcessingButton();
            return;
        }

        currentSessionId = data.session_id;

        // Hide selection, show progress
        videoSelectionSection.classList.add('hidden');
        progressSection.classList.remove('hidden');
        completionBanner.classList.add('hidden');
        warningBanner.classList.add('hidden');

        // Reset progress display
        resetProgress();

        // Show LLM card if knowledge files are enabled
        if (processTranscripts) {
            llmProgressCard.classList.remove('hidden');
        }

        // Start listening for updates
        connectToProgress(currentSessionId);

        addLog('Export started...', 'success');

    } catch (error) {
        addLog(`Connection error: ${error.message}`, 'error');
        resetProcessingButton();
    }
}

/**
 * Skip the current video during LLM processing.
 */
async function skipCurrentVideo() {
    if (!currentSessionId) return;

    skipBtn.disabled = true;
    skipBtn.textContent = 'Skipping...';

    try {
        await fetch(`/api/skip/${currentSessionId}`, { method: 'POST' }).then(checkAuth);
        addLog('Skip signal sent - will skip current video', 'warning');
    } catch (e) {
        addLog('Failed to send skip signal', 'error');
    }

    setTimeout(() => {
        skipBtn.disabled = false;
        skipBtn.textContent = 'Skip Current Video';
    }, 3000);
}

/**
 * Connect to Server-Sent Events for progress updates
 */
function connectToProgress(sessionId) {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource(`/api/progress/${sessionId}`);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleProgressUpdate(data);
    };

    eventSource.onerror = (error) => {
        console.error('SSE Error:', error);
        eventSource.close();
        addLog('Connection lost. Please try again.', 'error');
        resetFetchButton();
    };
}

/**
 * Handle progress updates from server
 */
function handleProgressUpdate(data) {
    // Handle heartbeat
    if (data.heartbeat) return;

    // Handle errors
    if (data.error) {
        addLog(`Error: ${data.error}`, 'error');
        resetFetchButton();
        if (eventSource) eventSource.close();
        return;
    }

    // Handle warning
    if (data.warning) {
        warningBanner.classList.remove('hidden');
        warningText.textContent = data.warning;
        addLog(data.warning, 'warning');
    }

    // Handle channel found
    if (data.channel_name && data.phase === 'ready') {
        channelName.textContent = data.channel_name;
        channelInitial.textContent = data.channel_name.charAt(0).toUpperCase();
    }

    if (data.total_videos && data.phase === 'ready') {
        videoCount.textContent = `${data.total_videos} videos`;
    }

    // Handle Phase 1: Transcript download progress
    if (data.phase === 'processing') {
        if (data.current && data.total) {
            const percent = Math.round((data.current / data.total) * 100);
            progressText.textContent = `${data.current}/${data.total} transcripts downloaded`;
            progressPercent.textContent = `${percent}%`;
            progressBar.style.width = `${percent}%`;
        }
        if (data.transcripts_saved !== undefined) {
            savedCount.textContent = data.transcripts_saved;
        }
        if (data.skipped !== undefined) {
            skippedCount.textContent = data.skipped;
        }
        statusText.textContent = data.status || 'Processing...';
    }

    // Handle phase transitions
    if (data.phase === 'init' || data.phase === 'testing') {
        statusText.textContent = data.status || 'Initializing...';
        addLog(data.status);
    }

    if (data.phase === 'post_processing') {
        // Transcript phase done, LLM phase starting
        progressText.textContent = 'Transcripts complete';
        progressPercent.textContent = '100%';
        progressBar.style.width = '100%';
        statusText.textContent = 'Transcript download complete';

        llmProgressCard.classList.remove('hidden');
        llmStatusText.textContent = data.status || 'Starting knowledge file generation...';
        addLog(data.status, 'success');
    }

    // Handle Phase 2: LLM processing progress
    if (data.phase === 'llm_processing') {
        llmProgressCard.classList.remove('hidden');
        if (data.llm_current !== undefined && data.llm_total !== undefined) {
            const percent = Math.round((data.llm_current / data.llm_total) * 100);
            llmProgressText.textContent = `${data.llm_current}/${data.llm_total} knowledge cards`;
            llmProgressPercent.textContent = `${percent}%`;
            llmProgressBar.style.width = `${percent}%`;
        }
        if (data.current_video_title) {
            llmStatusText.textContent = `Processing: ${data.current_video_title}`;
        } else {
            llmStatusText.textContent = data.status || 'Generating...';
        }
        addLog(data.status);
    }

    // Handle synthesis phase
    if (data.phase === 'synthesis') {
        llmStatusText.textContent = data.status || 'Running channel synthesis...';
        llmProgressText.textContent = 'Channel synthesis';
        llmProgressPercent.textContent = '';
        llmProgressBar.style.width = '100%';
        llmProgressBar.classList.add('synthesis-pulse');
        addLog(data.status);
    }

    // Handle LLM complete
    if (data.phase === 'llm_complete') {
        llmStatusText.textContent = 'Knowledge file generation complete!';
        llmProgressBar.classList.remove('synthesis-pulse');
        llmProgressBar.style.width = '100%';
        llmProgressPercent.textContent = '100%';
        addLog(data.status, 'success');
    }

    // Handle status updates for non-categorized phases
    if (data.status && !data.phase) {
        statusText.textContent = data.status;
        addLog(data.status);
    }

    // Handle completion
    if (data.complete) {
        showCompletion(data);
        if (eventSource) eventSource.close();
    }
}

/**
 * Show completion section
 */
function showCompletion(data) {
    // Hide warning
    warningBanner.classList.add('hidden');

    // Show completion section
    completionBanner.classList.remove('hidden');

    completedChannel.textContent = data.channel_name;
    finalTotal.textContent = data.total_videos;
    finalSaved.textContent = data.transcripts_saved;
    finalSkipped.textContent = data.videos_skipped;

    // Show knowledge cards count if applicable
    if (data.processed_files && data.processed_files.knowledge_cards) {
        finalCards.textContent = data.processed_files.knowledge_cards;
        finalCardsContainer.style.display = '';
    }

    // Update progress bars to 100%
    progressText.textContent = 'All processing complete';
    progressPercent.textContent = '100%';
    progressBar.style.width = '100%';
    statusText.textContent = 'Export complete!';

    addLog(`Export complete! ${data.transcripts_saved} transcripts saved.`, 'success');

    if (data.processed_files) {
        addLog(`${data.processed_files.knowledge_cards} knowledge cards generated.`, 'success');
    }

    // Configure Download Button
    const sid = data.session_id || currentSessionId;
    downloadBtn.href = `/api/download/${sid}`;

    resetFetchButton();

    // Scroll to completion
    completionBanner.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Add entry to log
 */
function addLog(message, type = '') {
    if (!message) return;
    const timestamp = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${timestamp}] ${message}`;
    logContent.appendChild(entry);
    logContent.scrollTop = logContent.scrollHeight;
}

/**
 * Clear log content
 */
function clearLog() {
    logContent.innerHTML = '';
}

/**
 * Reset progress display
 */
function resetProgress() {
    channelName.textContent = 'Loading...';
    channelInitial.textContent = '?';
    videoCount.textContent = '0 videos';
    savedCount.textContent = '0';
    skippedCount.textContent = '0';
    progressText.textContent = '0/0 videos processed';
    progressPercent.textContent = '0%';
    progressBar.style.width = '0%';
    statusText.textContent = 'Initializing...';

    // Reset LLM progress
    llmProgressCard.classList.add('hidden');
    llmProgressText.textContent = '0/0 knowledge cards';
    llmProgressPercent.textContent = '0%';
    llmProgressBar.style.width = '0%';
    llmProgressBar.classList.remove('synthesis-pulse');
    llmStatusText.textContent = 'Waiting...';
}

/**
 * Reset the fetch button
 */
function resetFetchButton() {
    startBtn.disabled = false;
    startBtn.innerHTML = '<span class="btn-text">Fetch Videos</span><span class="btn-icon">&#8594;</span>';
}

/**
 * Reset the processing button
 */
function resetProcessingButton() {
    startProcessingBtn.disabled = false;
    startProcessingBtn.innerHTML = '<span class="btn-text">Start Export</span><span class="btn-icon">&#8594;</span>';
}

/**
 * Reset app for new export
 */
function resetApp() {
    channelUrlInput.value = '';
    progressSection.classList.add('hidden');
    completionBanner.classList.add('hidden');
    warningBanner.classList.add('hidden');
    videoSelectionSection.classList.add('hidden');
    llmProgressCard.classList.add('hidden');
    downloadBtn.href = '#';
    fetchedVideos = [];
    currentSessionId = null;
    clearLog();
    resetProgress();
    resetFetchButton();
    channelUrlInput.focus();
}

/**
 * Escape HTML to prevent XSS in video titles
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Allow Enter key to fetch videos
channelUrlInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        handleStart();
    }
});

// Focus input on load
window.addEventListener('load', () => {
    channelUrlInput.focus();
});
