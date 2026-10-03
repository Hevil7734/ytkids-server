import os
import re

file_path = "/home/droidian/ytkids_app.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update run_download_worker yt-dlp flags
old_cmd = """    cmd = [
        "/usr/local/bin/yt-dlp",
        "--newline",
        "--no-overwrites",
        "--download-archive", archive_file,
        "--ignore-errors",
        "--no-colors",
        "--match-filters", "!is_live",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--embed-thumbnail",
        "--format", fmt,
        "--merge-output-format", "mp4",
        "-o", out_template,
    ]"""

new_cmd = """    cmd = [
        "/usr/local/bin/yt-dlp",
        "--newline",
        "--no-overwrites",
        "--download-archive", archive_file,
        "--ignore-errors",
        "--no-colors",
        "--lazy-playlist",
        "--socket-timeout", "20",
        "--retries", "5",
        "--fragment-retries", "5",
        "--no-cache-dir",
        "--match-filters", "!is_live",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--embed-thumbnail",
        "--format", fmt,
        "--merge-output-format", "mp4",
        "-o", out_template,
    ]"""

if old_cmd in code:
    code = code.replace(old_cmd, new_cmd, 1)
    print("✓ Updated yt-dlp command flags")
else:
    print("! old_cmd not found")

# 2. Update stdout loop in run_download_worker
old_loop = """        prog_regex = re.compile(r'\\[download\\]\\s+(\\d+(?:\\.\\d+)?)%\\s+of\\s+([^\\s]+)\\s+at\\s+([^\\s]+)\\s+ETA\\s+([^\\s]+)')
        dest_regex = re.compile(r'\\[download\\]\\s+Destination:\\s+(.+)')
        already_regex = re.compile(r'\\[download\\]\\s+(.+)\\s+has already been recorded in the archive')

        for line in proc.stdout:
            line_str = line.strip()
            if not line_str:
                continue
            add_log(line_str)
            dest_m = dest_regex.search(line_str)
            if dest_m:
                dest_file = os.path.basename(dest_m.group(1).strip())
                with state_lock:
                    current_job["current_file"] = dest_file
                    current_job["progress_percent"] = 0.0

            arch_m = already_regex.search(line_str)
            if arch_m:
                with state_lock:
                    current_job["current_file"] = "Already downloaded: " + os.path.basename(arch_m.group(1).strip())

            prog_m = prog_regex.search(line_str)
            if prog_m:
                pct = float(prog_m.group(1))
                speed = prog_m.group(3)
                eta = prog_m.group(4)
                with state_lock:
                    current_job["progress_percent"] = pct
                    current_job["speed"] = speed
                    current_job["eta"] = eta"""

new_loop = """        prog_regex = re.compile(r'\\[download\\]\\s+(\\d+(?:\\.\\d+)?)%\\s+of\\s+([^\\s]+)\\s+at\\s+([^\\s]+)\\s+ETA\\s+([^\\s]+)')
        dest_regex = re.compile(r'\\[download\\]\\s+Destination:\\s+(.+)')
        already_regex = re.compile(r'\\[download\\]\\s+(.+)\\s+has already been recorded in the archive')
        item_regex = re.compile(r'\\[download\\]\\s+Downloading (?:item|video)\\s+(\\d+)\\s+of\\s+(\\d+)')
        merger_regex = re.compile(r'\\[Merger\\]')
        embed_regex = re.compile(r'\\[EmbedThumbnail\\]')

        current_item_str = ""

        for line in proc.stdout:
            line_str = line.strip()
            if not line_str:
                continue
            add_log(line_str)

            item_m = item_regex.search(line_str)
            if item_m:
                current_item_str = f"Episode {item_m.group(1)}/{item_m.group(2)}: "
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Preparing video..."
                continue

            dest_m = dest_regex.search(line_str)
            if dest_m:
                dest_file = os.path.basename(dest_m.group(1).strip())
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}{dest_file}"
                    current_job["progress_percent"] = 0.0
                continue

            arch_m = already_regex.search(line_str)
            if arch_m:
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Already saved: " + os.path.basename(arch_m.group(1).strip())
                continue

            if merger_regex.search(line_str):
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Merging video & audio..."
                continue

            if embed_regex.search(line_str):
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Embedding cover art..."
                continue

            if "[youtube:tab]" in line_str or "Downloading webpage" in line_str:
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Connecting to YouTube..."
                continue

            if "timed out" in line_str.lower() or "retrying" in line_str.lower():
                with state_lock:
                    current_job["current_file"] = f"{current_item_str}Connection retry in progress..."
                continue

            prog_m = prog_regex.search(line_str)
            if prog_m:
                pct = float(prog_m.group(1))
                speed = prog_m.group(3)
                eta = prog_m.group(4)
                with state_lock:
                    current_job["progress_percent"] = pct
                    current_job["speed"] = speed
                    current_job["eta"] = eta"""

