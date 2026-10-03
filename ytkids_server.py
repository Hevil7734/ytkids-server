#!/usr/bin/env python3
"""
YouTube Kids Web App & Channel Downloader for Debian
Authentic YouTube Kids Android app-like experience:
- Home screen with shuffled cartoon recommendations & instant shuffle button
- In-app video player with HTTP Range 206 partial streaming
- Video playback click toggles fullscreen ON/OFF (just like the YouTube Kids app)
- Autoplay next episode managed exclusively in Grown-ups parental area
- YouTube channel search & downloader moved exclusively into Grown-ups parental area
- Series & Episode organization (Masha and the Bear, Bluey, Peppa Pig, Music & Nursery Rhymes)
- Automatic 16:9 thumbnail previews & ffmpeg extraction
"""

import os
import re
import sys
import json
import shutil
import hashlib
import subprocess
import threading
import time
import urllib.parse
from flask import Flask, request, jsonify, render_template_string, send_file, Response

app = Flask(__name__)

CARTOONS_DIR = "/srv/videos/Cartoons"
THUMBS_DIR = os.path.join(CARTOONS_DIR, ".thumbs")
os.makedirs(THUMBS_DIR, exist_ok=True)

state_lock = threading.RLock()
download_queue = []
current_job = {
    "active": False,
    "target_title": "",
    "target_url": "",
    "folder": "",
    "quality": "720",
    "limit": 10,
    "progress_percent": 0.0,
    "speed": "",
    "eta": "",
    "current_file": "",
    "status": "idle",
    "error_message": "",
    "logs": [],
    "process": None,
    "job_id": ""
}

def process_next_in_queue():
    global current_job, download_queue
    next_job = None
    with state_lock:
        if current_job.get("active", False):
            return
        if download_queue:
            next_job = download_queue.pop(0)
            current_job["active"] = True
            current_job["target_title"] = next_job["target_title"]
            current_job["target_url"] = next_job["target_url"]
            current_job["folder"] = sanitize_folder_name(next_job["folder"])
            current_job["quality"] = next_job["quality"]
            current_job["limit"] = next_job["limit"]
            current_job["job_id"] = next_job["id"]
            current_job["status"] = "downloading"
            current_job["progress_percent"] = 0.0
            current_job["speed"] = ""
            current_job["eta"] = ""
            current_job["current_file"] = "Starting next channel in queue..."
            current_job["error_message"] = ""
            current_job["logs"] = []

    if next_job:
        add_log(f"[Queue Worker] Starting next queued show: '{next_job['folder']}'")
        t = threading.Thread(
            target=run_download_worker,
            args=(next_job["target_url"], next_job["folder"], next_job["quality"], next_job["limit"]),
            daemon=True
        )
        t.start()

search_cache_lock = threading.Lock()
search_cache = {}

library_lock = threading.Lock()
library_cache = None
library_last_scanned = 0

def add_log(msg):
    with state_lock:
        current_job["logs"].append(msg.strip())
        if len(current_job["logs"]) > 200:
            current_job["logs"].pop(0)

def sanitize_folder_name(name):
    name = re.sub(r' - (Official Channel|Videos|Nursery Rhymes|Kids Songs)$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[\\/*?:"<>|]', '', name).strip()
    return name if name else "Cartoons"

def normalize_channel_url(url):
    url = url.strip()
    if url.startswith("@"):
        return f"https://www.youtube.com/{url}/videos"
    if not url.startswith("http://") and not url.startswith("https://"):
        return f"https://www.youtube.com/{url}"
    if ("youtube.com/@" in url or "youtube.com/channel/" in url) and not any(
        t in url for t in ["/videos", "/shorts", "/playlists", "/streams"]
    ):
        return url.rstrip("/") + "/videos"
    return url

def clean_episode_title(raw_name, show_name=""):
    base, _ = os.path.splitext(raw_name)
    if show_name:
        clean_show = re.escape(show_name)
        base = re.sub(rf'^{clean_show}\s*[-–—|:]\s*', '', base, flags=re.IGNORECASE)
    base = re.sub(r'[\(\[\{].*?(?:official|full video|4k|1080p|hd|202\d|new episode|cartoon for kids).*?[\)\]\}]', '', base, flags=re.IGNORECASE)
    base = re.sub(r'\s*[|｜].*$', '', base)
    base = re.sub(r'\s+', ' ', base).strip(' -–—|: ')
    return base if base else os.path.splitext(raw_name)[0]

def extract_episode_num(filename):
    m = re.search(r'(?:episode|ep\.?)\s*(\d+)', filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'\b(?:e|s\d+e)(\d+)\b', filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 9999

def get_disk_info():
    try:
        stat = shutil.disk_usage(CARTOONS_DIR)
        return {
            "total_gb": round(stat.total / (1024**3), 1),
            "free_gb": round(stat.free / (1024**3), 1),
            "used_gb": round(stat.used / (1024**3), 1),
            "percent": round((stat.used / stat.total) * 100, 1)
        }
    except Exception:
        return {"total_gb": 0, "free_gb": 0, "used_gb": 0, "percent": 0}

def scan_library(force=False):
    global library_cache, library_last_scanned
    now = time.time()
    with library_lock:
        if library_cache and not force and (now - library_last_scanned < 10):
            return library_cache

    shows_map = {}
    all_episodes = []
    video_exts = (".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov")

    if not os.path.exists(CARTOONS_DIR):
        os.makedirs(CARTOONS_DIR, exist_ok=True)

    for root, dirs, files in os.walk(CARTOONS_DIR):
        if ".thumbs" in root:
            continue
        for f in sorted(files):
            if not f.lower().endswith(video_exts) or f.endswith(".part"):
                continue
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, CARTOONS_DIR)
            parts = rel_path.split(os.sep)

            if len(parts) == 1:
                is_music = any(k in f.lower() for k in ["song", "rhyme", "monkey", "macdonald", "twinkle", "nursery", "music"])
                show_name = "Music & Nursery Rhymes" if is_music else "Cartoons"
            else:
                show_name = parts[0]

            clean_title = clean_episode_title(f, show_name)
            ep_num = extract_episode_num(f)

            try:
                size_mb = round(os.path.getsize(full_path) / (1024 * 1024), 1)
            except Exception:
                size_mb = 0

            # Check thumbnail
            stem, _ = os.path.splitext(full_path)
            thumb_rel = None
            for ext in [".jpg", ".webp", "-poster.jpg", "-thumb.jpg", ".png"]:
                if os.path.exists(stem + ext):
                    thumb_rel = os.path.relpath(stem + ext, CARTOONS_DIR)
                    break
            if not thumb_rel:
                thumb_rel = rel_path

            ep_obj = {
                "id": hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12],
                "title": clean_title,
                "raw_title": os.path.splitext(f)[0],
                "filename": f,
                "show": show_name,
                "rel_path": rel_path,
                "stream_url": f"stream/{urllib.parse.quote(rel_path)}",
                "thumb_url": f"thumb/{urllib.parse.quote(thumb_rel)}",
                "size_mb": size_mb,
                "ep_num": ep_num,
                "ext": os.path.splitext(f)[1].lower()
            }

            if show_name not in shows_map:
                shows_map[show_name] = {
                    "id": hashlib.md5(show_name.encode("utf-8")).hexdigest()[:8],
                    "title": show_name,
                    "folder": parts[0] if len(parts) > 1 else "",
                    "poster_url": ep_obj["thumb_url"],
                    "episodes": []
                }

            if len(parts) > 1:
                folder_dir = os.path.join(CARTOONS_DIR, parts[0])
                for fa in ["poster.jpg", "folder.jpg", "cover.jpg"]:
                    fap = os.path.join(folder_dir, fa)
                    if os.path.exists(fap):
                        shows_map[show_name]["poster_url"] = f"thumb/{urllib.parse.quote(os.path.relpath(fap, CARTOONS_DIR))}"
                        break

            shows_map[show_name]["episodes"].append(ep_obj)
            all_episodes.append(ep_obj)

    shows_list = []
    for s_name, s_data in sorted(shows_map.items()):
        s_data["episodes"].sort(key=lambda x: (x["ep_num"], x["title"]))
        s_data["episode_count"] = len(s_data["episodes"])
        shows_list.append(s_data)

    all_episodes.sort(key=lambda x: (x["show"], x["ep_num"], x["title"]))
    music_episodes = [ep for ep in all_episodes if ep["show"] == "Music & Nursery Rhymes" or "song" in ep["title"].lower() or "rhyme" in ep["title"].lower()]

    result = {
        "shows": shows_list,
        "all_episodes": all_episodes,
        "music_episodes": music_episodes,
        "stats": {
            "total_shows": len(shows_list),
            "total_episodes": len(all_episodes),
            "disk": get_disk_info()
        }
    }

    with library_lock:
        library_cache = result
        library_last_scanned = time.time()

    return result

