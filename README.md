# 📺 YouTube Kids Web & Streaming Server for Debian

An authentic, child-safe, self-hosted cartoon streaming server and YouTube channel downloader designed to run reliably on Debian Linux. Built to serve local web clients, Android tablets ([SafeKids Offline App](../ytkids-android)), and Android TV ([YTKidsTV](../ytkids-tv)).

---

## 🌟 Key Features

- **Authentic YouTube Kids User Experience**:
  - Kid-friendly interface with large touch-friendly thumbnails and character tiles.
  - Randomized cartoon recommendations on homepage with instant "Shuffle" button.
  - Single-click fullscreen player with responsive HTML5 video playback.
- **HTTP Range 206 Partial Streaming**:
  - High-performance chunked media streaming compatible with mobile web, ExoPlayer, and VLC.
  - Seamless seeking without buffering entire cartoon episodes.
- **Automated YouTube Video Downloader**:
  - Powered by `yt-dlp` to fetch channels and playlists in optimal formats (720p/1080p MP4).
  - Background task queue with progress tracking and automatic resume.
  - Automatic 16:9 thumbnail extraction via `ffmpeg`.
- **Protected Parental Control Gate**:
  - Math puzzle security challenge ("Grown-ups only") to prevent accidental child access to settings.
  - Channel manager, manual video downloader, storage cleaner, and autoplay toggle.
- **Client App Distribution Hub**:
  - Serves compiled APKs directly to local devices:
    - `/download-apk`: Android Tablet Offline Client (`ytkids-offline.apk`)
    - `/download-tv-apk`: Android TV Client (`ytkids-tv.apk`)
- **Remote Client Telemetry**:
  - `/api/client-log`: Centralized logging endpoint to capture logs, playback issues, and stack traces from remote TV and tablet clients.

---

## 🏗️ Architecture & Project Files

| File | Description |
| :--- | :--- |
| `ytkids_server.py` | Main production Flask server with client logging, streaming, and channel downloader APIs |
| `ytkids_app.py` | Standalone single-file YouTube Kids web application and UI |
| `kids_video_server.py` | Lightweight static video file server |
| `update_ytkids.py` | Automated batch updater script for cartoon channels & sync |
| `verify_queue.py` | Download verification and storage integrity utility |
| `ytkids.service` | Systemd service definition for automatic boot and restart on Debian |

---

## ⚙️ Prerequisites & Installation on Debian

### 1. System Packages
```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-flask python3-requests ffmpeg
sudo pip3 install yt-dlp
```

### 2. File Placement
Recommended server location: `/opt/ytkids-server` or `~/Projects/ytkids-server`

```bash
mkdir -p ~/Projects/ytkids-server
cd ~/Projects/ytkids-server
```

### 3. Media Storage Setup
Create the directory where downloaded cartoons will reside:
```bash
mkdir -p ~/Videos/cartoons
```

---

## 🚀 Running the Server

### Running Directly:
```bash
PORT=5050 python3 ytkids_server.py
```
Access in browser: `http://<debian-server-ip>:5050`

### Running via Systemd (Recommended):
1. Copy service file:
```bash
sudo cp ytkids.service /etc/systemd/system/ytkids.service
```
2. Adjust `User` and `WorkingDirectory` in `/etc/systemd/system/ytkids.service` if needed:
```ini
[Unit]
Description=YouTube Kids Cartoon Web Server for Debian
After=network.target

[Service]
Type=simple
User=ark
WorkingDirectory=/home/ark/Projects/ytkids-server
ExecStart=/usr/bin/python3 /home/ark/Projects/ytkids-server/ytkids_server.py
Restart=always
RestartSec=5
Environment=PORT=5050
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```
3. Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ytkids.service
sudo systemctl status ytkids.service
```

---

## 🔗 Key API Endpoints

- `GET /` — Kids web streaming portal interface.
- `GET /api/library` — JSON catalog of all downloaded cartoon categories and videos.
- `GET /video/<filename>` — HTTP Range 206 chunked video streaming endpoint.
- `GET /thumbnails/<filename>` — Video thumbnail preview image.
- `POST /api/download-channel` — Trigger background download of a YouTube channel or playlist.
- `GET /api/download-status` — Poll current queue and download progress.
- `POST /api/client-log` — Remote logging endpoint for Android Tablet & TV clients.
- `GET /download-apk` — Download latest Android Tablet offline APK.
- `GET /download-tv-apk` — Download latest Android TV APK.