if old_loop in code:
    code = code.replace(old_loop, new_loop, 1)
    print("✓ Updated stdout loop in run_download_worker")
else:
    print("! old_loop not found")

# 3. Update Player CSS
css_start = "    /* IN-APP VIDEO PLAYER MODAL */"
css_end = "    /* Countdown Overlay */"

new_css = """    /* IN-APP FULL-BLEED VIDEO PLAYER MODAL */
    .player-modal {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      background: #000;
      z-index: 9999;
      display: none;
      overflow: hidden;
      user-select: none;
      -webkit-user-select: none;
      animation: fadeIn 0.2s ease;
    }
    .player-modal.active {
      display: block;
    }

    /* Native Fullscreen adjustments */
    :fullscreen .player-modal,
    :-webkit-full-screen .player-modal,
    .player-modal:fullscreen,
    .player-modal:-webkit-full-screen {
      width: 100vw !important;
      height: 100vh !important;
      max-width: 100vw !important;
      max-height: 100vh !important;
      background: #000 !important;
    }

    /* Video Viewport - Extends 100% across the full screen */
    .player-video-viewport {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      background: #000;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1;
      cursor: pointer;
    }
    video#mainVideo {
      width: 100%;
      height: 100%;
      max-width: 100vw;
      max-height: 100vh;
      object-fit: contain;
      background: #000;
      display: block;
    }
    :fullscreen video#mainVideo,
    :-webkit-full-screen video#mainVideo,
    .player-modal:fullscreen video#mainVideo,
    .player-modal:-webkit-full-screen video#mainVideo {
      width: 100vw !important;
      height: 100vh !important;
      object-fit: contain !important;
    }

    /* Floating Top Bar Overlay */
    .player-topbar {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: linear-gradient(to bottom, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 65%, transparent 100%);
      z-index: 25;
      transition: opacity 0.35s ease, transform 0.35s ease;
    }
    .player-back-btn {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.15);
      backdrop-filter: blur(8px);
      border: none;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all 0.2s;
    }
    .player-back-btn:hover {
      background: var(--ytk-red);
      transform: scale(1.08);
    }
    .player-back-btn svg {
      width: 24px;
      height: 24px;
      fill: #FFF;
    }
    .player-header-title {
      font-family: 'Fredoka', cursive;
      font-size: 1.35rem;
      color: #FFF;
      max-width: 60%;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: center;
      text-shadow: 0 2px 8px rgba(0,0,0,0.8);
    }
    .player-top-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .player-icon-btn {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.15);
      backdrop-filter: blur(8px);
      border: none;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      color: #FFF;
      transition: all 0.2s;
    }
    .player-icon-btn:hover {
      background: rgba(255, 255, 255, 0.3);
      transform: scale(1.08);
    }
    .player-icon-btn.locked {
      background: var(--ytk-yellow);
      color: #000;
    }
    .player-icon-btn svg {
      width: 22px;
      height: 22px;
      fill: currentColor;
    }

    /* Floating Center Controls Overlay */
    .player-center-controls {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      display: flex;
      align-items: center;
      gap: 32px;
      z-index: 25;
      transition: opacity 0.35s ease, transform 0.35s ease;
    }
    .center-ctrl-btn {
      width: 62px;
      height: 62px;
      border-radius: 50%;
      background: rgba(0, 0, 0, 0.65);
      border: 2px solid rgba(255, 255, 255, 0.35);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      color: #FFF;
      transition: transform 0.2s, background 0.2s;
      backdrop-filter: blur(6px);
    }
    .center-ctrl-btn:hover {
      transform: scale(1.12);
      background: rgba(0, 0, 0, 0.85);
    }
    .center-ctrl-btn.play-toggle {
      width: 86px;
      height: 86px;
      background: var(--ytk-red);
      border: none;
      box-shadow: 0 8px 32px rgba(255, 0, 51, 0.65);
    }
    .center-ctrl-btn.play-toggle:hover {
      transform: scale(1.15);
      background: #FF1A47;
    }
    .center-ctrl-btn svg {
      width: 28px;
      height: 28px;
      fill: currentColor;
    }
    .center-ctrl-btn.play-toggle svg {
      width: 42px;
      height: 42px;
    }

    /* Floating Player Bottom Bar */
    .player-bottom-bar {
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      padding: 16px 28px 24px;
      background: linear-gradient(to top, rgba(0,0,0,0.9) 0%, rgba(0,0,0,0.5) 70%, transparent 100%);
      z-index: 25;
      transition: opacity 0.35s ease, transform 0.35s ease;
    }
    .player-timeline-row {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 10px;
    }
    .player-time {
      font-size: 1rem;
      font-weight: 700;
      color: #FFF;
      font-variant-numeric: tabular-nums;
      min-width: 70px;
      text-shadow: 0 1px 4px rgba(0,0,0,0.8);
    }
    .player-seek-slider {
      flex: 1;
      -webkit-appearance: none;
      height: 8px;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.25);
      outline: none;
      cursor: pointer;
    }
    .player-seek-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: var(--ytk-red);
      border: 3px solid #FFF;
      box-shadow: 0 2px 8px rgba(0,0,0,0.6);
      cursor: pointer;
      transition: transform 0.15s;
    }
    .player-seek-slider::-webkit-slider-thumb:hover {
      transform: scale(1.3);
    }
    .player-actions-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .player-actions-group {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .player-pill-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 18px;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.15);
      backdrop-filter: blur(8px);
      color: #FFF;
      font-weight: 800;
      font-size: 0.9rem;
      border: none;
      cursor: pointer;
      transition: all 0.2s;
    }
    .player-pill-btn:hover {
      background: var(--ytk-cyan);
      color: #000;
      transform: translateY(-2px);
    }
    .ep-count-pill {
      background: var(--ytk-red);
      color: #FFF;
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 12px;
      font-weight: 800;
    }

    /* CONTROLS AUTO-HIDE RULES */
    .player-modal:not(.controls-visible) .player-topbar {
      opacity: 0;
      pointer-events: none;
      transform: translateY(-30px);
    }
    .player-modal:not(.controls-visible) .player-bottom-bar {
      opacity: 0;
      pointer-events: none;
      transform: translateY(30px);
    }
    .player-modal:not(.controls-visible) .player-center-controls {
      opacity: 0;
      pointer-events: none;
      transform: translate(-50%, -50%) scale(0.9);
    }
    .player-modal:not(.controls-visible) {
      cursor: none;
    }

    /* UP NEXT / EPISODES SLIDE-UP DRAWER (HIDDEN BY DEFAULT WHILE PLAYING) */
    .playlist-rail-drawer {
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      background: linear-gradient(to top, rgba(8,11,18,0.98) 85%, rgba(8,11,18,0.85));
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      padding: 16px 24px 22px;
      z-index: 40;
      transform: translateY(105%);
      transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: 0 -12px 35px rgba(0,0,0,0.85);
      border-top: 1px solid rgba(255,255,255,0.1);
    }
    .playlist-rail-drawer.active {
      transform: translateY(0);
    }
    .playlist-drawer-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }
    .playlist-drawer-title {
      font-family: 'Fredoka', cursive;
      font-size: 1.25rem;
      color: #FFF;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .drawer-close-btn {
      background: rgba(255,255,255,0.15);
      color: #FFF;
      border: none;
      border-radius: 20px;
      padding: 6px 16px;
      font-weight: 700;
      font-size: 0.85rem;
      cursor: pointer;
      transition: background 0.2s;
    }
    .drawer-close-btn:hover {
      background: var(--ytk-red);
    }
    .playlist-rail {
      max-height: 155px;
      display: flex;
      gap: 14px;
      overflow-x: auto;
      padding-bottom: 6px;
    }
    .playlist-card {
      width: 180px;
      flex-shrink: 0;
      background: var(--card-bg);
      border-radius: 12px;
      overflow: hidden;
      cursor: pointer;
      border: 2px solid transparent;
      transition: all 0.2s;
    }
    .playlist-card:hover {
      border-color: var(--ytk-cyan);
      transform: translateY(-3px);
    }
    .playlist-card.active {
      border-color: var(--ytk-red);
    }
    .playlist-card-thumb {
      width: 100%;
      height: 85px;
      object-fit: cover;
    }
    .playlist-card-title {
      font-size: 0.8rem;
      font-weight: 700;
      padding: 6px 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* Live logs inside Grown-ups download card */
    .dl-live-logs {
      background: #080c14;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      padding: 8px 12px;
      font-family: monospace;
      font-size: 0.76rem;
      color: #7dd3fc;
      max-height: 75px;
      overflow-y: auto;
      margin-top: 10px;
      line-height: 1.4;
      white-space: pre-wrap;
      word-break: break-all;
    }\n\n"""