def generate_video_thumbnails(target_dir):
    extensions = (".mp4", ".mkv", ".webm", ".avi", ".mov")
    try:
        first_thumb = None
        for root, _, files in os.walk(target_dir):
            for f in files:
                if f.endswith(extensions) and not f.endswith(".part"):
                    base_name, _ = os.path.splitext(f)
                    thumb_path = os.path.join(root, base_name + "-thumb.jpg")
                    poster_path = os.path.join(root, base_name + "-poster.jpg")
                    jpg_path = os.path.join(root, base_name + ".jpg")
                    webp_path = os.path.join(root, base_name + ".webp")

                    if os.path.exists(webp_path) and not os.path.exists(jpg_path):
                        subprocess.run(["ffmpeg", "-y", "-i", webp_path, jpg_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    src_img = jpg_path if os.path.exists(jpg_path) else (thumb_path if os.path.exists(thumb_path) else (poster_path if os.path.exists(poster_path) else None))

                    if not src_img:
                        video_path = os.path.join(root, f)
                        cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:03", "-frames:v", "1", "-update", "1", jpg_path]
                        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if res.returncode == 0:
                            src_img = jpg_path

                    if src_img and not first_thumb:
                        first_thumb = src_img

        if first_thumb and target_dir != CARTOONS_DIR:
            for fa in ["poster.jpg", "folder.jpg", "cover.jpg"]:
                fap = os.path.join(target_dir, fa)
                if not os.path.exists(fap):
                    shutil.copy2(first_thumb, fap)

        try:
            subprocess.run(["sudo", "-n", "chmod", "-R", "a+rX", target_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    except Exception as e:
        add_log(f"[Thumbnails] Warning: {e}")

def run_download_worker(target_url, folder_name, quality, limit):
    global current_job
    clean_folder = sanitize_folder_name(folder_name)
    target_dir = os.path.join(CARTOONS_DIR, clean_folder)
    os.makedirs(target_dir, exist_ok=True)
    archive_file = os.path.join(target_dir, ".downloaded_archive.txt")
    download_url = normalize_channel_url(target_url)

    with state_lock:
        current_job["active"] = True
        current_job["status"] = "downloading"
        current_job["progress_percent"] = 0.0
        current_job["error_message"] = ""
        current_job["folder"] = clean_folder
        current_job["speed"] = ""
        current_job["eta"] = ""
        current_job["current_file"] = "Connecting to YouTube..."

    add_log("==================================================")
    add_log(f"[Download Started] Show: {clean_folder}")
    add_log(f"[Target URL] {download_url}")
    add_log(f"[Max Episodes] {limit if limit > 0 else 'All Available'}")
    add_log(f"[Quality] {quality}p HD")
    add_log("==================================================")

    if quality == "1080":
        fmt = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    elif quality == "480":
        fmt = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    else:
        fmt = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"

    out_template = os.path.join(target_dir, "%(title)s.%(ext)s")
    cmd = [
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
    ]
    if limit > 0:
        cmd.extend(["--playlist-end", str(limit)])
    cmd.append(download_url)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        with state_lock:
            current_job["process"] = proc

        prog_regex = re.compile(r'\[download\]\s+(\d+(?:\.\d+)?)%\s+of\s+([^\s]+)\s+at\s+([^\s]+)\s+ETA\s+([^\s]+)')
        dest_regex = re.compile(r'\[download\]\s+Destination:\s+(.+)')
        already_regex = re.compile(r'\[download\]\s+(.+)\s+has already been recorded in the archive')
        item_regex = re.compile(r'\[download\]\s+Downloading (?:item|video)\s+(\d+)\s+of\s+(\d+)')
        merger_regex = re.compile(r'\[Merger\]')
        embed_regex = re.compile(r'\[EmbedThumbnail\]')

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
                    current_job["eta"] = eta

        proc.wait()
        with state_lock:
            current_job["process"] = None
            if current_job["status"] != "stopped":
                current_job["status"] = "finished"
                current_job["progress_percent"] = 100.0
                add_log("[Download] All requested episodes finished!")

    except Exception as e:
        with state_lock:
            current_job["status"] = "error"
            current_job["error_message"] = str(e)
            current_job["process"] = None
        add_log(f"[Error] {e}")

    finally:
        generate_video_thumbnails(target_dir)
        scan_library(force=True)
        with state_lock:
            current_job["active"] = False
        process_next_in_queue()

def search_youtube(query):
    channels = []
    encoded_q = urllib.parse.quote(query)
    search_url = f"https://www.youtube.com/results?search_query={encoded_q}&sp=EgIQAg%253D%253D"
    cmd = [
        "curl", "-4", "--tlsv1.2", "-s", "-L", "--compressed", "--max-time", "8",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "-H", "Accept-Language: en-US,en;q=0.9",
        search_url
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        html = res.stdout
        m = re.search(r'var ytInitialData = ({.*?});</script>', html) or re.search(r'ytInitialData = ({.*?});</script>', html)
        if m:
            data = json.loads(m.group(1))
            def find_channel_renderers(obj):
                results = []
                if isinstance(obj, dict):
                    if "channelRenderer" in obj:
                        results.append(obj["channelRenderer"])
                    for v in obj.values():
                        results.extend(find_channel_renderers(v))
                elif isinstance(obj, list):
                    for item in obj:
                        results.extend(find_channel_renderers(item))
                return results

            ch_list = find_channel_renderers(data)
            seen_urls = set()
            for ch in ch_list:
                title = ch.get('title', {}).get('simpleText', '')
                if not title:
                    runs = ch.get('title', {}).get('runs', [])
                    if runs:
                        title = runs[0].get('text', '')
                if not title:
                    continue
                nav = ch.get('navigationEndpoint', {}).get('commandMetadata', {}).get('webCommandMetadata', {}).get('url', '')
                channel_url = f"https://www.youtube.com{nav}/videos" if nav.startswith("/") else f"https://www.youtube.com/{nav}"
                if channel_url in seen_urls:
                    continue
                seen_urls.add(channel_url)
                subs = ch.get('videoCountText', {}).get('simpleText', '') or ch.get('subscriberCountText', {}).get('simpleText', '')
                thumbs = ch.get('thumbnail', {}).get('thumbnails', [])
                avatar = thumbs[-1]['url'] if thumbs else ''
                if avatar.startswith("//"):
                    avatar = "https:" + avatar
                channels.append({
                    "name": title,
                    "url": channel_url,
                    "avatar": avatar,
                    "subscribers": subs,
                    "suggested_folder": sanitize_folder_name(title)
                })
                if len(channels) >= 8:
                    break
    except Exception as e:
        print(f"Search error: {e}", file=sys.stderr)

    return channels


# ----------------- FLASK ROUTES -----------------

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/manifest.json")
def manifest():
    manifest_data = {
        "name": "YouTube Kids",
        "short_name": "YT Kids",
        "description": "Kids Cartoon Player & YouTube Downloader",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#0A0D14",
        "theme_color": "#FF0033"
    }
    return jsonify(manifest_data)

@app.route("/app.apk")
@app.route("/download-app")
def download_app_apk():
    apk_path = "/home/ark/ytkids-offline.apk"
    if os.path.exists(apk_path):
        return send_file(apk_path, mimetype="application/vnd.android.package-archive", as_attachment=True, download_name="ytkids-offline.apk")
    return "APK not found", 404

@app.route("/tv.apk")
@app.route("/download-tv")
def download_tv_apk():
    apk_path = "/home/ark/ytkids-tv.apk"
    if os.path.exists(apk_path):
        return send_file(apk_path, mimetype="application/vnd.android.package-archive", as_attachment=True, download_name="ytkids-tv.apk")
    return "TV APK not found", 404

@app.route("/api/client-log", methods=["POST"])
def api_client_log():
    try:
        data = request.get_json(silent=True) or {}
        client = data.get("client", "Unknown Client")
        message = data.get("message", "")
        stack = data.get("stack", "")
        print(f"[CLIENT LOG - {client}] {message}")
        if stack:
            print(f"[CLIENT STACK]\n{stack}")
        add_log(f"[{client}] {message}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/library")
def api_library():
    force = request.args.get("force", "false").lower() == "true"
    return jsonify(scan_library(force=force))

@app.route("/stream/<path:relpath>")
def stream_video(relpath):
    relpath = urllib.parse.unquote(relpath)
    safe_path = os.path.normpath(os.path.join(CARTOONS_DIR, relpath))
    if not safe_path.startswith(os.path.abspath(CARTOONS_DIR)):
        return "Forbidden", 403
    if not os.path.isfile(safe_path):
        return "Not Found", 404

    ext = os.path.splitext(safe_path)[1].lower()
    mimetypes_map = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/webm",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo"
    }
    mime = mimetypes_map.get(ext, "video/mp4")
    return send_file(safe_path, mimetype=mime, conditional=True)

@app.route("/thumb/<path:relpath>")
def get_thumb(relpath):
    relpath = urllib.parse.unquote(relpath)
    safe_path = os.path.normpath(os.path.join(CARTOONS_DIR, relpath))
    if not safe_path.startswith(os.path.abspath(CARTOONS_DIR)):
        return "Forbidden", 403

    if safe_path.lower().endswith((".jpg", ".jpeg", ".webp", ".png")) and os.path.isfile(safe_path):
        mime = "image/webp" if safe_path.lower().endswith(".webp") else "image/jpeg"
        resp = send_file(safe_path, mimetype=mime)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    stem, _ = os.path.splitext(safe_path)
    for ext in [".jpg", ".webp", "-poster.jpg", "-thumb.jpg", ".png"]:
        img_p = stem + ext
        if os.path.exists(img_p):
            mime = "image/webp" if ext == ".webp" else "image/jpeg"
            resp = send_file(img_p, mimetype=mime)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp

    pdir = os.path.dirname(safe_path)
    for fa in ["folder.jpg", "poster.jpg", "cover.jpg"]:
        fap = os.path.join(pdir, fa)
        if os.path.exists(fap):
            resp = send_file(fap, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp

    h = hashlib.md5(relpath.encode("utf-8")).hexdigest()
    cached_thumb = os.path.join(THUMBS_DIR, f"{h}.jpg")
    if not os.path.exists(cached_thumb) and os.path.isfile(safe_path):
        try:
            cmd = ["ffmpeg", "-y", "-i", safe_path, "-ss", "00:00:03", "-frames:v", "1", "-update", "1", cached_thumb]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        except Exception:
            pass

    if os.path.exists(cached_thumb):
        resp = send_file(cached_thumb, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="225" viewBox="0 0 400 225">
        <rect width="100%" height="100%" fill="#1e2538"/>
        <circle cx="200" cy="112" r="36" fill="#ff0033"/>
        <polygon points="192,96 216,112 192,128" fill="#ffffff"/>
    </svg>"""
    return Response(svg, mimetype="image/svg+xml")

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(silent=True) or {}
    q = data.get("query", "").strip()
    if not q:
        return jsonify({"channels": []})
    q_lower = q.lower()
    now = time.time()
    with search_cache_lock:
        if q_lower in search_cache:
            ts, cached_channels = search_cache[q_lower]
            if now - ts < 600:
                return jsonify({"channels": cached_channels, "cached": True})

    channels = search_youtube(q)
    if channels:
        with search_cache_lock:
            search_cache[q_lower] = (now, channels)
    return jsonify({"channels": channels, "cached": False})

@app.route("/api/download", methods=["POST"])
def api_download():
    global current_job, download_queue
    data = request.get_json(silent=True) or {}
    target_url = data.get("url", "").strip()
    target_title = data.get("title", "").strip()
    folder_name = data.get("folder", "").strip() or target_title
    quality = data.get("quality", "720")
    limit = int(data.get("limit", 10))

    if not target_url:
        return jsonify({"error": "YouTube URL or channel is required"}), 400

    job_id = hashlib.md5(f"{target_url}_{time.time()}".encode("utf-8")).hexdigest()[:8]
    job_item = {
        "id": job_id,
        "target_title": target_title,
        "target_url": target_url,
        "folder": folder_name,
        "quality": quality,
        "limit": limit
    }

    with state_lock:
        if not current_job["active"]:
            current_job["active"] = True
            current_job["target_title"] = target_title
            current_job["target_url"] = target_url
            current_job["folder"] = sanitize_folder_name(folder_name)
            current_job["quality"] = quality
            current_job["limit"] = limit
            current_job["job_id"] = job_id
            current_job["logs"] = []
            t = threading.Thread(
                target=run_download_worker,
                args=(target_url, folder_name, quality, limit),
                daemon=True
            )
            t.start()
            return jsonify({
                "success": True,
                "message": f"Download started for '{folder_name}'",
                "queued": False,
                "queue_position": 0,
                "queue_count": len(download_queue)
            })
        else:
            download_queue.append(job_item)
            pos = len(download_queue)
            add_log(f"[Queue] Added '{folder_name}' to download queue (Position #{pos})")
            return jsonify({
                "success": True,
                "message": f"Added '{folder_name}' to download queue (Position #{pos})",
                "queued": True,
                "queue_position": pos,
                "queue_count": len(download_queue)
            })

@app.route("/api/status")
def api_status():
    with state_lock:
        q_copy = [
            {
                "id": q["id"],
                "title": q["target_title"],
                "folder": q["folder"],
                "quality": q["quality"],
                "limit": q["limit"]
            }
            for q in download_queue
        ]
        data = {
            "active": current_job["active"],
            "target_title": current_job["target_title"],
            "folder": current_job["folder"],
            "status": current_job["status"],
            "progress_percent": current_job["progress_percent"],
            "speed": current_job["speed"],
            "eta": current_job["eta"],
            "current_file": current_job["current_file"],
            "error_message": current_job["error_message"],
            "logs": current_job["logs"][-30:],
            "queue": q_copy,
            "queue_count": len(q_copy)
        }
    data["disk"] = get_disk_info()
    return jsonify(data)

@app.route("/api/queue/remove", methods=["POST"])
def api_queue_remove():
    global download_queue
    data = request.get_json(silent=True) or {}
    job_id = data.get("id", "").strip()
    with state_lock:
        download_queue = [q for q in download_queue if q["id"] != job_id]
        add_log(f"[Queue] Removed job from queue")
    return jsonify({"success": True, "message": "Removed from queue", "queue_count": len(download_queue)})

@app.route("/api/queue/clear", methods=["POST"])
def api_queue_clear():
    global download_queue
    with state_lock:
        download_queue.clear()
        add_log("[Queue] Download queue cleared.")
    return jsonify({"success": True, "message": "Queue cleared"})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    global current_job, download_queue
    data = request.get_json(silent=True) or {}
    stop_all = data.get("stop_all", False)
    with state_lock:
        if stop_all:
            download_queue.clear()
        proc = current_job.get("process")
        if proc and current_job["active"]:
            try:
                proc.terminate()
                current_job["status"] = "stopped"
                current_job["current_file"] = "Download stopped by user."
                add_log("[Download] Stopped by user request.")
                return jsonify({"success": True, "message": "Download stopped", "queue_count": len(download_queue)})
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        else:
            return jsonify({"message": "No active download process to stop", "queue_count": len(download_queue)})

@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    lib = scan_library(force=True)
    return jsonify({"success": True, "total_episodes": lib["stats"]["total_episodes"]})

# ----------------- HTML PAGE -----------------
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>YouTube Kids</title>
  <meta name="theme-color" content="#FF0033">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <link rel="manifest" href="manifest.json">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600;700&family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
  <style>
    :root {
      --ytk-red: #FF0033;
      --ytk-yellow: #FFB300;
      --ytk-cyan: #00E5FF;
      --ytk-green: #00E676;
      --ytk-purple: #7C4DFF;
      --ytk-orange: #FF6D00;
      --bg-dark: #0A0D14;
      --bg-surface: #131722;
      --card-bg: #1B2130;
      --card-hover: #262E42;
      --text-main: #FFFFFF;
      --text-muted: #9EABC0;
      --radius-sm: 12px;
      --radius-md: 20px;
      --radius-lg: 28px;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
      user-select: none;
    }

    body {
      font-family: 'Nunito', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg-dark);
      background-image: radial-gradient(circle at 50% 0%, #201738 0%, #0A0D14 70%);
      color: var(--text-main);
      min-height: 100vh;
      overflow-x: hidden;
      padding-bottom: 80px;
    }

    header.ytk-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(10, 13, 20, 0.90);
      backdrop-filter: blur(16px);
      border-bottom: 2px solid rgba(255, 255, 255, 0.08);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
    }

    .ytk-play-logo {
      width: 44px;
      height: 32px;
      background: var(--ytk-red);
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 14px rgba(255, 0, 51, 0.4);
      transition: transform 0.2s;
    }
    .brand:hover .ytk-play-logo {
      transform: scale(1.08) rotate(-4deg);
    }
    .ytk-play-logo svg {
      width: 16px;
      height: 16px;
      fill: #FFF;
      margin-left: 2px;
    }

    .ytk-wordmark {
      font-family: 'Fredoka', cursive, sans-serif;
      font-size: 1.6rem;
      font-weight: 700;
      letter-spacing: -0.5px;
      display: flex;
      align-items: center;
    }
    .word-yt { color: #FFF; }
    .word-kids {
      margin-left: 6px;
      display: inline-flex;
    }
    .k-letter { color: var(--ytk-cyan); }
    .i-letter { color: var(--ytk-yellow); }
    .d-letter { color: var(--ytk-green); }
    .s-letter { color: var(--ytk-red); }

    .ytk-tabs {
      display: flex;
      align-items: center;
      background: rgba(255, 255, 255, 0.06);
      padding: 6px;
      border-radius: 40px;
      gap: 6px;
    }

    .ytk-tab {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 18px;
      border-radius: 30px;
      font-family: 'Fredoka', cursive;
      font-size: 1.05rem;
      font-weight: 600;
      color: var(--text-muted);
      background: transparent;
      border: none;
      cursor: pointer;
      transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
    }

    .ytk-tab:hover {
      color: #FFF;
      transform: translateY(-2px);
    }

    .ytk-tab.active {
      background: #FFF;
      color: #111;
      box-shadow: 0 4px 16px rgba(255, 255, 255, 0.25);
      transform: scale(1.04);
    }

    .ytk-tab svg {
      width: 22px;
      height: 22px;
      fill: currentColor;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .grownups-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      border-radius: 30px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: var(--text-muted);
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
    }
    .grownups-btn:hover {
      background: rgba(255, 255, 255, 0.15);
      color: #FFF;
      transform: translateY(-1px);
    }
    .grownups-btn svg {
      width: 16px;
      height: 16px;
      fill: currentColor;
    }

    .container {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px;
    }

    .view-section {
      display: none;
      animation: fadeIn 0.3s ease;
    }
    .view-section.active {
      display: block;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Home Toolbar (Shuffle + Filters) */
    .home-toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
      background: var(--bg-surface);
      padding: 14px 20px;
      border-radius: var(--radius-md);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }
    .shuffle-action-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 22px;
      border-radius: 30px;
      background: linear-gradient(135deg, var(--ytk-purple), #5E35B1);
      color: #FFF;
      font-family: 'Fredoka', cursive;
      font-size: 1.1rem;
      font-weight: 700;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(124, 77, 255, 0.35);
      transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
    }
    .shuffle-action-btn:hover {
      transform: scale(1.05);
    }
    .shuffle-action-btn svg {
      width: 20px;
      height: 20px;
      fill: currentColor;
    }

    .search-filter-box {
      display: flex;
      align-items: center;
      background: rgba(255, 255, 255, 0.06);
      border: 2px solid rgba(255, 255, 255, 0.1);
      border-radius: 30px;
      padding: 8px 18px;
      gap: 10px;
      flex: 1;
      min-width: 220px;
      max-width: 440px;
    }
    .search-filter-box input {
      background: transparent;
      border: none;
      outline: none;
      color: #FFF;
      font-size: 1rem;
      font-family: inherit;
      width: 100%;
    }
    .filter-chips {
      display: flex;
      align-items: center;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 4px;
    }
    .chip {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
      transition: all 0.2s;
    }
    .chip:hover, .chip.active {
      background: var(--ytk-cyan);
      color: #000;
      border-color: var(--ytk-cyan);
    }

    /* Shows Grid */
    .shows-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 24px;
    }
    .show-card {
      background: var(--card-bg);
      border-radius: var(--radius-lg);
      overflow: hidden;
      border: 2px solid rgba(255, 255, 255, 0.06);
      cursor: pointer;
      transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
      position: relative;
    }
    .show-card:hover {
      transform: translateY(-8px) scale(1.02);
      border-color: rgba(255, 255, 255, 0.25);
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.5);
    }
    .show-card-thumb-wrap {
      width: 100%;
      padding-top: 56.25%;
      position: relative;
      background: #111;
      overflow: hidden;
    }
    .show-card-thumb {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.4s;
    }
    .show-card:hover .show-card-thumb {
      transform: scale(1.06);
    }
    .show-badge-count {
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(0, 0, 0, 0.8);
      color: #FFF;
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 0.85rem;
      font-weight: 800;
      backdrop-filter: blur(8px);
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .show-card-body {
      padding: 18px 20px;
    }
    .show-card-title {
      font-family: 'Fredoka', cursive;
      font-size: 1.35rem;
      font-weight: 700;
      color: #FFF;
      margin-bottom: 6px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .show-card-sub {
      color: var(--text-muted);
      font-size: 0.95rem;
      font-weight: 600;
    }

    /* Single Show View */
    .show-hero {
      background: linear-gradient(135deg, rgba(38, 46, 66, 0.8), rgba(27, 33, 48, 0.9));
      border: 2px solid rgba(255, 255, 255, 0.08);
      border-radius: var(--radius-lg);
      padding: 32px;
      margin-bottom: 28px;
      display: flex;
      align-items: center;
      gap: 32px;
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
      position: relative;
      overflow: hidden;
    }
    .show-hero-poster {
      width: 220px;
      height: 140px;
      border-radius: var(--radius-md);
      object-fit: cover;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
      border: 2px solid rgba(255, 255, 255, 0.15);
    }
    .show-hero-info h1 {
      font-family: 'Fredoka', cursive;
      font-size: 2.4rem;
      color: #FFF;
      margin-bottom: 8px;
    }
    .show-hero-info p {
      font-size: 1.1rem;
      color: var(--text-muted);
      margin-bottom: 18px;
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .show-hero-actions {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .play-all-btn {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 28px;
      border-radius: 40px;
      background: var(--ytk-red);
      color: #FFF;
      font-family: 'Fredoka', cursive;
      font-size: 1.2rem;
      font-weight: 700;
      border: none;
      cursor: pointer;
      box-shadow: 0 6px 20px rgba(255, 0, 51, 0.4);
      transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
    }
    .play-all-btn:hover {
      transform: scale(1.05);
      background: #FF1A47;
    }
    .back-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 20px;
      border-radius: 40px;
      background: rgba(255, 255, 255, 0.1);
      color: #FFF;
      font-size: 1.05rem;
      font-weight: 700;
      border: none;
      cursor: pointer;
      transition: all 0.2s;
    }
    .back-btn:hover {
      background: rgba(255, 255, 255, 0.2);
    }

    /* Video Cards Grid */
    .episodes-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 20px;
    }
    .video-card {
      background: var(--card-bg);
      border-radius: var(--radius-md);
      overflow: hidden;
      border: 2px solid rgba(255, 255, 255, 0.05);
      cursor: pointer;
      transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
      position: relative;
      display: flex;
      flex-direction: column;
    }
    .video-card:hover {
      transform: translateY(-6px) scale(1.02);
      border-color: var(--ytk-cyan);
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.4);
    }
    .video-thumb-wrap {
      width: 100%;
      padding-top: 56.25%;
      position: relative;
      background: #0d1117;
      overflow: hidden;
    }
    .video-thumb {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.3s;
    }
    .video-card:hover .video-thumb {
      transform: scale(1.05);
    }
    .play-overlay-icon {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%) scale(0.8);
      width: 52px;
      height: 52px;
      background: rgba(255, 0, 51, 0.85);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: all 0.25s;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
    }
    .play-overlay-icon svg {
      width: 22px;
      height: 22px;
      fill: #FFF;
      margin-left: 3px;
    }
    .video-card:hover .play-overlay-icon {
      opacity: 1;
      transform: translate(-50%, -50%) scale(1);
    }
    .video-duration-pill {
      position: absolute;
      bottom: 8px;
      right: 8px;
      background: rgba(0, 0, 0, 0.8);
      color: #FFF;
      padding: 3px 8px;
      border-radius: 8px;
      font-size: 0.8rem;
      font-weight: 800;
      backdrop-filter: blur(4px);
    }
    .video-card-body {
      padding: 14px 16px;
      flex-grow: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .video-card-title {
      font-size: 1.05rem;
      font-weight: 700;
      line-height: 1.35;
      color: #FFF;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      margin-bottom: 8px;
    }
    .video-card-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.85rem;
      color: var(--text-muted);
      font-weight: 600;
    }
    .video-show-tag {
      background: rgba(255, 255, 255, 0.08);
      padding: 3px 8px;
      border-radius: 6px;
      color: var(--ytk-yellow);
      font-weight: 700;
      font-size: 0.8rem;
      max-width: 140px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* IN-APP FULL-BLEED VIDEO PLAYER MODAL */
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
    }

    /* Countdown Overlay */
    .countdown-overlay {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      background: rgba(19, 23, 34, 0.95);
      border: 3px solid var(--ytk-green);
      border-radius: var(--radius-lg);
      padding: 32px 48px;
      text-align: center;
      z-index: 30;
      display: none;
      box-shadow: 0 16px 48px rgba(0,0,0,0.8);
      animation: bounceIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    }
    .countdown-overlay.active {
      display: block;
    }
    @keyframes bounceIn {
      from { transform: translate(-50%, -50%) scale(0.7); opacity: 0; }
      to { transform: translate(-50%, -50%) scale(1); opacity: 1; }
    }
    .countdown-number {
      font-family: 'Fredoka', cursive;
      font-size: 3.5rem;
      color: var(--ytk-green);
      margin: 8px 0;
    }
    .cancel-countdown-btn {
      padding: 8px 24px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.15);
      color: #FFF;
      font-weight: 700;
      border: none;
      cursor: pointer;
    }

    /* Grown-ups Math Gate Modal */
    .modal-backdrop {
      position: fixed;
      top: 0; left: 0; width: 100vw; height: 100vh;
      background: rgba(0, 0, 0, 0.85);
      backdrop-filter: blur(14px);
      z-index: 2000;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal-backdrop.active {
      display: flex;
    }
    .lock-dialog {
      background: var(--bg-surface);
      border-radius: var(--radius-lg);
      border: 2px solid rgba(255, 255, 255, 0.1);
      padding: 36px;
      max-width: 440px;
      width: 100%;
      text-align: center;
      box-shadow: 0 16px 40px rgba(0,0,0,0.7);
    }
    .lock-dialog h3 {
      font-family: 'Fredoka', cursive;
      font-size: 1.8rem;
      color: #FFF;
      margin-bottom: 12px;
    }
    .lock-math-question {
      font-size: 1.4rem;
      font-weight: 800;
      color: var(--ytk-yellow);
      background: rgba(0, 0, 0, 0.3);
      padding: 14px;
      border-radius: var(--radius-sm);
      margin: 16px 0;
    }
    .lock-input {
      width: 140px;
      text-align: center;
      font-size: 1.8rem;
      font-weight: 800;
      padding: 10px;
      border-radius: var(--radius-sm);
      background: #000;
      border: 2px solid rgba(255, 255, 255, 0.2);
      color: #FFF;
      outline: none;
      margin-bottom: 20px;
    }
    .lock-dialog-actions {
      display: flex;
      gap: 12px;
      justify-content: center;
    }
    .lock-confirm-btn {
      padding: 12px 28px;
      border-radius: 30px;
      background: var(--ytk-green);
      color: #000;
      font-weight: 800;
      font-size: 1.05rem;
      border: none;
      cursor: pointer;
    }
    .lock-cancel-btn {
      padding: 12px 24px;
      border-radius: 30px;
      background: rgba(255, 255, 255, 0.1);
      color: #FFF;
      font-weight: 700;
      border: none;
      cursor: pointer;
    }

    /* GROWN-UPS FULL DASHBOARD MODAL */
    .parent-dashboard-dialog {
      background: var(--bg-surface);
      border-radius: var(--radius-lg);
      border: 2px solid rgba(255, 255, 255, 0.12);
      padding: 32px;
      max-width: 900px;
      width: 100%;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 20px 50px rgba(0,0,0,0.8);
      position: relative;
    }
    .parent-dash-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .parent-dash-header h3 {
      font-family: 'Fredoka', cursive;
      font-size: 1.8rem;
      color: #FFF;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .parent-dash-close {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.1);
      border: none;
      color: #FFF;
      font-size: 1.2rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .parent-dash-close:hover {
      background: var(--ytk-red);
    }

    .parent-section-box {
      background: var(--card-bg);
      border-radius: var(--radius-md);
      padding: 20px 24px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      margin-bottom: 24px;
    }
    .parent-section-title {
      font-family: 'Fredoka', cursive;
      font-size: 1.35rem;
      color: var(--ytk-yellow);
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .parent-setting-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }
    .parent-setting-row:last-child {
      border-bottom: none;
    }

    /* Autoplay Switch */
    .autoplay-switch {
      position: relative;
      display: inline-block;
      width: 52px;
      height: 28px;
    }
    .autoplay-switch input { opacity: 0; width: 0; height: 0; }
    .autoplay-slider {
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: rgba(255, 255, 255, 0.2);
      transition: .3s;
      border-radius: 28px;
    }
    .autoplay-slider:before {
      position: absolute; content: ""; height: 22px; width: 22px; left: 3px; bottom: 3px;
      background-color: white;
      transition: .3s;
      border-radius: 50%;
    }
    input:checked + .autoplay-slider {
      background-color: var(--ytk-green);
    }
    input:checked + .autoplay-slider:before {
      transform: translateX(24px);
    }

    /* Search & Downloader inside Grown-ups */
    .search-input-group {
      display: flex;
      gap: 12px;
      margin-bottom: 16px;
    }
    .search-input-group input {
      flex: 1;
      padding: 14px 20px;
      border-radius: 30px;
      background: rgba(0, 0, 0, 0.4);
      border: 2px solid rgba(255, 255, 255, 0.12);
      color: #FFF;
      font-size: 1.05rem;
      font-family: inherit;
      outline: none;
    }
    .search-input-group input:focus {
      border-color: var(--ytk-red);
    }
    .search-btn {
      padding: 14px 28px;
      border-radius: 30px;
      background: var(--ytk-red);
      color: #FFF;
      font-family: 'Fredoka', cursive;
      font-size: 1.1rem;
      font-weight: 700;
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .search-btn:hover {
      transform: scale(1.03);
      background: #FF1A47;
    }

    .preset-chips-wrap {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 20px;
    }
    .preset-chip {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.14);
      color: #FFF;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
    }
    .preset-chip:hover {
      background: var(--ytk-yellow);
      color: #111;
      transform: translateY(-2px);
    }

    .channels-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }
    .channel-card {
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: var(--radius-sm);
      padding: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .channel-info {
      display: flex;
      align-items: center;
      gap: 10px;
      overflow: hidden;
    }
    .channel-avatar {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      object-fit: cover;
      background: #222;
      flex-shrink: 0;
    }
    .channel-text { overflow: hidden; }
    .channel-name {
      font-size: 0.95rem;
      font-weight: 800;
      color: #FFF;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .channel-subs {
      font-size: 0.8rem;
      color: var(--text-muted);
    }
    .channel-pick-btn {
      padding: 6px 14px;
      border-radius: 16px;
      background: var(--ytk-green);
      color: #0A0D14;
      font-weight: 800;
      font-size: 0.85rem;
      border: none;
      cursor: pointer;
      white-space: nowrap;
    }

    .download-config-card {
      background: rgba(0, 0, 0, 0.4);
      border: 2px solid var(--ytk-yellow);
      border-radius: var(--radius-md);
      padding: 20px;
      margin-top: 16px;
      display: none;
    }
    .download-config-card.active {
      display: block;
    }
    .download-form-row {
      display: grid;
      grid-template-columns: 2fr 1fr 1fr auto;
      gap: 12px;
      align-items: flex-end;
    }
    @media (max-width: 768px) {
      .download-form-row { grid-template-columns: 1fr; }
    }
    .form-group label {
      display: block;
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-muted);
      margin-bottom: 4px;
      text-transform: uppercase;
    }
    .form-group input, .form-group select {
      width: 100%;
      padding: 10px 14px;
      border-radius: 8px;
      background: #000;
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #FFF;
      font-size: 0.95rem;
      font-family: inherit;
      outline: none;
    }
    .start-dl-btn {
      padding: 10px 20px;
      border-radius: 8px;
      background: var(--ytk-yellow);
      color: #000;
      font-family: 'Fredoka', cursive;
      font-size: 1.05rem;
      font-weight: 700;
      border: none;
      cursor: pointer;
      white-space: nowrap;
    }

    .dl-progress-card {
      background: rgba(0, 0, 0, 0.4);
      border-radius: var(--radius-sm);
      padding: 16px;
      border: 1px solid rgba(255, 255, 255, 0.15);
      margin-top: 16px;
      display: none;
    }
    .dl-progress-card.active {
      display: block;
    }
    .progress-bar-wrap {
      width: 100%;
      height: 12px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 10px;
      overflow: hidden;
      margin: 8px 0;
    }
    .progress-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--ytk-green), var(--ytk-cyan));
      width: 0%;
      transition: width 0.3s;
    }
    .progress-info {
      display: flex;
      justify-content: space-between;
      font-size: 0.9rem;
      font-weight: 700;
      color: #FFF;
    }

    @media (max-width: 768px) {
      header.ytk-header { padding: 10px 16px; }
      .ytk-wordmark { display: none; }
      .ytk-tab span { display: none; }
      .ytk-tab { padding: 8px 12px; }
      .container { padding: 16px; }
      .show-hero { flex-direction: column; text-align: center; gap: 16px; padding: 20px; }
      .show-hero-poster { width: 100%; max-width: 280px; height: 160px; }
      .show-hero-actions { justify-content: center; }
      .shows-grid, .episodes-grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 14px; }
      .show-card-title { font-size: 1.1rem; }
      .video-card-title { font-size: 0.95rem; }
    }
  </style>
</head>
<body>

  <!-- Header -->
  <header class="ytk-header">
    <div class="brand" onclick="switchView('home')">
      <div class="ytk-play-logo">
        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
      </div>
      <div class="ytk-wordmark">
        <span class="word-yt">YouTube</span>
        <span class="word-kids">
          <span class="k-letter">K</span><span class="i-letter">i</span><span class="d-letter">d</span><span class="s-letter">s</span>
        </span>
      </div>
    </div>

    <!-- Category Tabs (Home / Shows / Music) -->
    <nav class="ytk-tabs">
      <button class="ytk-tab active" id="tab-home" onclick="switchView('home')">
        <svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
        <span>Home</span>
      </button>
      <button class="ytk-tab" id="tab-shows" onclick="switchView('shows')">
        <svg viewBox="0 0 24 24"><path d="M21 3H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h5v2h8v-2h5c1.1 0 1.99-.9 1.99-2L23 5c0-1.1-.9-2-2-2zm0 14H3V5h18v12z"/></svg>
        <span>Shows</span>
      </button>
      <button class="ytk-tab" id="tab-music" onclick="switchView('music')">
        <svg viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
        <span>Music</span>
      </button>
    </nav>

    <!-- Right Grown-ups Button -->
    <div class="header-actions">
      <button class="grownups-btn" onclick="openParentGate()">
        <svg viewBox="0 0 24 24"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
        <span>Grown-ups</span>
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <main class="container">

    <!-- 1. HOME VIEW (SHUFFLED VIDEOS RECOMMENDATION FEED) -->
    <section id="view-home" class="view-section active">
      <div class="home-toolbar">
        <button class="shuffle-action-btn" onclick="reshuffleHomeVideos()">
          <svg viewBox="0 0 24 24"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>
          <span>Shuffle Videos</span>
        </button>
        <div class="search-filter-box">
          <svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:#9EABC0;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
          <input type="text" id="homeFilterInput" placeholder="Filter videos by title..." oninput="filterHomeVideos()">
        </div>
        <div class="filter-chips" id="homeFilterChips"></div>
      </div>
      <div class="episodes-grid" id="homeVideosGrid"></div>
    </section>

    <!-- 2. SHOWS VIEW -->
    <section id="view-shows" class="view-section">
      <div id="shows-list-container">
        <div class="shows-grid" id="showsGrid"></div>
      </div>

      <!-- Single Show Details View -->
      <div id="single-show-container" style="display: none;">
        <div class="show-hero">
          <img id="showHeroPoster" class="show-hero-poster" src="" alt="Show Poster">
          <div class="show-hero-info">
            <h1 id="showHeroTitle">Show Title</h1>
            <p id="showHeroMeta">0 Episodes</p>
            <div class="show-hero-actions">
              <button class="play-all-btn" onclick="playCurrentShow(0)">
                <svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:currentColor;"><path d="M8 5v14l11-7z"/></svg>
                Watch First Episode
              </button>
              <button class="back-btn" onclick="closeShowDetails()">
                ← All Shows
              </button>
            </div>
          </div>
        </div>
        <div class="episodes-grid" id="showEpisodesGrid"></div>
      </div>
    </section>

    <!-- 3. MUSIC & RHYMES VIEW -->
    <section id="view-music" class="view-section">
      <div class="episodes-grid" id="musicGrid"></div>
    </section>

  </main>

  <!-- FULLSCREEN / APP VIDEO PLAYER MODAL -->
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
  </div>

  <!-- GROWN-UPS MATH GATE MODAL -->
  <div class="modal-backdrop" id="parentLockModal">
    <div class="lock-dialog">
      <h3>Grown-ups Only</h3>
      <p style="color:var(--text-muted);font-size:0.95rem;">Please solve this math question to continue:</p>
      <div class="lock-math-question" id="lockMathText">4 × 3 = ?</div>
      <input type="number" class="lock-input" id="lockAnswerInput" placeholder="?" onkeydown="if(event.key==='Enter') verifyParentLock()">
      <div class="lock-dialog-actions">
        <button class="lock-confirm-btn" onclick="verifyParentLock()">Unlock</button>
        <button class="lock-cancel-btn" onclick="closeParentGate()">Cancel</button>
      </div>
    </div>
  </div>

  <!-- GROWN-UPS COMPLETE DASHBOARD MODAL -->
  <div class="modal-backdrop" id="parentSettingsModal">
    <div class="parent-dashboard-dialog">
      <div class="parent-dash-header">
        <h3>
          <svg viewBox="0 0 24 24" style="width:28px;height:28px;fill:var(--ytk-cyan);"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
          Grown-ups Parental Controls & Downloader
        </h3>
        <button class="parent-dash-close" onclick="closeParentDashboard()">✕</button>
      </div>

      <!-- Section 1: Playback & System Settings -->
      <div class="parent-section-box">
        <div class="parent-section-title">
          <svg viewBox="0 0 24 24" style="width:22px;height:22px;fill:currentColor;"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
          Playback & System Settings
        </div>

        <!-- Autoplay Setting (Moved here as requested) -->
        <div class="parent-setting-row">
          <div>
            <div style="font-size:1.05rem;font-weight:700;color:#FFF;">Autoplay Next Episode</div>
            <div style="font-size:0.85rem;color:var(--text-muted);">Automatically start the next episode when one finishes</div>
          </div>
          <label class="autoplay-switch">
            <input type="checkbox" id="parentAutoplayCheckbox" onchange="toggleParentAutoplay(this.checked)">
            <span class="autoplay-slider"></span>
          </label>
        </div>

        <!-- Storage Stat -->
        <div class="parent-setting-row">
          <div>
            <div style="font-size:1.05rem;font-weight:700;color:#FFF;">Free Disk Storage</div>
            <div style="font-size:0.85rem;color:var(--text-muted);" id="statEpisodesCount">-- Shows, -- Episodes</div>
          </div>
          <div id="statDiskFree" style="color:var(--ytk-green);font-size:1.1rem;font-weight:800;">-- GB Free</div>
        </div>

        <!-- Action Buttons -->
        <div style="margin-top:16px;display:flex;flex-wrap:wrap;gap:12px;">
          <button class="play-all-btn" style="background:var(--ytk-cyan);color:#000;padding:10px 22px;font-size:1rem;" onclick="rescanCartoons()">
            🔄 Rescan Cartoon Library
          </button>
          <a href="/app.apk" download class="play-all-btn" style="text-decoration:none;background:var(--ytk-green);color:#000;padding:10px 22px;font-size:1rem;">
            📱 Download Android Tablet App (.APK)
          </a>
          <a href="/tv.apk" download class="play-all-btn" style="text-decoration:none;background:var(--ytk-yellow);color:#000;padding:10px 22px;font-size:1rem;">
            📺 Download Android TV App (.APK)
          </a>
          <a href="/" target="_blank" class="back-btn" style="text-decoration:none;padding:10px 22px;font-size:1rem;">
            🖥️ Open Debian Server cPanel
          </a>
        </div>
      </div>

      <!-- Section 2: Search & Add Cartoons (Moved here as requested) -->
      <div class="parent-section-box">
        <div class="parent-section-title">
          <svg viewBox="0 0 24 24" style="width:22px;height:22px;fill:currentColor;"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg>
          Search YouTube Channels & Download Shows
        </div>
        <p style="color:var(--text-muted);font-size:0.95rem;margin-bottom:16px;">
          Search any kid cartoon on YouTube or click a popular preset to download episodes directly to your Debian library.
        </p>

        <!-- Search Bar -->
        <div class="search-input-group">
          <input type="text" id="channelSearchInput" placeholder="Search cartoon show (e.g. Bluey, Peppa Pig, Cocomelon)..." onkeydown="if(event.key==='Enter') searchChannels()">
          <button class="search-btn" id="searchChannelsBtn" onclick="searchChannels()">
            <svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:currentColor;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
            Search
          </button>
        </div>

        <!-- Quick Presets -->
        <div class="preset-chips-wrap">
          <button class="preset-chip" onclick="quickSearch('Bluey')">🐶 Bluey</button>
          <button class="preset-chip" onclick="quickSearch('Masha and the Bear')">🐻 Masha and Bear</button>
          <button class="preset-chip" onclick="quickSearch('Peppa Pig')">🐷 Peppa Pig</button>
          <button class="preset-chip" onclick="quickSearch('Cocomelon')">🍉 Cocomelon</button>
          <button class="preset-chip" onclick="quickSearch('Super Simple Songs')">🎵 Super Simple Songs</button>
          <button class="preset-chip" onclick="quickSearch('Numberblocks')">🔢 Numberblocks</button>
          <button class="preset-chip" onclick="quickSearch('Paw Patrol')">🚒 Paw Patrol</button>
          <button class="preset-chip" onclick="quickSearch('Daniel Tiger')">🐯 Daniel Tiger</button>
          <button class="preset-chip" onclick="quickSearch('Blippi')">🚜 Blippi</button>
        </div>

        <!-- Live Download Status Card -->
        <div class="dl-progress-card" id="dlProgressCard">
          <div id="dlNotificationBanner" style="display:none;margin-bottom:12px;padding:8px 14px;border-radius:8px;font-size:0.9rem;font-weight:bold;"></div>
          <div class="progress-info">
            <span id="dlStatusText">Downloading...</span>
            <span id="dlPercentText">0%</span>
          </div>
          <div class="progress-bar-wrap">
            <div class="progress-bar-fill" id="dlProgressFill"></div>
          </div>
          <div class="progress-info" style="font-size:0.85rem;color:var(--text-muted);">
            <span id="dlCurrentFile">Connecting...</span>
            <span id="dlSpeedEta">Speed: -- | ETA: --</span>
          </div>
          <div class="dl-live-logs" id="dlLiveLogs">Connecting to YouTube...</div>
          <div id="dlQueueSection" style="margin-top:14px;display:none;border-top:1px solid rgba(255,255,255,0.1);padding-top:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <span style="font-family:'Fredoka';font-size:1rem;color:var(--ytk-yellow);display:flex;align-items:center;gap:6px;">
                <span>📋 Queued Channels:</span>
                <span id="dlQueueBadge" style="background:var(--ytk-yellow);color:#000;padding:2px 8px;border-radius:12px;font-size:0.8rem;font-weight:800;">0</span>
              </span>
              <button class="back-btn" style="padding:2px 10px;font-size:0.75rem;background:rgba(255,59,48,0.2);color:#FF453A;border:1px solid #FF453A;" onclick="clearQueue()">Clear Queue</button>
            </div>
            <div id="dlQueueList" style="display:flex;flex-direction:column;gap:6px;max-height:140px;overflow-y:auto;"></div>
          </div>
          <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;">
            <button class="back-btn" style="padding:4px 14px;font-size:0.85rem;" onclick="stopDownload(false)">Skip / Cancel Current</button>
            <button class="back-btn" style="padding:4px 14px;font-size:0.85rem;background:rgba(255,59,48,0.2);color:#FF453A;border:1px solid #FF453A;" onclick="stopDownload(true)">Stop All & Cancel Queue</button>
          </div>
        </div>

        <!-- Download Drawer -->
        <div class="download-config-card" id="downloadConfigCard">
          <div style="font-family:'Fredoka';font-size:1.2rem;color:var(--ytk-yellow);margin-bottom:12px;">
            <span id="dlTargetChannelName">Download Show</span>
          </div>
          <div class="download-form-row">
            <div class="form-group">
              <label>Show Folder Name</label>
              <input type="text" id="dlFolderInput" placeholder="Show Name">
            </div>
            <div class="form-group">
              <label>Episodes</label>
              <select id="dlLimitSelect">
                <option value="5">Top 5 Episodes</option>
                <option value="10" selected>Top 10 Episodes</option>
                <option value="20">Top 20 Episodes</option>
                <option value="50">Top 50 Episodes</option>
                <option value="0">All Available</option>
              </select>
            </div>
            <div class="form-group">
              <label>Quality</label>
              <select id="dlQualitySelect">
                <option value="720" selected>720p HD (Crisp & Fast)</option>
                <option value="1080">1080p Full HD</option>
                <option value="480">480p Standard</option>
              </select>
            </div>
            <button class="start-dl-btn" onclick="confirmStartDownload()">Start Download</button>
          </div>
        </div>

        <!-- Channel Search Results -->
        <div class="channels-grid" id="channelsGrid"></div>
      </div>
    </div>
  </div>

  <script>
    function getApiBase() {
      let p = window.location.pathname;
      while (p.endsWith('/') && p.length > 1) {
        p = p.slice(0, -1);
      }
      return p;
    }
    const API_BASE = getApiBase();
    function url(path) {
      return (API_BASE === '' || API_BASE === '/') ? '/' + path : API_BASE + '/' + path;
    }

    let library = { shows: [], all_episodes: [], music_episodes: [] };
    let shuffledHomeVideos = [];
    let activeFilterShow = 'all';
    let currentShow = null;
    let currentPlaylist = [];
    let currentVideoIdx = -1;
    let isKidLocked = false;
    let autoplay = true;
    let countdownTimer = null;
    let controlsHideTimeout = null;
    let activeSelectedChannel = null;

    // Load Autoplay Preference from localStorage (defaults to true)
    function initAutoplaySetting() {
      const saved = localStorage.getItem('ytkids_autoplay');
      autoplay = saved === null ? true : saved === 'true';
      const cb = document.getElementById('parentAutoplayCheckbox');
      if (cb) cb.checked = autoplay;
    }

    function toggleParentAutoplay(checked) {
      autoplay = checked;
      localStorage.setItem('ytkids_autoplay', checked ? 'true' : 'false');
    }

    // Fisher-Yates Array Shuffle
    function shuffleArray(arr) {
      const a = [...arr];
      for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
      }
      return a;
    }

    // Load Library on Start
    async function loadLibrary(force = false) {
      try {
        const res = await fetch(url('api/library' + (force ? '?force=true' : '')));
        if (res.ok) {
          library = await res.json();
          reshuffleHomeVideos();
          renderShows();
          renderMusic();
          updateStats();
        }
      } catch (e) {
        console.error("Library load error:", e);
      }
    }

    // View Switching (Home / Shows / Music)
    function switchView(viewName) {
      document.querySelectorAll('.ytk-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.view-section').forEach(s => s.classList.remove('active'));

      const tab = document.getElementById('tab-' + viewName);
      const view = document.getElementById('view-' + viewName);
      if (tab) tab.classList.add('active');
      if (view) view.classList.add('active');

      if (viewName === 'shows') {
        closeShowDetails();
      }
    }

    // ================= 1. HOME VIEW (SHUFFLED VIDEOS) =================
    function reshuffleHomeVideos() {
      shuffledHomeVideos = shuffleArray(library.all_episodes || []);
      renderHomeFilterChips();
      filterHomeVideos();
    }

    function renderHomeFilterChips() {
      const chips = document.getElementById('homeFilterChips');
      if (!chips) return;
      chips.innerHTML = `<button class="chip ${activeFilterShow === 'all' ? 'active' : ''}" onclick="filterHomeByShow('all', this)">All (${library.all_episodes.length})</button>` +
        library.shows.map(s => `<button class="chip ${activeFilterShow === s.title ? 'active' : ''}" onclick="filterHomeByShow('${escapeHtml(s.title)}', this)">${escapeHtml(s.title)} (${s.episode_count})</button>`).join('');
    }

    function filterHomeByShow(showName, btn) {
      activeFilterShow = showName;
      document.querySelectorAll('#homeFilterChips .chip').forEach(c => c.classList.remove('active'));
      if (btn) btn.classList.add('active');
      filterHomeVideos();
    }

    function filterHomeVideos() {
      const q = (document.getElementById('homeFilterInput')?.value || '').toLowerCase().trim();
      const filtered = shuffledHomeVideos.filter(ep => {
        const matchesShow = activeFilterShow === 'all' || ep.show === activeFilterShow;
        const matchesQuery = !q || ep.title.toLowerCase().includes(q) || ep.raw_title.toLowerCase().includes(q);
        return matchesShow && matchesQuery;
      });

      const grid = document.getElementById('homeVideosGrid');
      if (filtered.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);font-size:1.2rem;">No matching cartoon videos found.</div>';
        return;
      }
      grid.innerHTML = filtered.map((ep, idx) => `
        <div class="video-card" onclick="playEpisodeFromList(window.currentHomeFilteredList, ${idx})">
          <div class="video-thumb-wrap">
            <img class="video-thumb" src="${url(ep.thumb_url)}" alt="${escapeHtml(ep.title)}" loading="lazy">
            <div class="play-overlay-icon"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
            <div class="video-duration-pill">${ep.ext.toUpperCase().replace('.', '')}</div>
          </div>
          <div class="video-card-body">
            <div class="video-card-title">${escapeHtml(ep.title)}</div>
            <div class="video-card-meta">
              <span>${ep.size_mb} MB</span>
              <span class="video-show-tag">${escapeHtml(ep.show)}</span>
            </div>
          </div>
        </div>
      `).join('');
      window.currentHomeFilteredList = filtered;
    }

    // ================= 2. SHOWS VIEW =================
    function renderShows() {
      const container = document.getElementById('showsGrid');
      if (!library.shows || library.shows.length === 0) {
        container.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);font-size:1.2rem;">No cartoon shows found yet. Ask grown-ups to download shows in Grown-ups section!</div>';
        return;
      }
      container.innerHTML = library.shows.map((s, idx) => `
        <div class="show-card" onclick="openShowDetails(${idx})">
          <div class="show-card-thumb-wrap">
            <img class="show-card-thumb" src="${url(s.poster_url)}" alt="${escapeHtml(s.title)}" loading="lazy">
            <div class="show-badge-count">
              <svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H8V4h12v12z"/></svg>
              ${s.episode_count} ${s.episode_count === 1 ? 'video' : 'episodes'}
            </div>
          </div>
          <div class="show-card-body">
            <div class="show-card-title">${escapeHtml(s.title)}</div>
            <div class="show-card-sub">Tap to browse episodes</div>
          </div>
        </div>
      `).join('');
    }

    function openShowDetails(idx) {
      currentShow = library.shows[idx];
      if (!currentShow) return;

      document.getElementById('shows-list-container').style.display = 'none';
      const detail = document.getElementById('single-show-container');
      detail.style.display = 'block';

      document.getElementById('showHeroPoster').src = url(currentShow.poster_url);
      document.getElementById('showHeroTitle').textContent = currentShow.title;
      document.getElementById('showHeroMeta').textContent = `${currentShow.episode_count} Episodes available in HD`;

      const grid = document.getElementById('showEpisodesGrid');
      grid.innerHTML = currentShow.episodes.map((ep, epIdx) => `
        <div class="video-card" onclick="playEpisodeFromList(currentShow.episodes, ${epIdx})">
          <div class="video-thumb-wrap">
            <img class="video-thumb" src="${url(ep.thumb_url)}" alt="${escapeHtml(ep.title)}" loading="lazy">
            <div class="play-overlay-icon">
              <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            </div>
            <div class="video-duration-pill">${ep.ext.toUpperCase().replace('.', '')}</div>
          </div>
          <div class="video-card-body">
            <div class="video-card-title">${escapeHtml(ep.title)}</div>
            <div class="video-card-meta">
              <span>${ep.size_mb} MB</span>
              <span class="video-show-tag">${escapeHtml(currentShow.title)}</span>
            </div>
          </div>
        </div>
      `).join('');
    }

    function closeShowDetails() {
      document.getElementById('single-show-container').style.display = 'none';
      document.getElementById('shows-list-container').style.display = 'block';
      currentShow = null;
    }

    function playCurrentShow(startIdx = 0) {
      if (currentShow && currentShow.episodes.length > 0) {
        playEpisodeFromList(currentShow.episodes, startIdx);
      }
    }

    // ================= 3. MUSIC VIEW =================
    function renderMusic() {
      const grid = document.getElementById('musicGrid');
      const vids = library.music_episodes || [];
      if (vids.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:48px;color:var(--text-muted);font-size:1.2rem;">No music or nursery rhymes yet.</div>';
        return;
      }
      grid.innerHTML = vids.map((ep, idx) => `
        <div class="video-card" onclick="playEpisodeFromList(library.music_episodes, ${idx})">
          <div class="video-thumb-wrap">
            <img class="video-thumb" src="${url(ep.thumb_url)}" alt="${escapeHtml(ep.title)}" loading="lazy">
            <div class="play-overlay-icon"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
            <div class="video-duration-pill">${ep.ext.toUpperCase().replace('.', '')}</div>
          </div>
          <div class="video-card-body">
            <div class="video-card-title">${escapeHtml(ep.title)}</div>
            <div class="video-card-meta">
              <span>${ep.size_mb} MB</span>
              <span class="video-show-tag">🎵 Music</span>
            </div>
          </div>
        </div>
      `).join('');
    }

    // ================= VIDEO PLAYER & FULLSCREEN TOGGLE =================
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
    }

    // ================= GROWN-UPS MODAL & SEARCH/DOWNLOADER =================
    let expectedLockAnswer = 0;
    function openParentGate() {
      const n1 = Math.floor(Math.random() * 8) + 2;
      const n2 = Math.floor(Math.random() * 8) + 2;
      expectedLockAnswer = n1 * n2;
      document.getElementById('lockMathText').textContent = `${n1} × ${n2} = ?`;
      document.getElementById('lockAnswerInput').value = '';
      document.getElementById('parentLockModal').classList.add('active');
      setTimeout(() => document.getElementById('lockAnswerInput').focus(), 100);
    }

    function closeParentGate() {
      document.getElementById('parentLockModal').classList.remove('active');
    }

    function verifyParentLock() {
      const val = parseInt(document.getElementById('lockAnswerInput').value, 10);
      if (val === expectedLockAnswer) {
        closeParentGate();
        openParentDashboard();
      } else {
        alert("Incorrect answer. Grown-ups only!");
        closeParentGate();
      }
    }

    function openParentDashboard() {
      document.getElementById('parentSettingsModal').classList.add('active');
      initAutoplaySetting();
      updateStats();
      pollStatus();
    }

    function closeParentDashboard() {
      document.getElementById('parentSettingsModal').classList.remove('active');
    }

    async function rescanCartoons() {
      await loadLibrary(true);
      alert("Cartoon library rescanned successfully!");
    }

    function updateStats() {
      if (library.stats && library.stats.disk) {
        document.getElementById('statDiskFree').textContent = `${library.stats.disk.free_gb} GB Free (of ${library.stats.disk.total_gb} GB)`;
      }
      if (library.stats) {
        document.getElementById('statEpisodesCount').textContent = `${library.stats.total_shows} Shows, ${library.stats.total_episodes} Episodes`;
      }
    }

    // YouTube Channel Search & Download (inside Grown-ups)
    async function searchChannels() {
      const input = document.getElementById('channelSearchInput');
      const q = input.value.trim();
      if (!q) return;

      const btn = document.getElementById('searchChannelsBtn');
      btn.disabled = true;
      btn.innerHTML = 'Searching...';

      try {
        const res = await fetch(url('api/search'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q })
        });
        const data = await res.json();
        renderChannels(data.channels || []);
      } catch (e) {
        alert("Search failed: " + e.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:20px;height:20px;fill:currentColor;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg> Search';
      }
    }

    function quickSearch(query) {
      document.getElementById('channelSearchInput').value = query;
      searchChannels();
    }

    function renderChannels(channels) {
      const grid = document.getElementById('channelsGrid');
      if (channels.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:16px;color:var(--text-muted);">No channels found. Try another query.</div>';
        return;
      }
      grid.innerHTML = channels.map((ch, idx) => `
        <div class="channel-card">
          <div class="channel-info">
            <img class="channel-avatar" src="${ch.avatar}" alt="Avatar" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'50\\' height=\\'50\\' fill=\\'%23ff0033\\'><circle cx=\\'25\\' cy=\\'25\\' r=\\'20\\'/></svg>'">
            <div class="channel-text">
              <div class="channel-name">${escapeHtml(ch.name)}</div>
              <div class="channel-subs">${escapeHtml(ch.subscribers || 'YouTube')}</div>
            </div>
          </div>
          <button class="channel-pick-btn" onclick="selectChannelForDownload(${idx})">Select</button>
        </div>
      `).join('');
      window.lastChannelResults = channels;
    }

    function selectChannelForDownload(idx) {
      const ch = window.lastChannelResults[idx];
      activeSelectedChannel = ch;
      document.getElementById('dlTargetChannelName').textContent = `Download Show: ${ch.name}`;
      document.getElementById('dlFolderInput').value = ch.suggested_folder || ch.name;
      const card = document.getElementById('downloadConfigCard');
      card.classList.add('active');
      card.scrollIntoView({ behavior: 'smooth' });
    }

    function showDlBanner(msg, type = 'info') {
      const b = document.getElementById('dlNotificationBanner');
      if (!b) return;
      b.textContent = msg;
      b.style.display = 'block';
      if (type === 'queued' || type === 'info') {
        b.style.background = 'rgba(255, 204, 0, 0.2)';
        b.style.border = '1px solid #FFCC00';
        b.style.color = '#FFCC00';
      } else if (type === 'success') {
        b.style.background = 'rgba(48, 209, 88, 0.2)';
        b.style.border = '1px solid #30D158';
        b.style.color = '#30D158';
      } else {
        b.style.background = 'rgba(255, 69, 58, 0.2)';
        b.style.border = '1px solid #FF453A';
        b.style.color = '#FF453A';
      }
      setTimeout(() => {
        if (b) b.style.display = 'none';
      }, 6000);
    }

    async function confirmStartDownload() {
      if (!activeSelectedChannel) return;
      const folder = document.getElementById('dlFolderInput').value.trim() || activeSelectedChannel.name;
      const limit = parseInt(document.getElementById('dlLimitSelect').value, 10);
      const quality = document.getElementById('dlQualitySelect').value;

      try {
        const res = await fetch(url('api/download'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: activeSelectedChannel.url,
            title: activeSelectedChannel.name,
            folder: folder,
            limit: limit,
            quality: quality
          })
        });
        const data = await res.json();
        if (res.ok) {
          document.getElementById('downloadConfigCard').classList.remove('active');
          const pCard = document.getElementById('dlProgressCard');
          pCard.classList.add('active');
          pCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          if (data.queued) {
            showDlBanner(`✓ Added "${folder}" to download queue! (Position #${data.queue_position})`, 'queued');
          } else {
            showDlBanner(`🚀 Started download for "${folder}"`, 'success');
          }
          pollStatus(true);
        } else {
          showDlBanner("Error starting download: " + (data.error || "Unknown error"), 'error');
        }
      } catch (e) {
        showDlBanner("Failed to connect: " + e.message, 'error');
      }
    }

    let statusPolling = false;
    let statusPollTimer = null;
    async function pollStatus(force = false) {
      if (statusPolling && !force) return;
      statusPolling = true;
      if (statusPollTimer) {
        clearTimeout(statusPollTimer);
        statusPollTimer = null;
      }
      try {
        const res = await fetch(url('api/status'));
        if (res.ok) {
          const s = await res.json();
          const card = document.getElementById('dlProgressCard');

          // Render Queue Section
          const qBox = document.getElementById('dlQueueSection');
          const qList = document.getElementById('dlQueueList');
          const qBadge = document.getElementById('dlQueueBadge');
          if (s.queue && s.queue.length > 0) {
            if (qBox) qBox.style.display = 'block';
            if (qBadge) qBadge.textContent = s.queue.length;
            if (qList) {
              qList.innerHTML = s.queue.map((item, qIdx) => `
                <div style="display:flex;justify-content:space-between;align-items:center;background:rgba(255,255,255,0.06);padding:8px 12px;border-radius:8px;font-size:0.88rem;border-left:3px solid var(--ytk-yellow);">
                  <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:8px;">
                    <span style="color:#FFF;font-weight:700;">#${qIdx + 1} ${escapeHtml(item.title || item.folder)}</span>
                    <span style="color:var(--text-muted);font-size:0.8rem;margin-left:6px;">(${item.limit > 0 ? item.limit + ' eps' : 'All eps'}, ${item.quality}p)</span>
                  </div>
                  <button style="background:rgba(255,69,58,0.15);border:1px solid rgba(255,69,58,0.4);border-radius:6px;color:#FF453A;cursor:pointer;font-weight:800;padding:4px 10px;font-size:0.75rem;flex-shrink:0;" onclick="removeFromQueue('${item.id}')" title="Remove from queue">✕ Remove</button>
                </div>
              `).join('');
            }
          } else {
            if (qBox) qBox.style.display = 'none';
          }

          if (s.active || s.status === 'downloading' || (s.queue && s.queue.length > 0)) {
            card.classList.add('active');
            document.getElementById('dlStatusText').textContent = s.active ? `Downloading: ${s.folder}` : `Processing Queue (${s.queue.length} waiting)...`;
            document.getElementById('dlPercentText').textContent = `${s.progress_percent}%`;
            document.getElementById('dlProgressFill').style.width = `${s.progress_percent}%`;
            document.getElementById('dlCurrentFile').textContent = s.current_file || 'Downloading...';
            document.getElementById('dlSpeedEta').textContent = `Speed: ${s.speed || '--'} | ETA: ${s.eta || '--'}`;
            if (s.logs && s.logs.length > 0) {
              const logsBox = document.getElementById('dlLiveLogs');
              if (logsBox) {
                logsBox.textContent = s.logs.slice(-4).join(String.fromCharCode(10));
                logsBox.scrollTop = logsBox.scrollHeight;
              }
            }
            statusPollTimer = setTimeout(() => pollStatus(true), 1500);
          } else {
            if (s.status === 'finished') {
              document.getElementById('dlStatusText').textContent = '✅ All Downloads Finished!';
              document.getElementById('dlPercentText').textContent = '100%';
              document.getElementById('dlProgressFill').style.width = '100%';
              loadLibrary(true);
              setTimeout(() => {
                if (!s.active && (!s.queue || s.queue.length === 0)) {
                  card.classList.remove('active');
                }
              }, 5000);
            } else if (s.status === 'error') {
              document.getElementById('dlStatusText').textContent = '❌ ' + (s.error_message || 'Download Error');
            } else {
              card.classList.remove('active');
            }
          }
        }
      } catch (e) {
        console.error("Status poll error:", e);
      } finally {
        statusPolling = false;
      }
    }

    async function removeFromQueue(id) {
      try {
        await fetch(url('api/queue/remove'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id })
        });
        pollStatus(true);
      } catch (e) {}
    }

    async function clearQueue() {
      if (!confirm("Clear all queued channel downloads?")) return;
      try {
        await fetch(url('api/queue/clear'), { method: 'POST' });
        pollStatus(true);
      } catch (e) {}
    }

    async function stopDownload(stopAll = false) {
      try {
        await fetch(url('api/stop'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stop_all: stopAll })
        });
        pollStatus();
      } catch (e) {}
    }

    function formatSeconds(secs) {
      if (isNaN(secs) || secs < 0) return "00:00";
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return (m < 10 ? '0' + m : m) + ':' + (s < 10 ? '0' + s : s);
    }
    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Initialize
    initAutoplaySetting();
    loadLibrary();
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 5000))
    print(f"Starting YouTube Kids Web Server on {host}:{port}...")
    scan_library()
    app.run(host=host, port=port, debug=False, threaded=True)
