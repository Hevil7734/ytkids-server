#!/usr/bin/env python3
import http.server
import socketserver
import os
import urllib.parse
import html

PORT = 8080
VIDEO_DIR = "/srv/videos"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kids Video Player</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #0f172a;
    color: #f8fafc;
    margin: 0;
    padding: 20px;
  }
  header {
    text-align: center;
    padding: 10px 0 20px 0;
  }
  h1 {
    font-size: 2.2rem;
    margin: 0 0 8px 0;
    color: #38bdf8;
  }
  p.subtitle {
    color: #94a3b8;
    margin: 0;
    font-size: 1.1rem;
  }
  .player-box {
    max-width: 800px;
    margin: 0 auto 30px auto;
    background: #1e293b;
    border-radius: 16px;
    padding: 16px;
    box-shadow: 0 10px 25px rgba(0,0,0,0.4);
    display: none;
  }
  video {
    width: 100%;
    border-radius: 12px;
    outline: none;
    max-height: 480px;
    background: #000;
  }
  .now-playing {
    font-size: 1.2rem;
    font-weight: bold;
    margin: 10px 0 0 0;
    color: #facc15;
    text-align: center;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 20px;
    max-width: 1100px;
    margin: 0 auto;
  }
  .card {
    background: #1e293b;
    border-radius: 14px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    transition: transform 0.15s ease, background 0.15s ease;
    border: 1px solid #334155;
  }
  .card:hover {
    transform: translateY(-4px);
    background: #253349;
  }
  .card h3 {
    margin: 0 0 12px 0;
    font-size: 1.1rem;
    line-height: 1.4;
    color: #e2e8f0;
  }
  .actions {
    display: flex;
    gap: 10px;
    margin-top: 12px;
  }
  .btn {
    flex: 1;
    padding: 12px 14px;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    text-decoration: none;
    display: inline-block;
  }
  .btn-play {
    background: #38bdf8;
    color: #0f172a;
  }
  .btn-download {
    background: #334155;
    color: #f8fafc;
  }
  .badge {
    display: inline-block;
    background: #0ea5e9;
    color: #fff;
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: bold;
    margin-bottom: 8px;
  }
</style>
</head>
<body>
<header>
  <h1>🎬 Kids Video Player</h1>
  <p class="subtitle">Tap any video to play or download for offline viewing</p>
</header>

<div class="player-box" id="playerBox">
  <video id="mainPlayer" controls autoplay playsinline></video>
  <div class="now-playing" id="nowPlaying"></div>
</div>

<div class="grid">
  {CARDS}
</div>

<script>
function playVideo(url, title) {
  const box = document.getElementById("playerBox");
  const player = document.getElementById("mainPlayer");
  const titleEl = document.getElementById("nowPlaying");
  box.style.display = "block";
  player.src = url;
  titleEl.textContent = title;
  player.play();
  box.scrollIntoView({ behavior: "smooth" });
}
</script>
</body>
</html>
"""

class VideoHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=VIDEO_DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            cards = []
            for root, _, files in os.walk(VIDEO_DIR):
                for f in sorted(files):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in [".mp4", ".mkv", ".webm", ".avi", ".mov"]:
                        rel_dir = os.path.relpath(root, VIDEO_DIR)
                        category = "Videos" if rel_dir == "." else rel_dir
                        rel_path = os.path.join(rel_dir, f) if rel_dir != "." else f
                        url_path = urllib.parse.quote(rel_path)
                        clean_name = os.path.splitext(f)[0]
                        escaped_title = html.escape(clean_name)
                        js_title = escaped_title.replace("'", "\\'")
                        
                        card = f"""
                        <div class="card">
                          <div>
                            <span class="badge">{category}</span>
                            <h3>{escaped_title}</h3>
                          </div>
                          <div class="actions">
                            <button class="btn btn-play" onclick="playVideo('{url_path}', '{js_title}')">▶ Play</button>
                            <a class="btn btn-download" href="{url_path}" download>⬇ Save</a>
                          </div>
                        </div>
                        """
                        cards.append(card)

            if not cards:
                content = HTML_TEMPLATE.replace("{CARDS}", "<p style='text-align:center;color:#94a3b8;'>No videos found yet. Add videos to /srv/videos/Cartoons/</p>")
            else:
                content = HTML_TEMPLATE.replace("{CARDS}", "".join(cards))

            self.wfile.write(content.encode("utf-8"))
            return

        return super().do_GET()

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), VideoHandler) as httpd:
        print(f"Kids Video Server running on port {PORT}")
        httpd.serve_forever()