idx_start = code.find(css_start)
idx_end = code.find(css_end)
if idx_start != -1 and idx_end != -1:
    code = code[:idx_start] + new_css + code[idx_end:]
    print("✓ Updated Player CSS")
else:
    print("! css markers not found")

# 4. Update Player HTML
old_player_html = """  <!-- FULLSCREEN / APP VIDEO PLAYER MODAL -->
  <div class="player-modal" id="playerModal">
    <!-- Top Bar -->
    <div class="player-topbar">
      <button class="player-back-btn" onclick="closePlayer()" title="Back to Library">
        <svg viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
      </button>
      <div class="player-header-title" id="playerTitle">Episode Title</div>
      <div class="player-top-actions">
        <button class="player-icon-btn" id="kidLockBtn" onclick="toggleKidLock()" title="Toddler Lock">
          <svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
        </button>
        <button class="player-icon-btn" onclick="toggleFullscreen(playerModal)" title="Fullscreen">
          <svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
        </button>
      </div>
    </div>

    <!-- Video Viewport (CLICKING HERE TOGGLES FULLSCREEN ON/OFF) -->
    <div class="player-video-viewport" id="videoViewport" onclick="handleViewportClick(event)" title="Click video to toggle Fullscreen">
      <video id="mainVideo" playsinline preload="metadata"></video>

      <!-- Center Controls -->
      <div class="player-center-controls" id="centerControls">
        <button class="center-ctrl-btn" onclick="event.stopPropagation(); skipTime(-10)" title="Rewind 10s">
          <svg viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z"/></svg>
        </button>
        <button class="center-ctrl-btn play-toggle" id="centerPlayBtn" onclick="event.stopPropagation(); togglePlayPause()" title="Play/Pause">
          <svg id="centerPlayIcon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
        </button>
        <button class="center-ctrl-btn" onclick="event.stopPropagation(); skipTime(10)" title="Skip 10s">
          <svg viewBox="0 0 24 24"><path d="M12 5V1l5 5-5 5V7c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6h2c0 4.42-3.58 8-8 8s-8-3.58-8-8 3.58-8 8-8z"/></svg>
        </button>
      </div>

      <!-- Autoplay Next Episode Countdown Overlay -->
      <div class="countdown-overlay" id="countdownOverlay" onclick="event.stopPropagation()">
        <h3 style="font-family:'Fredoka';font-size:1.6rem;color:#FFF;">Next Episode Starting!</h3>
        <div class="countdown-number" id="countdownSecs">3</div>
        <button class="cancel-countdown-btn" onclick="cancelAutoplayCountdown()">Stay Here</button>
      </div>
    </div>

    <!-- Bottom Controls (Autoplay moved exclusively to Grown-ups) -->
    <div class="player-bottom-bar" id="playerBottomBar" onclick="event.stopPropagation()">
      <div class="player-timeline-row">
        <span class="player-time" id="timeCurrent">00:00</span>
        <input type="range" class="player-seek-slider" id="seekSlider" min="0" max="100" value="0" oninput="onSeekInput(this.value)">
        <span class="player-time" id="timeDuration" style="text-align:right;">00:00</span>
      </div>
      <div class="player-actions-row">
        <button class="next-ep-btn" onclick="playNextEpisode()">
          <span>Next Episode</span>
          <svg viewBox="0 0 24 24" style="width:18px;height:18px;fill:currentColor;"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
        </button>
      </div>
    </div>

    <!-- Up Next Playlist Rail -->
    <div class="playlist-rail" id="playlistRail" onclick="event.stopPropagation()"></div>
  </div>"""

new_player_html = """  <!-- FULLSCREEN / APP VIDEO PLAYER MODAL -->
  <div class="player-modal" id="playerModal" onclick="handlePlayerModalClick(event)">
    <!-- Video Viewport (Occupies 100% of the entire screen) -->
    <div class="player-video-viewport" id="videoViewport">
      <video id="mainVideo" playsinline preload="metadata"></video>
    </div>

    <!-- Floating Top Bar Overlay -->
    <div class="player-topbar" id="playerTopBar" onclick="event.stopPropagation()">
      <button class="player-back-btn" onclick="closePlayer()" title="Back to Library">
        <svg viewBox="0 0 24 24"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
      </button>
      <div class="player-header-title" id="playerTitle">Episode Title</div>
      <div class="player-top-actions">
        <button class="player-icon-btn" id="kidLockBtn" onclick="toggleKidLock()" title="Toddler Lock">
          <svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
        </button>
        <button class="player-icon-btn" onclick="toggleFullscreen()" title="Fullscreen">
          <svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
        </button>
      </div>
    </div>

    <!-- Floating Center Controls Overlay -->
    <div class="player-center-controls" id="centerControls" onclick="event.stopPropagation()">
      <button class="center-ctrl-btn" onclick="skipTime(-10)" title="Rewind 10s">
        <svg viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6H4c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8z"/></svg>
      </button>
      <button class="center-ctrl-btn play-toggle" id="centerPlayBtn" onclick="togglePlayPause()" title="Play/Pause">
        <svg id="centerPlayIcon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
      </button>
      <button class="center-ctrl-btn" onclick="skipTime(10)" title="Skip 10s">
        <svg viewBox="0 0 24 24"><path d="M12 5V1l5 5-5 5V7c-3.31 0-6 2.69-6 6s2.69 6 6 6 6-2.69 6-6h2c0 4.42-3.58 8-8 8s-8-3.58-8-8 3.58-8 8-8z"/></svg>
      </button>
    </div>

    <!-- Autoplay Next Episode Countdown Overlay -->
    <div class="countdown-overlay" id="countdownOverlay" onclick="event.stopPropagation()">
      <h3 style="font-family:'Fredoka';font-size:1.6rem;color:#FFF;">Next Episode Starting!</h3>
      <div class="countdown-number" id="countdownSecs">3</div>
      <button class="cancel-countdown-btn" onclick="cancelAutoplayCountdown()">Stay Here</button>
    </div>

    <!-- Floating Bottom Controls Overlay -->
    <div class="player-bottom-bar" id="playerBottomBar" onclick="event.stopPropagation()">
      <div class="player-timeline-row">
        <span class="player-time" id="timeCurrent">00:00</span>
        <input type="range" class="player-seek-slider" id="seekSlider" min="0" max="100" value="0" oninput="onSeekInput(this.value)">
        <span class="player-time" id="timeDuration" style="text-align:right;">00:00</span>
      </div>
      <div class="player-actions-row">
        <div class="player-actions-group">
          <button class="player-pill-btn" id="toggleDrawerBtn" onclick="togglePlaylistDrawer()">
            <span>🎬 Up Next</span>
            <span class="ep-count-pill" id="epCountPill">0</span>
          </button>
          <button class="player-pill-btn" onclick="playNextEpisode()">
            <span>Next Episode</span>
            <svg viewBox="0 0 24 24" style="width:18px;height:18px;fill:currentColor;"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
          </button>
        </div>
        <button class="player-icon-btn" onclick="toggleFullscreen()" title="Fullscreen" style="width:40px;height:40px;">
          <svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
        </button>
      </div>
    </div>

    <!-- Up Next Playlist Slide-Up Drawer -->
    <div class="playlist-rail-drawer" id="playlistDrawer" onclick="event.stopPropagation()">
      <div class="playlist-drawer-header">
        <div class="playlist-drawer-title">
          <span>🎬 Up Next in Show</span>
          <span style="font-size:0.9rem;color:var(--text-muted);font-family:'Quicksand';" id="drawerEpCount"></span>
        </div>
        <button class="drawer-close-btn" onclick="togglePlaylistDrawer(false)">✕ Close</button>
      </div>
      <div class="playlist-rail" id="playlistRail"></div>
    </div>
  </div>"""

if old_player_html in code:
    code = code.replace(old_player_html, new_player_html, 1)
    print("✓ Updated Player HTML")
else:
    print("! old_player_html not found")

# 5. Add live logs to dlProgressCard HTML
old_card_html = """          <div class="progress-info" style="font-size:0.85rem;color:var(--text-muted);">
            <span id="dlCurrentFile">Connecting...</span>
            <span id="dlSpeedEta">Speed: -- | ETA: --</span>
          </div>"""

new_card_html = """          <div class="progress-info" style="font-size:0.85rem;color:var(--text-muted);">
            <span id="dlCurrentFile">Connecting...</span>
            <span id="dlSpeedEta">Speed: -- | ETA: --</span>
          </div>
          <div class="dl-live-logs" id="dlLiveLogs">Connecting to YouTube...</div>"""

if old_card_html in code:
    code = code.replace(old_card_html, new_card_html, 1)
    print("✓ Updated dlProgressCard HTML")
else:
    print("! old_card_html not found")

# 6. Update Player JS
js_start = "    // ================= VIDEO PLAYER & FULLSCREEN TOGGLE ================="
js_end = "    // ================= GROWN-UPS MODAL & SEARCH/DOWNLOADER ================="

new_player_js_clean = """    // ================= VIDEO PLAYER & FULLSCREEN TOGGLE =================
    const videoElem = document.getElementById('mainVideo');
    const playerModal = document.getElementById('playerModal');
    const seekSlider = document.getElementById('seekSlider');

    // Cross-browser Fullscreen Toggle
    function toggleFullscreen() {
      const isFs = document.fullscreenElement || document.webkitFullscreenElement || document.mozFullScreenElement;

      if (!isFs) {
        if (playerModal.requestFullscreen) {
          playerModal.requestFullscreen().catch(() => {
            if (videoElem.webkitEnterFullscreen) videoElem.webkitEnterFullscreen();
          });
        } else if (playerModal.webkitRequestFullscreen) {
          playerModal.webkitRequestFullscreen();
        } else if (videoElem.webkitEnterFullscreen) {
          videoElem.webkitEnterFullscreen();
        }
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen().catch(() => {});
        } else if (document.webkitExitFullscreen) {
          document.webkitExitFullscreen();
        }
      }
    }

    // Modal click handling: tap shows controls; tap on video toggles fullscreen when controls are visible
    function handlePlayerModalClick(e) {
      if (isKidLocked) return;

      const drawer = document.getElementById('playlistDrawer');
      if (drawer && drawer.classList.contains('active')) {
        drawer.classList.remove('active');
        resetControlsTimeout();
        return;
      }

      if (!playerModal.classList.contains('controls-visible')) {
        showControls();
        return;
      }

      // If controls are visible and user clicked the video or viewport
      if (e.target === videoElem || e.target === document.getElementById('videoViewport')) {
        toggleFullscreen();
      }
      resetControlsTimeout();
    }

    function showControls() {
      playerModal.classList.add('controls-visible');
      resetControlsTimeout();
    }

    function togglePlaylistDrawer(forceState) {
      const drawer = document.getElementById('playlistDrawer');
      if (!drawer) return;
      if (typeof forceState === 'boolean') {
        if (forceState) drawer.classList.add('active');
        else drawer.classList.remove('active');
      } else {
        drawer.classList.toggle('active');
      }
      resetControlsTimeout();
    }

    function playEpisodeFromList(playlist, index) {
      if (!playlist || playlist.length === 0 || index < 0 || index >= playlist.length) return;
      currentPlaylist = playlist;
      currentVideoIdx = index;
      const ep = currentPlaylist[currentVideoIdx];

      document.getElementById('playerTitle').textContent = ep.title;
      videoElem.src = url(ep.stream_url);
      playerModal.classList.add('active');
      togglePlaylistDrawer(false);
      cancelAutoplayCountdown();

      videoElem.play().catch(e => console.log("Autoplay notice:", e));
      updatePlayIcon(true);
      renderPlaylistRail();
      showControls();

      // Launch in fullscreen automatically as it does in mobile app
      toggleFullscreen();
    }

    function closePlayer() {
      videoElem.pause();
      videoElem.src = '';
      playerModal.classList.remove('active');
      togglePlaylistDrawer(false);
      cancelAutoplayCountdown();
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        if (document.exitFullscreen) document.exitFullscreen().catch(() => {});
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      }
    }

    function togglePlayPause() {
      if (videoElem.paused) {
        videoElem.play();
        updatePlayIcon(true);
      } else {
        videoElem.pause();
        updatePlayIcon(false);
      }
      resetControlsTimeout();
    }

    function updatePlayIcon(isPlaying) {
      const icon = document.getElementById('centerPlayIcon');
      if (isPlaying) {
        icon.innerHTML = '<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';
      } else {
        icon.innerHTML = '<path d="M8 5v14l11-7z"/>';
      }
    }

    function skipTime(seconds) {
      videoElem.currentTime = Math.max(0, Math.min(videoElem.duration || 0, videoElem.currentTime + seconds));
      resetControlsTimeout();
    }

    function playNextEpisode() {
      if (currentVideoIdx + 1 < currentPlaylist.length) {
        playEpisodeFromList(currentPlaylist, currentVideoIdx + 1);
      } else {
        playEpisodeFromList(currentPlaylist, 0);
      }
    }

    videoElem.addEventListener('timeupdate', () => {
      if (!videoElem.duration) return;
      const cur = videoElem.currentTime;
      const dur = videoElem.duration;
      document.getElementById('timeCurrent').textContent = formatSeconds(cur);
      document.getElementById('timeDuration').textContent = formatSeconds(dur);
      seekSlider.value = (cur / dur) * 100;
    });

    videoElem.addEventListener('ended', () => {
      if (autoplay && currentPlaylist.length > 1) {
        startAutoplayCountdown();
      }
    });

    function onSeekInput(val) {
      if (!videoElem.duration) return;
      videoElem.currentTime = (val / 100) * videoElem.duration;
      resetControlsTimeout();
    }

    function startAutoplayCountdown() {
      const overlay = document.getElementById('countdownOverlay');
      const numElem = document.getElementById('countdownSecs');
      overlay.classList.add('active');
      let count = 3;
      numElem.textContent = count;
      countdownTimer = setInterval(() => {
        count -= 1;
        if (count > 0) {
          numElem.textContent = count;
        } else {
          clearInterval(countdownTimer);
          overlay.classList.remove('active');
          playNextEpisode();
        }
      }, 1000);
    }

    function cancelAutoplayCountdown() {
      if (countdownTimer) clearInterval(countdownTimer);
      document.getElementById('countdownOverlay').classList.remove('active');
    }

    function toggleKidLock() {
      isKidLocked = !isKidLocked;
      const btn = document.getElementById('kidLockBtn');
      if (isKidLocked) {
        btn.classList.add('locked');
        btn.title = "Screen Locked! Tap to unlock.";
      } else {
        btn.classList.remove('locked');
        btn.title = "Screen Unlocked.";
      }
    }

    function resetControlsTimeout() {
      playerModal.classList.add('controls-visible');
      clearTimeout(controlsHideTimeout);
      controlsHideTimeout = setTimeout(() => {
        const drawer = document.getElementById('playlistDrawer');
        const drawerOpen = drawer && drawer.classList.contains('active');
        if (!videoElem.paused && !drawerOpen) {
          playerModal.classList.remove('controls-visible');
        }
      }, 2800);
    }

    function renderPlaylistRail() {
      const rail = document.getElementById('playlistRail');
      if (!rail) return;
      const countElem = document.getElementById('epCountPill');
      if (countElem) countElem.textContent = currentPlaylist.length;
      const drawerCount = document.getElementById('drawerEpCount');
      if (drawerCount) drawerCount.textContent = `(${currentPlaylist.length} episodes)`;

      rail.innerHTML = currentPlaylist.map((ep, idx) => `
        <div class="playlist-card ${idx === currentVideoIdx ? 'active' : ''}" onclick="playEpisodeFromList(currentPlaylist, ${idx})">
          <img class="playlist-card-thumb" src="${url(ep.thumb_url)}" alt="Thumb" onerror="this.style.background='#222'">
          <div class="playlist-card-title">${escapeHtml(ep.title)}</div>
        </div>
      `).join('');
    }\n\n"""

idx_js_start = code.find(js_start)
idx_js_end = code.find(js_end)
if idx_js_start != -1 and idx_js_end != -1:
    code = code[:idx_js_start] + new_player_js_clean + code[idx_js_end:]
    print("✓ Updated Player JS")
else:
    print("! js markers not found")

# 7. Update pollStatus to update live log viewer
old_poll = """          if (s.active || s.status === 'downloading') {
            card.classList.add('active');
            document.getElementById('dlStatusText').textContent = `Downloading ${s.folder}...`;
            document.getElementById('dlPercentText').textContent = `${s.progress_percent}%`;
            document.getElementById('dlProgressFill').style.width = `${s.progress_percent}%`;
            document.getElementById('dlCurrentFile').textContent = s.current_file || 'Downloading...';
            document.getElementById('dlSpeedEta').textContent = `Speed: ${s.speed || '--'} | ETA: ${s.eta || '--'}`;
            setTimeout(pollStatus, 1500);"""

new_poll = """          if (s.active || s.status === 'downloading') {
            card.classList.add('active');
            document.getElementById('dlStatusText').textContent = `Downloading ${s.folder}...`;
            document.getElementById('dlPercentText').textContent = `${s.progress_percent}%`;
            document.getElementById('dlProgressFill').style.width = `${s.progress_percent}%`;
            document.getElementById('dlCurrentFile').textContent = s.current_file || 'Downloading...';
            document.getElementById('dlSpeedEta').textContent = `Speed: ${s.speed || '--'} | ETA: ${s.eta || '--'}`;
            if (s.logs && s.logs.length > 0) {
              const logsBox = document.getElementById('dlLiveLogs');
              if (logsBox) {
                logsBox.textContent = s.logs.slice(-4).join('\\n');
                logsBox.scrollTop = logsBox.scrollHeight;
              }
            }
            setTimeout(pollStatus, 1500);"""

if old_poll in code:
    code = code.replace(old_poll, new_poll, 1)
    print("✓ Updated pollStatus JS")
else:
    print("! old_poll not found")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("SUCCESS: ytkids_app.py modified!")
